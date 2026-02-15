"""
Nimbus Adapters - LLM integration adapters

Currently supported:
- DirectAdapter: Direct API calls via LiteLLM (primary)
- MockAdapter: For testing
"""

from .types import LLMConfig, LLMStreamEvent, VcpuLLMResponse
from .direct_adapter import DirectAdapter

__all__ = [
    "LLMConfig",
    "VcpuLLMResponse",
    "LLMStreamEvent",
    "DirectAdapter",
]
