"""
Tests for Nimbus v2 vCPU (Virtual CPU).

These tests verify the core execution engine functionality:
- Think-Act-Observe loop
- Action handling (TOOL_CALL, SUB_CALL, RETURN, THOUGHT, etc.)
- Iteration limits and error handling
- Memory (MMU) integration
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Callable

import pytest

from nimbus.core.memory.context import PinnedContext
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig
from nimbus.core.models.manifest import ModelManifest, ModelFeatures
from nimbus.os.gate import KernelGate, SimpleEventStream
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
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> MockLLMResponse:
        print(f"DEBUG: chat called with on_chunk={on_chunk}")
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
# Mock Tools
# =============================================================================

MOCK_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file"}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "Write",
            "description": "Write a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["file_path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "Edit",
            "description": "Edit a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file"},
                    "old_text": {"type": "string", "description": "Text to find"},
                    "new_text": {"type": "string", "description": "Replacement text"}
                },
                "required": ["file_path", "old_text", "new_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Run a bash command",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "return_result",
            "description": "Return final result",
            "parameters": {
                "type": "object",
                "properties": {
                    "result": {"type": "string", "description": "Final result"}
                },
                "required": ["result"]
            }
        }
    },
]


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
    return KernelGate(
        pid="test-proc-001",
        tool_executor=tool_executor,
        event_stream=event_stream,
        default_timeout=30.0
    )


@pytest.fixture
def mmu():
    """Create an MMU instance."""
    mmu = MMU(config=MMUConfig(), process_id="test-proc-001")
    mmu.set_pinned(PinnedContext(
        system_rules="Be helpful and use tools correctly.",
        capabilities="Available tools: Read, Write, Edit, Bash"
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
            config=vcpu_config,
            tools=MOCK_TOOLS,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        result = await vcpu.run("Say hello")

        assert result.status == "OK"
        assert result.is_final is True
        assert result.output == "Hello, World!"


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
            config=vcpu_config,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        result = await vcpu.run("Read the file")

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
            config=vcpu_config,
            tools=MOCK_TOOLS,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        result = await vcpu.run("Think about something")

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
        # LLM that keeps trying to call a tool endlessly
        # Use unique content per response to avoid staleness detection
        llm = MockLLMClient(responses=[
            MockLLMResponse(
                content=f"Thinking about step {i} of the problem...",
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="Bash",
                            arguments=f'{{"command": "echo step {i}"}}'
                        )
                    )
                ]
            )
            for i in range(100)
        ])
    
        # Set max_consecutive_thoughts high enough so max_iterations triggers first
        # Disable compaction to ensure we hit the hard limit
        config = VCPUConfig(
            max_iterations=5, 
            max_consecutive_thoughts=100,
            compact_on_limit=False
        )
        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=config,
            tools=MOCK_TOOLS,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        result = await vcpu.run("Never-ending task")

        assert result.status == "ERROR"
        assert result.fault is not None
        assert result.fault.code == "BUDGET_EXCEEDED"
        assert vcpu._state.iteration_count == 5








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
            config=vcpu_config,
            tools=MOCK_TOOLS,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        # Before execution, we're at root frame
        assert mmu.stack_depth == 1

        result = await vcpu.run("Do something complex")

        assert result.status == "OK"
        # After completion, we should be back at root frame
        assert mmu.stack_depth == 1




# =============================================================================
# Error Handling Tests
# =============================================================================

class TestVCPUErrors:
    """Test vCPU error handling."""

    # Note: test_tool_permission_denied was removed as permission checking was removed from Gate.
    # Permission checking can be re-added as a separate middleware if needed.

    @pytest.mark.asyncio
    async def test_hallucination_pattern_passes_through(self, decoder, gate, mmu, vcpu_config):
        """Test that text containing hallucination patterns passes through without Fault.

        Hallucination detection was moved out of the decoder to avoid false positives
        when models legitimately discuss tool patterns. The pipeline's HallucinationSanitizer
        (enabled per-model) provides soft defense by stripping patterns from content.
        Short patterns may be classified as REPLY by the conversational heuristic.
        """
        llm = MockLLMClient(responses=[
            # Response with hallucination pattern - should pass through (no Fault)
            MockLLMResponse(content="[Called Read tool with file.txt]"),
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config,
            tools=MOCK_TOOLS,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        mmu.add_user_message("Read a file")
        step_result = await vcpu.run("dummy goal")

        # No fault - hallucination firewall strips the content and injects a hint
        # The step completes without fault, but actions are empty since content was stripped
        assert step_result.fault is None




# =============================================================================
# State Accessor Tests
# =============================================================================

class TestVCPUState:
    """Test vCPU state accessors."""

    @pytest.mark.asyncio
    async def test_get_state(self, decoder, gate, mmu, vcpu_config):
        """Test get_state method."""
        # "Thinking..." is short (11 chars) and will be classified as REPLY
        # by the conversational heuristic, so VCPU finishes in 1 iteration.
        llm = MockLLMClient(responses=[
            MockLLMResponse(content="Thinking..."),
        ])

        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config,
            tools=MOCK_TOOLS,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        await vcpu.run("Do something")

        state = vcpu.get_state()
        assert "iteration" in state

        assert state["iteration"] == 1

    def test_initial_state(self, decoder, gate, mmu, vcpu_config):
        """Test initial vCPU state."""
        llm = MockLLMClient()
        vcpu = VCPU(
            alu=llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=vcpu_config,
            tools=MOCK_TOOLS,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        assert vcpu._state.iteration_count == 0



# =============================================================================
# Integration Tests
# =============================================================================

class TestVCPUIntegration:
    """Integration tests for vCPU with all components."""

    @pytest.mark.asyncio
    async def test_full_workflow(self, decoder, gate, mmu, vcpu_config, tool_executor):
        """Test a complete workflow with multiple steps."""
        tool_executor.results["Bash"] = "src/main.py\nsrc/utils.py"
        tool_executor.results["Read"] = "def main():\n    print('Hello')"

        llm = MockLLMClient(responses=[
            # Step 1: Find files
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockFunction(
                            name="Bash",
                            arguments='{"command": "find src -name *.py"}'
                        )
                    )
                ]
            ),
            # Step 2: Think about the files and Read a file
            MockLLMResponse(
                content="Found 2 Python files. Let me read one.",
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
            config=vcpu_config,
            tools=MOCK_TOOLS,
            manifest=ModelManifest(model_id="mock-llm", features=ModelFeatures())
        )

        result = await vcpu.run("Explore the codebase")

        assert result.status == "OK"
        assert result.is_final is True
        assert "main" in result.output.lower() or "read one" in result.output.lower()
        assert len(tool_executor.calls) == 2
        # Setup pushes a message, tool_calls push a message. 3 active iterations.
        assert vcpu._state.iteration_count == 3
