#!/usr/bin/env python3
"""Search the web using Brave Search API."""

import argparse
import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path


def _load_api_key() -> str:
    """Load Brave API key from ~/.nimbus/config.json, then fall back to env var."""
    # 1. Try ~/.nimbus/config.json
    config_path = Path.home() / ".nimbus" / "config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            key = config.get("api_keys", {}).get("brave_search", "")
            if key:
                return key
        except (json.JSONDecodeError, OSError):
            pass
    # 2. Fall back to environment variable
    return os.environ.get("BRAVE_API_KEY", "")


BRAVE_API_KEY = _load_api_key()
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


def brave_search(query: str, count: int = 5, summary: bool = False) -> str:
    """Execute a Brave web search and return formatted results."""
    params = {
        "q": query,
        "count": min(count, 20),
    }
    if summary:
        params["summary"] = "1"

    url = f"{BRAVE_SEARCH_URL}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("Accept-Encoding", "identity")
    req.add_header("X-Subscription-Token", BRAVE_API_KEY)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")[:500]
        except Exception:
            pass
        return f"Error: Brave Search API returned HTTP {e.code}\n{body}"
    except urllib.error.URLError as e:
        return f"Error: Could not connect to Brave Search API: {e.reason}"
    except Exception as e:
        return f"Error: {e}"

    parts: list[str] = []
    parts.append(f'=== Web Search: "{query}" ===')
    parts.append("")

    # AI Summary (if requested and available)
    if summary and "summarizer" in data and data["summarizer"].get("results"):
        summarizer = data["summarizer"]["results"][0]
        if summarizer.get("text"):
            parts.append("📝 AI Summary:")
            parts.append(summarizer["text"])
            parts.append("")
            parts.append("---")
            parts.append("")

    # Web results
    web_results = data.get("web", {}).get("results", [])

    if not web_results:
        parts.append("No results found.")
        return "\n".join(parts)

    parts.append(f"Found {len(web_results)} results:")
    parts.append("")

    for i, result in enumerate(web_results, 1):
        title = result.get("title", "No title")
        url_str = result.get("url", "")
        description = result.get("description", "No description")
        # Clean HTML tags from description
        import re
        description = re.sub(r"<[^>]+>", "", description)

        parts.append(f"[{i}] {title}")
        parts.append(f"    URL: {url_str}")
        parts.append(f"    {description}")
        parts.append("")

    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="Search the web using Brave Search API.")
    parser.add_argument("--query", required=True, help="The search query string")
    parser.add_argument("--count", type=int, default=5, help="Number of results (default: 5, max: 20)")
    parser.add_argument("--summary", action="store_true", default=False, help="Include AI summary")
    args = parser.parse_args()

    if not args.query.strip():
        print("Error: query cannot be empty", file=sys.stderr)
        sys.exit(1)

    output = brave_search(args.query, args.count, args.summary)
    print(output)


if __name__ == "__main__":
    main()
