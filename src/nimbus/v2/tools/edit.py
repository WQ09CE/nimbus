"""V2 Edit tool for precise string replacement editing.

This module provides the Edit tool in v2 format for AgentOS.
Reuses the sophisticated matching logic from v1.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from nimbus.tools.sandbox import Sandbox, SandboxError
from nimbus.tools.edit import (
    _normalize_edit_string,
    _find_match_with_fallback,
    _apply_replacement,
    _read_file_with_encoding,
)


async def edit_file(
    file_path: str,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    edits: Optional[List[dict]] = None,
    replace_all: bool = False,
    workspace: Path | None = None,
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
    """
    from nimbus.core.logging import get_logger
    logger = get_logger("tools.edit")

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


# V2 Tool Definition
EDIT_TOOL: Dict[str, Any] = {
    "name": "Edit",
    "description": """Edit a file using search-and-replace blocks. Supports multiple edits in one call.

IMPORTANT RULES:
1. The 'old_string' (search block) must contain enough context (3-5 lines) to be globally unique
2. DO NOT use '...' to abbreviate code - output complete blocks
3. If multiple identical blocks exist, the edit will fail - ensure uniqueness
4. Indentation matters for exact match, but fuzzy matching may recover minor errors
5. NEVER include line number prefixes from Read output - only use actual content!
""",
    "function": edit_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": "[Legacy] The exact text to search for and replace. Must contain 3-5 lines of context for uniqueness.",
            },
            "new_string": {
                "type": "string",
                "description": "[Legacy] The text to replace old_string with",
            },
            "edits": {
                "type": "array",
                "description": "[Recommended] Array of {search, replace} objects for multiple edits.",
                "items": {
                    "type": "object",
                    "properties": {
                        "search": {"type": "string", "description": "Text to search for"},
                        "replace": {"type": "string", "description": "Text to replace with"},
                    },
                },
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences instead of requiring unique match. Defaults to False.",
                "default": False,
            },
        },
        "required": ["file_path"],
    },
}
