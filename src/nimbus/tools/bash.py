"""Bash command execution tool with timeout and output handling.

This module provides a tool for executing shell commands with support for
timeouts, output capturing, and error handling.

Example:
    >>> result = await bash_command("ls -la", timeout=5000)
    >>> print(result)
    Exit code: 0
    stdout:
    total 16
    drwxr-xr-x  4 user  staff  128 Jan 20 10:00 .
    ...
"""

import asyncio
import os
from pathlib import Path
from typing import Any, Optional

from .base import ToolParameter, tool
from .sandbox import Sandbox, SandboxError

# Default timeout in milliseconds
DEFAULT_TIMEOUT_MS = 120000  # 2 minutes

# Maximum timeout in milliseconds
MAX_TIMEOUT_MS = 600000  # 10 minutes

# Maximum output length before truncation
MAX_OUTPUT_LENGTH = 30000


def _truncate_output(output: str, max_length: int = MAX_OUTPUT_LENGTH) -> str:
    """Truncate output if it exceeds max length.

    Args:
        output: The output string to potentially truncate.
        max_length: Maximum allowed length.

    Returns:
        Original or truncated output with indicator.
    """
    if len(output) <= max_length:
        return output

    # Keep first portion and last portion
    first_portion = max_length * 2 // 3
    last_portion = max_length // 3 - 100  # Leave room for truncation message

    truncation_msg = f"\n\n... [Output truncated: {len(output)} chars total, showing first {first_portion} and last {last_portion}] ...\n\n"

    return output[:first_portion] + truncation_msg + output[-last_portion:]


@tool(
    name="Bash",
    description="Execute a shell command with optional timeout. Captures stdout, stderr, and exit code.",
    parameters=[
        ToolParameter(
            "command",
            "string",
            "The shell command to execute",
            required=True,
        ),
        ToolParameter(
            "timeout",
            "integer",
            f"Timeout in milliseconds (max {MAX_TIMEOUT_MS}). Defaults to {DEFAULT_TIMEOUT_MS}.",
            required=False,
            default=DEFAULT_TIMEOUT_MS,
        ),
        ToolParameter(
            "cwd",
            "string",
            "Working directory for command execution. Defaults to workspace.",
            required=False,
        ),
        ToolParameter(
            "description",
            "string",
            "Brief description of what this command does",
            required=False,
        ),
    ],
    dangerous=True,
)
async def bash_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT_MS,
    cwd: Optional[str] = None,
    description: Optional[str] = None,
    workspace: Optional[Path] = None,
    **kwargs: Any,
) -> str:
    """Execute a shell command.

    Runs a shell command using asyncio subprocess, capturing stdout and stderr.
    Supports timeouts, working directory specification, and output truncation.

    Features:
        - Async execution with asyncio.subprocess
        - Configurable timeout (up to 10 minutes)
        - Output truncation at 30000 characters
        - Captures both stdout and stderr
        - Returns exit code

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

    Example:
        >>> result = await bash_command("python --version")
        >>> print(result)
        Exit code: 0
        stdout:
        Python 3.11.0

        >>> result = await bash_command("ls /nonexistent")
        >>> print(result)
        Exit code: 2
        stderr:
        ls: /nonexistent: No such file or directory
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
    # Success (exit_code=0, no stderr): return stdout directly
    # Failure or has stderr: return detailed info
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
