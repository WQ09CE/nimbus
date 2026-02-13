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

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from nimbus.core.memory.context import (
    IMAGE_TOKEN_ESTIMATE,
    Message,
    PinnedContext,
    StackFrame,
    create_root_frame,
)
from nimbus.core.memory.state_manager import StateManager
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
    max_context_tokens: int = 180000  # Increased to 180k (leaving 20k buffer for output)
    pinned_budget: int = 10000        # Generous space for system rules & goals
    frame_budget: int = 170000        # Massive history window (adjusted for 180k total)
    compress_threshold: float = 0.9   # Trigger sliding at ~162k tokens
    max_image_tokens: int = 10000     # Max tokens for image history (approx 3-6 images)
    keep_recent_messages: int = 20    # Keep more recent context
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

        # Project State Monitor (Deterministic)
        self._state_manager = StateManager()

        # Viewport Management (Sliding Window)
        # 0 means "follow latest". >0 means "scroll back N messages from end".
        self._view_offset: int = 0

        # Clipboard (Short-term memory buffer)
        self._clipboard: str = ""

    # =========================================================================
    # Pinned Context Management (The Anchor)
    # =========================================================================

    def update_clipboard(self, content: str) -> None:
        """Update the clipboard content (notes/scratchpad)."""
        self._clipboard = content

    def update_global_summary(self, new_summary: str) -> None:
        """Update the global summary with goal reinforcement."""
        # Extract original goal from pinned context
        goal_text = "Unknown Goal"
        if self._pinned:
            for anchor in self._pinned.custom_anchors:
                if anchor.startswith("# Current Goal"):
                    goal_text = anchor.replace("# Current Goal", "").strip()
                    break

        # Re-format specifically to fight recency bias
        # Using H1/H2 headers to catch LLM attention and reinforce the original goal
        self._global_summary = (
            f"# 🎯 PRIMARY GOAL\n{goal_text}\n\n"
            f"# 📝 EXECUTION STATUS\n{new_summary}"
        )

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

    def add_user_message(self, content: "str | list") -> None:
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

    def add_tool_result(self, tool_call_id: str, name: str, content: str, tool_args: dict = None) -> None:
        self.current_frame.add_tool_result(tool_call_id, name, content)

        # 1. Auto-detect explicit failures (Error Recovery)
        if self.config.auto_detect_failures:
            self._auto_detect_tool_failure(tool_call_id, content)

        # 2. Update Project State (Deterministic Anti-Drift)
        if tool_args:
            self._state_manager.update(name, tool_args, content)

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

    def cleanup_ephemeral_messages(self) -> int:
        """
        Remove messages marked as ephemeral from the current context.
        
        Ephemeral messages (e.g., retry hints, error corrections) are intended 
        to be seen only once by the LLM to guide the immediate next step.
        Once the LLM has responded (consumed the hint), these messages 
        should be removed to prevent context pollution.
        
        Returns:
            Number of messages removed.
        """
        count = 0
        # We generally only look at the current frame, but for safety scan all
        for frame in self._stack:
            # Create a new list keeping only non-ephemeral messages
            # We filter in-place or replace the list
            original_len = len(frame.messages)
            frame.messages = [
                msg for msg in frame.messages
                if not msg.meta.get("ephemeral", False)
            ]
            count += (original_len - len(frame.messages))

        return count

    def scroll(self, direction: str, steps: int = 10) -> str:
        """
        Scroll the memory view window.
        
        Args:
            direction: "up" (older) or "down" (newer)
            steps: Number of messages to scroll
            
        Returns:
            Status message describing the new view
        """
        # Calculate total available messages in stream
        total_msgs = sum(len(f.messages) for f in self._stack)

        if direction == "up":
            # Looking at older messages -> increase offset
            self._view_offset += steps

            # Limit offset so we don't scroll past the beginning (show at least 1 message)
            max_offset = max(0, total_msgs - 1)
            if self._view_offset > max_offset:
                self._view_offset = max_offset

        elif direction == "down":
            # Looking at newer messages -> decrease offset
            self._view_offset -= steps
            if self._view_offset < 0:
                self._view_offset = 0

        return (
            f"Scrolled {direction} {steps} steps. Current offset from latest: {self._view_offset}.\n"
            "IMPORTANT: The conversation history has been updated in your context window.\n"
            "PLEASE LOOK ABOVE this tool result to see the historical messages you requested."
        )

    # =========================================================================
    # Context Assembly (The "Smart Drop" Strategy)
    # =========================================================================

    def _image_key(self, block: Dict[str, Any]) -> str:
        """Generate unique key for an image block using content hash."""
        data = block.get("data", "")
        if isinstance(data, str) and data:
            digest = hashlib.sha256(data.encode("ascii", errors="replace")).hexdigest()[:16]
        else:
            digest = ""
        mime = block.get("mimeType", "")
        return f"{mime}:{digest}"

    def _optimize_context(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Optimize context by:
        1. Downgrading duplicate/budget-exceeding images.
        2. Truncating massive tool outputs in the view (preserving storage).
        """
        # --- Config Constants ---
        VIEW_MAX_TOOL_CHARS = 10_000  # Max chars for tool output in context view (~2.5k tokens)

        # 1. Image Downgrade Logic
        keep_indices = set()
        seen_keys = set()
        current_image_tokens = 0
        
        # Scan backwards for images
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            content = msg.get("content")
            if isinstance(content, list):
                for j in range(len(content) - 1, -1, -1):
                    block = content[j]
                    if isinstance(block, dict) and block.get("type") == "image":
                        key = self._image_key(block)
                        if key in seen_keys: continue
                        
                        img_tokens = IMAGE_TOKEN_ESTIMATE 
                        if current_image_tokens + img_tokens <= self.config.max_image_tokens:
                            keep_indices.add((i, j))
                            seen_keys.add(key)
                            current_image_tokens += img_tokens
                        else:
                            seen_keys.add(key)

        # 2. Rebuild with Optimizations
        result = []
        for i, msg in enumerate(messages):
            # --- Tool Output Truncation ---
            if msg.get("role") == "tool":
                content = msg.get("content")
                if isinstance(content, str) and len(content) > VIEW_MAX_TOOL_CHARS:
                    # Truncate string content
                    # We keep the head and a warning
                    new_content = content[:VIEW_MAX_TOOL_CHARS] + \
                        f"\n... [Truncated {len(content)-VIEW_MAX_TOOL_CHARS:,} chars for context view] ..."
                    
                    # Clone message to avoid modifying the original list object
                    new_msg = dict(msg)
                    new_msg["content"] = new_content
                    result.append(new_msg)
                    continue

            # --- Image Processing ---
            content = msg.get("content")
            if not isinstance(content, list):
                result.append(msg)
                continue
                
            new_content = []
            changed = False
            
            for j, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "image":
                    if (i, j) in keep_indices:
                        new_content.append(block)
                    else:
                        mime = block.get("mimeType", "image/unknown")
                        new_content.append({
                            "type": "text",
                            "text": f"\n[📷 Image ({mime}) — Omitted to save tokens (duplicate or budget limit)]\n"
                        })
                        changed = True
                else:
                    new_content.append(block)
            
            if changed:
                new_msg = dict(msg)
                new_msg["content"] = new_content
                result.append(new_msg)
            else:
                result.append(msg)
                
        return result

    def assemble_context(
        self,
        max_tokens: Optional[int] = None,
        filter_discardable: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Assemble the full context with "Recent-Anchored Sliding Window".
        
        Structure:
        1. Pinned Context (Goal/Rules)
        2. [System: Historical View Indicator]
        3. Historical Window (auto-managed sliding window)
        4. [System: Gap Indicator]
        5. Hot Context (Recent messages, always visible)
        """
        max_tokens = max_tokens or self.config.max_context_tokens
        messages: List[Dict[str, Any]] = []
        token_count = 0

        # --- 1. Pinned Context ---
        if self._pinned:
            pinned_msg = self._pinned.to_system_message()
            token_count += pinned_msg.token_estimate()
            messages.append(pinned_msg.to_dict())

        # Project State & Summary
        project_state = self._state_manager.render()
        if project_state:
            state_msg = Message(role="system", content=project_state, meta={"type": "project_state"})
            token_count += state_msg.token_estimate()
            messages.append(state_msg.to_dict())

        if self._global_summary:
            # Simplified summary assembly
            task_stack_view = "📋 [Mission Control]\n-------------------\n"
            root_goal = self._stack[0].goal or "Main Task"
            task_stack_view += f"🎯 Main Goal: {root_goal}\n-------------------\n"
            full_summary = f"{task_stack_view}\n{self._global_summary}"
            summary_msg = Message(role="system", content=full_summary, meta={"type": "global_summary"})
            token_count += summary_msg.token_estimate()
            messages.append(summary_msg.to_dict())

        # --- Memo (好记性不如烂笔头) ---
        # Auto-inject memo content if MemoManager is attached
        if hasattr(self, '_memo_manager') and self._memo_manager:
            try:
                memo_content = self._memo_manager.read()
                if memo_content and memo_content.strip():
                    memo_msg = Message(
                        role="system",
                        content=f"📝 [Your Memo - 你的记忆笔记]:\n{memo_content}",
                        meta={"type": "memo"}
                    )
                    memo_tokens = memo_msg.token_estimate()
                    if token_count + memo_tokens < max_tokens:
                        messages.append(memo_msg.to_dict())
                        token_count += memo_tokens
            except Exception:
                pass  # Memo read failed, continue without it

        remaining_budget = max_tokens - token_count

        # Debug: Log budget allocation
        from nimbus.core.logging import get_logger as _get_logger
        _mmu_logger = _get_logger("memory.mmu")
        _mmu_logger.debug(
            f"📊 assemble_context budget: max={max_tokens}, "
            f"pinned+state+memo={token_count}, remaining={remaining_budget}, "
            f"stream_msgs={sum(len(f.messages) for f in self._stack)}"
        )

        if remaining_budget < 500:
            # Emergency: Pinned context too large
            return messages

        # --- Prepare Stream ---
        stream_messages = []
        for frame in self._stack:
            stream_messages.extend(frame.to_context_messages())
        total_msgs = len(stream_messages)

        # --- 2. Hot Context (Always Visible) ---
        # Keep last N messages to ensure Agent knows current instruction

        HOT_COUNT = 15  # Keep last 15 messages always (expanded for better context retention)
        hot_messages = []
        hot_tokens = 0

        if total_msgs > 0:
            # Initial slice index
            hot_start_idx = max(0, total_msgs - HOT_COUNT)

            # SAFETY ADJUSTMENT: Ensure Hot Context doesn't start with 'tool' (orphaned result)
            # We extend the hot context BACKWARDS to include the parent assistant call.
            # This ensures we don't present a Tool Result without its Call.
            while hot_start_idx > 0:
                msg = stream_messages[hot_start_idx]
                if msg.role == "tool":
                    hot_start_idx -= 1
                else:
                    # Found a non-tool message (likely the assistant call or user)
                    break

            hot_slice = stream_messages[hot_start_idx:]

            # Verify they fit in budget (at least half of remaining)
            # Note: We process from end to start to ensure we keep the absolute latest
            for m in reversed(hot_slice):
                t = m.token_estimate()
                if hot_tokens + t > remaining_budget * 0.5:
                    break
                hot_messages.insert(0, m)
                hot_tokens += t

            # If we had to truncate hot_messages due to budget, we might have created
            # a NEW orphan problem at the beginning of hot_messages!
            # So we must apply the same safety check again on the final hot_messages list.
            while hot_messages and hot_messages[0].role == "tool":
                 hot_messages.pop(0)

        # Adjust remaining budget for History Window
        history_budget = remaining_budget - hot_tokens

        _mmu_logger.debug(
            f"📊 hot: {len(hot_messages)}/{total_msgs} msgs, {hot_tokens} tokens "
            f"(budget={int(remaining_budget*0.5)}), "
            f"history_budget={history_budget}"
        )

        # --- 3. Historical Window ---
        # The window ends at: total - len(hot_messages)
        # Note: We must use the ACTUAL length of hot_messages here,
        # because hot_start_idx might have overlapped with history window if we didn't account for it.

        history_stream_end = max(0, total_msgs - len(hot_messages))

        # Target end based on user scroll
        # offset=0 means we want to see up to history_stream_end
        # offset=10 means we want to see up to history_stream_end - 10
        target_end = max(0, history_stream_end - self._view_offset)

        window_messages = []
        window_tokens = 0
        start_index = target_end

        # Scan backwards from target_end
        for i in range(target_end - 1, -1, -1):
            msg = stream_messages[i]
            t = msg.token_estimate()
            if window_tokens + t > history_budget:
                break
            window_tokens += t
            window_messages.insert(0, msg)
            start_index = i

        # SAFETY ADJUSTMENT 1: Start Integrity
        # Avoid starting with a 'tool' message (orphaned result)
        while start_index < target_end:
            if stream_messages[start_index].role == "tool":
                start_index += 1
                if window_messages: window_messages.pop(0)
            else:
                break

        # SAFETY ADJUSTMENT 2: End Integrity
        # Avoid ending with an 'assistant' message that has tool_calls (orphaned call)
        while target_end > start_index:
            last_msg = stream_messages[target_end - 1]
            if last_msg.role == "assistant" and last_msg.tool_calls:
                target_end -= 1
                if window_messages: window_messages.pop()
            else:
                break

        # Re-sync view_messages if indices shifted (actually window_messages is already updated above)
        # But let's just re-slice to be safe and consistent with indices
        view_messages = stream_messages[start_index:target_end]

        # --- Assemble Final Sequence ---

        # Indicator: More history above?
        if start_index > 0:
            messages.append({
                "role": "system",
                "content": f"⬆️ [History: {start_index} older messages truncated. Use Memo to save important info!]"
            })

        for m in window_messages:
            messages.append(m.to_dict())

        # Indicator: Gap between Window and Hot Context?
        gap_size = history_stream_end - target_end
        if gap_size > 0:
            messages.append({
                "role": "system",
                "content": f"⬇️ [Gap: {gap_size} messages skipped. Important info should be in your Memo!]"
            })
        elif self._view_offset > 0:
             # We are scrolling, but we managed to connect to hot context?
             # Unlikely if offset > 0.
             pass

        # Add Hot Context
        if hot_messages:
            if gap_size > 0 or start_index > 0:
                 messages.append({
                    "role": "system",
                    "content": "👇 [Current Context (Recent Messages)]"
                })
            for m in hot_messages:
                messages.append(m.to_dict())

        # Phase 2: Optimize Context (Downgrade images, Truncate large tool outputs)
        messages = self._optimize_context(messages)

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

    def needs_compression(self) -> bool:
        """Safety net: fires inside VCPU.step() if proactive check missed."""
        current_tokens = self.estimate_tokens()
        safety_threshold = int(self.config.max_context_tokens *
                              min(self.config.compress_threshold + 0.05, 0.98))
        if current_tokens <= safety_threshold:
            return False
        total_messages = sum(len(f.messages) for f in self._stack)
        return total_messages >= 10

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
        Perform in-memory compaction: Summarize history + Truncate.
        No file I/O involved.
        """
        if not self._stack: return None
        frame = self.current_frame
        messages = frame.messages
        if not messages: return None

        # 1. Identify what fits in budget (Bottom-up)
        # We want to keep as much recent history as possible within frame_budget
        budget = self.config.frame_budget
        current_tokens = 0
        cut_index = 0

        # Scan backwards to find the cut point
        # kept_messages = messages[cut_index:]
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            tokens = msg.token_estimate()

            if current_tokens + tokens > budget:
                cut_index = i + 1
                break

            current_tokens += tokens

            # Optimization: Don't keep more than N messages even if budget allows
            # This prevents context from getting too long with tiny messages
            if len(messages) - i >= self.config.keep_recent_messages * 2:
                 cut_index = i
                 break

        # If we are keeping everything but still triggered compaction (likely due to Pinned Context size),
        # we must force cut something to make progress.
        if cut_index == 0 and self.needs_compression():
             # Force cut oldest 20%
             cut_index = max(1, int(len(messages) * 0.2))

        # SAFETY ADJUSTMENT: Avoid cutting in the middle of a tool-use turn
        # Specifically, we must NOT start the retained history with a 'tool' message,
        # because its corresponding 'assistant' call would be lost (archived).
        while cut_index < len(messages):
            if messages[cut_index].role == "tool":
                # This is an orphaned tool result (assistant call was archived).
                # We must archive this too.
                cut_index += 1
            else:
                break

        # 2. Split
        messages_to_archive = messages[:cut_index]
        messages_to_keep = messages[cut_index:]

        if not messages_to_archive:
            return None

        # 3. Summarize (Crucial: Update Global Summary)
        if summarizer:
            # We provide the full context to the summarizer so it can capture the transition
            try:
                summary_text = await summarizer(messages)
                if summary_text:
                    self.update_global_summary(summary_text)
            except Exception as e:
                # Log but continue (don't fail compaction)
                from nimbus.core.logging import get_logger
                get_logger("memory.mmu").error(f"Summarization failed: {e}")

        # 4. Apply Truncation
        # Phase 3 Sliding Window originally kept all messages for scroll-back,
        # but ScrollHistory was replaced by Memo tool. Physical truncation is
        # required to actually free token budget after compaction.
        frame.messages = messages_to_keep

        # Clear markers to reset tool state tracking
        self.clear_markers()

        return "memory_compacted"

    def clear(self) -> None:
        """Clear all state and reset to initial."""
        self._pinned = None
        self._stack = [create_root_frame()]
        self._global_summary = ""
        self._state_manager = StateManager()
