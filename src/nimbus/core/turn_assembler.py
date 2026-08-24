"""TurnAssembler — pure state machine assembling one LLM stream into a turn.

The decode stage of the pipeline (hax turn.c shape): consumes normalized
stream events and accumulates them into the turn's assembled parts — text,
complete tool calls, usage, error — with hax's invariants:

- ORDERING: parts accumulate in arrival order.
- TERMINAL CLOSURE: after done/error every further event is a no-op, so a
  misbehaving stream cannot pollute a finished assembly.
- ERROR PRESERVES THE SCENE: failure does not clear anything; the caller
  (fault_semantics enforcement) decides what survives.

Differences from hax kept deliberate: nimbus providers deliver COMPLETE tool
calls (per-provider argument-delta pairing stays in the adapter loops, the
analog of hax's providers/*), so no pending-call machinery lives here.

Zero dependencies by design — events are consumed structurally (anything
with .type / .text / .tool_call / .usage / .error attributes, i.e.
adapters.types.LLMStreamEvent without importing it). This keeps the module
a pure function of the event sequence: feed events, read parts.

Wired into DirectAdapter.chat(); the litellm/openai streaming loops still
carry inline assembly of their raw provider chunks and are migration
targets (their OUTPUT — LLMStreamEvent — already flows through here).
"""

import json
from typing import Any, Dict, List, Literal, Optional

AssemblyState = Literal["streaming", "done", "failed"]


class TurnAssembler:
    """Assemble one stream of normalized LLM events into turn parts."""

    def __init__(self) -> None:
        self._text_parts: List[str] = []
        self._tool_calls: List[Dict[str, Any]] = []
        self._usage: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None
        self.state: AssemblyState = "streaming"

    # --- consumption ---

    def consume(self, event: Any) -> None:
        """Consume one event. No-op once the assembly is terminal."""
        if self.state != "streaming":
            return
        etype = getattr(event, "type", None)
        if etype == "text":
            if event.text:
                self._text_parts.append(event.text)
        elif etype == "tool_call":
            if event.tool_call:
                self._tool_calls.append(event.tool_call)
        elif etype == "usage":
            if event.usage:
                # Later usage reports refine earlier ones (message_delta
                # over message_start); last write wins per key.
                self._usage = {**(self._usage or {}), **event.usage}
        elif etype == "stop":
            self.state = "done"
        elif etype == "error":
            # Keep everything assembled so far — the fault layer decides
            # what survives (fault_semantics: error preserves the scene).
            self._error = str(getattr(event, "error", "") or "stream error")
            self.state = "failed"
        # "thinking" and unknown kinds: keepalive/no-op.

    # --- assembled parts ---

    @property
    def text(self) -> str:
        return "".join(self._text_parts)

    @property
    def has_text(self) -> bool:
        return bool(self._text_parts)

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def usage(self) -> Optional[Dict[str, Any]]:
        return self._usage

    @property
    def raw_tool_calls(self) -> List[Dict[str, Any]]:
        """Tool calls as the providers delivered them: {id, name, arguments}."""
        return list(self._tool_calls)

    def openai_tool_calls(self) -> List[Dict[str, Any]]:
        """Tool calls in OpenAI function-call format, arguments JSON-encoded
        (dict arguments are serialized; string arguments pass through)."""
        formatted = []
        for tc in self._tool_calls:
            args = tc.get("arguments")
            formatted.append({
                "id": tc.get("id"),
                "type": "function",
                "function": {
                    "name": tc.get("name"),
                    "arguments": json.dumps(args) if isinstance(args, dict) else args,
                },
            })
        return formatted
