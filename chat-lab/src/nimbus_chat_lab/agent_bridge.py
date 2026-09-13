"""One native model request per Pi child; Nimbus alone executes client tools."""

import asyncio
import json
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from nimbus.adapters.types import TokenUsage, VcpuLLMResponse

from .diagnostics import BridgeFailure, BridgeTimeout, exception_record, save_diagnostic


class PiBridge:
    def __init__(self, root: Path, before_request, executable="pi"):
        self.root, self.before_request, self.executable = root, before_request, executable
        self.last_failure = None

    async def request(self, payload, *, validate=None, timeout_seconds=190):
        await self.before_request()
        self.last_failure = None
        request_id = uuid4()
        proc, stderr_task, failure, bridge_failure = None, None, None, None
        stdout, stderr = bytearray(), bytearray()
        stderr_bytes = 0
        stage = "request_encode"
        search_error = {}

        async def drain_stderr():
            nonlocal stderr_bytes
            while chunk := await proc.stderr.read(65536):
                stderr_bytes += len(chunk)
                stderr.extend(chunk[: max(0, 262144 - len(stderr))])

        try:
            data = json.dumps(payload, ensure_ascii=False).encode()
            if len(data) > 500000:
                raise ValueError("Model context bound exceeded")
            env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
            env.update(PI_SKIP_VERSION_CHECK="1", PI_TELEMETRY="0")
            stage = "spawn"
            async with asyncio.timeout(timeout_seconds):
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "nimbus_chat_lab.child_exec",
                    str(os.getpid()),
                    self.executable,
                    "--no-extensions",
                    "--no-skills",
                    "--no-context-files",
                    "--no-prompt-templates",
                    "--no-approve",
                    "--no-tools",
                    "--no-session",
                    "--provider",
                    "openai-codex",
                    "--model",
                    "gpt-6-astra",
                    "-e",
                    str(Path(__file__).with_name("pi_bridge.ts")),
                    "-p",
                    cwd=self.root,
                    env=env,
                    start_new_session=True,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=3000000,
                )
                stderr_task = asyncio.create_task(drain_stderr())
                stage = "child_io"
                proc.stdin.write(data)
                await proc.stdin.drain()
                proc.stdin.close()
                while chunk := await proc.stdout.read(65536):
                    remaining = 3000000 - len(stdout)
                    stdout.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        raise ValueError("Provider output bound exceeded")
                code = await proc.wait()
                await stderr_task
                stage = "child_exit"
                if code:
                    raise RuntimeError(f"Pi child exited with status {code}")
                frames = []
                # Protocol is LF-delimited; Unicode separators in JSON strings are not framing.
                for line in bytes(stdout).split(b"\n"):
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(item, dict) and item.get("nimbus"):
                        frames.append(item)
                stage = "missing_result" if not frames else "duplicate_result"
                if len(frames) != 1:
                    raise RuntimeError(f"Expected one bridge result, got {len(frames)}")
                result = frames[0]
                stage = "provider_error"
                if result.get("error"):
                    if payload.get("op") == "search" and isinstance(result.get("failure"), dict):
                        safe = result["failure"]
                        if safe.get("code") in (
                            "upstream_http_error",
                            "upstream_timeout",
                            "upstream_network_error",
                            "provider_response_invalid",
                            "provider_incomplete",
                            "provider_no_search",
                            "provider_error",
                        ):
                            search_error = {
                                "code": safe["code"],
                                "retryable": safe.get("retryable") is True,
                            }
                            if (
                                type(safe.get("http_status")) is int
                                and 100 <= safe["http_status"] <= 599
                            ):
                                search_error["http_status"] = safe["http_status"]
                                if (
                                    safe["http_status"] == 429
                                    and type(safe.get("retry_after_seconds")) is int
                                ):
                                    search_error["retry_after_seconds"] = max(
                                        1, min(3600, safe["retry_after_seconds"])
                                    )
                    raise RuntimeError("Pi bridge reported an error; original details in stdout")
                stage = "response_validation"
                response = result["result"]
                if not isinstance(response, dict):
                    raise ValueError("Bridge result is not an object")
                if validate:
                    response = validate(response)
        except asyncio.CancelledError as exc:
            stage, failure = "cancelled", exc
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                stage = "timeout"
            failure = exc
            failure_type = BridgeTimeout if isinstance(exc, TimeoutError) else BridgeFailure
            bridge_failure = failure_type(stage, request_id)
            bridge_failure.search_error = search_error
            self.last_failure = bridge_failure
            raise bridge_failure from None
        finally:
            if proc and proc.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), 3)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
            if stderr_task:
                try:
                    await asyncio.wait_for(stderr_task, 2)
                except asyncio.CancelledError:
                    stderr_task.cancel()
                    with suppress(Exception, asyncio.CancelledError):
                        await stderr_task
                    if asyncio.current_task().cancelling() and not isinstance(
                        failure, asyncio.CancelledError
                    ):
                        raise  # A fresh caller cancellation during cleanup must not become success.
                except Exception:
                    stderr_task.cancel()
                    with suppress(Exception, asyncio.CancelledError):
                        await stderr_task
            if failure is not None or stderr:
                saved = save_diagnostic(
                    self.root,
                    request_id,
                    {
                        "stage": stage,
                        "operation": payload.get("op") if isinstance(payload, dict) else None,
                        "exit_code": proc.returncode if proc else None,
                        "exception": exception_record(failure) if failure else None,
                        "stdout": stdout.decode("utf-8", "replace") if failure else "",
                        "stdout_at_bound": len(stdout) >= 3000000,
                        "stderr": stderr.decode("utf-8", "replace"),
                        "stderr_bytes": stderr_bytes,
                        "stderr_truncated": stderr_bytes > len(stderr),
                    },
                )
                if bridge_failure:
                    bridge_failure.diagnostic_saved = saved
        # Authority loss is not a provider failure; preserve existing cancellation/fencing semantics.
        await self.before_request()
        return response

    async def chat(self, messages, tools=None, on_chunk=None):
        def decode(data):
            names = {t["function"]["name"] for t in tools or []}
            calls = data.get("tool_calls", [])
            if (
                not isinstance(calls, list)
                or len(calls) > 8
                or any(
                    not isinstance(t, dict) or t.get("function", {}).get("name") not in names
                    for t in calls
                )
            ):
                raise ValueError("Unexpected tool request")
            u = data.get("usage", {})
            return VcpuLLMResponse(
                content=data.get("content", ""),
                tool_calls=calls,
                usage=TokenUsage(
                    input=u.get("input", 0),
                    output=u.get("output", 0),
                    cache_read=u.get("cacheRead", 0),
                ),
            )

        result = await self.request(
            {"op": "model", "messages": messages, "tools": tools or []}, validate=decode
        )
        if on_chunk and result.content:
            on_chunk(result.content)
        return result

    async def search(
        self, query, source="both", *, mode="research", timeout_seconds=175, x_filters=None
    ):
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 12000
            or source not in ("x", "web", "both")
            or mode not in ("discover", "verify", "research")
            or type(timeout_seconds) not in (float, int)
            or not 1 <= timeout_seconds <= 175
        ):
            raise ValueError("Invalid search request")
        return await self.request(
            {
                "op": "search",
                "query": query,
                "source": source,
                "mode": mode,
                "timeout_ms": int(timeout_seconds * 1000),
                "x_filters": x_filters or {},
            },
            timeout_seconds=min(190, timeout_seconds + 15),
        )
