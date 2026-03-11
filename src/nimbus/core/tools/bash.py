"""Bash Tool -- Execute shell commands with streaming output, timeout, and truncation.

Pi-coding-agent influence:
- on_update callback for streaming partial output (like pi's tool result streaming)
- Split result: output (text for LLM) + ui_detail (structured data for UI)
- Abort event for process group kill (pi-style killProcessTree)
"""

import asyncio
import os
import signal
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .registry import ToolParameter, tool

MAX_OUTPUT_BYTES = 100 * 1024  # 100KB
MAX_OUTPUT_LINES = 2000
DEFAULT_TIMEOUT = 60.0


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill entire process group (pi-style killProcessTree).

    Uses os.killpg to kill the process group, falling back to
    process.kill() if the group kill fails.
    """
    if process.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except ProcessLookupError:
            pass


@tool(
    name="Bash",
    description="Execute a bash command. Output truncated to first+last 2000 lines or 100KB.",
    parameters=[
        ToolParameter("command", "string", "The bash command to execute", required=True),
        ToolParameter("timeout", "number", "Timeout in seconds (default: 60)", required=False),
    ],
)
async def bash_command(
    command: str,
    timeout: Optional[float] = None,
    on_update: Optional[Callable[[str], None]] = None,
    _abort_event: Optional[asyncio.Event] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Execute bash command with optional streaming callback and abort support.

    Args:
        command: Shell command to run.
        timeout: Timeout in seconds.
        on_update: Called with each chunk of stdout for live streaming to UI.
            This is the pi-style "tool result streaming" pattern.
        _abort_event: If set, the process is killed immediately (pi-style abort).

    Returns:
        Dict with 'output' (for LLM) and 'ui_detail' (for UI rendering).
    """
    if not command or not command.strip():
        raise ValueError("command cannot be empty")

    timeout = float(timeout) if timeout else DEFAULT_TIMEOUT
    cwd = str(Path.cwd())

    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        preexec_fn=os.setsid,  # Create process group for clean kill
    )

    # Stream output line-by-line if callback provided (pi-style)
    chunks: list[bytes] = []
    total_bytes = 0
    timed_out = False
    aborted = False

    if on_update and process.stdout:
        async def _read_stream() -> None:
            nonlocal total_bytes
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                total_bytes += len(chunk)
                text = chunk.decode("utf-8", errors="replace")
                on_update(text)

        if _abort_event:
            # Race abort event against read stream
            read_task = asyncio.create_task(_read_stream())
            abort_task = asyncio.create_task(_abort_event.wait())
            try:
                done, pending = await asyncio.wait(
                    [read_task, abort_task],
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Cancel pending tasks to prevent orphan task leaks
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                if _abort_event.is_set():
                    aborted = True
                    await _kill_process_tree(process)
                elif read_task not in done:
                    # Timeout
                    timed_out = True
                    await _kill_process_tree(process)
                else:
                    # Normal completion
                    await process.wait()
            except asyncio.CancelledError:
                await _kill_process_tree(process)
                raise
        else:
            try:
                await asyncio.wait_for(_read_stream(), timeout=timeout)
                await process.wait()
            except asyncio.TimeoutError:
                timed_out = True
                await _kill_process_tree(process)
    else:
        if _abort_event:
            # Race abort event against communicate
            comm_task = asyncio.create_task(process.communicate())
            abort_task = asyncio.create_task(_abort_event.wait())
            try:
                done, pending = await asyncio.wait(
                    [comm_task, abort_task],
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Cancel pending tasks to prevent orphan task leaks
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                if _abort_event.is_set():
                    aborted = True
                    await _kill_process_tree(process)
                elif comm_task not in done:
                    # Timeout
                    timed_out = True
                    await _kill_process_tree(process)
                else:
                    # Normal completion
                    stdout, _ = comm_task.result()
                    chunks.append(stdout)
                    total_bytes = len(stdout)
            except asyncio.CancelledError:
                await _kill_process_tree(process)
                raise
        else:
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
                chunks.append(stdout)
                total_bytes = len(stdout)
            except asyncio.TimeoutError:
                timed_out = True
                await _kill_process_tree(process)

    if aborted:
        output = b"".join(chunks).decode("utf-8", errors="replace") if chunks else ""
        return {
            "output": f"[Aborted] {output[:2000]}",
            "ui_detail": {
                "command": command,
                "aborted": True,
                "exit_code": process.returncode,
                "partial_bytes": total_bytes,
            },
        }

    if timed_out:
        output = b"".join(chunks).decode("utf-8", errors="replace") if chunks else ""
        return {
            "output": f"Command timed out after {timeout}s: {command[:100]}\n\nPartial output:\n{output[:2000]}",
            "ui_detail": {
                "command": command,
                "timed_out": True,
                "timeout_seconds": timeout,
                "exit_code": process.returncode,
                "partial_bytes": total_bytes,
            },
        }

    output = b"".join(chunks).decode("utf-8", errors="replace")
    original_lines = output.count("\n") + 1
    original_bytes = len(output.encode("utf-8"))
    truncated = False

    # Truncate by bytes
    if original_bytes > MAX_OUTPUT_BYTES:
        output = output[-(MAX_OUTPUT_BYTES):]
        output = "[...truncated...]\n" + output
        truncated = True

    # Truncate by lines (keep head + tail for context)
    lines = output.split("\n")
    if len(lines) > MAX_OUTPUT_LINES:
        total = len(lines)
        head_lines = MAX_OUTPUT_LINES // 4   # 500 lines from start
        tail_lines = MAX_OUTPUT_LINES - head_lines  # 1500 lines from end
        head = lines[:head_lines]
        tail = lines[-tail_lines:]
        omitted = total - head_lines - tail_lines
        output = "\n".join(head) + f"\n\n[... {omitted} lines omitted (total {total} lines) ...]\n\n" + "\n".join(tail)
        truncated = True

    if not output.strip():
        output = "(no output)"

    exit_code = process.returncode
    if exit_code != 0:
        output += f"\n\nExit code: {exit_code}"

    return {
        "output": output,
        "ui_detail": {
            "command": command,
            "exit_code": exit_code,
            "total_lines": original_lines,
            "total_bytes": original_bytes,
            "truncated": truncated,
            "timed_out": False,
        },
    }
