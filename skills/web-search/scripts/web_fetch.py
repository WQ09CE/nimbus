#!/usr/bin/env python3
"""Fetch and extract readable content from a web page."""

import argparse
import re
import ssl
import sys
import urllib.request
import urllib.error


def strip_html(html: str) -> str:
    """Remove HTML tags and extract readable text."""
    # Remove script and style blocks entirely
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

    # Remove nav, header, footer blocks (common boilerplate)
    html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # Convert common block elements to newlines
    html = re.sub(r"<(?:br|hr)[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<(?:p|div|h[1-6]|li|tr|blockquote|pre)[^>]*>", "\n", html, flags=re.IGNORECASE)

    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", html)

    # Decode common HTML entities
    entity_map = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&apos;": "'",
        "&nbsp;": " ",
        "&mdash;": "—",
        "&ndash;": "–",
        "&hellip;": "…",
        "&copy;": "©",
        "&reg;": "®",
        "&trade;": "™",
    }
    for entity, char in entity_map.items():
        text = text.replace(entity, char)
    # Handle numeric entities
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)

    # Clean up whitespace
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(stripped)

    return "\n".join(lines)


def fetch_page(url: str, max_length: int = 10000) -> str:
    """Fetch a web page and return clean text content."""
    parts: list[str] = []
    parts.append(f"=== Web Fetch: {url} ===")
    parts.append("")

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    req.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    req.add_header("Accept-Language", "en-US,en;q=0.9,zh-CN;q=0.8")

    # Build SSL context — try verified first, fall back to unverified on macOS cert issues
    def _make_ssl_ctx():
        for factory in [
            lambda: ssl.create_default_context(),
            lambda: ssl.create_default_context(cafile=__import__("certifi").where()),
            lambda: ssl._create_unverified_context(),
        ]:
            try:
                return factory()
            except Exception:
                continue
        return ssl._create_unverified_context()

    ctx = _make_ssl_ctx()

    try:
        try:
            resp_cm = urllib.request.urlopen(req, timeout=15, context=ctx)
        except urllib.error.URLError as ssl_err:
            if "CERTIFICATE_VERIFY_FAILED" in str(ssl_err):
                ctx = ssl._create_unverified_context()
                resp_cm = urllib.request.urlopen(req, timeout=15, context=ctx)
            else:
                raise
        with resp_cm as resp:
            content_type = resp.headers.get("Content-Type", "")

            # Check if it's HTML
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                # Try to read as plain text
                raw = resp.read(max_length * 2)
                try:
                    text = raw.decode("utf-8", errors="ignore")
                except Exception:
                    text = raw.decode("latin-1", errors="ignore")
                if len(text) > max_length:
                    text = text[:max_length] + "\n\n[... truncated ...]"
                parts.append(text)
                return "\n".join(parts)

            # Detect encoding
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].strip().split(";")[0]

            raw = resp.read(max_length * 3)  # Read extra since HTML has tags
            try:
                html = raw.decode(charset, errors="ignore")
            except (LookupError, UnicodeDecodeError):
                html = raw.decode("utf-8", errors="ignore")

    except urllib.error.HTTPError as e:
        return f"Error: HTTP {e.code} when fetching {url}"
    except urllib.error.URLError as e:
        return f"Error: Could not connect to {url}: {e.reason}"
    except Exception as e:
        return f"Error: {e}"

    # Extract title
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    if title_match:
        title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()
        parts.append(f"Title: {title}")
        parts.append("")

    # Strip HTML and get text
    text = strip_html(html)

    if len(text) > max_length:
        text = text[:max_length] + "\n\n[... truncated at {} chars ...]".format(max_length)

    parts.append(text)

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Fetch and extract readable content from a web page.")
    parser.add_argument("--url", required=True, help="The URL to fetch")
    parser.add_argument("--max_length", type=int, default=10000, help="Max content length (default: 10000)")
    args = parser.parse_args()

    if not args.url.strip():
        print("Error: url cannot be empty", file=sys.stderr)
        sys.exit(1)

    output = fetch_page(args.url, args.max_length)
    print(output)


if __name__ == "__main__":
    main()
