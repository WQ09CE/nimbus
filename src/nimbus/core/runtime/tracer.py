"""
Trace Manager - Comprehensive Execution Tracing for VCPU

This module provides structural observability for the Think-Act-Observe loop.
It captures the exact context provided to the LLM, the raw reasoning,
and the execution results in a format suitable for debugging "infinite context" issues.
"""

import json
import time
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from pathlib import Path
from datetime import datetime

from nimbus.core.protocol import ActionIR, ToolResult, Fault

@dataclass
class ContextSnapshot:
    """Snapshot of what the AI 'saw' at this step."""
    pinned_tokens: int
    frame_tokens: int
    total_tokens: int
    summary_preview: Optional[str]
    messages: List[Dict[str, Any]]  # The exact list sent to LLM

@dataclass
class ExecutionTrace:
    """Full lifecycle trace of a single VCPU step."""
    iteration: int
    timestamp: str
    
    # 1. Input State
    context: ContextSnapshot
    
    # 2. AI Processing
    llm_raw_content: str
    llm_tool_calls: List[Dict[str, Any]]
    
    # 3. Decision
    actions: List[Dict[str, Any]]  # Serialized ActionIRs
    
    # 4. Outcome
    results: List[Dict[str, Any]]  # Serialized ToolResults
    fault: Optional[Dict[str, Any]]
    
    # Metrics
    timing_ms: Dict[str, int]

class TraceManager:
    """
    Manages the recording and persisting of execution traces.
    """
    def __init__(self, session_id: str, base_dir: str = ".nimbus/traces"):
        self.session_id = session_id or "unknown_session"
        # Create trace directory: .nimbus/traces/<session_id>/
        self.trace_dir = Path(base_dir) / self.session_id
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        
        # Current active trace
        self._current_step: Optional[ExecutionTrace] = None
        self._start_time = 0
        
    def start_step(self, iteration: int):
        """Begin tracing a new step."""
        self._start_time = time.time_ns()
        # Initialize with empty placeholders
        self._current_step = ExecutionTrace(
            iteration=iteration,
            timestamp=datetime.now().isoformat(),
            context=ContextSnapshot(0, 0, 0, None, []),
            llm_raw_content="",
            llm_tool_calls=[],
            actions=[],
            results=[],
            fault=None,
            timing_ms={}
        )

    def record_context(self, messages: List[Dict[str, Any]], pinned_tokens: int = 0, frame_tokens: int = 0):
        """Record the context assembly."""
        if not self._current_step: return
        
        # Extract summary if present
        summary = None
        for msg in messages:
            content = str(msg.get("content", ""))
            # Check for meta (if available) OR content heuristic
            if (msg.get("meta", {}).get("type") == "global_summary") or \
               ("📋 [Mission Control]" in content):
                summary = content[:200] + "..." # Preview
                break
                
        total = pinned_tokens + frame_tokens
        # Fallback if tokens not provided (rough estimate)
        if total == 0:
            total = sum(len(str(m.get("content", ""))) // 4 for m in messages)

        self._current_step.context = ContextSnapshot(
            pinned_tokens=pinned_tokens,
            frame_tokens=frame_tokens,
            total_tokens=total,
            summary_preview=summary,
            messages=messages
        )

    def record_llm_response(self, content: Optional[str], tool_calls: Optional[List[Any]]):
        """Record what the LLM actually said."""
        if not self._current_step: return
        self._current_step.llm_raw_content = content or ""
        self._current_step.llm_tool_calls = tool_calls or []

    def record_actions(self, actions: List[ActionIR]):
        """Record parsed actions."""
        if not self._current_step: return
        self._current_step.actions = [
            {"kind": a.kind, "name": a.name, "args": a.args, "id": a.id}
            for a in actions
        ]

    def record_results(self, results: List[ToolResult]):
        """Record execution results."""
        if not self._current_step: return
        self._current_step.results = [
            {"status": r.status, "output": str(r.output)[:500] + ("..." if len(str(r.output)) > 500 else ""), "fault": str(r.fault) if r.fault else None}
            for r in results
        ]

    def record_fault(self, fault: Fault):
        """Record step-level fault."""
        if not self._current_step: return
        self._current_step.fault = {
            "domain": fault.domain,
            "code": fault.code,
            "message": fault.message
        }

    def finish_step(self):
        """Finalize and write the step trace."""
        if not self._current_step: return
        
        # Calculate total time
        duration = (time.time_ns() - self._start_time) // 1_000_000
        self._current_step.timing_ms["total_trace_duration"] = duration
        
        # 1. Write JSON (Machine Readable)
        step_num = self._current_step.iteration
        json_path = self.trace_dir / f"step_{step_num:03d}.json"
        
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self._current_step), f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to write trace JSON: {e}")

        # 2. Write/Append Markdown (Human Readable)
        md_path = self.trace_dir / "trace_log.md"
        self._append_markdown_log(md_path)
        
        self._current_step = None

    def _append_markdown_log(self, path: Path):
        """Append a human-friendly summary to the markdown log."""
        step = self._current_step
        ctx = step.context
        
        md_content = f"""
## Step {step.iteration} [{step.timestamp}]

**Context Stats**: Total Tokens: ~{ctx.total_tokens} (Pinned: {ctx.pinned_tokens}, Frame: {ctx.frame_tokens})
**Global Summary**: {ctx.summary_preview or "N/A"}

### 🧠 AI Thought
{step.llm_raw_content}

"""
        if step.actions:
            md_content += "### ⚡ Actions\n"
            for act in step.actions:
                md_content += f"- **{act['kind']}**: `{act['name']}`\n  Args: `{json.dumps(act['args'])}`\n"
        
        if step.results:
            md_content += "\n### 👁️ Observations\n"
            for res in step.results:
                status_icon = "✅" if res['status'] == "OK" else "❌"
                md_content += f"- {status_icon} **{res['status']}**: {res['output']}\n"
        
        if step.fault:
            md_content += f"\n### 🛑 Fault\n**{step.fault['code']}**: {step.fault['message']}\n"
            
        md_content += "\n---\n"
        
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(md_content)
        except Exception:
            pass
