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

    # Patterns indicating the LLM considers its task complete
    _DONE_PATTERNS = re.compile(
        r"""
        (?:^|\W)
        (?:
            已完成|已解答|已回答|
            done|finished|complete|completed|
            that(?:'s|\s+is)\s+all|
            no\s+(?:further|more)\s+(?:action|step|tool)s?\s+(?:needed|required)
        )
        (?:\W|$)
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    _PLANNING_WORDS = (
        "next", "now i", "let me", "i will", "i'll", "i need to",
        "first", "then", "step", "接下来", "首先", "然后",
    )

    def decode(
        self,
        content: Optional[str],
        tool_calls: Optional[List[Any]],
        text_is_final: bool = True,
    ) -> List[ActionIR]:
        """Decode LLM output into a list of ActionIR instructions.

        Args:
            content: Text content from LLM response.
            tool_calls: Native tool call objects from the API.
            text_is_final: If True, pure text → REPLY; if False, use heuristics.
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

            if text_is_final:
                # Interactive mode: text is always a reply
                actions.append(ActionIR(kind="REPLY", args={"text": text}))
            elif self._is_done(text):
                # Agent thinks it's done
                actions.append(ActionIR(kind="RETURN", args={"text": text}))
            else:
                actions.append(ActionIR(kind="THOUGHT", args={"text": text}))

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

        # Parse arguments JSON
        try:
            args = json.loads(args_str) if isinstance(args_str, str) else (args_str or {})
        except json.JSONDecodeError as e:
            raise Fault(
                domain="LLM", code="ILL_INSTRUCTION",
                message=f"Invalid JSON in tool arguments: {e}",
                retryable=True,
                context={"tool": name, "raw_args": str(args_str)[:200]},
            )

        return ActionIR(kind="TOOL_CALL", name=name, id=tc_id, args=args)

    @classmethod
    def _is_done(cls, text: str) -> bool:
        """Heuristic: is this text a final answer rather than an intermediate thought?"""
        stripped = text.strip()

        # Short text with done-pattern
        if len(stripped) <= 300 and cls._DONE_PATTERNS.search(stripped):
            return True

        # Very short text without planning language
        if len(stripped) <= 120:
            lower = stripped.lower()
            if not any(w in lower for w in cls._PLANNING_WORDS):
                return True

        return False
