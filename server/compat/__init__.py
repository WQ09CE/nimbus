"""OpenCode Compatibility Layer.

This module provides API routes compatible with OpenCode API format,
allowing OpenWork to connect directly to Nimbus Server.
"""

from .opencode import router as opencode_router

__all__ = ["opencode_router"]
