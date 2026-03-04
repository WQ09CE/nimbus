"""
NimFS Agent Tools -- Artifact IPC

Three tools that expose NimFSManager artifact capabilities to agents:
  - NimFSWriteArtifact  : Write large pipeline products (IPC)
  - NimFSReadArtifact   : Read artifact by nimfs:// reference
  - NimFSListArtifacts  : List available artifacts

Long-term memory is handled by the unified Memo/Recall/ReadMemo tools
(see memo_tools.py). These tools follow the same dict-based registration
pattern as the core tools (Read/Write/Edit/Bash) and are registered in
tools/__init__.py.
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
    NimFSError,
)
from nimbus.tools.base import tool

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


@tool(
    name="NimFSWriteArtifact",
    description=(
        "Write a large pipeline product (code, report, diff, etc.) to NimFS shared disk and return "
        "a nimfs:// reference. Use this instead of returning huge strings in ToolResult — share the "
        "reference with other agents who can read it via NimFSReadArtifact. "
        "Solves the 16K context truncation problem for large outputs."
    ),
    category="nimfs",
)
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


@tool(
    name="NimFSReadArtifact",
    description=(
        "Read the content of a NimFS artifact by its nimfs://artifact/{id} reference. "
        "When reading large artifacts (e.g. from Auto-Offload), use offset/limit to paginate "
        "or grep_pattern to filter content to avoid context overflow."
    ),
    category="nimfs",
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


@tool(
    name="NimFSListArtifacts",
    description=(
        "List all available COMMITTED artifacts in NimFS for this project. "
        "Optionally filter by task_id. Shows references, sizes, and summaries."
    ),
    category="nimfs",
)
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


# All NimFS tools as a list (for bulk registration)
NIMFS_TOOLS: List[Dict[str, Any]] = [
    NIMFS_WRITE_ARTIFACT_TOOL,
    NIMFS_READ_ARTIFACT_TOOL,
    NIMFS_LIST_ARTIFACTS_TOOL,
]

NIMFS_TOOL_FUNCTIONS: Dict[str, Any] = {
    "NimFSWriteArtifact": nimfs_write_artifact,
    "NimFSReadArtifact":  nimfs_read_artifact,
    "NimFSListArtifacts": nimfs_list_artifacts,
}
