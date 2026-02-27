"""
NimFS Agent Tools

Seven tools that expose NimFSManager capabilities to agents:
  - NimFSWriteArtifact  : Write large pipeline products (IPC)
  - NimFSReadArtifact   : Read artifact by nimfs:// reference
  - NimFSListArtifacts  : List available artifacts
  - NimFSWriteMemory    : Write long-term memory entry
  - NimFSSearchMemory   : Keyword search over memory (with wildcard support)
  - NimFSListMemory     : List/browse all memory entries (no query needed)
  - NimFSLoadContext    : Load Anchor context injection package

These tools follow the same dict-based registration pattern as the core tools
(Read/Write/Edit/Bash) and are registered in tools/__init__.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import (
    ArtifactExpiredError,
    ArtifactNotFoundError,
    ArtifactPendingError,
    ArtifactTTL,
    MemoryCategory,
    MemoryScope,
    NimFSError,
)

# =============================================================================
# Context Helper
# =============================================================================


def _get_manager(**ctx: Any) -> NimFSManager:
    """Construct a NimFSManager from the tool execution context."""
    workspace = ctx.get("workspace") or ctx.get("cwd") or str(Path.cwd())
    return NimFSManager(str(workspace))


def _agent_role(**ctx: Any) -> str:
    return ctx.get("agent_role") or ctx.get("role") or "agent"


# =============================================================================
# Tool Functions
# =============================================================================


async def nimfs_write_artifact(
    content: str,
    task_id: str,
    summary: str = "",
    ttl: str = "session",
    type: str = "text",
    tags: str = "",
    **ctx: Any,
) -> str:
    """
    Write a large pipeline product to NimFS and return a nimfs:// reference.

    This implements the Claim-Check pattern: instead of passing a huge string
    through ToolResult (16K limit), write it here and share the reference.
    Any agent can then call NimFSReadArtifact to retrieve the full content.

    Args:
        content: Full content to store (no size limit).
        task_id: Owning task identifier (e.g. "task-implement-nimfs").
        summary: Short description of the artifact (< 200 chars).
        ttl:     Lifecycle: "task" | "session" | "project" | "permanent".
        type:    Content type: "code" | "report" | "diff" | "json" | "text".
        tags:    Comma-separated tag list (e.g. "python,implementation").

    Returns:
        Success message with nimfs:// reference string.
    """
    try:
        ttl_enum = ArtifactTTL(ttl)
    except ValueError:
        ttl_enum = ArtifactTTL.SESSION

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    manager = _get_manager(**ctx)
    ref = manager.write_artifact(
        content=content,
        task_id=task_id,
        producer=_agent_role(**ctx),
        artifact_type=type,
        ttl=ttl_enum,
        summary=summary,
        tags=tag_list,
    )

    manifest = manager.get_artifact_manifest(ref)
    return (
        f"✅ Artifact written to NimFS\n"
        f"Reference : {ref}\n"
        f"Size      : {manifest.size_bytes:,} bytes\n"
        f"TTL       : {manifest.ttl.value}\n"
        f"Summary   : {manifest.summary}\n\n"
        f"Share this reference with other agents to retrieve the full content."
    )


async def nimfs_read_artifact(
    ref: str, offset: int = 1, limit: int = 2000, grep_pattern: Optional[str] = None, **ctx: Any
) -> str:
    """
    Read the content of a NimFS artifact by its nimfs:// reference.

    When reading large artifacts (e.g. from Auto-Offload), use offset/limit to paginate
    or grep_pattern to filter content to avoid context overflow.

    Args:
        ref: "nimfs://artifact/{id}" reference returned by NimFSWriteArtifact.
        offset: Starting line number (1-indexed). Default is 1.
        limit: Maximum number of lines to read. Default is 2000.
        grep_pattern: Optional substring to filter lines.

    Returns:
        Artifact content (possibly filtered or paginated).
    """
    manager = _get_manager(**ctx)
    try:
        content = manager.read_artifact(ref)
        manifest = manager.get_artifact_manifest(ref)

        lines = content.splitlines()
        total_lines = len(lines)

        if grep_pattern:
            # Filter lines and keep track of original line numbers (1-indexed)
            filtered_lines = [
                f"{i+1}: {line}"
                for i, line in enumerate(lines)
                if grep_pattern.lower() in line.lower()
            ]
            result_content = "\n".join(filtered_lines)
            if not result_content:
                result_content = f"No lines matching pattern '{grep_pattern}' found."
        else:
            # Paginate
            start = max(0, offset - 1)
            end = start + limit
            chunk = lines[start:end]
            result_content = "\n".join(chunk)

            if end < total_lines:
                result_content += f"\n\n[System: Artifact has more lines. Use offset={end + 1} to read next chunk.]"

        header = (
            f"<!-- NimFS Artifact: {ref} | "
            f"type={manifest.type} | size={manifest.size_bytes:,}B | "
            f"lines={total_lines} | producer={manifest.producer} -->\n\n"
        )
        return header + result_content
    except ArtifactExpiredError as e:
        return f"❌ ArtifactExpiredError: {e}\nThe artifact has been GC'd. Check if a newer version exists."
    except ArtifactNotFoundError as e:
        return f"❌ ArtifactNotFoundError: {e}\nVerify the reference is correct."
    except ArtifactPendingError as e:
        return f"⚠️ ArtifactPendingError: {e}\nThe artifact is still being written. Retry shortly."
    except NimFSError as e:
        return f"❌ NimFSError: {e}"


async def nimfs_list_artifacts(task_id: str = "", **ctx: Any) -> str:
    """
    List available COMMITTED artifacts in NimFS, optionally filtered by task_id.

    Args:
        task_id: Optional task filter. Leave empty to list all artifacts.

    Returns:
        Formatted table of artifacts with their references and summaries.
    """
    manager = _get_manager(**ctx)
    artifacts = manager.list_artifacts(task_id=task_id or None)

    if not artifacts:
        filter_msg = f" for task '{task_id}'" if task_id else ""
        return f"No committed artifacts found{filter_msg} in NimFS."

    lines = [f"## NimFS Artifacts ({len(artifacts)} found)\n"]
    for m in artifacts:
        lines.append(
            f"### {m.artifact_id}\n"
            f"- **Reference**: nimfs://artifact/{m.artifact_id}\n"
            f"- **Task**     : {m.task_id}\n"
            f"- **Type**     : {m.type} ({m.size_bytes:,} bytes)\n"
            f"- **Producer** : {m.producer}\n"
            f"- **TTL**      : {m.ttl.value}\n"
            f"- **Summary**  : {m.summary}\n"
            f"- **Created**  : {m.created_at}\n"
        )

    return "\n".join(lines)


async def nimfs_write_memory(
    category: str,
    title: str,
    content: str,
    summary: str = "",
    confidence: str = "1.0",
    tags: str = "",
    scope: str = "project",
    **ctx: Any,
) -> str:
    """
    Write a long-term memory entry to NimFS with automatic L0/L1/L2 layering.

    Use this to persist knowledge that should survive across sessions:
    - profile     : Agent identity and role definition
    - preferences : User preferences, style guides, constraints
    - entities    : Key objects, components, file associations
    - events      : State changes, milestones, decisions
    - cases       : Success/failure experience cases
    - patterns    : Architecture patterns, technical specifications

    Args:
        category:   Memory category (one of the 6 above).
        title:      Short descriptive title (used for search).
        content:    Full content to persist.
        summary:    Compact abstract for the Anchor (< 200 chars).
        confidence: Reliability score 0.0–1.0 (default "1.0").
        tags:       Comma-separated tags for filtering.
        scope:      "project" (default) or "global" (cross-project).

    Returns:
        Confirmation with memory_id.
    """
    # Warn if writing to profile/preferences — should use NimFSUpdateProfile instead
    if category in ("profile", "preferences"):
        # Still allow the write (with dedup in manager), but log a warning
        from nimbus.core.logging import get_logger
        get_logger("nimfs.tools").warning(
            "NimFSWriteMemory called with category='%s' — consider using NimFSUpdateProfile for idempotent writes",
            category,
        )

    try:
        cat_enum = MemoryCategory(category.lower())
    except ValueError:
        valid = [c.value for c in MemoryCategory]
        return f"❌ Invalid category '{category}'. Must be one of: {valid}"

    try:
        scope_enum = MemoryScope(scope.lower())
    except ValueError:
        scope_enum = MemoryScope.PROJECT

    try:
        conf = float(confidence)
        conf = max(0.0, min(1.0, conf))
    except ValueError:
        conf = 1.0

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    manager = _get_manager(**ctx)
    memory_id = manager.write_memory(
        category=cat_enum,
        title=title,
        content=content,
        summary=summary,
        confidence=conf,
        source=_agent_role(**ctx),
        tags=tag_list,
        scope=scope_enum,
    )

    return (
        f"✅ Memory written to NimFS\n"
        f"Memory ID : {memory_id}\n"
        f"Category  : {cat_enum.value}\n"
        f"Scope     : {scope_enum.value}\n"
        f"Title     : {title}\n"
        f"Confidence: {conf:.2f}\n\n"
        f"Retrieve with: NimFSReadMemory (layer 0/1/2) or NimFSSearchMemory"
    )


async def nimfs_search_memory(
    query: str,
    category: str = "",
    top_k: str = "5",
    scope: str = "all",
    **ctx: Any,
) -> str:
    """
    Search long-term memory entries in NimFS by keyword.

    Searches against entry titles and tags (case-insensitive substring match).
    Phase 0 implementation; Phase 1 will add vector similarity search.

    Args:
        query:    Search query string.
        category: Optional category filter (leave empty for all categories).
        top_k:    Maximum number of results to return (default "5").
        scope:    "project" | "global" | "all" (default "all").

    Returns:
        Formatted list of matching memory entries with L0 summaries.
    """
    cat_enum: Optional[MemoryCategory] = None
    if category.strip():
        try:
            cat_enum = MemoryCategory(category.lower())
        except ValueError:
            valid = [c.value for c in MemoryCategory]
            return f"❌ Invalid category '{category}'. Must be one of: {valid}"

    try:
        k = int(top_k)
    except ValueError:
        k = 5

    manager = _get_manager(**ctx)
    results = manager.search_memory(query=query, category=cat_enum, top_k=k, scope=scope)

    if not results:
        return f"No memory entries found for query: '{query}'"

    lines = [f"## NimFS Memory Search: '{query}' ({len(results)} results)\n"]
    for entry in results:
        # Try to load L0 abstract for preview
        try:
            l0 = manager.read_memory(entry.memory_id, layer=0)
            preview = l0[:150] + "..." if len(l0) > 150 else l0
        except Exception:
            preview = "(no preview available)"

        lines.append(
            f"### {entry.title}\n"
            f"- **Memory ID** : {entry.memory_id}\n"
            f"- **Category**  : {entry.category.value}\n"
            f"- **Confidence**: {entry.confidence:.2f}\n"
            f"- **Tags**      : {', '.join(entry.tags) or 'none'}\n"
            f"- **Updated**   : {entry.updated_at}\n"
            f"- **Abstract**  : {preview}\n"
        )

    return "\n".join(lines)


async def nimfs_list_memory(
    category: str = "",
    scope: str = "all",
    top_k: str = "50",
    **ctx: Any,
) -> str:
    """
    List all memory entries in NimFS with optional category/scope filtering.

    No search query needed — returns all entries. Use this to browse all
    stored knowledge without needing specific keywords.

    Args:
        category: Optional category filter (leave empty for all categories).
        scope:    "project" | "global" | "all" (default "all").
        top_k:    Maximum number of entries to return (default "50").

    Returns:
        Formatted list of all memory entries with L0 summaries.
    """
    cat_enum: Optional[MemoryCategory] = None
    if category.strip():
        try:
            cat_enum = MemoryCategory(category.lower())
        except ValueError:
            valid = [c.value for c in MemoryCategory]
            return f"❌ Invalid category '{category}'. Must be one of: {valid}"

    try:
        k = int(top_k)
    except ValueError:
        k = 50

    manager = _get_manager(**ctx)
    results = manager.list_memory(category=cat_enum, scope=scope, top_k=k)

    if not results:
        filter_msg = f" in category '{category}'" if category.strip() else ""
        return f"No memory entries found{filter_msg}."

    lines = [f"## NimFS Memory: {len(results)} entries\n"]
    for entry in results:
        # Try to load L0 abstract for preview
        try:
            l0 = manager.read_memory(entry.memory_id, layer=0)
            preview = l0[:150] + "..." if len(l0) > 150 else l0
        except Exception:
            preview = "(no preview available)"

        lines.append(
            f"### {entry.title}\n"
            f"- **Memory ID** : {entry.memory_id}\n"
            f"- **Category**  : {entry.category.value}\n"
            f"- **Scope**     : {entry.scope.value}\n"
            f"- **Confidence**: {entry.confidence:.2f}\n"
            f"- **Tags**      : {', '.join(entry.tags) or 'none'}\n"
            f"- **Updated**   : {entry.updated_at}\n"
            f"- **Abstract**  : {preview}\n"
        )

    return "\n".join(lines)


async def nimfs_load_context(goal: str, max_chars: str = "3000", **ctx: Any) -> str:
    """
    Load an optimized context injection package from NimFS for the Anchor.

    Assembles L0 summaries from:
      1. Global profile and preferences (always included)
      2. Project memory entries relevant to the current goal

    Use this at the start of a task to pre-load relevant knowledge into context.

    Args:
        goal:      Current task/goal description (used for relevance matching).
        max_chars: Character budget for the output (default "3000").

    Returns:
        Formatted markdown context block ready for Anchor injection.
    """
    try:
        budget = int(max_chars)
    except ValueError:
        budget = 3000

    manager = _get_manager(**ctx)
    context_block = manager.load_context(current_goal=goal, max_chars=budget)

    if not context_block:
        return "No relevant NimFS context found for this goal. NimFS may be empty."

    return context_block


# =============================================================================
# Tool Definitions (dict format, consistent with tools/__init__.py)
# =============================================================================

NIMFS_WRITE_ARTIFACT_TOOL: Dict[str, Any] = {
    "name": "NimFSWriteArtifact",
    "description": (
        "Write a large pipeline product (code, report, diff, etc.) to NimFS shared disk "
        "and return a nimfs:// reference. Use this instead of returning huge strings in "
        "ToolResult — share the reference with other agents who can read it via "
        "NimFSReadArtifact. Solves the 16K context truncation problem for large outputs."
    ),
    "function": nimfs_write_artifact,
    "parameters": {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Full content to store (no size limit)"},
            "task_id": {"type": "string", "description": "Owning task identifier"},
            "summary": {"type": "string", "description": "Short description of the artifact (< 200 chars)"},
            "ttl":     {"type": "string", "description": "Lifecycle: task | session | project | permanent"},
            "type":    {"type": "string", "description": "Content type: code | report | diff | json | text"},
            "tags":    {"type": "string", "description": "Comma-separated tags (e.g. 'python,implementation')"},
        },
        "required": ["content", "task_id"],
    },
}

NIMFS_READ_ARTIFACT_TOOL: Dict[str, Any] = {
    "name": "NimFSReadArtifact",
    "description": (
        "Read the content of a NimFS artifact by its nimfs://artifact/{id} reference. "
        "When reading large artifacts (e.g. from Auto-Offload), use offset/limit to paginate "
        "or grep_pattern to filter content to avoid context overflow."
    ),
    "function": nimfs_read_artifact,
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "nimfs://artifact/{id} reference string"},
            "offset": {
                "type": "integer",
                "description": "Starting line number (1-indexed). Default is 1.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read. Default is 2000.",
            },
            "grep_pattern": {
                "type": "string",
                "description": "Optional substring to filter lines.",
            },
        },
        "required": ["ref"],
    },
}

NIMFS_LIST_ARTIFACTS_TOOL: Dict[str, Any] = {
    "name": "NimFSListArtifacts",
    "description": (
        "List all available COMMITTED artifacts in NimFS for this project. "
        "Optionally filter by task_id. Shows references, sizes, and summaries."
    ),
    "function": nimfs_list_artifacts,
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Optional task filter. Leave empty for all artifacts."},
        },
        "required": [],
    },
}


async def nimfs_update_profile(
    target: str,
    content: str,
    summary: str = "",
    **ctx: Any,
) -> str:
    """
    Update the global profile or preferences in NimFS.
    Overwrites any existing profile/preferences memory.

    Args:
        target:   Either "profile" or "preferences".
        content:  Full new content to persist.
        summary:  Compact abstract for the Anchor (< 200 chars).
    """
    if target not in ["profile", "preferences"]:
        return "Error: target must be 'profile' or 'preferences'."
        
    manager = ctx.get("nimfs_manager")
    if not manager:
        return "Error: NimFSManager not injected into context."
        
    try:
        from nimbus.core.nimfs.manager import MemoryCategory, MemoryScope
        import shutil
        
        # Clear existing profile/preferences
        target_dir = manager.global_root / target
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Write new memory
        category = MemoryCategory(target)
        memory_id = manager.write_memory(
            category=category,
            title=f"Global {target.capitalize()}",
            content=content,
            summary=summary or content[:150],
            scope=MemoryScope.GLOBAL,
        )
        return f"Successfully updated global {target}. New memory ID: {memory_id}"
    except Exception as e:
        return f"Error updating {target}: {str(e)}"

NIMFS_UPDATE_PROFILE_TOOL: Dict[str, Any] = {
    "name": "NimFSUpdateProfile",
    "description": (
        "Update the global user profile or preferences memory in NimFS. "
        "Overwrites existing entries to avoid duplication. "
        "Use this to permanently change how the agent behaves across all sessions."
    ),
    "function": nimfs_update_profile,
    "parameters": {
        "type": "object",
        "properties": {
            "target":  {"type": "string", "description": "Must be 'profile' or 'preferences'"},
            "content": {"type": "string", "description": "Full new content to persist"},
            "summary": {"type": "string", "description": "Compact abstract (< 200 chars) used in Anchor injection"},
        },
        "required": ["target", "content"],
    },
}

NIMFS_WRITE_MEMORY_TOOL: Dict[str, Any] = {
    "name": "NimFSWriteMemory",
    "description": (
        "Write a long-term memory entry to NimFS. Automatically creates L0 (abstract), "
        "L1 (overview), and L2 (full content) layers. "
        "Categories: profile | preferences | entities | events | cases | patterns."
    ),
    "function": nimfs_write_memory,
    "parameters": {
        "type": "object",
        "properties": {
            "category":   {"type": "string", "description": "profile | preferences | entities | events | cases | patterns"},
            "title":      {"type": "string", "description": "Short descriptive title (used for search)"},
            "content":    {"type": "string", "description": "Full content to persist"},
            "summary":    {"type": "string", "description": "Compact abstract (< 200 chars) used in Anchor injection"},
            "confidence": {"type": "string", "description": "Reliability score 0.0–1.0 (default '1.0')"},
            "tags":       {"type": "string", "description": "Comma-separated tags for filtering"},
            "scope":      {"type": "string", "description": "project (default) | global (cross-project)"},
        },
        "required": ["category", "title", "content"],
    },
}

NIMFS_SEARCH_MEMORY_TOOL: Dict[str, Any] = {
    "name": "NimFSSearchMemory",
    "description": (
        "Search long-term memory entries in NimFS by keyword. "
        "Searches titles and tags (case-insensitive). Returns L0 summaries for matching entries."
    ),
    "function": nimfs_search_memory,
    "parameters": {
        "type": "object",
        "properties": {
            "query":    {"type": "string", "description": "Search query string"},
            "category": {"type": "string", "description": "Optional category filter"},
            "top_k":    {"type": "string", "description": "Max results to return (default '5')"},
            "scope":    {"type": "string", "description": "project | global | all (default 'all')"},
        },
        "required": ["query"],
    },
}

NIMFS_LIST_MEMORY_TOOL: Dict[str, Any] = {
    "name": "NimFSListMemory",
    "description": (
        "List all memory entries in NimFS. No search query needed — shows all entries "
        "with optional category/scope filtering. Use this to browse all stored knowledge."
    ),
    "function": nimfs_list_memory,
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "description": "Optional category filter (e.g. 'patterns', 'entities', 'events', 'cases')"},
            "scope":    {"type": "string", "description": "project | global | all (default 'all')"},
            "top_k":    {"type": "string", "description": "Max entries to return (default '50')"},
        },
        "required": [],
    },
}

NIMFS_LOAD_CONTEXT_TOOL: Dict[str, Any] = {
    "name": "NimFSLoadContext",
    "description": (
        "Load an optimized context package from NimFS for the current goal. "
        "Combines global profile/preferences with relevant project knowledge (L0 summaries). "
        "Use at task start to pre-load relevant knowledge into the Anchor."
    ),
    "function": nimfs_load_context,
    "parameters": {
        "type": "object",
        "properties": {
            "goal":      {"type": "string", "description": "Current task/goal description"},
            "max_chars": {"type": "string", "description": "Character budget for output (default '3000')"},
        },
        "required": ["goal"],
    },
}

# All NimFS tools as a list (for bulk registration)
NIMFS_TOOLS: List[Dict[str, Any]] = [
    NIMFS_WRITE_ARTIFACT_TOOL,
    NIMFS_READ_ARTIFACT_TOOL,
    NIMFS_LIST_ARTIFACTS_TOOL,
    NIMFS_WRITE_MEMORY_TOOL,
    NIMFS_UPDATE_PROFILE_TOOL,
    NIMFS_SEARCH_MEMORY_TOOL,
    NIMFS_LIST_MEMORY_TOOL,
    NIMFS_LOAD_CONTEXT_TOOL,
]

NIMFS_TOOL_FUNCTIONS: Dict[str, Any] = {
    "NimFSWriteArtifact": nimfs_write_artifact,
    "NimFSReadArtifact":  nimfs_read_artifact,
    "NimFSListArtifacts": nimfs_list_artifacts,
    "NimFSWriteMemory":   nimfs_write_memory,
    "NimFSUpdateProfile": nimfs_update_profile,
    "NimFSSearchMemory":  nimfs_search_memory,
    "NimFSListMemory":    nimfs_list_memory,
    "NimFSLoadContext":   nimfs_load_context,
}
