"""Text summarization and keyword extraction skills."""

import re
from collections import Counter
from typing import List


async def summarize_text(
    text: str = "",
    max_length: int = 200,
    **kwargs,
) -> str:
    """Summarize text by extracting key sentences.

    MVP implementation uses simple heuristics. Can be extended
    to use LLM-based summarization.

    Args:
        text: Text to summarize.
        max_length: Maximum length of summary in characters.
        **kwargs: Accepts 'prompt' or 'source' as aliases for 'text'.

    Returns:
        Summarized text.
    """
    # Support 'prompt' or 'source' as aliases for 'text' (LLM may generate either)
    actual_text = text or kwargs.get("prompt", "") or kwargs.get("source", "")
    if not actual_text or not actual_text.strip():
        return "\u65e0\u5185\u5bb9\u53ef\u4ee5\u603b\u7ed3\u3002"

    text = actual_text.strip()

    # If text is already short, return as-is
    if len(text) <= max_length:
        return text

    # Split into sentences (Chinese and English)
    sentences = re.split(r'[.!?\u3002\uff01\uff1f\n]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return text[:max_length] + "..."

    # Score sentences by position and keywords
    scored = []
    keywords = set(await extract_keywords(text, top_k=10))

    for i, sentence in enumerate(sentences):
        score = 0
        # First and last sentences often important
        if i == 0:
            score += 3
        if i == len(sentences) - 1:
            score += 1
        # Keyword presence
        for kw in keywords:
            if kw in sentence:
                score += 1
        scored.append((score, i, sentence))

    # Sort by score, take top sentences
    scored.sort(key=lambda x: (-x[0], x[1]))

    summary_parts = []
    current_length = 0

    for _, orig_idx, sentence in scored:
        if current_length + len(sentence) > max_length:
            break
        summary_parts.append((orig_idx, sentence))
        current_length += len(sentence) + 1

    # Restore original order
    summary_parts.sort(key=lambda x: x[0])
    summary = "\u3002".join([s for _, s in summary_parts])

    if len(summary) > max_length:
        summary = summary[:max_length-3] + "..."

    return summary if summary else text[:max_length] + "..."


async def extract_keywords(text: str, top_k: int = 5) -> List[str]:
    """Extract keywords from text using simple frequency analysis.

    MVP implementation uses word frequency with stopword filtering.
    Can be extended to use TF-IDF, TextRank, or LLM extraction.

    Args:
        text: Text to extract keywords from.
        top_k: Number of top keywords to return.

    Returns:
        List of keywords.
    """
    if not text or not text.strip():
        return []

    # Chinese and English stopwords
    stopwords = {
        # Chinese
        "\u7684", "\u4e86", "\u662f", "\u5728", "\u6211", "\u6709", "\u548c", "\u5c31",
        "\u4e0d", "\u4eba", "\u90fd", "\u4e00", "\u4e00\u4e2a", "\u4e0a", "\u4e5f", "\u5f88",
        "\u5230", "\u8bf4", "\u8981", "\u53bb", "\u4f60", "\u4f1a", "\u7740", "\u6ca1\u6709",
        "\u770b", "\u597d", "\u81ea\u5df1", "\u8fd9", "\u90a3", "\u5979", "\u4ed6",
        # English
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "must", "shall",
        "can", "need", "dare", "ought", "used", "to", "of", "in",
        "for", "on", "with", "at", "by", "from", "as", "into",
        "through", "during", "before", "after", "above", "below",
        "between", "under", "again", "further", "then", "once",
        "and", "but", "or", "nor", "so", "yet", "both", "either",
        "neither", "not", "only", "own", "same", "than", "too",
        "very", "just", "also", "now", "here", "there", "when",
        "where", "why", "how", "all", "each", "every", "both",
        "few", "more", "most", "other", "some", "such", "no",
        "any", "it", "its", "this", "that", "these", "those",
    }

    # Tokenize: split on non-word characters, keep Chinese chars
    words = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', text.lower())

    # Filter stopwords and short words
    words = [w for w in words if w not in stopwords and len(w) > 1]

    # Count frequencies
    counter = Counter(words)

    # Return top-k
    return [word for word, _ in counter.most_common(top_k)]


async def summarize_with_keywords(
    text: str, max_length: int = 200, top_k: int = 5
) -> dict:
    """Summarize text and extract keywords together.

    Args:
        text: Text to analyze.
        max_length: Maximum summary length.
        top_k: Number of keywords.

    Returns:
        Dict with 'summary' and 'keywords' keys.
    """
    summary = await summarize_text(text, max_length)
    keywords = await extract_keywords(text, top_k)

    return {
        "summary": summary,
        "keywords": keywords,
    }
