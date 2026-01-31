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


# Global registry for background processes
# pid -> process object
_BACKGROUND_PROCESSES: Dict[int, asyncio.subprocess.Process] = {}


async def kill_process(pid: int, **kwargs) -> str:
    """Kill a background process."""
    import signal
    # Note: kwargs may contain 'workspace' injected by create_workspace_wrapper
    
    if pid not in _BACKGROUND_PROCESSES:
        # Try to kill by PID directly (system-wide) as fallback
        try:
            os.kill(pid, signal.SIGTERM)
            await asyncio.sleep(0.5)
            # Check if still running
            try:
                os.kill(pid, 0)  # Signal 0 = check if process exists
                # Still running, try SIGKILL
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass  # Process already dead
            return f"Process {pid} terminated (system kill)"
        except OSError as e:
            return f"No process found with PID {pid}: {e}"

    process = _BACKGROUND_PROCESSES[pid]
    try:
        # Try graceful termination first
        process.terminate()
        try:
            # Wait with timeout
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            # Force kill if SIGTERM didn't work
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=2.0)
        
        del _BACKGROUND_PROCESSES[pid]
        return f"Process {pid} terminated"
    except Exception as e:
        # Clean up from registry even if error
        if pid in _BACKGROUND_PROCESSES:
            del _BACKGROUND_PROCESSES[pid]
        return f"Process {pid} killed (with error: {e})"


async def bash_command(
    command: str,
    timeout: int = DEFAULT_TIMEOUT_MS,
    cwd: Optional[str] = None,
    background: bool = False,
    description: Optional[str] = None,
    workspace: Path | None = None,
    **kwargs: Any,
) -> str:
    """Execute a shell command.

    Args:
        command: The shell command to execute.
        timeout: Timeout in milliseconds (default 120000, max 600000).
        cwd: Working directory for the command. Must be within workspace.
        background: If True, run command in background and return PID immediately.
        description: Optional description of what the command does.
        workspace: Workspace directory for cwd validation.

    Returns:
        Formatted output containing exit code, stdout, and stderr.
        For background commands: "Background process started with PID <pid>..."
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

    # Handle background execution
    if background:
        _BACKGROUND_PROCESSES[process.pid] = process
        
        # Wait a brief moment to catch immediate failures (e.g., command not found)
        try:
            # Wait up to 1 second
            await asyncio.wait_for(process.wait(), timeout=1.0)
            
            # If we get here, process exited immediately
            stdout_bytes, stderr_bytes = await process.communicate()
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            
            # Remove from registry since it's done
            if process.pid in _BACKGROUND_PROCESSES:
                del _BACKGROUND_PROCESSES[process.pid]
                
            if process.returncode != 0:
                return f"Background command failed immediately (Exit {process.returncode}):\n{stderr or stdout}"
            else:
                return f"Background command finished immediately:\n{stdout}"
                
        except asyncio.TimeoutError:
            # Process is still running, which is good
            return (
                f"Background process started with PID {process.pid}.\n"
                f"Command: {command}\n"
                f"Use 'kill_process' tool with PID {process.pid} to stop it."
            )

    # Foreground execution
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
            "background": {
                "type": "boolean",
                "description": "Run command in background. Returns PID immediately. Useful for starting servers.",
                "default": False,
            },
            "description": {
                "type": "string",
                "description": "Brief description of what this command does",
            },
        },
        "required": ["command"],
    },
}

# Kill Process Tool
KILL_TOOL: Dict[str, Any] = {
    "name": "Kill",
    "description": "Kill a background process started by Bash tool.",
    "function": kill_process,
    "parameters": {
        "type": "object",
        "properties": {
            "pid": {
                "type": "integer",
                "description": "Process ID to kill",
            },
        },
        "required": ["pid"],
    },
}
