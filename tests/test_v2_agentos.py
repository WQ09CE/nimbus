"""
Tests for Nimbus v2 AgentOS

Tests cover:
1. AgentOS initialization and configuration
2. Tool registration and execution
3. Process management (spawn, wait, kill)
4. Simple goal execution (run)
5. DAG execution (run_dag)
6. Event collection
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest

from nimbus.agentos import (
    AgentOS,
    AgentOSConfig,
    ToolRegistry,
    create_agent_os,
)
from nimbus.core.protocol import Fault
from nimbus.core.scheduler import Task, TaskSpec, create_dag, create_linear_dag

# =============================================================================
# Mock LLM Client
# =============================================================================


@dataclass
class MockToolCall:
    """Mock tool call for testing."""

    function: Any

    @dataclass
    class Function:
        name: str
        arguments: str


@dataclass
class MockLLMResponse:
    """Mock LLM response for testing."""

    content: Optional[str] = None
    tool_calls: Optional[List[Any]] = None


class MockLLMClient:
    """
    Mock LLM client for testing.

    Supports scripted responses for predictable testing.
    """

    def __init__(self, responses: Optional[List[MockLLMResponse]] = None):
        self.responses = responses or []
        self.call_count = 0
        self.messages_received: List[List[Dict]] = []

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> MockLLMResponse:
        """Return next scripted response."""
        self.messages_received.append(messages)

        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response

        # Default: return task complete
        return MockLLMResponse(
            tool_calls=[
                MockToolCall(
                    function=MockToolCall.Function(
                        name="return_result", arguments='{"result": "Task completed"}'
                    )
                )
            ]
        )

    def add_response(self, response: MockLLMResponse) -> None:
        """Add a response to the queue."""
        self.responses.append(response)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_llm():
    """Create a mock LLM client."""
    return MockLLMClient()


@pytest.fixture
def simple_tools():
    """Create simple test tools."""

    def echo(text: str) -> str:
        return f"Echo: {text}"

    async def async_echo(text: str) -> str:
        await asyncio.sleep(0.01)
        return f"Async Echo: {text}"

    def add(a: int, b: int) -> int:
        return a + b

    return {
        "echo": echo,
        "async_echo": async_echo,
        "add": add,
    }


@pytest.fixture
def agent_os(mock_llm, simple_tools):
    """Create an AgentOS instance for testing."""
    return AgentOS(llm_client=mock_llm, tools=simple_tools)


# =============================================================================
# Test: Initialization
# =============================================================================


class TestAgentOSInit:
    """Tests for AgentOS initialization."""

    def test_create_with_defaults(self, mock_llm):
        """Test creating AgentOS with default configuration."""
        os = AgentOS(llm_client=mock_llm)

        assert os.config.max_processes == 10
        assert os.config.default_timeout == 300.0
        assert os.list_tools() == []

    def test_create_with_config(self, mock_llm):
        """Test creating AgentOS with custom configuration."""
        config = AgentOSConfig(
            max_processes=5,
            default_timeout=60.0,
            system_rules="Custom rules",
        )
        os = AgentOS(llm_client=mock_llm, config=config)

        assert os.config.max_processes == 5
        assert os.config.default_timeout == 60.0
        assert os.config.system_rules == "Custom rules"

    def test_create_with_tools(self, mock_llm, simple_tools):
        """Test creating AgentOS with initial tools."""
        os = AgentOS(llm_client=mock_llm, tools=simple_tools)

        assert "echo" in os.list_tools()
        assert "async_echo" in os.list_tools()
        assert "add" in os.list_tools()

    def test_factory_function(self, mock_llm, simple_tools):
        """Test create_agent_os factory function."""
        os = create_agent_os(
            llm_client=mock_llm,
            tools=simple_tools,
            system_rules="Test rules",
            max_processes=3,
            default_timeout=30.0,
        )

        assert os.config.max_processes == 3
        assert os.config.default_timeout == 30.0
        assert os.config.system_rules == "Test rules"


# =============================================================================
# Test: Tool Registry
# =============================================================================


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_register_tool(self):
        """Test registering a tool."""
        registry = ToolRegistry()
        registry.register("test", lambda x: x, description="Test tool")

        assert "test" in registry.list_tools()
        assert registry.get("test") is not None

    def test_unregister_tool(self):
        """Test unregistering a tool."""
        registry = ToolRegistry()
        registry.register("test", lambda x: x)

        assert registry.unregister("test") is True
        assert "test" not in registry.list_tools()

    def test_unregister_nonexistent(self):
        """Test unregistering a tool that doesn't exist."""
        registry = ToolRegistry()
        assert registry.unregister("nonexistent") is False

    @pytest.mark.asyncio
    async def test_execute_sync_tool(self):
        """Test executing a synchronous tool."""
        registry = ToolRegistry()
        registry.register("add", lambda a, b: a + b)

        result = await registry.execute("add", {"a": 2, "b": 3})
        assert result == 5

    @pytest.mark.asyncio
    async def test_execute_async_tool(self):
        """Test executing an asynchronous tool."""

        async def async_add(a: int, b: int) -> int:
            await asyncio.sleep(0.01)
            return a + b

        registry = ToolRegistry()
        registry.register("async_add", async_add)

        result = await registry.execute("async_add", {"a": 2, "b": 3})
        assert result == 5

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self):
        """Test executing a tool that doesn't exist."""
        registry = ToolRegistry()

        with pytest.raises(Fault) as exc_info:
            await registry.execute("nonexistent", {})

        assert exc_info.value.code == "TOOL_NOT_FOUND"

    def test_get_tool_definitions(self):
        """Test getting tool definitions for LLM."""
        registry = ToolRegistry()
        registry.register(
            "test",
            lambda x: x,
            description="Test tool",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        )

        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "test"
        assert defs[0]["function"]["description"] == "Test tool"


# =============================================================================
# Test: Tool Management via AgentOS
# =============================================================================


class TestAgentOSToolManagement:
    """Tests for tool management via AgentOS."""

    def test_register_tool(self, mock_llm):
        """Test registering a tool via AgentOS."""
        os = AgentOS(llm_client=mock_llm)
        os.register_tool("test", lambda x: x, description="Test")

        assert "test" in os.list_tools()

    def test_unregister_tool(self, mock_llm, simple_tools):
        """Test unregistering a tool via AgentOS."""
        os = AgentOS(llm_client=mock_llm, tools=simple_tools)

        assert os.unregister_tool("echo") is True
        assert "echo" not in os.list_tools()


# =============================================================================
# Test: Process Management
# =============================================================================


class TestProcessManagement:
    """Tests for process management."""

    def test_spawn_process(self, agent_os):
        """Test spawning a process."""
        pid = agent_os.spawn("Test goal")

        assert pid is not None
        assert pid.startswith("proc-")
        assert pid in agent_os.list_processes()

    def test_spawn_with_role(self, agent_os):
        """Test spawning a process with a role."""
        pid = agent_os.spawn("Explore code", role="eye")

        process = agent_os.get_process(pid)
        assert process is not None
        assert process.role == "eye"

    def test_spawn_limit(self, mock_llm):
        """Test process limit enforcement."""
        config = AgentOSConfig(max_processes=2)
        os = AgentOS(llm_client=mock_llm, config=config)

        # Spawn 2 processes
        pid1 = os.spawn("Task 1")
        pid2 = os.spawn("Task 2")

        # Start them (move to RUNNING state)
        proc1 = os.get_process(pid1)
        proc2 = os.get_process(pid2)
        proc1.state = "RUNNING"
        proc2.state = "RUNNING"

        # Third spawn should fail
        with pytest.raises(RuntimeError) as exc_info:
            os.spawn("Task 3")

        assert "Process limit reached" in str(exc_info.value)

    def test_get_process(self, agent_os):
        """Test getting a process by PID."""
        pid = agent_os.spawn("Test goal")

        process = agent_os.get_process(pid)
        assert process is not None
        assert process.goal == "Test goal"

    def test_get_process_not_found(self, agent_os):
        """Test getting a nonexistent process."""
        process = agent_os.get_process("nonexistent")
        assert process is None

    def test_list_processes(self, agent_os):
        """Test listing all processes."""
        pid1 = agent_os.spawn("Task 1")
        pid2 = agent_os.spawn("Task 2")

        processes = agent_os.list_processes()
        assert pid1 in processes
        assert pid2 in processes

    def test_kill_process(self, agent_os):
        """Test killing a process."""
        pid = agent_os.spawn("Test goal")

        # Mark as running (normally done by wait)
        process = agent_os.get_process(pid)
        process.state = "RUNNING"

        result = agent_os.kill(pid)
        assert result is True

        process = agent_os.get_process(pid)
        assert process.state == "CANCELLED"

    def test_kill_completed_process(self, agent_os):
        """Test killing an already completed process."""
        pid = agent_os.spawn("Test goal")

        process = agent_os.get_process(pid)
        process.state = "SUCCEEDED"

        result = agent_os.kill(pid)
        assert result is False  # Can't kill completed process

    def test_kill_nonexistent_process(self, agent_os):
        """Test killing a nonexistent process."""
        result = agent_os.kill("nonexistent")
        assert result is False


# =============================================================================
# Test: Simple Goal Execution
# =============================================================================


class TestSimpleExecution:
    """Tests for simple goal execution."""

    @pytest.mark.asyncio
    async def test_run_simple_goal(self, mock_llm, simple_tools):
        """Test running a simple goal."""
        # Setup mock to return completion immediately
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="return_result", arguments='{"result": "Done!"}'
                        )
                    )
                ]
            )
        )

        os = AgentOS(llm_client=mock_llm, tools=simple_tools)
        result = await os.run("Complete this task")

        assert result.status == "OK"
        assert result.is_final is True

    @pytest.mark.asyncio
    async def test_run_with_tool_call(self, mock_llm, simple_tools):
        """Test running a goal that uses a tool."""
        # First call: execute tool
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(name="echo", arguments='{"text": "hello"}')
                    )
                ]
            )
        )
        # Second call: return result
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="return_result", arguments='{"result": "Echo completed"}'
                        )
                    )
                ]
            )
        )

        os = AgentOS(llm_client=mock_llm, tools=simple_tools)
        result = await os.run("Echo hello")

        assert result.status == "OK"
        assert result.is_final is True

    @pytest.mark.asyncio
    async def test_run_with_role(self, mock_llm, simple_tools):
        """Test running a goal with a specific role."""
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="return_result", arguments='{"result": "Explored"}'
                        )
                    )
                ]
            )
        )

        os = AgentOS(llm_client=mock_llm, tools=simple_tools)
        result = await os.run("Explore codebase", role="eye")

        assert result.status == "OK"


# =============================================================================
# Test: Wait for Process
# =============================================================================


class TestWaitProcess:
    """Tests for waiting on processes."""

    @pytest.mark.asyncio
    async def test_wait_pending_process(self, mock_llm, simple_tools):
        """Test waiting on a pending process starts it."""
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="return_result", arguments='{"result": "Done"}'
                        )
                    )
                ]
            )
        )

        os = AgentOS(llm_client=mock_llm, tools=simple_tools)
        pid = os.spawn("Test goal")

        # Process should be pending
        process = os.get_process(pid)
        assert process.state == "PENDING"

        # Wait should start and complete it
        result = await os.wait(pid)

        assert result.status == "OK"
        process = os.get_process(pid)
        assert process.state == "SUCCEEDED"

    @pytest.mark.asyncio
    async def test_wait_nonexistent_process(self, mock_llm):
        """Test waiting on a nonexistent process."""
        os = AgentOS(llm_client=mock_llm)

        result = await os.wait("nonexistent")

        assert result.status == "ERROR"
        assert result.fault is not None
        assert "not found" in result.fault.message


# =============================================================================
# Test: DAG Execution
# =============================================================================


class TestDAGExecution:
    """Tests for DAG execution."""

    @pytest.mark.asyncio
    async def test_run_simple_dag(self, mock_llm, simple_tools):
        """Test running a simple single-task DAG."""
        # Return result for the single task
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="return_result", arguments='{"result": "Step completed"}'
                        )
                    )
                ]
            )
        )

        os = AgentOS(llm_client=mock_llm, tools=simple_tools)

        dag = create_dag([Task(id="t1", spec=TaskSpec(goal="Step 1"))])

        result = await os.run_dag(dag)

        assert result.status == "OK"

    @pytest.mark.asyncio
    async def test_run_linear_dag(self, mock_llm, simple_tools):
        """Test running a linear DAG with dependencies."""
        # Responses for each task in the linear DAG
        for _ in range(3):
            mock_llm.add_response(
                MockLLMResponse(
                    tool_calls=[
                        MockToolCall(
                            function=MockToolCall.Function(
                                name="return_result", arguments='{"result": "Step completed"}'
                            )
                        )
                    ]
                )
            )

        os = AgentOS(llm_client=mock_llm, tools=simple_tools)

        dag = create_linear_dag(["Step 1", "Step 2", "Step 3"])

        result = await os.run_dag(dag)

        assert result.status == "OK"


# =============================================================================
# Test: Event Collection
# =============================================================================


class TestEventCollection:
    """Tests for event collection."""

    @pytest.mark.asyncio
    async def test_events_collected(self, mock_llm, simple_tools):
        """Test that events are collected during execution."""
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="return_result", arguments='{"result": "Done"}'
                        )
                    )
                ]
            )
        )

        os = AgentOS(llm_client=mock_llm, tools=simple_tools)
        await os.run("Test goal")

        events = os.get_events()
        assert len(events) > 0

        # Should have PROC_SPAWNED event
        event_types = [e.type for e in events]
        assert "PROC_SPAWNED" in event_types

    def test_clear_events(self, agent_os):
        """Test clearing events."""
        # Spawn to generate events
        agent_os.spawn("Test")

        assert len(agent_os.get_events()) > 0

        agent_os.clear_events()

        assert len(agent_os.get_events()) == 0


# =============================================================================
# Test: State Access
# =============================================================================


class TestStateAccess:
    """Tests for state access."""

    def test_get_state(self, mock_llm, simple_tools):
        """Test getting AgentOS state."""
        os = AgentOS(llm_client=mock_llm, tools=simple_tools)
        os.spawn("Task 1")
        os.spawn("Task 2")

        state = os.get_state()

        assert state["config"]["max_processes"] == 10
        assert len(state["processes"]) == 2
        assert "echo" in state["tools"]
        assert state["event_count"] >= 2  # At least spawn events

    def test_get_active_processes(self, agent_os):
        """Test getting active (running) processes."""
        pid1 = agent_os.spawn("Task 1")
        pid2 = agent_os.spawn("Task 2")

        # Mark one as running
        proc1 = agent_os.get_process(pid1)
        proc1.state = "RUNNING"

        active = agent_os.get_active_processes()

        assert pid1 in active
        assert pid2 not in active  # Still pending


# =============================================================================
# Test: Error Handling
# =============================================================================


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_llm_error_handling(self, simple_tools):
        """Test handling LLM errors."""

        class FailingLLM:
            async def chat(self, messages, tools=None):
                raise RuntimeError("LLM connection failed")

        os = AgentOS(llm_client=FailingLLM(), tools=simple_tools)
        result = await os.run("Test goal")

        assert result.status == "ERROR"
        assert result.fault is not None

    @pytest.mark.asyncio
    async def test_tool_error_handling(self, mock_llm):
        """Test handling tool execution errors."""

        def failing_tool():
            raise ValueError("Tool failed")

        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(name="failing_tool", arguments="{}")
                    )
                ]
            )
        )
        # Return after error
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="return_result", arguments='{"result": "Recovered"}'
                        )
                    )
                ]
            )
        )

        os = AgentOS(llm_client=mock_llm, tools={"failing_tool": failing_tool})
        result = await os.run("Test goal")

        # Should complete (agent can recover from tool errors)
        assert result.status == "OK"


# =============================================================================
# Test: Integration
# =============================================================================


class TestIntegration:
    """Integration tests for AgentOS."""

    @pytest.mark.asyncio
    async def test_multi_process_execution(self, mock_llm, simple_tools):
        """Test running multiple processes."""
        # Add responses for two processes
        for _ in range(2):
            mock_llm.add_response(
                MockLLMResponse(
                    tool_calls=[
                        MockToolCall(
                            function=MockToolCall.Function(
                                name="return_result", arguments='{"result": "Done"}'
                            )
                        )
                    ]
                )
            )

        os = AgentOS(llm_client=mock_llm, tools=simple_tools)

        # Run two tasks concurrently
        results = await asyncio.gather(
            os.run("Task 1"),
            os.run("Task 2"),
        )

        assert all(r.status == "OK" for r in results)

    @pytest.mark.asyncio
    async def test_tool_execution_in_context(self, mock_llm, simple_tools):
        """Test that tools can access process context."""
        results_captured = []

        def capture_tool(data: str) -> str:
            results_captured.append(data)
            return f"Captured: {data}"

        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="capture", arguments='{"data": "test_data"}'
                        )
                    )
                ]
            )
        )
        mock_llm.add_response(
            MockLLMResponse(
                tool_calls=[
                    MockToolCall(
                        function=MockToolCall.Function(
                            name="return_result", arguments='{"result": "Done"}'
                        )
                    )
                ]
            )
        )

        os = AgentOS(llm_client=mock_llm, tools={"capture": capture_tool})
        await os.run("Capture test")

        assert "test_data" in results_captured
