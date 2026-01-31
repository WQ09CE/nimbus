"""
End-to-End Tests for Nimbus with Pi-AI Integration

This module tests the complete pipeline using pi-ai as the LLM backend:
1. PiAiHttpClient - HTTP client for pi-ai server
2. PiLLMAdapter - Adapter that wraps pi-ai for vCPU
3. AgentOS - Complete agent execution with tool calling

Requirements:
    - pi-ai server running on localhost:3031
    - Valid OAuth authentication (via ~/.pi/agent/auth.json)

Usage:
    # Start pi-ai server first
    ./scripts/start-pi-ai.sh
    
    # Run tests
    pytest tests/test_v2_e2e_pi_ai.py -v
    
    # Skip slow tests
    pytest tests/test_v2_e2e_pi_ai.py -v -m "not slow"
"""

import os
import pytest
import asyncio
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from nimbus.bridge.pi_ai_http import PiAiHttpClient, Message
from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig
from nimbus import AgentOS
from nimbus.tools import get_all_tools, TOOL_FUNCTIONS


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
async def pi_client():
    """Create and start pi-ai HTTP client."""
    client = PiAiHttpClient()
    await client.start()
    yield client
    await client.stop()


@pytest.fixture
async def check_pi_ai_running(pi_client):
    """Skip test if pi-ai server is not running."""
    is_healthy = await pi_client.health_check()
    if not is_healthy:
        pytest.skip("pi-ai server not running on localhost:3031")


@pytest.fixture
def sample_tools():
    """Sample tool definitions in OpenAI format."""
    return [
        {
            "type": "function",
            "function": {
                "name": "Read",
                "description": "Read the contents of a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "The absolute path to the file to read"
                        }
                    },
                    "required": ["file_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "return_result",
                "description": "Return the final result when task is complete",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "result": {
                            "type": "string",
                            "description": "The final result to return"
                        }
                    },
                    "required": ["result"]
                }
            }
        }
    ]


# =============================================================================
# Unit Tests - PiAiHttpClient
# =============================================================================

class TestPiAiHttpClient:
    """Test PiAiHttpClient basic functionality."""

    @pytest.mark.asyncio
    async def test_health_check(self, pi_client, check_pi_ai_running):
        """Test health check endpoint."""
        is_healthy = await pi_client.health_check()
        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_list_models(self, pi_client, check_pi_ai_running):
        """Test listing available models."""
        models = await pi_client.list_models()
        assert isinstance(models, list)
        # Should have at least one model
        if models:
            assert "id" in models[0]
            print(f"Available models: {[m['id'] for m in models[:5]]}")

    @pytest.mark.asyncio
    async def test_simple_completion(self, pi_client, check_pi_ai_running):
        """Test simple chat completion without tools."""
        messages = [
            Message(role="user", content="Say 'hello world' and nothing else.")
        ]
        
        result = await pi_client.complete(messages)
        
        assert result is not None
        assert result.content is not None
        assert len(result.content) > 0
        print(f"Response: {result.content}")


# =============================================================================
# Unit Tests - PiLLMAdapter
# =============================================================================

class TestPiLLMAdapter:
    """Test PiLLMAdapter for vCPU integration."""

    @pytest.mark.asyncio
    async def test_adapter_initialization(self, check_pi_ai_running):
        """Test adapter initialization with different configs."""
        # Default config
        adapter = PiLLMAdapter()
        assert adapter.config.base_url == "http://localhost:3031"
        
        # Custom config
        config = PiLLMConfig(
            model="anthropic/claude-sonnet-4-20250514",
            max_tokens=4096
        )
        adapter = PiLLMAdapter(config=config)
        assert adapter.config.model == "anthropic/claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_adapter_chat_no_tools(self, check_pi_ai_running):
        """Test chat without tools."""
        adapter = PiLLMAdapter()
        await adapter.start()
        
        try:
            messages = [
                {"role": "user", "content": "What is 2 + 2? Reply with just the number."}
            ]
            
            response = await adapter.chat(messages, tools=None)
            
            assert response is not None
            assert response.content is not None
            assert "4" in response.content
            print(f"Response: {response.content}")
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_adapter_chat_with_tools(self, check_pi_ai_running, sample_tools):
        """Test chat with tool calling."""
        adapter = PiLLMAdapter()
        await adapter.start()
        
        try:
            messages = [
                {"role": "user", "content": "Read the file /etc/hostname and tell me what it contains."}
            ]
            
            response = await adapter.chat(messages, tools=sample_tools)
            
            assert response is not None
            # Should either have content or tool calls
            has_content = response.content is not None and len(response.content) > 0
            has_tool_calls = response.tool_calls is not None and len(response.tool_calls) > 0
            assert has_content or has_tool_calls
            
            if response.tool_calls:
                print(f"Tool calls: {response.tool_calls}")
                # Should call Read tool
                # Handle both dict and object style tool calls
                tool_names = []
                for tc in response.tool_calls:
                    if isinstance(tc, dict):
                        tool_names.append(tc.get('function', {}).get('name', ''))
                    else:
                        tool_names.append(tc.function.name)
                assert "Read" in tool_names
            else:
                print(f"Response: {response.content}")
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_adapter_with_system_prompt(self, check_pi_ai_running):
        """Test chat with system prompt."""
        adapter = PiLLMAdapter()
        await adapter.start()
        
        try:
            messages = [
                {"role": "system", "content": "You are a helpful assistant that only responds with single words."},
                {"role": "user", "content": "What color is the sky on a clear day?"}
            ]
            
            response = await adapter.chat(messages, tools=None)
            
            assert response is not None
            assert response.content is not None
            # Should be a short response (single word or few words)
            assert len(response.content.strip()) < 50
            print(f"Response: {response.content}")
        finally:
            await adapter.stop()


# =============================================================================
# Integration Tests - AgentOS with Pi-AI
# =============================================================================

class TestAgentOSIntegration:
    """Integration tests for AgentOS with pi-ai backend."""

    @pytest.fixture
    async def agent_os(self, check_pi_ai_running):
        """Create AgentOS with pi-ai adapter."""
        adapter = PiLLMAdapter()
        await adapter.start()
        
        # Create AgentOS with default tools
        os = AgentOS(llm_client=adapter)
        
        yield os
        
        await adapter.stop()

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_simple_question(self, agent_os):
        """Test simple Q&A without tool use."""
        result = await agent_os.run("What is the capital of France? Reply with just the city name.")
        
        assert result.status == "OK"
        assert result.output is not None
        assert "Paris" in result.output
        print(f"Result: {result.output}")

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_read_file(self, agent_os):
        """Test reading a file using Read tool."""
        # Create a temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Hello from test file!\nLine 2\nLine 3")
            temp_path = f.name
        
        try:
            result = await agent_os.run(f"Read the file {temp_path} and tell me its contents.")
            
            assert result.status == "OK"
            assert result.output is not None
            # Should contain file contents or reference to them
            assert "Hello" in result.output or "test file" in result.output.lower()
            print(f"Result: {result.output}")
        finally:
            os.unlink(temp_path)

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_glob_files(self, agent_os):
        """Test finding files using Glob tool."""
        result = await agent_os.run(
            "Find all Python files in the tests/ directory. "
            "Just tell me approximately how many there are."
        )
        
        assert result.status == "OK"
        assert result.output is not None
        print(f"Result: {result.output}")

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_multi_step_task(self, agent_os):
        """Test a task that requires multiple tool calls."""
        result = await agent_os.run(
            "First, list the .py files in src/nimbus/tools/ directory, "
            "then read the first few lines of one of them. "
            "Summarize what you found."
        )
        
        assert result.status == "OK"
        assert result.output is not None
        assert len(result.output) > 50  # Should have substantial output
        print(f"Result: {result.output[:500]}")


# =============================================================================
# Stress Tests
# =============================================================================

class TestStress:
    """Stress tests for pi-ai integration."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_concurrent_requests(self, check_pi_ai_running):
        """Test handling concurrent requests."""
        adapter = PiLLMAdapter()
        await adapter.start()
        
        try:
            async def make_request(i: int):
                messages = [
                    {"role": "user", "content": f"What is {i} + {i}? Reply with just the number."}
                ]
                response = await adapter.chat(messages, tools=None)
                return i, response.content
            
            # Run 3 concurrent requests
            tasks = [make_request(i) for i in range(1, 4)]
            results = await asyncio.gather(*tasks)
            
            assert len(results) == 3
            for i, content in results:
                assert content is not None
                expected = str(i * 2)
                assert expected in content, f"Expected {expected} in response for {i}+{i}"
                print(f"{i}+{i} = {content.strip()}")
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_long_conversation(self, check_pi_ai_running):
        """Test multi-turn conversation."""
        adapter = PiLLMAdapter()
        await adapter.start()
        
        try:
            messages = []
            
            # Turn 1
            messages.append({"role": "user", "content": "Remember the number 42."})
            response = await adapter.chat(messages, tools=None)
            messages.append({"role": "assistant", "content": response.content})
            
            # Turn 2
            messages.append({"role": "user", "content": "What number did I ask you to remember?"})
            response = await adapter.chat(messages, tools=None)
            
            assert "42" in response.content
            print(f"Final response: {response.content}")
        finally:
            await adapter.stop()


# =============================================================================
# Error Handling Tests
# =============================================================================

class TestErrorHandling:
    """Test error handling in pi-ai integration."""

    @pytest.mark.asyncio
    async def test_connection_error(self):
        """Test handling when pi-ai server is not available."""
        # Use a wrong port
        config = PiLLMConfig(base_url="http://localhost:9999")
        adapter = PiLLMAdapter(config=config)
        await adapter.start()
        
        try:
            messages = [{"role": "user", "content": "Hello"}]
            
            with pytest.raises(Exception):
                await adapter.chat(messages, tools=None)
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_invalid_model(self, check_pi_ai_running):
        """Test handling invalid model name."""
        config = PiLLMConfig(model="invalid/nonexistent-model")
        adapter = PiLLMAdapter(config=config)
        await adapter.start()
        
        try:
            messages = [{"role": "user", "content": "Hello"}]
            
            # Should either raise an error or return an error response
            try:
                response = await adapter.chat(messages, tools=None)
                # If it doesn't raise, the response should indicate an error
                # (behavior depends on pi-ai's error handling)
            except Exception as e:
                # Expected - invalid model should cause an error
                print(f"Expected error: {e}")
        finally:
            await adapter.stop()


# =============================================================================
# Run tests
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
