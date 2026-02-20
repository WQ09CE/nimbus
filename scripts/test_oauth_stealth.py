#!/usr/bin/env python3
"""
Test OAuth stealth mode: call Anthropic API using OAuth token + Claude Code identity.

Usage:
    python scripts/test_oauth_stealth.py

This script:
1. Reads OAuth token from ~/.pi/agent/auth.json
2. Refreshes token if expired
3. Sends a single minimal request to claude-sonnet-4-5-20250929
4. Prints response and usage
"""

import json
import time
import sys
from pathlib import Path

import anthropic
import httpx


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AUTH_JSON_PATH = Path.home() / ".pi" / "agent" / "auth.json"
MODEL = "claude-sonnet-4-5-20250929"

REFRESH_ENDPOINT = "https://console.anthropic.com/v1/oauth/token"
REFRESH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_BUFFER_MS = 5 * 60 * 1000  # 5 minutes

STEALTH_HEADERS = {
    "anthropic-dangerous-direct-browser-access": "true",
    "anthropic-beta": (
        "claude-code-20250219,"
        "oauth-2025-04-20,"
        "fine-grained-tool-streaming-2025-05-14,"
        "interleaved-thinking-2025-05-14"
    ),
    "user-agent": "claude-cli/2.1.2 (external, cli)",
    "x-app": "cli",
}

SYSTEM_PROMPT = (
    "You are Claude Code, Anthropic's official CLI for Claude.\n\n"
    "Respond concisely."
)

# A minimal Read tool definition (Claude Code naming convention)
READ_TOOL = {
    "name": "Read",
    "description": "Reads a file from the local filesystem.",
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The absolute path to the file to read",
            }
        },
        "required": ["file_path"],
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[stealth] {msg}")


def load_auth() -> dict:
    """Load auth.json and return the anthropic section."""
    log(f"Reading auth from {AUTH_JSON_PATH}")
    if not AUTH_JSON_PATH.exists():
        raise FileNotFoundError(f"Auth file not found: {AUTH_JSON_PATH}")

    with open(AUTH_JSON_PATH, "r") as f:
        data = json.load(f)

    if "anthropic" not in data:
        raise KeyError("No 'anthropic' key in auth.json")

    auth = data["anthropic"]
    log(f"  type    = {auth.get('type')}")
    log(f"  access  = {auth['access'][:20]}...{auth['access'][-6:]}")
    log(f"  refresh = {auth['refresh'][:20]}...{auth['refresh'][-6:]}")
    log(f"  expires = {auth['expires']}")
    return auth


def check_and_refresh_token(auth: dict) -> str:
    """
    Check if the OAuth token is expired; refresh if needed.
    Returns a valid access token.
    """
    now_ms = time.time() * 1000
    expires_ms = auth["expires"]
    remaining_ms = expires_ms - now_ms

    if remaining_ms > 0:
        remaining_min = remaining_ms / 1000 / 60
        remaining_hr = remaining_min / 60
        log(f"Token still valid. Remaining: {remaining_hr:.1f} hours ({remaining_min:.0f} minutes)")
        return auth["access"]

    # Token expired, need to refresh
    log("Token EXPIRED. Refreshing...")
    return _refresh_token(auth)


def _refresh_token(auth: dict) -> str:
    """Refresh the OAuth token and write back to auth.json."""
    payload = {
        "grant_type": "refresh_token",
        "client_id": REFRESH_CLIENT_ID,
        "refresh_token": auth["refresh"],
    }

    log(f"POST {REFRESH_ENDPOINT}")
    resp = httpx.post(
        REFRESH_ENDPOINT,
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=30.0,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Token refresh failed: HTTP {resp.status_code}\n{resp.text}"
        )

    result = resp.json()
    new_access = result["access_token"]
    new_refresh = result["refresh_token"]
    expires_in = result["expires_in"]  # seconds

    new_expires_ms = time.time() * 1000 + expires_in * 1000 - REFRESH_BUFFER_MS

    log(f"Refresh successful!")
    log(f"  new access  = {new_access[:20]}...{new_access[-6:]}")
    log(f"  new refresh = {new_refresh[:20]}...{new_refresh[-6:]}")
    log(f"  expires_in  = {expires_in}s ({expires_in / 3600:.1f}h)")

    # Update auth dict
    auth["access"] = new_access
    auth["refresh"] = new_refresh
    auth["expires"] = new_expires_ms

    # Write back to auth.json
    with open(AUTH_JSON_PATH, "r") as f:
        full_data = json.load(f)

    full_data["anthropic"] = auth

    with open(AUTH_JSON_PATH, "w") as f:
        json.dump(full_data, f, indent=2)
    log(f"Updated auth.json with new tokens")

    return new_access


def send_test_request(access_token: str) -> None:
    """Send a single minimal request using Anthropic SDK with stealth headers."""
    log(f"Creating Anthropic client with stealth headers...")
    log(f"  model = {MODEL}")
    log(f"  auth_token = {access_token[:20]}...{access_token[-6:]}")

    client = anthropic.Anthropic(
        auth_token=access_token,
        default_headers=STEALTH_HEADERS,
    )

    log("Sending request: 'Say hello in exactly 3 words.'")
    log(f"  tools: [Read] (not expected to be called)")
    log(f"  system: '{SYSTEM_PROMPT[:60]}...'")

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=256,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": "Say hello in exactly 3 words."}
            ],
            tools=[READ_TOOL],
        )
    except anthropic.APIStatusError as e:
        log(f"API Error: {e.status_code}")
        log(f"  message: {e.message}")
        log(f"  body: {e.body}")
        raise
    except anthropic.APIConnectionError as e:
        log(f"Connection Error: {e}")
        raise

    # Print response
    log("=" * 60)
    log("RESPONSE:")
    log(f"  id         = {response.id}")
    log(f"  model      = {response.model}")
    log(f"  role       = {response.role}")
    log(f"  stop_reason= {response.stop_reason}")

    for i, block in enumerate(response.content):
        if block.type == "text":
            log(f"  content[{i}] = (text) {block.text}")
        elif block.type == "tool_use":
            log(f"  content[{i}] = (tool_use) {block.name}({block.input})")
        else:
            log(f"  content[{i}] = ({block.type}) {block}")

    log("")
    log("USAGE:")
    log(f"  input_tokens  = {response.usage.input_tokens}")
    log(f"  output_tokens = {response.usage.output_tokens}")
    if hasattr(response.usage, "cache_creation_input_tokens"):
        log(f"  cache_creation= {response.usage.cache_creation_input_tokens}")
    if hasattr(response.usage, "cache_read_input_tokens"):
        log(f"  cache_read    = {response.usage.cache_read_input_tokens}")
    log("=" * 60)
    log("SUCCESS - OAuth stealth mode works!")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    log("=" * 60)
    log("OAuth Stealth Mode Test")
    log("=" * 60)

    # Step 1: Load auth
    try:
        auth = load_auth()
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        log(f"FATAL: Cannot load auth: {e}")
        sys.exit(1)

    # Step 2: Check/refresh token
    try:
        access_token = check_and_refresh_token(auth)
    except RuntimeError as e:
        log(f"FATAL: Token refresh failed: {e}")
        sys.exit(1)

    # Step 3: Send test request
    try:
        send_test_request(access_token)
    except Exception as e:
        log(f"FATAL: Request failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
