"""Web search skill for information retrieval."""

from typing import List, Dict, Any


# Mock search results for MVP
_MOCK_RESULTS: Dict[str, List[Dict[str, str]]] = {
    "python": [
        {"title": "Python \u5b98\u65b9\u6559\u7a0b", "url": "https://docs.python.org/zh-cn/3/tutorial/", "snippet": "Python \u5165\u95e8\u6307\u5357\uff0c\u6db5\u76d6\u57fa\u7840\u8bed\u6cd5\u548c\u6807\u51c6\u5e93..."},
        {"title": "Python \u7f16\u7a0b\u5feb\u901f\u4e0a\u624b", "url": "https://example.com/python-quick", "snippet": "30\u5206\u949f\u638c\u63e1 Python \u57fa\u7840..."},
    ],
    "machine learning": [
        {"title": "\u673a\u5668\u5b66\u4e60\u5165\u95e8", "url": "https://example.com/ml-intro", "snippet": "\u4ece\u96f6\u5f00\u59cb\u5b66\u4e60\u673a\u5668\u5b66\u4e60..."},
        {"title": "TensorFlow \u6559\u7a0b", "url": "https://tensorflow.org/tutorials", "snippet": "\u4f7f\u7528 TensorFlow \u6784\u5efa\u795e\u7ecf\u7f51\u7edc..."},
    ],
}


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for information.

    MVP implementation returns mock results. Can be extended to use
    real search APIs (Google, Bing, DuckDuckGo, etc.).

    Args:
        query: Search query string.
        max_results: Maximum number of results to return.

    Returns:
        Formatted search results as string.
    """
    results = _search_mock(query, max_results)

    if not results:
        return f"\u672a\u627e\u5230\u4e0e '{query}' \u76f8\u5173\u7684\u7ed3\u679c\u3002"

    output_lines = [f"\u641c\u7d22 '{query}' \u7684\u7ed3\u679c:\n"]
    for i, result in enumerate(results, 1):
        output_lines.append(f"{i}. {result['title']}")
        output_lines.append(f"   {result['url']}")
        output_lines.append(f"   {result['snippet']}\n")

    return "\n".join(output_lines)


def _search_mock(query: str, max_results: int) -> List[Dict[str, str]]:
    """Mock search implementation.

    Args:
        query: Search query.
        max_results: Max results to return.

    Returns:
        List of mock search results.
    """
    query_lower = query.lower()

    # Check for keyword matches
    for keyword, results in _MOCK_RESULTS.items():
        if keyword in query_lower:
            return results[:max_results]

    # Default generic results
    return [
        {
            "title": f"\u5173\u4e8e '{query}' \u7684\u4fe1\u606f",
            "url": f"https://example.com/search?q={query.replace(' ', '+')}",
            "snippet": f"\u8fd9\u662f\u4e00\u4e2a\u5173\u4e8e {query} \u7684\u6a21\u62df\u641c\u7d22\u7ed3\u679c...",
        }
    ][:max_results]


async def search_with_context(
    query: str, context: str = "", max_results: int = 5
) -> str:
    """Search with additional context for better results.

    Args:
        query: Search query.
        context: Additional context to refine search.
        max_results: Maximum results.

    Returns:
        Formatted search results.
    """
    # In a real implementation, context would be used to refine the query
    enhanced_query = f"{query} {context}".strip() if context else query
    return await web_search(enhanced_query, max_results)
