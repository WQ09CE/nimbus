"""Tests for nimbus_next.adapter — LLM adapters."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nimbus_next.adapter import (
    AdapterConfig,
    AnthropicAdapter,
    LLMResponse,
    OpenAIAdapter,
)
from nimbus_next.protocol import Fault


class TestLLMResponse:
    def test_text_response(self):
        r = LLMResponse(content="hello world")
        assert r.content == "hello world"
        assert r.tool_calls is None

    def test_tool_call_response(self):
        tc = [{"id": "tc1", "type": "function", "function": {"name": "Read", "arguments": "{}"}}]
        r = LLMResponse(tool_calls=tc)
        assert r.content is None
        assert len(r.tool_calls) == 1


class TestAdapterConfig:
    def test_defaults(self):
        c = AdapterConfig()
        assert c.model == "gpt-4o"
        assert c.temperature == 0.0

    def test_custom(self):
        c = AdapterConfig(model="claude-sonnet-4-20250514", max_tokens=8192)
        assert c.model == "claude-sonnet-4-20250514"
        assert c.max_tokens == 8192


class TestAnthropicAdapter:
    def test_convert_user_message(self):
        adapter = AnthropicAdapter()
        msg = {"role": "user", "content": "hello"}
        result = adapter._convert_message(msg)
        assert result == {"role": "user", "content": "hello"}

    def test_convert_tool_result(self):
        adapter = AnthropicAdapter()
        msg = {"role": "tool", "content": "file contents", "tool_call_id": "tc1"}
        result = adapter._convert_message(msg)
        assert result["role"] == "user"
        assert result["content"][0]["type"] == "tool_result"
        assert result["content"][0]["tool_use_id"] == "tc1"

    def test_convert_assistant_with_tool_calls(self):
        adapter = AnthropicAdapter()
        msg = {
            "role": "assistant",
            "content": "Let me check.",
            "tool_calls": [{
                "id": "tc1", "type": "function",
                "function": {"name": "Read", "arguments": '{"file_path": "x"}'},
            }],
        }
        result = adapter._convert_message(msg)
        assert result["role"] == "assistant"
        # Should have text block + tool_use block
        assert len(result["content"]) == 2
        assert result["content"][0]["type"] == "text"
        assert result["content"][1]["type"] == "tool_use"
        assert result["content"][1]["name"] == "Read"
        assert result["content"][1]["input"] == {"file_path": "x"}

    def test_convert_plain_assistant(self):
        adapter = AnthropicAdapter()
        msg = {"role": "assistant", "content": "Sure!"}
        result = adapter._convert_message(msg)
        assert result == {"role": "assistant", "content": "Sure!"}
