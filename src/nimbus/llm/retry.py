"""
Nimbus v2 Retry Utilities

Provides intelligent retry logic with provider header support.
Learned from opencode's retry.ts implementation.

Features:
- Exponential backoff with configurable parameters
- HTTP header parsing (retry-after-ms, retry-after)
- Maximum delay caps to prevent excessive waits
- Error classification for retryable errors
"""

import math
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Optional

from nimbus.core.logging import get_logger

logger = get_logger("v2.llm.retry")

# =============================================================================
# Constants (from opencode's retry.ts)
# =============================================================================

RETRY_INITIAL_DELAY_MS = 2000  # 2 seconds
RETRY_BACKOFF_FACTOR = 2
RETRY_MAX_DELAY_NO_HEADERS_MS = 30_000  # 30 seconds cap when no headers
RETRY_MAX_DELAY_MS = 2_147_483_647  # Max 32-bit signed integer


# =============================================================================
# Delay Calculation
# =============================================================================

def calculate_delay(
    attempt: int,
    response_headers: Optional[Dict[str, str]] = None,
    initial_delay_ms: float = RETRY_INITIAL_DELAY_MS,
    backoff_factor: float = RETRY_BACKOFF_FACTOR,
) -> float:
    """
    Calculate retry delay in milliseconds.

    Priority:
    1. retry-after-ms header (milliseconds)
    2. retry-after header (seconds or HTTP-date)
    3. Exponential backoff with cap

    Args:
        attempt: Current retry attempt (1-indexed)
        response_headers: HTTP response headers dict
        initial_delay_ms: Initial delay in milliseconds
        backoff_factor: Multiplier for exponential backoff

    Returns:
        Delay in milliseconds
    """
    if response_headers:
        # Try retry-after-ms first (milliseconds)
        retry_after_ms = response_headers.get("retry-after-ms")
        if retry_after_ms:
            try:
                parsed_ms = float(retry_after_ms)
                if not math.isnan(parsed_ms) and parsed_ms > 0:
                    logger.debug(f"Using retry-after-ms header: {parsed_ms}ms")
                    return min(parsed_ms, RETRY_MAX_DELAY_MS)
            except ValueError:
                pass

        # Try retry-after (seconds or HTTP-date)
        retry_after = response_headers.get("retry-after")
        if retry_after:
            # Try parsing as seconds first
            try:
                parsed_seconds = float(retry_after)
                if not math.isnan(parsed_seconds) and parsed_seconds > 0:
                    delay_ms = math.ceil(parsed_seconds * 1000)
                    logger.debug(f"Using retry-after header (seconds): {delay_ms}ms")
                    return min(delay_ms, RETRY_MAX_DELAY_MS)
            except ValueError:
                pass

            # Try parsing as HTTP date format
            try:
                parsed_date = parsedate_to_datetime(retry_after)
                delay_ms = (parsed_date.timestamp() - time.time()) * 1000
                if delay_ms > 0:
                    logger.debug(f"Using retry-after header (HTTP-date): {delay_ms}ms")
                    return min(math.ceil(delay_ms), RETRY_MAX_DELAY_MS)
            except Exception:
                pass

    # Fallback to exponential backoff with cap
    calculated_delay = initial_delay_ms * (backoff_factor ** (attempt - 1))
    capped_delay = min(calculated_delay, RETRY_MAX_DELAY_NO_HEADERS_MS)
    logger.debug(f"Using exponential backoff: {capped_delay}ms (attempt {attempt})")
    return capped_delay


def calculate_delay_seconds(
    attempt: int,
    response_headers: Optional[Dict[str, str]] = None,
    initial_delay_ms: float = RETRY_INITIAL_DELAY_MS,
    backoff_factor: float = RETRY_BACKOFF_FACTOR,
) -> float:
    """
    Calculate retry delay in seconds.

    Convenience wrapper around calculate_delay().

    Args:
        attempt: Current retry attempt (1-indexed)
        response_headers: HTTP response headers dict
        initial_delay_ms: Initial delay in milliseconds
        backoff_factor: Multiplier for exponential backoff

    Returns:
        Delay in seconds
    """
    return calculate_delay(attempt, response_headers, initial_delay_ms, backoff_factor) / 1000.0


# =============================================================================
# Error Classification
# =============================================================================

# HTTP status codes that are retryable
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Error message patterns that indicate retryable errors
RETRYABLE_PATTERNS = [
    "overloaded",
    "too_many_requests",
    "rate_limit",
    "exhausted",
    "unavailable",
    "server_error",
    "econnreset",
    "connection reset",
    "timeout",
]


def is_retryable_status(status_code: int) -> bool:
    """Check if HTTP status code is retryable."""
    return status_code in RETRYABLE_STATUS_CODES


def is_retryable_error(
    error_message: str,
    status_code: Optional[int] = None,
) -> bool:
    """
    Determine if an error is retryable based on message and status.

    Args:
        error_message: Error message string
        status_code: Optional HTTP status code

    Returns:
        True if error is retryable
    """
    # Check status code first
    if status_code and is_retryable_status(status_code):
        return True

    # Check error message patterns
    message_lower = error_message.lower()
    for pattern in RETRYABLE_PATTERNS:
        if pattern in message_lower:
            return True

    return False


def get_retry_message(
    error_message: str,
    status_code: Optional[int] = None,
) -> Optional[str]:
    """
    Get user-friendly retry message if error is retryable.

    Args:
        error_message: Error message string
        status_code: Optional HTTP status code

    Returns:
        User-friendly message if retryable, None otherwise
    """
    message_lower = error_message.lower()

    if status_code == 429 or "rate_limit" in message_lower or "too_many_requests" in message_lower:
        return "Rate Limited"

    if "overloaded" in message_lower or "exhausted" in message_lower or "unavailable" in message_lower:
        return "Provider is overloaded"

    if status_code in {500, 502, 503, 504} or "server_error" in message_lower:
        return "Provider Server Error"

    if "econnreset" in message_lower or "connection reset" in message_lower:
        return "Connection Reset"

    if "timeout" in message_lower:
        return "Request Timeout"

    return None


def extract_headers_from_response(response: Any) -> Dict[str, str]:
    """
    Extract headers from various response types.

    Handles aiohttp.ClientResponse, httpx.Response, etc.

    Args:
        response: HTTP response object

    Returns:
        Dict of header name -> value (lowercase keys)
    """
    headers = {}

    try:
        # Try common header access patterns
        if hasattr(response, 'headers'):
            raw_headers = response.headers
            if hasattr(raw_headers, 'items'):
                for key, value in raw_headers.items():
                    headers[key.lower()] = value
            elif hasattr(raw_headers, 'get'):
                # Try common retry headers
                for key in ['retry-after-ms', 'retry-after', 'x-ratelimit-reset']:
                    value = raw_headers.get(key)
                    if value:
                        headers[key.lower()] = value
    except Exception as e:
        logger.debug(f"Failed to extract headers: {e}")

    return headers
