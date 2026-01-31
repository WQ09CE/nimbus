"""
Tests for Nimbus v2 vCPU (Virtual CPU).

These tests verify the core execution engine functionality:
- Think-Act-Observe loop
- Action handling (TOOL_CALL, SUB_CALL, RETURN, THOUGHT, etc.)
- Iteration limits and error handling
- Memory (MMU) integration
"""

import pytest
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from nimbus.core.protocol import ActionIR, ToolResult, Fault
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig, StepResult
from nimbus.os.gate import KernelGate, SimpleEventStream, SimplePermissionManager
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.memory.context import PinnedContext


# =============================================================================
# Mock LLM Client
# =============================================================================

@dataclass
class MockLLMResponse:
    """Mock LLM response for testing."""
    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


@dataclass
class MockToolCall:
    """Mock tool call structure."""
    function: Any = None


@dataclass
class MockFunction:
    """Mock function structure."""
    name: str = ""
    arguments: str = "{}"


class MockLLMClient:
    """
    Mock LLM client for testing.

    Can be configured with a sequence of responses to return.
    """

    def __init__(self, responses: Optional[List[MockLLMResponse]] = None):
        self.responses = responses or []
        self.call_count = 0
        self.messages_received: List[List[Dict[str, Any]]] = []

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> MockLLMResponse:
        self.messages_received.append(messages)
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        # Default: return a RETURN action
        return MockLLMResponse(
            tool_calls=[
                MockToolCall(
                    function=MockFunction(
                        name="return_result",
                        arguments='{"result": "default completion"}'
                    )
                )
            ]
        )


# =============================================================================
# Mock Tool Executor
# =============================================================================

class MockToolExecutor:
    """Mock tool executor for testing."""

    def __init__(self, results: Optional[Dict[str, Any]] = None):
        self.results = results or {}
        self.calls: List[tuple] = []

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        self.calls.append((tool_name, args))
        if tool_name in self.results:
            return self.results[tool_name]
        return f"Executed {tool_name} with {args}"


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def mock_llm():
    """Create a mock LLM client."""
    return MockLLMClient()


@pytest.fixture
def decoder():
    """Create an instruction decoder."""
    return InstructionDecoder()


@pytest.fixture
def event_stream():
    """Create an event stream for testing."""
    return SimpleEventStream()


@pytest.fixture
def tool_executor():
    """Create a mock tool executor."""
    return MockToolExecutor()


@pytest.fixture
def gate(event_stream, tool_executor):
    """Create a kernel gate with mock components."""
    perm = SimplePermissionManager(["*"])  # Allow all tools
    return KernelGate(
        pid="test-proc-001",
        permission_mgr=perm,
        event_stream=event_stream,
        tool_executor=tool_executor,
        default_timeout=30.0
    )


@pytest.fixture
def mmu():
    """Create an MMU instance."""
    mmu = MMU(config=MMUConfig(), process_id="test-proc-001")
    mmu.set_pinned(PinnedContext(
        system_rules="Be helpful and use tools correctly.",
        capabilities="Available tools: Read, Glob, Grep"
    ))
    return mmu


@pytest.fixture
def vcpu_config():
    """Create vCPU configuration."""
    return VCPUConfig(
        max_iterations=10,
        default_timeout=30.0,
        max_consecutive_thoughts=3,
        max_sub_call_depth=5,
        emit_step_events=True
    )


# =============================================================================
# Basic Execution Tests
# =============================================================================

class TestVCPUBasic:
    """Basic vCPU execution tests."""

    @pytest.mark.asyncio
    async def test_simple_return(self, decoder, gate, mmu, vcpu_config):
        """Test simple execution that returns immediately."""
        # Mock LLM that returns a result
        llm = MockLLMClient(responses=[
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Hello, World!"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        result = await vcpu.execute("Say hello")

        assert result.status == "OK"
        assert result.is_final is True
        assert result.output == "Hello, World!"
        assert vcpu.is_done is True

    @pytest.mark.asyncio
    async def test_tool_call(self, decoder, gate, mmu, vcpu_config, tool_executor):
        """Test tool call execution."""
        tool_executor.results["Read"] = "File content here"

        llm = MockLLMClient(responses=[
            # First: call a tool
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="Read",
                            arguments='{"file_path": "/test/file.txt"}'
                        )
                    )
                ]
            ),
            # Second: return result
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Read file successfully"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        result = await vcpu.execute("Read the file")

        assert result.status == "OK"
        assert result.is_final is True
        assert len(tool_executor.calls) == 1
        assert tool_executor.calls[0][0] == "Read"
        assert tool_executor.calls[0][1] == {"file_path": "/test/file.txt"}

    @pytest.mark.asyncio
    async def test_thought_handling(self, decoder, gate, mmu, vcpu_config):
        """Test thought handling and recording."""
        llm = MockLLMClient(responses=[
            # First: a thought
            MockLLMResponse(content="Let me think about this..."),
            # Second: return result
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Done thinking"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        result = await vcpu.execute("Think about something")

        assert result.status == "OK"
        assert result.is_final is True
        # Verify thought was recorded in memory
        assert any("think" in msg.content.lower()
                  for msg in mmu.current_frame.messages
                  if isinstance(msg.content, str))


# =============================================================================
# Iteration and Limit Tests
# =============================================================================

class TestVCPULimits:
    """Test vCPU iteration limits and boundaries."""

    @pytest.mark.asyncio
    async def test_max_iterations(self, decoder, gate, mmu):
        """Test that max iterations limit is enforced."""
        # LLM that never returns - just keeps thinking
        llm = MockLLMClient(responses=[
            MockLLMResponse(content="Thinking...")
            for _ in range(100)
        ])

        # Set max_consecutive_thoughts high enough so max_iterations triggers first
        config = VCPUConfig(max_iterations=5, max_consecutive_thoughts=100)
        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=config
        )

        result = await vcpu.execute("Never-ending task")

        assert result.status == "ERROR"
        assert result.fault is not None
        assert result.fault.code == "BUDGET_EXCEEDED"
        assert vcpu.iteration == 5

    @pytest.mark.asyncio
    async def test_max_consecutive_thoughts(self, decoder, gate, mmu):
        """Test that consecutive thoughts trigger auto-return."""
        # LLM returns text without tool calls
        thoughts = [
            MockLLMResponse(content=f"Thinking {i}...")
            for i in range(5)
        ]

        llm = MockLLMClient(responses=thoughts)

        # Set max_consecutive_thoughts to 3
        config = VCPUConfig(max_consecutive_thoughts=3)
        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=config
        )

        result = await vcpu.execute("Think a lot")

        # Should auto-return after 3 consecutive thoughts
        assert result.status == "OK"
        assert result.is_final is True
        # The output should be the last thought
        assert "Thinking 2" in result.output

    @pytest.mark.asyncio
    async def test_compaction_on_iteration_limit(self, decoder, gate, mmu):
        """Test that compaction is triggered when iteration limit is reached."""
        # Create many responses with DIFFERENT tool calls to avoid doom loop detection
        responses = []
        for i in range(15):
            if i < 12:
                # Keep doing tool calls with different file paths
                responses.append(MockLLMResponse(
                    content=f"Step {i}: Let me read file {i}",
                    tool_calls=[MockToolCall(
                        function=MockFunction(name="Read", arguments=f'{{"file_path": "/file_{i}.txt"}}')
                    )]
                ))
            else:
                # Eventually return result
                responses.append(MockLLMResponse(
                    content="Done!",
                    tool_calls=[MockToolCall(
                        function=MockFunction(name="return_result", arguments='{"result": "All done after compaction!"}')
                    )]
                ))
        
        llm = MockLLMClient(responses=responses)
        
        # Set low max_iterations to trigger compaction quickly
        config = VCPUConfig(
            max_iterations=5,
            max_consecutive_thoughts=100,
            compact_on_limit=True,
            max_compactions=3,
        )
        
        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=config
        )
        
        result = await vcpu.execute("Read many files")
        
        # Should succeed (not BUDGET_EXCEEDED) because compaction allowed continuation
        assert result.status == "OK"
        assert "All done" in result.output
        
        # Should have triggered at least one compaction (iteration reset from 5 back to 0)
        # We ran 12+ iterations with max_iterations=5, so at least 2 compactions
        assert vcpu._compaction_count >= 1

    @pytest.mark.asyncio
    async def test_max_compactions_limit(self, decoder, gate, mmu):
        """Test that max_compactions limit is enforced."""
        # Create endless responses with DIFFERENT paths to avoid doom loop
        responses = [
            MockLLMResponse(
                content=f"Step {i}",
                tool_calls=[MockToolCall(
                    function=MockFunction(name="Read", arguments=f'{{"file_path": "/file_{i}.txt"}}')
                )]
            )
            for i in range(100)
        ]
        
        llm = MockLLMClient(responses=responses)
        
        # Set very low limits to quickly hit max_compactions
        config = VCPUConfig(
            max_iterations=3,
            max_consecutive_thoughts=100,
            compact_on_limit=True,
            max_compactions=2,  # Only allow 2 compactions
        )
        
        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=config
        )
        
        result = await vcpu.execute("Endless task")
        
        # Should fail with BUDGET_EXCEEDED after max_compactions reached
        assert result.status == "ERROR"
        assert result.fault is not None
        assert result.fault.code == "BUDGET_EXCEEDED"
        # Verify compaction count reached max
        assert vcpu._compaction_count == 2


# =============================================================================
# SUB_CALL and RETURN Tests
# =============================================================================

class TestVCPUSubCall:
    """Test SUB_CALL and RETURN action handling."""

    @pytest.mark.asyncio
    async def test_sub_call_push_frame(self, decoder, gate, mmu, vcpu_config):
        """Test that SUB_CALL pushes a new frame."""
        llm = MockLLMClient(responses=[
            # First: spawn a subprocess
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="call_subroutine",
                            arguments='{"goal": "explore codebase"}'
                        )
                    )
                ]
            ),
            # Second: return from subprocess
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Explored successfully"}'
                        )
                    )
                ]
            ),
            # Third: return from main
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "All done"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        # Before execution, we're at root frame
        assert mmu.stack_depth == 1

        result = await vcpu.execute("Do something complex")

        assert result.status == "OK"
        # After completion, we should be back at root frame
        assert mmu.stack_depth == 1

    @pytest.mark.asyncio
    async def test_max_sub_call_depth(self, decoder, gate, mmu):
        """Test that max sub-call depth is enforced."""
        # Create responses that keep spawning sub-calls
        responses = []
        for i in range(15):
            responses.append(MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="call_subroutine",
                            arguments=f'{{"goal": "sub task {i}"}}'
                        )
                    )
                ]
            ))

        llm = MockLLMClient(responses=responses)

        config = VCPUConfig(max_sub_call_depth=3)
        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=config
        )

        result = await vcpu.execute("Deep recursion")

        # Should hit the depth limit
        assert mmu.stack_depth <= config.max_sub_call_depth


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestVCPUErrors:
    """Test vCPU error handling."""

    @pytest.mark.asyncio
    async def test_tool_permission_denied(self, decoder, mmu, vcpu_config, event_stream):
        """Test handling of permission denied errors."""
        # Create gate with restricted permissions
        perm = SimplePermissionManager(["Read"])  # Only allow Read
        executor = MockToolExecutor()
        gate = KernelGate(
            pid="test-proc",
            permission_mgr=perm,
            event_stream=event_stream,
            tool_executor=executor
        )

        llm = MockLLMClient(responses=[
            # Try to use a forbidden tool
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="Bash",  # Not allowed
                            arguments='{"command": "ls"}'
                        )
                    )
                ]
            ),
            # Then return
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Done"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        result = await vcpu.execute("Try to run bash")

        # Permission denied is non-retryable, so execution stops
        assert result.status == "ERROR"
        assert result.fault is not None
        assert result.fault.code == "PERMISSION_DENIED"
        # Executor should not have been called for Bash
        assert len(executor.calls) == 0

    @pytest.mark.asyncio
    async def test_hallucination_detection(self, decoder, gate, mmu, vcpu_config):
        """Test that hallucination patterns are detected."""
        llm = MockLLMClient(responses=[
            # Response with hallucination pattern
            MockLLMResponse(content="[Called Read tool with file.txt]"),
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        # Step once - should get a fault
        mmu.add_user_message("Read a file")
        step_result = await vcpu.step()

        assert step_result.fault is not None
        assert step_result.fault.code == "ILL_INSTRUCTION"


# =============================================================================
# Step Execution Tests
# =============================================================================

class TestVCPUStep:
    """Test vCPU step-by-step execution."""

    @pytest.mark.asyncio
    async def test_single_step(self, decoder, gate, mmu, vcpu_config, tool_executor):
        """Test single step execution."""
        tool_executor.results["Glob"] = ["file1.py", "file2.py"]

        llm = MockLLMClient(responses=[
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="Glob",
                            arguments='{"pattern": "*.py"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        mmu.add_user_message("Find Python files")
        step_result = await vcpu.step()

        assert len(step_result.actions) == 1
        assert step_result.actions[0].kind == "TOOL_CALL"
        assert step_result.actions[0].name == "Glob"
        assert len(step_result.results) == 1
        assert step_result.results[0].status == "OK"
        assert step_result.is_final is False

    @pytest.mark.asyncio
    async def test_step_timing(self, decoder, gate, mmu, vcpu_config):
        """Test that step timing is recorded."""
        llm = MockLLMClient(responses=[
            MockLLMResponse(content="Just thinking...")
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        mmu.add_user_message("Do something")
        step_result = await vcpu.step()

        assert "total" in step_result.timing_ms
        assert "think" in step_result.timing_ms
        assert "decode" in step_result.timing_ms
        assert step_result.timing_ms["total"] >= 0


# =============================================================================
# Event Emission Tests
# =============================================================================

class TestVCPUEvents:
    """Test vCPU event emission."""

    @pytest.mark.asyncio
    async def test_events_emitted(self, decoder, gate, mmu, vcpu_config, event_stream):
        """Test that events are emitted during execution."""
        llm = MockLLMClient(responses=[
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Done"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        await vcpu.execute("Do something")

        # Check that events were emitted
        event_types = [e.type for e in event_stream.events]
        assert "STEP_STARTED" in event_types
        assert "ACTION_EMITTED" in event_types

    @pytest.mark.asyncio
    async def test_events_disabled(self, decoder, gate, mmu, event_stream):
        """Test that events can be disabled."""
        config = VCPUConfig(emit_step_events=False)

        llm = MockLLMClient(responses=[
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Done"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=config
        )

        event_stream.clear()
        await vcpu.execute("Do something")

        # Only tool events from gate, not step events
        step_events = [e for e in event_stream.events if e.type in ["STEP_STARTED", "ACTION_EMITTED"]]
        assert len(step_events) == 0


# =============================================================================
# State Accessor Tests
# =============================================================================

class TestVCPUState:
    """Test vCPU state accessors."""

    @pytest.mark.asyncio
    async def test_get_state(self, decoder, gate, mmu, vcpu_config):
        """Test get_state method."""
        llm = MockLLMClient(responses=[
            MockLLMResponse(content="Thinking..."),
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Done"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        await vcpu.execute("Do something")

        state = vcpu.get_state()
        assert "iteration" in state
        assert "is_running" in state
        assert "is_done" in state
        assert "stack_depth" in state
        assert "mmu_state" in state
        assert state["is_done"] is True
        assert state["iteration"] == 2

    def test_initial_state(self, decoder, gate, mmu, vcpu_config):
        """Test initial vCPU state."""
        llm = MockLLMClient()
        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        assert vcpu.iteration == 0
        assert vcpu.is_running is False
        assert vcpu.is_done is False


# =============================================================================
# Integration Tests
# =============================================================================

class TestVCPUIntegration:
    """Integration tests for vCPU with all components."""

    @pytest.mark.asyncio
    async def test_full_workflow(self, decoder, gate, mmu, vcpu_config, tool_executor):
        """Test a complete workflow with multiple steps."""
        tool_executor.results["Glob"] = ["src/main.py", "src/utils.py"]
        tool_executor.results["Read"] = "def main():\n    print('Hello')"

        llm = MockLLMClient(responses=[
            # Step 1: Find files
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="Glob",
                            arguments='{"pattern": "src/*.py"}'
                        )
                    )
                ]
            ),
            # Step 2: Think about the files
            MockLLMResponse(content="Found 2 Python files. Let me read one."),
            # Step 3: Read a file
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="Read",
                            arguments='{"file_path": "src/main.py"}'
                        )
                    )
                ]
            ),
            # Step 4: Return result
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="return_result",
                            arguments='{"result": "Found main.py with a simple main function"}'
                        )
                    )
                ]
            )
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config
        )

        result = await vcpu.execute("Explore the codebase")

        assert result.status == "OK"
        assert result.is_final is True
        assert "main" in result.output.lower()
        assert len(tool_executor.calls) == 2
        assert vcpu.iteration == 4
