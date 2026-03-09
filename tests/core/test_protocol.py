"""Tests for nimbus_next.protocol — the system spine."""

from nimbus.core.protocol import (
    ActionIR,
    Event,
    Fault,
    StepResult,
    ToolResult,
)


class TestActionIR:
    def test_tool_call_creation(self):
        action = ActionIR(kind="TOOL_CALL", name="Read", args={"file_path": "/tmp/x"})
        assert action.kind == "TOOL_CALL"
        assert action.name == "Read"
        assert action.args == {"file_path": "/tmp/x"}
        assert len(action.id) == 8  # uuid hex[:8]

    def test_reply_creation(self):
        action = ActionIR(kind="REPLY", name="", args={"text": "hello"})
        assert action.kind == "REPLY"

    def test_thought_creation(self):
        action = ActionIR(kind="THOUGHT", name="reasoning")
        assert action.args == {}

    def test_unique_ids(self):
        a1 = ActionIR(kind="TOOL_CALL", name="Bash")
        a2 = ActionIR(kind="TOOL_CALL", name="Bash")
        assert a1.id != a2.id

    def test_meta_defaults_to_empty(self):
        action = ActionIR(kind="CANCEL")
        assert action.meta == {}


class TestToolResult:
    def test_ok_result(self):
        r = ToolResult(output="hello world")
        assert r.status == "OK"
        assert r.output == "hello world"
        assert r.fault is None

    def test_error_result_with_fault(self):
        f = Fault(domain="TOOL", code="TOOL_FAILURE", message="boom")
        r = ToolResult(status="ERROR", fault=f)
        assert r.status == "ERROR"
        assert r.fault.code == "TOOL_FAILURE"

    def test_final_result(self):
        r = ToolResult(output="done", is_final=True)
        assert r.is_final is True

    def test_timing_and_cost(self):
        r = ToolResult(timing_ms={"exec": 150}, cost={"tokens": 100})
        assert r.timing_ms["exec"] == 150
        assert r.cost["tokens"] == 100


class TestStepResult:
    def test_empty_step(self):
        s = StepResult()
        assert s.actions == []
        assert s.results == []
        assert s.is_final is False

    def test_step_with_actions_and_results(self):
        action = ActionIR(kind="TOOL_CALL", name="Bash", args={"command": "ls"})
        result = ToolResult(output="file1\nfile2")
        s = StepResult(actions=[action], results=[result])
        assert len(s.actions) == 1
        assert len(s.results) == 1

    def test_final_step(self):
        final = ToolResult(output="task done", is_final=True)
        s = StepResult(is_final=True, final_result=final)
        assert s.is_final is True
        assert s.final_result.output == "task done"


class TestFault:
    def test_fault_creation(self):
        f = Fault(domain="LLM", code="ILL_INSTRUCTION", message="hallucination detected")
        assert f.domain == "LLM"
        assert f.code == "ILL_INSTRUCTION"
        assert f.retryable is False

    def test_retryable_fault(self):
        f = Fault(domain="RESOURCE", code="TIMEOUT", message="timed out", retryable=True)
        assert f.retryable is True

    def test_fault_is_exception(self):
        f = Fault(domain="KERNEL", code="SYSTEM_ERROR", message="panic")
        assert isinstance(f, Exception)

    def test_fault_str(self):
        f = Fault(domain="TOOL", code="TOOL_NOT_FOUND", message="no such tool: Foo")
        assert str(f) == "[TOOL:TOOL_NOT_FOUND] no such tool: Foo"

    def test_fault_repr(self):
        f = Fault(domain="LLM", code="RATE_LIMIT", message="429")
        assert "RATE_LIMIT" in repr(f)

    def test_fault_context(self):
        f = Fault(
            domain="TOOL", code="INVALID_ARGS", message="bad args",
            context={"tool": "Read", "missing": "file_path"},
        )
        assert f.context["tool"] == "Read"

    def test_fault_domain_routing(self):
        """Verify faults can be routed by domain for recovery logic."""
        faults = [
            Fault(domain="LLM", code="ILL_INSTRUCTION", message="hallucination"),
            Fault(domain="TOOL", code="TOOL_FAILURE", message="crash"),
            Fault(domain="RESOURCE", code="TIMEOUT", message="slow", retryable=True),
        ]
        retryable = [f for f in faults if f.retryable]
        assert len(retryable) == 1
        assert retryable[0].code == "TIMEOUT"

        llm_faults = [f for f in faults if f.domain == "LLM"]
        assert len(llm_faults) == 1


class TestEvent:
    def test_event_creation(self):
        e = Event(type="TOOL_STARTED", pid="proc-1", data={"tool": "Bash"})
        assert e.type == "TOOL_STARTED"
        assert e.pid == "proc-1"
        assert e.ts_ms > 0

    def test_event_timestamp_auto(self):
        e1 = Event(type="STEP_STARTED", pid="p1")
        e2 = Event(type="STEP_STARTED", pid="p1")
        assert e2.ts_ms >= e1.ts_ms
