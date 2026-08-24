"""Tests for TurnAssembler — the pure decode stage (hax turn.c invariants)."""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from nimbus.core.turn_assembler import TurnAssembler


@dataclass
class Ev:
    """Structural stand-in for adapters.types.LLMStreamEvent."""
    type: str
    text: str = ""
    tool_call: Optional[Dict[str, Any]] = None
    usage: Optional[Dict[str, Any]] = None
    error: Any = None
    extra: Dict[str, Any] = field(default_factory=dict)


class TestAssembly:
    def test_text_accumulates_in_order(self):
        a = TurnAssembler()
        for c in ["Hel", "lo ", "world"]:
            a.consume(Ev("text", text=c))
        a.consume(Ev("stop"))
        assert a.text == "Hello world"
        assert a.state == "done"

    def test_tool_calls_collect_and_format(self):
        a = TurnAssembler()
        a.consume(Ev("tool_call", tool_call={"id": "t1", "name": "Read",
                                             "arguments": {"path": "/x"}}))
        a.consume(Ev("tool_call", tool_call={"id": "t2", "name": "Bash",
                                             "arguments": "raw-string"}))
        a.consume(Ev("stop"))
        raw = a.raw_tool_calls
        assert [tc["id"] for tc in raw] == ["t1", "t2"]
        formatted = a.openai_tool_calls()
        assert formatted[0]["function"]["arguments"] == '{"path": "/x"}'
        assert formatted[1]["function"]["arguments"] == "raw-string"
        assert all(tc["type"] == "function" for tc in formatted)

    def test_usage_last_write_wins_per_key(self):
        """message_start reports input; message_delta refines output."""
        a = TurnAssembler()
        a.consume(Ev("usage", usage={"input": 100, "output": 1}))
        a.consume(Ev("usage", usage={"output": 42, "total": 142}))
        a.consume(Ev("stop"))
        assert a.usage == {"input": 100, "output": 42, "total": 142}

    def test_thinking_and_unknown_are_noops(self):
        a = TurnAssembler()
        a.consume(Ev("thinking"))
        a.consume(Ev("mystery_kind"))
        assert a.text == "" and a.raw_tool_calls == []
        assert a.state == "streaming"


class TestTerminalClosure:
    def test_events_after_stop_are_ignored(self):
        a = TurnAssembler()
        a.consume(Ev("text", text="final"))
        a.consume(Ev("stop"))
        a.consume(Ev("text", text="stray"))
        a.consume(Ev("tool_call", tool_call={"id": "x", "name": "n", "arguments": {}}))
        assert a.text == "final"
        assert a.raw_tool_calls == []

    def test_error_preserves_the_scene(self):
        """Failure clears nothing — the fault layer decides what survives."""
        a = TurnAssembler()
        a.consume(Ev("text", text="partial answ"))
        a.consume(Ev("tool_call", tool_call={"id": "t1", "name": "Read", "arguments": {}}))
        a.consume(Ev("error", error="boom"))
        assert a.state == "failed"
        assert a.error == "boom"
        assert a.text == "partial answ"
        assert len(a.raw_tool_calls) == 1
        a.consume(Ev("text", text="stray"))  # terminal closure holds
        assert a.text == "partial answ"

    def test_empty_stream(self):
        a = TurnAssembler()
        a.consume(Ev("stop"))
        assert a.text == "" and not a.has_text
        assert a.raw_tool_calls == [] and a.usage is None
