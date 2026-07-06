"""Tests for subagent governance: contract_mode + submit_result + countdown steering."""

import json
import os
import pytest
import tempfile

from nimbus.core.decoder import InstructionDecoder
from nimbus.core.protocol import Fault


# =============================================================================
# contract_mode tests
# =============================================================================

class TestContractMode:
    """In contract_mode, pure text NEVER produces RETURN — always THOUGHT."""

    def test_submit_result_terminates_vcpu(self):
        """When sub-agent calls submit_result, VCPU must immediately terminate (is_final=True)
        so it doesn't enter an infinite loop of empty responses.
        """
        import asyncio
        from nimbus.core.protocol import ActionIR, ToolResult, Fault
        from nimbus.core.vcpu import VCPU, VCPUConfig
        from nimbus.core.mmu import MMU, MMUConfig

        class MockALU:
            async def chat(self, *args, **kwargs):
                from dataclasses import dataclass
                @dataclass
                class MockResponse:
                    content: str = ""
                    tool_calls: list = None
                    usage = None
                return MockResponse(content="", tool_calls=[])
                
        class MockDecoder:
            def decode(self, *args, **kwargs):
                return [ActionIR(id="c1", kind="TOOL_CALL", name="submit_result", args={"summary":"test"})]
                
        class MockGate:
            async def syscall_tool(self, *args, **kwargs):
                return ToolResult(status="OK", output="delivered")
        
        mmu = MMU(MMUConfig())
        # Mock mmu.set_last_usage
        mmu.set_last_usage = lambda u: None
        mmu.add_assistant_with_tool_calls = lambda *args: None
        mmu.add_tool_result = lambda *args, **kwargs: None
        
        vcpu = VCPU(MockALU(), MockDecoder(), MockGate(), mmu, tools=[], config=VCPUConfig(contract_mode=True))
        
        # Test step triggers termination
        res = asyncio.run(vcpu.step())
        
        assert len(res.results) == 1
        assert res.results[0].status == "OK"
        # The critical bug fix: the step must be marked final to break the loop!
        assert res.is_final is True
        assert res.final_result.output == "delivered"

    def setup_method(self):
        self.decoder = InstructionDecoder()

    def test_short_text_becomes_thought_not_return(self):
        """The core bug: short text like 'Done!' should NOT kill the sub-agent."""
        actions = self.decoder.decode(
            content="Done!", tool_calls=None,
            text_is_final=False, contract_mode=True,
        )
        assert len(actions) == 1
        assert actions[0].kind == "THOUGHT"  # NOT RETURN

    def test_chinese_done_pattern_becomes_thought(self):
        actions = self.decoder.decode(
            content="任务已完成。", tool_calls=None,
            text_is_final=False, contract_mode=True,
        )
        assert actions[0].kind == "THOUGHT"

    def test_short_non_planning_text_becomes_thought(self):
        """Previously: <=120 chars without planning words → RETURN. Now: THOUGHT."""
        actions = self.decoder.decode(
            content="好的，我来看看这个文件", tool_calls=None,
            text_is_final=False, contract_mode=True,
        )
        assert actions[0].kind == "THOUGHT"

    def test_tool_calls_still_work_in_contract_mode(self):
        """contract_mode should not affect tool call routing."""
        from tests.core.test_decoder import MockToolCall
        tc = MockToolCall("Read", '{"file_path": "/tmp/x"}')
        actions = self.decoder.decode(
            content="Let me read this.", tool_calls=[tc],
            text_is_final=False, contract_mode=True,
        )
        assert actions[0].kind == "THOUGHT"
        assert actions[1].kind == "TOOL_CALL"

    def test_contract_mode_false_preserves_existing_behavior(self):
        """Default (contract_mode=False) should still produce RETURN for short done-text."""
        actions = self.decoder.decode(
            content="Done!", tool_calls=None,
            text_is_final=False, contract_mode=False,
        )
        assert actions[0].kind == "RETURN"

    def test_hallucination_detection_still_works(self):
        """contract_mode should not bypass hallucination firewall."""
        with pytest.raises(Fault):
            self.decoder.decode(
                content='[Called Read with file_path="/tmp/x"]',
                tool_calls=None,
                text_is_final=False, contract_mode=True,
            )


# =============================================================================
# submit_result tool tests
# =============================================================================

class TestSubmitResult:
    """Test the submit_result tool."""

    def test_writes_deliverable_json(self):
        from nimbus.core.tools.submit_result import submit_result_impl

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "deliverable.json")
            result = submit_result_impl(
                summary="Found 3 files",
                findings=["a.py: 100 lines", "b.py: 200 lines"],
                artifacts=["scratchpad.md"],
                deliverable_path=path,
            )
            assert result["status"] == "DELIVERED"
            
            with open(path) as f:
                data = json.load(f)
            assert data["summary"] == "Found 3 files"
            assert len(data["findings"]) == 2

    def test_truncates_long_summary(self):
        from nimbus.core.tools.submit_result import submit_result_impl

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "deliverable.json")
            long_summary = "x" * 1000
            submit_result_impl(
                summary=long_summary,
                findings=[],
                artifacts=[],
                deliverable_path=path,
            )
            with open(path) as f:
                data = json.load(f)
            assert len(data["summary"]) <= 503  # 500 + "..."

    def test_truncates_long_finding_items(self):
        from nimbus.core.tools.submit_result import submit_result_impl

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "deliverable.json")
            submit_result_impl(
                summary="ok",
                findings=["x" * 500],
                artifacts=[],
                deliverable_path=path,
            )
            with open(path) as f:
                data = json.load(f)
            assert len(data["findings"][0]) <= 203  # 200 + "..."

    def test_json_is_valid_after_truncation(self):
        """Truncation must never break JSON structure."""
        from nimbus.core.tools.submit_result import submit_result_impl

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "deliverable.json")
            submit_result_impl(
                summary="a" * 2000,
                findings=["b" * 2000, "c" * 2000],
                artifacts=["d" * 2000],
                deliverable_path=path,
            )
            # Must parse without error
            with open(path) as f:
                data = json.load(f)
            assert isinstance(data, dict)


# =============================================================================
# Countdown steering tests
# =============================================================================

class TestDualToolResult:
    """Pi-style dual result: output → LLM context, ui_detail → UI persistence."""

    def test_ui_detail_stored_in_mmu_meta(self):
        """ui_detail should be persisted in Message.meta, not sent to LLM."""
        from nimbus.core.mmu import MMU, MMUConfig, PinnedContext

        mmu = MMU(MMUConfig(max_context_tokens=100_000))
        mmu.set_pinned(PinnedContext(system_rules="test"))
        mmu.add_user_message("test")
        mmu.add_tool_result(
            "call_1", "Read", "file content here...",
            ui_detail={"raw_text_output": "very long content...", "line_count": 500},
        )

        # Meta should contain ui_detail
        tool_msg = [m for m in mmu._messages if m.role == "tool"][0]
        assert tool_msg.meta["ui_detail"]["line_count"] == 500

        # Persistence (to_dict with meta)
        d = tool_msg.to_dict(include_meta=True)
        assert "meta" in d
        assert d["meta"]["ui_detail"]["line_count"] == 500

        # LLM API (to_dict without meta)
        d_llm = tool_msg.to_dict(include_meta=False)
        assert "meta" not in d_llm

    def test_assemble_context_excludes_meta(self):
        """assemble_context must NOT include meta (LLM providers reject unknown fields)."""
        from nimbus.core.mmu import MMU, MMUConfig, PinnedContext

        mmu = MMU(MMUConfig(max_context_tokens=100_000))
        mmu.set_pinned(PinnedContext(system_rules="test"))
        mmu.add_user_message("hello")
        mmu.add_assistant_with_tool_calls("thinking", [
            {"id": "c1", "type": "function", "function": {"name": "Read", "arguments": '{"path":"x"}'}}
        ])
        mmu.add_tool_result("c1", "Read", "content", ui_detail={"full_output": "very long..."})

        ctx = mmu.assemble_context()
        # Find the tool result message in the assembled context
        tool_msgs = [m for m in ctx if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "meta" not in tool_msgs[0]  # Must NOT be sent to LLM


class TestCountdownSteering:
    """Test that VCPU injects countdown warning near iteration limit."""

    def test_countdown_warning_injected_at_threshold(self):
        """At 85% of max_iterations, a system message should be injected."""
        from unittest.mock import MagicMock, AsyncMock
        from nimbus.core.vcpu import VCPU, VCPUConfig

        config = VCPUConfig(max_iterations=20, contract_mode=True)
        mmu = MagicMock()
        mmu.assemble_context.return_value = [{"role": "user", "content": "test"}]

        # Mock ALU to return pure text (will become THOUGHT in contract_mode)
        alu = AsyncMock()
        response = MagicMock()
        response.content = "Still working on it..."
        response.tool_calls = None
        response.usage = None
        alu.chat.return_value = response

        decoder = InstructionDecoder()
        gate = MagicMock()

        vcpu = VCPU(
            alu=alu, decoder=decoder, gate=gate, mmu=mmu,
            tools=[], config=config, text_is_final=False,
        )

        # Fast-forward to iteration 17 (85% of 20)
        vcpu._exec.iteration = 16  # will become 17 on next step

        import asyncio
        asyncio.run(vcpu.step())

        # Check that a countdown system message was added
        calls = [str(c) for c in mmu.add_system_message.call_args_list]
        assert any("步" in str(c) or "step" in str(c).lower() for c in calls), \
            f"Expected countdown warning in system messages, got: {calls}"

class TestMultiAgentSteering:
    """Verifies behavior when steering messages are injected during sub-agent execution."""

    @pytest.mark.asyncio
    async def test_steering_injection_routing(self):
        """Currently, steering messages injected into parent queue remain there until 
        the sub-agent completes. This test documents the current architecture's behavior.
        """
        import asyncio
        from nimbus.core.loop import MessageQueue
        
        # Parent queue
        class MockQueue:
            def __init__(self):
                self._q = []
            def enqueue(self, msg):
                self._q.append(msg)
            def dequeue(self):
                return self._q.pop(0) if self._q else None
            def empty(self):
                return len(self._q) == 0

        parent_queue = MockQueue()
        
        # Simulate an external API call injecting a message
        parent_queue.enqueue("Hey, change your plan!")
        
        # It sits in the parent's queue
        assert not parent_queue.empty()
        
        # If we had a sub-agent loop running, it would have its own queue
        sub_queue = MockQueue()
        assert sub_queue.empty()
        
        # The behavior: Parent queue retains the steering message, and will process it
        # on its NEXT think step after the subagent returns.
        msg = parent_queue.dequeue()
        assert msg == "Hey, change your plan!"
