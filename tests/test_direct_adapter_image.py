"""Tests for image block conversion in DirectAdapter.

Verifies that Nimbus internal image format:
    {"type": "image", "data": "<base64>", "mimeType": "image/png"}

is correctly converted to each LLM channel's native format:
  - Anthropic: {"type": "image", "source": {"type": "base64", ...}}
  - OpenAI/LiteLLM: {"type": "image_url", "image_url": {"url": "data:...;base64,..."}}
  - Codex Responses API: {"type": "input_image", "image_url": "data:...;base64,..."}
"""

import pytest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers to get a DirectAdapter without real credentials
# ---------------------------------------------------------------------------

def _make_adapter():
    """Create a DirectAdapter with mocked config (no real API keys needed)."""
    with patch("nimbus.adapters.direct_adapter.get_config") as mock_cfg, \
         patch("nimbus.adapters.direct_adapter.DirectAdapter._init_anthropic_oauth"), \
         patch("nimbus.adapters.direct_adapter.DirectAdapter._init_openai_codex_oauth"):
        cfg = MagicMock()
        cfg.gemini_api_key = None
        mock_cfg.return_value = cfg

        from nimbus.adapters.direct_adapter import DirectAdapter, LLMConfig
        adapter = DirectAdapter.__new__(DirectAdapter)
        adapter.config = LLMConfig()
        adapter._model = "test-model"
        adapter._anthropic_auth = None
        adapter._codex_auth = None
        adapter._anthropic_client = None
        adapter._anthropic_client_token = None
        adapter._codex_client = None
        adapter._codex_client_token = None
        return adapter


# ---------------------------------------------------------------------------
# _convert_image_block unit tests
# ---------------------------------------------------------------------------

class TestConvertImageBlock:
    """Test the static _convert_image_block method."""

    def setup_method(self):
        from nimbus.adapters.direct_adapter import DirectAdapter
        self.convert = DirectAdapter._convert_image_block

    def test_image_to_anthropic(self):
        block = {"type": "image", "data": "abc123==", "mimeType": "image/png"}
        result = self.convert(block, "anthropic")
        assert result == {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": "abc123==",
            },
        }

    def test_image_to_anthropic_jpeg(self):
        block = {"type": "image", "data": "jpg_data", "mimeType": "image/jpeg"}
        result = self.convert(block, "anthropic")
        assert result["source"]["media_type"] == "image/jpeg"
        assert result["source"]["data"] == "jpg_data"

    def test_image_to_openai(self):
        block = {"type": "image", "data": "abc123==", "mimeType": "image/png"}
        result = self.convert(block, "openai")
        assert result == {
            "type": "image_url",
            "image_url": {
                "url": "data:image/png;base64,abc123==",
            },
        }

    def test_image_to_openai_webp(self):
        block = {"type": "image", "data": "webp_data", "mimeType": "image/webp"}
        result = self.convert(block, "openai")
        assert result["image_url"]["url"] == "data:image/webp;base64,webp_data"

    def test_image_to_responses(self):
        block = {"type": "image", "data": "abc123==", "mimeType": "image/png"}
        result = self.convert(block, "responses")
        assert result == {
            "type": "input_image",
            "image_url": "data:image/png;base64,abc123==",
        }

    def test_image_default_mime(self):
        """Missing mimeType defaults to image/png."""
        block = {"type": "image", "data": "data"}
        result = self.convert(block, "anthropic")
        assert result["source"]["media_type"] == "image/png"

    def test_text_block_anthropic(self):
        """Text blocks pass through unchanged for anthropic."""
        block = {"type": "text", "text": "hello"}
        result = self.convert(block, "anthropic")
        assert result == {"type": "text", "text": "hello"}

    def test_text_block_openai(self):
        """Text blocks pass through unchanged for openai."""
        block = {"type": "text", "text": "hello"}
        result = self.convert(block, "openai")
        assert result == {"type": "text", "text": "hello"}

    def test_text_block_responses(self):
        """Text blocks get remapped to input_text for responses API."""
        block = {"type": "text", "text": "hello"}
        result = self.convert(block, "responses")
        assert result == {"type": "input_text", "text": "hello"}

    def test_empty_image_data(self):
        """Image with empty data still converts correctly."""
        block = {"type": "image", "data": "", "mimeType": "image/png"}
        result = self.convert(block, "openai")
        assert result["image_url"]["url"] == "data:image/png;base64,"


# ---------------------------------------------------------------------------
# _convert_content_blocks unit tests
# ---------------------------------------------------------------------------

class TestConvertContentBlocks:
    """Test the static _convert_content_blocks method."""

    def setup_method(self):
        from nimbus.adapters.direct_adapter import DirectAdapter
        self.convert = DirectAdapter._convert_content_blocks

    def test_string_passthrough(self):
        """String content is returned unchanged."""
        assert self.convert("hello", "anthropic") == "hello"
        assert self.convert("hello", "openai") == "hello"
        assert self.convert("hello", "responses") == "hello"

    def test_empty_string(self):
        assert self.convert("", "anthropic") == ""

    def test_empty_list(self):
        """Empty list returns empty list (no crash)."""
        assert self.convert([], "anthropic") == []

    def test_list_with_text_and_image_anthropic(self):
        content = [
            {"type": "text", "text": "What is this?"},
            {"type": "image", "data": "b64data", "mimeType": "image/png"},
        ]
        result = self.convert(content, "anthropic")
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "What is this?"}
        assert result[1]["type"] == "image"
        assert result[1]["source"]["type"] == "base64"
        assert result[1]["source"]["data"] == "b64data"

    def test_list_with_text_and_image_openai(self):
        content = [
            {"type": "text", "text": "Describe"},
            {"type": "image", "data": "b64data", "mimeType": "image/jpeg"},
        ]
        result = self.convert(content, "openai")
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "Describe"}
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == "data:image/jpeg;base64,b64data"

    def test_list_with_text_and_image_responses(self):
        content = [
            {"type": "text", "text": "Analyze"},
            {"type": "image", "data": "b64data", "mimeType": "image/png"},
        ]
        result = self.convert(content, "responses")
        assert len(result) == 2
        assert result[0] == {"type": "input_text", "text": "Analyze"}
        assert result[1] == {
            "type": "input_image",
            "image_url": "data:image/png;base64,b64data",
        }

    def test_multiple_images(self):
        content = [
            {"type": "text", "text": "Compare these:"},
            {"type": "image", "data": "img1", "mimeType": "image/png"},
            {"type": "image", "data": "img2", "mimeType": "image/jpeg"},
        ]
        result = self.convert(content, "openai")
        assert len(result) == 3
        assert result[1]["image_url"]["url"] == "data:image/png;base64,img1"
        assert result[2]["image_url"]["url"] == "data:image/jpeg;base64,img2"


# ---------------------------------------------------------------------------
# Integration: _convert_messages_to_anthropic
# ---------------------------------------------------------------------------

class TestAnthropicMessageConversion:
    """Test image conversion within _convert_messages_to_anthropic."""

    def setup_method(self):
        self.adapter = _make_adapter()

    def test_user_message_with_image(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image", "data": "b64img", "mimeType": "image/png"},
            ],
        }]
        system_text, anthropic_msgs = self.adapter._convert_messages_to_anthropic(messages)
        assert len(anthropic_msgs) == 1
        content = anthropic_msgs[0]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "What is this?"}
        assert content[1]["type"] == "image"
        assert content[1]["source"]["type"] == "base64"
        assert content[1]["source"]["media_type"] == "image/png"
        assert content[1]["source"]["data"] == "b64img"

    def test_user_message_plain_string(self):
        """Plain string content is unchanged."""
        messages = [{"role": "user", "content": "Hello"}]
        _, anthropic_msgs = self.adapter._convert_messages_to_anthropic(messages)
        assert anthropic_msgs[0]["content"] == "Hello"

    def test_user_message_none_content(self):
        """None content defaults to empty string."""
        messages = [{"role": "user", "content": None}]
        _, anthropic_msgs = self.adapter._convert_messages_to_anthropic(messages)
        assert anthropic_msgs[0]["content"] == ""


# ---------------------------------------------------------------------------
# Integration: _convert_messages_to_responses_api
# ---------------------------------------------------------------------------

class TestResponsesApiMessageConversion:
    """Test image conversion within _convert_messages_to_responses_api."""

    def setup_method(self):
        self.adapter = _make_adapter()

    def test_user_message_with_image(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this"},
                {"type": "image", "data": "b64img", "mimeType": "image/png"},
            ],
        }]
        _, input_items = self.adapter._convert_messages_to_responses_api(messages)
        assert len(input_items) == 1
        content = input_items[0]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "input_text", "text": "Analyze this"}
        assert content[1] == {
            "type": "input_image",
            "image_url": "data:image/png;base64,b64img",
        }

    def test_user_message_plain_string(self):
        """Plain string content produces input_text block."""
        messages = [{"role": "user", "content": "Hello"}]
        _, input_items = self.adapter._convert_messages_to_responses_api(messages)
        assert input_items[0]["content"] == [{"type": "input_text", "text": "Hello"}]

    def test_user_message_empty_content(self):
        """Empty content produces input_text with empty string."""
        messages = [{"role": "user", "content": ""}]
        _, input_items = self.adapter._convert_messages_to_responses_api(messages)
        assert input_items[0]["content"] == [{"type": "input_text", "text": ""}]

    def test_user_message_none_content(self):
        """None content defaults to empty string then input_text."""
        messages = [{"role": "user", "content": None}]
        _, input_items = self.adapter._convert_messages_to_responses_api(messages)
        assert input_items[0]["content"] == [{"type": "input_text", "text": ""}]

    def test_user_message_multiple_images(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Compare"},
                {"type": "image", "data": "img1", "mimeType": "image/png"},
                {"type": "image", "data": "img2", "mimeType": "image/jpeg"},
            ],
        }]
        _, input_items = self.adapter._convert_messages_to_responses_api(messages)
        content = input_items[0]["content"]
        assert len(content) == 3
        assert content[0]["type"] == "input_text"
        assert content[1]["type"] == "input_image"
        assert content[2]["type"] == "input_image"
        assert "image/jpeg" in content[2]["image_url"]


# ---------------------------------------------------------------------------
# Integration: LiteLLM clean_messages
# ---------------------------------------------------------------------------

class TestLiteLLMCleanMessages:
    """Test that LiteLLM path converts image blocks in message content."""

    def setup_method(self):
        self.adapter = _make_adapter()

    def test_image_conversion_in_clean_messages(self):
        """Simulate the clean_messages loop from _stream_litellm."""
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image", "data": "b64data", "mimeType": "image/png"},
            ]},
            {"role": "assistant", "content": "It's a cat."},
            {"role": "user", "content": "Thanks"},
        ]

        # Replicate the exact logic from _stream_litellm
        clean_messages = []
        for m in messages:
            msg = m.copy()
            if msg.get("content") is None:
                msg["content"] = ""
            elif isinstance(msg.get("content"), list):
                msg["content"] = self.adapter._convert_content_blocks(
                    msg["content"], "openai"
                )
            clean_messages.append(msg)

        # First message: converted image blocks
        first = clean_messages[0]["content"]
        assert isinstance(first, list)
        assert first[0] == {"type": "text", "text": "What is this?"}
        assert first[1]["type"] == "image_url"
        assert first[1]["image_url"]["url"] == "data:image/png;base64,b64data"

        # Second message: plain string unchanged
        assert clean_messages[1]["content"] == "It's a cat."

        # Third message: plain string unchanged
        assert clean_messages[2]["content"] == "Thanks"

    def test_none_content_becomes_empty_string(self):
        messages = [{"role": "user", "content": None}]
        clean_messages = []
        for m in messages:
            msg = m.copy()
            if msg.get("content") is None:
                msg["content"] = ""
            elif isinstance(msg.get("content"), list):
                msg["content"] = self.adapter._convert_content_blocks(
                    msg["content"], "openai"
                )
            clean_messages.append(msg)
        assert clean_messages[0]["content"] == ""
