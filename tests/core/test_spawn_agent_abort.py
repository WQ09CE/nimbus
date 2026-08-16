"""Regression tests for spawn-agent contract and parent/child abort wiring."""

import asyncio
from types import SimpleNamespace

import pytest

from nimbus.core.protocol import ToolResult
from nimbus.core.tools.spawn_agent import _run_sub_agent


class _FakeLoop:
    def __init__(self, wait_for_abort: bool = False):
        self._abort_event = asyncio.Event()
        self.gate = SimpleNamespace(_abort_event=self._abort_event)
        self.vcpu = SimpleNamespace(interrupted=False)
        self.mmu = SimpleNamespace(_plan="")
        self.partial_results = []
        self.wait_for_abort = wait_for_abort

    def abort(self):
        self.vcpu.interrupted = True
        self._abort_event.set()

    async def stream(self):
        if self.wait_for_abort:
            await self.gate._abort_event.wait()
            yield {"type": "final", "result": ToolResult(status="CANCELLED", output="aborted")}
        else:
            yield {"type": "final", "result": ToolResult(status="OK", output="plain text only")}


async def _install_fake_agent(monkeypatch, loop):
    import nimbus.adapters.llm_factory as factory_module
    import nimbus.core.agent as agent_module

    class FakeAgentOS:
        def __init__(self, **kwargs):
            pass

        def stream_with_queue(self, goal, session_id=None):
            return loop

    async def fake_create_llm_client(**kwargs):
        return object()

    monkeypatch.setattr(agent_module, "AgentOS", FakeAgentOS)
    monkeypatch.setattr(factory_module, "create_llm_client", fake_create_llm_client)


@pytest.mark.asyncio
async def test_parent_abort_reaches_child_gate_event(monkeypatch, tmp_path):
    """The child Gate/Bash event must remain the loop's construction-time event."""
    monkeypatch.chdir(tmp_path)
    loop = _FakeLoop(wait_for_abort=True)
    original_child_event = loop._abort_event
    await _install_fake_agent(monkeypatch, loop)
    parent_event = asyncio.Event()

    task = asyncio.create_task(_run_sub_agent(
        role="reader", goal="wait", sub_session_id="sub_abort",
        timeout_seconds=5, _abort_event=parent_event,
    ))
    await asyncio.sleep(0)
    parent_event.set()
    result = await asyncio.wait_for(task, 1)

    assert loop._abort_event is original_child_event
    assert loop.gate._abort_event is original_child_event
    assert original_child_event.is_set()
    assert loop.vcpu.interrupted
    assert result["status"] == "ERROR"
    assert result["ui_detail"]["status"] == "ERROR"  # no deliverable is never completed


@pytest.mark.asyncio
async def test_missing_contract_deliverable_is_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    loop = _FakeLoop()
    await _install_fake_agent(monkeypatch, loop)

    result = await _run_sub_agent(
        role="reader", goal="answer in text", sub_session_id="sub_missing",
        timeout_seconds=5,
    )

    assert result["status"] == "ERROR"
    assert result["ui_detail"]["status"] == "ERROR"
    assert result["ui_detail"]["error"] == "Missing contract deliverable"
    assert "failed its result contract" in result["output"]
