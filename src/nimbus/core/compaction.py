"""Compaction strategies — summarization prompts, serialization, and drop policies.

Everything the MMU uses to shrink history lives here: the pi-aligned
summarization prompts, message serialization + file-operation tracking,
tool-use turn detection, tombstone stubs, and the smart-drop fallback.
The MMU orchestrates (decides *when* and applies the result); this module
owns the strategies (*how*).
"""

import json
import logging
from typing import List

from .messages import Message

# Keep the historical logger channel: compaction logs have always appeared
# under nimbus.mmu and downstream log filters rely on it.
logger = logging.getLogger("nimbus.mmu")


# =============================================================================
# Summarization Prompts (aligned with pi-coding-agent)
# =============================================================================

SUMMARIZATION_SYSTEM_PROMPT = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI coding assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[Durable objective from CURRENT GOAL, plus any explicit goal evolution. Do not replace it with recent side requests.]

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
- PRESERVE the durable goal from CURRENT GOAL; record goal evolution separately from progress
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
