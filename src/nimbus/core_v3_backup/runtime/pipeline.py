"""
Response Processing Pipeline.

This module decouples model-specific "patches" from the core VCPU logic.
It implements a pipeline of middleware that can inspect, modify, or block
LLM responses before they reach the execution engine.
"""

import json
import logging
import re
from typing import Any, List, Optional, Protocol

from nimbus.core.models.manifest import ModelFeatures
from nimbus.core.protocol import ActionIR

logger = logging.getLogger("kernel.vcpu.pipeline")


class LLMResponse(Protocol):
    """Protocol for LLM response objects (same as VCPU definition)."""
    @property
    def content(self) -> Optional[str]: ...
    @content.setter
    def content(self, value: Optional[str]): ...

    @property
    def tool_calls(self) -> Optional[List[Any]]: ...


class ResponseMiddleware(Protocol):
    """Interface for a pipeline stage."""

    def reset(self) -> None:
        """Reset state for a new turn."""
        ...

    def process_chunk(self, chunk: str) -> Optional[str]:
        """
        Process a stream chunk. 
        Return None to suppress the chunk (and potentially future chunks).
        Return modified chunk or original chunk to pass through.
        """
        ...

    def process_response(self, response: LLMResponse, decoder: Any) -> Optional[List[ActionIR]]:
        """
        Process the final response.
        Return List[ActionIR] if this middleware produces actions (e.g. splitting).
        Return None if it just modifies response (e.g. sanitizing) or passes through.
        """
        ...


class ResponsePipeline:
    """
    Orchestrates a sequence of middleware.
    """
    def __init__(self, features: ModelFeatures, text_is_final: bool = True, role: str = ""):
        self.features = features
        self.text_is_final = text_is_final
        self.role = role
        self.middleware: List[ResponseMiddleware] = []

        # 1. First sanitize (modify response content)
        if self.features.firewall_hallucinations:
            self.middleware.append(HallucinationSanitizer(self.features.hallucination_patterns))

        # 2. Then split (produce actions from modified response)
        if self.features.split_mixed_responses:
            self.middleware.append(MixedResponseSplitter())

    def reset(self):
        """Reset all middleware for a new turn."""
        for mw in self.middleware:
            mw.reset()

    def process_chunk(self, chunk: str) -> Optional[str]:
        """Run chunk through all middleware."""
        current_chunk = chunk
        for mw in self.middleware:
            current_chunk = mw.process_chunk(current_chunk)
            if current_chunk is None:
                return None
        return current_chunk

    def process_response(self, response: LLMResponse, decoder: Any) -> List[ActionIR]:
        """
        Run response through middleware chain.
        If a middleware returns actions, we stop and return those.
        If none return actions, we fall back to standard decoding.
        """
        actions = []

        for mw in self.middleware:
            # Middleware can modify response in-place or return actions
            result = mw.process_response(response, decoder)

            if result is not None:
                # Middleware handled the response completely (e.g. splitting)
                return result

        # Default behavior: Standard Decode
        # (Only if no middleware produced actions)
        try:
            actions = decoder.decode(
                content=response.content,
                tool_calls=response.tool_calls,
                text_is_final=self.text_is_final,
                role=self.role,
                model_features=self.features,
            )
        except Exception as e:
            # If decoding fails, we catch it here or let it propagate?
            # VCPU expects exceptions to propagate or be handled.
            raise e

        return actions


class HallucinationSanitizer:
    """
    Blocks or sanitizes output containing hallucinated tool patterns.
    """
    def __init__(self, patterns: List[str]):
        self.patterns = patterns
        self.reset()

    def reset(self):
        self.buffer = ""
        self.suppressed = False

    def process_chunk(self, chunk: str) -> Optional[str]:
        self.buffer += chunk

        if not self.suppressed:
            for pattern in self.patterns:
                if pattern in self.buffer:
                    self.suppressed = True
                    logger.warning(
                        f"🛡️ Hallucination firewall (stream): Suppressing output containing '{pattern}'"
                    )
                    return None

        if self.suppressed:
            return None

        return chunk

    def process_response(self, response: LLMResponse, decoder: Any) -> Optional[List[ActionIR]]:
        """
        Sanitize final response content.
        If content contains hallucination, strip it or block it.
        """
        if response.content:
            # 1. Special handling: Strip "Historical context" noise (don't block everything)
            if "[Historical context:" in response.content:
                logger.warning("🛡️ Hallucination firewall: Stripping [Historical context] noise.")
                import re
                # Remove the entire bracketed block
                cleaned = re.sub(r"\[Historical context:.*?\]", "", response.content, flags=re.DOTALL).strip()
                try:
                    response.content = cleaned if cleaned else None
                except AttributeError:
                    pass

        if response.content and not self.suppressed:
            # 2. Check for blocking patterns in the full content
            for pattern in self.patterns:
                # Skip if already handled or not present
                if pattern.startswith("[Historical context"): continue

                if pattern in response.content:
                    logger.warning(
                        f"🛡️ Hallucination firewall (final): Stripping content with '{pattern}'"
                    )
                    # Strip content effectively
                    try:
                        response.content = None
                    except AttributeError:
                        pass
                    return None # Continue to next middleware

        return None # Continue to next middleware




class MixedResponseSplitter:
    """
    Splits responses with both Content and ToolCalls into THOUGHT + TOOL_CALLS.
    """
    def reset(self):
        pass

    def process_chunk(self, chunk: str) -> Optional[str]:
        # Pass through
        return chunk

    def process_response(self, response: LLMResponse, decoder: Any) -> Optional[List[ActionIR]]:
        """
        If response has both content and tools, split them.
        """
        if response.content and response.content.strip() and response.tool_calls:
            logger.info(
                "🔀 Detected mixed response (content + tool_calls). Splitting into thought → action."
            )

            actions = []

            # 1. Thought Action
            thought_action = ActionIR(
                kind="THOUGHT",
                name="thought",
                args={"text": response.content.strip()},
                meta={"non_blocking": True}
            )
            actions.append(thought_action)

            # 2. Tool Actions (Standard Decode of just tools)
            # We explicitly pass content=None to decoder to get only tools
            tool_actions = decoder.decode(content=None, tool_calls=response.tool_calls)
            actions.extend(tool_actions)

            return actions

        return None # Not handled, fall back to default
