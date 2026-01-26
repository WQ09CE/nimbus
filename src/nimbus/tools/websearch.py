"""WebSearch tool for searching the web using DuckDuckGo.

This module provides a tool for searching the web using DuckDuckGo,
returning results formatted in markdown for easy reading.

Example:
    >>> result = await web_search("python async programming")
    >>> print(result)
    Found 10 results for "python async programming":

    1. **[Async IO in Python: A Complete Walkthrough](https://realpython.com/...)**
       Learn how to use async/await syntax in Python...
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional

from .base import ToolParameter, tool

# Default settings
DEFAULT_MAX_RESULTS = 10
DEFAULT_REGION = "wt-wt"  # Worldwide
DEFAULT_TIMEOUT = 30

# Thread pool for running sync operations
_executor = ThreadPoolExecutor(max_workers=4)


class WebSearchError(Exception):
    """Exception raised when web search fails.

    Attributes:
        message: Error description.
        original_error: The underlying exception, if any.
    """

    def __init__(self, message: str, original_error: Optional[Exception] = None):
        self.message = message
        self.original_error = original_error
        super().__init__(message)


def _perform_search(
    query: str,
    max_results: int,
    region: str,
    time_range: Optional[str],
) -> List[dict]:
    """Perform the actual search using duckduckgo-search.

    This function runs synchronously and should be called in an executor.

    Args:
        query: The search query.
        max_results: Maximum number of results to return.
        region: Region code for search.
        time_range: Optional time range filter.

    Returns:
        List of search result dictionaries.

    Raises:
        WebSearchError: If search fails.
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError as e:
        raise WebSearchError(
            "duckduckgo-search library not installed. "
            "Install it with: pip install duckduckgo-search",
            original_error=e,
        )

    try:
        with DDGS() as ddgs:
            # Build search parameters
            search_kwargs = {
                "keywords": query,
                "region": region,
                "max_results": max_results,
            }

            # Add time range if specified
            if time_range:
                search_kwargs["timelimit"] = time_range

            # Perform search
            results = list(ddgs.text(**search_kwargs))
            return results

    except Exception as e:
        error_msg = str(e)

        # Handle common errors with better messages
        if "Ratelimit" in error_msg or "429" in error_msg:
            raise WebSearchError(
                "DuckDuckGo rate limit reached. Please wait a moment and try again.",
                original_error=e,
            )
        elif "timeout" in error_msg.lower():
            raise WebSearchError(
                "Search request timed out. Please try again.",
                original_error=e,
            )
        else:
            raise WebSearchError(
                f"Search failed: {error_msg}",
                original_error=e,
            )


def _format_results(query: str, results: List[dict]) -> str:
    """Format search results as markdown.

    Args:
        query: Original search query.
        results: List of search result dictionaries.

    Returns:
        Formatted markdown string.
    """
    if not results:
        return f'No results found for "{query}".'

    lines = [f'Found {len(results)} results for "{query}":', ""]

    for i, result in enumerate(results, 1):
        title = result.get("title", "Untitled")
        url = result.get("href", result.get("link", ""))
        snippet = result.get("body", result.get("snippet", ""))

        # Format as markdown list item
        if url:
            lines.append(f"{i}. **[{title}]({url})**")
        else:
            lines.append(f"{i}. **{title}**")

        if snippet:
            # Clean up snippet - remove extra whitespace
            snippet = " ".join(snippet.split())
            lines.append(f"   {snippet}")

        lines.append("")

    # Add source attribution
    lines.append("---")
    lines.append("Source: DuckDuckGo")

    return "\n".join(lines)


def _validate_time_range(time_range: Optional[str]) -> Optional[str]:
    """Validate and normalize time range parameter.

    Args:
        time_range: Time range string ('d', 'w', 'm', 'y').

    Returns:
        Normalized time range or None.

    Raises:
        ValueError: If time range is invalid.
    """
    if time_range is None:
        return None

    valid_ranges = {"d", "w", "m", "y"}
    normalized = time_range.lower().strip()

    if normalized not in valid_ranges:
        raise ValueError(
            f"Invalid time_range: '{time_range}'. "
            f"Must be one of: 'd' (day), 'w' (week), 'm' (month), 'y' (year)."
        )

    return normalized


@tool(
    name="WebSearch",
    description=(
        "Search the web using DuckDuckGo. "
        "Returns search results with titles, URLs, and snippets. "
        "Useful for finding up-to-date information, documentation, "
        "tutorials, and current events. "
        "Results are formatted as markdown with clickable links."
    ),
    parameters=[
        ToolParameter(
            "query",
            "string",
            "The search query. Be specific for better results.",
            required=True,
        ),
        ToolParameter(
            "max_results",
            "integer",
            "Maximum number of results to return (1-20). Defaults to 10.",
            required=False,
            default=DEFAULT_MAX_RESULTS,
        ),
        ToolParameter(
            "region",
            "string",
            (
                "Region for search results. Examples: 'us-en' (US English), "
                "'cn-zh' (China Chinese), 'wt-wt' (worldwide). Defaults to 'wt-wt'."
            ),
            required=False,
            default=DEFAULT_REGION,
        ),
        ToolParameter(
            "time_range",
            "string",
            (
                "Filter results by time: 'd' (past day), 'w' (past week), "
                "'m' (past month), 'y' (past year). Omit for all time."
            ),
            required=False,
            enum=["d", "w", "m", "y"],
        ),
    ],
)
async def web_search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    region: str = DEFAULT_REGION,
    time_range: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Search the web using DuckDuckGo.

    Performs a web search and returns formatted results with titles,
    URLs, and snippets. Uses DuckDuckGo as the search engine, which
    requires no API key.

    Features:
        - No API key required
        - Multiple region support
        - Time-based filtering
        - Markdown formatted output

    Args:
        query: The search query. Cannot be empty.
        max_results: Maximum number of results (1-20). Defaults to 10.
        region: Region code for localized results. Defaults to worldwide.
        time_range: Filter by time ('d', 'w', 'm', 'y'). None for all time.
        **kwargs: Additional context (unused).

    Returns:
        Formatted markdown string with search results.

    Raises:
        ValueError: If query is empty or parameters are invalid.
        WebSearchError: If search fails (rate limit, timeout, etc.).

    Example:
        >>> result = await web_search("python web scraping")
        >>> print(result)
        Found 10 results for "python web scraping":

        1. **[Beautiful Soup Tutorial](https://example.com/beautifulsoup)**
           Learn how to use Beautiful Soup for web scraping in Python...

        2. **[Scrapy Documentation](https://scrapy.org/)**
           Scrapy is a fast high-level web crawling framework...

        ---
        Source: DuckDuckGo
    """
    # Validate query
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty.")

    query = query.strip()

    # Validate and normalize max_results
    if max_results < 1:
        max_results = 1
    elif max_results > 20:
        max_results = 20

    # Validate time_range
    validated_time_range = _validate_time_range(time_range)

    # Run search in thread pool executor (duckduckgo-search is synchronous)
    loop = asyncio.get_event_loop()

    try:
        results = await loop.run_in_executor(
            _executor,
            _perform_search,
            query,
            max_results,
            region,
            validated_time_range,
        )
    except WebSearchError:
        raise
    except Exception as e:
        raise WebSearchError(f"Unexpected error during search: {e}", original_error=e)

    # Format and return results
    return _format_results(query, results)


def clear_executor() -> None:
    """Shutdown the thread pool executor.

    Call this when the application is shutting down to clean up resources.
    """
    _executor.shutdown(wait=False)
