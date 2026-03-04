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
        "Save knowledge for later use. "
        "scope='session': temporary scratchpad for current conversation (lost when session ends). "
        "scope='project': persistent memory across sessions (default). "
        "scope='global': persistent memory across all projects."
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
        scope: Storage scope -- "session" (temporary), "project" (default), or "global" (cross-project).
        supersedes: Optional memo_id of an older entry this replaces.
    """
    if not title.strip() or not content.strip():
        return "Error: title and content must be non-empty."

    # Session-scoped: write to MMU clipboard (in-memory, visible in context)
    if scope.lower() == "session":
        mmu = ctx.get("mmu")
        if not mmu:
            return "Error: session memo unavailable (no process context)."
        existing = getattr(mmu, "_clipboard", "") or ""
        entry = f"### {title}\n{content}\n"
        mmu.update_clipboard((existing + "\n" + entry).strip() if existing else entry)
        return (
            f"Session memo saved: {title}\n"
            f"(Visible in current conversation only, lost when session ends)"
        )

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
        "Search your memory for previously saved knowledge. "
        "Searches both session memos (temporary) and persistent memos. "
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
        scope: Search scope -- "session", "project", "global", or "all" (default).
    """
    lines = []
    found_count = 0

    # Search session memos (clipboard)
    if scope in ("session", "all"):
        mmu = ctx.get("mmu")
        if mmu:
            clipboard = getattr(mmu, "_clipboard", "") or ""
            if clipboard and query.lower() in clipboard.lower():
                lines.append("**Session Memos (temporary):**")
                lines.append(clipboard[:500] + ("..." if len(clipboard) > 500 else ""))
                lines.append("")
                found_count += 1

    # Search persistent memos (NimFS)
    if scope != "session":
        manager = _get_manager(**ctx)
        results = manager.search_memory(query=query, top_k=top_k, scope=scope)
        if results:
            found_count += len(results)
            lines.append(f"**Persistent Memos ({len(results)} found):**")
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

    if not found_count:
        return f"No memos found for '{query}'."
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
