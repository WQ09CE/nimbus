"""vcompute end-to-end: the Gate contract exercised against a real channel.

Everything here goes through the full path — KernelGate dispatch loop →
VComputeBackend (httpx over ASGI transport) → the vcompute FastAPI daemon →
real subprocesses. The chaos endpoints make every row of the fault-routing
table reachable deterministically.
"""

import asyncio

import httpx
import pytest

from nimbus.core.backend import LocalBackend, RoutingBackend
from nimbus.core.backends.vcompute import VComputeBackend
from nimbus.core.gate import KernelGate
from nimbus.core.protocol import ActionIR
from nimbus.infra.vcompute import create_app


def _bash(command, aid="c1"):
    return ActionIR(id=aid, kind="TOOL_CALL", name="Bash", args={"command": command})


class Harness:
    def __init__(self, tmp_path):
        self.app = create_app(root=str(tmp_path))
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://vc"
        )
        self.backend = VComputeBackend(session_id="sess_vc", client=self.client)
        self.events = []
        self.gate = KernelGate(
            pid="p1",
            backend=self.backend,
            event_callback=self.events.append,
            session_id="sess_vc",
        )
        self.gate.set_audit_sink(lambda t, d: None)

    async def chaos(self, **kw):
        resp = await self.client.post("/v1/chaos", json=kw)
        assert resp.status_code == 200

    async def lease_info(self):
        assert self.backend._lease is not None
        resp = await self.client.get(f"/v1/leases/{self.backend._lease.lease_id}")
        return resp.status_code, resp.json()


@pytest.fixture
def vc(tmp_path):
    return Harness(tmp_path)


class TestVComputeThroughGate:
    @pytest.mark.asyncio
    async def test_exec_roundtrip(self, vc):
        result = await vc.gate.syscall_tool(_bash("echo hi from vcompute"))
        assert result.status == "OK"
        assert "hi from vcompute" in result.output
        assert result.ui_detail["backend"] == "vcompute"
        assert result.ui_detail["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_nonzero_exit_is_a_tool_error_not_a_channel_fault(self, vc):
        result = await vc.gate.syscall_tool(_bash("exit 3"))
        assert result.status == "ERROR"
        # The channel worked; the TOOL failed. No BACKEND fault involved.
        assert result.fault is not None and result.fault.domain == "TOOL"
        assert result.ui_detail["exit_code"] == 3

    @pytest.mark.asyncio
    async def test_lease_affinity_cwd_persists_across_calls(self, vc):
        r1 = await vc.gate.syscall_tool(_bash("mkdir -p subdir && cd subdir && pwd", "c1"))
        assert r1.status == "OK"
        r2 = await vc.gate.syscall_tool(_bash("pwd", "c2"))
        assert r2.status == "OK"
        assert r2.output.strip().endswith("/subdir")
        assert r2.ui_detail["lease_id"] == r1.ui_detail["lease_id"]

    @pytest.mark.asyncio
    async def test_network_blip_is_retried_silently(self, vc):
        await vc.chaos(fail_next=1)
        result = await vc.gate.syscall_tool(_bash("echo survived"))
        assert result.status == "OK"
        assert "survived" in result.output
        health = (await vc.client.get("/v1/health")).json()
        assert health["exec_count"] == 1  # one real exec; the 503 never ran anything

    @pytest.mark.asyncio
    async def test_recycle_surfaces_lease_lost_then_self_heals(self, vc):
        r1 = await vc.gate.syscall_tool(_bash("echo warm", "c1"))
        old_lease = r1.ui_detail["lease_id"]
        await vc.client.post("/v1/chaos/recycle")

        r2 = await vc.gate.syscall_tool(_bash("echo after-recycle", "c2"))
        assert r2.status == "ERROR"
        assert r2.fault.code == "LEASE_LOST"
        assert "state" in r2.output  # the model is told, never silent

        r3 = await vc.gate.syscall_tool(_bash("echo healed", "c3"))
        assert r3.status == "OK"
        assert r3.ui_detail["lease_id"] != old_lease  # fresh lease, next call

    @pytest.mark.asyncio
    async def test_auth_required_routes_to_user(self, vc):
        await vc.chaos(auth_state="required")
        result = await vc.gate.syscall_tool(_bash("echo nope"))
        assert result.status == "ERROR"
        assert result.fault.code == "AUTH_REQUIRED"
        assert any(e.type == "AUTH_REQUESTED" for e in vc.events)

    @pytest.mark.asyncio
    async def test_auth_expired_heals_via_silent_refresh(self, vc):
        await vc.chaos(auth_state="expired_once")
        result = await vc.gate.syscall_tool(_bash("echo refreshed"))
        assert result.status == "OK"
        assert "refreshed" in result.output
        assert not any(e.type == "AUTH_REQUESTED" for e in vc.events)

    @pytest.mark.asyncio
    async def test_timeout_leaves_no_orphan_process(self, vc):
        # Warm the lease so we can inspect it afterwards.
        await vc.gate.syscall_tool(_bash("echo warm", "c0"))
        result = await vc.gate.syscall_tool(_bash("sleep 30", "c_slow"), timeout=0.4)
        assert result.status == "TIMEOUT"
        await asyncio.sleep(0.2)  # let the kill/reap settle
        status_code, info = await vc.lease_info()
        assert status_code == 200
        assert info["running"] == []  # the remote process group is dead


class TestStreamingLeg:
    @pytest.mark.asyncio
    async def test_remote_output_streams_live(self, vc, tmp_path):
        # Finding #3 fixed: chunks reach the Gate's streaming wrapper while
        # the remote command is still running.
        chunks = []
        harness = Harness(tmp_path)
        gate = KernelGate(
            pid="p3",
            backend=harness.backend,
            on_tool_output=lambda tool, chunk: chunks.append(chunk),
            session_id="sess_vc",
        )
        result = await gate.syscall_tool(_bash("echo one; sleep 0.25; echo two"))
        assert result.status == "OK"
        streamed = "".join(chunks)
        assert "one" in streamed and "two" in streamed
        assert "__VC_CWD__" not in streamed  # the sentinel never leaks to the UI
        assert len(chunks) >= 2  # arrived incrementally, not as one blob

class TestSandboxTravel:
    @pytest.mark.asyncio
    @pytest.mark.skipif(
        __import__("shutil").which("bwrap") is None, reason="bwrap not installed"
    )
    async def test_grant_travels_and_confines_remote_exec(self, vc, tmp_path):
        # Finding #4 fixed: an execute-class grant rides the exec request and
        # the daemon enforces it with bubblewrap.
        harness = Harness(tmp_path)
        events = []
        gate = KernelGate(
            pid="p4",
            backend=harness.backend,
            event_callback=events.append,
            session_id="sess_vc",
            sandbox_mode="best_effort",  # gate's inherited-capability branch
        )
        result = await gate.syscall_tool(_bash(
            "touch /usr/vc_probe 2>/dev/null && echo ESCAPED || echo CONFINED; "
            "echo data > ws.txt && cat ws.txt"
        ))
        assert result.status == "OK"
        assert "CONFINED" in result.output and "ESCAPED" not in result.output
        assert "data" in result.output  # workspace stays writable
        detail = result.ui_detail["sandbox_details"]
        assert detail["state"] == "active" and detail["backend"] == "bubblewrap"
        assert any(e.type == "SANDBOX_STATUS" for e in events)


class TestWorkspaceMount:
    @pytest.mark.asyncio
    async def test_mounted_lease_unifies_file_and_exec_surface(self, tmp_path):
        # Finding #7 fixed: with a path_context, the lease mounts the session
        # workspace — local Write and remote Bash see the same directory.
        from nimbus.core.path_context import AgentPathContext

        ws = tmp_path / "agent_ws"
        ws.mkdir()
        (ws / "fib.py").write_text("print('unified')\n")
        harness = Harness(tmp_path)
        gate = KernelGate(
            pid="p5",
            backend=harness.backend,
            path_context=AgentPathContext(
                workspace_root=str(ws), target_root=str(ws), execution_cwd=str(ws),
            ),
            session_id="sess_vc",
        )
        r = await gate.syscall_tool(_bash("python3 fib.py"))
        assert r.status == "OK"
        assert "unified" in r.output

        r2 = await gate.syscall_tool(_bash("echo from-remote > out.txt", "c2"))
        assert r2.status == "OK"
        # The exec surface's writes are the file surface's files.
        assert (ws / "out.txt").read_text().strip() == "from-remote"


class TestSnapshotRestore:
    @pytest.mark.asyncio
    async def test_snapshot_refuses_non_quiesced_lease(self, vc):
        # The two-phase cut is enforced provider-side: no snapshot under a
        # running call — that would be a torn read of machine state.
        await vc.gate.syscall_tool(_bash("echo warm", "c0"))
        task = asyncio.create_task(vc.gate.syscall_tool(_bash("sleep 3", "c_run")))
        for _ in range(50):
            await asyncio.sleep(0.05)
            _, info = await vc.lease_info()
            if info["running"]:
                break
        assert info["running"] == ["c_run"]
        lease_id = vc.backend._lease.lease_id
        resp = await vc.client.post(f"/v1/leases/{lease_id}/snapshot")
        assert resp.status_code == 409
        assert resp.json()["code"] == "NOT_QUIESCED"
        await vc.client.post(f"/v1/leases/{lease_id}/calls/c_run/cancel")
        await task

    @pytest.mark.asyncio
    async def test_cut_survives_daemon_restart(self, vc, tmp_path):
        # Build machine state: files + cwd depth.
        r = await vc.gate.syscall_tool(_bash(
            "echo 'requests==2.31' > requirements.txt && mkdir -p src && "
            "echo 'print(1)' > src/app.py && cd src", "c1",
        ))
        assert r.status == "OK"
        snap_id = await vc.backend.snapshot_lease()

        # A NEW app over the SAME root = daemon restart: leases are gone
        # (in-memory), snapshots persist (disk).
        reborn = Harness(tmp_path)
        lease = await reborn.backend.restore_lease(snap_id)
        assert lease.lease_id != vc.backend._lease.lease_id
        r2 = await reborn.gate.syscall_tool(_bash("pwd && cat ../requirements.txt", "c2"))
        assert r2.status == "OK"
        assert r2.output.strip().splitlines()[0].endswith("/src")  # cwd restored
        assert "requests==2.31" in r2.output                       # files restored

    @pytest.mark.asyncio
    async def test_split_brain_when_only_one_side_restores(self, vc, tmp_path):
        # Counter-example: session "remembers" the file, but a fresh lease
        # (no restore) has empty machine state — the brain splits.
        r = await vc.gate.syscall_tool(_bash("echo data > important.txt", "c1"))
        assert r.status == "OK"
        reborn = Harness(tmp_path)  # restart, NO restore
        r2 = await reborn.gate.syscall_tool(_bash("cat important.txt", "c2"))
        assert r2.status == "ERROR"
        assert r2.ui_detail["exit_code"] != 0


class TestDirtyTracking:
    @pytest.mark.asyncio
    async def test_side_effect_lifecycle(self, vc):
        # fresh backend: nothing dispatched, nothing snapshotted
        assert vc.backend.dirty is False
        assert vc.backend.last_snapshot_id is None

        r = await vc.gate.syscall_tool(_bash("echo x > f.txt"))
        assert r.status == "OK"
        assert vc.backend.dirty is True  # execute-class dispatch happened

        snap = await vc.backend.snapshot_lease()
        assert vc.backend.dirty is False  # snapshot re-baselines
        assert vc.backend.last_snapshot_id == snap

        r = await vc.gate.syscall_tool(_bash("echo y >> f.txt", "c2"))
        assert vc.backend.dirty is True  # diverged again

    @pytest.mark.asyncio
    async def test_restore_rebaselines_clean(self, vc, tmp_path):
        await vc.gate.syscall_tool(_bash("echo z > f.txt"))
        snap = await vc.backend.snapshot_lease()
        reborn = Harness(tmp_path)
        await reborn.backend.restore_lease(snap)
        # machine state == snapshot content, by definition
        assert reborn.backend.dirty is False
        assert reborn.backend.last_snapshot_id == snap


class TestRoutingBackend:
    @pytest.mark.asyncio
    async def test_tools_split_between_backends(self, vc, tmp_path):
        local_seen = []

        async def local_executor(name, args):
            local_seen.append(name)
            return "local says hi"

        router = RoutingBackend(
            default=LocalBackend(local_executor),
            routes={"Bash": vc.backend},
        )
        gate = KernelGate(pid="p2", backend=router, session_id="sess_vc")

        remote = await gate.syscall_tool(_bash("echo remote"))
        assert remote.ui_detail["backend"] == "vcompute"

        local = await gate.syscall_tool(
            ActionIR(id="c9", kind="TOOL_CALL", name="Read", args={"file_path": "x"})
        )
        assert local.output == "local says hi"
        assert local_seen == ["Read"]
