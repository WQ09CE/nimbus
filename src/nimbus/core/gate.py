"""
Kernel Gate — The syscall layer for tool execution.

All tool calls flow through the Gate. It provides:
1. Arg normalization (fix LLM hallucinated param names)
2. Doom loop detection (same tool+args repeated N times)
3. Timeout enforcement (asyncio.wait_for)
4. Output truncation (prevent context blowup)
5. Event emission (TOOL_STARTED / TOOL_FINISHED)

This is the single bottleneck point for all side-effects.
"""

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .protocol import ActionIR, Event, Fault, ToolResult


# =============================================================================
# Doom Loop Detector (inlined — single responsibility, ~50 lines)
# =============================================================================

DOOM_LOOP_THRESHOLD = 3

DOOM_GUIDANCE = {
    "Edit": "Read the file first to get current content, then retry with exact text.",
    "Read": "File may not exist. Use Bash to find the correct path.",
    "Bash": "Same command keeps failing. Try a different approach.",
    "spawn_agent": "Same sub-agent goal keeps repeating. Revise your approach or handle the goal directly.",
}


class DoomLoopDetector:
    """Detect when the same tool call is repeated consecutively."""

    def __init__(self, threshold: int = DOOM_LOOP_THRESHOLD):
        self.threshold = threshold
        self._recent: List[Tuple[str, str]] = []
        self.trip_count = 0

    def check(self, tool_name: str, args: Dict) -> Optional[str]:
        """Check for doom loop. Returns guidance string if detected, None otherwise."""
        key = json.dumps({"t": tool_name, "a": args}, sort_keys=True)
        self._recent.append((tool_name, key))
        if len(self._recent) > self.threshold:
            self._recent = self._recent[-self.threshold:]

        if len(self._recent) == self.threshold and all(c[1] == key for c in self._recent):
            self.trip_count += 1
            self._recent.clear()
            return DOOM_GUIDANCE.get(
                tool_name,
                f"Tool '{tool_name}' is repeating with same args. Try a different approach.",
            )
        return None


# =============================================================================
# Arg Normalization
# =============================================================================

_ARG_ALIASES: Dict[str, Dict[str, str]] = {
    "Read":  {"path": "file_path", "filename": "file_path", "file": "file_path"},
    "Write": {"path": "file_path", "filename": "file_path", "file": "file_path"},
    "Edit":  {"path": "file_path", "file": "file_path",
              "old": "old_text", "oldText": "old_text",
              "new": "new_text", "newText": "new_text"},
    "Bash":  {"cmd": "command", "script": "command"},
    "Grep":  {"query": "pattern", "search": "pattern", "dir": "path"},
    "spawn_agent": {"timeout": "timeout_seconds", "task": "goal"},
}


def _normalize_args(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    aliases = _ARG_ALIASES.get(tool_name)
    if not aliases:
        return args
    normalized = dict(args)
    for alias, canonical in aliases.items():
        if alias in normalized and canonical not in normalized:
            normalized[canonical] = normalized.pop(alias)
    return normalized


# =============================================================================
# Output Truncation
# =============================================================================

MAX_OUTPUT_CHARS = 200_000
TRUNCATION_KEEP = 2_000


def _truncate_output(text: Any) -> Any:
    if not isinstance(text, str) or len(text) <= MAX_OUTPUT_CHARS:
        return text
    cut = text.rfind("\n", 0, TRUNCATION_KEEP)
    cut = cut if cut > TRUNCATION_KEEP * 0.8 else TRUNCATION_KEEP
    return text[:cut] + f"\n\n[Truncated: {len(text):,} chars → first {cut:,}]"


# =============================================================================
# Kernel Gate
# =============================================================================


class KernelGate:
    """Execute tool calls with timeout, doom loop detection, and observability."""

    def __init__(
        self,
        pid: str,
        tool_executor: Callable,
        event_callback: Optional[Callable[[Event], None]] = None,
        default_timeout: float = 60.0,
        on_tool_output: Optional[Callable[[str, str], None]] = None,
        abort_event: Optional[asyncio.Event] = None,
    ):
        self.pid = pid
        self._executor = tool_executor
        self._event_cb = event_callback
        self._default_timeout = default_timeout
        self._doom = DoomLoopDetector()
        # Pi-style: callback for streaming tool output (tool_name, chunk)
        self._on_tool_output = on_tool_output
        # Abort event -- propagated to tools (e.g., bash) for process group kill
        self._abort_event = abort_event

    async def syscall_tool(self, action: ActionIR, timeout: Optional[float] = None) -> ToolResult:
        """Execute a TOOL_CALL action through the gate."""
        tool_name = action.name
        t0 = time.monotonic()

        # 1. Emit start event (include full args and call_id for real-time SSE)
        self._emit("TOOL_STARTED", {
            "tool": tool_name,
            "call_id": action.id,
            "args": {k: str(v) for k, v in action.args.items()},
        })

        # 2. Normalize args
        action.args = _normalize_args(tool_name, action.args)

        # 3. Doom loop check
        doom_msg = self._doom.check(tool_name, action.args)
        if doom_msg:
            if self._doom.trip_count >= 2:
                # Fatal: agent is stuck
                return self._finish(action, t0, ToolResult(
                    status="ERROR",
                    output=f"Doom loop terminated: {doom_msg}",
                    fault=Fault(domain="TOOL", code="TOOL_FAILURE", message=doom_msg),
                ))
            # Warning: inject guidance, still execute
            # (first trip gives the agent a chance to self-correct)

        # 4. Execute with timeout
        # spawn_agent manages its own timeout internally; don't double-wrap it.
        if tool_name == "spawn_agent":
            effective_timeout = None
        else:
            effective_timeout = timeout or self._default_timeout

        # Inject streaming callback for tools that support it (pi-style)
        exec_args = dict(action.args)
        if self._on_tool_output and tool_name in ("Bash", "spawn_agent"):
            def _on_update(chunk: str) -> None:
                assert self._on_tool_output is not None
                self._on_tool_output(tool_name, chunk)
                self._emit("TOOL_CALL_DELTA", {"tool": tool_name, "chunk": chunk})
            exec_args["on_update"] = _on_update

        # Propagate abort event to tools that support it (pi-style process group kill)
        if self._abort_event:
            exec_args["_abort_event"] = self._abort_event

        try:
            coro = self._executor(tool_name, exec_args)
            if effective_timeout is not None:
                raw_output = await asyncio.wait_for(coro, timeout=effective_timeout)
            else:
                raw_output = await coro

            # Handle split tool results (pi-style: output + ui_detail)
            ui_detail = {}
            if isinstance(raw_output, dict) and "output" in raw_output:
                raw_text = raw_output["output"]
                ui_detail = raw_output.get("ui_detail", {})
            else:
                raw_text = raw_output
            
            output = _truncate_output(raw_text)
            
            # If truncation occurred (string lengths differ), store the full raw text in ui_detail 
            # so the frontend SSE stream still renders the massive payload cleanly.
            if len(output) != len(raw_text) and isinstance(raw_text, str):
                ui_detail["raw_text_output"] = raw_text
                
            result = ToolResult(status="OK", output=output, ui_detail=ui_detail if ui_detail else None)

            # Append doom loop guidance if first warning
            if doom_msg:
                result.output = f"{result.output}\n\n[WARNING: Doom loop detected]\n{doom_msg}"

        except asyncio.TimeoutError:
            result = ToolResult(
                status="TIMEOUT",
                output=f"Tool '{tool_name}' timed out after {effective_timeout}s",
                fault=Fault(domain="RESOURCE", code="TIMEOUT",
                            message=f"Timeout after {effective_timeout}s", retryable=True),
            )
        except Exception as e:
            result = ToolResult(
                status="ERROR",
                output=f"Tool '{tool_name}' failed: {e}",
                fault=Fault(domain="TOOL", code="TOOL_FAILURE",
                            message=str(e), retryable=False),
            )

        return self._finish(action, t0, result)

    def _finish(self, action: ActionIR, t0: float, result: ToolResult) -> ToolResult:
        elapsed = int((time.monotonic() - t0) * 1000)
        result.timing_ms = {"exec": elapsed}
        
        # The event emitted here goes straight to the SSE stream. 
        # We check if `raw_text_output` was stashed in ui_detail (meaning LLM context was truncated).
        # Prioritize sending the raw unfettered output to the UI, otherwise default to context output.
        full_output = (result.ui_detail or {}).get("raw_text_output", result.output)
        
        event_data: Dict[str, Any] = {
            "tool": action.name, "status": result.status,
            "call_id": action.id,
            "duration_ms": elapsed,
            "output_preview": str(full_output)[:200] if full_output else None,
            "output": str(full_output) if full_output else None,
        }
        # Include ui_detail in event for UI subscribers (pi-style split result)
        if result.ui_detail:
            # Drop the raw_text_output from ui_detail payload itself to avoid duplicate fat JSON
            safe_ui_detail = {k: v for k, v in result.ui_detail.items() if k != "raw_text_output"}
            event_data["ui_detail"] = safe_ui_detail
            
        self._emit("TOOL_FINISHED", event_data)
        
        # Now remove raw_text_output entirely from the returned Result so the LLM doesn't see it
        if result.ui_detail and "raw_text_output" in result.ui_detail:
            del result.ui_detail["raw_text_output"]
            
        return result

    def _emit(self, event_type: str, data: Dict) -> None:
        if self._event_cb:
            self._event_cb(Event(type=event_type, pid=self.pid, data=data))
