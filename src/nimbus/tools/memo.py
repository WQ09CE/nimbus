"""
Memo Tool - NimFS-backed Implementation (v2)

DEPRECATED: The original file-based Memo system has been replaced by NimFS.
- Global Memo → NimFS Memory (long-term, searchable, layered L0/L1/L2)
- Session Memo → NimFS Artifacts (task-scoped, auto-GC)

This module retains the `Memo` tool interface for backward compatibility,
but internally delegates all operations to NimFS via NimFSManager.

Migration: Run `scripts/migrate_memo_to_nimfs.py` to migrate existing .md files.
"""

import warnings
from pathlib import Path
from typing import Optional, Tuple, Callable, Dict, Any

from nimbus.core.nimfs.manager import NimFSManager

# Tool Definition for OpenAI format (kept for backward compatibility)
MEMO_TOOL_DEF = {
    "name": "Memo",
    "description": (
        "[DEPRECATED → Use NimFSWriteMemory / NimFSWriteArtifact instead]\n"
        "Read or update your memo. "
        "Two scopes: 'session' (→ NimFS Artifact) for current session notes, "
        "'global' (→ NimFS Memory) for cross-session project knowledge. "
        "Prefer using NimFS tools directly for richer capabilities."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "append", "clear"],
                "description": (
                    "'read' - view current memo; "
                    "'write' - overwrite entire memo; "
                    "'append' - add to end of memo; "
                    "'clear' - reset memo to empty"
                ),
            },
            "content": {
                "type": "string",
                "description": "The content to write (required for 'write' and 'append' actions).",
            },
            "scope": {
                "type": "string",
                "enum": ["session", "global"],
                "description": (
                    "'session' (default) - stored as NimFS Artifact (task lifecycle); "
                    "'global' - stored as NimFS Memory (permanent, searchable)"
                ),
                "default": "session",
            },
        },
        "required": ["action"],
    },
}


class NimFSMemoAdapter:
    """
    Adapter that provides MemoManager-compatible interface backed by NimFS.
    
    For 'global' scope: uses NimFS Memory (write_memory / search_memory / load_context)
    For 'session' scope: uses NimFS Artifacts (write_artifact / read_artifact)
    """

    def __init__(self, nimfs: NimFSManager, scope: str, session_id: str = "default"):
        self._nimfs = nimfs
        self._scope = scope  # "global" or "session"
        self._session_id = session_id
        # In-memory buffer for session memo (written to artifact on each update)
        self._session_buffer = ""
        self._session_artifact_ref: Optional[str] = None
        # Global memo: memory IDs we've written
        self._global_memory_ids: list = []

    def read(self) -> str:
        """Read memo content."""
        if self._scope == "global":
            return self._read_global()
        else:
            return self._read_session()

    def write(self, content: str) -> str:
        """Overwrite memo content."""
        if self._scope == "global":
            return self._write_global(content)
        else:
            return self._write_session(content)

    def append(self, content: str) -> str:
        """Append to memo content."""
        existing = self.read()
        new_content = f"{existing}\n{content}" if existing.strip() else content
        return self.write(new_content)

    def clear(self) -> str:
        """Clear memo content."""
        if self._scope == "session":
            self._session_buffer = ""
            self._session_artifact_ref = None
            return "Session memo cleared."
        else:
            # For global, we can't easily delete NimFS memory entries
            # Just acknowledge the clear
            return "Global memo cleared (NimFS memory entries are retained for history)."

    # --- Private: Global (NimFS Memory) ---

    def _read_global(self) -> str:
        """Read global knowledge from NimFS Memory via load_context."""
        try:
            # Use load_context to get a curated summary of all memory
            context = self._nimfs.load_context(
                current_goal="Read global project knowledge",
                max_chars=3000
            )
            return context if context else ""
        except Exception:
            return ""

    def _write_global(self, content: str) -> str:
        """Write global knowledge to NimFS Memory."""
        try:
            # Parse content into sections if it's structured markdown
            sections = self._parse_markdown_sections(content)
            
            if sections:
                written_ids = []
                for title, body in sections:
                    # 过滤无意义标题
                    if title.strip().lower() in ("general", "notes", "misc", "untitled", ""):
                        title = "Project Knowledge Summary"
                    memory_id = self._nimfs.write_memory(
                        content=body,
                        category="patterns",
                        title=title,
                        summary=body[:180] if len(body) > 180 else body,
                        tags="memo,global,project-knowledge",
                        scope="project",
                    )
                    written_ids.append(memory_id)
                    self._global_memory_ids.append(memory_id)
                return f"✅ Global memo written to NimFS Memory ({len(written_ids)} entries)"
            else:
                # Single block write
                memory_id = self._nimfs.write_memory(
                    content=content,
                    category="patterns",
                    title="Global Memo Entry",
                    summary=content[:180] if len(content) > 180 else content,
                    tags="memo,global,project-knowledge",
                    scope="project",
                )
                self._global_memory_ids.append(memory_id)
                return f"✅ Global memo written to NimFS Memory (ID: {memory_id})"
        except Exception as e:
            return f"❌ Failed to write global memo: {e}"

    # --- Private: Session (NimFS Artifacts) ---

    def _read_session(self) -> str:
        """Read session memo from in-memory buffer (or NimFS artifact)."""
        if self._session_buffer:
            return self._session_buffer
        if self._session_artifact_ref:
            try:
                return self._nimfs.read_artifact(self._session_artifact_ref)
            except Exception:
                return ""
        return ""

    def _write_session(self, content: str) -> str:
        """Write session memo as NimFS artifact."""
        self._session_buffer = content
        try:
            ref = self._nimfs.write_artifact(
                content=content,
                task_id=self._session_id,
                summary=f"Session memo for {self._session_id}",
                tags="memo,session",
                artifact_type="text",
                ttl="session",
            )
            self._session_artifact_ref = ref
            return f"✅ Session memo saved (ref: {ref})"
        except Exception as e:
            # Keep in-memory buffer even if artifact write fails
            return f"⚠️ Session memo saved in-memory (artifact write failed: {e})"

    # --- Utility ---

    @staticmethod
    def _parse_markdown_sections(content: str) -> list:
        """Parse markdown content into (title, body) sections."""
        sections = []
        current_title = None
        current_body = []

        for line in content.split("\n"):
            if line.startswith("## "):
                if current_title:
                    sections.append((current_title, "\n".join(current_body).strip()))
                current_title = line.lstrip("# ").strip()
                current_body = []
            elif line.startswith("# ") and not current_title:
                # Skip top-level header
                continue
            else:
                current_body.append(line)

        if current_title:
            sections.append((current_title, "\n".join(current_body).strip()))

        return sections


def create_memo_tool(
    workspace: Path,
    session_id: str = "default",
) -> Tuple[Dict[str, Any], Callable, NimFSMemoAdapter, NimFSMemoAdapter]:
    """
    Create a Memo tool backed by NimFS.

    Returns:
        (tool_definition, tool_function, session_adapter, global_adapter)

    The adapters provide .read()/.write()/.append()/.clear() for backward
    compatibility with MMU's assemble_context and compaction callback.
    """
    warnings.warn(
        "create_memo_tool() is deprecated. NimFS tools (NimFSWriteMemory, "
        "NimFSWriteArtifact, etc.) are now the primary memory interface.",
        DeprecationWarning,
        stacklevel=2,
    )

    nimfs = NimFSManager(workspace_path=workspace)

    session_adapter = NimFSMemoAdapter(nimfs, scope="session", session_id=session_id)
    global_adapter = NimFSMemoAdapter(nimfs, scope="global", session_id=session_id)

    def memo_func(
        action: str,
        content: str = "",
        scope: str = "session",
    ) -> str:
        adapter = global_adapter if scope == "global" else session_adapter

        if action == "read":
            result = adapter.read()
            return result if result else "(empty)"
        elif action == "write":
            if not content:
                return "Error: 'write' action requires 'content'."
            return adapter.write(content)
        elif action == "append":
            if not content:
                return "Error: 'append' action requires 'content'."
            return adapter.append(content)
        elif action == "clear":
            return adapter.clear()
        else:
            return f"Unknown action: {action}. Use read/write/append/clear."

    return MEMO_TOOL_DEF, memo_func, session_adapter, global_adapter


# =============================================================================
# Legacy MemoManager (DEPRECATED - kept for import compatibility)
# =============================================================================

class MemoManager:
    """
    DEPRECATED: Use NimFSMemoAdapter instead.
    
    This class is kept only for backward compatibility with code that
    imports MemoManager directly. New code should use NimFS tools.
    """

    def __init__(self, memo_path: Path):
        warnings.warn(
            "MemoManager is deprecated. Use NimFS Memory/Artifacts instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.memo_path = memo_path
        self.memo_path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> str:
        if self.memo_path.exists():
            return self.memo_path.read_text(encoding="utf-8")
        return ""

    def write(self, content: str) -> str:
        self.memo_path.write_text(content, encoding="utf-8")
        return f"Memo saved ({len(content)} chars)"

    def append(self, content: str) -> str:
        existing = self.read()
        new_content = f"{existing}\n{content}" if existing else content
        return self.write(new_content)

    def clear(self) -> str:
        self.memo_path.write_text("", encoding="utf-8")
        return "Memo cleared."
