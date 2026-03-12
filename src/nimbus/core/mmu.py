"""
MMU (Memory Management Unit) — Context window management.

The core innovation: Anchor & Stream architecture.
- Anchor (PinnedContext): Immutable system rules that NEVER get compressed.
  Prevents the LLM from "forgetting" its instructions during long tasks.
- Stream (message history): Mutable conversation history that gets
  compressed (archived) when approaching the context limit.

Compaction strategy (aligned with pi-coding-agent):
1. Token-based cut point: walk backward keeping ~20K tokens verbatim ("hot zone")
2. LLM summarization: everything before cut is serialized to text and summarized
3. Incremental updates: second compaction passes <previous-summary> for update
4. File operation tracking: read/modified files appended as XML tags
5. Structured prompt: Goal, Progress, Key Decisions, Next Steps format
6. Fallback: deterministic tombstone stubs when no summarizer is available

Safety guarantees:
- Tool-use turns (assistant+tool_calls -> tool results) are NEVER split
- Dropped messages leave a tombstone trace
- Global summary is MERGED (not appended) to prevent unbounded growth
- Hot zone (recent N tokens) is always preserved
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("nimbus.mmu")


# =============================================================================
# Token Estimation
# =============================================================================

MESSAGE_OVERHEAD = 4  # role marker, separators


def estimate_text_tokens(text) -> int:
    """Estimate token count with CJK awareness.

    Accepts str, list (multimodal content blocks), or dict.
    """
    if not text:
        return 0
    # Multimodal content: list of blocks e.g. [{'type':'text','text':'...'}, {'type':'image',...}]
    if isinstance(text, list):
        total = 0
        for block in text:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    total += estimate_text_tokens(block.get("text", ""))
                elif block.get("type") == "image":
                    total += 256  # rough token cost for an image
            elif isinstance(block, str):
                total += estimate_text_tokens(block)
        return total
    if isinstance(text, dict):
        # Single content block
        if text.get("type") == "text":
            return estimate_text_tokens(text.get("text", ""))
        if text.get("type") == "image":
            return 256
        return 0
    # Plain string
    text = str(text)
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return int(cjk * 1.2) + (other // 3)


# =============================================================================
# Summarization Prompts (aligned with pi-coding-agent)
# =============================================================================

SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI coding assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use the same format as above (Goal, Constraints, Progress, Key Decisions, Next Steps, Critical Context)."""


# =============================================================================
# Message Serialization & File Operation Tracking (pi-style)
# =============================================================================


def _serialize_messages(messages: List["Message"]) -> str:
    """Serialize messages to flat text for summarization.

    Converts to [User]: / [Assistant]: / [Tool call]: / [Tool result]: format.
    This prevents the summarization LLM from "continuing" the conversation.
    (Aligned with pi-coding-agent's serializeConversation)
    """
    parts: List[str] = []
    for msg in messages:
        if msg.role == "user":
            content = str(msg.content) if msg.content else ""
            if content:
                parts.append(f"[User]: {content}")
        elif msg.role == "assistant":
            if msg.tool_calls:
                calls = []
                for tc in (msg.tool_calls or []):
                    func = tc.get("function", {})
                    name = func.get("name", "?")
                    args = func.get("arguments", "{}")
                    calls.append(f"{name}({args})")
                parts.append(f"[Tool calls]: {'; '.join(calls)}")
            if msg.content:
                parts.append(f"[Assistant]: {msg.content}")
        elif msg.role == "tool":
            preview = str(msg.content)[:500] if msg.content else ""
            status = "ERROR" if msg.is_error else "OK"
            parts.append(f"[Tool result ({msg.name or '?'}, {status})]: {preview}")
        elif msg.role == "system":
            pass  # Skip system messages in serialization
    return "\n\n".join(parts)


def _extract_file_ops(messages: List["Message"]) -> tuple[list[str], list[str]]:
    """Extract file paths from tool calls, returning (read_only_files, modified_files).

    Aligned with pi-coding-agent's file operation tracking.
    """
    read_files: set[str] = set()
    modified_files: set[str] = set()

    for msg in messages:
        if not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            try:
                args = json.loads(func.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                continue
            path = args.get("file_path") or args.get("path") or ""
            if not path:
                continue
            if name in ("Read", "read_file", "Glob", "grep_search", "Grep"):
                read_files.add(path)
            elif name in ("Write", "write_file", "Edit", "edit_file"):
                modified_files.add(path)

    # read_only = read but not modified
    read_only = sorted(read_files - modified_files)
    modified = sorted(modified_files)
    return read_only, modified


def _format_file_ops(read_files: list[str], modified_files: list[str]) -> str:
    """Format file operations as XML tags (pi-style)."""
    sections: List[str] = []
    if read_files:
        sections.append(f"<read-files>\n{chr(10).join(read_files)}\n</read-files>")
    if modified_files:
        sections.append(f"<modified-files>\n{chr(10).join(modified_files)}\n</modified-files>")
    if not sections:
        return ""
    return "\n\n" + "\n\n".join(sections)


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
    user_memory: str = ""  # Contents of ~/.nimbus/memory.md

    def to_system_message(self) -> Message:
        parts = []
        if self.system_rules:
            parts.append(f"# System Rules\n{self.system_rules}")
        if self.workspace_info:
            parts.append(f"# Workspace\n{self.workspace_info}")
        if self.user_memory:
            parts.append(f"# User Memory\n{self.user_memory}")
        return Message(role="system", content="\n\n".join(parts))

    def token_estimate(self) -> int:
        return (
            estimate_text_tokens(self.system_rules)
            + estimate_text_tokens(self.workspace_info)
            + estimate_text_tokens(self.user_memory)
        )


# =============================================================================
# MMU Configuration
# =============================================================================


@dataclass
class MMUConfig:
    max_context_tokens: int = 100_000
    compress_threshold: float = 0.85  # trigger compaction at 85% capacity
    summary_max_tokens: int = 2000
    keep_recent_tokens: int = 20_000  # token budget for hot zone (aligned with pi's keepRecentTokens)


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
    keep_recent_tokens: int,
) -> tuple[List[Message], str]:
    """Drop messages to fit within token budget, with priority ordering.

    Priority (drop first → last):
    1. Failed/error tool-use turns (least valuable — agent already failed)
    2. Old successful tool-use turns (content was consumed by the LLM)
    3. Old user/assistant exchanges

    Protects a token-based hot zone (last ~keep_recent_tokens).
    Never splits tool_call ↔ tool_result pairs.
    Dropped messages become a tombstone stub.

    Returns: (surviving messages, tombstone text)
    """
    if not messages:
        return messages, ""

    # Protect hot zone: walk backward until we accumulate keep_recent_tokens
    # (aligned with pi's keepRecentTokens: 20000)
    hot_boundary = 0
    accumulated = 0
    for i in range(len(messages) - 1, -1, -1):
        accumulated += messages[i].token_estimate()
        if accumulated >= keep_recent_tokens:
            hot_boundary = i
            break

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

    # EMERGENCY: If we STILL need to free tokens, we MUST breach the hot boundary
    # to protect the token limit. (e.g. massive tool results in the hot zone)
    if tokens_freed < tokens_needed:
        hot_segments: List[tuple[int, int, int, int]] = []
        for start, end in turns:
            if start >= hot_boundary:
                turn_msgs = messages[start:end + 1]
                turn_tokens = sum(m.token_estimate() for m in turn_msgs)
                has_error = any(m.is_error for m in turn_msgs)
                priority = 1 if has_error else 2
                hot_segments.append((start, end, priority, turn_tokens))
                for k in range(start, end + 1):
                    covered.add(k)
        
        for i in range(hot_boundary, len(messages)):
            if i not in covered:
                hot_segments.append((i, i, 3, messages[i].token_estimate()))
                
        # Sort hot segments: drop errors first, then oldest
        hot_segments.sort(key=lambda s: (s[2], s[0]))
        
        for start, end, priority, seg_tokens in hot_segments:
            if tokens_freed >= tokens_needed:
                break
            # Never drop the absolute last message if it's the only one left
            if start == len(messages) - 1 and len(to_drop) == len(messages) - 1:
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
        self._last_usage = None  # TokenUsage from last LLM response (for hybrid estimation)
        self._message_count_at_usage: int = 0  # message count when _last_usage was recorded

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
        4. Current message history (token-budget limited, pi-style keepRecentTokens)

        Token budget: only includes recent messages that fit within the available
        context window. Older messages are silently excluded (their content is
        already captured in the global summary or will be at next compaction).
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

        # 4. Current stream — apply token budget (pi-style keepRecentTokens)
        # Calculate available budget: total context minus anchor tokens minus reserve
        anchor_tokens = sum(
            estimate_text_tokens(m.get("content", "") or "") + MESSAGE_OVERHEAD
            for m in messages
        )
        reserve_tokens = 4096  # Reserve for LLM output
        available_budget = self.config.max_context_tokens - anchor_tokens - reserve_tokens

        # Walk backward from most recent message, accumulating tokens
        # until we hit the budget. This ensures the most recent context
        # is always included (aligned with pi's keepRecentTokens).
        if self._messages:
            accumulated = 0
            cut_index = len(self._messages)  # Start with "include all"

            for i in range(len(self._messages) - 1, -1, -1):
                msg_tokens = self._messages[i].token_estimate()
                if accumulated + msg_tokens > available_budget:
                    cut_index = i + 1  # Exclude this message and everything before it
                    break
                accumulated += msg_tokens
            else:
                cut_index = 0  # All messages fit

            # Adjust cut_index to avoid splitting tool_call ↔ tool_result pairs
            while cut_index < len(self._messages) and self._messages[cut_index].is_tool_result:
                cut_index += 1  # Skip orphan tool_results

            # Include only messages from cut_index onward
            included = self._messages[cut_index:]

            # If we dropped messages, add a brief inline notice
            if cut_index > 0 and not self._global_summary:
                dropped_count = cut_index
                messages.append({
                    "role": "user",
                    "content": f"[{dropped_count} earlier messages omitted to fit context window]",
                })

            for msg in included:
                messages.append(msg.to_dict())
        
        return messages

    # --- Token Estimation ---

    def set_last_usage(self, usage) -> None:
        """Update with real usage data from LLM response (pi-style hybrid estimation)."""
        self._last_usage = usage
        self._message_count_at_usage = len(self._messages)

    def estimate_tokens(self) -> int:
        """Hybrid context token estimation (pi-style estimateContextTokens).

        Strategy:
        - If we have real usage data from the LLM: use usage.total as baseline,
          then estimate only the new messages added since that LLM call.
        - If no real usage: fall back to pure chars/4 estimation.
        """
        total = 0
        if self._pinned:
            total += self._pinned.token_estimate()
        if self._goal:
            total += estimate_text_tokens(self._goal) + MESSAGE_OVERHEAD
        if self._global_summary:
            total += estimate_text_tokens(self._global_summary) + MESSAGE_OVERHEAD

        # Hybrid: use real LLM usage when available
        if self._last_usage is not None and hasattr(self._last_usage, 'total'):
            usage_total = self._last_usage.total
            # Estimate only trailing messages added after the LLM response
            trailing_estimate = 0
            for msg in self._messages[self._message_count_at_usage:]:
                trailing_estimate += msg.token_estimate()
            # Real usage already includes pinned/summary tokens (system prompt),
            # so use it directly plus the trailing estimate
            return usage_total + trailing_estimate

        # Fallback: pure estimation
        for msg in self._messages:
            total += msg.token_estimate()
        return total

    def needs_compaction(self) -> bool:
        threshold = int(self.config.max_context_tokens * self.config.compress_threshold)
        return self.estimate_tokens() >= threshold

    # --- Token-Based Cut Point (pi-style) ---

    def _find_cut_point(self, keep_recent_tokens: int = 20000) -> int:
        """Find the cut point by walking backward, keeping ~keep_recent_tokens.

        Never cuts inside a tool_call -> tool_result pair.
        Returns the index of the first message to KEEP (everything before is summarized).
        (Aligned with pi-coding-agent's findCutPoint)
        """
        accumulated = 0
        cut_index = 0  # Default: summarize everything

        for i in range(len(self._messages) - 1, -1, -1):
            msg = self._messages[i]
            accumulated += msg.token_estimate()
            if accumulated >= keep_recent_tokens:
                cut_index = i
                # Walk forward to avoid cutting inside a tool turn:
                # if cut_index lands on a tool_result, include its preceding tool_call
                while cut_index < len(self._messages):
                    m = self._messages[cut_index]
                    if m.is_tool_result:
                        # Can't start with a tool result -- go back to include its tool_call
                        if cut_index > 0:
                            cut_index -= 1
                        else:
                            break
                    else:
                        break
                break

        return cut_index

    # --- Compaction (Archive & Reset) ---

    async def archive_and_reset(
        self,
        summarizer: Optional[Callable] = None,
    ) -> Optional[str]:
        """Compress history: LLM summarization (preferred) or deterministic fallback.

        Aligned with pi-coding-agent's compaction:
        1. Token-based cut point (keep ~20K recent tokens verbatim)
        2. Serialize old messages to text -> LLM summary
        3. Incremental update with <previous-summary>
        4. File operation tracking via XML tags
        5. Structured prompt (Goal, Progress, Decisions, Next Steps)

        Args:
            summarizer: async function(system_prompt: str, user_prompt: str) -> str
                       If None, uses deterministic extraction.

        Returns:
            The summary text, or None if nothing to compact.
        """
        if not self._messages:
            return None

        # 1. Find cut point (token-based, aligned with pi's keepRecentTokens)
        keep_tokens = min(20000, self.config.max_context_tokens // 4)
        cut_index = self._find_cut_point(keep_recent_tokens=keep_tokens)

        to_summarize = self._messages[:cut_index]
        to_keep = self._messages[cut_index:]

        anchor_tokens = 0
        if self._pinned:
            anchor_tokens += self._pinned.token_estimate()
        if self._goal:
            anchor_tokens += estimate_text_tokens(self._goal) + MESSAGE_OVERHEAD
        if self._global_summary:
            anchor_tokens += estimate_text_tokens(self._global_summary) + MESSAGE_OVERHEAD

        stream_budget = int(self.config.max_context_tokens * 0.7) - anchor_tokens
        keep_tokens_estimate = sum(m.token_estimate() for m in to_keep)

        if not to_summarize or keep_tokens_estimate > stream_budget:
            # Nothing to summarize or to_keep is still over budget due to massive tool turns.
            # Fall back to the old smart_drop approach on all messages.
            surviving, tombstone = _smart_drop(
                self._messages,
                target_tokens=max(stream_budget, 0),
                keep_recent_tokens=self.config.keep_recent_tokens,
            )

            summary_parts: List[str] = []
            if self._global_summary:
                summary_parts.append(self._global_summary)
            if tombstone:
                summary_parts.append(tombstone)

            new_summary = "\n\n".join(summary_parts) if summary_parts else "(history compacted)"

            max_summary_chars = self.config.summary_max_tokens * 4
            if len(new_summary) > max_summary_chars:
                new_summary = "..." + new_summary[-(max_summary_chars - 3):]

            self._global_summary = new_summary
            self._messages = surviving
            return new_summary

        # 2. Extract file operations from messages being summarized
        read_files, modified_files = _extract_file_ops(to_summarize)

        # 3. Generate summary (LLM or deterministic)
        if summarizer:
            # LLM-powered summarization (pi-style)
            serialized = _serialize_messages(to_summarize)
            user_prompt = f"<conversation>\n{serialized}\n</conversation>\n\n"
            if self._global_summary:
                user_prompt += f"<previous-summary>\n{self._global_summary}\n</previous-summary>\n\n"
                user_prompt += UPDATE_SUMMARIZATION_PROMPT
            else:
                user_prompt += SUMMARIZATION_PROMPT

            try:
                new_summary = await summarizer(SUMMARIZATION_SYSTEM_PROMPT, user_prompt)
            except Exception as e:
                logger.warning("LLM summarization failed (%s), falling back to deterministic", e)
                new_summary = self._deterministic_summary(to_summarize)
        else:
            # Deterministic fallback
            new_summary = self._deterministic_summary(to_summarize)

        # 4. Append file operations
        new_summary += _format_file_ops(read_files, modified_files)

        # 5. Trim if too long
        max_chars = self.config.summary_max_tokens * 4
        if len(new_summary) > max_chars:
            new_summary = "..." + new_summary[-(max_chars - 3):]

        # 6. Update state
        self._global_summary = new_summary
        self._messages = to_keep

        logger.info(
            "Compaction: summarized %d messages, kept %d, summary %d chars",
            len(to_summarize), len(to_keep), len(new_summary),
        )

        return new_summary

    def _deterministic_summary(self, messages: List["Message"]) -> str:
        """Fallback: create tombstone summary without LLM."""
        tombstone = _make_tombstone(messages)
        parts: List[str] = []
        if self._global_summary:
            parts.append(self._global_summary)
        if tombstone:
            parts.append(tombstone)
        return "\n\n".join(parts) if parts else "(history compacted)"

    def clear(self) -> None:
        self._messages.clear()
        self._global_summary = ""
        self._goal = ""
        self._last_usage = None
        self._message_count_at_usage = 0
