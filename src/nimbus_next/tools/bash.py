"""Bash Tool — Execute shell commands with timeout and output truncation."""

import asyncio
from pathlib import Path
from typing import Any, Optional

from .registry import ToolParameter, tool

MAX_OUTPUT_BYTES = 100 * 1024  # 100KB
MAX_OUTPUT_LINES = 2000
DEFAULT_TIMEOUT = 60.0


@tool(
    name="Bash",
    description="Execute a bash command. Output truncated to last 2000 lines or 100KB.",
    parameters=[
        ToolParameter("command", "string", "The bash command to execute", required=True),
        ToolParameter("timeout", "number", "Timeout in seconds (default: 60)", required=False),
    ],
)
async def bash_command(command: str, timeout: Optional[float] = None, **kwargs: Any) -> str:
    if not command or not command.strip():
        raise ValueError("command cannot be empty")

    timeout = float(timeout) if timeout else DEFAULT_TIMEOUT
    cwd = str(Path.cwd())

    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return f"Command timed out after {timeout}s: {command[:100]}"

    output = stdout.decode("utf-8", errors="replace")

    # Truncate by bytes
    if len(output.encode("utf-8")) > MAX_OUTPUT_BYTES:
        output = output[-(MAX_OUTPUT_BYTES):]
        output = "[...truncated...]\n" + output

    # Truncate by lines (keep tail)
    lines = output.split("\n")
    if len(lines) > MAX_OUTPUT_LINES:
        total = len(lines)
        lines = lines[-MAX_OUTPUT_LINES:]
        output = "\n".join(lines)
        output = f"[Showing last {MAX_OUTPUT_LINES} of {total} lines]\n" + output

    if not output.strip():
        output = "(no output)"

    if process.returncode != 0:
        output += f"\n\nExit code: {process.returncode}"

    return output
