"""
Nimbus Adapters - 适配外部系统

目前支持:
- Pi Adapter: 使用 pi-ai 和 pi-tui
"""

from .pi_adapter import (
    PiLLMAdapter,
    PiLLMConfig,
    PiIOAdapter,
    create_pi_adapters,
)

__all__ = [
    "PiLLMAdapter",
    "PiLLMConfig",
    "PiIOAdapter",
    "create_pi_adapters",
]
