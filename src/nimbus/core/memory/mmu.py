"""
Nimbus v2 Memory Management Unit (MMU) - Simplified "Anchor & Stream" Design

The MMU is responsible for:
1. Managing the context window for LLM interactions
2. Maintaining the core context (Anchor: Goal + Rules)
3. Managing the execution history (Stream: Messages)
4. Enforcing token budgets via "Smart Drop" strategy

Memory Layout:
┌─────────────────────────────────┐
│        The Anchor               │  ← Always at top (Immutable)
│  - System Rules                 │
│  - User Goal (Pinned)           │
│  - Workspace Info               │
├─────────────────────────────────┤
│        Global Summary           │  ← Rolling Summary of past events
├─────────────────────────────────┤
│        The Stream               │  ← Mutable History
│  - Frame 1 (Root)               │
│  - Frame 2 (Sub)                │
│  ...                            │
└─────────────────────────────────┘

Design Principles (Simplified):
- **Anchor First**: Goal and Rules are sacrosanct.
- **Unified Stream**: Context is treated as a continuous stream of events.
- **Smart Drop**: When budget is tight, we drop:
    1. Failed tool calls (noise)
    2. Old history (summarized)
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Literal

from nimbus.core.memory.context import (
    Message,
    PinnedContext,
    StackFrame,
    create_root_frame,
)
from nimbus.core.persistence import (
    MemorySnapshotModel,
    MessageModel,
    PinnedContextModel,
    StackFrameModel,
)


@dataclass
class MMUConfig:
    """
    Configuration for MMU.
    """
    max_context_tokens: int = 8000
    pinned_budget: int = 2000
    frame_budget: int = 6000  # Combined budget for history
    compress_threshold: float = 0.9
    keep_recent_messages: int = 10
    auto_detect_failures: bool = True
    remove_failed_tool_calls: bool = True


class MMU:
    """
    Memory Management Unit.
    
    Implements the "Anchor & Stream" strategy for context management.
    """

    def __init__(self, config: Optional[MMUConfig] = None, process_id: str = ""):
        self.config = config or MMUConfig()
        self.process_id = process_id

        # Pinned context (The Anchor)
        self._pinned: Optional[PinnedContext] = None

        # Call stack (The Stream)
        self._stack: List[StackFrame] = [create_root_frame()]

        # Global Summary
        self._global_summary: str = ""

    # =========================================================================
    # Pinned Context Management (The Anchor)
    # =========================================================================

    def update_global_summary(self, new_summary: str) -> None:
        """Update the global summary with goal reinforcement."""
        # Extract original goal from pinned context
        original_goal = ""
        if self._pinned:
            for anchor in self._pinned.custom_anchors:
                if anchor.startswith("# Current Goal"):
                    original_goal = anchor
                    break
        
        if original_goal:
            self._global_summary = f"{original_goal}\n\n[Execution Progress Summary]:\n{new_summary}"
        else:
            self._global_summary = new_summary

    def set_pinned(self, pinned: PinnedContext) -> None:
        self._pinned = pinned

    def get_pinned(self) -> Optional[PinnedContext]:
        return self._pinned

    def update_system_rules(self, rules: str) -> None:
        if self._pinned is None: self._pinned = PinnedContext()
        self._pinned.system_rules = rules

    def update_workspace_info(self, info: str) -> None:
        if self._pinned is None: self._pinned = PinnedContext()
        self._pinned.workspace_info = info

    def update_env_state(self, state: str) -> None:
        if self._pinned is None: self._pinned = PinnedContext()
        self._pinned.update_env_state(state)

    def update_capabilities(self, caps: str) -> None:
        if self._pinned is None: self._pinned = PinnedContext()
        self._pinned.capabilities = caps

    def pin_user_goal(self, goal: str) -> None:
        """
        Pin the user's current goal to the top of context.
        """
        if self._pinned is None:
            self._pinned = PinnedContext()

        # Remove existing goal
        goal_prefix = "# Current Goal\n"
        self._pinned.custom_anchors = [
            a for a in self._pinned.custom_anchors if not a.startswith(goal_prefix)
        ]

        # Add new goal
        self._pinned.custom_anchors.append(f"{goal_prefix}{goal}")

    def add_milestones(self, milestones: List[str]) -> None:
        """Add completed milestones to persistent context."""
        if not self._pinned:
            self._pinned = PinnedContext()
            
        anchor_prefix = "# ✅ Milestones\n"
        existing_idx = -1
        for i, anchor in enumerate(self._pinned.custom_anchors):
            if anchor.startswith(anchor_prefix):
                existing_idx = i
                break
                
        new_items = ""
        for m in milestones:
             new_items += f"- [x] {m}\n"

        if existing_idx != -1:
            if new_items.strip() not in self._pinned.custom_anchors[existing_idx]:
                 self._pinned.custom_anchors[existing_idx] += new_items
        else:
            self._pinned.custom_anchors.append(anchor_prefix + new_items)

    # =========================================================================
    # Stack Management (Simplified)
    # =========================================================================

    @property
    def current_frame(self) -> StackFrame:
        return self._stack[-1]

    @property
    def stack_depth(self) -> int:
        return len(self._stack)

    def push_frame(self, goal: str, meta: Optional[Dict[str, Any]] = None) -> str:
        """Push a new frame (logical separation for sub-tasks)."""
        parent = self.current_frame
        new_frame = StackFrame(
            goal=goal,
            parent_frame_id=parent.frame_id,
            meta=meta or {},
        )
        self._stack.append(new_frame)
        return new_frame.frame_id
        
    def pop_frame(self) -> Optional[StackFrame]:
        """Pop the current frame (no complex distillation)."""
        if len(self._stack) > 1:
            return self._stack.pop()
        return None

    def get_frame(self, frame_id: str) -> Optional[StackFrame]:
        for frame in self._stack:
            if frame.frame_id == frame_id:
                return frame
        return None

    # =========================================================================
    # Message Management
    # =========================================================================

    def add_message(self, message: Message) -> None:
        self.current_frame.add_message(message)

    def add_user_message(self, content: str) -> None:
        # Check for pending tool calls and fix order if needed
        self._ensure_tool_call_integrity()
        self.current_frame.add_user_message(content)

    def _ensure_tool_call_integrity(self):
        """Ensure OpenAI API message order integrity (Assistant+Tools -> Tool Results)."""
        pending = self._get_pending_tool_calls_with_names()
        if pending:
            for tc_id, tc_name in pending.items():
                self.current_frame.add_tool_result(
                    tool_call_id=tc_id,
                    name=tc_name,
                    content="[Operation interrupted by user input]",
                )

    def _get_pending_tool_calls_with_names(self) -> Dict[str, str]:
        pending: Dict[str, str] = {}
        for msg in self.current_frame.messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    tc_id = tc.get("id") or tc.get("tool_call_id")
                    tc_name = tc.get("function", {}).get("name", "unknown")
                    if tc_id: pending[tc_id] = tc_name
            elif msg.role == "tool":
                tc_id = msg.tool_call_id
                if tc_id in pending: del pending[tc_id]
        return pending

    def add_assistant_message(self, content: str) -> None:
        self.current_frame.add_assistant_message(content)

    def add_system_message(self, content: str) -> None:
        self.current_frame.add_message(Message(role="system", content=content))

    def add_assistant_with_tool_calls(
        self, content: Optional[str], tool_calls: List[Dict[str, Any]]
    ) -> None:
        self.current_frame.add_assistant_with_tool_calls(content, tool_calls)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self.current_frame.add_tool_result(tool_call_id, name, content)
        if self.config.auto_detect_failures:
            self._auto_detect_tool_failure(tool_call_id, content)

    # =========================================================================
    # Tool Call Marking (Simplified)
    # =========================================================================

    def mark_tool_call(self, tool_call_id: str, discard: bool = True, reason: str = "") -> None:
        """Mark a tool call as discarded (failed/useless)."""
        # Search backwards through frames to find the message
        for frame in reversed(self._stack):
            for msg in reversed(frame.messages):
                if msg.role == "tool" and msg.tool_call_id == tool_call_id:
                    if discard:
                        msg.meta["discard"] = True
                        if reason: msg.meta["discard_reason"] = reason
                        # Also mark the corresponding assistant tool call
                        self._mark_assistant_tool_call(frame, tool_call_id, discard)
                    else:
                        msg.meta.pop("discard", None)
                    return

    def _mark_assistant_tool_call(self, frame: StackFrame, tool_call_id: str, discard: bool):
        for msg in reversed(frame.messages):
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    if tc.get("id") == tool_call_id:
                        if discard:
                             discard_list = msg.meta.get("discard_tool_calls", [])
                             if tool_call_id not in discard_list:
                                 discard_list.append(tool_call_id)
                                 msg.meta["discard_tool_calls"] = discard_list
                        return

    def _auto_detect_tool_failure(self, tool_call_id: str, content: str) -> bool:
        """Detect explicit failures."""
        if content.startswith("[Error]") or "Traceback" in content:
            self.mark_tool_call(tool_call_id, discard=True, reason="auto_detected_failure")
            return True
        return False

    def mark_recent_tool_calls(self, discard: bool = True, count: int = 1) -> int:
        """Mark recent tool calls."""
        marked = 0
        current_idx = len(self.current_frame.messages) - 1
        messages = self.current_frame.messages
        
        while current_idx >= 0 and marked < count:
            msg = messages[current_idx]
            if msg.role == "tool" and msg.tool_call_id:
                self.mark_tool_call(msg.tool_call_id, discard=discard, reason="manual_mark")
                marked += 1
            current_idx -= 1
        return marked

    def clear_markers(self) -> None:
        """Clear all discard markers."""
        for frame in self._stack:
            for msg in frame.messages:
                msg.meta.pop("discard", None)
                msg.meta.pop("discard_tool_calls", None)

    # =========================================================================
    # Context Assembly (The "Smart Drop" Strategy)
    # =========================================================================

    def assemble_context(
        self,
        max_tokens: Optional[int] = None,
        filter_discardable: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Assemble the context, applying the Smart Drop strategy if over budget.
        """
        max_tokens = max_tokens or self.config.max_context_tokens
        messages: List[Dict[str, Any]] = []
        token_count = 0

        # 1. The Anchor (Pinned Context) - Must have
        if self._pinned:
            pinned_msg = self._pinned.to_system_message()
            token_count += pinned_msg.token_estimate()
            messages.append(pinned_msg.to_dict())

        # 2. Global Summary (If available)
        if self._global_summary:
            # Build Task Stack visualization
            task_stack_view = "📋 [Mission Control]\n-------------------\n"
            
            # Main Goal (Root Frame)
            root_goal = self._stack[0].goal or "Main Task"
            task_stack_view += f"🎯 Main Goal: {root_goal}\n-------------------\n"
            
            # Completed Milestones
            if self._pinned:
                for anchor in self._pinned.custom_anchors:
                    if anchor.startswith("# ✅ Milestones"):
                        # Extract content after header
                        milestones_content = anchor.replace("# ✅ Milestones\n", "").strip()
                        if milestones_content:
                            task_stack_view += "✅ Completed Milestones:\n" + milestones_content + "\n-------------------\n"
                        break
            
            # Execution Path (Frames)
            task_stack_view += "📍 Execution Path:\n"
            for i, frame in enumerate(self._stack):
                marker = "(Current Focus)" if i == len(self._stack) - 1 else ""
                goal_preview = frame.goal[:50] + "..." if len(frame.goal) > 50 else frame.goal
                task_stack_view += f"  {i+1}. [{frame.frame_id[:6]}] {goal_preview} {marker}\n"
            
            task_stack_view += "-------------------\n"
            
            full_summary_content = f"{task_stack_view}\n{self._global_summary}"
            
            summary_msg = Message(role="system", content=full_summary_content, meta={"type": "global_summary"})
            summary_tokens = summary_msg.token_estimate()
            if token_count + summary_tokens < max_tokens:
                messages.append(summary_msg.to_dict())
                token_count += summary_tokens

        remaining_budget = max_tokens - token_count
        
        # 3. The Stream (Execution History)
        stream_messages = []
        for frame in self._stack:
            stream_messages.extend(frame.to_context_messages())
            
        stream_tokens = sum(m.token_estimate() for m in stream_messages)
        
        if stream_tokens <= remaining_budget:
            # All good, return full stream
            for m in stream_messages:
                messages.append(m.to_dict())
        else:
            # Over budget: Apply Smart Drop
            
            # Drop Strategy 1: Remove discarded/failed tools
            if self.config.remove_failed_tool_calls:
                stream_messages = self._filter_discarded(stream_messages)
                stream_tokens = sum(m.token_estimate() for m in stream_messages)
            
            if stream_tokens <= remaining_budget:
                for m in stream_messages:
                    messages.append(m.to_dict())
                return messages

            # Drop Strategy 2: Keep recent, summarize old (Simplified implementation: simple truncation for now)
            # In a full implementation, we would call CompactionEngine here.
            # For now, we keep the last N messages and maybe a placeholder for the rest.
            
            keep_count = self.config.keep_recent_messages
            if len(stream_messages) > keep_count:
                kept_messages = stream_messages[-keep_count:]
                
                # Add a truncation marker/summary
                messages.append({
                    "role": "system",
                    "content": f"[... Earlier history truncated to fit context window ...]",
                })
                
                for m in kept_messages:
                    messages.append(m.to_dict())
            else:
                 # Even recent messages don't fit? This is bad. Just add what fits.
                 for m in stream_messages:
                     messages.append(m.to_dict())

        return messages

    def _filter_discarded(self, messages: List[Message]) -> List[Message]:
        """Filter out messages marked as discarded."""
        filtered = []
        for msg in messages:
            if msg.role == "tool" and msg.meta.get("discard"):
                continue
            
            if msg.role == "assistant" and msg.tool_calls:
                discard_ids = msg.meta.get("discard_tool_calls", [])
                if discard_ids:
                    # Filter specific tool calls from the list
                    valid_calls = [tc for tc in msg.tool_calls if tc.get("id") not in discard_ids]
                    if valid_calls or msg.content:
                        # Create copy with filtered calls
                        new_msg = Message(
                            role=msg.role,
                            content=msg.content,
                            tool_calls=valid_calls if valid_calls else None,
                            meta=msg.meta
                        )
                        filtered.append(new_msg)
                    continue # Message handled
            
            filtered.append(msg)
        return filtered

    # =========================================================================
    # State & Utils
    # =========================================================================

    def estimate_tokens(self) -> int:
        total = 0
        if self._pinned: total += self._pinned.token_estimate()
        for frame in self._stack: total += frame.token_estimate()
        return total

    def rollback_incomplete_turn(self) -> int:
        """Rollback pending tool calls if interrupted."""
        if not self._stack: return 0
        frame = self.current_frame
        messages = frame.messages
        removed = 0
        while messages:
            last = messages[-1]
            if last.role == "user": break
            if last.role == "tool":
                messages.pop(); removed += 1; continue
            if last.role == "assistant" and last.tool_calls:
                messages.pop(); removed += 1; continue
            break
        return removed

    def get_state(self) -> Dict[str, Any]:
        """Simple state for debugging."""
        return {
            "pinned": self._pinned is not None,
            "stack_depth": len(self._stack),
            "total_messages": sum(len(f.messages) for f in self._stack),
            "tokens": self.estimate_tokens(),
        }
    
    # Snapshot logic remains similar but simplified structure
    def create_snapshot(self) -> MemorySnapshotModel:
        # Convert Stack
        stack_models = []
        for frame in self._stack:
            msg_models = [
                MessageModel(
                    role=msg.role, content=msg.content, name=msg.name,
                    tool_call_id=msg.tool_call_id, tool_calls=msg.tool_calls,
                    meta=msg.meta
                ) for msg in frame.messages
            ]
            stack_models.append(StackFrameModel(
                frame_id=frame.frame_id, goal=frame.goal, messages=msg_models,
                state=frame.state, parent_frame_id=frame.parent_frame_id,
                result=frame.result, created_at=frame.created_at, meta=frame.meta,
            ))
            
        pinned_model = None
        if self._pinned:
             pinned_model = PinnedContextModel(
                system_rules=self._pinned.system_rules,
                workspace_info=self._pinned.workspace_info,
                env_state=self._pinned.env_state,
                capabilities=self._pinned.capabilities,
                custom_anchors=self._pinned.custom_anchors,
                version=self._pinned.version,
             )

        return MemorySnapshotModel(
            process_id=self.process_id,
            pinned_context=pinned_model,
            stack=stack_models,
            tool_markers={}, # Removed
            frame_discardable={}, # Removed
        )

    def restore_from_snapshot(self, snapshot: MemorySnapshotModel) -> None:
        self.process_id = snapshot.process_id
        if snapshot.pinned_context:
            self._pinned = PinnedContext(
                system_rules=snapshot.pinned_context.system_rules,
                workspace_info=snapshot.pinned_context.workspace_info,
                env_state=getattr(snapshot.pinned_context, "env_state", ""),
                capabilities=snapshot.pinned_context.capabilities,
                custom_anchors=snapshot.pinned_context.custom_anchors,
                version=snapshot.pinned_context.version,
            )
        
        self._stack = []
        for frame_model in snapshot.stack:
            messages = [
                Message(
                    role=m.role, content=m.content, name=m.name,
                    tool_call_id=m.tool_call_id, tool_calls=m.tool_calls,
                    meta=m.meta
                ) for m in frame_model.messages
            ]
            self._stack.append(StackFrame(
                frame_id=frame_model.frame_id, goal=frame_model.goal,
                messages=messages, state=frame_model.state,
                parent_frame_id=frame_model.parent_frame_id, result=frame_model.result,
                created_at=frame_model.created_at, meta=frame_model.meta
            ))

    async def archive_and_reset(self, session_id: str, summarizer=None) -> Optional[str]:
        """
        Simplified compaction: Truncate history, keeping only the most recent messages.
        
        This satisfies the vCPU's compaction requirement by physically removing 
        old messages from the stack frame. In the "Smart Drop" design, this is 
        equivalent to a "Hard Cut" when the buffer is full.
        """
        if not self._stack:
            return None

        frame = self.current_frame
        keep_count = self.config.keep_recent_messages
        
        # If we have fewer messages than we want to keep, we can't compact by truncation.
        # But we might still be over budget due to huge messages.
        # For simplicity, we only truncate by count here.
        if len(frame.messages) <= keep_count:
            # If we are here, it means needs_compression() is True, but we don't have many messages.
            # This implies the messages are huge. We should probably clear more or warn.
            # For now, let's keep it safe and do nothing, relying on assemble_context smart drop.
            # But we must return a value to signal "attempted".
            return None 

        # Keep tail
        kept_messages = frame.messages[-keep_count:]
        removed_count = len(frame.messages) - keep_count
        
        # Reset frame messages
        frame.messages = kept_messages
        
        # Insert a marker (system message) to indicate truncation
        # This helps the LLM know there's a gap
        frame.messages.insert(0, Message(
            role="system", 
            content=f"[System: Memory compacted. {removed_count} older messages were archived.]",
            meta={"compaction_marker": True}
        ))
        
        # Update discard markers (clear them as they likely referred to old messages)
        # In our simplified design, markers are on messages, so they are gone with the messages.
        
        return "memory_truncated_in_live_buffer"
