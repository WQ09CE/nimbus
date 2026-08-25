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
from .registry import ToolParameter, ToolTraits, tool

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
    traits=ToolTraits(side_effects="execute"),
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

    # Effective sandbox capability comes from KernelGate's authorization
    # decision. Direct callers fall back to env configuration for compatibility.
    supplied_policy = kwargs.get("_sandbox_policy")
    explicit_mode = supplied_policy.get("mode") if isinstance(supplied_policy, dict) else None
    sandbox_mode = sandbox.configured_mode(explicit_mode)
    writable = list(
        getattr(_path_context, "writable_roots", None)
        or [_path_context.target_root]
    )
    writable.append(start_cwd)
    allow_network = bool(
        supplied_policy.get("allow_network", False)
        if isinstance(supplied_policy, dict) else False
    )
    sandbox_details = sandbox.sandbox_plan(
        sandbox_mode, writable, allow_network=allow_network,
    )
    sandbox_state = str(sandbox_details["state"])
    sandbox_backend = str(sandbox_details["backend"])

    # Required means fail closed: an unavailable kernel boundary is a policy
    # denial, never an unconfined fallback.
    if sandbox_state == "unavailable" and sandbox_mode == "required":
        return {
            "status": "ERROR",
            "output": (
                "Sandbox is required for Bash, but no supported backend is "
                "available. The command was NOT executed."
            ),
            "ui_detail": {
                "command": command,
                "executed": False,
                "executed_in": start_cwd,
                "sandbox": sandbox_state,
                "sandbox_backend": sandbox_backend,
                "sandbox_mode": sandbox_mode,
                "sandbox_details": sandbox_details,
            },
        }

    sb_tmp: Optional[str] = None
    if sandbox_state == "active":
        sb_tmp = tempfile.mkdtemp(prefix="nimbus-sb-")
        argv = sandbox.wrap_command(
            ["/bin/sh", "-c", wrapped_command],
            writable_roots=writable,
            tmp_dir=sb_tmp,
            allow_network=allow_network,
        )
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
            # A requested-but-unavailable sandbox still strips credentials;
            # only syscall confinement degraded.
            env=sandbox.sandboxed_env() if sandbox_mode != "off" else None,
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
            "status": "CANCELLED",
            "output": f"[Aborted] {output[:2000]}",
            "ui_detail": {
                "command": command,
                "aborted": True,
                "exit_code": process.returncode,
                "partial_bytes": total_bytes,
                "executed_in": start_cwd,
                "sandbox": sandbox_state,
                "sandbox_backend": sandbox_backend,
                "sandbox_mode": sandbox_mode,
                "sandbox_details": sandbox_details,
            },
        }

    if timed_out:
        output = b"".join(chunks).decode("utf-8", errors="replace") if chunks else ""
        return {
            "status": "TIMEOUT",
            "output": f"Command timed out after {timeout}s: {command[:100]}\n\nPartial output:\n{output[:2000]}",
            "ui_detail": {
                "command": command,
                "timed_out": True,
                "timeout_seconds": timeout,
                "exit_code": process.returncode,
                "partial_bytes": total_bytes,
                "executed_in": start_cwd,
                "sandbox": sandbox_state,
                "sandbox_backend": sandbox_backend,
                "sandbox_mode": sandbox_mode,
                "sandbox_details": sandbox_details,
            },
        }

    output = b"".join(chunks).decode("utf-8", errors="replace")

    # Extract the final cwd sentinel without changing the command's own
    # newline semantics. ``rfind`` selects our trailing marker even if command
    # output happened to contain the marker text earlier.
    if _path_context and _CWD_SENTINEL in output:
        marker_at = output.rfind(_CWD_SENTINEL)
        trailer = output[marker_at + len(_CWD_SENTINEL):]
        new_cwd, separator, remainder = trailer.partition("\n")
        new_cwd = new_cwd.strip()
        if new_cwd:
            _path_context.update_cwd(new_cwd)
        output = output[:marker_at] + (remainder if separator else "")

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
        sandbox_verdict = sandbox.classify_output(output, backend=sandbox_backend)
    if sandbox_verdict == "runner-failure":
        output = (
            f"[Sandbox runner failure: {sandbox_backend} failed -- "
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
            "\n\n[UNSANDBOXED: sandbox was requested but no supported kernel "
            "backend exists on this platform -- credentials were stripped, but "
            "the command ran without filesystem/network confinement]"
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
        "sandbox_backend": sandbox_backend,
        "sandbox_mode": sandbox_mode,
        "sandbox_details": {
            **sandbox_details,
            **({"verdict": sandbox_verdict} if sandbox_verdict else {}),
        },
    }
    if sandbox_verdict:
        ui_detail["sandbox_verdict"] = sandbox_verdict

    return {
        "status": "OK" if exit_code == 0 else "ERROR",
        "output": output,
        "ui_detail": ui_detail,
    }
