"""
Tests for MMU Image Token Optimization (Phase 1-3).

Covers:
- Phase 1: token_estimate() with image blocks
- Phase 2: _downgrade_seen_images() dedup + budget
- Phase 2: _image_key() fingerprinting
- Phase 3: compaction _format_messages() with multimodal content
"""

import pytest
from nimbus.core.memory.context import Message, IMAGE_TOKEN_ESTIMATE, MESSAGE_OVERHEAD
from nimbus.core.memory.mmu import MMU, MMUConfig


# =============================================================================
# Phase 1: token_estimate() with image blocks
# =============================================================================

class TestImageTokenEstimate:
    """Phase 1: token_estimate correctly accounts for image tokens."""

    def test_single_image_block(self):
        """A message with one image should include IMAGE_TOKEN_ESTIMATE + overhead."""
        msg = Message(role="user", content=[
            {"type": "image", "data": "base64data", "mimeType": "image/png"}
        ])
        assert msg.token_estimate() == IMAGE_TOKEN_ESTIMATE + MESSAGE_OVERHEAD

    def test_text_plus_image(self):
        """Mixed text + image: tokens = text_tokens + IMAGE_TOKEN_ESTIMATE + overhead."""
        msg = Message(role="user", content=[
            {"type": "text", "text": "a" * 100},  # 100/4 = 25 tokens
            {"type": "image", "data": "base64data", "mimeType": "image/png"}
        ])
        estimate = msg.token_estimate()
        assert estimate == 25 + IMAGE_TOKEN_ESTIMATE + MESSAGE_OVERHEAD

    def test_multiple_images(self):
        """Multiple images: each adds IMAGE_TOKEN_ESTIMATE, plus message overhead."""
        msg = Message(role="user", content=[
            {"type": "image", "data": "img1", "mimeType": "image/png"},
            {"type": "image", "data": "img2", "mimeType": "image/jpeg"},
            {"type": "image", "data": "img3", "mimeType": "image/png"},
        ])
        assert msg.token_estimate() == IMAGE_TOKEN_ESTIMATE * 3 + MESSAGE_OVERHEAD

    def test_no_image_blocks(self):
        """List content with only text blocks: no image tokens."""
        msg = Message(role="user", content=[
            {"type": "text", "text": "Hello world"},
        ])
        estimate = msg.token_estimate()
        assert estimate > MESSAGE_OVERHEAD  # Has text content beyond overhead
        assert estimate < IMAGE_TOKEN_ESTIMATE  # Just text, no image

    def test_string_content_with_overhead(self):
        """String content includes message overhead."""
        msg = Message(role="user", content="a" * 100)
        assert msg.token_estimate() == 25 + MESSAGE_OVERHEAD

    def test_empty_content_has_overhead(self):
        """Even empty/None content returns message overhead."""
        msg = Message(role="user", content=None)
        assert msg.token_estimate() == MESSAGE_OVERHEAD


# =============================================================================
# Phase 2: _image_key() fingerprinting
# =============================================================================

class TestImageKey:
    """Phase 2: _image_key produces reliable fingerprints."""

    def setup_method(self):
        self.mmu = MMU()

    def test_same_image_same_key(self):
        """Identical image data should produce the same key."""
        block = {"type": "image", "data": "iVBORw0KGgoAAAANSUhEU", "mimeType": "image/png"}
        key1 = self.mmu._image_key(block)
        key2 = self.mmu._image_key(block)
        assert key1 == key2

    def test_different_data_different_key(self):
        """Different image data should produce different keys."""
        block1 = {"type": "image", "data": "iVBORw0KGgoAAAANSUhEU_image_A_unique_data", "mimeType": "image/png"}
        block2 = {"type": "image", "data": "iVBORw0KGgoAAAANSUhEU_image_B_unique_data", "mimeType": "image/png"}
        assert self.mmu._image_key(block1) != self.mmu._image_key(block2)

    def test_same_prefix_different_data(self):
        """Images sharing a 64-char prefix but different overall data MUST produce different keys."""
        # This test verifies the SHA256 fix (old prefix-based would fail here)
        shared_prefix = "A" * 100  # Shared first 100 chars
        block1 = {"type": "image", "data": shared_prefix + "_SUFFIX_ONE", "mimeType": "image/png"}
        block2 = {"type": "image", "data": shared_prefix + "_SUFFIX_TWO", "mimeType": "image/png"}
        assert self.mmu._image_key(block1) != self.mmu._image_key(block2)

    def test_different_mime_different_key(self):
        """Same data but different MIME type should produce different keys."""
        data = "same_data_here"
        block1 = {"type": "image", "data": data, "mimeType": "image/png"}
        block2 = {"type": "image", "data": data, "mimeType": "image/jpeg"}
        assert self.mmu._image_key(block1) != self.mmu._image_key(block2)

    def test_empty_data(self):
        """Block with no data should not crash."""
        block = {"type": "image", "mimeType": "image/png"}
        key = self.mmu._image_key(block)
        assert isinstance(key, str)

    def test_none_data(self):
        """Block with data=None should not crash."""
        block = {"type": "image", "data": None, "mimeType": "image/png"}
        key = self.mmu._image_key(block)
        assert isinstance(key, str)


# =============================================================================
# Phase 2: _downgrade_seen_images()
# =============================================================================

class TestDowngradeSeenImages:
    """Phase 2: _downgrade_seen_images correctly deduplicates and budgets."""

    def setup_method(self):
        self.mmu = MMU(config=MMUConfig(max_image_tokens=3000))  # Budget for ~2 images

    def _make_image_msg(self, data: str, text: str = "", mime: str = "image/png", role: str = "user") -> dict:
        """Helper to create a message dict with image content."""
        content = []
        if text:
            content.append({"type": "text", "text": text})
        content.append({"type": "image", "data": data, "mimeType": mime})
        return {"role": role, "content": content}

    def test_single_image_kept(self):
        """A single image within budget should be kept."""
        messages = [self._make_image_msg("img_data_1", "Look at this")]
        result = self.mmu._downgrade_seen_images(messages)
        assert result[0]["content"][1]["type"] == "image"

    def test_duplicate_image_deduplicated(self):
        """Same image appearing twice: only the later one is kept."""
        messages = [
            self._make_image_msg("img_data_same", "First upload"),
            {"role": "assistant", "content": "I see the image"},
            self._make_image_msg("img_data_same", "Same image again"),
        ]
        result = self.mmu._downgrade_seen_images(messages)
        # First occurrence should be placeholder
        first_img_block = result[0]["content"][1]
        assert first_img_block["type"] == "text"
        assert "📷" in first_img_block["text"]
        # Last occurrence should be kept
        last_img_block = result[2]["content"][1]
        assert last_img_block["type"] == "image"

    def test_budget_enforcement(self):
        """When images exceed budget, oldest unique images are dropped."""
        # Budget is 3000 = 2 images max (1500 each)
        messages = [
            self._make_image_msg("unique_img_1", "Image 1"),
            self._make_image_msg("unique_img_2", "Image 2"),
            self._make_image_msg("unique_img_3", "Image 3"),
        ]
        result = self.mmu._downgrade_seen_images(messages)
        
        # Count kept images
        kept = 0
        placeholders = 0
        for msg in result:
            for block in msg["content"]:
                if isinstance(block, dict):
                    if block.get("type") == "image":
                        kept += 1
                    elif block.get("type") == "text" and "📷" in block.get("text", ""):
                        placeholders += 1
        assert kept == 2   # Budget allows 2
        assert placeholders == 1  # 1 dropped

    def test_newest_images_kept(self):
        """Budget keeps the NEWEST images (backward scan)."""
        messages = [
            self._make_image_msg("oldest_img", "Old"),
            self._make_image_msg("middle_img", "Mid"),
            self._make_image_msg("newest_img", "New"),
        ]
        result = self.mmu._downgrade_seen_images(messages)
        
        # Oldest should be placeholder
        assert result[0]["content"][1]["type"] == "text"
        assert "📷" in result[0]["content"][1]["text"]
        # Newest two should be kept
        assert result[1]["content"][1]["type"] == "image"
        assert result[2]["content"][1]["type"] == "image"

    def test_text_only_messages_untouched(self):
        """Messages without images should pass through unchanged."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        result = self.mmu._downgrade_seen_images(messages)
        assert result == messages

    def test_non_list_content_preserved(self):
        """String content messages are preserved as-is."""
        messages = [
            {"role": "system", "content": "System prompt"},
            self._make_image_msg("img1", "Image"),
        ]
        result = self.mmu._downgrade_seen_images(messages)
        assert result[0]["content"] == "System prompt"

    def test_text_blocks_in_mixed_message_preserved(self):
        """Text blocks in a multimodal message are never replaced."""
        messages = [self._make_image_msg("img", "Important text")]
        result = self.mmu._downgrade_seen_images(messages)
        text_block = result[0]["content"][0]
        assert text_block["type"] == "text"
        assert text_block["text"] == "Important text"

    def test_placeholder_contains_mime_type(self):
        """Placeholder text should mention the original MIME type."""
        self.mmu = MMU(config=MMUConfig(max_image_tokens=0))  # Zero budget = all dropped
        messages = [self._make_image_msg("data", mime="image/jpeg")]
        result = self.mmu._downgrade_seen_images(messages)
        placeholder = result[0]["content"][0]
        assert "image/jpeg" in placeholder["text"]

    def test_empty_messages_list(self):
        """Empty input should return empty output."""
        assert self.mmu._downgrade_seen_images([]) == []


# =============================================================================
# Phase 3: compaction _format_messages() with multimodal content
# =============================================================================

class TestCompactionFormatMessages:
    """Phase 3: _format_messages handles list content with images."""

    def setup_method(self):
        from nimbus.core.compaction import DefaultCompactionLLM
        # We need a mock LLM client, but _format_messages doesn't use it
        self.formatter = DefaultCompactionLLM(llm_client=None)

    def test_string_content_unchanged(self):
        """String content works as before."""
        messages = [{"role": "user", "content": "Hello world"}]
        result = self.formatter._format_messages(messages)
        assert "Hello world" in result

    def test_list_content_with_text_only(self):
        """List content with only text blocks extracts text."""
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Some question"},
        ]}]
        result = self.formatter._format_messages(messages)
        assert "Some question" in result

    def test_list_content_with_image(self):
        """List content with image produces [Attached image: mime] marker."""
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "Look at this"},
            {"type": "image", "data": "base64...", "mimeType": "image/png"},
        ]}]
        result = self.formatter._format_messages(messages)
        assert "Look at this" in result
        assert "[Attached image: image/png]" in result
        # Should NOT contain raw base64 data
        assert "base64..." not in result

    def test_list_content_image_only(self):
        """Image-only message (no text) still produces marker."""
        messages = [{"role": "user", "content": [
            {"type": "image", "data": "base64data", "mimeType": "image/jpeg"},
        ]}]
        result = self.formatter._format_messages(messages)
        assert "[Attached image: image/jpeg]" in result

    def test_list_content_no_raw_data_leak(self):
        """Ensure no raw base64 data leaks into formatted output."""
        fake_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        messages = [{"role": "user", "content": [
            {"type": "image", "data": fake_b64, "mimeType": "image/png"},
        ]}]
        result = self.formatter._format_messages(messages)
        assert fake_b64 not in result


# =============================================================================
# Tool Result Progressive Compression
# =============================================================================

class TestToolResultCompression:
    """Test tool result progressive compression (hot vs history)."""

    def setup_method(self):
        self.mmu = MMU()

    def _make_tool_msg(self, content: str) -> dict:
        """Helper to create a tool result message dict."""
        return {"role": "tool", "content": content, "tool_call_id": "tc_1", "name": "Read"}

    def test_hot_tool_result_keeps_10k(self):
        """Tool results in hot context should keep up to 10K chars."""
        big_content = "x" * 15_000
        messages = [self._make_tool_msg(big_content)]
        # hot_count=1 means all messages are hot
        result = self.mmu._optimize_context(messages, hot_count=1)
        # Should truncate at 10K (VIEW_MAX_TOOL_CHARS)
        assert len(result[0]["content"]) < 11_000
        assert len(result[0]["content"]) > 9_000

    def test_history_tool_result_compresses_to_1k(self):
        """Tool results in history should compress to ~1K chars."""
        big_content = "x" * 15_000
        messages = [
            self._make_tool_msg(big_content),  # history (index 0)
            {"role": "user", "content": "next"},  # hot (index 1)
        ]
        # hot_count=1 means only last message is hot
        result = self.mmu._optimize_context(messages, hot_count=1)
        # History tool result should be ~1K
        assert len(result[0]["content"]) < 1_200
        assert "compressed for context efficiency" in result[0]["content"]

    def test_small_tool_result_not_truncated(self):
        """Small tool results should not be truncated regardless of position."""
        small_content = "x" * 500
        messages = [self._make_tool_msg(small_content)]
        result = self.mmu._optimize_context(messages, hot_count=0)
        assert result[0]["content"] == small_content

    def test_hot_count_zero_defaults_all_hot(self):
        """When hot_count=0, all messages treated as hot (backward compat)."""
        big_content = "x" * 15_000
        messages = [self._make_tool_msg(big_content)]
        result = self.mmu._optimize_context(messages, hot_count=0)
        # Should use VIEW_MAX_TOOL_CHARS (10K), not HISTORY (1K)
        assert len(result[0]["content"]) > 9_000

    def test_compression_message_includes_total_chars(self):
        """Compression suffix should include total character count."""
        big_content = "y" * 20_000
        messages = [
            self._make_tool_msg(big_content),
            {"role": "user", "content": "next"},
        ]
        result = self.mmu._optimize_context(messages, hot_count=1)
        assert "20,000 chars total" in result[0]["content"]


# =============================================================================
# Token Estimate View
# =============================================================================

class TestTokenEstimateView:
    """Test token_estimate_view() for accurate LLM-facing estimates."""

    def test_small_tool_message_same_as_regular(self):
        """Small tool messages: view estimate == regular estimate."""
        msg = Message(role="tool", content="short result", tool_call_id="tc_1")
        assert msg.token_estimate_view() == msg.token_estimate()

    def test_large_tool_message_capped(self):
        """Large tool messages: view estimate should be much smaller."""
        big_content = "x" * 50_000  # 50K chars
        msg = Message(role="tool", content=big_content, tool_call_id="tc_1")
        view_est = msg.token_estimate_view()
        full_est = msg.token_estimate()
        assert view_est < full_est
        # View should be roughly 10K/4 + overhead = ~2504
        assert view_est < 3000

    def test_non_tool_message_unaffected(self):
        """Non-tool messages: view estimate == regular estimate."""
        msg = Message(role="user", content="x" * 50_000)
        assert msg.token_estimate_view() == msg.token_estimate()

    def test_assistant_message_unaffected(self):
        """Assistant messages: view estimate == regular estimate."""
        msg = Message(role="assistant", content="x" * 50_000)
        assert msg.token_estimate_view() == msg.token_estimate()

    def test_mmu_estimate_tokens_uses_view(self):
        """MMU.estimate_tokens() should use view-based estimates."""
        mmu = MMU()
        big_content = "x" * 50_000
        mmu.add_tool_result("tc_1", "Read", big_content)

        view_tokens = mmu.estimate_tokens()
        # If using view-based, should be much less than 50K/4 = 12500
        assert view_tokens < 5000
