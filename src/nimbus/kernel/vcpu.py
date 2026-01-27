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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..llm.base import CompletionResponse, LLMClient, ToolCall, ToolResult
from ..tools.base import ToolRegistry
from .proc import AgentProcess, ProcessState

logger = logging.getLogger(__name__)

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

# Maximum number of correction retries before giving up
MAX_CORRECTION_RETRIES = 3

# Temperature stepping for retries (reduces randomness on each retry)
RETRY_TEMPERATURES = [0.7, 0.3, 0.0]

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
FAKE_TOOL_CALL_PATTERN = re.compile(
    r"\[Called\s+(\w+)\s+with\s+(\{.+?\})\]",
    re.IGNORECASE | re.DOTALL
)

# Minimum response length to be considered valid (avoid empty responses)
MIN_RESPONSE_LENGTH = 20

# Maximum retries for empty response
MAX_EMPTY_RESPONSE_RETRIES = 2

# Message to force LLM to provide a summary when response is empty
EMPTY_RESPONSE_PROMPT = (
    "You stopped without providing any output. "
    "Please summarize what you found or did. "
    "Provide a clear, concise response to the original task."
)


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
        max_iterations: Maximum iterations per process execution
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        max_iterations: int = 50,
        workspace: Optional[Path] = None,
    ):
        """Initialize vCPU.

        Args:
            llm_client: LLM client (ALU) for generating completions
            tool_registry: Tool registry (ISA) for executing tools
            max_iterations: Maximum iterations per process (safety limit)
            workspace: Working directory for tool execution (file operations)
        """
        self.llm = llm_client
        self.tools = tool_registry
        self.max_iterations = max_iterations
        self.workspace = workspace

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

            # Correction retry counter for "talkative LLM" problem
            correction_retries = 0
            # Empty response retry counter
            empty_response_retries = 0

            # Main execution loop
            iteration = 0
            while iteration < self.max_iterations:
                logger.debug(
                    f"Process {process.pid} iteration {iteration + 1}/{self.max_iterations}"
                )

                # Check resource limits (Interrupt Handler)
                self._check_resource_limits(process)

                # Step 1: Assemble context (MMU - Registers)
                context = self._assemble_context(process)

                # Step 2: Think (Control Unit -> ALU)
                response = await self._think(context, process)

                # Step 2.5: Check for "talkative LLM" problem
                # If LLM describes tool calls in text but doesn't actually call them,
                # send a correction message and retry
                if not response.tool_calls and response.content and self._detect_tool_description_in_text(response.content):
                    correction_retries += 1
                    if correction_retries <= MAX_CORRECTION_RETRIES:
                        logger.warning(
                            f"Process {process.pid} LLM described tool call in text "
                            f"(retry {correction_retries}/{MAX_CORRECTION_RETRIES}), "
                            f"sending correction..."
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
                        # Don't increment iteration, just retry
                        continue
                    else:
                        # Exceeded retries - try Mimicry Parser as last resort
                        logger.warning(
                            f"Process {process.pid} exceeded correction retries "
                            f"({MAX_CORRECTION_RETRIES}), trying Mimicry Parser..."
                        )
                        parsed = self._try_parse_fake_tool_call(response.content)
                        if parsed:
                            tool_name, tool_args = parsed
                            logger.info(
                                f"Process {process.pid} Mimicry Parser rescued: "
                                f"executing {tool_name} from text"
                            )
                            # Create synthetic tool call and inject into response
                            synthetic_call = ToolCall(
                                id=f"mimicry_{iteration}",
                                name=tool_name,
                                arguments=tool_args,
                            )
                            # Replace response with one that has tool calls
                            response = CompletionResponse(
                                content=response.content,
                                tool_calls=[synthetic_call],
                                finish_reason="tool_calls",
                                raw_response=response.raw_response,
                            )
                            logger.info(
                                f"Process {process.pid} Mimicry Parser injected tool call: "
                                f"{tool_name}"
                            )
                        else:
                            logger.warning(
                                f"Process {process.pid} Mimicry Parser failed, "
                                f"proceeding with text response"
                            )
                        # Reset counter for future iterations
                        correction_retries = 0

                # Reset correction counter on successful tool call
                if response.tool_calls:
                    correction_retries = 0
                    empty_response_retries = 0  # Reset empty response counter too

                # Step 3: Check stop condition
                if self._is_done(response):
                    # Step 3.5: Check for empty response and force summary
                    content = response.content or ""
                    if len(content.strip()) < MIN_RESPONSE_LENGTH:
                        empty_response_retries += 1
                        if empty_response_retries <= MAX_EMPTY_RESPONSE_RETRIES:
                            logger.warning(
                                f"Process {process.pid} returned empty/short response "
                                f"(retry {empty_response_retries}/{MAX_EMPTY_RESPONSE_RETRIES}), "
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
                            # Don't increment iteration, just retry
                            continue
                        else:
                            logger.warning(
                                f"Process {process.pid} still empty after "
                                f"{MAX_EMPTY_RESPONSE_RETRIES} retries, accepting empty response"
                            )

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
                f"Process {process.pid} exceeded maximum iterations ({self.max_iterations})",
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
    ) -> CompletionResponse:
        """Think step (Control Unit -> ALU).

        Calls the LLM to generate the next action based on current context.

        Args:
            context: Assembled context messages
            process: The process (for tool permissions)

        Returns:
            CompletionResponse from the LLM
        """
        # Get tool schemas for allowed tools only
        tools_schema = self._get_allowed_tools_schema(process)

        # Call LLM (ALU)
        response = await self.llm.complete_with_tools(
            messages=context,
            tools=tools_schema,
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
        """Mimicry Parser: Extract tool call from fake text format.

        When LLM outputs '[Called Edit with {...}]' instead of actual function call,
        this parser extracts the tool name and arguments so we can execute anyway.

        This is a robust engineering fallback for the "format hallucination" problem
        where the model believes text representation equals API call.

        Args:
            text: The LLM response text that may contain fake tool call

        Returns:
            Tuple of (tool_name, arguments_dict) if parseable, None otherwise
        """
        if not text:
            return None

        match = FAKE_TOOL_CALL_PATTERN.search(text)
        if not match:
            return None

        tool_name = match.group(1)
        args_str = match.group(2)

        # Try multiple parsing strategies
        args = None

        # Strategy 1: Standard JSON
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            pass

        # Strategy 2: Python literal (handles single quotes, True/False/None)
        if args is None:
            try:
                args = ast.literal_eval(args_str)
            except (ValueError, SyntaxError):
                pass

        # Strategy 3: Fix common issues and retry
        if args is None:
            try:
                # Replace Python-style booleans/None with JSON equivalents
                fixed = args_str.replace("True", "true").replace("False", "false").replace("None", "null")
                # Replace single quotes with double quotes (simple cases)
                fixed = re.sub(r"'([^']*)'", r'"\1"', fixed)
                args = json.loads(fixed)
            except (json.JSONDecodeError, Exception):
                pass

        if args is not None and isinstance(args, dict):
            logger.info(
                f"Mimicry Parser: Extracted fake tool call -> {tool_name}({list(args.keys())})"
            )
            return (tool_name, args)

        logger.warning(
            f"Mimicry Parser: Found pattern but failed to parse args: {args_str[:100]}..."
        )
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
