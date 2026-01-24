"""Token estimation utilities."""


def estimate_tokens(text: str) -> int:
    """Estimate token count for text.

    Uses a simple heuristic: roughly 1 token per 3 characters for English,
    and approximately 1 token per 1.5 characters for Chinese/mixed content.

    Args:
        text: Input text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    if not text:
        return 0

    # Count Chinese characters
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars

    # Chinese: ~1 token per 1.5 chars, Other: ~1 token per 3 chars
    chinese_tokens = int(chinese_chars / 1.5) if chinese_chars else 0
    other_tokens = other_chars // 3

    return max(1, chinese_tokens + other_tokens)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately fit within token limit.

    Args:
        text: Input text to truncate.
        max_tokens: Maximum allowed tokens.

    Returns:
        Truncated text.
    """
    if estimate_tokens(text) <= max_tokens:
        return text

    # Estimate character limit (conservative: assume 2 chars per token)
    char_limit = max_tokens * 2

    if len(text) <= char_limit:
        return text

    return text[:char_limit] + "..."
