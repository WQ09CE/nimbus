"""Native terminal result, snapshot and turn/end must agree (no provider calls)."""

import json

import pytest

from nimbus.core.agent import AgentConfig, AgentOS
from nimbus.core.loop import RuntimeLoop
from nimbus.core.mmu import MMU
from nimbus.core.path_context import AgentPathContext
from nimbus.core.protocol import Fault, StepResult, ToolResult
from nimbus.core.session_log import check_invariants
from nimbus.core.storage import SessionStorage
from nimbus.core.tools.registry import ToolRegistry


class TerminalVCPU:
    iteration = 0

    def __init__(self, result):
        self.result = result

    async def step(self):
        return StepResult(is_final=True, final_result=self.result)


def assert_projection(loop, root, snapshot_status, reason):
    dump = json.loads((root / "probe.json").read_text())
    assert dump["status"] == snapshot_status
    ends = [e for e in loop.session_log.events if e.type == "turn/end"]
    assert ends[-1].data["reason"]["kind"] == reason
    assert check_invariants(loop.session_log.events) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,snapshot,reason",
    [
        ("OK", "completed", "completed"),
        ("ERROR", "error", "error"),
        ("TIMEOUT", "error", "error"),
        ("CANCELLED", "suspended", "aborted"),
        ("PAUSED", "paused", "paused"),
        ("SKIPPED", "error", "error"),
    ],
)
async def test_terminal_result_projection(tmp_path, status, snapshot, reason):
    result = ToolResult(status=status, output="synthetic", is_final=True)
    loop = RuntimeLoop(
        TerminalVCPU(result), MMU(), session_id="probe", storage=SessionStorage(str(tmp_path))
    )
    assert (await loop.run()).status == status
    assert_projection(loop, tmp_path, snapshot, reason)


@pytest.mark.asyncio
async def test_missing_final_result_is_error(tmp_path):
    loop = RuntimeLoop(
        TerminalVCPU(None), MMU(), session_id="probe", storage=SessionStorage(str(tmp_path))
    )
    assert (await loop.run()).status == "ERROR"
    assert_projection(loop, tmp_path, "error", "error")


@pytest.mark.asyncio
async def test_failed_turn_then_explicit_followup_has_distinct_end_reasons(tmp_path):
    class VCPU:
        iteration = 0

        async def step(self):
            self.iteration += 1
            return StepResult(
                is_final=True,
                final_result=ToolResult(
                    status="ERROR" if self.iteration == 1 else "OK", output="synthetic"
                ),
            )

    loop = RuntimeLoop(VCPU(), MMU(), session_id="probe", storage=SessionStorage(str(tmp_path)))
    loop.followup_queue.follow_up("separate explicit followup")
    assert (await loop.run()).status == "OK"
    ends = [e.data["reason"]["kind"] for e in loop.session_log.events if e.type == "turn/end"]
    assert ends == ["error", "completed"]
    assert_projection(loop, tmp_path, "completed", "completed")


@pytest.mark.asyncio
async def test_real_agentos_adapter_failure_is_not_completed(tmp_path):
    class Adapter:
        async def chat(self, *args, **kwargs):
            raise RuntimeError("synthetic upstream error")

    p = str(tmp_path)
    agent = AgentOS(
        config=AgentConfig(provider="openai", allowed_tools=[], max_iterations=2),
        adapter=Adapter(),
        tools=ToolRegistry(),
        system_prompt="Synthetic test.",
        path_context=AgentPathContext(p, p, p),
    )
    loop = agent.stream_with_queue("test", session_id="probe", storage=SessionStorage(p))
    result = await loop.run()
    assert result.status == "ERROR"
    assert_projection(loop, tmp_path, "error", "error")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,reason", [("CTX_OVERFLOW", "error"), ("BUDGET_EXCEEDED", "max-iterations")]
)
async def test_failed_compaction_persists_error_snapshot(tmp_path, code, reason):
    fault = Fault(domain="RESOURCE", code=code, message="synthetic", retryable=False)

    class VCPU:
        iteration = 0

        async def step(self):
            return StepResult(
                is_final=True, fault=fault, final_result=ToolResult(status="ERROR", fault=fault)
            )

    loop = RuntimeLoop(VCPU(), MMU(), session_id="probe", storage=SessionStorage(str(tmp_path)))

    async def no_compaction():
        return None

    loop._try_compaction = no_compaction
    assert (await loop.run()).status == "ERROR"
    assert_projection(loop, tmp_path, "error", reason)
