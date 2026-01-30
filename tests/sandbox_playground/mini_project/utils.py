"""Utility functions."""
import re
from typing import List


def slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    return text


def truncate(text: str, max_length: int = 50, suffix: str = "...") -> str:
    """Truncate text to max length."""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix


def parse_tags(tag_string: str) -> List[str]:
    """Parse comma-separated tags."""
    if not tag_string:
        return []
    tags = [t.strip() for t in tag_string.split(",")]
    return [t for t in tags if t]  # Remove empty tags
