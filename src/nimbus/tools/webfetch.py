"""WebFetch tool for fetching and converting web content to markdown.

This module provides a tool for fetching web pages and converting their
HTML content to readable markdown format with caching support.

Example:
    >>> result = await web_fetch("https://example.com")
    >>> print(result)
    URL: https://example.com
    Status: 200
    Content-Type: text/html

    ---

    # Example Domain

    This domain is for use in illustrative examples...
"""

import re
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import html2text
import httpx

from .base import ToolParameter, tool

# Cache configuration
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes
MAX_CACHE_SIZE = 100  # Maximum number of cached entries

# Default settings
DEFAULT_TIMEOUT = 30
DEFAULT_MAX_CONTENT_LENGTH = 50000
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; NimbusBot/1.0; +https://nimbus.ai/bot)"
)

# Content type patterns for HTML
HTML_CONTENT_TYPES = frozenset([
    "text/html",
    "application/xhtml+xml",
])


class WebFetchCache:
    """Simple in-memory cache with TTL support.

    Stores fetched web content with automatic expiration after TTL.
    Implements a simple LRU-like eviction when max size is exceeded.

    Attributes:
        ttl: Time-to-live in seconds for cache entries.
        max_size: Maximum number of entries to cache.
    """

    def __init__(self, ttl: int = CACHE_TTL_SECONDS, max_size: int = MAX_CACHE_SIZE):
        """Initialize cache with TTL and size limits.

        Args:
            ttl: Time-to-live in seconds for cache entries.
            max_size: Maximum number of entries to store.
        """
        self._cache: Dict[str, Tuple[str, float]] = {}
        self._ttl = ttl
        self._max_size = max_size

    def get(self, key: str) -> Optional[str]:
        """Get cached value if exists and not expired.

        Args:
            key: Cache key (typically URL).

        Returns:
            Cached content if valid, None otherwise.
        """
        entry = self._cache.get(key)
        if entry is None:
            return None

        content, timestamp = entry
        if time.time() - timestamp > self._ttl:
            # Expired, remove and return None
            del self._cache[key]
            return None

        return content

    def set(self, key: str, value: str) -> None:
        """Store value in cache with current timestamp.

        Args:
            key: Cache key (typically URL).
            value: Content to cache.
        """
        # Evict old entries if at capacity
        if len(self._cache) >= self._max_size:
            self._evict_expired()

        # If still at capacity, remove oldest entry
        if len(self._cache) >= self._max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
            del self._cache[oldest_key]

        self._cache[key] = (value, time.time())

    def _evict_expired(self) -> None:
        """Remove all expired entries from cache."""
        current_time = time.time()
        expired_keys = [
            k for k, (_, ts) in self._cache.items()
            if current_time - ts > self._ttl
        ]
        for key in expired_keys:
            del self._cache[key]

    def clear(self) -> None:
        """Clear all entries from cache."""
        self._cache.clear()


# Global cache instance
_cache = WebFetchCache()


def _validate_url(url: str) -> str:
    """Validate and normalize URL.

    Checks if the URL is valid and upgrades HTTP to HTTPS.

    Args:
        url: URL string to validate.

    Returns:
        Normalized URL with HTTPS scheme.

    Raises:
        ValueError: If URL is invalid or has unsupported scheme.
    """
    if not url:
        raise ValueError("URL cannot be empty")

    # Parse URL
    parsed = urlparse(url)

    # Check for valid scheme
    if not parsed.scheme:
        # Assume HTTPS if no scheme provided
        url = f"https://{url}"
        parsed = urlparse(url)
    elif parsed.scheme == "http":
        # Upgrade HTTP to HTTPS
        parsed = parsed._replace(scheme="https")
        url = urlunparse(parsed)
    elif parsed.scheme != "https":
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}. Only HTTP(S) is supported.")

    # Check for valid netloc (hostname)
    if not parsed.netloc:
        raise ValueError(f"Invalid URL: missing hostname in '{url}'")

    return url


def _convert_html_to_markdown(html_content: str) -> str:
    """Convert HTML content to readable markdown.

    Uses html2text library to convert HTML to markdown format,
    with configuration for optimal readability.

    Args:
        html_content: HTML string to convert.

    Returns:
        Markdown formatted text.
    """
    converter = html2text.HTML2Text()

    # Configuration for readable output
    converter.ignore_links = False
    converter.ignore_images = False
    converter.ignore_emphasis = False
    converter.body_width = 0  # No line wrapping
    converter.unicode_snob = True
    converter.skip_internal_links = True
    converter.ignore_tables = False
    converter.single_line_break = True
    converter.mark_code = True

    markdown = converter.handle(html_content)

    # Clean up excessive whitespace
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    markdown = markdown.strip()

    return markdown


def _extract_content_type(content_type_header: Optional[str]) -> str:
    """Extract the main content type from Content-Type header.

    Args:
        content_type_header: Full Content-Type header value.

    Returns:
        Main content type (e.g., "text/html").
    """
    if not content_type_header:
        return "unknown"

    # Extract main type (before any parameters like charset)
    main_type = content_type_header.split(";")[0].strip().lower()
    return main_type


def _format_response(
    url: str,
    status_code: int,
    content_type: str,
    content: str,
    max_length: int,
    redirect_info: Optional[str] = None,
) -> str:
    """Format the response output.

    Args:
        url: Final URL after any redirects.
        status_code: HTTP status code.
        content_type: Content-Type of the response.
        content: Processed content (markdown or plain text).
        max_length: Maximum content length before truncation.
        redirect_info: Optional message about redirects.

    Returns:
        Formatted response string.
    """
    header_lines = [
        f"URL: {url}",
        f"Status: {status_code}",
        f"Content-Type: {content_type}",
    ]

    if redirect_info:
        header_lines.append(f"Redirect: {redirect_info}")

    header = "\n".join(header_lines)

    # Truncate content if needed
    truncated = False
    if len(content) > max_length:
        content = content[:max_length]
        truncated = True

    result = f"{header}\n\n---\n\n{content}"

    if truncated:
        result += f"\n\n---\n[Content truncated at {max_length} characters]"

    return result


@tool(
    name="WebFetch",
    description=(
        "Fetch content from a URL and convert to readable format. "
        "HTML pages are converted to markdown. "
        "HTTP URLs are automatically upgraded to HTTPS. "
        "Includes a 15-minute cache for repeated requests."
    ),
    parameters=[
        ToolParameter(
            "url",
            "string",
            "The URL to fetch content from. Must be a fully-formed valid URL.",
            required=True,
        ),
        ToolParameter(
            "timeout",
            "integer",
            "Request timeout in seconds. Defaults to 30.",
            required=False,
            default=DEFAULT_TIMEOUT,
        ),
        ToolParameter(
            "max_content_length",
            "integer",
            "Maximum content length to return in characters. Defaults to 50000.",
            required=False,
            default=DEFAULT_MAX_CONTENT_LENGTH,
        ),
    ],
)
async def web_fetch(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    max_content_length: int = DEFAULT_MAX_CONTENT_LENGTH,
    **kwargs: Any,
) -> str:
    """Fetch content from a URL and convert to readable format.

    Fetches the specified URL, converts HTML to markdown, and returns
    a formatted response with metadata. Supports automatic HTTPS upgrade,
    redirect following, and response caching.

    Features:
        - Automatic HTTP to HTTPS upgrade
        - HTML to markdown conversion
        - 15-minute response caching
        - Cross-domain redirect detection
        - Content truncation for large responses
        - Timeout handling

    Args:
        url: The URL to fetch. HTTP URLs are automatically upgraded to HTTPS.
        timeout: Request timeout in seconds. Defaults to 30.
        max_content_length: Maximum content length to return. Defaults to 50000.
        **kwargs: Additional context (unused).

    Returns:
        Formatted response containing URL, status, content-type, and content.
        HTML content is converted to markdown.

    Raises:
        ValueError: If URL is invalid or has unsupported scheme.
        httpx.TimeoutException: If request times out.
        httpx.RequestError: If network request fails.

    Example:
        >>> result = await web_fetch("https://example.com")
        >>> print(result)
        URL: https://example.com
        Status: 200
        Content-Type: text/html

        ---

        # Example Domain
        ...
    """
    # Validate and normalize URL
    normalized_url = _validate_url(url)

    # Check cache
    cache_key = f"{normalized_url}:{max_content_length}"
    cached_result = _cache.get(cache_key)
    if cached_result is not None:
        return cached_result

    # Parse original URL for redirect detection
    original_host = urlparse(normalized_url).netloc.lower()

    # Configure HTTP client
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    redirect_info: Optional[str] = None

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        max_redirects=10,
    ) as client:
        try:
            response = await client.get(normalized_url, headers=headers)
        except httpx.TimeoutException:
            raise httpx.TimeoutException(
                f"Request timed out after {timeout} seconds"
            )
        except httpx.RequestError as e:
            raise httpx.RequestError(
                f"Failed to fetch URL '{normalized_url}': {e}"
            )

        # Check for cross-domain redirect
        final_url = str(response.url)
        final_host = urlparse(final_url).netloc.lower()

        if final_host != original_host:
            redirect_info = f"Redirected from {original_host} to {final_host}"

        # Get content type
        content_type_header = response.headers.get("content-type")
        content_type = _extract_content_type(content_type_header)

        # Get response content
        raw_content = response.text

        # Convert HTML to markdown if applicable
        if content_type in HTML_CONTENT_TYPES:
            content = _convert_html_to_markdown(raw_content)
        else:
            # Return raw text for non-HTML content
            content = raw_content

        # Format response
        result = _format_response(
            url=final_url,
            status_code=response.status_code,
            content_type=content_type,
            content=content,
            max_length=max_content_length,
            redirect_info=redirect_info,
        )

        # Cache the result
        _cache.set(cache_key, result)

        return result


def clear_cache() -> None:
    """Clear the WebFetch cache.

    Utility function to clear all cached responses.
    Useful for testing or when fresh data is needed.
    """
    _cache.clear()
