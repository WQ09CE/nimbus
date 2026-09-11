"""All filesystem/code tools use rootless gVisor; never execute a command on the host.

Workspace persistence is a size-bounded ZIP in PostgreSQL, not a writable host bind.
Each operation's writable filesystem is size-limited tmpfs inside a memory-limited unit.
"""

import asyncio
import base64
import hashlib
import json
import os
import signal
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4


class Sandbox:
    def __init__(self, state, before_request, config):
        self.state, self.before_request, self.config = state, before_request, config
        self.workspace_lock = asyncio.Lock()

    def verify_runtime(self):
        runtime = Path(self.config["runtime"]).resolve()
        base = (Path.home() / ".local/share/nimbus-chat-lab/gvisor").resolve()
        if base not in runtime.parents or runtime.name != "runsc":
            raise RuntimeError("Runtime must be in the private verified installation")
        root = runtime.parent
        manifest = self.config.get("runtime_sha256", {})
        if "runsc" not in manifest or "gvisor-bin/gvisor_sentry" not in manifest:
            raise RuntimeError("Verified gVisor manifest required")
        for relative, expected in manifest.items():
            path = (root / relative).resolve()
            if root not in path.parents or not path.is_file():
                raise RuntimeError("Runtime file missing")
            with path.open("rb") as f:
                if hashlib.file_digest(f, "sha256").hexdigest() != expected:
                    raise RuntimeError("Runtime verification failed")

    async def execute(self, action, args):
        # Native models can issue parallel tool calls. Serialize the entire
        # read/execute/snapshot transaction so sibling writes cannot overwrite
        # one another's workspace snapshots. Cross-turn ownership is DB-fenced.
        async with self.workspace_lock:
            return await self._execute(action, args)

    async def _execute(self, action, args):
        if action not in ("bash", "read", "write", "edit", "list") or not isinstance(args, dict):
            raise ValueError("Invalid workspace operation")
        if len(json.dumps(args)) > 200000:
            raise ValueError("Workspace input bound")
        await self.before_request()
        archive = await self.state.archive()
        result = await self.operation(action, args, archive)
        await self.before_request()
        data = base64.b64decode(result.pop("archive"), validate=True)
        if data != archive:
            await self.state.archive(data)
        return result

    async def operation(self, action, args, archive=b""):
        cfg = self.config
        await asyncio.to_thread(self.verify_runtime)
        if "@sha256:" not in cfg.get("image", ""):
            raise RuntimeError("Pinned image required")
        name = "nimbus-sbox-" + uuid4().hex
        unit = name + ".service"
        env = {
            k: os.environ[k]
            for k in ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
            if k in os.environ
        }
        with tempfile.TemporaryDirectory(prefix="nimbus-sandbox-") as tmp:
            root = Path(tmp)
            (root / "seed.zip").write_bytes(archive)
            (root / "request.json").write_text(json.dumps({"action": action, "args": args}))
            runner = Path(__file__).with_name("sandbox_runner.py")
            command = [
                "systemd-run",
                "--user",
                "--wait",
                "--pipe",
                "--collect",
                "--quiet",
                "--unit=" + name,
                "--property=Type=exec",
                "--property=MemoryMax=768M",
                "--property=TasksMax=256",
                "--property=CPUQuota=100%",
                "--property=RuntimeMaxSec=70",
                "/usr/bin/podman",
                "--runtime=" + cfg["runtime"],
                "--runtime-flag=platform=systrap",
                "--runtime-flag=ignore-cgroups=true",
                "run",
                "--name=" + name,
                "--label=nimbus-chat-owned=true",
                "--rm",
                "--cgroups=no-conmon",
                "--pull=never",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "--tmpfs=/workspace:rw,nosuid,nodev,size=64m",
                "--tmpfs=/tmp:rw,nosuid,nodev,size=16m",
                "--workdir=/workspace",
                "-v",
                f"{runner}:/runner.py:ro",
                "-v",
                f"{root}/seed.zip:/seed.zip:ro",
                "-v",
                f"{root}/request.json:/request.json:ro",
                cfg["image"],
                "python",
                "-I",
                "/runner.py",
            ]
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "nimbus_chat_lab.child_exec",
                str(os.getpid()),
                *command,
                env=env,
                start_new_session=True,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=25000000,
            )
            try:
                async with asyncio.timeout(65):
                    chunks = []
                    size = 0
                    while chunk := await proc.stdout.read(65536):
                        size += len(chunk)
                        if size > 24 * 1024 * 1024:
                            raise RuntimeError("Sandbox output bound")
                        chunks.append(chunk)
                    code = await proc.wait()
                if code:
                    raise RuntimeError("gVisor operation failed; no local fallback")
                result = json.loads(b"".join(chunks))
                if result.get("error") or not result.get("kernel", "").endswith("-gvisor"):
                    raise RuntimeError("Sandbox execution/validation failed")
                if (
                    not isinstance(result.get("archive"), str)
                    or len(result["archive"]) > 23 * 1024 * 1024
                ):
                    raise RuntimeError("Invalid workspace snapshot")
                return result
            finally:
                if proc.returncode is None:
                    with suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                    await proc.wait()
                # Exact owned transient unit and container only; also works after cancellations.
                for cmd in (
                    ["systemctl", "--user", "kill", "--signal=KILL", unit],
                    ["podman", "rm", "--force", "--time=0", name],
                ):
                    cleanup = await asyncio.create_subprocess_exec(
                        *cmd,
                        env=env,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    try:
                        await asyncio.wait_for(cleanup.wait(), 2)
                    except TimeoutError:
                        cleanup.kill()
                        await cleanup.wait()
