"""
Instruction Decoder — The Firewall between LLM and vCPU.

Translates raw LLM output into ActionIR instructions.
Detects and rejects hallucinated tool calls (text-based simulation).

This is nimbus's key differentiator vs simpler agent frameworks:
the LLM output is UNTRUSTED and must pass through this firewall.
"""

import json
import re
from typing import Any, List, Optional

from .protocol import ActionIR, Fault


class InstructionDecoder:
    """Decode LLM responses into ActionIR instructions."""

    # Text patterns that indicate the LLM is simulating tool calls
    # instead of using the function calling API ("the Gemini Patch")
    HALLUCINATION_PATTERNS = [
        "[Called",
        "[Calling",
        "[Tool:",
        "[Execute:",
        "```tool",
        "<tool_call>",
        "<function_call>",
    ]

    _ANNOUNCED_TOOL_INTENT_RE = re.compile(
        r"""
        (?:
            \b(?:i\s+(?:will|am\s+going\s+to|need\s+to)|i'll|let\s+me|now\s+i)\b
            .{0,80}
            \b(?:use|call|invoke|execute|run)\b
            .{0,80}
            \b(?:Read|Write|Edit|Bash|Grep|spawn_agent|submit_result)\b
        )
        |
        (?:
            (?:我(?:将|会|需要|来)|接下来我|现在我|开始)
            .{0,80}
            (?:使用|调用|执行|运行|启动)
            .{0,80}
            (?:Read|Write|Edit|Bash|Grep|spawn_agent|submit_result)
        )
        """,
        re.IGNORECASE | re.VERBOSE | re.DOTALL,
    )

    def decode(
        self,
        content: Optional[str],
        tool_calls: Optional[List[Any]],
        text_is_final: bool = True,  # accepted for compat; text always ends the turn
        contract_mode: bool = False,
    ) -> List[ActionIR]:
        """Decode LLM output into a list of ActionIR instructions.

        Termination inversion (see docs/design/termination-inversion.md):
        pure text ALWAYS ends the turn (REPLY) — termination is the model's
        decision, and the evidence-based guards in the VCPU (narrate / claim)
        are the only exit gates. The old run-mode `_is_done()` lexical
        heuristic penalized long complete answers into THOUGHT loops
        (measured: the same answer regenerated 8x before stall forced an end).

        Args:
            content: Text content from LLM response.
            tool_calls: Native tool call objects from the API.
            text_is_final: Ignored (kept so existing call sites don't break).
            contract_mode: If True, pure text is ALWAYS THOUGHT, never REPLY.
                A sub-agent's exit is a structured contract (submit_result),
                not a guess — speaking does not end its turn.
        """
        actions: List[ActionIR] = []

        # 1. Check for hallucinated tool calls in text
        if content and not tool_calls:
            self._check_hallucination(content)

        # 2. Native tool calls present → map each to ActionIR
        if tool_calls:
            # Text alongside tool calls = non-blocking thought
            if content and content.strip():
                actions.append(ActionIR(
                    kind="THOUGHT", name="thought",
                    args={"text": content.strip()},
                ))
            for tc in tool_calls:
                actions.append(self._map_tool_call(tc))
            return actions

        # 3. Pure text, no tool calls
        if content and content.strip():
            text = content.strip()
            if contract_mode:
                actions.append(ActionIR(kind="THOUGHT", args={"text": text}))
            else:
                actions.append(ActionIR(kind="REPLY", args={"text": text}))

        return actions

    def _check_hallucination(self, content: str) -> None:
        """Raise Fault if text contains tool simulation patterns."""
        stripped = content.strip()
        is_short = len(stripped) <= 300

        for pattern in self.HALLUCINATION_PATTERNS:
            if pattern not in content:
                continue
            # Short text: any match is suspicious
            # Long text: only flag if pattern is near the start
            if is_short or stripped[:100].find(pattern) >= 0:
                raise Fault(
                    domain="LLM",
                    code="ILL_INSTRUCTION",
                    message=f"Detected text-based tool simulation (pattern: '{pattern}'). "
                    "Use the function calling API instead.",
                    retryable=True,
                    context={"pattern": pattern},
                )

        if self._ANNOUNCED_TOOL_INTENT_RE.search(stripped):
            raise Fault(
                domain="LLM",
                code="ILL_INSTRUCTION",
                message=(
                    "The model announced it would use a tool but did not emit a tool call. "
                    "When a tool is needed, output only a JSON tool call with the exact "
                    "tool name and arguments."
                ),
                retryable=True,
                context={"pattern": "announced_tool_intent_without_call"},
            )

    def _map_tool_call(self, tool_call: Any) -> ActionIR:
        """Convert a tool call object to ActionIR."""
        # Support OpenAI-style objects and dicts
        tc_id = None
        if hasattr(tool_call, "function"):
            name = tool_call.function.name
            args_str = tool_call.function.arguments
            tc_id = getattr(tool_call, "id", None)
        elif isinstance(tool_call, dict):
            func = tool_call.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "{}")
            tc_id = tool_call.get("id")
        else:
            raise Fault(
                domain="LLM", code="ILL_INSTRUCTION",
                message=f"Unknown tool call format: {type(tool_call)}",
            )

        # Parse arguments JSON. Weak models routinely append trailing junk
        # after a valid object ("{...} extra", two objects glued together) —
        # salvage the FIRST valid JSON value instead of failing the call
        # (observed: qwen failed a 3-step task with three straight
        # "Extra data" decode faults while genuinely trying to act).
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else (args_str or {})
        except json.JSONDecodeError as e:
            salvaged = None
            if isinstance(args_str, str):
                try:
                    salvaged, _ = json.JSONDecoder().raw_decode(args_str.strip())
                except json.JSONDecodeError:
                    salvaged = None
            if isinstance(salvaged, dict):
                args = salvaged
            else:
                raise Fault(
                    domain="LLM", code="ILL_INSTRUCTION",
                    message=f"Invalid JSON in tool arguments: {e}",
                    retryable=True,
                    context={"tool": name, "raw_args": str(args_str)[:200]},
                )

        return ActionIR(kind="TOOL_CALL", name=name, id=tc_id, args=args)

