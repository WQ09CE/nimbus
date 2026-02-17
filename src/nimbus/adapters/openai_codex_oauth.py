"""
OpenAI Codex OAuth Token Management

Loads, validates, and refreshes OAuth tokens for OpenAI Codex (ChatGPT Plus/Pro subscription)
stored in ~/.pi/agent/auth.json under the "openai-codex" key.
"""

import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTH_JSON_PATH = Path.home() / ".pi" / "agent" / "auth.json"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REFRESH_BUFFER_MS = 5 * 60 * 1000  # 5 minutes
AUTH_KEY = "openai-codex"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_oauth_token(auth_path: Optional[Path] = None) -> Optional[Dict]:
    """
    Read the ``openai-codex`` entry from *auth_path* (default AUTH_JSON_PATH).

    Returns ``{"access": "...", "refresh": "...", "expires": ...}`` on
    success, or ``None`` if the file does not exist or has no ``openai-codex``
    key.
    """
    path = auth_path or AUTH_JSON_PATH

    if not path.exists():
        logger.debug("Auth file not found: %s", path)
        return None

    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read auth file %s: %s", path, exc)
        return None

    auth = data.get(AUTH_KEY)
    if auth is None:
        logger.debug("No '%s' key in %s", AUTH_KEY, path)
        return None

    logger.debug(
        "Loaded OAuth token (expires in %.1f min)",
        (auth.get("expires", 0) - time.time() * 1000) / 1000 / 60,
    )
    return auth


def check_and_refresh(
    auth: Dict,
    auth_path: Optional[Path] = None,
) -> str:
    """
    Return a valid access token, refreshing transparently if expired.

    Parameters
    ----------
    auth : dict
        The ``openai-codex`` section loaded by :func:`load_oauth_token`.
    auth_path : Path, optional
        Override for the auth.json location (used when writing back after
        a refresh).

    Returns
    -------
    str
        A valid OAuth access token.

    Raises
    ------
    RuntimeError
        If the token is expired and the refresh request fails.
    """
    now_ms = time.time() * 1000
    expires_ms = auth.get("expires", 0)
    remaining_ms = expires_ms - now_ms

    if remaining_ms > REFRESH_BUFFER_MS:
        remaining_min = remaining_ms / 1000 / 60
        logger.debug("OAuth token valid for %.0f more minutes", remaining_min)
        return auth["access"]

    logger.info("OAuth token expired or expiring soon, refreshing...")
    return _refresh_token(auth, auth_path)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _refresh_token(
    auth: Dict,
    auth_path: Optional[Path] = None,
) -> str:
    """POST to TOKEN_URL, update *auth* in-place, and persist to disk."""
    path = auth_path or AUTH_JSON_PATH

    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": auth["refresh"],
    }

    resp = httpx.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=payload,
        timeout=30.0,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"OAuth token refresh failed: HTTP {resp.status_code}\n{resp.text}"
        )

    result = resp.json()
    new_access = result["access_token"]
    new_refresh = result["refresh_token"]
    expires_in = result["expires_in"]  # seconds

    new_expires_ms = time.time() * 1000 + expires_in * 1000 - REFRESH_BUFFER_MS

    # Update auth dict in-place so the caller sees new values too.
    auth["access"] = new_access
    auth["refresh"] = new_refresh
    auth["expires"] = new_expires_ms

    # Persist to disk.
    try:
        with open(path, "r") as f:
            full_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        full_data = {}

    full_data[AUTH_KEY] = auth

    with open(path, "w") as f:
        json.dump(full_data, f, indent=2)

    logger.info(
        "OAuth token refreshed (new expiry in %.1f hours)",
        expires_in / 3600,
    )
    return new_access
