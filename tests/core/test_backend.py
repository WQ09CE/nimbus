"""ExecutionBackend contract tests — one test per fault-routing table row.

Design: docs/design/toolcall-gate-execution-backend.md §4. The invariant under
test everywhere: one dispatched call yields exactly one ToolResult, and a
BackendFault never escapes the Gate.
"""

import asyncio

import pytest

from nimbus.core.backend import BackendFault, LocalBackend, PreparedCall
from nimbus.core.gate import BACKEND_RETRY_MAX, KernelGate
from nimbus.core.protocol import ActionIR, ToolTraits
from nimbus.core.tools.registry import DEFAULT_TRAITS


def _action(tool="Bash", args=None, aid="call_1"):
    return ActionIR(id=aid, kind="TOOL_CALL", name=tool, args=args or {"command": "echo ok"})


def _call(tool="Bash", traits=None, grant=None, **kw):
    return PreparedCall(
        call_id="call_1",
        tool=tool,
        args=kw.pop("args", {"command": "echo ok"}),
        traits=traits or ToolTraits(side_effects="execute"),
        deadline_s=None,
        **kw,
    ) if grant is None else PreparedCall(
        call_id="call_1",
        tool=tool,
        args=kw.pop("args", {"command": "echo ok"}),
        traits=traits or ToolTraits(side_effects="execute"),
        deadline_s=None,
        sandbox_grant=grant,
        **kw,
    )


class FakeBackend:
    """Scripted backend: pops one outcome per run() call."""

    backend_id = "fake"

    def __init__(self, outcomes, run_delay=0.0):
        self.outcomes = list(outcomes)
        self.run_calls = 0
        self.cancel_calls = []
        self.run_delay = run_delay

    def advertise(self):
        return []

    def catalog_version(self):
        return 0

    async def open_lease(self, session_id):
        return None

    async def close_lease(self, lease):
        return None

    async def run(self, call, lease=None):
        self.run_calls += 1
        self.last_call = call
        if self.run_delay:
            await asyncio.sleep(self.run_delay)
        return self.outcomes.pop(0)

    async def cancel(self, call_id, lease=None):
        self.cancel_calls.append(call_id)


def _gate(backend, **kw):
    events = []
    audits = []
    gate = KernelGate(
        pid="p1",
        backend=backend,
        event_callback=events.append,
        session_id="sess_t",
        **kw,
    )
    gate.set_audit_sink(lambda t, d: audits.append((t, d)))
    return gate, events, audits


# =============================================================================
# Routing table rows (design doc §4)
# =============================================================================


class TestBackendFaultRouting:
    @pytest.mark.asyncio
    async def test_raw_payload_passes_through_unchanged(self):
        gate, _, _ = _gate(FakeBackend(["hello"]))
        result = await gate.syscall_tool(_action())
        assert result.status == "OK"
        assert result.output == "hello"

    @pytest.mark.asyncio
    async def test_retryable_fault_then_success_is_invisible_to_model(self):
        backend = FakeBackend([
            BackendFault(code="NETWORK", message="blip", retryable=True),
            "recovered",
        ])
        gate, _, audits = _gate(backend)
        result = await gate.syscall_tool(_action())
        assert result.status == "OK"
        assert result.output == "recovered"
        assert backend.run_calls == 2
        assert any(t == "backend/retry" for t, _ in audits)

    @pytest.mark.asyncio
    async def test_retryable_exhaustion_degrades_to_model_visible_error(self):
        faults = [BackendFault(code="NETWORK", message="down", retryable=True)] * (
            BACKEND_RETRY_MAX + 1
        )
        backend = FakeBackend(faults)
        gate, _, _ = _gate(backend)
        result = await gate.syscall_tool(_action())
        assert result.status == "ERROR"
        assert result.fault is not None
        assert result.fault.domain == "BACKEND"
        assert result.fault.code == "NETWORK"
        assert backend.run_calls == BACKEND_RETRY_MAX + 1

    @pytest.mark.asyncio
    async def test_non_retryable_fault_fails_immediately(self):
        backend = FakeBackend([
            BackendFault(code="BACKEND_UNAVAILABLE", message="gone", retryable=False),
        ])
        gate, _, _ = _gate(backend)
        result = await gate.syscall_tool(_action())
        assert result.status == "ERROR"
        assert result.fault.code == "BACKEND_UNAVAILABLE"
        assert backend.run_calls == 1

    @pytest.mark.asyncio
    async def test_lease_lost_tells_the_model_state_is_gone(self):
        backend = FakeBackend([
            BackendFault(code="LEASE_LOST", message="sandbox recycled", retryable=False),
        ])
        gate, _, _ = _gate(backend)
        result = await gate.syscall_tool(_action())
        assert result.status == "ERROR"
        assert result.fault.code == "LEASE_LOST"
        assert "state" in result.output

    @pytest.mark.asyncio
    async def test_auth_required_routes_to_user_not_model(self):
        backend = FakeBackend([
            BackendFault(code="AUTH_REQUIRED", message="connect Notion", retryable=False),
        ])
        gate, events, audits = _gate(backend)
        result = await gate.syscall_tool(_action())
        assert result.status == "ERROR"
        assert result.fault.code == "AUTH_REQUIRED"
        assert backend.run_calls == 1  # never auto-retried
        assert any(e.type == "AUTH_REQUESTED" for e in events)
        assert any(t == "backend/auth_required" for t, _ in audits)

    @pytest.mark.asyncio
    async def test_auth_expired_gets_one_silent_refresh_then_degrades(self):
        backend = FakeBackend([
            BackendFault(code="AUTH_EXPIRED", message="token expired", retryable=False),
            BackendFault(code="AUTH_EXPIRED", message="token expired", retryable=False),
        ])
        gate, events, _ = _gate(backend)
        result = await gate.syscall_tool(_action())
        assert backend.run_calls == 2  # exactly one silent refresh attempt
        assert result.status == "ERROR"
        assert result.fault.code == "AUTH_REQUIRED"  # degraded to the user flow
        assert any(e.type == "AUTH_REQUESTED" for e in events)

    @pytest.mark.asyncio
    async def test_timeout_cancels_backend_so_work_is_never_orphaned(self):
        backend = FakeBackend(["late"], run_delay=1.0)
        gate, _, _ = _gate(backend)
        result = await gate.syscall_tool(_action(), timeout=0.05)
        assert result.status == "TIMEOUT"
        assert backend.cancel_calls == ["call_1"]

    @pytest.mark.asyncio
    async def test_session_identity_rides_the_call(self):
        # Finding #2: a backend managing its own leases must know for whom.
        backend = FakeBackend(["ok"])
        gate, _, _ = _gate(backend)
        await gate.syscall_tool(_action())
        assert backend.last_call.session_id == "sess_t"

    @pytest.mark.asyncio
    async def test_retry_budget_zero_disables_retry(self):
        # Finding #6: the channel self-describes its retry-worthiness.
        backend = FakeBackend([
            BackendFault(code="NETWORK", message="x", retryable=True, retry_budget=0),
        ])
        gate, _, _ = _gate(backend)
        result = await gate.syscall_tool(_action())
        assert result.status == "ERROR"
        assert backend.run_calls == 1

    @pytest.mark.asyncio
    async def test_retry_budget_can_exceed_the_default(self):
        faults = [
            BackendFault(code="NETWORK", message="x", retryable=True, retry_budget=4)
        ] * 4
        backend = FakeBackend(faults + ["recovered"])
        gate, _, _ = _gate(backend)
        result = await gate.syscall_tool(_action())
        assert result.status == "OK"
        assert backend.run_calls == 5

    @pytest.mark.asyncio
    async def test_fault_never_escapes_as_exception(self):
        # Pairing invariant: whatever the channel does, syscall_tool returns
        # exactly one ToolResult.
        for fault in (
            BackendFault(code="NETWORK", message="x", retryable=True),
            BackendFault(code="WEIRD_NEW_CODE", message="x", retryable=False),
        ):
            gate, _, _ = _gate(FakeBackend([fault] * (BACKEND_RETRY_MAX + 1)))
            result = await gate.syscall_tool(_action())
            assert result.status == "ERROR"
            assert result.fault.domain == "BACKEND"


# =============================================================================
# LocalBackend injection rules
# =============================================================================


class TestLocalBackend:
    @pytest.mark.asyncio
    async def test_execute_traits_receive_sandbox_grant(self):
        seen = {}

        async def executor(name, args):
            seen.update(args)
            return "ok"

        backend = LocalBackend(executor, abort_event=asyncio.Event())
        grant = {"mode": "bwrap", "state": "active"}
        await backend.run(_call(traits=ToolTraits(side_effects="execute"), grant=grant))
        assert seen["_sandbox_policy"] == grant
        assert "_abort_event" in seen

    @pytest.mark.asyncio
    async def test_write_traits_never_receive_sandbox_grant(self):
        # A strict-signature tool must not get surprise kwargs.
        seen = {}

        async def executor(name, args):
            seen.update(args)
            return "ok"

        backend = LocalBackend(executor)
        grant = {"mode": "path_scope", "state": "active"}
        await backend.run(_call(tool="Write", traits=ToolTraits(side_effects="write"), grant=grant))
        assert "_sandbox_policy" not in seen

    @pytest.mark.asyncio
    async def test_spawn_agent_inherits_parent_identity(self):
        seen = {}

        async def executor(name, args):
            seen.update(args)
            return "ok"

        backend = LocalBackend(executor, parent_model="m1", parent_base_url="http://b")
        await backend.run(_call(tool="spawn_agent", traits=ToolTraits(side_effects="write"),
                                args={"goal": "g"}))
        assert seen["_parent_model"] == "m1"
        assert seen["_parent_base_url"] == "http://b"

    @pytest.mark.asyncio
    async def test_cancel_is_a_local_noop(self):
        backend = LocalBackend(lambda n, a: None)
        assert await backend.cancel("c1") is None


# =============================================================================
# Traits resolution
# =============================================================================


class TestTraitsResolution:
    def test_builtins_declare_their_class(self):
        gate, _, _ = _gate(FakeBackend([]))
        assert gate._traits_for("Bash").side_effects == "execute"
        assert gate._traits_for("Read").side_effects == "read"
        assert gate._traits_for("Write").side_effects == "write"
        assert gate._traits_for("submit_result").side_effects == "none"

    def test_unknown_tool_gets_distrusted_default(self):
        gate, _, _ = _gate(FakeBackend([]))
        assert gate._traits_for("some_plugin_tool") == DEFAULT_TRAITS
        assert DEFAULT_TRAITS.side_effects == "write"

    def test_explicit_lookup_wins_over_builtins(self):
        gate, _, _ = _gate(
            FakeBackend([]),
            traits_lookup=lambda name: ToolTraits(side_effects="none"),
        )
        assert gate._traits_for("Bash").side_effects == "none"
