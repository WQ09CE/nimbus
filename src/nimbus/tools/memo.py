"""
Memo Tool - "好记性不如烂笔头"

A simple, explicit memory management tool that lets the AI maintain
its own persistent notes in a session-specific memo.md file.

Philosophy:
- Instead of complex sliding windows and retrieval systems,
  let the AI decide what's worth remembering.
- The memo is prepended to context, always visible.
- If it's not in the memo, it will be forgotten.
"""

from pathlib import Path
from typing import Optional

# Tool Definition for OpenAI format
MEMO_TOOL_DEF = {
    "name": "Memo",
    "description": (
        "Read or update your memo files. "
        "Two scopes: 'session' (default) for current session notes, "
        "'global' for cross-session project knowledge that persists forever. "
        "Anything not written here WILL BE FORGOTTEN. "
        "The memo content is always visible at the top of your context."
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
                )
            },
            "content": {
                "type": "string",
                "description": "Content to write/append (required for write/append actions)"
            },
            "scope": {
                "type": "string",
                "enum": ["session", "global"],
                "description": (
                    "'session' (default) - current session memo, lost after session ends; "
                    "'global' - persistent memo across all sessions, for project-level knowledge"
                )
            }
        },
        "required": ["action"]
    }
}


class MemoManager:
    """
    Manages a session-specific memo file in the workspace.
    """

    def __init__(self, workspace: Path, session_id: str = "default"):
        self.workspace = Path(workspace)
        self.session_id = session_id
        self.memo_dir = self.workspace / ".nimbus"
        self.memo_file = self.memo_dir / f"memo_{session_id}.md"

        # Ensure directory exists
        self.memo_dir.mkdir(parents=True, exist_ok=True)

        # Initialize memo if not exists
        if not self.memo_file.exists():
            self._init_memo()

    def _init_memo(self):
        """Initialize memo with a template."""
        if self.memo_file.exists():
            return
        if self.session_id == "global":
            template = "# Project Knowledge\n\n<!-- Cross-session persistent memory -->\n"
        else:
            template = """# 📝 Session Memo

> 这是你的"烂笔头"。任何重要的事情，如果不写在这里，下一轮对话就会消失。

## 🎯 Current Task


## 📁 Key Files


## 💡 Decisions & Notes


## ⏭️ Next Steps

"""
        self.memo_file.write_text(template, encoding="utf-8")

    def read(self) -> str:
        """Read current memo content."""
        if not self.memo_file.exists():
            self._init_memo()
        return self.memo_file.read_text(encoding="utf-8")

    def write(self, content: str) -> str:
        """Overwrite memo with new content."""
        self.memo_file.write_text(content, encoding="utf-8")
        return f"Memo updated ({len(content)} chars)"

    def append(self, content: str) -> str:
        """Append content to memo."""
        current = self.read()
        new_content = current.rstrip() + "\n\n" + content
        self.memo_file.write_text(new_content, encoding="utf-8")
        return f"Appended to memo ({len(content)} chars added)"

    def clear(self) -> str:
        """Reset memo to initial template."""
        self._init_memo()
        return "Memo cleared and reset to template"

    def execute(self, action: str, content: Optional[str] = None) -> str:
        """Execute memo action."""
        if action == "read":
            return self.read()
        elif action == "write":
            if not content:
                return "[Error] 'write' action requires content"
            return self.write(content)
        elif action == "append":
            if not content:
                return "[Error] 'append' action requires content"
            return self.append(content)
        elif action == "clear":
            return self.clear()
        else:
            return f"[Error] Unknown action: {action}"


def create_memo_tool(workspace: Path, session_id: str = "default"):
    """
    Factory function to create a memo tool instance bound to a specific session.

    Returns:
        Tuple of (tool_definition, tool_function, session_manager, global_manager)
    """
    session_manager = MemoManager(workspace, session_id)
    global_manager = MemoManager(workspace, "global")

    async def memo_tool(action: str, content: str = None, scope: str = "session") -> str:
        mgr = global_manager if scope == "global" else session_manager
        return mgr.execute(action, content)

    return MEMO_TOOL_DEF, memo_tool, session_manager, global_manager
