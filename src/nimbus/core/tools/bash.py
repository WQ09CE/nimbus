"""Bash Tool -- Execute shell commands with streaming output, timeout, and truncation.

Pi-coding-agent influence:
- on_update callback for streaming partial output (like pi's tool result streaming)
- Split result: output (text for LLM) + ui_detail (structured data for UI)
- Abort event for process group kill (pi-style killProcessTree)
"""

import asyncio
import os
import signal
import tempfile
from typing import Any, Callable, Dict, Optional

from nimbus.core.path_context import AgentPathContext

from . import sandbox
from .registry import ToolParameter, tool


def _sandbox_flag_on() -> bool:
    """Bash sandbox is opt-in via NIMBUS_BASH_SANDBOX (1/true/on). Default off
    keeps the existing test suite (which writes to arbitrary tmp paths and
    hits the network) green until callers opt in per-session."""
    flag = os.environ.get("NIMBUS_BASH_SANDBOX", "").strip().lower()
    return flag in ("1", "true", "on", "yes")

MAX_OUTPUT_BYTES = 50 * 1024  # 50KB (aligned with pi-coding-agent)
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
    description="Execute a bash command. REQUIRED: provide the `command` string argument with the exact shell command to run (for example: `pwd` or `ls -la`). Output truncated to last 2000 lines or 50KB. If truncated, full output is saved to a temp file.",
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
    _path_context: AgentPathContext = kwargs.get("_path_context") or AgentPathContext.from_cwd()
    cwd = _path_context.execution_cwd
    # NOTE: Bash uses execution_cwd (tracks cd), not target_root

    # Save start cwd before subprocess (cd may change execution_cwd later)
    start_cwd = _path_context.execution_cwd

    # Wrap command with a cwd sentinel so we can track `cd` effects.
    # The sentinel is printed on a unique line after the user's command
    # completes. The command's exit status is captured before the sentinel
    # echo and re-raised at the end -- otherwise the echo masks it and every
    # failing command would report exit 0.
    _CWD_SENTINEL = "__NIMBUS_CWD__:"
    wrapped_command = (
        f'{{ {command}\n}}; __nimbus_st=$?; '
        f'echo "{_CWD_SENTINEL}$(pwd)"; exit $__nimbus_st'
    )

    # Optionally confine execution to an OS-level trust boundary. When enabled,
    # the child inherits only whitelisted env (no API keys), cannot reach the
    # network, and can only write inside the agent's writable_roots.
    #
    # Three states, never silent: 'off' (not requested), 'active' (confined),
    # 'unavailable' (requested but no platform mechanism -- runs UNsandboxed
    # and says so in the result, so degradation is observable, not silent).
    sb_tmp: Optional[str] = None
    sandbox_state = "off"
    if _sandbox_flag_on():
        sandbox_state = "active" if sandbox.sandbox_available() else "unavailable"
    if sandbox_state == "active":
        writable = list(getattr(_path_context, "writable_roots", None) or [_path_context.target_root])
        writable.append(start_cwd)
        profile = sandbox.build_profile(writable, allow_network=False)
        sb_tmp = tempfile.mkdtemp(prefix="nimbus-sb-")
        argv = sandbox.wrap_argv(profile, sb_tmp) + ["/bin/sh", "-c", wrapped_command]
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=sandbox.sandboxed_env(),
            preexec_fn=os.setsid,
        )
    else:
        process = await asyncio.create_subprocess_shell(
            wrapped_command,
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

    # Sandbox profile has been consumed at exec time and the process is done;
    # drop the temp dir regardless of which return path we take below.
    if sb_tmp:
        import shutil
        shutil.rmtree(sb_tmp, ignore_errors=True)

    if aborted:
        output = b"".join(chunks).decode("utf-8", errors="replace") if chunks else ""
        return {
            "output": f"[Aborted] {output[:2000]}",
            "ui_detail": {
                "command": command,
                "aborted": True,
                "exit_code": process.returncode,
                "partial_bytes": total_bytes,
                "executed_in": start_cwd,
                "sandbox": sandbox_state,
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
                "executed_in": start_cwd,
                "sandbox": sandbox_state,
            },
        }

    output = b"".join(chunks).decode("utf-8", errors="replace")

    # Extract cwd sentinel and update path context
    if _path_context and _CWD_SENTINEL in output:
        lines = output.split("\n")
        clean_lines = []
        for line in lines:
            if line.startswith(_CWD_SENTINEL):
                new_cwd = line[len(_CWD_SENTINEL):].strip()
                if new_cwd:
                    _path_context.update_cwd(new_cwd)
            else:
                clean_lines.append(line)
        output = "\n".join(clean_lines)

    original_lines = output.count("\n") + 1
    original_bytes = len(output.encode("utf-8"))
    truncated = False
    full_output_path = None

    # Save full output to temp file if it exceeds limit (pi-style)
    if original_bytes > MAX_OUTPUT_BYTES:
        try:
            fd, full_output_path = tempfile.mkstemp(
                prefix="nimbus-bash-", suffix=".log", dir=tempfile.gettempdir()
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(output)
        except Exception:
            full_output_path = None  # Silently skip if temp file creation fails

    # Truncate by bytes (keep tail, aligned with pi)
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

    # Append truncation notice with temp file path (pi-style)
    if truncated and full_output_path:
        output += f"\n\n[Output truncated to {MAX_OUTPUT_BYTES // 1024}KB. Full output ({original_bytes // 1024}KB): {full_output_path}]"

    if not output.strip():
        output = "(no output)"

    exit_code = process.returncode
    if exit_code != 0:
        output += f"\n\nExit code: {exit_code}"

    # Failure attribution (runner-failure checked before denial inside
    # classify_output): the model must not mistake "sandbox broke, command
    # never ran" or "sandbox blocked an effect" for an ordinary failure.
    sandbox_verdict = None
    if sandbox_state == "active" and exit_code != 0:
        sandbox_verdict = sandbox.classify_output(output)
    if sandbox_verdict == "runner-failure":
        output = (
            "[Sandbox runner failure: sandbox-exec itself failed -- "
            "the command was NOT executed]\n" + output
        )
    elif sandbox_verdict == "denial":
        output += (
            "\n[Likely sandbox denial: an operation was blocked by the sandbox "
            "(writes confined to workspace roots, network disabled). "
            "Not necessarily a bug in the command.]"
        )
    if sandbox_state == "unavailable":
        output += (
            "\n\n[Sandbox requested via NIMBUS_BASH_SANDBOX but no mechanism "
            "exists on this platform -- command ran UNSANDBOXED]"
        )

    ui_detail = {
        "command": command,
        "exit_code": exit_code,
        "total_lines": original_lines,
        "total_bytes": original_bytes,
        "truncated": truncated,
        "timed_out": False,
        "executed_in": start_cwd,
        "new_execution_cwd": _path_context.execution_cwd,
        "sandbox": sandbox_state,
    }
    if sandbox_verdict:
        ui_detail["sandbox_verdict"] = sandbox_verdict

    return {
        "output": output,
        "ui_detail": ui_detail,
    }
