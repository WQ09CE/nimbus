#!/usr/bin/env python3
"""Full-LLM consistency-cut vertical: PAUSE is the quiesce trigger.

Real model (pi-ai sidecar) drives a two-step Bash task on an ISOLATED
vcompute lease (true-cloud form, no workspace mount). The run pauses at the
seam after step 1 — SessionManagerV2 snapshots the lease and persists the
layer-3 binding. The vcompute daemon is then killed and restarted (a real
recycle: leases evaporate, snapshots survive). Resume restores the lease
from the bound snapshot; the model's step 2 reads the pre-pause state from
the restored machine.

Requires: NIMBUS_PI_SIDECAR_URL (running sidecar), Node creds per
pi_codex_smoke.sh. Manages its own vcompute daemon.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

VC_PORT = int(os.environ.get("VC_CUT_PORT", "8793"))
VC_URL = f"http://127.0.0.1:{VC_PORT}"
os.environ["NIMBUS_VCOMPUTE_URL"] = VC_URL
os.environ["NIMBUS_VCOMPUTE_MOUNT"] = "0"  # true-cloud isolated form
os.environ.setdefault("NIMBUS_SANDBOX_MODE", "best_effort")

from nimbus.adapters.llm_factory import create_llm_client  # noqa: E402
from nimbus.core.storage import SessionStorage  # noqa: E402
from nimbus.server.models import PermissionDecision  # noqa: E402
from nimbus.server.permission import PermissionManager  # noqa: E402
from nimbus.server.session import SessionManagerV2  # noqa: E402
from nimbus.server.sse import SSEHub  # noqa: E402

MODEL = f"pi-codex/{os.environ.get('PI_CODEX_MODEL', 'gpt-5.6-sol')}"
SESSION_ID = "sess_vc_pause_cut"
MARKER = "cut-proof-777"

PROMPT = (
    "Do exactly two Bash steps, one at a time. "
    f"Step 1: run `echo {MARKER} > state.txt && cat state.txt`. "
    "Step 2 (as a separate Bash call): run `cat state.txt` and then reply "
    "with the file content followed by the word CUT_RESUME_OK."
)


def start_daemon(root: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [".venv/bin/python", "-m", "nimbus.infra.vcompute"],
        env={**os.environ, "VCOMPUTE_PORT": str(VC_PORT), "VCOMPUTE_ROOT": root},
        cwd=str(Path(__file__).resolve().parents[2]),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        try:
            httpx.get(f"{VC_URL}/v1/health", timeout=0.5)
            return proc
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("vcompute daemon did not start")


def kill_daemon(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


async def close_manager(manager: SessionManagerV2) -> None:
    publishers = []
    for agent in list(manager._sessions.values()):
        task = getattr(agent, "_pub_task", None)
        if task is not None:
            task.cancel()
            publishers.append(task)
    if publishers:
        await asyncio.gather(*publishers, return_exceptions=True)
    await manager.close_all()


async def new_manager(storage: SessionStorage) -> tuple[SessionManagerV2, PermissionManager]:
    permissions = PermissionManager()
    manager = SessionManagerV2(SSEHub(), permissions)
    manager._storage = storage
    manager._shared_llm_client = await create_llm_client(MODEL, thinking_effort="low")
    return manager, permissions


async def wait_for_permission(
    permissions: PermissionManager, task: asyncio.Task, timeout: float = 90.0,
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        pending = permissions.get_pending_requests(SESSION_ID)
        if pending:
            return pending[0]
        if task.done():
            raise AssertionError(f"run ended before permission request: {task.exception()}")
        await asyncio.sleep(0.01)
    raise TimeoutError("timed out waiting for the Bash permission request")


async def main() -> None:
    managers: list[SessionManagerV2] = []
    with tempfile.TemporaryDirectory(prefix="nimbus-vc-cut-") as tmp:
        root = Path(tmp)
        (root / "workspace").mkdir()
        vc_root = str(root / "vcroot")
        daemon = start_daemon(vc_root)
        try:
            storage = SessionStorage(str(root / "sessions"))
            storage.save_session(
                session_id=SESSION_ID, status="active", messages=[], vcpu_state={},
                llm_config={}, metadata={
                    "name": "vc pause cut vertical",
                    "workspace_path": str(root / "workspace"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "config_overrides": {"skills": [], "plugins": []},
                },
            )

            # ── Phase 1: real model starts the task; pause lands at the seam
            # after the approved step-1 Bash; the PAUSED branch snapshots. ──
            manager1, permissions1 = await new_manager(storage)
            managers.append(manager1)
            manager1._sse_hub.prepare_session(SESSION_ID)
            run1 = asyncio.create_task(manager1.stream_chat(SESSION_ID, PROMPT))
            manager1.register_task(SESSION_ID, run1)

            request = await wait_for_permission(permissions1, run1)
            assert request["tool"] == "Bash", request
            pause = await manager1.pause_session(SESSION_ID)
            assert pause["status"] == "pause_requested", pause
            await permissions1.resolve_permission(
                request["request_id"], PermissionDecision.ALLOW_ONCE,
            )
            await asyncio.wait_for(run1, timeout=120.0)
            manager1.unregister_task(SESSION_ID)

            paused = storage.load_session(SESSION_ID)
            assert paused["status"] == "paused", paused["status"]
            binding = paused["metadata"].get("sandbox_binding")
            assert binding and binding["snapshot_id"].startswith("snap_"), (
                f"no layer-3 binding at the pause seam: {binding!r}"
            )
            print(f"[phase1] paused at seam; binding={binding['snapshot_id']} "
                  f"lease={binding['lease_id']}")
            await close_manager(manager1)
            managers.remove(manager1)

            # ── Recycle: daemon killed and restarted; leases evaporate,
            # snapshots (and the binding in session metadata) survive. ──
            kill_daemon(daemon)
            daemon = start_daemon(vc_root)
            print("[recycle] vcompute daemon killed + restarted")

            # ── Phase 2: fresh manager over the same storage; resume
            # restores the lease from the bound snapshot, then the model's
            # step 2 reads pre-pause machine state. ──
            manager2, permissions2 = await new_manager(storage)
            managers.append(manager2)
            resumed = await manager2.resume_session(SESSION_ID)
            assert resumed["status"] == "resuming", resumed
            run2 = manager2._active_tasks[SESSION_ID]
            request2 = await wait_for_permission(permissions2, run2)
            assert request2["tool"] == "Bash", request2
            await permissions2.resolve_permission(
                request2["request_id"], PermissionDecision.ALLOW_ONCE,
            )
            await asyncio.wait_for(run2, timeout=120.0)

            final = storage.load_session(SESSION_ID)
            assert final["status"] == "completed", final["status"]
            assistants = [m for m in final["messages"] if m.get("role") == "assistant"]
            tail = " ".join((m.get("content") or "") for m in assistants)
            assert MARKER in tail and "CUT_RESUME_OK" in tail, tail

            trace = await manager2.get_session_log(SESSION_ID)
            assert trace["invariant_violations"] == [], trace["invariant_violations"]
            assert trace["stats"]["turn_end_reasons"] == ["paused", "completed"]
            results = [e for e in trace["events"] if e["type"] == "action/result"]
            assert MARKER in results[-1]["data"]["output_preview"], results[-1]["data"]
            print(f"[phase2] resumed; step-2 Bash read {MARKER!r} from the "
                  f"RESTORED lease; final: {assistants[-1]['content']!r}")
            print("PASS: full-LLM PAUSE cut — quiesce(PAUSE) → snapshot → bind → "
                  "recycle → restore → continue, no split-brain")
        finally:
            for manager in reversed(managers):
                await close_manager(manager)
            kill_daemon(daemon)


if __name__ == "__main__":
    asyncio.run(main())
