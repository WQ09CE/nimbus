"""
Custom tool definitions for the Dual-Agent orchestration layer.

- Dispatch: Orchestrator sends sub-tasks to Executor
- Verify: Orchestrator runs deterministic checks on workspace
"""

import asyncio
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from nimbus.agentos import AgentOS



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
            "model": {
                "type": "string",
                "description": (
                    "Optional: specify which LLM model the Executor should use. "
                    "Aliases: 'claude'→claude-opus-4-6, 'sonnet'→claude-sonnet-4-5, 'gpt'→gpt-4o, 'gemini'→gemini-3.1-pro-preview. "
                    "Or use full model ID like 'openai-codex/gpt-4o'. "
                    "Default: same model as the orchestrator."
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
# Specialist Tool Definitions
# =============================================================================

EXPLORE_TOOL_DEF = {
    "name": "Explore",
    "description": (
        "Delegate codebase exploration to the Explorer agent (read-only). "
        "The Explorer can Read files, Glob for patterns, and Grep for content. "
        "Use for: finding files, understanding code structure, searching patterns. "
        "Returns the Explorer's findings with file paths and line numbers. "
        "Cheap and fast -- can be called multiple times or in parallel."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "What to explore. Be specific: what files to find, what patterns to search, "
                    "what code structure to understand."
                ),
            },
            "context": {
                "type": "string",
                "description": "Additional context from prior exploration or user instructions. Optional.",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds. Default 600. Increase if the task needs more time.",
            },
        },
        "required": ["task"],
    },
}

IMPLEMENT_TOOL_DEF = {
    "name": "Implement",
    "description": (
        "Delegate code implementation to the Implementer agent. "
        "The Implementer has full Read/Write/Edit/Bash/Glob/Grep permissions. "
        "Use for: writing code, editing files, running commands, multi-file changes. "
        "Provide clear, specific instructions with exact file paths and code details. "
        "Returns a summary of changes and list of modified files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Specific implementation task. Include: what to do, which files to modify, "
                    "exact names/values, success criteria."
                ),
            },
            "context": {
                "type": "string",
                "description": "Relevant code snippets, file contents, or prior findings. Optional.",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds. Default 600. Increase if the task needs more time.",
            },
        },
        "required": ["task"],
    },
}

DESIGN_TOOL_DEF = {
    "name": "Design",
    "description": (
        "Delegate architecture/design work to the Architect agent. "
        "The Architect can Read code and Write markdown (.md) files only. "
        "Use for: design documents, architecture proposals, technical specs, code reviews. "
        "Returns the created document content."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Design task: what document to create, what to analyze, what to propose.",
            },
            "context": {
                "type": "string",
                "description": "Relevant code references, requirements, or prior findings. Optional.",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds. Default 600. Increase if the task needs more time.",
            },
        },
        "required": ["task"],
    },
}

TEST_TOOL_DEF = {
    "name": "Test",
    "description": (
        "Delegate test execution to the Tester agent. "
        "The Tester can Read files, run Bash commands, and Glob for patterns. "
        "Use for: running test suites, verification commands, checking build status. "
        "Returns test results with pass/fail details. "
        "The Tester does NOT fix failures -- it only reports them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What to test: commands to run, test files to execute, what to verify.",
            },
            "context": {
                "type": "string",
                "description": "Context about recent changes or expected behavior. Optional.",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds. Default 600. Increase if the task needs more time.",
            },
        },
        "required": ["task"],
    },
}
