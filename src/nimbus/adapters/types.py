"""
Nimbus LLM Adapter Types

Shared types used by all LLM adapters (DirectAdapter, MockAdapter, etc.)

This module is the single source of truth for all adapter-related types:
- LLMConfig: adapter configuration
- VcpuLLMResponse: normalized LLM response for the VCPU
- LLMStreamEvent: streaming event from LLM
- TokenUsage: structured token usage (pi-coding-agent style)
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# =============================================================================
# Token Usage (structured, aligned with pi-coding-agent)
# =============================================================================


@dataclass
class TokenUsage:
    """Structured token usage aligned with pi-coding-agent's Usage interface.

    Fields:
        input: prompt/input tokens
        output: completion/output tokens
        cache_read: tokens read from cache (Anthropic cache_read_input_tokens / OpenAI cached_tokens)
        cache_write: tokens written to cache (Anthropic cache_creation_input_tokens)
        total: total tokens (input + output + cache_read + cache_write if not provided natively)
        cost_*: computed cost breakdown in USD
    """
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: float = 0.0
    cost_cache_write: float = 0.0
    cost_total: float = 0.0

    def __post_init__(self):
        if self.total == 0:
            self.total = self.input + self.output + self.cache_read + self.cache_write

    def compute_cost(self, cost_per_million: Optional[Dict[str, float]] = None):
        """Compute cost from per-million-token pricing dict.

        Expected keys: 'input', 'output', 'cache_read', 'cache_write'
        """
        if not cost_per_million:
            return
        rate = lambda k: cost_per_million.get(k, 0.0) / 1_000_000
        self.cost_input = self.input * rate("input")
        self.cost_output = self.output * rate("output")
        self.cost_cache_read = self.cache_read * rate("cache_read")
        self.cost_cache_write = self.cache_write * rate("cache_write")
        self.cost_total = self.cost_input + self.cost_output + self.cost_cache_read + self.cost_cache_write

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input": self.input, "output": self.output,
            "cache_read": self.cache_read, "cache_write": self.cache_write,
            "total": self.total,
            "cost": {
                "input": round(self.cost_input, 6),
                "output": round(self.cost_output, 6),
                "cache_read": round(self.cost_cache_read, 6),
                "cache_write": round(self.cost_cache_write, 6),
                "total": round(self.cost_total, 6),
            },
        }

    def __iadd__(self, other: "TokenUsage") -> "TokenUsage":
        self.input += other.input
        self.output += other.output
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        # Recompute total from sub-fields to avoid drift when API total_tokens
        # doesn't match input+output+cache_read+cache_write
        self.total = self.input + self.output + self.cache_read + self.cache_write
        self.cost_input += other.cost_input
        self.cost_output += other.cost_output
        self.cost_cache_read += other.cost_cache_read
        self.cost_cache_write += other.cost_cache_write
        self.cost_total += other.cost_total
        return self


# =============================================================================
# LLM Configuration
# =============================================================================

@dataclass
class LLMConfig:
    """Configuration for LLM client.

    Can specify the model in two ways:
    1. model: "anthropic/claude-sonnet-4-20250514" (full name)
    2. provider + model_id: separate fields (auto-merged via get_model())
    """

    base_url: str = ""
    model: str = ""  # Full model name (provider/model_id)
    provider: str = ""
    model_id: str = ""
    max_tokens: int = 8192
    timeout: float = 120.0
    temperature: Optional[float] = None
    thinking: Optional[bool] = None
    stop: Optional[List[str]] = None

    def get_model(self) -> str:
        """Get full model name (provider/model_id)."""
        if self.model:
            return self.model
        return f"{self.provider}/{self.model_id}"


class VcpuLLMResponse:
    """Response from LLM (tool calls + content + usage).

    Used by vCPU to process LLM outputs uniformly across adapters.
    """

    def __init__(self, content: str | None = None, tool_calls: list | None = None, usage: Any = None):
        self._content = content
        self._tool_calls = tool_calls
        self._usage = usage  # TokenUsage from streaming usage events

    @property
    def content(self) -> str | None:
        return self._content

    @content.setter
    def content(self, value: str | None) -> None:
        self._content = value

    @property
    def tool_calls(self) -> list | None:
        return self._tool_calls

    @tool_calls.setter
    def tool_calls(self, value: list | None) -> None:
        self._tool_calls = value

    @property
    def usage(self) -> Any:
        return self._usage

    @usage.setter
    def usage(self, value: Any) -> None:
        self._usage = value


@dataclass
class LLMStreamEvent:
    """Streaming event from LLM.

    Event types:
    - "text": Text content delta (text field)
    - "tool_call": Tool call received (tool_call field)
    - "thinking": Extended thinking event (keepalive)
    - "usage": Token usage info (usage field)
    - "stop": Stream completed (reason field)
    - "error": Error occurred (error field)
    """

    type: str  # "text" | "tool_call" | "thinking" | "usage" | "stop" | "error"
    text: str = ""
    tool_call: dict | None = None
    usage: dict | None = None
    reason: str = ""
    error: str = ""
