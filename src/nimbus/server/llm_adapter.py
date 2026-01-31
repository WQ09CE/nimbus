"""LLM Adapter - Bridge v1 LLM clients to v2 LLMClient protocol.

This adapter wraps v1 LLM clients to make them compatible with v2's VCPU.
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class AdaptedLLMResponse:
    """Response from adapted LLM client."""
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    usage: Optional[Dict[str, int]] = None


class V1ToV2LLMAdapter:
    """
    Adapter that wraps v1 LLM clients to implement v2 LLMClient protocol.
    
    v1 clients have methods like:
    - generate(messages, tools=None) -> str
    - stream(messages, tools=None) -> AsyncGenerator[str, None]
    
    v2 VCPU expects:
    - async chat(messages, tools=None) -> LLMResponse
      where LLMResponse has .content and .tool_calls
    """
    
    def __init__(self, v1_client):
        """
        Args:
            v1_client: A v1 LLM client (GeminiClient, etc.)
        """
        self._client = v1_client
    
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> AdaptedLLMResponse:
        """
        Chat method required by v2 LLMClient protocol.
        
        Args:
            messages: List of message dicts (OpenAI format)
            tools: Optional list of tool definitions
        
        Returns:
            AdaptedLLMResponse with content and/or tool_calls
        """
        # Try different v1 client methods in order of preference
        
        # 1. Try complete_with_tools (Gemini, best for tool support)
        if hasattr(self._client, 'complete_with_tools'):
            # FIX: Pass messages as keyword argument!
            response = await self._client.complete_with_tools(messages=messages, tools=tools)
            
            # complete_with_tools returns CompletionResponse
            if hasattr(response, 'content'):
                # Convert v1 ToolCall objects to dicts for v2
                tool_calls = getattr(response, 'tool_calls', None)
                if tool_calls:
                    tool_calls = [
                        {
                            "id": tc.id if hasattr(tc, 'id') else f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": tc.name if hasattr(tc, 'name') else tc.get('name', ''),
                                "arguments": tc.arguments if hasattr(tc, 'arguments') else tc.get('arguments', {}),
                            }
                        }
                        if not isinstance(tc, dict) else tc
                        for i, tc in enumerate(tool_calls)
                    ]
                
                return AdaptedLLMResponse(
                    content=response.content,
                    tool_calls=tool_calls,
                    usage=getattr(response, 'usage', None),
                )
            elif isinstance(response, dict):
                return AdaptedLLMResponse(
                    content=response.get('content'),
                    tool_calls=response.get('tool_calls'),
                    usage=response.get('usage'),
                )
        
        # 2. Try generate() (Ollama)
        elif hasattr(self._client, 'generate'):
            response = await self._client.generate(messages, tools=tools)
            
            # Handle different response formats
            if isinstance(response, str):
                # Plain text response
                return AdaptedLLMResponse(content=response)
            elif isinstance(response, dict):
                # Structured response
                return AdaptedLLMResponse(
                    content=response.get('content'),
                    tool_calls=response.get('tool_calls'),
                    usage=response.get('usage'),
                )
            else:
                # Unknown format
                return AdaptedLLMResponse(content=str(response))
        
        # 3. Try complete() (fallback, no tools)
        elif hasattr(self._client, 'complete'):
            # Extract last user message
            prompt = messages[-1].get('content', '') if messages else ''
            response = await self._client.complete(prompt, history=messages[:-1] if len(messages) > 1 else None)
            
            if isinstance(response, str):
                return AdaptedLLMResponse(content=response)
            else:
                return AdaptedLLMResponse(content=str(response))
        
        # Fallback: no compatible method
        raise NotImplementedError(
            f"v1 client {type(self._client).__name__} doesn't have generate(), complete_with_tools(), or complete() method"
        )
