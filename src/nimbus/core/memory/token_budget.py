import logging
from typing import Any, Dict, List, Optional
from nimbus.core.memory.context import IMAGE_TOKEN_ESTIMATE

logger = logging.getLogger(__name__)

def _approx_tokens(text: str) -> int:
    """Very rough approximation: 1 token ≈ 4 chars."""
    if not text:
        return 0
    return len(text) // 4

def approximate_message_tokens(msg: Dict[str, Any]) -> int:
    """Calculate approximate tokens for a single message dictionary."""
    if not msg:
        return 0
        
    tokens = 0
    content = msg.get("content", "")
    
    if isinstance(content, str):
        tokens += _approx_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    tokens += _approx_tokens(part.get("text", ""))
                elif part.get("type") == "image_url":     # OpenAI format
                    tokens += IMAGE_TOKEN_ESTIMATE
                elif part.get("type") == "image":         # Anthropic format
                    tokens += IMAGE_TOKEN_ESTIMATE
    
    # Add tokens for tools/function calls
    if msg.get("tool_calls"):
        for tc in msg.get("tool_calls"):
            if isinstance(tc, dict):
                func = tc.get("function", {})
                tokens += _approx_tokens(func.get("name", ""))
                tokens += _approx_tokens(str(func.get("arguments", "")))
            else:
                func = getattr(tc, "function", None)
                if func:
                    tokens += _approx_tokens(getattr(func, "name", ""))
                    tokens += _approx_tokens(str(getattr(func, "arguments", "")))
                    
    return tokens

def estimate_total_tokens(pinned_tokens: int, messages: List[Dict[str, Any]]) -> int:
    """Estimate total tokens across pinned context and all messages."""
    stream_tokens = sum(approximate_message_tokens(m) for m in messages)
    return pinned_tokens + stream_tokens

def drop_oldest_non_essential(
    messages: List[Dict[str, Any]], 
    hot_count: int, 
    auto_detect_failures: bool = True
) -> bool:
    """
    Attempt to drop 1 message from the 'history' segment of the context.
    Returns True if a message was dropped, False if nothing can be dropped.

    Drop strategy (only applies to history, never hot context):
    1. Failed/Discarded Tool Calls (if auto_detect_failures is True)
    2. Oldest generic message (Assistant thought or unpinned User chat)
    """
    total = len(messages)
    if total <= hot_count:
        return False  # Everything is hot, cannot drop

    history_end = total - hot_count

    # 1. Try to find a disposable tool interaction (tool call + tool result pair)
    if auto_detect_failures:
        for i in range(history_end):
            msg = messages[i]
            # Look for tool results that indicate failure or were marked discardable
            if msg.get("role") == "tool":
                content = str(msg.get("content", "")).lower()
                is_error = "error" in content or "failed" in content or "exception" in content
                if is_error or msg.get("metadata", {}).get("discardable", False):
                    # We can drop this tool result.
                    # We should ideally also drop the assistant message that spawned it,
                    # but for safety in the unified stream, dropping just the big error output helps a lot.
                    del messages[i]
                    return True

    # 2. Try to drop oldest generic message (Assistant thought, or old User message)
    for i in range(history_end):
        msg = messages[i]
        role = msg.get("role")
        # Don't drop system or pure tool results here unconditionally without their parent
        if role in ("assistant", "user"):
            del messages[i]
            return True

    return False
