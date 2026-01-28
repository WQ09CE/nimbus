"""V2 Bash tool for shell command execution.

This module provides the Bash tool in v2 format for AgentOS.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from nimbus.tools.sandbox import Sandbox, SandboxError

# Default timeout in milliseconds
DEFAULT_TIMEOUT_MS = 120000  # 2 minutes

# Maximum timeout in milliseconds
MAX_TIMEOUT_MS = 600000  # 10 minutes

# Maximum output length before truncation
MAX_OUTPUT_LENGTH = 30000


def _truncate_output(output: str, max_length: int = MAX_OUTPUT_LENGTH) -> str:
    """Truncate output if it exceeds max length."""
    if len(output) <= max_length:
        return output

    # Keep first portion and last portion
    first_portion = max_length * 2 // 3
    last_portion = max_length // 3 - 100

    truncation_msg = f"\n\n... [Output truncated: {len(output)} chars total, showing first {first_portion} and last {last_portion}] ...\n\n"

    return output[:first_portion] + truncation_msg + output[-last_portion:]


async def bash_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT_MS,
    cwd: Optional[str] = None,
    description: Optional[str] = None,
    workspace: Path | None = None,
    **kwargs: Any,
) -> str:
    """Execute a shell command.

    Args:
        command: The shell command to execute.
        timeout: Timeout in milliseconds (default 120000, max 600000).
        cwd: Working directory for the command. Must be within workspace.
        description: Optional description of what the command does.
        workspace: Workspace directory for cwd validation.

    Returns:
        Formatted output containing exit code, stdout, and stderr.

    Raises:
        ValueError: If command is empty or timeout is invalid.
        SandboxError: If cwd escapes workspace.
        asyncio.TimeoutError: If command exceeds timeout.
    """
    # Validate parameters
    if not command or not command.strip():
        raise ValueError("command cannot be empty")

    if timeout <= 0:
        raise ValueError(f"timeout must be positive, got {timeout}")

    if timeout > MAX_TIMEOUT_MS:
        timeout = MAX_TIMEOUT_MS

    # Convert timeout to seconds for asyncio
    timeout_seconds = timeout / 1000.0

    # Determine working directory
    if workspace is None:
        workspace = Path.cwd()

    if cwd:
        # Validate cwd with sandbox
        sandbox = Sandbox(workspace)
        try:
            work_dir = sandbox.validate(cwd, must_exist=True)
        except SandboxError:
            raise
        except FileNotFoundError:
            raise FileNotFoundError(f"Working directory not found: {cwd}")

        if not work_dir.is_dir():
            raise NotADirectoryError(f"cwd is not a directory: {cwd}")
    else:
        work_dir = workspace

    # Prepare environment
    env = os.environ.copy()

    # Execute command
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir),
            env=env,
        )
    except OSError as e:
        raise OSError(f"Failed to execute command: {e}")

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        # Kill the process on timeout
        process.kill()
        await process.wait()
        raise asyncio.TimeoutError(
            f"Command timed out after {timeout}ms: {command[:100]}..."
        )

    # Decode output
    try:
        stdout = stdout_bytes.decode("utf-8")
    except UnicodeDecodeError:
        stdout = stdout_bytes.decode("latin-1")

    try:
        stderr = stderr_bytes.decode("utf-8")
    except UnicodeDecodeError:
        stderr = stderr_bytes.decode("latin-1")

    exit_code = process.returncode

    # Truncate outputs if needed
    stdout = _truncate_output(stdout)
    stderr = _truncate_output(stderr)

    # Format output - Claude Code compatible format
    if exit_code == 0 and not stderr.strip():
        # Simple success case - just return stdout
        if stdout.strip():
            return stdout.rstrip()
        else:
            return "(no output)"
    else:
        # Has error or stderr - return detailed format
        output_parts = []

        if stdout.strip():
            output_parts.append(stdout.rstrip())

        if stderr.strip():
            output_parts.append(f"stderr:\n{stderr.rstrip()}")

        if exit_code != 0:
            output_parts.append(f"Exit code: {exit_code}")

        if not output_parts:
            output_parts.append("(no output)")

        return "\n\n".join(output_parts)


# V2 Tool Definition
BASH_TOOL: Dict[str, Any] = {
    "name": "Bash",
    "description": "Execute a shell command with optional timeout. Captures stdout, stderr, and exit code.",
    "function": bash_command,
    "dangerous": True,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": f"Timeout in milliseconds (max {MAX_TIMEOUT_MS}). Defaults to {DEFAULT_TIMEOUT_MS}.",
                "default": DEFAULT_TIMEOUT_MS,
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for command execution. Defaults to workspace.",
            },
            "description": {
                "type": "string",
                "description": "Brief description of what this command does",
            },
        },
        "required": ["command"],
    },
}
