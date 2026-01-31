"""Tests for Gemini LLM client."""

import asyncio
import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nimbus.llm import GeminiClient
from nimbus.llm.gemini import GeminiConfig, GeminiError


class TestGeminiConfig:
    """Tests for GeminiConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = GeminiConfig()
        assert config.model == "gemini-2.0-flash"
        assert config.temperature == 0.7
        assert config.max_output_tokens == 8192
        assert config.max_retries == 3

    def test_custom_config(self):
        """Test custom configuration values."""
        config = GeminiConfig(
            model="gemini-pro",
            temperature=0.5,
            max_output_tokens=4096,
        )
        assert config.model == "gemini-pro"
        assert config.temperature == 0.5
        assert config.max_output_tokens == 4096


class TestGeminiClient:
    """Tests for GeminiClient."""

    def test_init_with_api_key(self):
        """Test initialization with API key."""
        client = GeminiClient(api_key="test-key")
        assert client._api_key == "test-key"
        assert client.config.model == "gemini-2.0-flash"

    def test_init_with_env_var(self, monkeypatch):
        """Test initialization with environment variable."""
        monkeypatch.setenv("GEMINI_API_KEY", "env-test-key")
        client = GeminiClient()
        assert client._api_key == "env-test-key"

    def test_init_without_key_raises(self, monkeypatch):
        """Test initialization without API key raises error."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key is required"):
            GeminiClient()

    def test_format_message_user(self):
        """Test formatting user message."""
        client = GeminiClient(api_key="test-key")
        msg = client._format_message("user", "Hello")
        assert msg == {
            "role": "user",
            "parts": [{"text": "Hello"}],
        }

    def test_format_message_assistant(self):
        """Test formatting assistant message (converts to 'model')."""
        client = GeminiClient(api_key="test-key")
        msg = client._format_message("assistant", "Hi there!")
        assert msg == {
            "role": "model",
            "parts": [{"text": "Hi there!"}],
        }

    def test_format_history(self):
        """Test formatting conversation history."""
        client = GeminiClient(api_key="test-key")
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        formatted = client._format_history(history)
        assert len(formatted) == 2
        assert formatted[0]["role"] == "user"
        assert formatted[1]["role"] == "model"

    def test_format_empty_history(self):
        """Test formatting empty history."""
        client = GeminiClient(api_key="test-key")
        assert client._format_history(None) == []
        assert client._format_history([]) == []

    def test_build_url(self):
        """Test URL building."""
        client = GeminiClient(api_key="test-key", model="gemini-pro")
        url = client._build_url("generateContent")
        assert "models/gemini-pro:generateContent" in url
        assert "key=test-key" in url

    def test_build_request_body(self):
        """Test request body building."""
        client = GeminiClient(api_key="test-key")
        body = client._build_request_body(
            prompt="Hello",
            system_instruction="Be helpful",
        )
        assert "contents" in body
        assert len(body["contents"]) == 1
        assert body["contents"][0]["parts"][0]["text"] == "Hello"
        assert "systemInstruction" in body
        assert body["generationConfig"]["temperature"] == 0.7

    def test_build_request_body_with_history(self):
        """Test request body with conversation history."""
        client = GeminiClient(api_key="test-key")
        history = [
            {"role": "user", "content": "My name is Bob"},
            {"role": "assistant", "content": "Hello Bob!"},
        ]
        body = client._build_request_body(
            prompt="What's my name?",
            history=history,
        )
        assert len(body["contents"]) == 3
        assert body["contents"][0]["parts"][0]["text"] == "My name is Bob"
        assert body["contents"][1]["role"] == "model"
        assert body["contents"][2]["parts"][0]["text"] == "What's my name?"

    def test_extract_text_from_response(self):
        """Test extracting text from API response."""
        client = GeminiClient(api_key="test-key")
        response = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Hello!"}, {"text": " World!"}],
                    "role": "model",
                }
            }]
        }
        text = client._extract_text_from_response(response)
        assert text == "Hello! World!"

    def test_extract_text_empty_candidates(self):
        """Test extracting text with empty candidates."""
        client = GeminiClient(api_key="test-key")
        response = {"candidates": []}
        text = client._extract_text_from_response(response)
        assert text == ""

    def test_extract_text_prompt_blocked(self):
        """Test extracting text when prompt is blocked."""
        client = GeminiClient(api_key="test-key")
        response = {
            "promptFeedback": {
                "blockReason": "SAFETY"
            }
        }
        with pytest.raises(GeminiError, match="Prompt blocked"):
            client._extract_text_from_response(response)


class TestGeminiClientAsync:
    """Async tests for GeminiClient."""

    @pytest.fixture
    def mock_response(self):
        """Create a mock successful response."""
        return {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Test response"}],
                    "role": "model",
                }
            }]
        }

    @pytest.mark.asyncio
    async def test_complete_success(self, mock_response):
        """Test successful completion."""
        client = GeminiClient(api_key="test-key")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_session") as mock_session:
            session = AsyncMock()
            session.post = AsyncMock(return_value=mock_resp)
            mock_session.return_value = session

            result = await client.complete("Hello")
            assert result == "Test response"

        await client.close()

    @pytest.mark.asyncio
    async def test_complete_with_history(self, mock_response):
        """Test completion with conversation history."""
        client = GeminiClient(api_key="test-key")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_session") as mock_session:
            session = AsyncMock()
            session.post = AsyncMock(return_value=mock_resp)
            mock_session.return_value = session

            history = [
                {"role": "user", "content": "My name is Alice"},
                {"role": "assistant", "content": "Hello Alice!"},
            ]
            result = await client.complete("What's my name?", history=history)
            assert result == "Test response"

            # Verify the request included history
            call_args = session.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert len(body["contents"]) == 3

        await client.close()

    @pytest.mark.asyncio
    async def test_complete_api_error(self):
        """Test handling API errors."""
        client = GeminiClient(api_key="test-key")

        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value='{"error": {"message": "Bad request"}}')

        with patch.object(client, "_get_session") as mock_session:
            session = AsyncMock()
            session.post = AsyncMock(return_value=mock_resp)
            mock_session.return_value = session

            with pytest.raises(GeminiError, match="Bad request"):
                await client.complete("Hello")

        await client.close()

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_response):
        """Test async context manager."""
        async with GeminiClient(api_key="test-key") as client:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response)

            with patch.object(client, "_get_session") as mock_session:
                session = AsyncMock()
                session.post = AsyncMock(return_value=mock_resp)
                mock_session.return_value = session

                result = await client.complete("Hello")
                assert result == "Test response"

    @pytest.mark.asyncio
    async def test_complete_with_tools(self):
        """Test completion with function calling."""
        client = GeminiClient(api_key="test-key")

        mock_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "get_weather",
                            "args": {"location": "Tokyo"}
                        }
                    }],
                    "role": "model",
                }
            }]
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_session") as mock_session:
            session = AsyncMock()
            session.post = AsyncMock(return_value=mock_resp)
            mock_session.return_value = session

            tools = [{
                "function_declarations": [{
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"}
                        }
                    }
                }]
            }]

            result = await client.complete_with_tools("What's the weather in Tokyo?", tools)
            assert result.has_tool_calls
            assert len(result.tool_calls) == 1
            assert result.tool_calls[0].name == "get_weather"
            assert result.tool_calls[0].arguments["location"] == "Tokyo"
            assert result.finish_reason == "tool_calls"

        await client.close()


class TestGeminiIntegration:
    """Integration tests (require GEMINI_API_KEY env var)."""

    @pytest.fixture
    def has_api_key(self):
        """Check if API key is available."""
        return bool(os.environ.get("GEMINI_API_KEY"))

    @pytest.mark.asyncio
    async def test_real_complete(self, has_api_key):
        """Test real API call (skipped if no API key)."""
        if not has_api_key:
            pytest.skip("GEMINI_API_KEY not set")

        async with GeminiClient() as client:
            response = await client.complete("Say 'hello' in one word")
            assert len(response) > 0
            assert "hello" in response.lower()

    @pytest.mark.asyncio
    async def test_real_stream(self, has_api_key):
        """Test real streaming (skipped if no API key)."""
        if not has_api_key:
            pytest.skip("GEMINI_API_KEY not set")

        async with GeminiClient() as client:
            chunks = []
            async for chunk in client.stream("Count from 1 to 3"):
                chunks.append(chunk)

            full_response = "".join(chunks)
            assert len(full_response) > 0

    @pytest.mark.asyncio
    async def test_real_with_history(self, has_api_key):
        """Test real API call with history (skipped if no API key)."""
        if not has_api_key:
            pytest.skip("GEMINI_API_KEY not set")

        async with GeminiClient() as client:
            history = [
                {"role": "user", "content": "My name is TestUser"},
                {"role": "assistant", "content": "Hello TestUser! Nice to meet you."},
            ]
            response = await client.complete(
                "What's my name? Just say the name, nothing else.",
                history=history,
            )
            assert "TestUser" in response or "testuser" in response.lower()
