"""Message primitives — the vocabulary shared by MMU, compaction, and loop.

Holds the Message dataclass (OpenAI/Anthropic-compatible), the PinnedContext
anchor, and CJK-aware token estimation. Pure data + estimation: no context
management or compression logic lives here (that stays in mmu.py /
compaction.py).
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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

    def to_dict(self, include_meta: bool = True) -> Dict[str, Any]:
        """Serialize message to dict.

        Args:
            include_meta: If True, include meta (ui_detail etc.) for persistence.
                         If False, omit meta for LLM API calls (providers reject unknown fields).
        """
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        if include_meta and self.meta:
            d["meta"] = self.meta
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
    skill_instructions: str = ""

    def to_system_message(self) -> Message:
        parts = []
        if self.system_rules:
            parts.append(f"# System Rules\n{self.system_rules}")
        if self.workspace_info:
            parts.append(f"# Workspace\n{self.workspace_info}")
        if self.user_memory:
            parts.append(f"# User Memory\n{self.user_memory}")
        if self.skill_instructions:
            parts.append(self.skill_instructions)
        return Message(role="system", content="\n\n".join(parts))

    def token_estimate(self) -> int:
        return (
            estimate_text_tokens(self.system_rules)
            + estimate_text_tokens(self.workspace_info)
            + estimate_text_tokens(self.user_memory)
            + estimate_text_tokens(self.skill_instructions)
        )
