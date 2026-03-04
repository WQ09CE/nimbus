"""
Unified Memory Tools -- Memo / Recall / ReadMemo

Consolidates 12 legacy memory tools into 3 simple, LLM-friendly tools.
Internally backed by NimFSManager.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope, MemoryNotFoundError
from nimbus.tools.base import tool


def _get_manager(**ctx: Any) -> NimFSManager:
    """Construct a NimFSManager from the tool execution context."""
    workspace = ctx.get("workspace") or ctx.get("cwd") or str(Path.cwd())
    return NimFSManager(str(workspace))


def _infer_category(tags: str) -> MemoryCategory:
    """Infer MemoryCategory from comma-separated tags."""
    lower = tags.lower()
    if "profile" in lower or "preference" in lower:
        return MemoryCategory.PROFILE
    if "strategy" in lower or "pattern" in lower or "architecture" in lower:
        return MemoryCategory.PATTERNS
    if "event" in lower or "milestone" in lower:
        return MemoryCategory.EVENTS
    if "case" in lower or "experience" in lower or "lesson" in lower:
        return MemoryCategory.CASES
    return MemoryCategory.ENTITIES


@tool(
    name="Memo",
    description=(
        "Save important knowledge for future sessions. Use this to remember "
        "facts, decisions, patterns, or user preferences that should persist "
        "across conversations."
    ),
    category="extension",
)
async def memo(
    title: str,
    content: str,
    tags: str = "",
    scope: str = "project",
    supersedes: str = "",
    **ctx: Any,
) -> str:
    """
    Save a memo to long-term memory.

    Args:
        title: Short descriptive title for the memo.
        content: Full content to remember (markdown supported).
        tags: Comma-separated tags for categorization (e.g. "profile,frontend,react").
        scope: Storage scope -- "project" (default) or "global" (cross-project).
        supersedes: Optional memo_id of an older entry this replaces.
    """
    if not title.strip() or not content.strip():
        return "Error: title and content must be non-empty."

    category = _infer_category(tags)

    try:
        scope_enum = MemoryScope(scope.lower())
    except ValueError:
        scope_enum = MemoryScope.PROJECT

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    manager = _get_manager(**ctx)

    memory_id = manager.write_memory(
        category=category,
        title=title,
        content=content,
        summary=content[:180] if len(content) > 180 else content,
        source=ctx.get("agent_role", "agent"),
        tags=tag_list,
        scope=scope_enum,
    )

    # Update supersedes link
    if supersedes.strip():
        try:
            old_dir = manager._find_memory_dir(supersedes.strip())
            if old_dir:
                meta_path = old_dir / "meta.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta["superseded_by"] = memory_id
                    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except Exception:
            pass

    return (
        f"Memo saved.\n"
        f"ID    : {memory_id}\n"
        f"Title : {title}\n"
        f"Scope : {scope_enum.value}\n"
        f"Tags  : {', '.join(tag_list) or 'none'}"
    )


@tool(
    name="Recall",
    description=(
        "Search your long-term memory for previously saved knowledge. "
        "Returns summaries of matching memos with their IDs."
    ),
    category="extension",
)
async def recall(
    query: str,
    top_k: int = 5,
    scope: str = "all",
    **ctx: Any,
) -> str:
    """
    Search long-term memory by keyword.

    Args:
        query: Search keywords (searches titles and tags).
        top_k: Maximum number of results to return (default 5).
        scope: Search scope -- "project", "global", or "all" (default).
    """
    manager = _get_manager(**ctx)
    results = manager.search_memory(query=query, top_k=top_k, scope=scope)

    if not results:
        return f"No memos found for '{query}'."

    lines = [f"Found {len(results)} memo(s) for '{query}':\n"]
    for entry in results:
        try:
            abstract = manager.read_memory(entry.memory_id, layer=0)
            preview = abstract[:150] + "..." if len(abstract) > 150 else abstract
        except Exception:
            preview = "(no preview)"

        lines.append(
            f"- **{entry.title}** (ID: `{entry.memory_id}`)\n"
            f"  Tags: {', '.join(entry.tags) or 'none'} | "
            f"Updated: {entry.updated_at}\n"
            f"  {preview}\n"
        )

    lines.append("Use ReadMemo(memo_id=...) to read full content.")
    return "\n".join(lines)


@tool(
    name="ReadMemo",
    description="Read a memo's full content by its ID (from Recall results).",
    category="extension",
)
async def read_memo(
    memo_id: str,
    detail: str = "full",
    **ctx: Any,
) -> str:
    """
    Read a memo's content by ID.

    Args:
        memo_id: The memo ID (from Recall results).
        detail: Detail level -- "summary" or "full" (default).
    """
    manager = _get_manager(**ctx)
    layer = 1 if detail == "summary" else 2

    try:
        entry = manager.get_memory_entry(memo_id)
        content = manager.read_memory(memo_id, layer=layer)
        return (
            f"## {entry.title}\n"
            f"Category: {entry.category.value} | Tags: {', '.join(entry.tags) or 'none'}\n\n"
            f"{content}"
        )
    except MemoryNotFoundError:
        return f"Memo '{memo_id}' not found. Use Recall to search for available memos."
    except Exception as e:
        return f"Error reading memo '{memo_id}': {e}"
