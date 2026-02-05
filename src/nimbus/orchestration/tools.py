"""
Custom tool definitions for the Dual-Agent orchestration layer.

- Dispatch: Core sends sub-tasks to Executor
- Verify: Core runs deterministic checks on workspace
"""

import asyncio
import socket
from pathlib import Path
from typing import Any, Dict, List

from nimbus.tools.base import ToolParameter, tool


# =============================================================================
# Dispatch Tool Definition (for AgentOS.register_tool)
# =============================================================================

DISPATCH_TOOL_DEF = {
    "name": "Dispatch",
    "description": (
        "Dispatch a sub-task to the Executor agent for implementation. "
        "The Executor has full Read/Write/Edit/Bash permissions. "
        "Provide a clear, specific task description with exact file paths, "
        "names, and values. Returns the Executor's summary and a list of "
        "files it created/modified/deleted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Clear, specific implementation task for the Executor. "
                    "Include: what to do, which files to modify, "
                    "exact names/values to use, and success criteria."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Additional context: relevant code snippets, file contents, "
                    "or constraints the Executor needs to know. Optional."
                ),
            },
        },
        "required": ["task"],
    },
}


# =============================================================================
# Verify Tool Definition
# =============================================================================

VERIFY_TOOL_DEF = {
    "name": "Verify",
    "description": (
        "Run deterministic verification checks on the workspace. "
        "Checks include: file existence, pattern matching in files, "
        "command exit codes, port listening, and process running. "
        "Returns structured pass/fail results. Use after Dispatch to verify work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "description": "List of verification checks to run",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "file_exists",
                                "file_not_exists",
                                "file_contains",
                                "file_not_contains",
                                "command_succeeds",
                                "command_output_contains",
                                "port_listening",
                                "process_running",
                            ],
                            "description": "Type of check to perform",
                        },
                        "target": {
                            "type": "string",
                            "description": (
                                "Target of the check: file path, command string, "
                                "port number, or process name pattern"
                            ),
                        },
                        "pattern": {
                            "type": "string",
                            "description": (
                                "For file_contains/file_not_contains: text to search. "
                                "For command_output_contains: expected substring in output."
                            ),
                        },
                    },
                    "required": ["type", "target"],
                },
            },
        },
        "required": ["checks"],
    },
}


# =============================================================================
# Verify Implementation
# =============================================================================

async def run_verify_checks(checks: List[Dict[str, Any]], workspace: Path) -> str:
    """
    Execute deterministic verification checks.

    Args:
        checks: List of check specifications
        workspace: Workspace root for resolving relative paths

    Returns:
        Formatted verification report with ✅/❌ per check
    """
    results = []

    for check in checks:
        check_type = check.get("type", "")
        target = check.get("target", "")
        pattern = check.get("pattern", "")

        # LLM sometimes uses 'file_path' or 'path' instead of 'target'
        if not target:
            target = check.get("file_path", "") or check.get("path", "") or check.get("command", "")

        try:
            if check_type == "file_exists":
                path = _resolve_path(target, workspace)
                passed = path.exists() and path.is_file()
                results.append(_fmt(passed, f"file_exists: {target}"))

            elif check_type == "file_not_exists":
                path = _resolve_path(target, workspace)
                passed = not path.exists()
                results.append(_fmt(passed, f"file_not_exists: {target}"))

            elif check_type == "file_contains":
                path = _resolve_path(target, workspace)
                if path.exists():
                    content = path.read_text(errors="replace")
                    passed = pattern in content
                else:
                    passed = False
                results.append(_fmt(passed, f"file_contains: '{pattern}' in {target}"))

            elif check_type == "file_not_contains":
                path = _resolve_path(target, workspace)
                if path.exists():
                    content = path.read_text(errors="replace")
                    passed = pattern not in content
                else:
                    passed = True  # file doesn't exist, so it doesn't contain the pattern
                results.append(_fmt(passed, f"file_not_contains: '{pattern}' in {target}"))

            elif check_type == "command_succeeds":
                proc = await asyncio.create_subprocess_shell(
                    target,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workspace),
                )
                _, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                passed = proc.returncode == 0
                results.append(_fmt(passed, f"command_succeeds: {target[:80]}"))

            elif check_type == "command_output_contains":
                proc = await asyncio.create_subprocess_shell(
                    target,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workspace),
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                output = (stdout or b"").decode(errors="replace") + (stderr or b"").decode(errors="replace")
                passed = pattern in output
                detail = f" (got: {output[:100]})" if not passed else ""
                results.append(_fmt(passed, f"command_output_contains: '{pattern}' in `{target[:60]}`{detail}"))

            elif check_type == "port_listening":
                port = int(target)
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                try:
                    passed = sock.connect_ex(("localhost", port)) == 0
                finally:
                    sock.close()
                results.append(_fmt(passed, f"port_listening: {port}"))

            elif check_type == "process_running":
                proc = await asyncio.create_subprocess_shell(
                    f"pgrep -f {target!r}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                passed = proc.returncode == 0 and bool(stdout.strip())
                results.append(_fmt(passed, f"process_running: {target}"))

            else:
                results.append(f"⚠️  unknown check type: {check_type}")

        except asyncio.TimeoutError:
            results.append(f"⏱  timeout: {check_type}: {target[:60]}")
        except Exception as e:
            results.append(f"💥 error in {check_type}: {e}")

    all_passed = all("✅" in r for r in results)
    header = "## Verification: ALL PASSED ✅" if all_passed else "## Verification: ISSUES FOUND ❌"
    return header + "\n" + "\n".join(results)


def _resolve_path(target: str, workspace: Path) -> Path:
    """Resolve a path, treating relative paths as relative to workspace."""
    p = Path(target)
    if p.is_absolute():
        return p
    return workspace / p


def _fmt(passed: bool, msg: str) -> str:
    return f"{'✅' if passed else '❌'} {msg}"


# =============================================================================
# Core Bash Whitelist Filter
# =============================================================================

CORE_BASH_WHITELIST_PREFIXES = [
    # Search
    "grep", "egrep", "fgrep", "rg", "ag",
    # File discovery
    "find", "fd", "locate",
    # Directory browsing
    "ls", "tree", "du",
    # File viewing
    "cat", "head", "tail", "less", "more", "bat",
    # File info
    "wc", "stat", "file", "md5sum", "sha256sum",
    # Comparison
    "diff", "comm",
    # Output
    "echo", "printf",
    # Verification scripts
    "python3 -c", "python -c",
    # Command lookup
    "which", "type", "whereis",
    # Environment
    "env", "printenv",
    # Network check (read-only)
    "curl", "wget",
    "nc -z",
    # Process inspection
    "pgrep", "ps", "lsof",
    # Git read-only
    "git status", "git log", "git diff", "git show", "git branch",
    # Other read-only
    "date", "uname", "hostname", "whoami", "id",
    "pip list", "pip show", "pip freeze",
    "npm list", "npm ls",
    "test ", "[ ",  # shell test expressions
]


def is_command_readonly(command: str) -> bool:
    """
    Check if a command matches the Core agent's read-only whitelist.

    Uses prefix matching. Returns True if the command is allowed.
    """
    cmd = command.strip()
    # Allow pipes and chains if each segment is whitelisted
    # But for simplicity, just check the primary command
    # (pipes like `grep ... | wc -l` are fine)

    # Strip leading env vars like KEY=val
    while "=" in cmd.split()[0] if cmd.split() else False:
        cmd = cmd.split(None, 1)[1] if " " in cmd else ""

    for prefix in CORE_BASH_WHITELIST_PREFIXES:
        if cmd.startswith(prefix):
            return True

    return False
