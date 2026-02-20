"""
Nimbus v2 Instruction Decoder - The Firewall

This module translates raw LLM output into ActionIR instructions.
It acts as a firewall against hallucinations and malformed requests.

Key Features:
- Detects text-based tool simulation (the "Gemini Patch")
- Maps special tool calls to control flow actions
- Validates tool arguments

The Decoder sits between the LLM (ALU) and the vCPU (Control Unit).
"""

import json
import re
from typing import Any, List, Optional, Protocol

from nimbus.core.protocol import ActionIR, Fault


class ToolCall(Protocol):
    """Protocol for tool call objects from various LLM providers."""

    @property
    def function(self) -> Any:
        """The function object containing name and arguments."""
        ...


class InstructionDecoder:
    """
    Translates raw LLM output into ActionIR.
    Acts as a firewall against hallucinations and malformed requests.

    The decoder performs three key functions:
    1. Detects and rejects text-based tool simulation (hallucination)
    2. Maps native tool calls to ActionIR
    3. Routes special tools (call_subroutine, return_result) to control flow

    Example:
        decoder = InstructionDecoder()
        actions = decoder.decode(content="Let me think...", tool_calls=[...])
    """

    # Patterns that indicate text-based tool simulation
    HALLUCINATION_PATTERNS = [
        "[Called",
        "[Calling",
        "[Tool:",
        "[Execute:",
        "```tool",
        "<tool_call>",
        "<function_call>",
        "[Historical context:",  # Fix for GPT-5.3/Gemini hallucinating context
        "Do not mimic this format",
    ]

    # Patterns that indicate the LLM has finished and needs no further action.
    # These are checked against pure-text responses (no tool calls).
    _DONE_PATTERNS = re.compile(
        r"""
        (?:^|\W)                       # word boundary
        (?:
            已完成|已解答|已回答|        # Chinese completions
            done|finished|complete|completed|  # English completions
            that(?:'s|\s+is)\s+all|
            no\s+(?:further|more|additional)\s+(?:action|step|tool)s?\s+(?:needed|required|necessary)
        )
        (?:\W|$)
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    @classmethod
    def _is_conversational_reply(cls, text: str) -> bool:
        """
        Heuristic: detect whether a pure-text LLM response is a conversational
        final answer (should map to RETURN) rather than an intermediate thought
        (should map to THOUGHT).

        Returns True if the text appears to be a self-contained final answer.
        """
        stripped = text.strip()

        # Rule 1: Explicit done markers
        if cls._DONE_PATTERNS.search(stripped):
            return True

        # Rule 2: Very short responses (≤ 120 chars) with no planning language
        # are almost certainly direct answers or greetings.
        _PLANNING_WORDS = ("next", "now i", "let me", "i will", "i'll", "i need to",
                           "first", "then", "step", "接下来", "首先", "然后", "我需要", "我将")
        if len(stripped) <= 120:
            lower = stripped.lower()
            if not any(w in lower for w in _PLANNING_WORDS):
                return True

        return False

    # Special tool names that map to control flow actions
    CONTROL_FLOW_TOOLS = {
        "call_subroutine": "SUB_CALL",
        "spawn_subprocess": "SUB_CALL",
        "return_result": "RETURN",
        "task_complete": "RETURN",
        "post_ipc": "POST_IPC",
        "publish_result": "POST_IPC",
        "request_replan": "REQUEST_REPLAN",
        "need_replan": "REQUEST_REPLAN",
        "cancel_task": "CANCEL",
    }

    # Regex to capture content inside <reply>...</reply> tags
    REPLY_TAG_PATTERN = re.compile(r"<reply>(.*?)</reply>", re.DOTALL | re.IGNORECASE)

    def decode(
        self,
        content: Optional[str],
        tool_calls: Optional[List[Any]],
        role: Optional[str] = None,
    ) -> List[ActionIR]:
        """
        Decode LLM output into ActionIR instructions.

        Args:
            content: Text content from LLM response
            tool_calls: List of tool call objects from LLM response
            role: The role of the agent being decoded (e.g., 'orchestrator')

        Returns:
            List of ActionIR instructions

        Raises:
            Fault: If hallucination is detected or arguments are invalid
        """
        actions = []

        # 1. Parse <reply> tags if present in content
        if content and (reply_match := self.REPLY_TAG_PATTERN.search(content)):
            reply_text = reply_match.group(1).strip()
            actions.append(ActionIR(kind="REPLY", name="reply", args={"text": reply_text}))
            # If we found a <reply> tag, we ignore other tool calls or text to enforce 
            # the "reply is final" semantics (similar to RETURN).
            return actions

        # 2. Parse Native Tool Calls
        if tool_calls:
            for tc in tool_calls:
                action = self._map_tool_call(tc)
                actions.append(action)

            # 2b. Text alongside tool calls = non-blocking thought (LLM commentary)
            # This mirrors what MixedResponseSplitter does for models with
            # split_mixed_responses=True. The text is just the LLM explaining
            # what it's doing — it must NOT trigger REPLY/RETURN termination.
            if content and content.strip():
                actions.append(ActionIR(
                    kind="THOUGHT", name="thought",
                    args={"text": content.strip()},
                    meta={"non_blocking": True},
                ))
            return actions

        # 3. Handle Orchestrator-specific conversation logic
        # If an Orchestrator role provides text but NO valid tool calls,
        # we treat it as a REPLY (conversational response) rather than a THOUGHT.
        # This prevents the system from re-prompting (System Poke) when the
        # Orchestrator is simply talking to the user.
        if role == "orchestrator" and content and content.strip():
            # Double check for simulated calls in content (hallucinations)
            self._check_hallucination(content)
            actions.append(ActionIR(kind="REPLY", name="reply", args={"text": content.strip()}))
            return actions

        # 4. Handle pure thought/text if no tool calls (General Agent logic)
        elif content and content.strip():
            stripped = content.strip()
            # Heuristic: short conversational replies or explicit "done" signals
            # should be treated as REPLY, not THOUGHT, to avoid re-prompting loops.
            kind = "REPLY" if self._is_conversational_reply(stripped) else "THOUGHT"
            actions.append(ActionIR(kind=kind, name="thought" if kind == "THOUGHT" else "reply", args={"text": stripped}))

        return actions

    def _check_hallucination(self, content: str) -> None:
        """
        Check for text-based tool simulation patterns in pure-text responses.

        Uses a two-tier approach to avoid false positives:
        - Short text (≤ 300 chars): any pattern match triggers (likely a fake tool call)
        - Long text (> 300 chars): only triggers if a pattern appears near the start,
          indicating the response IS a fake tool call rather than a discussion ABOUT one.

        Raises:
            Fault: If hallucination pattern is detected
        """
        stripped = content.strip()
        is_short = len(stripped) <= 300

        for pattern in self.HALLUCINATION_PATTERNS:
            if pattern not in content:
                continue

            # Short text with pattern = almost certainly a fake tool call
            # Long text = only flag if pattern is near the beginning (first 100 chars)
            if is_short or content.strip()[:100].find(pattern) >= 0:
                raise Fault(
                    domain="LLM",
                    code="ILL_INSTRUCTION",
                    message=f"Detected text-based tool simulation (pattern: '{pattern}'). "
                    "You MUST use the function calling API, not text simulation. "
                    "Call the actual tool functions instead of writing them as text.",
                    retryable=True,  # Allow retry so LLM can correct itself
                    context={"raw_content": content[:500], "pattern": pattern},
                )

    def _map_tool_call(self, tool_call: Any) -> ActionIR:
        """
        Map a tool call to an ActionIR instruction.

        Args:
            tool_call: Tool call object from LLM

        Returns:
            ActionIR instruction

        Raises:
            Fault: If arguments are invalid JSON
        """
        # Extract name, arguments, and tool_call_id
        # Support both OpenAI-style and generic dict-style tool calls
        tool_call_id = None
        if hasattr(tool_call, "function"):
            name = tool_call.function.name
            args_str = tool_call.function.arguments
            tool_call_id = getattr(tool_call, "id", None)
        elif isinstance(tool_call, dict):
            func = tool_call.get("function", {})
            name = func.get("name", "")
            args_str = func.get("arguments", "{}")
            tool_call_id = tool_call.get("id")
        else:
            raise Fault(
                domain="LLM",
                code="ILL_INSTRUCTION",
                message=f"Unknown tool call format: {type(tool_call)}",
                retryable=False,
            )

        # Parse arguments
        try:
            if isinstance(args_str, str):
                args = json.loads(args_str) if args_str else {}
            else:
                args = args_str if args_str else {}
        except json.JSONDecodeError as e:
            raise Fault(
                domain="LLM",
                code="ILL_INSTRUCTION",
                message=f"Invalid JSON in tool arguments: {e}",
                retryable=True,
                context={"tool_name": name, "raw_args": args_str[:200]},
            )

        # Route to control flow or standard tool call
        if name in self.CONTROL_FLOW_TOOLS:
            kind = self.CONTROL_FLOW_TOOLS[name]
            return ActionIR(
                kind=kind,
                name=args.get("goal", args.get("name", name)),
                id=tool_call_id,  # Preserve original tool_call_id for API compatibility
                args=args,
                meta={"original_tool": name},
            )

        # Default: Standard Tool Call (Syscall)
        return ActionIR(
            kind="TOOL_CALL",
            name=name,
            id=tool_call_id,  # Preserve original tool_call_id for API compatibility
            args=args,
        )

    def add_hallucination_pattern(self, pattern: str) -> None:
        """Add a custom hallucination detection pattern."""
        if pattern not in self.HALLUCINATION_PATTERNS:
            self.HALLUCINATION_PATTERNS.append(pattern)

    def add_control_flow_tool(self, tool_name: str, action_kind: str) -> None:
        """Register a custom control flow tool mapping."""
        self.CONTROL_FLOW_TOOLS[tool_name] = action_kind

    def _is_conversational_reply(self, content: str) -> bool:
        """
        Check if the content is likely a conversational reply rather than a thought.
        """
        # Conversational markers
        reply_markers = [
            "?",
            "你好",
            "hello",
            "hi ",
            "完成",
            "done",
            "fixed",
            "已修复",
            "请问",
            "我可以",
            "帮您",
            "还有什么",
        ]
        lower_content = content.lower()
        if any(marker in lower_content for marker in reply_markers):
            return True

        # Very short responses are likely replies
        if len(content) < 50:
            return True

        return False
