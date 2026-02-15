"""
Nimbus LLM Adapter Types

Shared types used by all LLM adapters (DirectAdapter, MockAdapter, etc.)
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


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
    """Response from LLM (tool calls + content).

    Used by vCPU to process LLM outputs uniformly across adapters.
    """

    def __init__(self, content: str | None = None, tool_calls: list | None = None):
        self._content = content
        self._tool_calls = tool_calls

    @property
    def content(self) -> str | None:
        return self._content

    @property
    def tool_calls(self) -> list | None:
        return self._tool_calls


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
