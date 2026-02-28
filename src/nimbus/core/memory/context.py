"""
Nimbus v2 Memory Context - PinnedContext and StackFrame

This module defines the core memory structures:
- PinnedContext: Immutable system anchors that never get compressed
- StackFrame: Call stack frames for subprocess management
- Message: Standard message format for LLM interactions

Design Principles:
- Pinned context is ALWAYS at the top of the context window
- Stack frames grow downward (newest frame at the bottom)
- Each frame has its own message history (isolation)
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# =============================================================================
# Message Format
# =============================================================================

# Conservative estimate for image tokens across different models
# Claude: ~2764 (1080p), GPT-4V: ~1105 (high), Gemini: 258
# We pick 1500 as a reasonable average
IMAGE_TOKEN_ESTIMATE = 1500

# Per-message overhead: role marker, separators, formatting tokens
# Standard across OpenAI/Anthropic APIs (~4 tokens per message)
MESSAGE_OVERHEAD = 4

# Fixed overhead per tool call: id (~3 tokens), type (~1), function name (~2),
# JSON structure tokens (~4)
TOOL_CALL_OVERHEAD = 10

MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    """
    Standard message format for LLM interactions.

    This is compatible with OpenAI/Anthropic message formats.

    Attributes:
        role: Message role (system/user/assistant/tool)
        content: Message content (text or structured)
        name: Optional name (for tool messages)
        tool_call_id: Optional tool call ID (for tool results)
        tool_calls: Optional list of tool calls (for assistant messages)
        meta: Additional metadata
    """

    role: MessageRole
    content: Any  # str or list of content blocks
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None  # For assistant messages with tool calls
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict format for LLM API."""
        d: Dict[str, Any] = {"role": self.role}
        # Content can be None if tool_calls is present
        if self.content is not None:
            d["content"] = self.content
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d

    @staticmethod
    def _estimate_text(text: str) -> int:
        """
        Estimate token count for a text string with language awareness.

        Ratios (per expert review):
        - English: ~4 chars/token
        - Chinese: ~1.5-2 chars/token (more conservative: 1.5)
        - Code: ~3 chars/token (keywords, symbols)
        """
        if not text:
            return 0
        # Count Chinese characters
        chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other_chars = len(text) - chinese_chars
        # Chinese: 1.5 chars/token, Other: 4 chars/token
        return int(chinese_chars / 1.5) + (other_chars // 4)

    def _estimate_content(self) -> int:
        """Estimate tokens from message content (text and image blocks)."""
        if isinstance(self.content, str):
            return self._estimate_text(self.content)
        elif isinstance(self.content, list):
            total = 0
            for block in self.content:
                if isinstance(block, dict):
                    if "text" in block:
                        total += self._estimate_text(block["text"])
                    elif block.get("type") == "image":
                        total += IMAGE_TOKEN_ESTIMATE
            return total
        return 0

    def _estimate_tool_calls(self) -> int:
        """Estimate tokens from tool_calls payloads."""
        if not self.tool_calls:
            return 0
        total = 0
        for tc in self.tool_calls:
            # Each tool call has id, type, function structure
            total += TOOL_CALL_OVERHEAD
            if isinstance(tc, dict):
                func = tc.get("function", {})
                if isinstance(func, dict):
                    # Function name tokens
                    name = func.get("name", "")
                    if name:
                        total += self._estimate_text(name)
                    # Arguments JSON tokens
                    args = func.get("arguments", "")
                    if isinstance(args, str):
                        total += self._estimate_text(args)
                    elif isinstance(args, dict):
                        total += self._estimate_text(json.dumps(args))
        return total

    def token_estimate(self) -> int:
        """
        Token estimate with language awareness and structural overhead.

        Accounts for:
        - Per-message overhead (role, separators, formatting)
        - Content text (English/Chinese aware)
        - Image blocks
        - Tool call payloads (id, function name, arguments JSON)
        """
        tokens = MESSAGE_OVERHEAD
        tokens += self._estimate_content()
        tokens += self._estimate_tool_calls()
        return tokens

    def token_estimate_view(self, max_tool_chars: int = 10_000) -> int:
        """Token estimate based on view-truncated content (matches what LLM actually receives)."""
        if self.role == "tool" and isinstance(self.content, str) and len(self.content) > max_tool_chars:
            capped_tokens = self._estimate_text(self.content[:max_tool_chars])
            return MESSAGE_OVERHEAD + capped_tokens
        return self.token_estimate()


# =============================================================================
# Pinned Context
# =============================================================================


@dataclass
class PinnedContext:
    """
    Immutable system anchors that NEVER get compressed or removed.

    The PinnedContext sits at the very top of the context window.
    It contains critical information that the Agent must always see:
    - System rules (e.g., "don't hallucinate", "use tools correctly")
    - Workspace information (cwd, project structure)
    - Capability descriptions (what tools are available)

    Design Principle: "No matter how deep the call stack, the Agent
    never forgets the system rules."

    Attributes:
        system_rules: Core behavioral rules (highest priority)
        workspace_info: Current workspace context
        capabilities: Available tools and their descriptions
        custom_anchors: User-defined pinned content
        version: Schema version
    """

    system_rules: str = ""
    workspace_info: str = ""
    env_state: str = ""  # Dynamic environment state (e.g. key vars, paths)
    capabilities: str = ""
    custom_anchors: Dict[str, str] = field(default_factory=dict)
    version: str = "1.0"

    def to_system_message(self) -> Message:
        """Convert to a system message for LLM."""
        parts = []

        if self.system_rules:
            parts.append(f"# System Rules\n{self.system_rules}")

        if self.workspace_info:
            parts.append(f"# Workspace\n{self.workspace_info}")

        if self.env_state:
            parts.append(f"# Environment State\n{self.env_state}")

        if self.capabilities:
            parts.append(f"# Capabilities\n{self.capabilities}")

        for k, v in self.custom_anchors.items():
            parts.append(f"{k}:\n{v}")

        content = "\n\n".join(parts)
        return Message(role="system", content=content, meta={"pinned": True})

    def token_estimate(self) -> int:
        """Token estimate with language awareness. Reuses Message._estimate_text."""
        total = Message._estimate_text(self.system_rules)
        total += Message._estimate_text(self.workspace_info)
        total += Message._estimate_text(self.env_state)
        total += Message._estimate_text(self.capabilities)
        for k, v in self.custom_anchors.items():
            total += Message._estimate_text(k) + Message._estimate_text(v)
        return total

    def add_anchor(self, key: str, content: str) -> None:
        """Add a custom anchor."""
        self.custom_anchors[key] = content

    def update_workspace(self, info: str) -> None:
        """Update workspace information."""
        self.workspace_info = info

    def update_env_state(self, state: str) -> None:
        """Update environment state."""
        self.env_state = state

    def update_capabilities(self, caps: str) -> None:
        """Update capabilities description."""
        self.capabilities = caps


# =============================================================================
# Stack Frame
# =============================================================================

FrameState = Literal["ACTIVE", "SUSPENDED", "COMPLETED", "FAILED"]


@dataclass
class StackFrame:
    """
    Call stack frame for subprocess management.

    Each SUB_CALL creates a new StackFrame. The frame contains:
    - Its own message history (isolation from parent)
    - The goal it's trying to achieve
    - Metadata about the call

    When RETURN is called, the frame is popped and its result
    is passed back to the parent frame.

    Attributes:
        frame_id: Unique frame identifier
        goal: What this frame is trying to achieve
        messages: Conversation history within this frame
        state: Frame state (ACTIVE/SUSPENDED/COMPLETED/FAILED)
        parent_frame_id: ID of parent frame (None for root)
        result: Result when frame completes
        created_at: Creation timestamp
        meta: Additional metadata
    """

    frame_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    goal: str = ""
    messages: List[Message] = field(default_factory=list)
    state: FrameState = "ACTIVE"
    parent_frame_id: Optional[str] = None
    result: Any = None
    created_at: float = field(default_factory=time.time)
    meta: Dict[str, Any] = field(default_factory=dict)

    def add_message(self, message: Message) -> None:
        """Add a message to this frame's history."""
        self.messages.append(message)

    def add_user_message(self, content: "str | list") -> None:
        """Add a user message.
        
        Args:
            content: Text string or list of content blocks 
                     (e.g. [{"type": "text", "text": "..."}, {"type": "image", "data": "base64...", "mimeType": "image/png"}])
        """
        self.messages.append(Message(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message."""
        self.messages.append(Message(role="assistant", content=content))

    def add_assistant_with_tool_calls(
        self, content: Optional[str], tool_calls: List[Dict[str, Any]]
    ) -> None:
        """Add an assistant message with tool calls.

        This is used when the LLM responds with tool calls. The message format
        is compatible with OpenAI/OpenRouter API which requires the assistant
        message with tool_calls to be present before the tool result messages.

        Args:
            content: Optional text content from the assistant
            tool_calls: List of tool call objects in OpenAI format
        """
        self.messages.append(Message(role="assistant", content=content, tool_calls=tool_calls))

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """Add a tool result message."""
        self.messages.append(
            Message(role="tool", content=content, name=name, tool_call_id=tool_call_id)
        )

    def token_estimate(self) -> int:
        """Estimate total tokens in this frame."""
        total = len(self.goal) // 4
        for msg in self.messages:
            total += msg.token_estimate()
        return total

    def complete(self, result: Any) -> None:
        """Mark frame as completed with result."""
        self.state = "COMPLETED"
        self.result = result

    def fail(self, error: str) -> None:
        """Mark frame as failed."""
        self.state = "FAILED"
        self.result = error

    def to_context_messages(self) -> List[Message]:
        """Get messages for context assembly."""
        # Start with the goal as a user message if this is a sub-frame
        result = []
        if self.goal and self.parent_frame_id is not None:
            result.append(
                Message(
                    role="user",
                    content=f"[Subtask] {self.goal}",
                    meta={"frame_id": self.frame_id, "is_goal": True},
                )
            )
        result.extend(self.messages)
        return result


# =============================================================================
# Factory Functions
# =============================================================================


def create_root_frame(goal: str = "") -> StackFrame:
    """Create the root frame (no parent)."""
    return StackFrame(goal=goal, parent_frame_id=None, meta={"is_root": True})


def create_sub_frame(parent_frame_id: str, goal: str) -> StackFrame:
    """Create a sub-frame with parent reference."""
    return StackFrame(goal=goal, parent_frame_id=parent_frame_id, meta={"is_root": False})
