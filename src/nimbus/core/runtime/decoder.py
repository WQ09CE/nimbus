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

    def decode(
        self,
        content: Optional[str],
        tool_calls: Optional[List[Any]],
    ) -> List[ActionIR]:
        """
        Decode LLM output into ActionIR instructions.

        Args:
            content: Text content from LLM response
            tool_calls: List of tool call objects from LLM response

        Returns:
            List of ActionIR instructions

        Raises:
            Fault: If hallucination is detected or arguments are invalid
        """
        actions = []

        # 1. Firewall: Check for text-based tool simulation (The "Gemini Patch")
        if content:
            self._check_hallucination(content)

        # 2. Parse Native Tool Calls
        if tool_calls:
            for tc in tool_calls:
                action = self._map_tool_call(tc)
                actions.append(action)

        # 3. Handle pure thought/text if no tool calls
        elif content and content.strip():
            actions.append(ActionIR(kind="THOUGHT", name="thought", args={"text": content.strip()}))

        return actions

    def _check_hallucination(self, content: str) -> None:
        """
        Check for text-based tool simulation patterns.

        Raises:
            Fault: If hallucination pattern is detected
        """
        for pattern in self.HALLUCINATION_PATTERNS:
            if pattern in content:
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
