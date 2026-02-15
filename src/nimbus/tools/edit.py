"""Edit Tool - Enhanced with fuzzy matching, BOM/CRLF preservation, diff generation

Based on pi-coding-agent implementation with:
- Exact match first, fuzzy match fallback
- BOM preservation (UTF-8 BOM handling)
- Line ending preservation (CRLF vs LF)
- Unified diff generation
- Multiple occurrence detection

Example:
    >>> result = await edit_file("test.py", "def  hello():", "def hello():")
    >>> print(result)
    Successfully replaced text in test.py.
    [Diff shows +/- changes]
"""

from pathlib import Path
from typing import Any, Optional

from .utils import (
    detect_line_ending,
    fuzzy_find_text,
    generate_unified_diff,
    normalize_for_fuzzy_match,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)


async def edit_file(
    file_path: str,
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    # Backward compatibility aliases
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """
    Edit a file by replacing exact text. Falls back to fuzzy matching.
    Preserves BOM and line endings.

    Strategy:
    1. Try exact match first
    2. If fails, try fuzzy match (normalize whitespace/quotes/dashes)
    3. Preserve UTF-8 BOM if present
    4. Preserve original line endings (CRLF/LF)
    5. Generate unified diff for review

    Args:
        file_path: Path to file (relative or absolute)
        old_text: Text to find and replace
        new_text: Text to replace with
        workspace: Workspace root for relative paths

    Returns:
        Success message with diff preview

    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If text not found or appears multiple times
    """
    # Handle backward compatibility (old_string/new_string → old_text/new_text)
    if old_text is None and old_string is not None:
        old_text = old_string
    if new_text is None and new_string is not None:
        new_text = new_string

    if old_text is None or new_text is None:
        raise ValueError("old_text and new_text are required")

    # Resolve path
    path_obj = Path(file_path)
    if workspace is None:
        workspace = path_obj.parent if path_obj.is_absolute() else Path.cwd()

    if path_obj.is_absolute():
        resolved_path = path_obj.resolve()
    else:
        resolved_path = (workspace / file_path).resolve()

    if not resolved_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    try:
        # Read file
        with open(resolved_path, "r", encoding="utf-8") as f:
            raw_content = f.read()
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8: {file_path}")
    except Exception as e:
        raise OSError(f"Failed to read file: {str(e)}")

    # Strip BOM and detect line ending
    bom, content = strip_bom(raw_content)
    original_ending = detect_line_ending(content)

    # Normalize to LF for processing
    normalized_content = normalize_to_lf(content)
    normalized_old_text = normalize_to_lf(old_text)
    normalized_new_text = normalize_to_lf(new_text)

    # Find the old text (exact → fuzzy fallback)
    match_result = fuzzy_find_text(normalized_content, normalized_old_text)

    if not match_result["found"]:
        # Find closest matching block for diagnostic diff
        from difflib import SequenceMatcher, unified_diff

        closest = None
        old_lines = normalized_old_text.splitlines()
        content_lines = normalized_content.splitlines()

        # Search for the first non-empty line of old_text in file content
        first_line = next((l.strip() for l in old_lines if l.strip()), "")
        if first_line and content_lines:
            best_ratio = 0.0
            best_idx = -1
            for i, line in enumerate(content_lines):
                ratio = SequenceMatcher(None, first_line, line.strip()).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_idx = i

            if best_ratio >= 0.4 and best_idx >= 0:
                block_size = len(old_lines)
                end = min(best_idx + block_size, len(content_lines))
                closest = "\n".join(content_lines[best_idx:end])

        if closest:
            diff_lines = list(unified_diff(
                normalized_old_text.splitlines(keepends=True),
                closest.splitlines(keepends=True),
                fromfile="your old_string",
                tofile="actual content in file",
                n=2,
            ))
            diff_text = "".join(diff_lines[:30])  # Cap at 30 lines
            raise ValueError(
                f"Could not find the exact text to replace in {file_path}.\n"
                f"Closest match found (similarity diff):\n{diff_text}\n"
                f"Check for whitespace, indentation, or content differences."
            )
        else:
            raise ValueError(
                f"Could not find the exact text to replace in {file_path}. "
                f"No similar text found. The content may have changed — use Read to re-read the file."
            )

    # Count occurrences using fuzzy-normalized content
    fuzzy_content = normalize_for_fuzzy_match(normalized_content)
    fuzzy_old_text = normalize_for_fuzzy_match(normalized_old_text)
    occurrences = fuzzy_content.count(fuzzy_old_text)

    if occurrences > 1:
        raise ValueError(
            f"Found {occurrences} occurrences of the text in {file_path}. "
            "The text must be unique. Please provide more context to make it unique."
        )

    # Perform replacement
    base_content = match_result["content_for_replacement"]
    new_content = (
        base_content[: match_result["index"]]
        + normalized_new_text
        + base_content[match_result["index"] + match_result["match_length"] :]
    )

    # Verify something changed
    if base_content == new_content:
        raise ValueError(
            f"No changes made to {file_path}. The replacement produced identical content."
        )

    # Restore line endings and BOM
    final_content = bom + restore_line_endings(new_content, original_ending)

    # Write back
    try:
        with open(resolved_path, "w", encoding="utf-8") as f:
            f.write(final_content)
    except PermissionError:
        raise PermissionError(f"Permission denied: {file_path}")
    except Exception as e:
        raise OSError(f"Failed to write file: {str(e)}")

    # Generate diff for output
    diff_result = generate_unified_diff(base_content, new_content)

    # Build output message
    output = f"Successfully replaced text in {file_path}."

    if match_result["used_fuzzy_match"]:
        output += "\n⚠ Used fuzzy matching (normalized whitespace/quotes/dashes)"

    if diff_result["diff"]:
        # Truncate diff if too long
        diff_lines = diff_result["diff"].split("\n")
        if len(diff_lines) > 20:
            diff_preview = "\n".join(diff_lines[:20])
            output += f"\n\nDiff preview (first 20 lines):\n{diff_preview}\n... ({len(diff_lines) - 20} more lines)"
        else:
            output += f"\n\nDiff:\n{diff_result['diff']}"

    return output
