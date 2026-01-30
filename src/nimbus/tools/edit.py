"""File edit tool for precise string replacement editing.

Architecture Layer: 0 (Infrastructure)
Von Neumann Role: ISA (Instruction Set Architecture)

This module provides a tool for editing files by replacing exact string matches,
with support for batch edits, three-tier matching, and automatic indentation preservation.

Features:
    - Search-and-Replace (SAR) block mode with batch edits support
    - Three-tier matching algorithm:
      - Tier 1: Exact match (fastest)
      - Tier 2: Indent-agnostic match (strip and compare)
      - Tier 3: Fuzzy match (85%+ similarity)
    - Handles common LLM output issues (escaped newlines, line number prefixes, etc.)
    - Uniqueness validation (unless replace_all=True)
    - Preserves file encoding (UTF-8 preferred, latin-1 fallback)
    - Automatic indentation preservation for non-exact matches

Example:
    >>> from pathlib import Path
    >>> # Single edit (legacy mode)
    >>> result = await edit_file(
    ...     "/project/src/main.py",
    ...     old_string="def hello():",
    ...     new_string="def greet():",
    ...     workspace=Path("/project")
    ... )
    >>> # Batch edit (recommended)
    >>> result = await edit_file(
    ...     "/project/src/main.py",
    ...     edits=[
    ...         {"search": "def foo():", "replace": "def bar():"},
    ...         {"search": "def baz():", "replace": "def qux():"},
    ...     ],
    ...     workspace=Path("/project")
    ... )
"""

__layer__ = 0  # Infrastructure Layer
__role__ = "ISA"  # Instruction Set Architecture

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, List, Optional

from ..core.logging import get_logger
from .base import ToolParameter, tool
from .sandbox import Sandbox, SandboxError

logger = get_logger("tools.edit")


@dataclass
class MatchResult:
    """Result of a search match.

    Attributes:
        start: Start position in content.
        end: End position in content.
        match_type: Type of match ("exact", "indent_agnostic", "fuzzy").
        matched_text: The actual text that was matched.
    """

    start: int
    end: int
    match_type: str  # "exact", "indent_agnostic", "fuzzy"
    matched_text: str


def _normalize_edit_string(text: str) -> str:
    """Normalize edit string to handle common LLM output issues.

    Handles:
        1. Cross-platform newline normalization (\\r\\n -> \\n)
        2. Line number prefixes from Read output (e.g., '   14->content')
        3. Escaped newlines (\\n -> actual newline)
        4. Escaped tabs (\\t -> actual tab)
        5. Double-escaped sequences (\\\\n -> newline)
        6. Markdown code block markers (```python ... ```)
        7. Trailing whitespace normalization

    Args:
        text: The text that may contain LLM output artifacts.

    Returns:
        Normalized text ready for matching.
    """
    original = text
    modifications = []

    # 0. Cross-platform newline normalization (Windows \r\n -> Unix \n)
    if '\r\n' in text:
        text = text.replace('\r\n', '\n')
        modifications.append("normalized CRLF to LF")
    if '\r' in text:
        text = text.replace('\r', '\n')
        modifications.append("normalized CR to LF")

    # 1. Remove line number prefixes (format: optional spaces + digits + arrow)
    lines = text.split('\n')
    cleaned_lines = []
    has_line_prefix = False

    for line in lines:
        # Match pattern: optional spaces + digits + arrow + content
        match = re.match(r'^(\s*\d+[→>])(.*)$', line)
        if match:
            has_line_prefix = True
            cleaned_lines.append(match.group(2))
        else:
            cleaned_lines.append(line)

    if has_line_prefix:
        modifications.append("removed line number prefixes")
    text = '\n'.join(cleaned_lines)

    # 2. Handle escaped characters (LLM often outputs \\n instead of actual newline)
    # Only process if string looks over-escaped (has \\n but no actual newlines)
    if '\\n' in text and '\n' not in text.replace('\\n', ''):
        text = text.replace('\\n', '\n')
        modifications.append("converted \\\\n to newlines")

    if '\\t' in text and '\t' not in text.replace('\\t', ''):
        text = text.replace('\\t', '\t')
        modifications.append("converted \\\\t to tabs")

    # Handle double-escaped sequences
    if '\\\\n' in text:
        text = text.replace('\\\\n', '\n')
        modifications.append("converted \\\\\\\\n to newlines")

    if '\\\\t' in text:
        text = text.replace('\\\\t', '\t')
        modifications.append("converted \\\\\\\\t to tabs")

    # 3. Remove Markdown code block markers
    original_len = len(text)
    text = re.sub(r'^```\w*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    if len(text) != original_len:
        modifications.append("removed markdown code blocks")

    # 4. Remove leading/trailing empty lines and stray backslashes
    # LLM sometimes outputs '\\\n' at the start (backslash + newline)
    text = text.strip()
    # Remove leading backslash if followed by nothing meaningful
    if text.startswith('\\') and len(text) > 1 and text[1] in '\n\r':
        text = text[1:].lstrip('\n\r')
        modifications.append("removed leading backslash")

    # 5. Normalize line endings (remove trailing whitespace, unify newlines)
    lines = text.split('\n')
    lines = [line.rstrip() for line in lines]
    text = '\n'.join(lines)

    if text != original and modifications:
        logger.warning(
            f"Edit: Normalized input string: {', '.join(modifications)} "
            f"(original length={len(original)}, new length={len(text)})"
        )

    return text


def _detect_indent(text: str) -> str:
    """Detect the base indentation of text.

    Returns the minimum indentation among all non-empty lines.
    This handles multi-line blocks where the first line may have no indent
    but subsequent lines are indented.

    Args:
        text: Text to analyze.

    Returns:
        The minimum indentation string (spaces/tabs) among non-empty lines.
    """
    lines = text.split('\n')
    indents = []

    for line in lines:
        if line.strip():
            indent = line[:len(line) - len(line.lstrip())]
            indents.append(indent)

    if not indents:
        return ""

    # Return minimum indent (by length)
    return min(indents, key=len)


def _reindent(text: str, target_indent: str) -> str:
    """Re-indent all lines in text to use target_indent.

    Args:
        text: Text to re-indent.
        target_indent: Target indentation string.

    Returns:
        Text with re-indented lines.
    """
    lines = text.split('\n')
    if not lines:
        return text

    # Detect current base indent
    current_indent = _detect_indent(text)

    # Re-indent each line
    result = []
    for line in lines:
        if line.strip():
            # Remove current indent, add target indent
            stripped = line.lstrip()
            extra_indent = line[:len(line) - len(line.lstrip())]
            if extra_indent.startswith(current_indent):
                extra_indent = extra_indent[len(current_indent):]
            result.append(target_indent + extra_indent + stripped)
        else:
            result.append(line)

    return '\n'.join(result)


def _strip_indent(text: str) -> str:
    """Strip leading whitespace from each line for comparison.

    Args:
        text: Text to process.

    Returns:
        Text with leading whitespace stripped from each line.
    """
    return '\n'.join(line.lstrip() for line in text.split('\n'))


# =============================================================================
# Enhanced Matching Functions (Learned from opencode's edit.ts)
# =============================================================================

def _levenshtein_distance(a: str, b: str) -> int:
    """Calculate Levenshtein distance between two strings.

    This is used for fuzzy matching in block anchor replacer.

    Args:
        a: First string
        b: Second string

    Returns:
        Edit distance (number of operations to transform a into b)
    """
    if not a or not b:
        return max(len(a), len(b))

    # Create matrix
    m, n = len(a), len(b)
    matrix = [[0] * (n + 1) for _ in range(m + 1)]

    # Initialize first row and column
    for i in range(m + 1):
        matrix[i][0] = i
    for j in range(n + 1):
        matrix[0][j] = j

    # Fill the matrix
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            matrix[i][j] = min(
                matrix[i - 1][j] + 1,      # deletion
                matrix[i][j - 1] + 1,      # insertion
                matrix[i - 1][j - 1] + cost  # substitution
            )

    return matrix[m][n]


def _levenshtein_similarity(a: str, b: str) -> float:
    """Calculate similarity ratio using Levenshtein distance.

    Returns:
        Similarity between 0.0 and 1.0 (1.0 = identical)
    """
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    distance = _levenshtein_distance(a, b)
    return 1.0 - (distance / max_len)


# Block anchor matching thresholds (from opencode)
SINGLE_CANDIDATE_SIMILARITY_THRESHOLD = 0.0  # Accept any match for single candidate
MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD = 0.3  # Require some similarity for multiple


def _block_anchor_match(
    content: str, search: str
) -> Optional[tuple[int, int, str]]:
    """Block anchor matching using first and last line as anchors.

    This is Stage 3 from opencode's 9-stage matching pipeline.
    Uses first and last line as anchors, then validates middle lines
    with Levenshtein similarity.

    Args:
        content: File content to search in
        search: Search string

    Returns:
        Tuple of (start_idx, end_idx, matched_text) or None
    """
    content_lines = content.split('\n')
    search_lines = search.split('\n')

    # Need at least 3 lines for anchor matching
    if len(search_lines) < 3:
        return None

    # Remove trailing empty line if present
    if search_lines and search_lines[-1] == '':
        search_lines = search_lines[:-1]

    if len(search_lines) < 2:
        return None

    first_line_search = search_lines[0].strip()
    last_line_search = search_lines[-1].strip()

    # Find all candidates where both anchors match
    candidates = []
    for i, line in enumerate(content_lines):
        if line.strip() != first_line_search:
            continue

        # Look for matching last line
        for j in range(i + 2, len(content_lines)):
            if content_lines[j].strip() == last_line_search:
                candidates.append((i, j))
                break  # Only first occurrence

    if not candidates:
        return None

    # Single candidate - accept with relaxed threshold
    if len(candidates) == 1:
        start_line, end_line = candidates[0]
        # Calculate similarity of middle lines
        similarity = _calculate_block_similarity(
            content_lines, search_lines, start_line, end_line
        )

        if similarity >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD:
            return _extract_block(content, content_lines, start_line, end_line)
        return None

    # Multiple candidates - find best match
    best_match = None
    max_similarity = -1.0

    for start_line, end_line in candidates:
        similarity = _calculate_block_similarity(
            content_lines, search_lines, start_line, end_line
        )
        if similarity > max_similarity:
            max_similarity = similarity
            best_match = (start_line, end_line)

    if max_similarity >= MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD and best_match:
        return _extract_block(content, content_lines, best_match[0], best_match[1])

    return None


def _calculate_block_similarity(
    content_lines: List[str],
    search_lines: List[str],
    start_line: int,
    end_line: int,
) -> float:
    """Calculate similarity of middle lines in a block match."""
    actual_block_size = end_line - start_line + 1
    search_block_size = len(search_lines)

    lines_to_check = min(search_block_size - 2, actual_block_size - 2)
    if lines_to_check <= 0:
        return 1.0  # No middle lines to check

    total_similarity = 0.0
    for j in range(1, min(search_block_size - 1, actual_block_size - 1)):
        content_line = content_lines[start_line + j].strip()
        search_line = search_lines[j].strip()
        total_similarity += _levenshtein_similarity(content_line, search_line)

    return total_similarity / lines_to_check


def _extract_block(
    content: str,
    content_lines: List[str],
    start_line: int,
    end_line: int,
) -> tuple[int, int, str]:
    """Extract a block of text and its position."""
    # Calculate start index
    start_idx = sum(len(content_lines[k]) + 1 for k in range(start_line))

    # Calculate end index
    end_idx = start_idx
    for k in range(start_line, end_line + 1):
        end_idx += len(content_lines[k])
        if k < end_line:
            end_idx += 1  # newline

    matched_text = content[start_idx:end_idx]
    return (start_idx, end_idx, matched_text)


def _whitespace_normalized_match(
    content: str, search: str
) -> Optional[tuple[int, int, str]]:
    """Match with whitespace normalization.

    Stage 4 from opencode: Normalizes consecutive whitespace to single space.

    Args:
        content: File content
        search: Search string

    Returns:
        Match result or None
    """
    def normalize_ws(text: str) -> str:
        return re.sub(r'\s+', ' ', text).strip()

    normalized_search = normalize_ws(search)
    if not normalized_search:
        return None

    # Try line-by-line matching
    lines = content.split('\n')
    for i, line in enumerate(lines):
        if normalize_ws(line) == normalized_search:
            # Found single line match
            start_idx = sum(len(lines[k]) + 1 for k in range(i))
            return (start_idx, start_idx + len(line), line)

    # Try multi-line matching
    search_lines = search.split('\n')
    if len(search_lines) > 1:
        for i in range(len(lines) - len(search_lines) + 1):
            block = '\n'.join(lines[i:i + len(search_lines)])
            if normalize_ws(block) == normalized_search:
                start_idx = sum(len(lines[k]) + 1 for k in range(i))
                return (start_idx, start_idx + len(block), block)

    return None


def _escape_normalized_match(
    content: str, search: str
) -> Optional[tuple[int, int, str]]:
    """Match with escape sequence normalization.

    Stage 6 from opencode: Unescapes common escape sequences before matching.

    Args:
        content: File content
        search: Search string

    Returns:
        Match result or None
    """
    def unescape(text: str) -> str:
        replacements = [
            ('\\n', '\n'),
            ('\\t', '\t'),
            ('\\r', '\r'),
            ("\\'", "'"),
            ('\\"', '"'),
            ('\\`', '`'),
            ('\\\\', '\\'),
            ('\\$', '$'),
        ]
        result = text
        for escaped, unescaped in replacements:
            result = result.replace(escaped, unescaped)
        return result

    unescaped_search = unescape(search)

    # Try exact match with unescaped search
    if unescaped_search in content:
        start_idx = content.index(unescaped_search)
        return (start_idx, start_idx + len(unescaped_search), unescaped_search)

    # Try matching escaped content against unescaped search
    unescaped_content = unescape(content)
    if unescaped_search in unescaped_content:
        # Find position in original content
        start_idx = unescaped_content.index(unescaped_search)
        # This is approximate - escape sequences may shift positions
        # For safety, do exact substring search nearby
        search_start = max(0, start_idx - 10)
        search_end = min(len(content), start_idx + len(unescaped_search) + 10)
        if unescaped_search in content[search_start:search_end]:
            actual_start = content.index(unescaped_search, search_start)
            return (actual_start, actual_start + len(unescaped_search), unescaped_search)

    return None


def _context_aware_match(
    content: str, search: str
) -> Optional[tuple[int, int, str]]:
    """Context-aware matching using first/last line anchors with 50% heuristic.

    Stage 8 from opencode: Uses first and last lines as anchors, validates
    that at least 50% of middle lines match when trimmed.

    Args:
        content: File content
        search: Search string

    Returns:
        Match result or None
    """
    content_lines = content.split('\n')
    search_lines = search.split('\n')

    if len(search_lines) < 2:
        return None

    # Remove trailing empty line
    if search_lines and search_lines[-1] == '':
        search_lines = search_lines[:-1]

    if len(search_lines) < 2:
        return None

    first_line_search = search_lines[0].strip()
    last_line_search = search_lines[-1].strip()

    # Find blocks with matching anchors
    for i in range(len(content_lines)):
        if content_lines[i].strip() != first_line_search:
            continue

        for j in range(i + 1, len(content_lines)):
            if content_lines[j].strip() != last_line_search:
                continue

            block_lines = content_lines[i:j + 1]

            # Check if block size matches
            if len(block_lines) == len(search_lines):
                # Validate middle lines with 50% threshold
                matching_lines = 0
                total_non_empty = 0

                for k in range(1, len(block_lines) - 1):
                    block_line = block_lines[k].strip()
                    search_line = search_lines[k].strip()

                    if block_line or search_line:
                        total_non_empty += 1
                        if block_line == search_line:
                            matching_lines += 1

                # Accept if >= 50% match or no middle lines
                if total_non_empty == 0 or matching_lines / total_non_empty >= 0.5:
                    return _extract_block(content, content_lines, i, j)

            break  # Only check first matching last line

    return None


def _fuzzy_find_in_content(
    content: str, search: str, threshold: float = 0.85
) -> Optional[tuple[int, int, float]]:
    """Find search string in content using fuzzy matching.

    Uses a sliding window approach with SequenceMatcher to find
    the best match above the similarity threshold.

    Args:
        content: The content to search in.
        search: The string to search for.
        threshold: Minimum similarity ratio (0.0 to 1.0). Defaults to 0.85.

    Returns:
        Tuple of (start_index, end_index, similarity_ratio) if found,
        None otherwise.
    """
    search_len = len(search)
    content_len = len(content)

    if search_len == 0 or search_len > content_len:
        return None

    best_ratio = 0.0
    best_pos = None

    # Optimization: use a step size for initial scan, then refine
    # For very long content, sample every N characters
    step = max(1, min(search_len // 4, 50))

    # First pass: coarse search
    candidate_positions = []
    for i in range(0, content_len - search_len + 1, step):
        candidate = content[i:i + search_len]
        # Quick check: if first and last chars match, it's worth checking
        if candidate[0] == search[0] or candidate[-1] == search[-1]:
            candidate_positions.append(i)

    # Also add positions around newlines (common edit boundaries)
    newline_positions = [i for i, c in enumerate(content) if c == '\n']
    for pos in newline_positions:
        if pos + 1 < content_len - search_len:
            candidate_positions.append(pos + 1)

    # Remove duplicates and sort
    candidate_positions = sorted(set(candidate_positions))

    # Limit search for performance (max 1000 candidates)
    if len(candidate_positions) > 1000:
        # Sample evenly
        step = len(candidate_positions) // 1000
        candidate_positions = candidate_positions[::step]

    # Second pass: check promising positions
    for i in candidate_positions:
        candidate = content[i:i + search_len]
        ratio = SequenceMatcher(None, search, candidate).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_pos = i

    # If we found a good match, refine around that position
    if best_pos is not None and best_ratio >= threshold * 0.9:
        # Search in a window around best position
        search_start = max(0, best_pos - step)
        search_end = min(content_len - search_len + 1, best_pos + step + 1)

        for i in range(search_start, search_end):
            candidate = content[i:i + search_len]
            ratio = SequenceMatcher(None, search, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_pos = i

    if best_ratio >= threshold and best_pos is not None:
        logger.info(f"Edit: Fuzzy match found with similarity {best_ratio:.2%}")
        return (best_pos, best_pos + search_len, best_ratio)

    return None


def _find_match_with_fallback(
    content: str, search: str, file_path: str
) -> Optional[MatchResult]:
    """Find search string in content using 9-stage matching pipeline.

    Matching stages (learned from opencode's edit.ts):
    1. Exact match (fastest)
    2. Line-trimmed match (strip each line, compare)
    3. Block anchor match (first/last line anchors + Levenshtein)
    4. Whitespace normalized match
    5. Indentation flexible match (same as tier 2 but with indent removal)
    6. Escape normalized match
    7. Hallucination truncation (remove trailing lines)
    8. Context-aware match (50% middle line similarity)
    9. Fuzzy match (85%+ SequenceMatcher similarity)

    Args:
        content: The file content to search in.
        search: The string to find.
        file_path: Path to file (for logging).

    Returns:
        MatchResult with (start, end, match_type, matched_text) or None.
    """
    # Stage 1: Exact match (SimpleReplacer)
    if search in content:
        start = content.index(search)
        return MatchResult(start, start + len(search), "exact", search)

    # Stage 2: Line-trimmed match (LineTrimmedReplacer)
    search_stripped = _strip_indent(search)
    content_lines = content.split('\n')
    search_lines = search.split('\n')

    for i in range(len(content_lines) - len(search_lines) + 1):
        candidate_lines = content_lines[i:i + len(search_lines)]
        candidate_stripped = _strip_indent('\n'.join(candidate_lines))
        if candidate_stripped == search_stripped:
            start = sum(len(line) + 1 for line in content_lines[:i])
            if i == 0:
                start = 0
            original_text = '\n'.join(candidate_lines)
            logger.info(
                f"Edit: Line-trimmed match found in {file_path} at line {i + 1}"
            )
            return MatchResult(
                start, start + len(original_text), "line_trimmed", original_text
            )

    # Stage 3: Block anchor match (BlockAnchorReplacer)
    block_result = _block_anchor_match(content, search)
    if block_result:
        start, end, matched_text = block_result
        logger.info(f"Edit: Block anchor match found in {file_path}")
        return MatchResult(start, end, "block_anchor", matched_text)

    # Stage 4: Whitespace normalized match (WhitespaceNormalizedReplacer)
    ws_result = _whitespace_normalized_match(content, search)
    if ws_result:
        start, end, matched_text = ws_result
        logger.info(f"Edit: Whitespace-normalized match found in {file_path}")
        return MatchResult(start, end, "whitespace_normalized", matched_text)

    # Stage 5: Indentation flexible match (IndentationFlexibleReplacer)
    # This is similar to stage 2 but removes base indentation first
    def remove_base_indent(text: str) -> str:
        lines = text.split('\n')
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            return text
        min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
        return '\n'.join(
            line if not line.strip() else line[min_indent:]
            for line in lines
        )

    normalized_search = remove_base_indent(search)
    for i in range(len(content_lines) - len(search_lines) + 1):
        candidate = '\n'.join(content_lines[i:i + len(search_lines)])
        if remove_base_indent(candidate) == normalized_search:
            start = sum(len(line) + 1 for line in content_lines[:i])
            if i == 0:
                start = 0
            logger.info(f"Edit: Indentation-flexible match found in {file_path}")
            return MatchResult(start, start + len(candidate), "indent_flexible", candidate)

    # Stage 6: Escape normalized match (EscapeNormalizedReplacer)
    escape_result = _escape_normalized_match(content, search)
    if escape_result:
        start, end, matched_text = escape_result
        logger.info(f"Edit: Escape-normalized match found in {file_path}")
        return MatchResult(start, end, "escape_normalized", matched_text)

    # Stage 7: Hallucination truncation (TrimmedBoundaryReplacer)
    if len(search_lines) >= 3:
        for trim_count in [1, 2]:
            trimmed_search = '\n'.join(search_lines[:-trim_count])
            if trimmed_search.strip():
                trimmed_stripped = _strip_indent(trimmed_search)
                trimmed_line_count = len(search_lines) - trim_count

                for i in range(len(content_lines) - trimmed_line_count + 1):
                    candidate_lines = content_lines[i:i + trimmed_line_count]
                    candidate_stripped = _strip_indent('\n'.join(candidate_lines))
                    if candidate_stripped == trimmed_stripped:
                        start = sum(len(line) + 1 for line in content_lines[:i])
                        if i == 0:
                            start = 0
                        original_text = '\n'.join(candidate_lines)
                        logger.warning(
                            f"Edit: Match found after truncating {trim_count} hallucinated line(s) "
                            f"from search text in {file_path}"
                        )
                        return MatchResult(
                            start, start + len(original_text), "truncated", original_text
                        )

    # Stage 8: Context-aware match (ContextAwareReplacer)
    context_result = _context_aware_match(content, search)
    if context_result:
        start, end, matched_text = context_result
        logger.info(f"Edit: Context-aware match found in {file_path}")
        return MatchResult(start, end, "context_aware", matched_text)

    # Stage 9: Fuzzy match (85%+ similarity)
    fuzzy_result = _fuzzy_find_in_content(content, search, threshold=0.85)
    if fuzzy_result:
        start, end, ratio = fuzzy_result
        matched_text = content[start:end]
        logger.info(
            f"Edit: Fuzzy match found in {file_path} with {ratio:.2%} similarity"
        )
        return MatchResult(start, end, "fuzzy", matched_text)

    return None


def _build_indent_map(original: str, search: str) -> dict:
    """Build a mapping from search line indices to original indentations.

    For indent-agnostic matching, we need to figure out how each line's
    indentation in the original differs from the search text.

    Args:
        original: The actual matched text from the file.
        search: The search text provided by user.

    Returns:
        Dict mapping line index to (original_indent, search_indent) pairs.
    """
    original_lines = original.split('\n')
    search_lines = search.split('\n')
    indent_map = {}

    for i, (orig_line, search_line) in enumerate(zip(original_lines, search_lines)):
        if orig_line.strip() and search_line.strip():
            orig_indent = orig_line[:len(orig_line) - len(orig_line.lstrip())]
            search_indent = search_line[:len(search_line) - len(search_line.lstrip())]
            indent_map[i] = (orig_indent, search_indent)

    return indent_map


def _apply_indent_transform(replace: str, search: str, indent_map: dict) -> str:
    """Apply indentation transformation from search to replacement.

    For each line in the replacement, if a corresponding search line exists,
    transform its indentation to match what the original had.

    Args:
        replace: Replacement text.
        search: Search text.
        indent_map: Mapping from line index to (original_indent, search_indent).

    Returns:
        Replacement text with transformed indentation.
    """
    if not indent_map:
        return replace

    replace_lines = replace.split('\n')
    search_lines = search.split('\n')
    result = []

    # Get the base offset from the first mapped line
    first_mapped_idx = min(indent_map.keys()) if indent_map else 0
    if first_mapped_idx in indent_map:
        orig_base, search_base = indent_map[first_mapped_idx]
        # Calculate base offset
        if len(orig_base) >= len(search_base):
            base_offset = orig_base[:len(orig_base) - len(search_base)]
        else:
            base_offset = ""
    else:
        base_offset = ""

    for i, repl_line in enumerate(replace_lines):
        if not repl_line.strip():
            result.append(repl_line)
            continue

        repl_indent = repl_line[:len(repl_line) - len(repl_line.lstrip())]
        repl_content = repl_line.lstrip()

        # If this line index has a mapping, use it to compute the new indent
        if i < len(search_lines) and i in indent_map:
            orig_indent, search_indent = indent_map[i]
            # Compute how much "extra" indent the replacement has vs search
            if len(repl_indent) >= len(search_indent):
                extra = repl_indent[len(search_indent):]
            else:
                extra = ""
            new_indent = orig_indent + extra
        else:
            # For new lines not in original, apply base offset
            new_indent = base_offset + repl_indent

        result.append(new_indent + repl_content)

    return '\n'.join(result)


def _apply_replacement(
    content: str, match: MatchResult, search: str, replace: str
) -> str:
    """Apply replacement, preserving original indentation if needed.

    For indent-agnostic and fuzzy matches, detect the indentation offset
    between original and search text, then apply the same offset to replacement.

    Args:
        content: Original file content.
        match: Match result from _find_match_with_fallback.
        search: Original search string.
        replace: Replacement string.

    Returns:
        Content with replacement applied.
    """
    if match.match_type == "exact":
        # Direct replacement
        return content[:match.start] + replace + content[match.end:]

    # For indent-agnostic/fuzzy: build indent map and transform replacement
    indent_map = _build_indent_map(match.matched_text, search)

    if indent_map:
        original_replace = replace
        replace = _apply_indent_transform(replace, search, indent_map)
        if replace != original_replace:
            logger.info("Edit: Applied indent transformation to replacement")

    return content[:match.start] + replace + content[match.end:]


def _read_file_with_encoding(file_path: Path) -> tuple[str, str, str]:
    """Read file content with encoding fallback and newline detection.

    Attempts to read file as UTF-8 first, falling back to latin-1
    if UTF-8 decoding fails. Normalizes newlines to \\n for processing.

    Args:
        file_path: Path to the file to read.

    Returns:
        Tuple of (content_normalized, encoding_used, original_newline).
        original_newline is 'crlf', 'cr', or 'lf'.

    Raises:
        OSError: If file cannot be read.
    """
    # Try UTF-8 first
    try:
        with open(file_path, "rb") as f:
            raw = f.read()
        content = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        # Fall back to latin-1 (which accepts any byte sequence)
        content = raw.decode("latin-1")
        encoding = "latin-1"

    # Detect and normalize newlines
    if '\r\n' in content:
        original_newline = 'crlf'
        content = content.replace('\r\n', '\n')
    elif '\r' in content:
        original_newline = 'cr'
        content = content.replace('\r', '\n')
    else:
        original_newline = 'lf'

    return content, encoding, original_newline


@tool(
    name="Edit",
    description="""Edit a file using search-and-replace blocks. Supports multiple edits in one call.

Execute this tool IMMEDIATELY without any preamble.

IMPORTANT RULES:
1. The 'old_string' (search block) must contain enough context (3-5 lines) to be globally unique
2. DO NOT use '...' to abbreviate code - output complete blocks
3. If multiple identical blocks exist, the edit will fail - ensure uniqueness
4. Indentation matters for exact match, but fuzzy matching may recover minor errors
5. NEVER include line number prefixes (e.g., "    5->") from Read output - only use actual content!
""",
    parameters=[
        ToolParameter(
            "file_path",
            "string",
            "Absolute path to the file to edit",
            required=True,
        ),
        ToolParameter(
            "old_string",
            "string",
            "[Legacy] The exact text to search for and replace. Must contain 3-5 lines of context for uniqueness.",
            required=False,
        ),
        ToolParameter(
            "new_string",
            "string",
            "[Legacy] The text to replace old_string with",
            required=False,
        ),
        ToolParameter(
            "edits",
            "array",
            "[Recommended] Array of {search, replace} objects for multiple edits. Example: [{\"search\": \"old\", \"replace\": \"new\"}]",
            required=False,
            items={"type": "object", "properties": {
                "search": {"type": "string", "description": "Text to search for"},
                "replace": {"type": "string", "description": "Text to replace with"},
            }},
        ),
        ToolParameter(
            "replace_all",
            "boolean",
            "Replace all occurrences instead of requiring unique match. Defaults to False.",
            required=False,
            default=False,
        ),
    ],
)
async def edit_file(
    file_path: str,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    edits: Optional[List[dict]] = None,
    replace_all: bool = False,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """Edit a file by replacing exact string matches.

    Supports two modes:
    1. Legacy mode: old_string + new_string for single replacement
    2. Batch mode: edits array for multiple replacements

    Uses three-tier matching:
    - Tier 1: Exact match (fastest)
    - Tier 2: Indent-agnostic match (strip and compare)
    - Tier 3: Fuzzy match (85%+ similarity)

    Args:
        file_path: Absolute path to the file to edit.
        old_string: [Legacy] The exact text to find and replace.
        new_string: [Legacy] The replacement text.
        edits: [Recommended] Array of {search, replace} dicts for batch edits.
        replace_all: If True, replace all occurrences. If False (default),
                     search text must appear exactly once in the file.
        workspace: Optional workspace directory for sandbox validation.

    Returns:
        Success message with replacement count and match type.

    Raises:
        SandboxError: If path escapes workspace.
        FileNotFoundError: If file doesn't exist.
        ValueError: If search text is empty, not found, or not unique.
        IsADirectoryError: If path points to a directory.

    Example:
        >>> # Legacy single replacement
        >>> result = await edit_file("/project/main.py", "def foo():", "def bar():")

        >>> # Batch replacements (recommended)
        >>> result = await edit_file(
        ...     "/project/main.py",
        ...     edits=[
        ...         {"search": "def foo():", "replace": "def bar():"},
        ...         {"search": "import old", "replace": "import new"},
        ...     ]
        ... )
    """
    # Validate parameters
    if not file_path:
        raise ValueError("file_path cannot be empty")

    # Build edit list from parameters
    edit_list: List[tuple[str, str]] = []

    if edits is not None:
        # Batch mode
        for edit in edits:
            search = edit.get("search", "")
            replace = edit.get("replace", "")
            if not search:
                raise ValueError("Each edit must have a non-empty 'search' field")
            edit_list.append((search, replace))
    elif old_string is not None:
        # Legacy mode
        if not old_string:
            raise ValueError("old_string cannot be empty")
        if new_string is None:
            raise ValueError("new_string is required when using old_string")
        edit_list.append((old_string, new_string))
    else:
        raise ValueError("Either 'edits' array or 'old_string'+'new_string' must be provided")

    # Normalize input strings
    normalized_edits = []
    for search, replace in edit_list:
        search = _normalize_edit_string(search)
        replace = _normalize_edit_string(replace)
        if search == replace:
            raise ValueError(f"search and replace cannot be the same: {search[:50]}...")
        normalized_edits.append((search, replace))

    # Determine workspace
    path_obj = Path(file_path)
    if workspace is None:
        workspace = path_obj.parent if path_obj.is_absolute() else Path.cwd()

    # Validate path with sandbox
    sandbox = Sandbox(workspace)
    try:
        resolved_path = sandbox.validate(file_path, must_exist=True)
    except SandboxError:
        raise
    except FileNotFoundError:
        raise FileNotFoundError(f"File not found: {file_path}")

    # Check if it's a directory
    if resolved_path.is_dir():
        raise IsADirectoryError(f"Path is a directory, not a file: {file_path}")

    # Read file content (normalized to \n for processing)
    try:
        content, encoding, original_newline = _read_file_with_encoding(resolved_path)
    except OSError as e:
        raise OSError(f"Cannot read file '{file_path}': {e}")

    # Apply edits sequentially
    results = []
    for search, replace in normalized_edits:
        # Find match with fallback strategies
        match = _find_match_with_fallback(content, search, file_path)

        if match is None:
            # Provide helpful error message
            error_msg = (
                f"Search text not found in file '{file_path}'. "
                "Make sure the text matches exactly including whitespace and indentation. "
                "Tip: Copy the exact text from the Read tool output without line numbers."
            )
            raise ValueError(error_msg)

        # For fuzzy matches, only allow single replacement (too risky for replace_all)
        if match.match_type == "fuzzy" and replace_all:
            logger.warning(
                "Edit: Fuzzy matching disabled for replace_all mode. "
                "Please provide exact text for bulk replacements."
            )
            raise ValueError(
                f"Search text not found exactly in file '{file_path}'. "
                "Fuzzy matching is not supported with replace_all=True. "
                "Please provide the exact text to replace."
            )

        # Count occurrences of the matched string
        occurrence_count = content.count(match.matched_text)

        if not replace_all and occurrence_count > 1:
            # Find line numbers of occurrences for helpful error message
            lines = content.splitlines(keepends=True)
            occurrence_lines = []
            for line_num, line in enumerate(lines, 1):
                if match.matched_text in line:
                    occurrence_lines.append(line_num)

            raise ValueError(
                f"Search text appears {occurrence_count} times in '{file_path}' "
                f"(lines: {', '.join(map(str, occurrence_lines[:5]))}{'...' if len(occurrence_lines) > 5 else ''}). "
                "Either provide more surrounding context to make it unique, "
                "or use replace_all=True to replace all occurrences."
            )

        # Perform replacement
        if replace_all:
            # Replace all occurrences
            new_content = content.replace(match.matched_text, replace)
            count = occurrence_count
        else:
            # Single replacement using position-based approach
            new_content = _apply_replacement(content, match, search, replace)
            count = 1

        results.append({
            "search": search[:50] + ("..." if len(search) > 50 else ""),
            "match_type": match.match_type,
            "count": count,
        })

        # Update content for next edit
        content = new_content

    # Restore original newline style before writing
    if original_newline == 'crlf':
        content = content.replace('\n', '\r\n')
    elif original_newline == 'cr':
        content = content.replace('\n', '\r')

    # Write back with same encoding
    try:
        with open(resolved_path, "wb") as f:
            f.write(content.encode(encoding))
    except OSError as e:
        raise OSError(f"Cannot write to file '{file_path}': {e}")

    # Build result message
    if len(results) == 1:
        r = results[0]
        match_info = ""
        if r["match_type"] == "indent_agnostic":
            match_info = " (matched after indent normalization)"
        elif r["match_type"] == "fuzzy":
            match_info = " (matched using fuzzy matching)"
        return f"The file {resolved_path} has been updated successfully{match_info}."
    else:
        # Multiple edits
        summary_parts = []
        for i, r in enumerate(results, 1):
            summary_parts.append(
                f"  {i}. '{r['search']}' ({r['match_type']}, {r['count']} occurrence(s))"
            )
        summary = "\n".join(summary_parts)
        return (
            f"The file {resolved_path} has been updated with {len(results)} edits:\n"
            f"{summary}"
        )
