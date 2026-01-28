"""
Nimbus v2 Memory Management Unit (MMU)

The MMU is responsible for:
1. Managing the context window for LLM interactions
2. Maintaining the call stack (SUB_CALL / RETURN)
3. Assembling context from Pinned + Stack
4. Token budget management and compression

Memory Layout:
┌─────────────────────────────────┐
│        Pinned Context           │  ← Always at top (immutable)
│  - System Rules                 │
│  - Workspace Info               │
│  - Capabilities                 │
├─────────────────────────────────┤
│        Root Frame               │  ← Main conversation
│  - messages[...]                │
├─────────────────────────────────┤
│        Sub Frame 1              │  ← First SUB_CALL
│  - goal: "explore codebase"     │
│  - messages[...]                │
├─────────────────────────────────┤
│        Sub Frame 2              │  ← Nested SUB_CALL
│  - goal: "find auth module"     │
│  - messages[...]                │
└─────────────────────────────────┘
         ↑ Current frame (top of stack)

Design Principles:
- Pinned context is NEVER compressed
- Each frame is isolated (has its own message history)
- Context is assembled bottom-up (current frame first)
- Token budget is enforced during assembly
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from nimbus.v2.core.memory.context import (
    PinnedContext,
    StackFrame,
    Message,
    create_root_frame,
    create_sub_frame,
)


@dataclass
class MMUConfig:
    """
    Configuration for MMU.

    Attributes:
        max_context_tokens: Maximum tokens for the entire context
        pinned_budget: Token budget for pinned context
        frame_budget: Token budget per frame
        compress_threshold: Trigger compression at this percentage
        keep_recent_messages: Number of recent messages to keep when compressing
    """
    max_context_tokens: int = 16000
    pinned_budget: int = 2000
    frame_budget: int = 8000
    compress_threshold: float = 0.9  # Compress at 90% capacity
    keep_recent_messages: int = 10


class MMU:
    """
    Memory Management Unit.

    Manages the context window for a single process.
    Handles the call stack for SUB_CALL/RETURN operations.

    Example:
        mmu = MMU(config=MMUConfig())
        mmu.set_pinned(PinnedContext(system_rules="Be helpful"))

        # Add conversation
        mmu.add_user_message("Hello")
        mmu.add_assistant_message("Hi there!")

        # Subprocess call
        mmu.push_frame("explore codebase")
        mmu.add_user_message("Find the auth module")
        # ... subprocess work ...
        result = mmu.pop_frame("Found it at src/auth/")

        # Assemble context for LLM
        messages = mmu.assemble_context()
    """

    def __init__(self, config: Optional[MMUConfig] = None, process_id: str = ""):
        """
        Initialize MMU.

        Args:
            config: MMU configuration
            process_id: Process ID for this MMU instance
        """
        self.config = config or MMUConfig()
        self.process_id = process_id

        # Pinned context (always at top)
        self._pinned: Optional[PinnedContext] = None

        # Call stack (root frame at index 0, current frame at end)
        self._stack: List[StackFrame] = []

        # Initialize with root frame
        self._stack.append(create_root_frame())

    # =========================================================================
    # Pinned Context Management
    # =========================================================================

    def set_pinned(self, pinned: PinnedContext) -> None:
        """Set the pinned context."""
        self._pinned = pinned

    def get_pinned(self) -> Optional[PinnedContext]:
        """Get the pinned context."""
        return self._pinned

    def update_system_rules(self, rules: str) -> None:
        """Update system rules in pinned context."""
        if self._pinned is None:
            self._pinned = PinnedContext()
        self._pinned.system_rules = rules

    def update_workspace_info(self, info: str) -> None:
        """Update workspace info in pinned context."""
        if self._pinned is None:
            self._pinned = PinnedContext()
        self._pinned.workspace_info = info

    def update_capabilities(self, caps: str) -> None:
        """Update capabilities in pinned context."""
        if self._pinned is None:
            self._pinned = PinnedContext()
        self._pinned.capabilities = caps

    # =========================================================================
    # Stack Management (SUB_CALL / RETURN)
    # =========================================================================

    @property
    def current_frame(self) -> StackFrame:
        """Get the current (top) frame."""
        return self._stack[-1]

    @property
    def stack_depth(self) -> int:
        """Get the current stack depth."""
        return len(self._stack)

    @property
    def is_root_frame(self) -> bool:
        """Check if currently in root frame."""
        return len(self._stack) == 1

    def push_frame(self, goal: str, meta: Optional[Dict[str, Any]] = None) -> str:
        """
        Push a new frame onto the stack (SUB_CALL).

        Args:
            goal: Goal for the new frame
            meta: Additional metadata

        Returns:
            Frame ID of the new frame
        """
        parent_id = self.current_frame.frame_id
        new_frame = create_sub_frame(parent_id, goal)
        if meta:
            new_frame.meta.update(meta)

        # Suspend current frame
        self.current_frame.state = "SUSPENDED"

        # Push new frame
        self._stack.append(new_frame)

        return new_frame.frame_id

    def pop_frame(self, result: Any = None) -> Optional[Any]:
        """
        Pop the current frame (RETURN).

        Args:
            result: Result to pass back to parent frame

        Returns:
            The result, or None if at root frame
        """
        if self.is_root_frame:
            # Can't pop root frame
            return None

        # Complete current frame
        frame = self._stack.pop()
        frame.complete(result)

        # Resume parent frame
        self.current_frame.state = "ACTIVE"

        # Add result to parent frame as tool result
        self.current_frame.add_assistant_message(
            f"[Subtask completed] {frame.goal}\nResult: {result}"
        )

        return result

    def get_frame(self, frame_id: str) -> Optional[StackFrame]:
        """Get a frame by ID."""
        for frame in self._stack:
            if frame.frame_id == frame_id:
                return frame
        return None

    # =========================================================================
    # Message Management
    # =========================================================================

    def add_message(self, message: Message) -> None:
        """Add a message to the current frame."""
        self.current_frame.add_message(message)

    def add_user_message(self, content: str) -> None:
        """Add a user message to the current frame."""
        self.current_frame.add_user_message(content)

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to the current frame."""
        self.current_frame.add_assistant_message(content)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Add a tool result to the current frame."""
        self.current_frame.add_tool_result(tool_call_id, name, content)

    # =========================================================================
    # Context Assembly
    # =========================================================================

    def assemble_context(self, max_tokens: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Assemble the full context for LLM.

        This method combines:
        1. Pinned context (system message)
        2. Stack frames (from root to current)

        The result is a list of messages ready for LLM API.

        Args:
            max_tokens: Optional token limit (uses config default if not specified)

        Returns:
            List of message dicts for LLM API
        """
        max_tokens = max_tokens or self.config.max_context_tokens
        messages: List[Dict[str, Any]] = []
        token_count = 0

        # 1. Add pinned context (always first)
        if self._pinned:
            pinned_msg = self._pinned.to_system_message()
            pinned_tokens = self._pinned.token_estimate()

            if pinned_tokens <= self.config.pinned_budget:
                messages.append(pinned_msg.to_dict())
                token_count += pinned_tokens

        # 2. Add stack frames (from root to current)
        # We need to be smart about token budget here
        remaining_budget = max_tokens - token_count

        # Collect all frame messages
        all_frame_messages: List[Message] = []
        for frame in self._stack:
            frame_messages = frame.to_context_messages()
            all_frame_messages.extend(frame_messages)

        # Estimate tokens
        frame_tokens = sum(msg.token_estimate() for msg in all_frame_messages)

        # If within budget, include all
        if frame_tokens <= remaining_budget:
            for msg in all_frame_messages:
                messages.append(msg.to_dict())
        else:
            # Need to compress - keep recent messages from current frame
            # and summaries from parent frames
            messages.extend(self._compress_frames(remaining_budget))

        return messages

    def _compress_frames(self, budget: int) -> List[Dict[str, Any]]:
        """
        Compress frames to fit within budget.

        Strategy:
        1. Keep all messages from current frame (up to keep_recent_messages)
        2. Summarize parent frames

        Args:
            budget: Token budget for frames

        Returns:
            Compressed message list
        """
        result: List[Dict[str, Any]] = []
        keep_recent = self.config.keep_recent_messages

        # Process frames from root to current
        for i, frame in enumerate(self._stack):
            is_current = (i == len(self._stack) - 1)

            if is_current:
                # Keep recent messages from current frame
                messages = frame.to_context_messages()
                if len(messages) > keep_recent:
                    # Add summary of older messages
                    older = messages[:-keep_recent]
                    summary = self._summarize_messages(older)
                    result.append(Message(
                        role="system",
                        content=f"[Earlier conversation summary]\n{summary}",
                        meta={"compressed": True}
                    ).to_dict())
                    # Add recent messages
                    for msg in messages[-keep_recent:]:
                        result.append(msg.to_dict())
                else:
                    for msg in messages:
                        result.append(msg.to_dict())
            else:
                # Summarize parent frames
                summary = f"[Frame: {frame.goal}] (completed)" if frame.state == "COMPLETED" else f"[Frame: {frame.goal}] (suspended)"
                result.append(Message(
                    role="system",
                    content=summary,
                    meta={"compressed": True, "frame_id": frame.frame_id}
                ).to_dict())

        return result

    def _summarize_messages(self, messages: List[Message]) -> str:
        """
        Create a simple summary of messages.

        This is a placeholder - in production, you might use
        an LLM to create a proper summary.
        """
        if not messages:
            return "(no earlier messages)"

        parts = []
        for msg in messages:
            role = msg.role
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            # Truncate long content
            if len(content) > 100:
                content = content[:100] + "..."
            parts.append(f"- [{role}] {content}")

        return "\n".join(parts[:5])  # Keep at most 5 summary items

    # =========================================================================
    # Token Management
    # =========================================================================

    def estimate_tokens(self) -> int:
        """Estimate total tokens in current context."""
        total = 0

        if self._pinned:
            total += self._pinned.token_estimate()

        for frame in self._stack:
            total += frame.token_estimate()

        return total

    def needs_compression(self) -> bool:
        """Check if context needs compression."""
        current = self.estimate_tokens()
        threshold = int(self.config.max_context_tokens * self.config.compress_threshold)
        return current > threshold

    # =========================================================================
    # State Management
    # =========================================================================

    def get_state(self) -> Dict[str, Any]:
        """Get MMU state for checkpointing."""
        return {
            "process_id": self.process_id,
            "pinned": self._pinned.__dict__ if self._pinned else None,
            "stack_depth": len(self._stack),
            "current_frame_id": self.current_frame.frame_id,
            "total_messages": sum(len(f.messages) for f in self._stack),
            "estimated_tokens": self.estimate_tokens(),
        }

    def clear(self) -> None:
        """Clear all state and reset to initial."""
        self._pinned = None
        self._stack = [create_root_frame()]
