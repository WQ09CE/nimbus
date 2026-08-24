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


# =============================================================================
# Extended coverage (hax test_turn.c parity) — ordering, terminal-closure
# variants, usage semantics, formatting edges, degenerate streams
# =============================================================================


class TestInterleaving:
    def test_text_tool_text_tool_preserves_both_orders(self):
        a = TurnAssembler()
        a.consume(Ev("text", text="I'll read the file. "))
        a.consume(Ev("tool_call", tool_call={"id": "t1", "name": "Read", "arguments": {}}))
        a.consume(Ev("text", text="Then run tests."))
        a.consume(Ev("tool_call", tool_call={"id": "t2", "name": "Bash", "arguments": {}}))
        a.consume(Ev("stop"))
        assert a.text == "I'll read the file. Then run tests."
        assert [tc["id"] for tc in a.raw_tool_calls] == ["t1", "t2"]

    def test_tool_before_any_text(self):
        a = TurnAssembler()
        a.consume(Ev("tool_call", tool_call={"id": "t1", "name": "Read", "arguments": {}}))
        a.consume(Ev("text", text="done"))
        a.consume(Ev("stop"))
        assert a.text == "done" and len(a.raw_tool_calls) == 1

    def test_whitespace_text_preserved_verbatim(self):
        """The assembler must not trim — presentation decides, not decode."""
        a = TurnAssembler()
        a.consume(Ev("text", text="  \n"))
        a.consume(Ev("text", text="indented"))
        a.consume(Ev("stop"))
        assert a.text == "  \nindented"


class TestTerminalClosureVariants:
    def test_double_stop_idempotent(self):
        a = TurnAssembler()
        a.consume(Ev("stop"))
        a.consume(Ev("stop"))
        assert a.state == "done"
        assert a.dropped_after_terminal == 0  # stop is not substantive

    def test_first_terminal_wins_error_after_stop(self):
        a = TurnAssembler()
        a.consume(Ev("stop"))
        a.consume(Ev("error", error="late boom"))
        assert a.state == "done"
        assert a.error is None

    def test_first_terminal_wins_stop_after_error(self):
        a = TurnAssembler()
        a.consume(Ev("error", error="boom"))
        a.consume(Ev("stop"))
        assert a.state == "failed"
        assert a.error == "boom"

    def test_dropped_substantive_events_are_counted(self):
        """The usage-before-stop provider contract: violations must be
        observable — a dropped usage event is silent billing loss."""
        a = TurnAssembler()
        a.consume(Ev("stop"))
        a.consume(Ev("text", text="late"))
        a.consume(Ev("usage", usage={"input": 5}))
        a.consume(Ev("tool_call", tool_call={"id": "x", "name": "n", "arguments": {}}))
        a.consume(Ev("thinking"))  # keepalive after stop: not substantive
        assert a.dropped_after_terminal == 3
        assert a.usage is None

    def test_error_scene_includes_usage(self):
        """Usage reported before the failure survives (retry accounting)."""
        a = TurnAssembler()
        a.consume(Ev("usage", usage={"input": 100, "output": 3}))
        a.consume(Ev("error", error="cut"))
        assert a.usage == {"input": 100, "output": 3}


class TestUsageSemantics:
    def test_single_report_passthrough(self):
        a = TurnAssembler()
        a.consume(Ev("usage", usage={"input": 10, "output": 2, "total": 12}))
        a.consume(Ev("stop"))
        assert a.usage == {"input": 10, "output": 2, "total": 12}

    def test_multi_report_partial_refine(self):
        """message_start-style then message_delta-style: later keys refine,
        earlier keys survive (merge, not replace — a partial second report
        must not zero the first's fields)."""
        a = TurnAssembler()
        a.consume(Ev("usage", usage={"input": 100, "cache_read": 50}))
        a.consume(Ev("usage", usage={"output": 42}))
        a.consume(Ev("stop"))
        assert a.usage == {"input": 100, "cache_read": 50, "output": 42}

    def test_usage_none_payload_ignored(self):
        a = TurnAssembler()
        a.consume(Ev("usage", usage=None))
        a.consume(Ev("stop"))
        assert a.usage is None


class TestToolCallFormatting:
    def test_none_arguments_pass_through(self):
        """Pinned legacy equivalence: non-dict arguments (incl. None) are not
        encoded — downstream decoder handles raw forms."""
        a = TurnAssembler()
        a.consume(Ev("tool_call", tool_call={"id": "t", "name": "N", "arguments": None}))
        a.consume(Ev("stop"))
        assert a.openai_tool_calls()[0]["function"]["arguments"] is None

    def test_empty_dict_arguments_encode_to_empty_object(self):
        a = TurnAssembler()
        a.consume(Ev("tool_call", tool_call={"id": "t", "name": "N", "arguments": {}}))
        a.consume(Ev("stop"))
        assert a.openai_tool_calls()[0]["function"]["arguments"] == "{}"

    def test_missing_id_and_name_survive_as_none(self):
        a = TurnAssembler()
        a.consume(Ev("tool_call", tool_call={"arguments": {"k": 1}}))
        a.consume(Ev("stop"))
        f = a.openai_tool_calls()[0]
        assert f["id"] is None and f["function"]["name"] is None

    def test_cjk_arguments_json_roundtrip(self):
        import json as _json
        a = TurnAssembler()
        a.consume(Ev("tool_call", tool_call={"id": "t", "name": "Write",
                                             "arguments": {"content": "背景改纯白"}}))
        a.consume(Ev("stop"))
        encoded = a.openai_tool_calls()[0]["function"]["arguments"]
        assert _json.loads(encoded) == {"content": "背景改纯白"}

    def test_raw_tool_calls_returns_copy(self):
        a = TurnAssembler()
        a.consume(Ev("tool_call", tool_call={"id": "t", "name": "N", "arguments": {}}))
        view = a.raw_tool_calls
        view.clear()
        assert len(a.raw_tool_calls) == 1


class TestDegenerateStreams:
    def test_no_events_at_all(self):
        a = TurnAssembler()
        assert a.state == "streaming"
        assert a.text == "" and a.raw_tool_calls == [] and a.usage is None

    def test_usage_only_stream(self):
        a = TurnAssembler()
        a.consume(Ev("usage", usage={"input": 1}))
        a.consume(Ev("stop"))
        assert not a.has_text and a.usage == {"input": 1}

    def test_error_with_no_message_gets_fallback(self):
        a = TurnAssembler()
        a.consume(Ev("error", error=None))
        assert a.state == "failed"
        assert a.error == "stream error"
