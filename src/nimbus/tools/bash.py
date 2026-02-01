"""Bash Tool - Enhanced with streaming output and temp file support

Based on pi-coding-agent implementation with:
- Streaming output via callback (for UI progress)
- Temp file for large outputs (> 50KB)
- Tail truncation (keep last N lines)
- Default 60s timeout

Example:
    >>> result = await bash_command("ls -la", timeout=10.0)
    >>> print(result)
    total 16
    drwxr-xr-x  4 user  staff  128 Jan 20 10:00 .
    ...

    >>> def on_progress(partial_output):
    ...     print(f"Progress: {len(partial_output)} bytes")
    >>> result = await bash_command("long_command", on_update=on_progress)
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from .sandbox import Sandbox, SandboxError
from .utils import DEFAULT_MAX_BYTES, truncate_tail


async def bash_command(
    command: str,
    timeout: Optional[float] = 60.0,  # Default 60s timeout
    workspace: Optional[Path] = None,
    on_update: Optional[Callable[[str], None]] = None,
    **kwargs: Any,
) -> str:
    """
    Execute a bash command. Supports streaming output and temp files.

    Features:
    - Default 60s timeout (configurable)
    - Streaming output via on_update callback
    - Temp file for outputs > 50KB
    - Tail truncation (keep last 2000 lines or 50KB)

    Args:
        command: Bash command to execute
        timeout: Timeout in seconds (default: 60s)
        workspace: Working directory
        on_update: Callback for streaming partial output

    Returns:
        Command output (stdout + stderr) with truncation notice if applicable

    Raises:
        ValueError: If command is empty
        SandboxError: If workspace escapes sandbox
        asyncio.TimeoutError: If command exceeds timeout
    """
    # Ensure types
    if timeout is not None:
        try:
            timeout = float(timeout)
        except (ValueError, TypeError):
             timeout = 60.0

    # Validate parameters
    if not command or not command.strip():
        raise ValueError("command cannot be empty")

    if workspace is None:
        workspace = Path.cwd()

    # Validate workspace
    sandbox = Sandbox(workspace)
    try:
        work_dir = sandbox.validate(str(workspace))
    except SandboxError:
        raise

    try:
        # Start process
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(work_dir),
        )

        # Stream output
        chunks = []
        total_bytes = 0
        temp_file_path = None
        temp_file = None

        async def read_stream(stream):
            nonlocal total_bytes, temp_file_path, temp_file

            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break

                chunks.append(chunk)
                total_bytes += len(chunk)

                # Create temp file if exceeds limit
                if total_bytes > DEFAULT_MAX_BYTES and temp_file_path is None:
                    temp_file_path = tempfile.mktemp(suffix=".log", prefix="pi-bash-")
                    temp_file = open(temp_file_path, "wb")
                    # Write all buffered chunks
                    for c in chunks:
                        temp_file.write(c)

                # Write to temp file if we have one
                if temp_file:
                    temp_file.write(chunk)

                # Stream partial output
                if on_update:
                    full_text = b"".join(chunks).decode("utf-8", errors="replace")
                    truncation = truncate_tail(full_text)
                    on_update(truncation["content"])

        # Read both streams and wait for process with timeout
        async def run_with_streams():
            await asyncio.gather(read_stream(process.stdout), read_stream(process.stderr))
            await process.wait()

        try:
            if timeout:
                await asyncio.wait_for(run_with_streams(), timeout=timeout)
            else:
                await run_with_streams()
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

            if temp_file:
                temp_file.close()

            raise asyncio.TimeoutError(f"Command timed out after {timeout}s: {command[:100]}...")

        # Close temp file
        if temp_file:
            temp_file.close()

        # Build final output
        full_output = b"".join(chunks).decode("utf-8", errors="replace")
        truncation = truncate_tail(full_output)

        output_text = truncation["content"] or "(no output)"

        # Add truncation notice
        if truncation["truncated"]:
            start_line = truncation["total_lines"] - truncation["output_lines"] + 1
            end_line = truncation["total_lines"]
            output_text += (
                f"\n\n[Showing lines {start_line}-{end_line} of {truncation['total_lines']}. "
                f"Full output: {temp_file_path}]"
            )

        # Add exit code info if non-zero (don't raise - let LLM see the error)
        if process.returncode != 0:
            output_text += f"\n\nCommand exited with code {process.returncode}"

        return output_text

    except Exception as e:
        # Re-raise known exceptions
        if isinstance(e, (asyncio.TimeoutError, OSError, SandboxError, ValueError)):
            raise
        # Wrap unexpected exceptions
        raise OSError(f"Failed to execute command: {str(e)}")
