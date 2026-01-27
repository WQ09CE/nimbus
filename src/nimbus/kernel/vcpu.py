"""
Virtual CPU for Agent OS.

Architecture Layer: 1 (Agent OS - Kernel)
Von Neumann Role: vCPU (Virtual Processor)

vCPU = Control Unit + MMU + Interrupt Handler

The vCPU is the core execution engine for Agent processes. It implements
the Think-Act-Observe loop that drives agent behavior:

1. Control Unit: Orchestrates the agentic loop
   - Think: Call LLM to decide next action
   - Act: Execute tool calls
   - Observe: Update process state with results

2. MMU (Memory Management Unit): Context window management
   - Assembles context from process memory
   - Future: Intelligent context compression

3. Interrupt Handler: Error recovery
   - Handles tool execution errors
   - Manages resource limit violations
   - Graceful process termination

Example:
    >>> from nimbus.kernel.vcpu import vCPU
    >>> from nimbus.llm.base import LLMClient
    >>> from nimbus.tools.base import ToolRegistry
    >>>
    >>> vcpu = vCPU(llm_client, tool_registry)
    >>> result = await vcpu.execute(process)
"""

__layer__ = 1
__role__ = "vCPU"

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..llm.base import CompletionResponse, LLMClient, ToolCall, ToolResult
from ..tools.base import ToolRegistry
from .proc import AgentProcess, ProcessState

logger = logging.getLogger(__name__)


# ============================================================================
# vCPU Configuration
# ============================================================================


@dataclass
class vCPUConfig:
    """Configuration for vCPU behavior.

    This dataclass centralizes all configurable parameters for the vCPU,
    replacing scattered magic numbers with a single, documented config object.

    Attributes:
        max_iterations: Maximum iterations per process execution (safety limit).
        max_correction_retries: Max retries for "talkative LLM" correction.
        max_empty_response_retries: Max retries when LLM returns empty response.
        min_response_length: Minimum chars for a response to be considered valid.
        retry_temperatures: Temperature values for retry attempts (decaying).
        enable_temperature_decay: Whether to use temperature decay on retries.
    """
    max_iterations: int = 50
    max_correction_retries: int = 3
    max_empty_response_retries: int = 2
    min_response_length: int = 20
    retry_temperatures: List[float] = field(default_factory=lambda: [0.7, 0.3, 0.0])
    enable_temperature_decay: bool = True

    def get_retry_temperature(self, retry_count: int) -> Optional[float]:
        """Get temperature for a given retry attempt.

        Args:
            retry_count: Current retry number (1-indexed).

        Returns:
            Temperature value, or None if temperature decay is disabled.
        """
        if not self.enable_temperature_decay:
            return None
        if retry_count <= 0:
            return None
        # Use the temperature at index (retry_count - 1), capped at last value
        idx = min(retry_count - 1, len(self.retry_temperatures) - 1)
        return self.retry_temperatures[idx]


# Default config instance
DEFAULT_CONFIG = vCPUConfig()


# ============================================================================
# Constants and Patterns
# ============================================================================


# Patterns that indicate the LLM is describing tool calls in text instead of actually calling them
# This is the "talkative LLM" problem common with Gemini and other models
TOOL_DESCRIPTION_PATTERNS = [
    r"I will (now )?call",
    r"I('ll| will) use the (\w+) tool",
    r"\[Called \w+ with",
    r"Let me (use|call|invoke)",
    r"I('ll| am going to| will) (invoke|execute|run)",
    r"Using the (\w+) (tool|function)",
    r"Calling (\w+) with",
]

# Correction message sent to the LLM when it describes tool calls instead of calling them
# Uses "Contrastive Correction" - explicitly showing what is WRONG vs RIGHT
TOOL_CALL_CORRECTION_MESSAGE = (
    "CRITICAL ERROR: Invalid tool call format.\n"
    "WRONG: [Called Edit with {...}] or 'I will call Edit...'\n"
    "RIGHT: Use the actual API function call mechanism.\n"
    "You are outputting TEXT that looks like a tool call. I cannot execute text.\n"
    "RETRY: Respond with a RAW function call via the API. ZERO natural language allowed."
)

# Regex pattern for extracting fake tool calls from text
# Matches: [Called ToolName with {json_args}] or [Called ToolName with {'key': 'value'}]
# Uses stack-based matching for nested JSON via findall with non-greedy match
FAKE_TOOL_CALL_PATTERN = re.compile(
    r"\[Called\s+(\w+)\s+with\s+(\{.+?\})\]",
    re.IGNORECASE | re.DOTALL
)

# Message to force LLM to provide a summary when response is empty
EMPTY_RESPONSE_PROMPT = (
    "You stopped without providing any output. "
    "Please summarize what you found or did. "
    "Provide a clear, concise response to the original task."
)


# Legacy constants for backward compatibility (deprecated, use vCPUConfig instead)
MAX_CORRECTION_RETRIES = DEFAULT_CONFIG.max_correction_retries
RETRY_TEMPERATURES = DEFAULT_CONFIG.retry_temperatures
MIN_RESPONSE_LENGTH = DEFAULT_CONFIG.min_response_length
MAX_EMPTY_RESPONSE_RETRIES = DEFAULT_CONFIG.max_empty_response_retries


class vCPUError(Exception):
    """Base exception for vCPU errors."""

    def __init__(self, message: str, process_pid: Optional[str] = None):
        super().__init__(message)
        self.process_pid = process_pid


class ResourceLimitError(vCPUError):
    """Raised when a process exceeds its resource limits."""
    pass


class MaxIterationsError(vCPUError):
    """Raised when a process exceeds maximum iterations."""
    pass


class vCPU:
    """Virtual CPU for executing Agent processes.

    The vCPU implements the core execution loop for agent processes,
    coordinating between the LLM (ALU) and tools (ISA).

    Components:
    - Control Unit: Agentic loop (Think -> Act -> Observe)
    - MMU: Context window management (Registers)
    - Interrupt Handler: Error recovery

    Attributes:
        llm: LLM client for generating completions (ALU)
        tools: Tool registry for executing tools (ISA)
        config: vCPU configuration (iteration limits, retry settings, etc.)
        max_iterations: Maximum iterations per process execution (from config)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        max_iterations: int = 50,
        workspace: Optional[Path] = None,
        config: Optional[vCPUConfig] = None,
    ):
        """Initialize vCPU.

        Args:
            llm_client: LLM client (ALU) for generating completions
            tool_registry: Tool registry (ISA) for executing tools
            max_iterations: Maximum iterations per process (safety limit).
                           If config is provided, this is ignored.
            workspace: Working directory for tool execution (file operations)
            config: Optional vCPUConfig for detailed configuration.
                   If not provided, uses default config with max_iterations override.
        """
        self.llm = llm_client
        self.tools = tool_registry
        self.workspace = workspace

        # Use provided config or create default with max_iterations override
        if config is not None:
            self.config = config
        else:
            self.config = vCPUConfig(max_iterations=max_iterations)

        # Expose max_iterations for backward compatibility
        self.max_iterations = self.config.max_iterations

    async def execute(self, process: AgentProcess) -> Any:
        """Execute a process until completion.

        This is the main execution loop implementing Think-Act-Observe:

        1. Assemble context (MMU - Registers)
        2. Think (Control Unit -> LLM/ALU)
        3. Check stop condition
        4. Act (Control Unit -> Tools/ISA)
        5. Observe (Update process memory)
        6. Repeat or return

        Args:
            process: The AgentProcess to execute

        Returns:
            Process result (content from final LLM response)

        Raises:
            vCPUError: If process is not in a runnable state
            ResourceLimitError: If token budget or turn limit exceeded
            MaxIterationsError: If max iterations reached
        """
        # Validate process state - accept READY or RUNNING
        # (ProcessManager may set RUNNING before calling executor)
        if process.state not in (ProcessState.READY, ProcessState.RUNNING):
            raise vCPUError(
                f"Process {process.pid} is not in a runnable state "
                f"(current: {process.state.value}, expected: ready or running)",
                process_pid=process.pid,
            )

        # Ensure state is RUNNING
        if process.state == ProcessState.READY:
            process.state = ProcessState.RUNNING
        if process.started_at is None:
            process.started_at = datetime.now()

        logger.info(f"vCPU executing process {process.pid} (role={process.role})")

        try:
            # Initialize memory with system prompt and task
            self._initialize_memory(process)

            # Retry counters
            correction_retries = 0
            empty_response_retries = 0

            # Main execution loop
            iteration = 0
            while iteration < self.config.max_iterations:
                logger.debug(
                    f"Process {process.pid} iteration {iteration + 1}/{self.config.max_iterations}"
                )

                # Check resource limits (Interrupt Handler)
                self._check_resource_limits(process)

                # Step 1: Assemble context (MMU - Registers)
                context = self._assemble_context(process)

                # Step 2: Think (Control Unit -> ALU)
                # Apply temperature decay on correction retries
                temperature = self.config.get_retry_temperature(correction_retries)
                response = await self._think(context, process, temperature=temperature)

                # Step 2.5: Handle "talkative LLM" problem
                handled, response, correction_retries = self._handle_talkative_llm(
                    response, process, iteration, correction_retries
                )
                if handled == "retry":
                    continue  # Don't increment iteration, just retry

                # Reset correction counter on successful tool call
                if response.tool_calls:
                    correction_retries = 0
                    empty_response_retries = 0

                # Step 3: Check stop condition
                if self._is_done(response):
                    # Step 3.5: Handle empty response
                    should_retry, empty_response_retries = self._handle_empty_response(
                        response, process, empty_response_retries
                    )
                    if should_retry:
                        continue  # Don't increment iteration, just retry

                    result = self._extract_result(response)
                    process.complete(result)
                    logger.info(
                        f"Process {process.pid} completed successfully "
                        f"(turns={process.current_turn}, tokens={process.token_usage})"
                    )
                    return result

                # Step 4: Act (Control Unit -> Tools/ISA)
                tool_results = await self._act(response, process)

                # Step 5: Observe (Update process memory)
                self._observe(process, response, tool_results)

                # Increment iteration
                iteration += 1

            # Max iterations reached (Interrupt Handler)
            raise MaxIterationsError(
                f"Process {process.pid} exceeded maximum iterations ({self.config.max_iterations})",
                process_pid=process.pid,
            )

        except (ResourceLimitError, MaxIterationsError) as e:
            # Known limit violations - fail gracefully
            await self._handle_error(process, e)
            raise
        except Exception as e:
            # Unexpected errors
            await self._handle_error(process, e)
            raise vCPUError(
                f"Process {process.pid} failed: {e}",
                process_pid=process.pid,
            ) from e

    def _handle_talkative_llm(
        self,
        response: CompletionResponse,
        process: AgentProcess,
        iteration: int,
        correction_retries: int,
    ) -> Tuple[str, CompletionResponse, int]:
        """Handle "talkative LLM" problem - LLM describing tool calls instead of calling them.

        This method implements a multi-stage recovery strategy:
        1. First, send correction messages with temperature decay
        2. If max retries exceeded, try Mimicry Parser to extract and execute fake tool calls

        Args:
            response: The LLM response to check
            process: The process being executed
            iteration: Current iteration number (for synthetic call IDs)
            correction_retries: Current retry count

        Returns:
            Tuple of:
            - action: "retry" if should retry without incrementing iteration, "continue" otherwise
            - response: Possibly modified response (with injected tool calls from Mimicry Parser)
            - correction_retries: Updated retry count
        """
        # Check if this is actually a talkative LLM case
        if response.tool_calls or not response.content:
            return ("continue", response, correction_retries)

        if not self._detect_tool_description_in_text(response.content):
            return ("continue", response, correction_retries)

        # Detected tool description in text
        correction_retries += 1

        if correction_retries <= self.config.max_correction_retries:
            logger.warning(
                f"Process {process.pid} LLM described tool call in text "
                f"(retry {correction_retries}/{self.config.max_correction_retries}), "
                f"sending correction with temperature={self.config.get_retry_temperature(correction_retries)}..."
            )
            # Add correction message to memory
            process.memory.append({
                "role": "assistant",
                "content": response.content,
            })
            process.memory.append({
                "role": "user",
                "content": TOOL_CALL_CORRECTION_MESSAGE,
            })
            return ("retry", response, correction_retries)

        # Exceeded retries - try Mimicry Parser as last resort
        logger.warning(
            f"Process {process.pid} exceeded correction retries "
            f"({self.config.max_correction_retries}), trying Mimicry Parser..."
        )
        parsed_calls = self._try_parse_fake_tool_calls(response.content)
        if parsed_calls:
            # Create synthetic tool calls
            synthetic_calls = [
                ToolCall(
                    id=f"mimicry_{iteration}_{i}",
                    name=name,
                    arguments=args,
                )
                for i, (name, args) in enumerate(parsed_calls)
            ]
            tool_names = [tc.name for tc in synthetic_calls]
            logger.info(
                f"Process {process.pid} Mimicry Parser rescued: "
                f"extracted {len(synthetic_calls)} tool calls: {tool_names}"
            )
            # Replace response with one that has tool calls
            response = CompletionResponse(
                content=response.content,
                tool_calls=synthetic_calls,
                finish_reason="tool_calls",
                raw_response=response.raw_response,
            )
        else:
            logger.warning(
                f"Process {process.pid} Mimicry Parser failed, "
                f"proceeding with text response"
            )

        # Reset counter for future iterations
        return ("continue", response, 0)

    def _handle_empty_response(
        self,
        response: CompletionResponse,
        process: AgentProcess,
        empty_response_retries: int,
    ) -> Tuple[bool, int]:
        """Handle empty or too-short responses from LLM.

        Args:
            response: The LLM response to check
            process: The process being executed
            empty_response_retries: Current retry count

        Returns:
            Tuple of:
            - should_retry: True if should retry without incrementing iteration
            - empty_response_retries: Updated retry count
        """
        content = response.content or ""
        if len(content.strip()) >= self.config.min_response_length:
            return (False, empty_response_retries)

        empty_response_retries += 1

        if empty_response_retries <= self.config.max_empty_response_retries:
            logger.warning(
                f"Process {process.pid} returned empty/short response "
                f"(retry {empty_response_retries}/{self.config.max_empty_response_retries}), "
                f"requesting summary..."
            )
            # Add prompt to force summary
            process.memory.append({
                "role": "assistant",
                "content": content if content else "(no response)",
            })
            process.memory.append({
                "role": "user",
                "content": EMPTY_RESPONSE_PROMPT,
            })
            return (True, empty_response_retries)

        logger.warning(
            f"Process {process.pid} still empty after "
            f"{self.config.max_empty_response_retries} retries, accepting empty response"
        )
        return (False, empty_response_retries)

    def _initialize_memory(self, process: AgentProcess) -> None:
        """Initialize process memory with system prompt and task.

        Sets up the initial context for the LLM conversation.

        Args:
            process: The process to initialize
        """
        # Add system prompt if provided
        if process.system_prompt:
            process.memory.append({
                "role": "system",
                "content": process.system_prompt,
            })

        # Add task instruction as user message
        if process.task_instruction:
            process.memory.append({
                "role": "user",
                "content": process.task_instruction,
            })

    def _check_resource_limits(self, process: AgentProcess) -> None:
        """Check if process has exceeded resource limits.

        This is part of the Interrupt Handler - detecting limit violations.

        Args:
            process: The process to check

        Raises:
            ResourceLimitError: If any limit is exceeded
        """
        if process.token_usage >= process.max_token_budget:
            raise ResourceLimitError(
                f"Token budget exceeded: {process.token_usage}/{process.max_token_budget}",
                process_pid=process.pid,
            )

        if process.current_turn >= process.max_turns:
            raise ResourceLimitError(
                f"Turn limit exceeded: {process.current_turn}/{process.max_turns}",
                process_pid=process.pid,
            )

    def _assemble_context(self, process: AgentProcess) -> List[Dict[str, Any]]:
        """Assemble context window for LLM (MMU - Registers).

        This is the "Registers" in Von Neumann architecture - the most
        expensive, high-speed storage that the ALU directly operates on.

        Currently returns all messages. Future versions will implement
        intelligent context compression when approaching token limits.

        Args:
            process: The process to assemble context for

        Returns:
            List of messages for the LLM context
        """
        # TODO: Implement intelligent context compression
        # - Track token count per message
        # - Compress older messages when near budget
        # - Preserve critical information (system prompt, recent context)
        return process.memory.copy()

    async def _think(
        self,
        context: List[Dict[str, Any]],
        process: AgentProcess,
        temperature: Optional[float] = None,
    ) -> CompletionResponse:
        """Think step (Control Unit -> ALU).

        Calls the LLM to generate the next action based on current context.

        Args:
            context: Assembled context messages
            process: The process (for tool permissions)
            temperature: Optional temperature override for this call.
                        Used for temperature decay during correction retries.
                        If None, uses LLM's default temperature.

        Returns:
            CompletionResponse from the LLM
        """
        # Get tool schemas for allowed tools only
        tools_schema = self._get_allowed_tools_schema(process)

        # Build kwargs for LLM call
        llm_kwargs: Dict[str, Any] = {}
        if temperature is not None:
            llm_kwargs["temperature"] = temperature
            logger.debug(f"Process {process.pid} using temperature={temperature}")

        # Call LLM (ALU)
        response = await self.llm.complete_with_tools(
            messages=context,
            tools=tools_schema,
            **llm_kwargs,
        )

        # Update resource usage
        # TODO: Get actual token count from response.raw_response
        estimated_tokens = self._estimate_tokens(context, response)
        process.token_usage += estimated_tokens
        process.current_turn += 1

        logger.debug(
            f"Process {process.pid} think: "
            f"tool_calls={len(response.tool_calls)}, "
            f"finish_reason={response.finish_reason}"
        )

        # Log LLM response content for debugging
        if response.content:
            # Truncate long content for readability
            content_preview = response.content[:200] + "..." if len(response.content) > 200 else response.content
            logger.debug(f"Process {process.pid} LLM response: {content_preview}")

        # Log tool calls details
        for tc in response.tool_calls:
            logger.debug(f"Process {process.pid} tool_call: {tc.name}({tc.arguments})")

        return response

    def _get_allowed_tools_schema(self, process: AgentProcess) -> List[Dict[str, Any]]:
        """Get tool schemas for tools the process is allowed to use.

        Args:
            process: The process with tool permissions

        Returns:
            List of tool definitions in OpenAI format
        """
        if not process.allowed_tools:
            return []

        schemas = []
        for tool_name in process.allowed_tools:
            definition = self.tools.get_definition(tool_name)
            if definition:
                schemas.append(definition.to_openai_format())
            else:
                logger.warning(
                    f"Process {process.pid} has permission for unknown tool: {tool_name}"
                )

        return schemas

    def _detect_tool_description_in_text(self, text: str) -> bool:
        """Detect if LLM is describing tool calls in text instead of actually calling them.

        This is a common problem with some LLM providers (especially Gemini) where
        the model describes what it would do instead of actually making function calls.

        Args:
            text: The LLM response text to check

        Returns:
            True if text contains patterns indicating described (not actual) tool calls
        """
        if not text:
            return False

        for pattern in TOOL_DESCRIPTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                logger.debug(f"Detected tool description pattern: {pattern}")
                return True
        return False

    def _try_parse_fake_tool_call(self, text: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Mimicry Parser: Extract single tool call from fake text format (legacy).

        This is kept for backward compatibility. Use _try_parse_fake_tool_calls
        for multi-tool support.

        Args:
            text: The LLM response text that may contain fake tool call

        Returns:
            Tuple of (tool_name, arguments_dict) if parseable, None otherwise
        """
        results = self._try_parse_fake_tool_calls(text)
        return results[0] if results else None

    def _try_parse_fake_tool_calls(self, text: str) -> List[Tuple[str, Dict[str, Any]]]:
        """Mimicry Parser: Extract multiple tool calls from fake text format.

        When LLM outputs '[Called Edit with {...}] ... [Called Read with {...}]'
        instead of actual function calls, this parser extracts all tool names
        and arguments so we can execute them anyway.

        This is a robust engineering fallback for the "format hallucination" problem
        where the model believes text representation equals API call.

        Features:
        - Supports multiple tool calls in one response
        - Uses stack-based JSON matching for nested structures
        - Multiple parsing strategies (JSON, Python literal, fixup)

        Args:
            text: The LLM response text that may contain fake tool calls

        Returns:
            List of (tool_name, arguments_dict) tuples. Empty list if none found.
        """
        if not text:
            return []

        results: List[Tuple[str, Dict[str, Any]]] = []

        # Find all "[Called ToolName with " patterns and extract JSON with stack matching
        pattern = re.compile(r"\[Called\s+(\w+)\s+with\s+", re.IGNORECASE)

        for match in pattern.finditer(text):
            tool_name = match.group(1)
            json_start = match.end()

            # Use stack-based matching to find complete JSON object
            args_str = self._extract_balanced_json(text, json_start)
            if not args_str:
                continue

            # Try to parse the arguments
            args = self._parse_json_flexible(args_str)
            if args is not None and isinstance(args, dict):
                logger.info(
                    f"Mimicry Parser: Extracted fake tool call -> {tool_name}({list(args.keys())})"
                )
                results.append((tool_name, args))
            else:
                logger.warning(
                    f"Mimicry Parser: Found pattern but failed to parse args for {tool_name}: "
                    f"{args_str[:100]}..."
                )

        return results

    def _extract_balanced_json(self, text: str, start: int) -> Optional[str]:
        """Extract a balanced JSON object from text using stack-based matching.

        Handles nested braces properly, unlike simple regex.

        Args:
            text: The full text
            start: Position where the JSON object starts (at '{')

        Returns:
            The extracted JSON string, or None if no valid object found
        """
        if start >= len(text) or text[start] != '{':
            return None

        stack = 0
        in_string = False
        escape_next = False
        end = start

        for i in range(start, len(text)):
            char = text[i]

            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == '{':
                stack += 1
            elif char == '}':
                stack -= 1
                if stack == 0:
                    end = i + 1
                    break

        if stack != 0:
            return None

        return text[start:end]

    def _parse_json_flexible(self, args_str: str) -> Optional[Dict[str, Any]]:
        """Parse JSON-like string with multiple fallback strategies.

        Args:
            args_str: String that should be a JSON object

        Returns:
            Parsed dict, or None if all strategies fail
        """
        # Strategy 1: Standard JSON
        try:
            return json.loads(args_str)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Python literal (handles single quotes, True/False/None)
        try:
            result = ast.literal_eval(args_str)
            if isinstance(result, dict):
                return result
        except (ValueError, SyntaxError):
            pass

        # Strategy 3: Fix common issues and retry
        try:
            # Replace Python-style booleans/None with JSON equivalents
            fixed = args_str.replace("True", "true").replace("False", "false").replace("None", "null")
            # Replace single quotes with double quotes (simple cases only)
            # This regex avoids replacing quotes inside strings
            fixed = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', fixed)
            return json.loads(fixed)
        except (json.JSONDecodeError, Exception):
            pass

        return None

    def _estimate_tokens(
        self,
        context: List[Dict[str, Any]],
        response: CompletionResponse,
    ) -> int:
        """Estimate token usage for context and response.

        This is a rough estimation. Real implementations should use
        the actual token counts from the LLM response.

        Args:
            context: Context messages
            response: LLM response

        Returns:
            Estimated token count
        """
        # Rough estimation: ~4 characters per token
        context_chars = sum(
            len(str(m.get("content", "") if isinstance(m, dict) else ""))
            for m in context
        )
        response_chars = len(response.content or "")

        # Add overhead for tool calls
        tool_chars = sum(
            len(str(tc.arguments))
            for tc in response.tool_calls
        )

        total_chars = context_chars + response_chars + tool_chars
        return total_chars // 4

    def _is_done(self, response: CompletionResponse) -> bool:
        """Check if task is complete.

        The task is complete when the LLM returns a response without
        any tool calls (it has finished reasoning and acting).

        Args:
            response: LLM response to check

        Returns:
            True if no more tool calls are needed
        """
        return response.is_complete

    def _extract_result(self, response: CompletionResponse) -> Dict[str, Any]:
        """Extract final result from LLM response.

        Args:
            response: Final LLM response

        Returns:
            Result dictionary with text and raw response
        """
        return {
            "text": response.content or "",
            "finish_reason": response.finish_reason,
        }

    async def _act(
        self,
        response: CompletionResponse,
        process: AgentProcess,
    ) -> List[ToolResult]:
        """Act step (Control Unit -> Tools/ISA).

        Executes tool calls requested by the LLM.

        Args:
            response: LLM response with tool calls
            process: The process (for permission checking)

        Returns:
            List of tool execution results
        """
        if not response.tool_calls:
            return []

        results = []
        for tool_call in response.tool_calls:
            result = await self._execute_tool(tool_call, process)
            results.append(result)

        return results

    async def _execute_tool(
        self,
        tool_call: ToolCall,
        process: AgentProcess,
    ) -> ToolResult:
        """Execute a single tool call with permission checking.

        Args:
            tool_call: The tool call to execute
            process: The process (for permission checking)

        Returns:
            ToolResult with output or error
        """
        tool_name = tool_call.name
        tool_args = tool_call.arguments

        logger.debug(f"Process {process.pid} executing tool: {tool_name} (allowed: {process.allowed_tools})")

        # Check permission
        if tool_name not in process.allowed_tools:
            logger.warning(
                f"Process {process.pid} permission denied for tool: {tool_name}"
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Permission denied: {tool_name} not in allowed_tools",
                is_error=True,
            )

        # Execute tool with workspace context
        try:
            output = await self.tools.execute(tool_name, tool_args, workspace=self.workspace)
            return ToolResult(
                tool_call_id=tool_call.id,
                content=str(output),
                is_error=False,
            )
        except Exception as e:
            logger.error(
                f"Process {process.pid} tool {tool_name} failed: {e}"
            )
            return ToolResult(
                tool_call_id=tool_call.id,
                content=f"Tool execution error: {e}",
                is_error=True,
            )

    def _observe(
        self,
        process: AgentProcess,
        response: CompletionResponse,
        tool_results: List[ToolResult],
    ) -> None:
        """Observe step (Update process memory).

        Adds the assistant's response and tool results to process memory
        for the next iteration.

        Args:
            process: The process to update
            response: LLM response
            tool_results: Results from tool execution
        """
        # Add assistant response
        assistant_message: Dict[str, Any] = {
            "role": "assistant",
        }

        if response.content:
            assistant_message["content"] = response.content

        if response.tool_calls:
            # Convert ToolCall objects to dict format
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in response.tool_calls
            ]

        process.memory.append(assistant_message)

        # Add tool results
        for result in tool_results:
            process.memory.append({
                "role": "tool",
                "tool_call_id": result.tool_call_id,
                "content": result.content,
            })

    async def _handle_error(self, process: AgentProcess, error: Exception) -> None:
        """Interrupt Handler: Handle errors during execution.

        Marks the process as failed and records the error.

        Args:
            process: The process that failed
            error: The exception that occurred
        """
        error_msg = str(error)
        logger.error(f"Process {process.pid} failed: {error_msg}")

        process.fail(error_msg)
        process.finished_at = datetime.now()

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"vCPU(llm={self.llm.__class__.__name__}, "
            f"tools={len(self.tools)}, "
            f"max_iterations={self.max_iterations})"
        )
