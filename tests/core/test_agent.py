"""Tests for nimbus_next.agent — the AgentOS facade."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from nimbus.core.agent import AgentConfig, AgentOS, _register_default_tools
from nimbus.core.adapter import LLMResponse
from nimbus.core.tools.registry import ToolRegistry, ToolParameter, tool


# =============================================================================
# Mock Adapter
# =============================================================================


class MockAdapter:
    """Predictable mock LLM that returns predefined responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools, on_chunk=None):
        if self._idx >= len(self._responses):
            return LLMResponse(content="Done.")
        r = self._responses[self._idx]
        self._idx += 1
        return r


# =============================================================================
# Tests
# =============================================================================


class TestAgentConfig:
    def test_defaults(self):
        c = AgentConfig()
        assert c.model == "gpt-4o"
        assert c.provider == "openai"
        assert c.max_iterations == 200

    def test_anthropic_config(self):
        c = AgentConfig(provider="anthropic", model="claude-sonnet-4-20250514")
        assert c.provider == "anthropic"


class TestAgentOSAssembly:
    def test_creates_with_defaults(self):
        """AgentOS can be created with all defaults."""
        adapter = MockAdapter([LLMResponse(content="hello")])
        agent = AgentOS(adapter=adapter)
        assert agent.registry is not None
        assert len(agent.registry) >= 5  # Read, Write, Edit, Bash, Grep

    def test_creates_with_custom_tools(self):
        """AgentOS can use a custom tool registry."""
        registry = ToolRegistry()

        @tool("MyTool", "A custom tool", [
            ToolParameter(name="x", type="string", description="input"),
        ])
        def my_tool(x: str) -> str:
            return f"got {x}"

        registry.register_decorated(my_tool)

        adapter = MockAdapter([LLMResponse(content="hello")])
        agent = AgentOS(adapter=adapter, tools=registry)
        assert "MyTool" in agent.registry

    def test_register_tool(self):
        """Can register tools after construction."""
        adapter = MockAdapter([LLMResponse(content="hi")])
        agent = AgentOS(adapter=adapter)

        @tool("Extra", "An extra tool")
        def extra():
            return "extra"

        agent.register_tool(extra)
        assert "Extra" in agent.registry


class TestAgentOSRun:
    @pytest.mark.asyncio
    async def test_simple_run(self):
        """Agent runs a simple task that completes immediately."""
        adapter = MockAdapter([
            LLMResponse(content="Task complete! The files are listed."),
        ])
        agent = AgentOS(
            config=AgentConfig(text_is_final=True),
            adapter=adapter,
        )
        result = await agent.run("List files")
        assert result.status == "OK"

    @pytest.mark.asyncio
    async def test_run_with_tool_call(self):
        """Agent calls a tool then completes."""
        adapter = MockAdapter([
            LLMResponse(tool_calls=[{
                "id": "tc1", "type": "function",
                "function": {"name": "Bash", "arguments": '{"command": "echo hello"}'},
            }]),
            LLMResponse(content="Done! Command output: hello"),
        ])
        agent = AgentOS(
            config=AgentConfig(text_is_final=True),
            adapter=adapter,
        )
        result = await agent.run("Run echo hello")
        assert result.status == "OK"

    @pytest.mark.asyncio
    async def test_chat_returns_string(self):
        """chat() returns a string response."""
        adapter = MockAdapter([
            LLMResponse(content="Hello! How can I help?"),
        ])
        agent = AgentOS(adapter=adapter)
        response = await agent.chat("Hi there")
        assert isinstance(response, str)
        assert "Hello" in response

    @pytest.mark.asyncio
    async def test_stream_yields_events(self):
        """stream() yields step events."""
        adapter = MockAdapter([
            LLMResponse(content="All done."),
        ])
        agent = AgentOS(
            config=AgentConfig(text_is_final=True),
            adapter=adapter,
        )
        events = []
        async for event in agent.stream("Do something"):
            events.append(event)
        assert len(events) >= 1
        assert any(e.get("type") == "final" for e in events)


class TestAgentOSEventCallback:
    @pytest.mark.asyncio
    async def test_events_emitted(self):
        """Event callback receives events during execution."""
        events = []
        adapter = MockAdapter([
            LLMResponse(tool_calls=[{
                "id": "tc1", "type": "function",
                "function": {"name": "Bash", "arguments": '{"command": "echo test"}'},
            }]),
            LLMResponse(content="Done."),
        ])
        agent = AgentOS(
            config=AgentConfig(text_is_final=True),
            adapter=adapter,
            event_callback=lambda e: events.append(e),
        )
        await agent.run("Test events")
        # Gate should emit TOOL_STARTED and TOOL_FINISHED
        assert any(e.type == "TOOL_STARTED" for e in events)
        assert any(e.type == "TOOL_FINISHED" for e in events)


class TestDefaultToolRegistration:
    def test_all_default_tools_registered(self):
        """All 5 default tools are registered."""
        registry = ToolRegistry()
        _register_default_tools(registry)
        tools = registry.list_tools()
        assert "Read" in tools
        assert "Write" in tools
        assert "Edit" in tools
        assert "Bash" in tools
        assert "Grep" in tools
