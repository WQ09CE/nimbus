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
    def __init__(self, features: ModelFeatures, role: str = ""):
        self.features = features
        self.role = role
        self.middleware: List[ResponseMiddleware] = []

        # 1. First sanitize (modify response content)
        if self.features.firewall_hallucinations:
            self.middleware.append(HallucinationSanitizer(self.features.hallucination_patterns))

        # 2. Extract JSON tool calls from content (for models like qwen3.5 via ollama)
        if self.features.json_tool_call_extraction:
            self.middleware.append(JsonToolCallExtractor())

        # 3. Then split (produce actions from modified response)
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


class JsonToolCallExtractor:
    """
    Extracts tool calls from JSON embedded in content field.

    Some local models (e.g. qwen3.5 via ollama) understand tool calling
    semantics but output tool calls as JSON text in the content field
    instead of using structured tool_calls.

    Supported formats:
    1. Single tool call: {"name": "func", "arguments": {...}}
    2. Array of tool calls: [{"name": "func1", "arguments": {...}}, ...]
    3. Content with trailing/leading whitespace or markdown code fences
    """

    def __init__(self, available_tools: list[str] | None = None):
        self._available_tools = set(available_tools) if available_tools else None

    def reset(self):
        pass

    def process_chunk(self, chunk: str) -> str | None:
        return chunk  # pass through — extraction happens at response level

    def process_response(self, response: LLMResponse, decoder: Any) -> list[ActionIR] | None:
        """
        If content looks like a JSON tool call and there are no native tool_calls,
        extract tool calls from content and convert to proper response format.
        Preserves any reasoning text outside the JSON block.
        """
        # Only activate when there's content but no tool_calls
        if not response.content or response.tool_calls:
            return None

        content = response.content.strip()

        # Find markdown code fences if present, and separate the reasoning text
        import re
        m = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', content, re.DOTALL)
        if m:
            json_str = m.group(1).strip()
            reasoning_text = content[:m.start()].strip() + "\n" + content[m.end():].strip()
            reasoning_text = reasoning_text.strip()
        else:
            json_str = content
            reasoning_text = ""

        # Must start with { or [ to be potential JSON
        if not (json_str.startswith('{') or json_str.startswith('[')):
            return None

        try:
            parsed = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            return None

        # Normalize to list of tool call dicts
        tool_call_dicts = self._extract_tool_calls(parsed)
        if not tool_call_dicts:
            return None

        # Validate tool names if we have a tool list
        if self._available_tools:
            tool_call_dicts = [
                tc for tc in tool_call_dicts
                if tc["name"] in self._available_tools
            ]
            if not tool_call_dicts:
                return None

        # Convert to tool_calls format and set on response
        logger.info(
            "🔧 JsonToolCallExtractor: Extracted %d tool call(s) from content: %s",
            len(tool_call_dicts),
            ", ".join(tc["name"] for tc in tool_call_dicts)
        )

        # Build tool_calls in OpenAI format
        tool_calls = []
        for i, tc in enumerate(tool_call_dicts):
            tool_calls.append({
                "id": f"json_extract_{i}",
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"]) if isinstance(tc["arguments"], dict) else tc["arguments"]
                }
            })

        # Mutate response: move JSON to tool_calls, preserve reasoning
        try:
            response.content = reasoning_text if reasoning_text else None
            response.tool_calls = tool_calls
        except AttributeError:
            # If response doesn't support mutation, fall back
            pass

        # Let the pipeline continue — decoder.decode will now see tool_calls
        return None

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Remove markdown code fences: ```json ... ``` or ``` ... ```"""
        text = text.strip()
        # Match ```json\n...\n``` or ```\n...\n```
        m = re.match(r'^```(?:json)?\s*\n?(.*?)\n?\s*```$', text, re.DOTALL)
        if m:
            return m.group(1).strip()
        return text

    @staticmethod
    def _extract_tool_calls(parsed: Any) -> list[dict]:
        """
        Extract tool call dicts from parsed JSON.

        Supports:
        - {"name": "func", "arguments": {...}}
        - [{"name": "func1", "arguments": {...}}, ...]
        - {"function": {"name": "func", "arguments": {...}}}  (OpenAI-ish)
        """
        results = []

        if isinstance(parsed, dict):
            tc = JsonToolCallExtractor._try_parse_single(parsed)
            if tc:
                results.append(tc)
        elif isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    tc = JsonToolCallExtractor._try_parse_single(item)
                    if tc:
                        results.append(tc)

        return results

    @staticmethod
    def _try_parse_single(d: dict) -> dict | None:
        """
        Try to parse a single dict as a tool call.

        Accepts:
        - {"name": "func", "arguments": {...}}
        - {"function": {"name": "func", "arguments": {...}}}
        - {"name": "func", "parameters": {...}}
        - {"result": "..."} -> inferred as SubmitResult
        """
        # Format 1: {"name": ..., "arguments": ...}
        if "name" in d and isinstance(d["name"], str):
            name = d["name"]
            args = d.get("arguments") or d.get("parameters") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    return None
            if isinstance(args, dict):
                return {"name": name, "arguments": args}

        # Format 2: {"function": {"name": ..., "arguments": ...}}
        if "function" in d and isinstance(d["function"], dict):
            func = d["function"]
            return JsonToolCallExtractor._try_parse_single(func)

        # Format 3: Bare {"result": "..."} (Qwen specific hallucination mitigation)
        if "name" not in d and "result" in d:
            # Often Qwen hallucinates {"result": "...", "id": null, "type": "text"}
            # Infer this as SubmitResult
            return {
                "name": "SubmitResult",
                "arguments": {"result": d["result"]}
            }

        return None


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
