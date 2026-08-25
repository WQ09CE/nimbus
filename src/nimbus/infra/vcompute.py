"""vcompute — a local virtual compute service for exercising ExecutionBackend.

One process plays the role of a compute provider: leases are workspaces with a
persistent cwd, execs run in the lease's own process group and are cancellable,
and /v1/chaos injects exactly the fault classes the Gate routing table must
survive (network blips, lease recycling, auth required/expired).

Exec responses stream NDJSON — {"type":"chunk","data":...} lines while the
command runs, then one {"type":"result",...} line (step-6 finding #3: the
streaming leg). When the exec request carries a sandbox grant, the command is
confined with bubblewrap: host filesystem read-only, workspace writable,
network unshared unless allowed (finding #4: the grant travels).

This is validation infra, not a product: it binds 127.0.0.1 only (it executes
arbitrary shell commands) and keeps no state across restarts — a restart IS a
lease-recycle event, which is the honest cloud behavior.

Run: python -m nimbus.infra.vcompute  (VCOMPUTE_PORT, VCOMPUTE_ROOT to override)
"""

import asyncio
import json
import os
import random
import shutil
import tarfile
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

SENTINEL = "__VC_CWD__"
DEFAULT_PORT = 8788
CHUNK_QUEUE_MAX = 256


class ExecReq(BaseModel):
    call_id: str
    command: str
    timeout_s: float = 60.0
    # Travelling sandbox grant: {"mode": off|best_effort|required,
    # "allow_network": bool, "required": bool}. Absent → unconfined.
    sandbox: Optional[Dict] = None


class ChaosReq(BaseModel):
    fail_next: Optional[int] = None
    network_error_rate: Optional[float] = None
    auth_state: Optional[str] = None  # ok | required | expired_once


class LeaseState:
    def __init__(self, lease_id: str, workspace: Path):
        self.lease_id = lease_id
        self.workspace = workspace
        self.cwd = str(workspace)
        self.procs: Dict[str, asyncio.subprocess.Process] = {}
        self.created = time.time()


def _kill_group(proc: asyncio.subprocess.Process) -> bool:
    try:
        os.killpg(proc.pid, 9)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _sandbox_argv(sandbox: Optional[Dict], workspace: str):
    """Translate a travelling grant into a bwrap prefix + verdict.

    Returns (argv_prefix or None-on-fail-closed, verdict_dict).
    """
    mode = (sandbox or {}).get("mode")
    if mode not in ("best_effort", "required"):
        return [], {"state": "off", "backend": "none", "mode": mode or "off"}
    if shutil.which("bwrap") is None:
        verdict = {"state": "unavailable", "backend": "none", "mode": mode}
        if mode == "required":
            return None, verdict  # fail closed
        return [], verdict
    prefix = [
        "bwrap", "--die-with-parent",
        "--ro-bind", "/", "/",
        "--dev", "/dev", "--proc", "/proc",
        "--tmpfs", "/tmp",
        # Bind after the tmpfs so a workspace under /tmp is restored writable.
        "--bind", workspace, workspace,
    ]
    network = "host" if (sandbox or {}).get("allow_network") else "denied"
    if network == "denied":
        prefix.append("--unshare-net")
    return prefix, {
        "state": "active", "backend": "bubblewrap", "mode": mode, "network": network,
    }


def create_app(root: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="nimbus-vcompute")
    base = Path(
        root or os.environ.get("VCOMPUTE_ROOT")
        or Path.home() / ".nimbus" / "vcompute"
    )
    st = app.state
    st.leases = {}
    st.chaos = {"fail_next": 0, "network_error_rate": 0.0, "auth_state": "ok"}
    st.exec_count = 0  # execs actually started (observability for tests)
    st.owners = set()  # strong refs to owner tasks (they outlive the request)

    def _err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"code": code, "message": message})

    @app.get("/v1/health")
    async def health():
        return {"ok": True, "leases": len(st.leases), "exec_count": st.exec_count}

    @app.post("/v1/chaos")
    async def set_chaos(req: ChaosReq):
        for k in ("fail_next", "network_error_rate", "auth_state"):
            v = getattr(req, k)
            if v is not None:
                st.chaos[k] = v
        return dict(st.chaos)

    @app.post("/v1/chaos/recycle")
    async def recycle():
        """The provider recycled every instance: state gone, leases invalid."""
        n = len(st.leases)
        for lease in st.leases.values():
            for proc in lease.procs.values():
                _kill_group(proc)
        st.leases.clear()
        return {"recycled": n}

    @app.post("/v1/leases")
    async def open_lease(body: Dict[str, str]):
        lease_id = f"vc_{uuid.uuid4().hex[:10]}"

        # Restored lease (layer-2 restore verb): rebuild machine state from a
        # snapshot into a fresh isolated workspace. Snapshots live on disk, so
        # they survive daemon restarts — the durable half of the pair.
        restore = (body or {}).get("restore") or ""
        if restore:
            snap_dir = base / "snapshots"
            tar_path = snap_dir / f"{restore}.tar.gz"
            meta_path = snap_dir / f"{restore}.json"
            if not tar_path.exists() or not meta_path.exists():
                return _err(404, "SNAPSHOT_NOT_FOUND", f"snapshot {restore!r} not found")
            workspace = base / "leases" / lease_id
            workspace.mkdir(parents=True, exist_ok=True)
            with tarfile.open(tar_path, "r:gz") as tf:
                tf.extractall(workspace, filter="data")
            lease = LeaseState(lease_id, workspace)
            meta = json.loads(meta_path.read_text())
            cwd = os.path.realpath(str(workspace / meta.get("cwd_rel", ".")))
            if os.path.isdir(cwd):
                lease.cwd = cwd
            st.leases[lease_id] = lease
            return {
                "lease_id": lease_id, "workspace": str(workspace),
                "mounted": False, "restored_from": restore,
            }

        # Mounted lease (step-6 finding #7, the data plane): when the client
        # names a workspace, the lease mounts it instead of creating an
        # isolated one — the compute side attaches the session's volume, so
        # the file surface (local Write/Edit) and the exec surface are one
        # directory. Omit it to study the split-brain form.
        mounted = False
        ws_hint = (body or {}).get("workspace") or ""
        if ws_hint and os.path.isabs(ws_hint) and os.path.isdir(ws_hint):
            workspace = Path(ws_hint)
            mounted = True
        else:
            workspace = base / "leases" / lease_id
            workspace.mkdir(parents=True, exist_ok=True)
        lease = LeaseState(lease_id, workspace)
        cwd_hint = (body or {}).get("cwd") or ""
        if (
            mounted and cwd_hint and os.path.isdir(cwd_hint)
            and os.path.realpath(cwd_hint).startswith(os.path.realpath(str(workspace)))
        ):
            lease.cwd = cwd_hint
        st.leases[lease_id] = lease
        return {"lease_id": lease_id, "workspace": str(workspace), "mounted": mounted}

    @app.post("/v1/leases/{lease_id}/snapshot")
    async def snapshot_lease(lease_id: str):
        """Layer-2 snapshot verb. Refuses a non-quiesced lease: the two-phase
        cut (runtime quiesce FIRST, then machine snapshot) is enforced on the
        provider side too — a snapshot taken under a running call would be a
        torn read of machine state."""
        lease = st.leases.get(lease_id)
        if lease is None:
            return _err(410, "LEASE_LOST", f"lease {lease_id} is gone")
        if lease.procs:
            return _err(
                409, "NOT_QUIESCED",
                f"calls still running: {list(lease.procs)}; quiesce first",
            )
        snap_id = f"snap_{uuid.uuid4().hex[:10]}"
        snap_dir = base / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(snap_dir / f"{snap_id}.tar.gz", "w:gz") as tf:
            tf.add(lease.workspace, arcname=".")
        ws_real = os.path.realpath(str(lease.workspace))
        cwd_real = os.path.realpath(lease.cwd)
        cwd_rel = (
            os.path.relpath(cwd_real, ws_real)
            if cwd_real == ws_real or cwd_real.startswith(ws_real + os.sep) else "."
        )
        (snap_dir / f"{snap_id}.json").write_text(json.dumps({
            "snapshot_id": snap_id,
            "cwd_rel": cwd_rel,
            "source_lease": lease_id,
            "created": time.time(),
        }))
        return {"snapshot_id": snap_id, "cwd_rel": cwd_rel}

    @app.get("/v1/leases/{lease_id}")
    async def lease_info(lease_id: str):
        lease = st.leases.get(lease_id)
        if lease is None:
            return _err(410, "LEASE_LOST", f"lease {lease_id} is gone")
        return {
            "lease_id": lease_id,
            "cwd": lease.cwd,
            "running": list(lease.procs.keys()),
        }

    @app.delete("/v1/leases/{lease_id}")
    async def close_lease(lease_id: str):
        lease = st.leases.pop(lease_id, None)
        if lease:
            for proc in lease.procs.values():
                _kill_group(proc)
        return {"closed": lease is not None}

    async def _put_chunk(q: asyncio.Queue, text: str) -> None:
        # Chunks are UI sugar: if the client is gone and the queue fills,
        # drop them. The result line is never dropped (see _put_result).
        try:
            q.put_nowait({"type": "chunk", "data": text})
        except asyncio.QueueFull:
            pass

    async def _put_result(q: asyncio.Queue, item: Dict) -> None:
        # Never block on a full queue (a vanished client stops draining):
        # evict chunks until the result fits — the result always lands.
        while True:
            try:
                q.put_nowait(item)
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass

    async def _owner(lease: LeaseState, req: ExecReq, q: asyncio.Queue) -> None:
        """Owns the process end to end, independent of the HTTP request's
        lifetime: a vanished client cannot orphan the child — the backstop
        timeout still reaps it, and /cancel can still kill the tracked group.
        """
        started = time.monotonic()
        deadline = started + req.timeout_s
        sandbox_prefix, verdict = _sandbox_argv(req.sandbox, str(lease.workspace))

        def _result(status: str, output: str, exit_code) -> Dict:
            return {
                "type": "result",
                "status": status,
                "output": output,
                "exit_code": exit_code,
                "cwd": lease.cwd,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "sandbox": verdict,
            }

        if sandbox_prefix is None:
            await _put_result(q, _result(
                "ERROR",
                "[vcompute] required sandbox unavailable (bwrap missing); "
                "execution failed closed",
                None,
            ))
            return

        script = (
            f"cd {lease.cwd!r} || exit 97\n"
            f"{req.command}\n"
            f"__vc_rc=$?\n"
            f"printf '\\n{SENTINEL}%s\\n' \"$PWD\"\n"
            f"exit $__vc_rc\n"
        )
        proc = await asyncio.create_subprocess_exec(
            *sandbox_prefix, "bash", "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        lease.procs[req.call_id] = proc

        raw = bytearray()
        pending = ""  # partial line held back from streaming
        status = "OK"
        try:
            assert proc.stdout is not None
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=remaining)
                if not chunk:
                    break
                raw += chunk
                pending += chunk.decode(errors="replace")
                lines = pending.split("\n")
                pending = lines.pop()
                streamable = "".join(
                    line + "\n" for line in lines if not line.startswith(SENTINEL)
                )
                if streamable:
                    await _put_chunk(q, streamable)
            if pending and not pending.startswith(SENTINEL):
                await _put_chunk(q, pending)
            await asyncio.wait_for(proc.wait(), timeout=max(1.0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            _kill_group(proc)
            await proc.wait()
            status = "TIMEOUT"
        finally:
            lease.procs.pop(req.call_id, None)

        if status == "TIMEOUT":
            await _put_result(q, _result(
                "TIMEOUT",
                f"[vcompute] killed after backstop timeout {req.timeout_s}s",
                None,
            ))
            return

        # Recover the persistent cwd from the sentinel line, then strip it.
        output = raw.decode(errors="replace")
        lines = output.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith(SENTINEL):
                lease.cwd = lines[i][len(SENTINEL):]
                del lines[i]
                if i > 0 and lines[i - 1] == "":
                    del lines[i - 1]
                break
        rc = proc.returncode
        await _put_result(q, _result("OK" if rc == 0 else "ERROR", "\n".join(lines), rc))

    @app.post("/v1/leases/{lease_id}/exec")
    async def exec_call(lease_id: str, req: ExecReq):
        # -- chaos gate (simulated provider-side failures) --
        if st.chaos["auth_state"] == "required":
            return _err(403, "AUTH_REQUIRED", "compute credential not connected")
        if st.chaos["auth_state"] == "expired_once":
            st.chaos["auth_state"] = "ok"
            return _err(401, "AUTH_EXPIRED", "compute token expired")
        if st.chaos["fail_next"] > 0:
            st.chaos["fail_next"] -= 1
            return _err(503, "NETWORK", "injected upstream failure")
        if st.chaos["network_error_rate"] > 0 and random.random() < st.chaos["network_error_rate"]:
            return _err(503, "NETWORK", "random upstream failure")

        lease = st.leases.get(lease_id)
        if lease is None:
            return _err(410, "LEASE_LOST", f"lease {lease_id} is gone (recycled?)")

        st.exec_count += 1
        q: asyncio.Queue = asyncio.Queue(maxsize=CHUNK_QUEUE_MAX)
        owner = asyncio.create_task(_owner(lease, req, q))
        st.owners.add(owner)
        owner.add_done_callback(st.owners.discard)

        async def gen():
            while True:
                item = await q.get()
                yield json.dumps(item) + "\n"
                if item["type"] == "result":
                    return

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/v1/leases/{lease_id}/calls/{call_id}/cancel")
    async def cancel_call(lease_id: str, call_id: str):
        lease = st.leases.get(lease_id)
        if lease is None:
            return {"killed": False, "reason": "lease gone"}
        proc = lease.procs.get(call_id)
        if proc is None:
            return {"killed": False, "reason": "not running"}
        killed = _kill_group(proc)
        return {"killed": killed}

    return app


def main() -> None:
    import uvicorn

    port = int(os.environ.get("VCOMPUTE_PORT", DEFAULT_PORT))
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
