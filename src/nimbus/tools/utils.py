"""Pi Tools - Shared Utility Functions

This module provides utility functions shared across Pi tools.
Based on pi-coding-agent source code.
"""

import difflib
import re
from typing import Any, Dict, Optional, Tuple

# =============================================================================
# Constants
# =============================================================================

# SMART LIMITS: Optimized for modern 100k+ context LLMs (Claude, GPT-4, etc.)
# Previous: 300 lines / 12KB (too conservative for modern models)
# Current: Intelligent scaling based on context capacity and file size

# Base limits for 100k context models (8x improvement over legacy limits)
DEFAULT_MAX_LINES = 2000  # ~100KB for typical code files
DEFAULT_MAX_BYTES = 100 * 1024  # 100KB ≈ 33k tokens (vs old 12KB)


def get_smart_limits(context_capacity: Optional[int] = None, file_size: Optional[int] = None) -> tuple[int, int]:
    """
    Calculate smart limits based on context capacity and file size.

    Args:
        context_capacity: Max context tokens (default: 100k)
        file_size: File size in bytes (for optimization hints)

    Returns:
        Tuple of (max_lines, max_bytes)
    """
    context_capacity = context_capacity or 100_000  # Default to 100k

    # Simple scaling: larger context = more generous limits
    if context_capacity >= 200_000:
        # Very large context (200k+): be generous
        max_lines = 4000
        max_bytes = 200 * 1024  # 200KB
    elif context_capacity >= 100_000:
        # Large context (100k+): use default modern limits
        max_lines = DEFAULT_MAX_LINES
        max_bytes = DEFAULT_MAX_BYTES
    elif context_capacity >= 32_000:
        # Medium context (32k-100k): scale down
        max_lines = 1000
        max_bytes = 50 * 1024  # 50KB
    else:
        # Small context (<32k): be conservative
        max_lines = 300
        max_bytes = 12 * 1024  # 12KB

    # File size optimization: if file is small, read it all
    if file_size and file_size <= max_bytes // 4:
        # File is small (< 25% of limit), no need to truncate
        return max_lines * 4, max_bytes * 4

    return max_lines, max_bytes


# =============================================================================
# Formatting
# =============================================================================


def format_size(bytes_count: int) -> str:
    """Format bytes as human-readable size."""
    if bytes_count < 1024:
        return f"{bytes_count}B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f}KB"
    else:
        return f"{bytes_count / (1024 * 1024):.1f}MB"


# =============================================================================
# Line Ending Handling
# =============================================================================


def detect_line_ending(content: str) -> str:
    """Detect line ending style (CRLF vs LF)."""
    crlf_idx = content.find("\r\n")
    lf_idx = content.find("\n")

    if lf_idx == -1:
        return "\n"
    if crlf_idx == -1:
        return "\n"

    return "\r\n" if crlf_idx < lf_idx else "\n"


def normalize_to_lf(text: str) -> str:
    """Normalize all line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: str) -> str:
    """Restore original line ending style."""
    return text.replace("\n", ending) if ending == "\r\n" else text


def strip_bom(content: str) -> Tuple[str, str]:
    """Strip UTF-8 BOM if present, return (bom, text_without_bom)."""
    if content.startswith("\ufeff"):
        return "\ufeff", content[1:]
    return "", content


# =============================================================================
# Fuzzy Matching for Edit Tool
# =============================================================================


def normalize_for_fuzzy_match(text: str) -> str:
    """
    Normalize text for fuzzy matching.
    - Strip trailing whitespace from each line
    - Normalize smart quotes to ASCII
    - Normalize Unicode dashes to ASCII hyphen
    - Normalize special Unicode spaces to regular space
    """
    lines = text.split("\n")
    normalized_lines = [line.rstrip() for line in lines]
    result = "\n".join(normalized_lines)

    # Smart single quotes → '
    result = re.sub(r"[\u2018\u2019\u201a\u201b]", "'", result)

    # Smart double quotes → "
    result = re.sub(r"[\u201c\u201d\u201e\u201f]", '"', result)

    # Various dashes → -
    result = re.sub(r"[\u2010-\u2015\u2212]", "-", result)

    # Special spaces → regular space
    result = re.sub(r"[\u00a0\u2002-\u200a\u202f\u205f\u3000]", " ", result)

    return result


def fuzzy_find_text(content: str, old_text: str) -> Dict[str, Any]:
    """
    Find old_text in content, trying exact match first, then fuzzy.

    Returns:
        {
            'found': bool,
            'index': int,
            'match_length': int,
            'used_fuzzy_match': bool,
            'content_for_replacement': str
        }
    """
    # Try exact match first
    exact_index = content.find(old_text)
    if exact_index != -1:
        return {
            "found": True,
            "index": exact_index,
            "match_length": len(old_text),
            "used_fuzzy_match": False,
            "content_for_replacement": content,
        }

    # Try fuzzy match
    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old_text = normalize_for_fuzzy_match(old_text)

    fuzzy_index = fuzzy_content.find(fuzzy_old_text)
    if fuzzy_index == -1:
        return {
            "found": False,
            "index": -1,
            "match_length": 0,
            "used_fuzzy_match": False,
            "content_for_replacement": content,
        }

    return {
        "found": True,
        "index": fuzzy_index,
        "match_length": len(fuzzy_old_text),
        "used_fuzzy_match": True,
        "content_for_replacement": fuzzy_content,
    }


# =============================================================================
# Diff Generation
# =============================================================================


def generate_unified_diff(
    old_content: str, new_content: str, context_lines: int = 4
) -> Dict[str, Any]:
    """
    Generate unified diff with line numbers.

    Returns:
        {
            'diff': str,  # Formatted diff string
            'first_changed_line': int  # First changed line number
        }
    """
    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")

    # Use difflib to compute the diff
    diff = difflib.unified_diff(old_lines, new_lines, lineterm="", n=context_lines)

    diff_lines = []
    first_changed_line = None
    line_num = 0

    for line in diff:
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            # Parse line number from @@ -1,5 +1,6 @@
            match = re.search(r"\+(\d+)", line)
            if match and first_changed_line is None:
                line_num = int(match.group(1))
                first_changed_line = line_num
            continue

        if line.startswith("+"):
            diff_lines.append(f"+{line_num:4d} {line[1:]}")
            line_num += 1
        elif line.startswith("-"):
            diff_lines.append(f"-{line_num:4d} {line[1:]}")
        else:
            diff_lines.append(f" {line_num:4d} {line[1:]}")
            line_num += 1

    return {"diff": "\n".join(diff_lines), "first_changed_line": first_changed_line or 1}


# =============================================================================
# Content Truncation
# =============================================================================


def truncate_head(
    content: str, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES
) -> Dict[str, Any]:
    """
    Truncate content from the head (keep first N lines/bytes).
    Used for Read tool.
    """
    total_bytes = len(content.encode("utf-8"))
    lines = content.split("\n")
    total_lines = len(lines)

    # No truncation needed
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return {
            "content": content,
            "truncated": False,
            "truncated_by": None,
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "output_lines": total_lines,
            "output_bytes": total_bytes,
            "first_line_exceeds_limit": False,
        }

    # Check if first line exceeds byte limit
    first_line_bytes = len(lines[0].encode("utf-8"))
    if first_line_bytes > max_bytes:
        return {
            "content": "",
            "truncated": True,
            "truncated_by": "bytes",
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "output_lines": 0,
            "output_bytes": 0,
            "first_line_exceeds_limit": True,
        }

    # Collect complete lines that fit
    output_lines = []
    output_bytes = 0
    truncated_by = "lines"

    for i, line in enumerate(lines):
        if i >= max_lines:
            truncated_by = "lines"
            break

        line_bytes = len(line.encode("utf-8")) + (1 if i > 0 else 0)  # +1 for \n
        if output_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break

        output_lines.append(line)
        output_bytes += line_bytes

    output_content = "\n".join(output_lines)

    return {
        "content": output_content,
        "truncated": True,
        "truncated_by": truncated_by,
        "total_lines": total_lines,
        "total_bytes": total_bytes,
        "output_lines": len(output_lines),
        "output_bytes": len(output_content.encode("utf-8")),
        "first_line_exceeds_limit": False,
    }


def truncate_tail(
    content: str, max_lines: int = DEFAULT_MAX_LINES, max_bytes: int = DEFAULT_MAX_BYTES
) -> Dict[str, Any]:
    """
    Truncate content from the tail (keep last N lines/bytes).
    Used for Bash tool.
    """
    total_bytes = len(content.encode("utf-8"))
    lines = content.split("\n")
    total_lines = len(lines)

    # No truncation needed
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return {
            "content": content,
            "truncated": False,
            "total_lines": total_lines,
            "total_bytes": total_bytes,
            "output_lines": total_lines,
            "output_bytes": total_bytes,
        }

    # Work backwards from the end
    output_lines = []
    output_bytes = 0

    for i in range(len(lines) - 1, -1, -1):
        if len(output_lines) >= max_lines:
            break

        line = lines[i]
        line_bytes = len(line.encode("utf-8")) + (1 if output_lines else 0)

        if output_bytes + line_bytes > max_bytes:
            break

        output_lines.insert(0, line)
        output_bytes += line_bytes

    output_content = "\n".join(output_lines)

    return {
        "content": output_content,
        "truncated": True,
        "total_lines": total_lines,
        "total_bytes": total_bytes,
        "output_lines": len(output_lines),
        "output_bytes": len(output_content.encode("utf-8")),
    }
