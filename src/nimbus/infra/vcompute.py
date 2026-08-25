"""vcompute — a local virtual compute service for exercising ExecutionBackend.

One process plays the role of a compute provider: leases are workspaces with a
persistent cwd, execs run in the lease's own process group and are cancellable,
and /v1/chaos injects exactly the fault classes the Gate routing table must
survive (network blips, lease recycling, auth required/expired).

This is validation infra, not a product: it binds 127.0.0.1 only (it executes
arbitrary shell commands) and keeps no state across restarts — a restart IS a
lease-recycle event, which is the honest cloud behavior.

Run: python -m nimbus.infra.vcompute  (VCOMPUTE_PORT, VCOMPUTE_ROOT to override)
"""

import asyncio
import os
import random
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

SENTINEL = "__VC_CWD__"
DEFAULT_PORT = 8788


class ExecReq(BaseModel):
    call_id: str
    command: str
    timeout_s: float = 60.0


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


def create_app(root: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="nimbus-vcompute")
    base = Path(
        root or os.environ.get("VCOMPUTE_ROOT")
        or Path.home() / ".nimbus" / "vcompute"
    )
    st = app.state
    st.leases = {}
    st.chaos = {"fail_next": 0, "network_error_rate": 0.0, "auth_state": "ok"}
    st.exec_count = 0  # successful execs actually started (observability for tests)

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
        workspace = base / "leases" / lease_id
        workspace.mkdir(parents=True, exist_ok=True)
        st.leases[lease_id] = LeaseState(lease_id, workspace)
        return {"lease_id": lease_id, "workspace": str(workspace)}

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

    async def _run_and_reap(lease: LeaseState, req: ExecReq) -> Dict:
        """Owns the process end to end; shielded so a dropped HTTP client
        cannot orphan the child — the backstop timeout still reaps it."""
        script = (
            f"cd {lease.cwd!r} || exit 97\n"
            f"{req.command}\n"
            f"__vc_rc=$?\n"
            f"printf '\\n{SENTINEL}%s\\n' \"$PWD\"\n"
            f"exit $__vc_rc\n"
        )
        started = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        lease.procs[req.call_id] = proc
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=req.timeout_s)
            output = stdout.decode(errors="replace")
        except asyncio.TimeoutError:
            _kill_group(proc)
            await proc.wait()
            return {
                "status": "TIMEOUT",
                "output": f"[vcompute] killed after backstop timeout {req.timeout_s}s",
                "exit_code": None,
                "cwd": lease.cwd,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
        finally:
            lease.procs.pop(req.call_id, None)

        # Recover the persistent cwd from the sentinel line, then strip it.
        lines = output.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith(SENTINEL):
                lease.cwd = lines[i][len(SENTINEL):]
                del lines[i]
                if i > 0 and lines[i - 1] == "":
                    del lines[i - 1]
                break
        rc = proc.returncode
        return {
            "status": "OK" if rc == 0 else "ERROR",
            "output": "\n".join(lines),
            "exit_code": rc,
            "cwd": lease.cwd,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

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
        # Shield: a cancelled/disconnected HTTP request must not orphan the
        # child — the reap task keeps running to its backstop timeout, and the
        # explicit /cancel endpoint can still kill the tracked process group.
        return await asyncio.shield(asyncio.create_task(_run_and_reap(lease, req)))

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
