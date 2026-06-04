"""Tests for nimbus_next.decoder — the hallucination firewall."""

import pytest

from nimbus.core.decoder import InstructionDecoder
from nimbus.core.protocol import Fault


class MockFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class MockToolCall:
    def __init__(self, name, arguments, id="tc_123"):
        self.function = MockFunction(name, arguments)
        self.id = id


class TestDecodeToolCalls:
    def setup_method(self):
        self.decoder = InstructionDecoder()

    def test_single_tool_call(self):
        tc = MockToolCall("Read", '{"file_path": "/tmp/x"}')
        actions = self.decoder.decode(content=None, tool_calls=[tc])
        assert len(actions) == 1
        assert actions[0].kind == "TOOL_CALL"
        assert actions[0].name == "Read"
        assert actions[0].args["file_path"] == "/tmp/x"

    def test_multiple_tool_calls(self):
        tc1 = MockToolCall("Read", '{"file_path": "a.py"}', id="tc1")
        tc2 = MockToolCall("Read", '{"file_path": "b.py"}', id="tc2")
        actions = self.decoder.decode(content=None, tool_calls=[tc1, tc2])
        assert len(actions) == 2
        assert all(a.kind == "TOOL_CALL" for a in actions)

    def test_text_with_tool_calls_becomes_thought(self):
        tc = MockToolCall("Bash", '{"command": "ls"}')
        actions = self.decoder.decode(content="Let me check the files.", tool_calls=[tc])
        assert len(actions) == 2
        assert actions[0].kind == "THOUGHT"
        assert actions[1].kind == "TOOL_CALL"

    def test_dict_style_tool_call(self):
        tc = {"function": {"name": "Write", "arguments": '{"file_path": "x", "content": "y"}'}, "id": "d1"}
        actions = self.decoder.decode(content=None, tool_calls=[tc])
        assert actions[0].name == "Write"
        assert actions[0].id == "d1"

    def test_invalid_json_args_raises_fault(self):
        tc = MockToolCall("Read", "not json")
        with pytest.raises(Fault) as exc_info:
            self.decoder.decode(content=None, tool_calls=[tc])
        assert exc_info.value.code == "ILL_INSTRUCTION"
        assert exc_info.value.retryable is True

    def test_unknown_tool_call_format(self):
        with pytest.raises(Fault):
            self.decoder.decode(content=None, tool_calls=["bad"])

    def test_preserves_tool_call_id(self):
        tc = MockToolCall("Bash", '{"command": "echo hi"}', id="call_abc")
        actions = self.decoder.decode(content=None, tool_calls=[tc])
        assert actions[0].id == "call_abc"


class TestHallucinationDetection:
    def setup_method(self):
        self.decoder = InstructionDecoder()

    def test_detects_called_pattern(self):
        with pytest.raises(Fault) as exc_info:
            self.decoder.decode(content='[Called Read with file_path="/tmp/x"]', tool_calls=None)
        assert exc_info.value.code == "ILL_INSTRUCTION"

    def test_detects_tool_tag(self):
        with pytest.raises(Fault):
            self.decoder.decode(content="<tool_call>Read</tool_call>", tool_calls=None)

    def test_detects_code_block_tool(self):
        with pytest.raises(Fault):
            self.decoder.decode(content='```tool\nRead(file_path="/tmp/x")\n```', tool_calls=None)

    def test_long_text_only_flags_start(self):
        """Long text with pattern NOT at start should pass."""
        long_text = "A" * 400 + " [Called something]"
        # Should NOT raise because pattern is not near the start
        actions = self.decoder.decode(content=long_text, tool_calls=None, text_is_final=True)
        assert actions[0].kind == "REPLY"

    def test_short_text_always_flags(self):
        with pytest.raises(Fault):
            self.decoder.decode(content="I'll use [Called Read].", tool_calls=None)

    def test_no_false_positive_on_clean_text(self):
        actions = self.decoder.decode(content="Here's how to read files in Python.", tool_calls=None, text_is_final=True)
        assert len(actions) == 1

    def test_detects_announced_tool_intent_english(self):
        with pytest.raises(Fault) as exc_info:
            self.decoder.decode(
                content="I will use the Write tool to create the document now.",
                tool_calls=None,
                text_is_final=True,
            )
        assert exc_info.value.code == "ILL_INSTRUCTION"
        assert exc_info.value.retryable is True
        assert exc_info.value.context["pattern"] == "announced_tool_intent_without_call"

    def test_detects_announced_tool_intent_chinese(self):
        with pytest.raises(Fault) as exc_info:
            self.decoder.decode(
                content="我将使用 `Write` 工具为你生成这个文档的内容。",
                tool_calls=None,
                text_is_final=True,
            )
        assert exc_info.value.code == "ILL_INSTRUCTION"
        assert exc_info.value.retryable is True

    def test_allows_tool_discussion_without_intent(self):
        actions = self.decoder.decode(
            content="你可以用 Write 工具写文件，也可以用 Read 检查内容。",
            tool_calls=None,
            text_is_final=True,
        )
        assert actions[0].kind == "REPLY"


class TestPureTextDecoding:
    def setup_method(self):
        self.decoder = InstructionDecoder()

    def test_text_is_final_reply(self):
        """When text_is_final=True, pure text becomes REPLY."""
        actions = self.decoder.decode(content="The answer is 42.", tool_calls=None, text_is_final=True)
        assert actions[0].kind == "REPLY"

    def test_short_text_becomes_return(self):
        """Short, non-planning text in non-final mode → RETURN (done)."""
        actions = self.decoder.decode(content="Done!", tool_calls=None, text_is_final=False)
        assert actions[0].kind == "RETURN"

    def test_planning_text_becomes_thought(self):
        """Text with planning language in non-final mode → THOUGHT."""
        actions = self.decoder.decode(
            content="Let me first check the configuration file.",
            tool_calls=None, text_is_final=False,
        )
        assert actions[0].kind == "THOUGHT"

    def test_done_pattern_chinese(self):
        actions = self.decoder.decode(content="任务已完成。", tool_calls=None, text_is_final=False)
        assert actions[0].kind == "RETURN"

    def test_done_pattern_english(self):
        actions = self.decoder.decode(content="Task is complete.", tool_calls=None, text_is_final=False)
        assert actions[0].kind == "RETURN"

    def test_empty_content(self):
        actions = self.decoder.decode(content="", tool_calls=None)
        assert actions == []

    def test_none_content(self):
        actions = self.decoder.decode(content=None, tool_calls=None)
        assert actions == []

    def test_whitespace_only(self):
        actions = self.decoder.decode(content="   \n  ", tool_calls=None)
        assert actions == []
