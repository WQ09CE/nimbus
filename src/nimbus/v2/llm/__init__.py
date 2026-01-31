"""
Nimbus v2 LLM Clients

This module provides LLM client implementations that conform to the v2 LLMClient Protocol.
These clients are used by the vCPU (Virtual CPU) as the ALU (Arithmetic Logic Unit).

Available Clients:
- AnthropicV2Client: Anthropic Claude API client for v2 architecture
- GeminiV2Client: Google Gemini API client for v2 architecture
- OpenRouterV2Client: OpenRouter API client (access to multiple models)

Usage:
    from nimbus.v2.llm import AnthropicV2Client, GeminiV2Client, OpenRouterV2Client

    # Anthropic (default)
    client = AnthropicV2Client(api_key="your-api-key")
    response = await client.chat(messages, tools=tool_definitions)

    # Gemini
    client = GeminiV2Client(api_key="your-api-key")
    response = await client.chat(messages, tools=tool_definitions)

    # OpenRouter (access Claude, GPT, etc.)
    client = OpenRouterV2Client(api_key="your-api-key", model="anthropic/claude-opus-4")
    response = await client.chat(messages, tools=tool_definitions)
"""

from nimbus.v2.llm.anthropic import AnthropicV2Client, AnthropicV2Response
from nimbus.v2.llm.gemini import GeminiV2Client, GeminiV2Response
from nimbus.v2.llm.openrouter import OpenRouterV2Client, OpenRouterV2Response

__all__ = [
    "AnthropicV2Client",
    "AnthropicV2Response",
    "GeminiV2Client",
    "GeminiV2Response",
    "OpenRouterV2Client",
    "OpenRouterV2Response",
]
