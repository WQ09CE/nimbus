"""
State Manager - Deterministic Project State Tracking

This module implements a state machine that tracks the "objective reality" 
of the project execution, independent of LLM summarization.

It tracks:
1. File Working Set: Which files are modified, created, or read.
2. Execution Status: The outcome of the last critical command (pass/fail).
"""

import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class FileState:
    """State of a single file in the working set."""
    path: str
    status: str = "read"  # 'read', 'modified', 'created'
    access_count: int = 1
    last_accessed: float = field(default_factory=time.time)

    def mark_modified(self):
        self.status = "modified"
        self.access_count += 1
        self.last_accessed = time.time()

    def mark_read(self):
        self.access_count += 1
        self.last_accessed = time.time()


class StateManager:
    """
    Manages the deterministic state of the project.
    """
    def __init__(self):
        self.files: Dict[str, FileState] = {}
        self.last_cmd_status: Optional[str] = None
        self.last_cmd_time: float = 0

        # Max files to display in prompt (LRU)
        self.max_display_files = 10

    def update(self, tool_name: str, args: dict, result_content: str) -> None:
        """
        Update state based on tool execution.
        This is a deterministic hook called after every tool result.
        """
        # 1. File Tracking
        file_path = args.get("file_path") or args.get("path")

        if tool_name == "Write":
            if file_path:
                self._get_or_create_file(file_path).status = "created"

        elif tool_name == "Edit":
            if file_path:
                self._get_or_create_file(file_path).mark_modified()

        elif tool_name == "Read":
            if file_path:
                self._get_or_create_file(file_path).mark_read()

        # 2. Execution Status Tracking (Bash)
        elif tool_name == "Bash":
            cmd = args.get("command", "") or args.get("cmd", "")
            # Only track significant commands
            if any(k in cmd for k in ["pytest", "npm test", "python", "node", "cargo test", "go test"]):
                self._update_cmd_status(cmd, result_content)

    def _get_or_create_file(self, path: str) -> FileState:
        if path not in self.files:
            self.files[path] = FileState(path=path)
        return self.files[path]

    def _update_cmd_status(self, cmd: str, output: str) -> None:
        """Parse command output to determine pass/fail status."""
        self.last_cmd_time = time.time()

        # Simple heuristic for failure
        # In most CLIs, failure is indicated by non-zero exit code (not captured here yet)
        # or keywords in stdout/stderr
        output_lower = output.lower()

        # Look for test runner summary patterns (last few lines are most relevant)
        # Typical patterns: "2 failed", "FAILED", "1 error", "FATAL:", "tests failed"
        lines = output_lower.strip().splitlines()
        # Focus on last 10 lines where test runners print summaries
        tail = "\n".join(lines[-10:]) if lines else ""

        # Failure patterns: match test runner summary formats
        failure_patterns = [
            " failed",      # "2 failed", "tests failed"
            " error",       # "1 error"
            "fatal:",       # "FATAL: ..."
            "failure",      # "FAILURE", "Build failure"
        ]
        # Success patterns
        success_patterns = [
            " passed",      # "10 passed"
            "success",      # "Build success"
            "all tests",    # "all tests passed"
            " ok",          # "tests ok", but not "token" etc.
        ]

        if any(p in tail for p in failure_patterns):
            self.last_cmd_status = f"🔴 FAILED: {cmd[:30]}..."
        elif any(p in tail for p in success_patterns):
            self.last_cmd_status = f"🟢 PASSED: {cmd[:30]}..."
        else:
            self.last_cmd_status = f"⚪ EXECUTED: {cmd[:30]}..."

    def render(self) -> str:
        """
        Render the project state as a Markdown block.
        This will be injected into the LLM context.
        """
        if not self.files and not self.last_cmd_status:
            return ""

        lines = ["🛡️ [Project State Monitor]"]

        # 1. Execution Status (High Priority)
        if self.last_cmd_status:
            lines.append(f"**Last Command Status**: {self.last_cmd_status}")

        # 2. File Working Set
        if self.files:
            lines.append("**Active Working Set**:")

            # Sort by relevance: Modified > Created > Read
            # Then by recency
            def sort_key(item):
                f = item[1]
                score = 0
                if f.status == "created": score = 3000
                elif f.status == "modified": score = 2000
                elif f.status == "read": score = 1000
                return score + f.last_accessed

            sorted_files = sorted(self.files.items(), key=sort_key, reverse=True)

            # Display modified/created files first
            displayed_count = 0
            for path, f in sorted_files:
                if displayed_count >= self.max_display_files:
                    lines.append(f"  ... ({len(self.files) - displayed_count} more files hidden)")
                    break

                icon = "📄"
                extra = ""
                if f.status == "created":
                    icon = "✨"
                    extra = "(Created)"
                elif f.status == "modified":
                    icon = "✏️"
                    extra = f"(Modified {f.access_count} times)"
                elif f.status == "read":
                    icon = "👀"
                    extra = "(Read only)"

                lines.append(f"- {icon} `{path}` {extra}")
                displayed_count += 1

        return "\n".join(lines)
