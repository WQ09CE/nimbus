"""
Nimbus v2 LLM Clients

This module provides LLM client implementations that conform to the v2 LLMClient Protocol.
These clients are used by the vCPU (Virtual CPU) as the ALU (Arithmetic Logic Unit).

Available Clients:
- GeminiV2Client: Google Gemini API client for v2 architecture

Usage:
    from nimbus.v2.llm import GeminiV2Client

    client = GeminiV2Client(api_key="your-api-key")
    response = await client.chat(messages, tools=tool_definitions)
"""

from nimbus.v2.llm.gemini import GeminiV2Client, GeminiV2Response

__all__ = [
    "GeminiV2Client",
    "GeminiV2Response",
]
