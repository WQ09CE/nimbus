"""Edit Tool — Precise text replacement with fuzzy matching fallback."""

import difflib
from pathlib import Path
from typing import Any, Optional

from .registry import ToolParameter, tool


def _fuzzy_find(content: str, old_text: str) -> Optional[int]:
    """Try fuzzy matching by normalizing whitespace."""
    import re
    normalize = lambda s: re.sub(r"\s+", " ", s)
    norm_content = normalize(content)
    norm_old = normalize(old_text)
    idx = norm_content.find(norm_old)
    if idx == -1:
        return None
    # Map back to original position (approximate)
    char_count = 0
    norm_pos = 0
    for i, ch in enumerate(content):
        if norm_pos >= idx:
            return i
        if ch in " \t\n\r":
            if norm_pos < len(norm_content) and norm_content[norm_pos] == " ":
                norm_pos += 1
        else:
            norm_pos += 1
    return None


@tool(
    name="Edit",
    description="Edit a file by replacing exact text. old_text must match precisely (including whitespace).",
    parameters=[
        ToolParameter("file_path", "string", "Path to the file", required=True),
        ToolParameter("old_text", "string", "Text to find and replace", required=True),
        ToolParameter("new_text", "string", "Replacement text", required=True),
    ],
)
async def edit_file(file_path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
    path = Path(file_path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    content = path.read_text(encoding="utf-8")
    used_fuzzy = False

    # Exact match
    count = content.count(old_text)
    if count == 1:
        new_content = content.replace(old_text, new_text, 1)
    elif count > 1:
        raise ValueError(f"Found {count} occurrences in {file_path}. Provide more context to make it unique.")
    else:
        # Fuzzy fallback
        idx = _fuzzy_find(content, old_text)
        if idx is None:
            # Show closest region for debugging
            lines = content.split("\n")
            first_line = old_text.split("\n")[0].strip()
            best_i, best_r = 0, 0.0
            for i, line in enumerate(lines):
                r = difflib.SequenceMatcher(None, first_line, line.strip()).ratio()
                if r > best_r:
                    best_r, best_i = r, i
            start = max(0, best_i - 2)
            context = "\n".join(f"{j+1:4d} | {lines[j]}" for j in range(start, min(start + 10, len(lines))))
            raise ValueError(
                f"Text not found in {file_path}.\n\nMost similar region:\n{context}\n\nCopy text exactly from above."
            )
        # Find the end of the fuzzy match
        import re
        norm = lambda s: re.sub(r"\s+", " ", s)
        norm_old_len = len(norm(old_text))
        # Walk forward to find end position
        norm_count = 0
        end = idx
        for end in range(idx, len(content)):
            ch = content[end]
            if ch in " \t\n\r":
                if norm_count < norm_old_len and norm(content[idx:end+1]).rstrip() == norm(old_text)[:norm_count+1].rstrip():
                    pass
            norm_count = len(norm(content[idx:end+1]))
            if norm_count >= norm_old_len:
                end += 1
                break
        new_content = content[:idx] + new_text + content[end:]
        used_fuzzy = True

    if content == new_content:
        raise ValueError("No changes — old_text and new_text produce identical content.")

    path.write_text(new_content, encoding="utf-8")

    # Generate compact diff
    old_lines = content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = "".join(difflib.unified_diff(old_lines, new_lines, n=2))
    diff_lines = diff.split("\n")
    if len(diff_lines) > 20:
        diff = "\n".join(diff_lines[:20]) + f"\n... ({len(diff_lines) - 20} more lines)"

    msg = f"Successfully edited {file_path}."
    if used_fuzzy:
        msg += " (fuzzy match)"
    if diff:
        msg += f"\n\n{diff}"
    return msg
