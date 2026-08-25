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
