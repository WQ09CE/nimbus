"""
Nimbus Adapters - 适配外部系统

目前支持:
- Pi Adapter: 使用 pi-ai HTTP 服务
"""

from .pi_adapter import (
    PiLLMAdapter,
    PiLLMConfig,
    VcpuLLMResponse,
    LLMStreamEvent,
    create_pi_adapter,
)

__all__ = [
    "PiLLMAdapter",
    "PiLLMConfig",
    "VcpuLLMResponse",
    "LLMStreamEvent",
    "create_pi_adapter",
]
