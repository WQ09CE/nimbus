"""
VCPU (Virtual CPU) — The Think-Act-Observe execution engine.

Implements the core agent loop as a Finite State Machine:
  INIT → REASONING → ACTION_EXECUTION → OBSERVATION → (back to INIT or COMPLETED)

Why FSM instead of a simple while loop?
- Each state transition is explicit and validated
- Interruption is clean (check between states)
- Execution is observable (emit events at transitions)
- Error recovery is a first-class state, not an ad-hoc catch

This is the "brain" that ties together:
- ALU (LLM) for thinking
- Decoder for translating LLM output to ActionIR
- Gate for executing tools
- MMU for managing context
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from .protocol import ActionIR, Fault, StepResult, ToolResult

logger = logging.getLogger("nimbus.vcpu")


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class VCPUConfig:
    max_iterations: int = 50
    max_consecutive_thoughts: int = 8
    max_consecutive_errors: int = 3
    llm_call_timeout: float = 300.0


# =============================================================================
# Protocols — what the VCPU expects from its collaborators
# =============================================================================


class ALUProtocol(Protocol):
    """The LLM adapter (Arithmetic Logic Unit)."""
    async def chat(self, messages: List[Dict], tools: List[Dict]) -> Any: ...


class GateProtocol(Protocol):
    """The tool execution gate."""
    async def syscall_tool(self, action: ActionIR, timeout: Optional[float] = None) -> ToolResult: ...


class DecoderProtocol(Protocol):
    """The instruction decoder."""
    def decode(self, content: Optional[str], tool_calls: Optional[List], text_is_final: bool = True) -> List[ActionIR]: ...


# =============================================================================
# Execution State (counters)
# =============================================================================


@dataclass
class ExecutionState:
    iteration: int = 0
    consecutive_thoughts: int = 0
    consecutive_errors: int = 0

    def on_action(self) -> None:
        self.consecutive_thoughts = 0
        self.consecutive_errors = 0

    def on_thought(self) -> int:
        self.consecutive_thoughts += 1
        return self.consecutive_thoughts

    def on_error(self) -> int:
        self.consecutive_errors += 1
        return self.consecutive_errors


# =============================================================================
# VCPU — The FSM engine
# =============================================================================


class VCPU:
    """Virtual CPU implementing Think-Act-Observe via FSM."""

    def __init__(
        self,
        alu: ALUProtocol,
        decoder: DecoderProtocol,
        gate: GateProtocol,
        mmu: Any,  # MMU instance
        tools: List[Dict[str, Any]],
        config: Optional[VCPUConfig] = None,
        text_is_final: bool = True,
    ):
        self.alu = alu
        self.decoder = decoder
        self.gate = gate
        self.mmu = mmu
        self.tools = tools
        self.config = config or VCPUConfig()
        self.text_is_final = text_is_final

        self._exec = ExecutionState()
        self._interrupted = False

    def request_interruption(self) -> None:
        self._interrupted = True

    @property
    def iteration(self) -> int:
        return self._exec.iteration

    async def step(self) -> StepResult:
        """Drive the FSM forward by one complete Think-Act-Observe cycle.

        Returns a StepResult. If is_final=True, the agent is done.
        """
        result = StepResult()

        # Check interruption
        if self._interrupted:
            result.is_final = True
            result.final_result = ToolResult(
                status="CANCELLED", output="Execution interrupted.", is_final=True,
            )
            return result

        # Check iteration limit
        self._exec.iteration += 1
        if self._exec.iteration > self.config.max_iterations:
            result.is_final = True
            result.final_result = ToolResult(
                status="ERROR",
                output=f"Max iterations ({self.config.max_iterations}) reached.",
                fault=Fault(domain="RESOURCE", code="BUDGET_EXCEEDED",
                            message="Max iterations", retryable=False),
                is_final=True,
            )
            return result

        # ---- THINK (Reasoning) ----
        try:
            messages = self.mmu.assemble_context()
            response = await asyncio.wait_for(
                self.alu.chat(messages, self.tools),
                timeout=self.config.llm_call_timeout,
            )
        except asyncio.TimeoutError:
            return self._error_step(result, "LLM call timed out", retryable=True)
        except Exception as e:
            return self._error_step(result, f"LLM error: {e}")

        # ---- DECODE ----
        content = getattr(response, "content", None)
        tool_calls = getattr(response, "tool_calls", None)

        try:
            actions = self.decoder.decode(content, tool_calls, text_is_final=self.text_is_final)
        except Fault as f:
            # Hallucination or parse error — inject feedback and retry
            self.mmu.add_system_message(f"[Decoder Error] {f.message}")
            errs = self._exec.on_error()
            if errs >= self.config.max_consecutive_errors:
                return self._error_step(result, f"Too many decode errors: {f.message}")
            return result  # non-final, will retry

        if not actions:
            self.mmu.add_system_message("Empty response. You MUST call a tool or return a final answer.")
            self._exec.on_error()
            return result

        result.actions = actions

        # ---- ROUTE: RETURN/REPLY → done ----
        for action in actions:
            if action.kind in ("RETURN", "REPLY"):
                text = action.args.get("text", action.args.get("result", ""))
                # Persist to MMU
                if content:
                    self.mmu.add_assistant_message(content)
                elif text:
                    self.mmu.add_assistant_message(text)
                result.is_final = True
                result.final_result = ToolResult(status="OK", output=text, is_final=True)
                return result

        # ---- ACT (execute tool calls) ----
        tool_actions = [a for a in actions if a.kind == "TOOL_CALL"]
        thought_text = None
        for a in actions:
            if a.kind == "THOUGHT":
                thought_text = a.args.get("text", "")

        if tool_actions:
            self._exec.on_action()

            # Persist assistant message with tool_calls
            tc_dicts = [{
                "id": a.id, "type": "function",
                "function": {"name": a.name, "arguments": json.dumps(a.args)},
            } for a in tool_actions]
            self.mmu.add_assistant_with_tool_calls(thought_text, tc_dicts)

            # Execute each tool through the Gate
            for action in tool_actions:
                tool_result = await self.gate.syscall_tool(action)
                result.results.append(tool_result)

                # ---- OBSERVE (write result to MMU) ----
                self.mmu.add_tool_result(
                    action.id, action.name, str(tool_result.output),
                )
        else:
            # Pure thought — no tool calls
            count = self._exec.on_thought()
            if thought_text:
                self.mmu.add_assistant_message(thought_text)

            if count >= self.config.max_consecutive_thoughts:
                result.is_final = True
                result.final_result = ToolResult(
                    status="OK",
                    output=thought_text or "Agent stopped after too many thoughts without action.",
                    is_final=True,
                )
                return result

        return result

    def _error_step(self, result: StepResult, message: str, retryable: bool = False) -> StepResult:
        result.is_final = True
        result.fault = Fault(domain="LLM", code="SYSTEM_ERROR", message=message, retryable=retryable)
        result.final_result = ToolResult(
            status="ERROR", output=message, fault=result.fault, is_final=True,
        )
        return result
