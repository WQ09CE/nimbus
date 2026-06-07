"""Tests for tool-call extraction + JSON-leak stripping (direct_adapter)."""

from nimbus.adapters.direct_adapter import (
    _extract_tool_calls_from_text,
    _find_json_spans,
    _strip_tool_call_blocks,
)


def test_find_json_spans_balanced():
    text = 'before {"a": 1, "b": {"c": 2}} after [1, 2] end'
    spans = _find_json_spans(text)
    segs = [text[s:e] for s, e in spans]
    assert '{"a": 1, "b": {"c": 2}}' in segs
    assert "[1, 2]" in segs


def test_find_json_spans_ignores_braces_in_strings():
    text = 'x {"k": "a } b {"} y'
    spans = _find_json_spans(text)
    assert [text[s:e] for s, e in spans] == ['{"k": "a } b {"}']


def test_strip_bare_inline_tool_call():
    # The real msg3 shape: prose + inlined tool-call JSON.
    text = (
        "My apologies. I will correct this now by writing to the correct path.\n\n"
        '{"name": "Write", "arguments": {"file_path": "a.md", "content": "x"}}'
    )
    out = _strip_tool_call_blocks(text)
    assert "name" not in out and "{" not in out
    assert out == "My apologies. I will correct this now by writing to the correct path."


def test_strip_fenced_tool_call():
    text = "Sure, running it.\n```json\n{\"name\": \"Bash\", \"arguments\": {\"command\": \"ls\"}}\n```"
    out = _strip_tool_call_blocks(text)
    assert out == "Sure, running it."


def test_strip_leaves_ordinary_json_untouched():
    # JSON the user is discussing — NOT a tool call (no name/arguments) → keep it.
    text = 'The config is {"port": 8080, "host": "localhost"} for the server.'
    assert _strip_tool_call_blocks(text) == text


def test_strip_noop_without_json():
    text = "Just a normal explanation with no JSON at all."
    assert _strip_tool_call_blocks(text) == text


def test_extract_still_works_after_changes():
    assert _extract_tool_calls_from_text('{"name":"Bash","arguments":{"command":"pwd"}}')
    assert _extract_tool_calls_from_text("prefix\n```json\n{\"name\":\"Read\",\"arguments\":{}}\n```")
    assert _extract_tool_calls_from_text("no tool call here") is None
