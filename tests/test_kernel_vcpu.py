"""
vCPU (Virtual Processor) tests.

Tests the core vCPU functionality:
- Think-Act-Observe loop
- Resource limit checking
- Tool execution with permissions
- Error handling
"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from nimbus.kernel import (
    AgentOS,
    AgentProcess,
    ProcessState,
    vCPU,
    vCPUError,
    ResourceLimitError,
    MaxIterationsError,
)
from nimbus.llm.base import CompletionResponse, ToolCall
from nimbus.tools.base import ToolRegistry, ToolDefinition, ToolParameter, tool


# ============================================================================
# Mock LLM Clients
# ============================================================================


class MockLLMClient:
    """Mock LLM that completes immediately without tool calls."""

    def __init__(self, response_text: str = "Task completed successfully"):
        self.response_text = response_text
        self.call_count = 0

    async def complete(self, prompt: str, history: Optional[List] = None, **kwargs) -> str:
        self.call_count += 1
        return self.response_text

    async def stream(self, prompt: str, history: Optional[List] = None, **kwargs):
        yield self.response_text

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> CompletionResponse:
        self.call_count += 1
        return CompletionResponse(
            content=self.response_text,
            tool_calls=[],
            finish_reason="stop",
        )


class MockToolCallingLLM:
    """Mock LLM that calls tools a specified number of times before completing."""

    def __init__(
        self,
        tool_calls_sequence: List[List[Dict[str, Any]]],
        final_response: str = "All done",
    ):
        """
        Args:
            tool_calls_sequence: List of tool call lists for each iteration.
                                When exhausted, returns final response.
            final_response: Response text when no more tool calls.
        """
        self.tool_calls_sequence = tool_calls_sequence
        self.final_response = final_response
        self.call_count = 0
        self.messages_received: List[List[Dict]] = []

    async def complete(self, prompt: str, history: Optional[List] = None, **kwargs) -> str:
        return self.final_response

    async def stream(self, prompt: str, history: Optional[List] = None, **kwargs):
        yield self.final_response

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> CompletionResponse:
        self.messages_received.append(messages.copy())
        iteration = self.call_count
        self.call_count += 1

        if iteration < len(self.tool_calls_sequence):
            # Return tool calls for this iteration
            tool_calls = [
                ToolCall(
                    id=f"call_{iteration}_{i}",
                    name=tc["name"],
                    arguments=tc.get("arguments", {}),
                )
                for i, tc in enumerate(self.tool_calls_sequence[iteration])
            ]
            return CompletionResponse(
                content=None,
                tool_calls=tool_calls,
                finish_reason="tool_calls",
            )
        else:
            # No more tool calls, return final response
            return CompletionResponse(
                content=self.final_response,
                tool_calls=[],
                finish_reason="stop",
            )


class InfiniteLLM:
    """Mock LLM that always returns tool calls (for testing max iterations)."""

    def __init__(self):
        self.call_count = 0

    async def complete(self, prompt: str, **kwargs) -> str:
        return "infinite"

    async def stream(self, prompt: str, **kwargs):
        yield "infinite"

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> CompletionResponse:
        self.call_count += 1
        return CompletionResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id=f"infinite_{self.call_count}",
                    name="Echo",
                    arguments={"message": "loop"},
                )
            ],
            finish_reason="tool_calls",
        )


# ============================================================================
# Test Tools
# ============================================================================


@tool(
    name="Echo",
    description="Echo back the input message",
    parameters=[
        ToolParameter("message", "string", "Message to echo", required=True),
    ],
)
async def echo_tool(message: str, **kwargs) -> str:
    return f"Echo: {message}"


@tool(
    name="Add",
    description="Add two numbers",
    parameters=[
        ToolParameter("a", "number", "First number", required=True),
        ToolParameter("b", "number", "Second number", required=True),
    ],
)
async def add_tool(a: float, b: float, **kwargs) -> str:
    return str(a + b)


@tool(
    name="Fail",
    description="A tool that always fails",
    parameters=[
        ToolParameter("reason", "string", "Failure reason", required=False),
    ],
)
async def fail_tool(reason: str = "Unknown", **kwargs) -> str:
    raise RuntimeError(f"Tool failed: {reason}")


def create_test_registry() -> ToolRegistry:
    """Create a tool registry with test tools."""
    registry = ToolRegistry()
    registry.register_decorated(echo_tool)
    registry.register_decorated(add_tool)
    registry.register_decorated(fail_tool)
    return registry


# ============================================================================
# vCPU Basic Tests
# ============================================================================


class TestVCPUBasic:
    """Test basic vCPU functionality."""

    def test_vcpu_init(self):
        """Test vCPU initialization."""
        llm = MockLLMClient()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools, max_iterations=10)

        assert vcpu.llm == llm
        assert vcpu.tools == tools
        assert vcpu.max_iterations == 10

    def test_vcpu_repr(self):
        """Test vCPU string representation."""
        llm = MockLLMClient()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        repr_str = repr(vcpu)
        assert "vCPU" in repr_str
        assert "MockLLMClient" in repr_str

    @pytest.mark.asyncio
    async def test_execute_simple_task(self):
        """Test executing a simple task without tool calls."""
        llm = MockLLMClient(response_text="Analysis complete")
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="analyst",
            task_instruction="Analyze this data",
            allowed_tools=set(),
        )
        proc.state = ProcessState.READY

        result = await vcpu.execute(proc)

        assert proc.state == ProcessState.COMPLETED
        assert proc.exit_code == 0
        assert result["text"] == "Analysis complete"
        assert llm.call_count == 1

    @pytest.mark.asyncio
    async def test_execute_with_system_prompt(self):
        """Test that system prompt is included in context."""
        llm = MockLLMClient()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="assistant",
            system_prompt="You are a helpful assistant.",
            task_instruction="Help me",
            allowed_tools=set(),
        )
        proc.state = ProcessState.READY

        await vcpu.execute(proc)

        # Check memory contains system prompt
        assert len(proc.memory) >= 2
        assert proc.memory[0]["role"] == "system"
        assert proc.memory[0]["content"] == "You are a helpful assistant."
        assert proc.memory[1]["role"] == "user"
        assert proc.memory[1]["content"] == "Help me"


# ============================================================================
# vCPU Tool Execution Tests
# ============================================================================


class TestVCPUToolExecution:
    """Test vCPU tool execution."""

    @pytest.mark.asyncio
    async def test_execute_with_tool_calls(self):
        """Test executing a task that requires tool calls."""
        llm = MockToolCallingLLM(
            tool_calls_sequence=[
                [{"name": "Echo", "arguments": {"message": "Hello"}}],
            ],
            final_response="Echoed the message",
        )
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="echo_bot",
            task_instruction="Echo hello",
            allowed_tools={"Echo"},
        )
        proc.state = ProcessState.READY

        result = await vcpu.execute(proc)

        assert proc.state == ProcessState.COMPLETED
        assert result["text"] == "Echoed the message"
        assert llm.call_count == 2  # First call returns tool, second returns final

        # Check tool result is in memory
        tool_messages = [m for m in proc.memory if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert "Echo: Hello" in tool_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self):
        """Test multiple sequential tool calls."""
        llm = MockToolCallingLLM(
            tool_calls_sequence=[
                [{"name": "Echo", "arguments": {"message": "First"}}],
                [{"name": "Add", "arguments": {"a": 1, "b": 2}}],
            ],
            final_response="Done with all tools",
        )
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="multi_tool",
            task_instruction="Do multiple things",
            allowed_tools={"Echo", "Add"},
        )
        proc.state = ProcessState.READY

        result = await vcpu.execute(proc)

        assert proc.state == ProcessState.COMPLETED
        assert llm.call_count == 3  # 2 tool rounds + 1 final

        tool_messages = [m for m in proc.memory if m.get("role") == "tool"]
        assert len(tool_messages) == 2

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_in_one_response(self):
        """Test handling multiple tool calls in a single response."""
        llm = MockToolCallingLLM(
            tool_calls_sequence=[
                [
                    {"name": "Echo", "arguments": {"message": "One"}},
                    {"name": "Echo", "arguments": {"message": "Two"}},
                ],
            ],
            final_response="Both echoed",
        )
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="parallel",
            task_instruction="Echo two things",
            allowed_tools={"Echo"},
        )
        proc.state = ProcessState.READY

        result = await vcpu.execute(proc)

        assert proc.state == ProcessState.COMPLETED

        tool_messages = [m for m in proc.memory if m.get("role") == "tool"]
        assert len(tool_messages) == 2

    @pytest.mark.asyncio
    async def test_tool_permission_denied(self):
        """Test that unauthorized tools are blocked."""
        llm = MockToolCallingLLM(
            tool_calls_sequence=[
                [{"name": "Echo", "arguments": {"message": "test"}}],
            ],
            final_response="Done",
        )
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="restricted",
            task_instruction="Try to echo",
            allowed_tools={"Add"},  # Echo not allowed!
        )
        proc.state = ProcessState.READY

        result = await vcpu.execute(proc)

        # Should complete but with permission error in tool result
        assert proc.state == ProcessState.COMPLETED

        tool_messages = [m for m in proc.memory if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert "Permission denied" in tool_messages[0]["content"]

    @pytest.mark.asyncio
    async def test_tool_execution_error(self):
        """Test handling of tool execution errors."""
        llm = MockToolCallingLLM(
            tool_calls_sequence=[
                [{"name": "Fail", "arguments": {"reason": "test failure"}}],
            ],
            final_response="Handled the error",
        )
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="error_handler",
            task_instruction="Try a failing tool",
            allowed_tools={"Fail"},
        )
        proc.state = ProcessState.READY

        result = await vcpu.execute(proc)

        # Should complete with error in tool result
        assert proc.state == ProcessState.COMPLETED

        tool_messages = [m for m in proc.memory if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert "Tool execution error" in tool_messages[0]["content"]


# ============================================================================
# vCPU Resource Limit Tests
# ============================================================================


class TestVCPUResourceLimits:
    """Test vCPU resource limit enforcement."""

    @pytest.mark.asyncio
    async def test_max_iterations_exceeded(self):
        """Test that max iterations limit is enforced."""
        llm = InfiniteLLM()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools, max_iterations=3)

        proc = AgentProcess.create(
            role="infinite",
            task_instruction="Loop forever",
            allowed_tools={"Echo"},
        )
        proc.state = ProcessState.READY

        with pytest.raises(MaxIterationsError) as exc_info:
            await vcpu.execute(proc)

        assert proc.state == ProcessState.FAILED
        assert "maximum iterations" in str(exc_info.value).lower()
        assert llm.call_count == 3

    @pytest.mark.asyncio
    async def test_token_budget_exceeded(self):
        """Test that token budget is enforced."""
        llm = MockLLMClient()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="budget_test",
            task_instruction="Do something",
            allowed_tools=set(),
            max_token_budget=10,  # Very small budget
        )
        proc.state = ProcessState.READY
        proc.token_usage = 10  # Already at limit

        with pytest.raises(ResourceLimitError) as exc_info:
            await vcpu.execute(proc)

        assert proc.state == ProcessState.FAILED
        assert "token budget" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_turn_limit_exceeded(self):
        """Test that turn limit is enforced."""
        llm = MockLLMClient()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="turn_test",
            task_instruction="Do something",
            allowed_tools=set(),
            max_turns=5,
        )
        proc.state = ProcessState.READY
        proc.current_turn = 5  # Already at limit

        with pytest.raises(ResourceLimitError) as exc_info:
            await vcpu.execute(proc)

        assert proc.state == ProcessState.FAILED
        assert "turn limit" in str(exc_info.value).lower()


# ============================================================================
# vCPU Error Handling Tests
# ============================================================================


class TestVCPUErrorHandling:
    """Test vCPU error handling."""

    @pytest.mark.asyncio
    async def test_process_not_runnable(self):
        """Test error when process is not in a runnable state."""
        llm = MockLLMClient()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(role="test")
        # proc.state is CREATED, not READY or RUNNING

        with pytest.raises(vCPUError) as exc_info:
            await vcpu.execute(proc)

        assert "not in a runnable state" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_process_already_running(self):
        """Test that vCPU accepts RUNNING state (for scheduler integration)."""
        llm = MockLLMClient(response_text="done")
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="test",
            task_instruction="Test",
            allowed_tools=set(),
        )
        proc.state = ProcessState.RUNNING  # Scheduler may set this before executor

        result = await vcpu.execute(proc)

        assert proc.state == ProcessState.COMPLETED
        assert result["text"] == "done"

    @pytest.mark.asyncio
    async def test_llm_error_handling(self):
        """Test handling of LLM errors."""

        class FailingLLM:
            async def complete(self, *args, **kwargs):
                raise RuntimeError("LLM is down")

            async def stream(self, *args, **kwargs):
                raise RuntimeError("LLM is down")

            async def complete_with_tools(self, *args, **kwargs):
                raise RuntimeError("LLM is down")

        llm = FailingLLM()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="test",
            task_instruction="Test",
            allowed_tools=set(),
        )
        proc.state = ProcessState.READY

        with pytest.raises(vCPUError) as exc_info:
            await vcpu.execute(proc)

        assert proc.state == ProcessState.FAILED
        assert "LLM is down" in str(exc_info.value)


# ============================================================================
# AgentOS Integration Tests
# ============================================================================


class TestAgentOSWithVCPU:
    """Test AgentOS integration with vCPU."""

    @pytest.mark.asyncio
    async def test_agentos_with_vcpu(self):
        """Test AgentOS with vCPU integration."""
        llm = MockLLMClient(response_text="Task done")
        tools = create_test_registry()

        kernel = AgentOS(llm_client=llm, tool_registry=tools)

        assert kernel.vcpu is not None
        assert kernel.llm_client == llm
        assert kernel.tool_registry == tools

    @pytest.mark.asyncio
    async def test_spawn_and_wait_with_vcpu(self):
        """Test spawn and wait with vCPU execution."""
        llm = MockLLMClient(response_text="Analysis complete")
        tools = create_test_registry()

        kernel = AgentOS(llm_client=llm, tool_registry=tools)

        pid = await kernel.spawn(
            role="analyst",
            goal="Analyze the data",
            allowed_tools=set(),
        )

        result = await kernel.wait(pid)

        assert result["exit_code"] == 0
        assert result["result"]["text"] == "Analysis complete"

    @pytest.mark.asyncio
    async def test_spawn_with_tools(self):
        """Test spawn with tool usage."""
        llm = MockToolCallingLLM(
            tool_calls_sequence=[
                [{"name": "Add", "arguments": {"a": 10, "b": 20}}],
            ],
            final_response="The sum is 30",
        )
        tools = create_test_registry()

        kernel = AgentOS(llm_client=llm, tool_registry=tools)

        pid = await kernel.spawn(
            role="calculator",
            goal="Calculate 10 + 20",
            allowed_tools={"Add"},
        )

        result = await kernel.wait(pid)

        assert result["exit_code"] == 0
        assert result["result"]["text"] == "The sum is 30"

    @pytest.mark.asyncio
    async def test_agentos_without_vcpu(self):
        """Test AgentOS without vCPU (mock execution)."""
        kernel = AgentOS()  # No LLM or tools

        assert kernel.vcpu is None

        # Should use mock executor
        pid = await kernel.spawn(role="test", goal="Test task")
        result = await kernel.wait(pid)

        # Mock executor completes immediately
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_multiple_processes(self):
        """Test running multiple processes."""
        llm = MockLLMClient()
        tools = create_test_registry()

        kernel = AgentOS(llm_client=llm, tool_registry=tools)

        # Spawn multiple processes
        pid1 = await kernel.spawn(role="worker1", goal="Task 1")
        pid2 = await kernel.spawn(role="worker2", goal="Task 2")

        # Wait for both
        result1 = await kernel.wait(pid1)
        result2 = await kernel.wait(pid2)

        assert result1["exit_code"] == 0
        assert result2["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_process_failure_propagates(self):
        """Test that process failures are properly reported."""
        llm = InfiniteLLM()
        tools = create_test_registry()

        kernel = AgentOS(
            llm_client=llm,
            tool_registry=tools,
            max_iterations=2,  # Force failure quickly
        )

        pid = await kernel.spawn(
            role="failer",
            goal="Fail task",
            allowed_tools={"Echo"},
        )

        result = await kernel.wait(pid)

        assert result["exit_code"] != 0
        assert result["error"] is not None
        assert "maximum iterations" in result["error"].lower()


# ============================================================================
# Memory Management Tests
# ============================================================================


class TestVCPUMemory:
    """Test vCPU memory management (MMU)."""

    @pytest.mark.asyncio
    async def test_memory_accumulation(self):
        """Test that memory accumulates across iterations."""
        llm = MockToolCallingLLM(
            tool_calls_sequence=[
                [{"name": "Echo", "arguments": {"message": "msg1"}}],
                [{"name": "Echo", "arguments": {"message": "msg2"}}],
            ],
            final_response="Done",
        )
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="memory_test",
            system_prompt="System",
            task_instruction="Task",
            allowed_tools={"Echo"},
        )
        proc.state = ProcessState.READY

        await vcpu.execute(proc)

        # Memory should contain:
        # 1. System prompt
        # 2. Task instruction (user)
        # 3. Assistant response 1 (tool call, no content)
        # 4. Tool result 1
        # 5. Assistant response 2 (tool call, no content)
        # 6. Tool result 2
        # At minimum 6 messages (final response doesn't add to memory since task completes)
        assert len(proc.memory) >= 6, f"Expected >= 6 messages, got {len(proc.memory)}: {proc.memory}"

        # Verify message roles
        roles = [m["role"] for m in proc.memory]
        assert roles[0] == "system"
        assert roles[1] == "user"
        assert "assistant" in roles
        assert "tool" in roles

    @pytest.mark.asyncio
    async def test_token_counting(self):
        """Test that tokens are counted."""
        llm = MockLLMClient()
        tools = create_test_registry()
        vcpu = vCPU(llm, tools)

        proc = AgentProcess.create(
            role="token_test",
            task_instruction="A" * 1000,  # Long message
            allowed_tools=set(),
        )
        proc.state = ProcessState.READY
        initial_tokens = proc.token_usage

        await vcpu.execute(proc)

        # Token usage should have increased
        assert proc.token_usage > initial_tokens
