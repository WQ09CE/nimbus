"""
MMU (Memory Management Unit) — Context window management.

The core innovation: Anchor & Stream architecture.
- Anchor (PinnedContext): Immutable system rules that NEVER get compressed.
  Prevents the LLM from "forgetting" its instructions during long tasks.
- Stream (message history): Mutable conversation history that gets
  compressed (archived) when approaching the context limit.

This is what separates a capable agent from a naive chatbot.
Without it, the LLM drifts off-task after ~20 turns.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# =============================================================================
# Token Estimation
# =============================================================================

MESSAGE_OVERHEAD = 4  # role marker, separators


def estimate_text_tokens(text: str) -> int:
    """Estimate token count with CJK awareness."""
    if not text:
        return 0
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return int(cjk / 1.5) + (other // 4)


# =============================================================================
# Message
# =============================================================================


@dataclass
class Message:
    """Standard message format compatible with OpenAI/Anthropic APIs."""
    role: str  # "system", "user", "assistant", "tool"
    content: Any = ""
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d

    def token_estimate(self) -> int:
        tokens = MESSAGE_OVERHEAD
        if isinstance(self.content, str):
            tokens += estimate_text_tokens(self.content)
        if self.tool_calls:
            for tc in self.tool_calls:
                tokens += 10  # tool call overhead
                func = tc.get("function", {})
                tokens += estimate_text_tokens(func.get("name", ""))
                args = func.get("arguments", "")
                tokens += estimate_text_tokens(str(args))
        return tokens


# =============================================================================
# Pinned Context (Anchor)
# =============================================================================


@dataclass
class PinnedContext:
    """Immutable system context that NEVER gets compressed.

    No matter how long the conversation, the agent always sees these rules.
    This fights the "recency bias" problem in long-horizon tasks.
    """
    system_rules: str = ""
    workspace_info: str = ""
    env_state: str = ""
    capabilities: str = ""
    custom_anchors: Dict[str, str] = field(default_factory=dict)

    def to_system_message(self) -> Message:
        parts = []
        if self.system_rules:
            parts.append(f"# System Rules\n{self.system_rules}")
        if self.workspace_info:
            parts.append(f"# Workspace\n{self.workspace_info}")
        if self.env_state:
            parts.append(f"# Environment\n{self.env_state}")
        if self.capabilities:
            parts.append(f"# Capabilities\n{self.capabilities}")
        for k, v in self.custom_anchors.items():
            parts.append(f"# {k}\n{v}")
        return Message(role="system", content="\n\n".join(parts))

    def token_estimate(self) -> int:
        total = estimate_text_tokens(self.system_rules)
        total += estimate_text_tokens(self.workspace_info)
        total += estimate_text_tokens(self.env_state)
        total += estimate_text_tokens(self.capabilities)
        for k, v in self.custom_anchors.items():
            total += estimate_text_tokens(k) + estimate_text_tokens(v)
        return total


# =============================================================================
# MMU Configuration
# =============================================================================


@dataclass
class MMUConfig:
    max_context_tokens: int = 100_000
    compress_threshold: float = 0.85  # trigger compaction at 85% capacity
    summary_max_tokens: int = 2000


# =============================================================================
# MMU — The Memory Management Unit
# =============================================================================


class MMU:
    """Manages the context window: Anchor (pinned) + Stream (history).

    Usage:
        mmu = MMU(config)
        mmu.set_pinned(PinnedContext(system_rules="..."))
        mmu.add_user_message("Fix the bug in auth.py")
        mmu.add_assistant_message("Let me read the file first.")
        messages = mmu.assemble_context()  # → list of dicts for LLM API
    """

    def __init__(self, config: Optional[MMUConfig] = None):
        self.config = config or MMUConfig()
        self._pinned: Optional[PinnedContext] = None
        self._messages: List[Message] = []
        self._archives: List[str] = []  # past compaction summaries
        self._goal: str = ""

    # --- Pinned Context (Anchor) ---

    def set_pinned(self, pinned: PinnedContext) -> None:
        self._pinned = pinned

    def get_pinned(self) -> Optional[PinnedContext]:
        return self._pinned

    def set_goal(self, goal: str) -> None:
        """Pin the user's original goal (resists recency bias)."""
        self._goal = goal

    # --- Message Management (Stream) ---

    def add_user_message(self, content: Any) -> None:
        self._messages.append(Message(role="user", content=content))

    def add_assistant_message(self, content: str) -> None:
        self._messages.append(Message(role="assistant", content=content))

    def add_assistant_with_tool_calls(self, content: Optional[str], tool_calls: List[Dict]) -> None:
        self._messages.append(Message(role="assistant", content=content, tool_calls=tool_calls))

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        self._messages.append(Message(
            role="tool", content=content, name=name, tool_call_id=tool_call_id,
        ))

    def add_system_message(self, content: str) -> None:
        """Inject a transient system message (e.g., compaction notice)."""
        self._messages.append(Message(role="user", content=f"[System] {content}"))

    @property
    def message_count(self) -> int:
        return len(self._messages)

    # --- Context Assembly ---

    def assemble_context(self) -> List[Dict[str, Any]]:
        """Build the full messages array for the LLM API call.

        Structure:
        1. System message (from PinnedContext) — always first
        2. Goal reminder (if set) — pinned after system
        3. Archive summaries (from past compactions)
        4. Current message history
        """
        messages = []

        # 1. System (Anchor)
        if self._pinned:
            messages.append(self._pinned.to_system_message().to_dict())

        # 2. Goal reminder (resists recency bias in long conversations)
        if self._goal:
            messages.append({"role": "user", "content": f"[Goal] {self._goal}"})

        # 3. Archive summaries
        if self._archives:
            summary = "\n\n---\n\n".join(self._archives)
            messages.append({
                "role": "user",
                "content": f"[Previous conversation summary]\n{summary}",
            })

        # 4. Current stream
        for msg in self._messages:
            messages.append(msg.to_dict())

        return messages

    # --- Token Estimation ---

    def estimate_tokens(self) -> int:
        total = 0
        if self._pinned:
            total += self._pinned.token_estimate()
        if self._goal:
            total += estimate_text_tokens(self._goal) + MESSAGE_OVERHEAD
        for archive in self._archives:
            total += estimate_text_tokens(archive) + MESSAGE_OVERHEAD
        for msg in self._messages:
            total += msg.token_estimate()
        return total

    def needs_compaction(self) -> bool:
        threshold = int(self.config.max_context_tokens * self.config.compress_threshold)
        return self.estimate_tokens() >= threshold

    # --- Compaction (Archive & Reset) ---

    async def archive_and_reset(
        self,
        summarizer: Optional[Callable] = None,
    ) -> Optional[str]:
        """Compress current history into a summary and start fresh.

        This is the key mechanism for infinite-horizon conversations.
        When context is running out:
        1. Summarize the current conversation (via LLM or simple truncation)
        2. Store the summary in archives
        3. Clear the message history

        Args:
            summarizer: async function(messages) -> summary string.
                       If None, uses a simple last-N-messages extraction.

        Returns:
            The summary text, or None if nothing to compact.
        """
        if not self._messages:
            return None

        if summarizer:
            # Use LLM to generate a summary
            context = self.assemble_context()
            summary = await summarizer(context)
        else:
            # Fallback: keep last few messages as "summary"
            recent = self._messages[-4:]
            parts = []
            for msg in self._messages[:-4]:
                if msg.role == "assistant" and msg.content:
                    parts.append(str(msg.content)[:200])
                elif msg.role == "tool" and msg.content:
                    parts.append(f"[{msg.name}]: {str(msg.content)[:100]}")
            if parts:
                summary = "Previous actions:\n" + "\n".join(parts[-10:])
            else:
                summary = "(conversation history compacted)"
            self._messages = list(recent)
            self._archives.append(summary)
            return summary

        # After LLM summary
        self._messages.clear()
        self._archives.append(summary)
        return summary

    def rollback_incomplete_turn(self) -> int:
        """Remove trailing orphaned tool results (no matching assistant message)."""
        removed = 0
        while self._messages and self._messages[-1].role == "tool":
            self._messages.pop()
            removed += 1
        # Also remove trailing assistant with tool_calls if no results followed
        if (self._messages and self._messages[-1].role == "assistant"
                and self._messages[-1].tool_calls):
            self._messages.pop()
            removed += 1
        return removed

    def clear(self) -> None:
        self._messages.clear()
        self._archives.clear()
        self._goal = ""
