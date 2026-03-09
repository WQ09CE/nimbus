"""
MMU (Memory Management Unit) — Context window management.

The core innovation: Anchor & Stream architecture.
- Anchor (PinnedContext): Immutable system rules that NEVER get compressed.
  Prevents the LLM from "forgetting" its instructions during long tasks.
- Stream (message history): Mutable conversation history that gets
  compressed (archived) when approaching the context limit.

History dropping design (from original nimbus):
- Safe cut points: NEVER split tool_call ↔ tool_result pairs
- Tombstone stubs: dropped messages leave behind a one-line trace
- Smart drop: failures/errors dropped first, then oldest non-essential
- Archive merge: global summary is merged (not appended) to prevent growth

This is what separates a capable agent from a naive chatbot.
Without it, the LLM drifts off-task after ~20 turns.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("nimbus.mmu")


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

    @property
    def is_tool_call(self) -> bool:
        """This is an assistant message that initiates tool calls."""
        return self.role == "assistant" and bool(self.tool_calls)

    @property
    def is_tool_result(self) -> bool:
        """This is a tool result message."""
        return self.role == "tool"

    @property
    def is_error(self) -> bool:
        """Heuristic: does this message contain an error/failure?"""
        if not isinstance(self.content, str):
            return False
        c = self.content
        # Explicit error markers
        if c.startswith("[Error]"):
            return True
        # Python traceback
        stripped = c.lstrip()
        if stripped.startswith("Traceback (most recent call last)"):
            return True
        # Common error patterns (only at start to avoid false positives)
        lower = c[:200].lower()
        return any(marker in lower for marker in [
            "error:", "failed:", "exception:", "command timed out",
            "doom loop terminated", "tool_failure",
        ])


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

    def to_system_message(self) -> Message:
        parts = []
        if self.system_rules:
            parts.append(f"# System Rules\n{self.system_rules}")
        if self.workspace_info:
            parts.append(f"# Workspace\n{self.workspace_info}")
        return Message(role="system", content="\n\n".join(parts))

    def token_estimate(self) -> int:
        return estimate_text_tokens(self.system_rules) + estimate_text_tokens(self.workspace_info)


# =============================================================================
# MMU Configuration
# =============================================================================


@dataclass
class MMUConfig:
    max_context_tokens: int = 100_000
    compress_threshold: float = 0.85  # trigger compaction at 85% capacity
    summary_max_tokens: int = 2000
    keep_recent_messages: int = 20  # minimum hot messages to always retain


# =============================================================================
# Tool-Use Turn Detection
# =============================================================================


def _find_turn_boundaries(messages: List[Message]) -> List[tuple[int, int]]:
    """Identify tool-use turns: (assistant_with_tool_calls, last_tool_result).

    A tool-use turn is:
      messages[i]   = assistant with tool_calls
      messages[i+1] = tool result
      messages[i+2] = tool result  (possibly more)
      ...until next non-tool message

    These are ATOMIC — dropping any part breaks the LLM API contract.
    """
    turns: List[tuple[int, int]] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.is_tool_call:
            start = i
            end = i
            # Collect all following tool results
            j = i + 1
            while j < len(messages) and messages[j].is_tool_result:
                end = j
                j += 1
            if end > start:
                turns.append((start, end))
            i = j
        else:
            i += 1
    return turns


# =============================================================================
# Tombstone Stubs
# =============================================================================


def _make_tombstone(messages: List[Message]) -> str:
    """Create a one-line-per-message tombstone for dropped messages.

    Instead of silently deleting history, leave a compact trace so the LLM
    knows what happened (even if it can't see the full content).
    """
    lines: List[str] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.is_tool_call:
            # Summarize the tool-use turn as one line
            tool_names = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    fname = tc.get("function", {}).get("name", "?")
                    tool_names.append(fname)
            # Collect results
            results_summary = []
            j = i + 1
            while j < len(messages) and messages[j].is_tool_result:
                r = messages[j]
                status = "ERR" if r.is_error else "OK"
                results_summary.append(f"{r.name or '?'}→{status}")
                j += 1
            tools_str = "+".join(tool_names)
            results_str = ", ".join(results_summary)
            lines.append(f"  [{tools_str}] {results_str}")
            i = j
        elif msg.role == "assistant":
            preview = str(msg.content)[:80].replace("\n", " ") if msg.content else "(empty)"
            lines.append(f"  Assistant: {preview}")
            i += 1
        elif msg.role == "user":
            preview = str(msg.content)[:80].replace("\n", " ") if msg.content else "(empty)"
            lines.append(f"  User: {preview}")
            i += 1
        else:
            i += 1

    if not lines:
        return "(empty history dropped)"

    count = len(messages)
    return f"[Dropped {count} messages. Trace:]\n" + "\n".join(lines)


# =============================================================================
# Smart Drop
# =============================================================================


def _smart_drop(
    messages: List[Message],
    target_tokens: int,
    keep_recent: int,
) -> tuple[List[Message], str]:
    """Drop messages to fit within token budget, with priority ordering.

    Priority (drop first → last):
    1. Failed/error tool-use turns (least valuable — agent already failed)
    2. Old successful tool-use turns (content was consumed by the LLM)
    3. Old user/assistant exchanges

    Never drops the last `keep_recent` messages.
    Never splits tool_call ↔ tool_result pairs.
    Dropped messages become a tombstone stub.

    Returns: (surviving messages, tombstone text)
    """
    if not messages:
        return messages, ""

    # Protect hot zone
    hot_boundary = max(0, len(messages) - keep_recent)

    # Nothing to drop
    current_tokens = sum(m.token_estimate() for m in messages)
    if current_tokens <= target_tokens:
        return messages, ""

    # Build droppable segments: each is (start, end, priority, tokens)
    # Lower priority number = drop first
    segments: List[tuple[int, int, int, int]] = []
    turns = _find_turn_boundaries(messages)
    covered: set[int] = set()

    for start, end in turns:
        if start >= hot_boundary:
            continue  # In hot zone, don't touch
        turn_msgs = messages[start:end + 1]
        turn_tokens = sum(m.token_estimate() for m in turn_msgs)
        has_error = any(m.is_error for m in turn_msgs)
        priority = 1 if has_error else 2
        segments.append((start, end, priority, turn_tokens))
        for k in range(start, end + 1):
            covered.add(k)

    # Non-turn messages in history zone
    for i in range(hot_boundary):
        if i in covered:
            continue
        priority = 3  # plain messages are lowest priority for dropping
        segments.append((i, i, priority, messages[i].token_estimate()))

    # Sort by priority (drop first = lowest number), then by index (oldest first)
    segments.sort(key=lambda s: (s[2], s[0]))

    # Drop segments until under budget
    to_drop: set[int] = set()
    tokens_freed = 0
    tokens_needed = current_tokens - target_tokens

    for start, end, priority, seg_tokens in segments:
        if tokens_freed >= tokens_needed:
            break
        for k in range(start, end + 1):
            to_drop.add(k)
        tokens_freed += seg_tokens

    if not to_drop:
        return messages, ""

    # Build tombstone from dropped messages
    dropped_msgs = [messages[i] for i in sorted(to_drop)]
    tombstone = _make_tombstone(dropped_msgs)

    # Build surviving list
    surviving = [messages[i] for i in range(len(messages)) if i not in to_drop]

    logger.info(
        "Smart drop: removed %d messages (%d tokens freed), %d remaining",
        len(to_drop), tokens_freed, len(surviving),
    )

    return surviving, tombstone


# =============================================================================
# MMU — The Memory Management Unit
# =============================================================================


class MMU:
    """Manages the context window: Anchor (pinned) + Stream (history).

    Key safety guarantees:
    - Tool-use turns (assistant+tool_calls → tool results) are NEVER split
    - Dropped messages leave a tombstone trace
    - Archives are merged (not infinitely appended)
    - Hot zone (recent N messages) is always preserved

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
        self._global_summary: str = ""  # merged summary (NOT a list — prevents growth)
        self._goal: str = ""

    # --- Pinned Context (Anchor) ---

    def set_pinned(self, pinned: PinnedContext) -> None:
        self._pinned = pinned

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
        3. Global summary (merged from past compactions)
        4. Current message history
        """
        messages = []

        # 1. System (Anchor)
        if self._pinned:
            messages.append(self._pinned.to_system_message().to_dict())

        # 2. Goal reminder (resists recency bias in long conversations)
        if self._goal:
            messages.append({
                "role": "user", 
                "content": f"### 🎯 CURRENT GOAL\n{self._goal}\n\n---\n"
            })

        # 3. Global summary (single merged string, not a growing list)
        if self._global_summary:
            messages.append({
                "role": "user",
                "content": f"[Previous conversation summary]\n{self._global_summary}",
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
        if self._global_summary:
            total += estimate_text_tokens(self._global_summary) + MESSAGE_OVERHEAD
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

        Key improvements over naive compaction:
        1. Safe cut: never splits tool_call ↔ tool_result pairs
        2. Smart drop: errors/failures dropped first
        3. Tombstone: dropped messages leave a trace
        4. Merge: global summary is UPDATED (not appended) to prevent growth

        Args:
            summarizer: async function(messages) -> summary string.
                       If None, uses deterministic extraction.

        Returns:
            The summary text, or None if nothing to compact.
        """
        if not self._messages:
            return None

        if summarizer:
            # LLM-powered summarization
            context = self.assemble_context()
            new_summary = await summarizer(context)
            # Merge with existing summary (not append)
            self._update_global_summary(new_summary)
            self._messages.clear()
            return new_summary

        # --- Fallback: deterministic compaction ---

        # Calculate how many tokens we need to free
        anchor_tokens = 0
        if self._pinned:
            anchor_tokens += self._pinned.token_estimate()
        if self._goal:
            anchor_tokens += estimate_text_tokens(self._goal) + MESSAGE_OVERHEAD
        if self._global_summary:
            anchor_tokens += estimate_text_tokens(self._global_summary) + MESSAGE_OVERHEAD

        stream_budget = int(self.config.max_context_tokens * 0.7) - anchor_tokens
        keep_recent = min(self.config.keep_recent_messages, len(self._messages))

        # Smart drop: prioritized dropping with safe cut points
        surviving, tombstone = _smart_drop(
            self._messages,
            target_tokens=max(stream_budget, 0),
            keep_recent=keep_recent,
        )

        # Build summary from tombstone + previous summary
        summary_parts: List[str] = []
        if self._global_summary:
            summary_parts.append(self._global_summary)
        if tombstone:
            summary_parts.append(tombstone)

        new_summary = "\n\n".join(summary_parts) if summary_parts else "(history compacted)"

        # Trim summary if too long (keep last N chars)
        max_summary_chars = self.config.summary_max_tokens * 4  # rough token→char
        if len(new_summary) > max_summary_chars:
            new_summary = "..." + new_summary[-(max_summary_chars - 3):]

        self._global_summary = new_summary
        self._messages = surviving

        return new_summary

    def _update_global_summary(self, new_summary: str) -> None:
        """Merge new summary into global summary (not append).

        If there's an existing summary, the new one should supersede it
        since the LLM summarizer was given the full context including
        the old summary. We replace, not append, to prevent unbounded growth.
        """
        # LLM summary replaces old one (it already incorporated the old)
        max_chars = self.config.summary_max_tokens * 4
        if len(new_summary) > max_chars:
            new_summary = "..." + new_summary[-(max_chars - 3):]
        self._global_summary = new_summary

    def clear(self) -> None:
        self._messages.clear()
        self._global_summary = ""
        self._goal = ""
