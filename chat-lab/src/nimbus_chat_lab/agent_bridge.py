"""One native model request per Pi child; never let Pi execute Nimbus tools."""

import asyncio
import json
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

from nimbus.adapters.types import TokenUsage, VcpuLLMResponse


class PiBridge:
    def __init__(self, root: Path, before_request, executable="pi"):
        self.root, self.before_request, self.executable = root, before_request, executable

    async def request(self, payload):
        await self.before_request()
        env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
        env.update(PI_SKIP_VERSION_CHECK="1", PI_TELEMETRY="0")
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
            stderr=asyncio.subprocess.DEVNULL,
            limit=3000000,
        )
        try:
            data = json.dumps(payload, ensure_ascii=False).encode()
            if len(data) > 500000:
                raise RuntimeError("Model context bound exceeded")
            proc.stdin.write(data)
            await proc.stdin.drain()
            proc.stdin.close()
            result = None
            size = 0
            async with asyncio.timeout(190):
                while line := await proc.stdout.readline():
                    size += len(line)
                    if size > 3000000:
                        raise RuntimeError("Provider output bound exceeded")
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    if item.get("nimbus"):
                        if result is not None:
                            raise RuntimeError("Duplicate bridge result")
                        result = item
                code = await proc.wait()
            if code or not result or result.get("error"):
                raise RuntimeError("Pi provider request failed")
            await self.before_request()
            return result["result"]
        finally:
            if proc.returncode is None:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(proc.wait(), 3)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()

    async def chat(self, messages, tools=None, on_chunk=None):
        data = await self.request({"op": "model", "messages": messages, "tools": tools or []})
        names = {t["function"]["name"] for t in tools or []}
        calls = data.get("tool_calls", [])
        if len(calls) > 8 or any(t.get("function", {}).get("name") not in names for t in calls):
            raise RuntimeError("Unexpected tool request")
        if on_chunk and data.get("content"):
            on_chunk(data["content"])
        u = data.get("usage", {})
        return VcpuLLMResponse(
            content=data.get("content", ""),
            tool_calls=calls,
            usage=TokenUsage(
                input=u.get("input", 0), output=u.get("output", 0), cache_read=u.get("cacheRead", 0)
            ),
        )

    async def search(self, query, source="both"):
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 12000
            or source not in ("x", "web", "both")
        ):
            raise ValueError("Invalid search request")
        return await self.request({"op": "search", "query": query, "source": source})
