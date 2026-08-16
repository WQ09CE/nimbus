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

Module layout: message primitives live in messages.py, compaction strategies
in compaction.py; this module owns the MMU orchestrator. Historical import
paths (`from nimbus.core.mmu import Message, _smart_drop, ...`) keep working
via the re-exports below.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .compaction import (  # noqa: F401  (re-exported for compat)
    SUMMARIZATION_PROMPT,
    SUMMARIZATION_SYSTEM_PROMPT,
    UPDATE_SUMMARIZATION_PROMPT,
    _extract_file_ops,
    _find_turn_boundaries,
    _format_file_ops,
    _make_tombstone,
    _serialize_messages,
    _smart_drop,
)
from .messages import (  # noqa: F401  (re-exported for compat)
    MESSAGE_OVERHEAD,
    Message,
    PinnedContext,
    estimate_text_tokens,
)

logger = logging.getLogger("nimbus.mmu")


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
        self._plan: str = ""  # agent-authored plan anchor (update_plan tool)
        self._last_usage = None  # TokenUsage from last LLM response (for hybrid estimation)
        self._message_count_at_usage: int = 0  # message count when _last_usage was recorded
        # Optional observer for live message mutations (Phase 0 dual-write:
        # RuntimeLoop points this at its SessionLog). Restore paths that write
        # _messages directly bypass it BY DESIGN — rehydration must not re-log.
        self.event_sink: Optional[Callable[[str, Dict[str, Any]], None]] = None

    def _notify(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.event_sink is not None:
            try:
                self.event_sink(event_type, data)
            except Exception:
                logger.exception("MMU event_sink failed (trace only; ignoring)")

    # --- Pinned Context (Anchor) ---

    def set_pinned(self, pinned: PinnedContext) -> None:
        self._pinned = pinned

    def set_goal(self, goal: str) -> None:
        """Pin the user's original goal (resists recency bias)."""
        self._goal = goal

    @property
    def goal(self) -> str:
        return self._goal

    def set_plan(self, plan: str) -> None:
        """Pin the agent-authored task plan (update_plan tool).

        Anchor state, not stream state: always assembled near the top of the
        context, immune to compaction, restated on every step. Each update is
        logged as a plan/updated event so the trace shows plan evolution and
        restore paths can recover it."""
        self._plan = plan
        self._notify("plan/updated", {"plan": plan})

    @property
    def plan(self) -> str:
        return self._plan

    # --- Message Management (Stream) ---

    def add_user_message(self, content: Any) -> None:
        msg = Message(role="user", content=content)
        self._messages.append(msg)
        self._notify("user/message", {"message": msg.to_dict()})

    def add_assistant_message(self, content: str) -> None:
        msg = Message(role="assistant", content=content)
        self._messages.append(msg)
        self._notify("assistant/message", {"message": msg.to_dict()})

    def add_assistant_with_tool_calls(self, content: Optional[str], tool_calls: List[Dict]) -> None:
        msg = Message(role="assistant", content=content, tool_calls=tool_calls)
        self._messages.append(msg)
        self._notify("assistant/message", {"message": msg.to_dict()})

    def add_tool_result(
        self, tool_call_id: str, name: str, content: str,
        ui_detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        meta = {"ui_detail": ui_detail} if ui_detail else {}
        msg = Message(
            role="tool", content=content, name=name, tool_call_id=tool_call_id,
            meta=meta,
        )
        self._messages.append(msg)
        self._notify("tool/result", {"message": msg.to_dict()})

    def add_system_message(self, content: str) -> None:
        """Inject a transient system message (e.g., compaction notice, guard
        nudge). Sent as a user-role message because most providers reject a
        mid-conversation system role. meta.internal marks it as framework
        steering so UIs can hide it from the chat transcript (it stays fully
        visible in the session log / trace view)."""
        msg = Message(
            role="user", content=f"[System] {content}", meta={"internal": True},
        )
        self._messages.append(msg)
        self._notify("user/message", {"message": msg.to_dict()})

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
        # Output reserve scales with the window (see _output_reserve). The
        # backward walk below is the LAST-RESORT hard cap only: the runtime runs
        # compaction (needs_compaction) BEFORE assembly, so in the normal path the
        # full backlog already fits and nothing is dropped here.
        available_budget = self.usable_input_budget() - anchor_tokens

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

            # Emergency drop happened (compaction should normally prevent this).
            # ALWAYS leave an explicit notice — never silently lose history — so
            # the degrade is visible to both the model and the logs.
            if cut_index > 0:
                dropped_count = cut_index
                logger.warning(
                    "assemble_context hard-cap: dropped %d message(s) that did not "
                    "fit the usable budget (%d tok) even after compaction",
                    dropped_count, self.usable_input_budget(),
                )
                messages.append({
                    "role": "user",
                    "content": (
                        f"[⚠ {dropped_count} earlier message(s) dropped: exceeded the "
                        f"context budget even after compaction. See the summary above "
                        f"for their content.]"
                    ),
                })

            for msg in included:
                messages.append(msg.to_dict(include_meta=False))  # LLM API: no meta

        # 5. Agent-authored plan anchor (update_plan) — injected at the TAIL:
        # recitation puts the plan at the model's most-attended position
        # (recency), and a changing plan no longer invalidates the prompt
        # cache for the entire message stream (measured 12.4% hit rate with
        # the plan in the prefix). Still anchor state: survives compaction.
        if self._plan:
            messages.append({
                "role": "user",
                "content": f"### 📋 CURRENT PLAN (yours — keep it updated)\n{self._plan}\n\n---\n",
            })

        return messages

    # --- Token Estimation ---

    def set_last_usage(self, usage) -> None:
        """Record real usage from the last LLM response. TELEMETRY ONLY — this
        MUST NOT drive compaction. usage.total reflects the already-assembled
        (possibly truncated) payload; using it as the compaction trigger created a
        feedback loop of amnesia: a truncated send under-reported the backlog, so
        needs_compaction() never fired and history grew unsent forever.
        needs_compaction()/estimate_tokens() now read the full backlog instead."""
        self._last_usage = usage
        self._message_count_at_usage = len(self._messages)

    def _output_reserve(self) -> int:
        """Tokens reserved for the model's output. Scales with the window (20%,
        capped at 4096) so a small configured cap is not entirely consumed by a
        fixed reserve (a fixed 4096 against a 2500 window yielded a negative input
        budget → total drop). No lower floor: for tiny windows the reserve must
        stay below the window or the usable budget collapses to zero."""
        return min(4096, int(self.config.max_context_tokens * 0.2))

    def usable_input_budget(self) -> int:
        """Window minus the output reserve — the budget available for prompt tokens."""
        return max(0, self.config.max_context_tokens - self._output_reserve())

    def estimate_tokens(self) -> int:
        """Estimate the FULL prompt tokens: anchor + goal + summary + the entire
        message backlog. Always reflects the true backlog (never the last,
        possibly-truncated, send) so compaction triggers correctly."""
        total = 0
        if self._pinned:
            total += self._pinned.token_estimate()
        if self._goal:
            total += estimate_text_tokens(self._goal) + MESSAGE_OVERHEAD
        if self._plan:
            total += estimate_text_tokens(self._plan) + MESSAGE_OVERHEAD
        if self._global_summary:
            total += estimate_text_tokens(self._global_summary) + MESSAGE_OVERHEAD
        for msg in self._messages:
            total += msg.token_estimate()
        return total

    def needs_compaction(self) -> bool:
        # Compact against the usable INPUT budget (window minus output reserve),
        # not the raw window — otherwise we only trigger once the prompt already
        # eats into the reserved output space.
        threshold = int(self.usable_input_budget() * self.config.compress_threshold)
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
        if self._plan:
            anchor_tokens += estimate_text_tokens(self._plan) + MESSAGE_OVERHEAD
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

            max_summary_chars = self._summary_char_budget()
            if len(new_summary) > max_summary_chars:
                new_summary = "..." + new_summary[-(max_summary_chars - 3):]

            # kept_indices: survivors' positions in the PRE-compaction surface
            # (smart-drop survivors are non-contiguous). Lets the event log
            # replay compaction as a deterministic surface replace.
            surviving_ids = {id(m) for m in surviving}
            kept_indices = [i for i, m in enumerate(self._messages) if id(m) in surviving_ids]
            self._global_summary = new_summary
            self._messages = surviving
            self._notify("compaction/applied", {
                "mode": "smart-drop", "kept": len(surviving),
                "kept_indices": kept_indices,
                "summary": new_summary,
            })
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

        # 5. Trim if too long (budget scales with the window)
        max_chars = self._summary_char_budget()
        if len(new_summary) > max_chars:
            new_summary = "..." + new_summary[-(max_chars - 3):]

        # 6. Update state
        self._global_summary = new_summary
        self._messages = to_keep
        self._notify("compaction/applied", {
            "mode": "summarize", "kept": len(to_keep),
            "kept_indices": list(range(cut_index, cut_index + len(to_keep))),
            "summary": new_summary,
        })

        logger.info(
            "Compaction: summarized %d messages, kept %d, summary %d chars",
            len(to_summarize), len(to_keep), len(new_summary),
        )

        return new_summary

    def _summary_char_budget(self) -> int:
        """Max chars for the merged summary. Scales with the window so the summary
        itself can't dwarf a small configured cap (a fixed 2000-token summary alone
        overflows a 2500-token window). ~4 chars/token."""
        budget_tokens = min(
            self.config.summary_max_tokens,
            max(256, int(self.usable_input_budget() * 0.25)),
        )
        return budget_tokens * 4

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
        self._plan = ""
        self._last_usage = None
        self._message_count_at_usage = 0
