"""Tests for Ollama LLM client."""

import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from nimbus.llm import OllamaClient
from nimbus.llm.ollama import OllamaConfig, OllamaError


class TestOllamaConfig:
    """Tests for OllamaConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = OllamaConfig()
        assert config.model == "qwen3:8b"
        assert config.base_url == "http://localhost:11434"
        assert config.temperature == 0.7
        assert config.max_tokens == 8192
        assert config.max_retries == 3

    def test_custom_config(self):
        """Test custom configuration values."""
        config = OllamaConfig(
            model="llama3:8b",
            base_url="http://custom:8080",
            temperature=0.5,
            max_tokens=4096,
        )
        assert config.model == "llama3:8b"
        assert config.base_url == "http://custom:8080"
        assert config.temperature == 0.5
        assert config.max_tokens == 4096


class TestOllamaClient:
    """Tests for OllamaClient."""

    def test_init_default(self):
        """Test initialization with defaults."""
        client = OllamaClient()
        assert client.config.model == "qwen3:8b"
        assert client.config.base_url == "http://localhost:11434"

    def test_init_with_params(self):
        """Test initialization with custom parameters."""
        client = OllamaClient(
            model="llama3:8b",
            base_url="http://custom:8080",
            temperature=0.5,
        )
        assert client.config.model == "llama3:8b"
        assert client.config.base_url == "http://custom:8080"
        assert client.config.temperature == 0.5

    def test_init_with_env_vars(self, monkeypatch):
        """Test initialization with environment variables."""
        monkeypatch.setenv("NIMBUS_LLM_URL", "http://env-url:11434")
        monkeypatch.setenv("NIMBUS_LLM_MODEL", "env-model")

        client = OllamaClient()
        assert client.config.base_url == "http://env-url:11434"
        assert client.config.model == "env-model"

    def test_format_messages_basic(self):
        """Test formatting basic messages."""
        client = OllamaClient()
        messages = client._format_messages("Hello")

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hello"

    def test_format_messages_with_system(self):
        """Test formatting messages with system instruction."""
        client = OllamaClient()
        messages = client._format_messages(
            "Hello",
            system_instruction="Be helpful",
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Be helpful"
        assert messages[1]["role"] == "user"

    def test_format_messages_with_history(self):
        """Test formatting messages with conversation history."""
        client = OllamaClient()
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        messages = client._format_messages("How are you?", history=history)

        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Hi"
        assert messages[1]["role"] == "assistant"
        assert messages[1]["content"] == "Hello!"
        assert messages[2]["role"] == "user"
        assert messages[2]["content"] == "How are you?"

    def test_build_options(self):
        """Test building Ollama options."""
        client = OllamaClient(temperature=0.5, max_tokens=2048)
        options = client._build_options()

        assert options["temperature"] == 0.5
        assert options["num_predict"] == 2048
        assert "top_p" in options
        assert "top_k" in options


class TestOllamaClientAsync:
    """Async tests for OllamaClient."""

    @pytest.fixture
    def mock_response(self):
        """Create a mock successful response."""
        return {
            "message": {
                "role": "assistant",
                "content": "Test response from Ollama",
            },
            "done": True,
        }

    @pytest.mark.asyncio
    async def test_complete_success(self, mock_response):
        """Test successful completion."""
        client = OllamaClient()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_session") as mock_session:
            session = AsyncMock()
            session.post = AsyncMock(return_value=mock_resp)
            mock_session.return_value = session

            result = await client.complete("Hello")
            assert result == "Test response from Ollama"

        await client.close()

    @pytest.mark.asyncio
    async def test_complete_with_history(self, mock_response):
        """Test completion with conversation history."""
        client = OllamaClient()

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
            assert result == "Test response from Ollama"

            # Verify the request included history
            call_args = session.post.call_args
            body = call_args.kwargs.get("json") or call_args[1].get("json")
            assert len(body["messages"]) == 3

        await client.close()

    @pytest.mark.asyncio
    async def test_complete_api_error(self):
        """Test handling API errors."""
        client = OllamaClient()

        mock_resp = AsyncMock()
        mock_resp.status = 500
        mock_resp.text = AsyncMock(return_value="Internal Server Error")

        # Reduce retries for faster test
        client.config.max_retries = 1

        with patch.object(client, "_get_session") as mock_session:
            session = AsyncMock()
            session.post = AsyncMock(return_value=mock_resp)
            mock_session.return_value = session

            with pytest.raises(OllamaError, match="Ollama API error"):
                await client.complete("Hello")

        await client.close()

    @pytest.mark.asyncio
    async def test_context_manager(self, mock_response):
        """Test async context manager."""
        async with OllamaClient() as client:
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value=mock_response)

            with patch.object(client, "_get_session") as mock_session:
                session = AsyncMock()
                session.post = AsyncMock(return_value=mock_resp)
                mock_session.return_value = session

                result = await client.complete("Hello")
                assert result == "Test response from Ollama"

    @pytest.mark.asyncio
    async def test_generate_endpoint(self):
        """Test the generate endpoint (non-chat)."""
        client = OllamaClient()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"response": "Generated text"})

        with patch.object(client, "_get_session") as mock_session:
            session = AsyncMock()
            session.post = AsyncMock(return_value=mock_resp)
            mock_session.return_value = session

            result = await client.generate("Generate something")
            assert result == "Generated text"

            # Verify it used /api/generate
            call_args = session.post.call_args
            url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
            assert "/api/generate" in url

        await client.close()


class TestOllamaIntegration:
    """Integration tests (require local Ollama server)."""

    @pytest.fixture
    def has_ollama(self):
        """Check if Ollama server is available."""
        import socket
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(('localhost', 11434))
            sock.close()
            return result == 0
        except Exception:
            return False

    @pytest.mark.asyncio
    async def test_real_complete(self, has_ollama):
        """Test real API call (skipped if Ollama not available)."""
        if not has_ollama:
            pytest.skip("Ollama server not available")

        async with OllamaClient(model="qwen3:8b") as client:
            response = await client.complete("Say 'hello' in one word")
            assert len(response) > 0

    @pytest.mark.asyncio
    async def test_real_list_models(self, has_ollama):
        """Test listing models (skipped if Ollama not available)."""
        if not has_ollama:
            pytest.skip("Ollama server not available")

        async with OllamaClient() as client:
            models = await client.list_models()
            assert isinstance(models, list)
