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
from .utils import DEFAULT_MAX_BYTES, auto_offload_result, truncate_tail


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

        # Stream output with Ring Buffer optimization
        # Instead of accumulating all chunks in memory, we only keep:
        # 1. The head (first few KB) for context
        # 2. The tail (last few KB) for most recent output
        # Everything else goes to temp file if needed.
        
        HEAD_LIMIT = 4096  # Keep first 4KB
        TAIL_LIMIT = 64 * 1024  # Keep last 64KB
        
        chunks = [] # Stores head
        tail_buffer = bytearray() # Rolling buffer for tail
        total_bytes = 0
        temp_file_path = None
        temp_file = None

        async def read_stream(stream):
            nonlocal total_bytes, temp_file_path, temp_file, tail_buffer

            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break

                total_bytes += len(chunk)

                # 1. Temp File Handling
                if total_bytes > DEFAULT_MAX_BYTES and temp_file_path is None:
                    temp_file_path = tempfile.mktemp(suffix=".log", prefix="pi-bash-")
                    temp_file = open(temp_file_path, "wb")
                    # Write existing head chunks
                    for c in chunks:
                        temp_file.write(c)
                    # Write current tail buffer
                    temp_file.write(tail_buffer)

                if temp_file:
                    temp_file.write(chunk)

                # 2. Memory Management (Head/Tail)
                if len(chunks) * 8192 < HEAD_LIMIT:
                    # Still filling head
                    chunks.append(chunk)
                else:
                    # Update tail buffer
                    tail_buffer.extend(chunk)
                    if len(tail_buffer) > TAIL_LIMIT:
                        # Keep only the last TAIL_LIMIT bytes
                        # Slice efficiently
                        del tail_buffer[:-TAIL_LIMIT]

                # Stream partial output (Throttle this in production)
                if on_update:
                    # Construct current view
                    current_view = b"".join(chunks) + tail_buffer
                    full_text = current_view.decode("utf-8", errors="replace")
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
        # Reconstruct from Head + Tail (Memory Safe)
        reconstructed_output = b"".join(chunks) + tail_buffer
        
        is_internal_truncated = False
        # If we dropped data in the middle (between head and tail), insert a marker
        if total_bytes > len(reconstructed_output):
             is_internal_truncated = True
             dropped = total_bytes - len(reconstructed_output)
             marker = f"\n\n... [Skipped {dropped} bytes of intermediate output] ...\n\n".encode("utf-8")
             # Insert marker between head (chunks) and tail (tail_buffer)
             reconstructed_output = b"".join(chunks) + marker + tail_buffer

        reconstructed_text = reconstructed_output.decode("utf-8", errors="replace")
        
        if is_internal_truncated:
            # We already truncated strategically (Ring Buffer).
            # Do NOT call truncate_tail(), as it might blindly cut the Head we preserved.
            truncation = {
                "content": reconstructed_text,
                "truncated": True,
                "total_lines": -1, # Unknown/Irrelevant
                "output_lines": reconstructed_text.count('\n') + 1
            }
        else:
            truncation = truncate_tail(reconstructed_text)

        preview_text = truncation["content"] or "(no output)"

        # Add truncation notice
        if truncation.get("truncated") or temp_file_path:
            extra_info = ""
            if truncation.get("truncated"):
                total_lines = truncation.get("total_lines", 0)
                if total_lines > 0:
                    # Normal tail truncation
                    start_line = total_lines - truncation.get("output_lines", 0) + 1
                    end_line = total_lines
                    extra_info = f"Showing lines {start_line}-{end_line} of {total_lines}."
                else:
                    # Ring buffer truncation (Head + Tail)
                    extra_info = "Output partially truncated (showing head + tail)."
            
            if temp_file_path:
                extra_info += f" Full output saved to: {temp_file_path}"
                
            preview_text += f"\n\n[{extra_info}]"

        # Add exit code info if non-zero (don't raise - let LLM see the error)
        if process.returncode != 0:
            preview_text += f"\n\nCommand exited with code {process.returncode}"

        # If we have a temp file, read the REAL full content from it for offloading
        # Otherwise use the reconstructed text
        full_text_for_offload = reconstructed_text
        if temp_file_path and Path(temp_file_path).exists():
            try:
                with open(temp_file_path, "r", encoding="utf-8", errors="replace") as f:
                    full_text_for_offload = f.read()
                # Append exit code to full text as well
                if process.returncode != 0:
                    full_text_for_offload += f"\n\nCommand exited with code {process.returncode}"
            except Exception:
                pass

        # Auto-offload
        result = auto_offload_result(
            tool_name="Bash",
            full_content=full_text_for_offload,
            truncated_content=preview_text,
            total_bytes=total_bytes,
            workspace=workspace,
            **kwargs,
        )

        return result

    except Exception as e:
        # Re-raise known exceptions
        if isinstance(e, (asyncio.TimeoutError, OSError, SandboxError, ValueError)):
            raise
        # Wrap unexpected exceptions
        raise OSError(f"Failed to execute command: {str(e)}")
