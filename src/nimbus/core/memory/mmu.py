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
import re
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
from nimbus.core.memory.context_assembler import _NIMFS_NO_OFFLOAD
from nimbus.core.persistence import (
    MemorySnapshotModel,
    MessageModel,
    PinnedContextModel,
    StackFrameModel,
)


# NimFS auto-offload marker (used by ContextAssembler)
_NIMFS_OFFLOAD_MARKER = "[NimFS Auto-Offload]"


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

    # NimFS auto-offload: tool results larger than this are offloaded to NimFS.
    # Set to 0 to disable. Requires nimfs_workspace to be set on the MMU.
    nimfs_offload_threshold: int = 8_000  # characters


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

        # NimFS offload: workspace path for auto-offloading large tool results.
        # Set by AgentOS after spawning. None = offload disabled.
        self.nimfs_workspace: Optional[str] = None
        self._nimfs_offload_counter: int = 0  # for unique task_id generation

    # =========================================================================
    # Pinned Context Management (The Anchor)
    # =========================================================================

    def update_clipboard(self, content: str) -> None:
        """Update the clipboard content (notes/scratchpad)."""
        self._clipboard = content

    def update_global_summary(self, new_summary: str) -> None:
        """Update the global summary with goal reinforcement."""
        # Extract original goal from pinned context (try multiple sources)
        goal_text = ""

        # Source 1: pinned custom_anchors (e.g. key "Goal")
        if self._pinned:
            if "Goal" in self._pinned.custom_anchors:
                goal_text = self._pinned.custom_anchors["Goal"].strip()

        # Source 2: root frame goal
        if not goal_text and self._stack:
            root_goal = self._stack[0].goal
            if root_goal:
                goal_text = root_goal

        # Source 3: first user message in frame messages
        if not goal_text and self._stack:
            for frame in self._stack:
                for msg in frame.messages:
                    if msg.role == "user" and msg.content:
                        goal_text = str(msg.content)[:200]
                        break
                if goal_text:
                    break

        if not goal_text:
            goal_text = "Unknown Goal"

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

        # Replace existing goal (or add new one)
        self._pinned.custom_anchors["Goal"] = goal

    def add_milestones(self, milestones: List[str]) -> None:
        """Add completed milestones to persistent context."""
        if not self._pinned:
            self._pinned = PinnedContext()

        anchor_key = "Milestones"
        
        new_items = ""
        for m in milestones:
             new_items += f"- [x] {m}\n"
             
        if anchor_key in self._pinned.custom_anchors:
            # Append if not identical
            if new_items.strip() not in self._pinned.custom_anchors[anchor_key]:
                 self._pinned.custom_anchors[anchor_key] += "\n" + new_items
        else:
            self._pinned.custom_anchors[anchor_key] = new_items

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
                    # Support both dict-style (production API) and object-style (mock) tool calls
                    if isinstance(tc, dict):
                        tc_id = tc.get("id") or tc.get("tool_call_id")
                        tc_name = tc.get("function", {}).get("name", "unknown")
                    else:
                        tc_id = getattr(tc, "id", None) or getattr(tc, "tool_call_id", None)
                        func = getattr(tc, "function", None)
                        tc_name = getattr(func, "name", "unknown") if func else "unknown"
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
        # NimFS auto-offload: if tool result is large and workspace is configured,
        # store the full content in NimFS and replace with a compact reference message.
        threshold = self.config.nimfs_offload_threshold
        if (
            threshold > 0
            and self.nimfs_workspace
            and isinstance(content, str)
            and len(content) > threshold
            and name not in _NIMFS_NO_OFFLOAD
        ):
            content = self._offload_tool_result_to_nimfs(name, content)

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
                    tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                    if tc_id == tool_call_id:
                        if discard:
                             discard_list = msg.meta.get("discard_tool_calls", [])
                             if tool_call_id not in discard_list:
                                 discard_list.append(tool_call_id)
                                 msg.meta["discard_tool_calls"] = discard_list
                        return

    def _offload_tool_result_to_nimfs(self, tool_name: str, content: str) -> str:
        """
        Offload a large tool result to NimFS and return a compact reference message.

        Called automatically by add_tool_result() when content exceeds
        config.nimfs_offload_threshold and nimfs_workspace is set.

        Returns a short message containing the nimfs:// reference so the
        MMU stores only ~200 chars instead of the full content.
        """
        try:
            from nimbus.core.nimfs.manager import NimFSManager
            from nimbus.core.nimfs.models import ArtifactTTL

            self._nimfs_offload_counter += 1
            task_id = f"mmu-offload-{self.process_id or 'proc'}-{self._nimfs_offload_counter}"

            manager = NimFSManager(self.nimfs_workspace)
            ref = manager.write_artifact(
                content=content,
                task_id=task_id,
                producer=f"mmu/{tool_name}",
                artifact_type="text",
                ttl=ArtifactTTL.SESSION,
                summary=content[:150].replace("\n", " "),
            )

            preview_len = min(2000, len(content))
            return (
                f"[NimFS Auto-Offload] Tool '{tool_name}' returned {len(content):,} chars "
                f"(exceeded {self.config.nimfs_offload_threshold:,} threshold).\n"
                f"Full output stored at: {ref}\n"
                f"Use NimFSReadArtifact(ref='{ref}') to retrieve the complete content.\n\n"
                f"Preview:\n{content[:preview_len]}{'...' if len(content) > preview_len else ''}"
            )
        except Exception:
            # Offload failed — return original content unchanged (graceful degradation)
            return content

    def _auto_detect_tool_failure(self, tool_call_id: str, content: str) -> bool:
        """Detect explicit failures."""
        # Recovery output exemption - already contains useful recovery info, don't discard
        if "[Auto-Recovery Output]" in content:
            return False
        if content.startswith("[Error]"):
            self.mark_tool_call(tool_call_id, discard=True, reason="auto_detected_failure")
            return True
        # Only match actual Python tracebacks, not source code containing "Traceback"
        stripped = content.lstrip()
        if stripped.startswith("Traceback (most recent call last)"):
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

    def get_last_messages(self, count: int) -> List[Dict[str, Any]]:
        """
        Get the last N messages from the current frame as raw dictionaries.
        Used for post-mortem analysis and debugging.
        """
        messages = self.current_frame.messages
        last_n = messages[-count:] if count > 0 else []
        return [msg.to_dict() for msg in last_n]

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

    # =========================================================================
    # Context Assembly (The "Smart Drop" Strategy)
    # =========================================================================

    def assemble_context(
        self,
        system_prefix: Optional[str] = None,
        model_features: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        Delegates the context window generation to the ContextAssembler.
        """
        from nimbus.core.memory.context_assembler import ContextAssembler
        assembler = ContextAssembler(self)
        return assembler.assemble(system_prefix, model_features)

    # =========================================================================
    # State & Utils
    # =========================================================================

    def estimate_tokens(self) -> int:
        """Estimate total tokens currently tracked by the MMU."""
        from nimbus.core.memory.token_budget import estimate_total_tokens, _approx_tokens
        
        # Calculate pinned tokens roughly
        pinned_text = ""
        if self._pinned:
            if getattr(self._pinned, "system_rules", None):
                pinned_text += self._pinned.system_rules
                
        # Gather all stream messages for counting
        stream_messages = []
        for frame in self._stack:
            stream_messages.extend([msg.to_dict() for msg in frame.to_context_messages()])
            
        return estimate_total_tokens(_approx_tokens(pinned_text), stream_messages)


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
