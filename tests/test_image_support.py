"""Tests for image/attachment support across the stack.

Tests the data transformation chain:
  ChatRequest (with attachments)
    → api.py builds content_parts
    → session_v2 passes to agentos
    → agentos passes to mmu
    → mmu stores in Message
    → pi_adapter converts to HTTP format
    → pi_ai_http sends to pi-ai-server

These are unit tests that don't require a running server.
"""

import pytest
import json


class TestAttachmentModel:
    """Test AttachmentCreate model with new mime_type field."""

    def test_image_attachment(self):
        from nimbus.server.models import AttachmentCreate

        att = AttachmentCreate(
            type="image",
            content="iVBORw0KGgo=",  # fake base64
            name="screenshot.png",
            mime_type="image/png",
        )
        assert att.type == "image"
        assert att.content == "iVBORw0KGgo="
        assert att.mime_type == "image/png"
        assert att.name == "screenshot.png"

    def test_text_attachment(self):
        from nimbus.server.models import AttachmentCreate

        att = AttachmentCreate(
            type="text",
            content="Hello, world!\nLine 2",
            name="readme.txt",
            mime_type="text/plain",
        )
        assert att.type == "text"
        assert att.content == "Hello, world!\nLine 2"

    def test_attachment_defaults(self):
        from nimbus.server.models import AttachmentCreate

        att = AttachmentCreate(type="image")
        assert att.mime_type is None
        assert att.content is None
        assert att.name is None
        assert att.path is None

    def test_chat_request_with_attachments(self):
        from nimbus.server.models import AttachmentCreate, ChatRequest

        request = ChatRequest(
            content="What's in this image?",
            attachments=[
                AttachmentCreate(
                    type="image",
                    content="iVBORw0KGgo=",
                    mime_type="image/png",
                    name="photo.png",
                ),
                AttachmentCreate(
                    type="text",
                    content="Some text file content",
                    mime_type="text/plain",
                    name="notes.txt",
                ),
            ],
        )
        assert request.content == "What's in this image?"
        assert len(request.attachments) == 2
        assert request.attachments[0].type == "image"
        assert request.attachments[1].type == "text"

    def test_chat_request_without_attachments(self):
        """Backward compatibility: no attachments."""
        from nimbus.server.models import ChatRequest

        request = ChatRequest(content="Hello")
        assert request.content == "Hello"
        assert request.attachments == []


class TestContentPartsBuilder:
    """Test the content_parts building logic from api.py chat endpoint."""

    def _build_content(self, content: str, attachments: list) -> "str | list":
        """Replicate the logic from api.py chat() endpoint."""
        from nimbus.server.models import AttachmentCreate

        chat_content: str | list = content
        atts = [AttachmentCreate(**a) if isinstance(a, dict) else a for a in attachments]

        if atts:
            content_parts = []
            if content:
                content_parts.append({"type": "text", "text": content})
            for att in atts:
                if att.type == "image" and att.content:
                    content_parts.append({
                        "type": "image",
                        "data": att.content,
                        "mimeType": att.mime_type or "image/png",
                    })
                elif att.type in ("text", "pdf") and att.content:
                    file_label = att.name or "attachment"
                    content_parts.append({
                        "type": "text",
                        "text": f"\n\n--- {file_label} ---\n{att.content}\n--- end of {file_label} ---",
                    })
            if content_parts:
                chat_content = content_parts

        return chat_content

    def test_no_attachments(self):
        """Without attachments, content stays as string."""
        result = self._build_content("Hello", [])
        assert result == "Hello"
        assert isinstance(result, str)

    def test_image_only(self):
        """Image attachment without text."""
        result = self._build_content("", [
            {"type": "image", "content": "base64data", "mime_type": "image/jpeg"},
        ])
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "image"
        assert result[0]["data"] == "base64data"
        assert result[0]["mimeType"] == "image/jpeg"

    def test_text_with_image(self):
        """Text message with image attachment."""
        result = self._build_content("What's this?", [
            {"type": "image", "content": "base64data", "mime_type": "image/png"},
        ])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "What's this?"}
        assert result[1]["type"] == "image"
        assert result[1]["data"] == "base64data"

    def test_text_with_file_attachment(self):
        """Text message with text file attachment."""
        result = self._build_content("Analyze this file", [
            {"type": "text", "content": "file content here", "name": "data.csv"},
        ])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "Analyze this file"}
        assert "data.csv" in result[1]["text"]
        assert "file content here" in result[1]["text"]

    def test_multiple_attachments(self):
        """Multiple attachments of different types."""
        result = self._build_content("Check these", [
            {"type": "image", "content": "img1", "mime_type": "image/png"},
            {"type": "text", "content": "txt content", "name": "notes.md"},
            {"type": "image", "content": "img2", "mime_type": "image/jpeg"},
        ])
        assert isinstance(result, list)
        assert len(result) == 4  # 1 text + 1 image + 1 text file + 1 image
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image"
        assert result[2]["type"] == "text"
        assert result[3]["type"] == "image"

    def test_image_default_mime_type(self):
        """Image without explicit mime_type defaults to image/png."""
        result = self._build_content("", [
            {"type": "image", "content": "base64data"},
        ])
        assert result[0]["mimeType"] == "image/png"

    def test_pdf_attachment(self):
        """PDF treated same as text (content already extracted)."""
        result = self._build_content("Summarize this PDF", [
            {"type": "pdf", "content": "Extracted PDF text...", "name": "report.pdf"},
        ])
        assert isinstance(result, list)
        assert len(result) == 2
        assert "report.pdf" in result[1]["text"]
        assert "Extracted PDF text..." in result[1]["text"]

    def test_empty_attachment_content_ignored(self):
        """Attachments with no content are skipped."""
        result = self._build_content("Hello", [
            {"type": "image", "content": None},
            {"type": "image", "content": ""},
        ])
        # No valid attachments, but we have text - stays as list with just text
        # Actually: content="" is falsy, so image block is skipped
        # And content=None is also falsy
        # So content_parts only has the text part
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0] == {"type": "text", "text": "Hello"}


class TestMessageTypeCompatibility:
    """Test that str | list content works through the memory stack."""

    def test_message_with_string_content(self):
        from nimbus.core.memory.context import Message

        msg = Message(role="user", content="Hello")
        d = msg.to_dict()
        assert d["content"] == "Hello"
        assert d["role"] == "user"

    def test_message_with_list_content(self):
        from nimbus.core.memory.context import Message

        content = [
            {"type": "text", "text": "What's this?"},
            {"type": "image", "data": "base64...", "mimeType": "image/png"},
        ]
        msg = Message(role="user", content=content)
        d = msg.to_dict()
        assert isinstance(d["content"], list)
        assert len(d["content"]) == 2
        assert d["content"][0]["type"] == "text"
        assert d["content"][1]["type"] == "image"

    def test_stack_frame_add_user_message_string(self):
        """Backward compat: string content still works."""
        from nimbus.core.memory.context import StackFrame

        frame = StackFrame(frame_id="test", goal="test")
        frame.add_user_message("Hello")
        assert len(frame.messages) == 1
        assert frame.messages[0].content == "Hello"

    def test_stack_frame_add_user_message_list(self):
        """New: list content for multimodal messages."""
        from nimbus.core.memory.context import StackFrame

        frame = StackFrame(frame_id="test", goal="test")
        content = [
            {"type": "text", "text": "Describe this"},
            {"type": "image", "data": "abc123", "mimeType": "image/png"},
        ]
        frame.add_user_message(content)
        assert len(frame.messages) == 1
        assert isinstance(frame.messages[0].content, list)
        assert frame.messages[0].content[1]["data"] == "abc123"

    def test_message_to_dict_preserves_list(self):
        """Message.to_dict() must preserve list content for LLM API."""
        from nimbus.core.memory.context import Message

        content = [{"type": "text", "text": "hi"}, {"type": "image", "data": "x", "mimeType": "image/png"}]
        msg = Message(role="user", content=content)
        d = msg.to_dict()
        assert d == {"role": "user", "content": content}


class TestPiAdapterMessageConversion:
    """Test that pi_adapter correctly handles multimodal content."""

    def test_convert_string_content(self):
        """String content: should produce HttpMessage with string content."""
        from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig

        adapter = PiLLMAdapter(PiLLMConfig())
        messages = [{"role": "user", "content": "Hello"}]
        http_msgs = adapter._convert_messages_to_http(messages)
        assert len(http_msgs) == 1
        assert http_msgs[0].role == "user"
        assert http_msgs[0].content == "Hello"

    def test_convert_list_content(self):
        """List content: should produce HttpMessage with list content (transparent passthrough)."""
        from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig

        adapter = PiLLMAdapter(PiLLMConfig())
        content = [
            {"type": "text", "text": "What is this?"},
            {"type": "image", "data": "base64data", "mimeType": "image/png"},
        ]
        messages = [{"role": "user", "content": content}]
        http_msgs = adapter._convert_messages_to_http(messages)
        assert len(http_msgs) == 1
        assert http_msgs[0].role == "user"
        assert isinstance(http_msgs[0].content, list)
        assert http_msgs[0].content[0]["type"] == "text"
        assert http_msgs[0].content[1]["type"] == "image"
        assert http_msgs[0].content[1]["data"] == "base64data"

    def test_convert_mixed_messages(self):
        """Mixed: text-only + multimodal messages in same conversation."""
        from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig

        adapter = PiLLMAdapter(PiLLMConfig())
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! How can I help?"},
            {"role": "user", "content": [
                {"type": "text", "text": "What's in this image?"},
                {"type": "image", "data": "abc", "mimeType": "image/jpeg"},
            ]},
        ]
        http_msgs = adapter._convert_messages_to_http(messages)
        assert len(http_msgs) == 3
        assert isinstance(http_msgs[0].content, str)
        assert isinstance(http_msgs[2].content, list)


class TestPiAiHttpMessageBuild:
    """Test that pi_ai_http correctly serializes list content."""

    def test_build_request_string_content(self):
        from nimbus.bridge.pi_ai_http import Message, PiAiHttpClient

        client = PiAiHttpClient()
        messages = [Message(role="user", content="Hello")]
        req = client._build_request(messages, model="test/model")
        assert req["messages"][0]["content"] == "Hello"

    def test_build_request_list_content(self):
        from nimbus.bridge.pi_ai_http import Message, PiAiHttpClient

        client = PiAiHttpClient()
        content = [
            {"type": "text", "text": "Describe this"},
            {"type": "image", "data": "base64data", "mimeType": "image/png"},
        ]
        messages = [Message(role="user", content=content)]
        req = client._build_request(messages, model="test/model")
        assert isinstance(req["messages"][0]["content"], list)
        assert req["messages"][0]["content"][1]["type"] == "image"

    def test_build_request_preserves_all_fields(self):
        """Ensure mimeType and data fields are preserved in serialization."""
        from nimbus.bridge.pi_ai_http import Message, PiAiHttpClient

        client = PiAiHttpClient()
        content = [
            {"type": "image", "data": "iVBORw0KGgo=", "mimeType": "image/png"},
        ]
        messages = [Message(role="user", content=content)]
        req = client._build_request(messages, model="test/model")
        img_block = req["messages"][0]["content"][0]
        assert img_block["data"] == "iVBORw0KGgo="
        assert img_block["mimeType"] == "image/png"
        # Verify it's valid JSON
        json_str = json.dumps(req)
        assert "iVBORw0KGgo=" in json_str
