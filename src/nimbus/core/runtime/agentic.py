"""Agentic Loop Runtime for Nimbus.

This module provides the AgenticRunner, which implements an agentic loop
pattern where the LLM decides when to call tools and when to respond.

Unlike the DAG-based executor, the agentic runner:
- Lets the LLM see tool results before deciding next action
- Supports dynamic decision making based on intermediate results
- Naturally handles tasks that require exploration before execution

Example:
    ```python
    from nimbus.core.runtime.agentic import AgenticRunner
    from nimbus.tools import ToolRegistry

    runner = AgenticRunner(
        llm_client=client,
        tool_registry=registry,
    )

    async for event in runner.run("Read config.py and fix any bugs"):
        if event["type"] == "tool_call":
            print(f"Calling: {event['name']}")
        elif event["type"] == "tool_result":
            print(f"Result: {event['content'][:100]}...")
        elif event["type"] == "response":
            print(f"Final: {event['content']}")
    ```
"""

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Protocol, Set

from ..logging import get_logger

logger = get_logger("runtime.agentic")


# =============================================================================
# Protocols
# =============================================================================


class LLMClientWithTools(Protocol):
    """Protocol for LLM client with tool calling support."""

    async def complete_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_instruction: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        """Generate completion with tool calling support."""
        ...


class ToolExecutor(Protocol):
    """Protocol for tool execution."""

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        workspace: Optional[Path] = None,
    ) -> str:
        """Execute a tool and return result."""
        ...

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get OpenAI-format tool schemas."""
        ...


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class AgenticConfig:
    """Configuration for AgenticRunner.

    Attributes:
        max_iterations: Maximum number of LLM calls before stopping.
        max_tool_calls_per_turn: Maximum tool calls in a single LLM response.
        system_instruction: Base system instruction for the agent.
        temperature: LLM temperature for generation.
        allowed_tools: Set of allowed tool names (None = all).
        workspace: Working directory for tool execution.
    """
    max_iterations: int = 20
    max_tool_calls_per_turn: int = 10
    system_instruction: Optional[str] = None
    temperature: float = 0.7
    allowed_tools: Optional[Set[str]] = None
    workspace: Optional[Path] = None


# =============================================================================
# Events
# =============================================================================


@dataclass
class AgenticEvent:
    """Event emitted during agentic loop execution.

    Event types:
        - "start": Loop started
        - "tool_call": LLM requested a tool call
        - "tool_result": Tool execution completed
        - "thinking": LLM is generating (for streaming)
        - "response": Final response from LLM
        - "error": An error occurred
        - "max_iterations": Stopped due to iteration limit
    """
    type: str
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls, goal: str) -> "AgenticEvent":
        return cls(type="start", data={"goal": goal})

    @classmethod
    def tool_call(cls, name: str, arguments: Dict[str, Any], call_id: str) -> "AgenticEvent":
        return cls(type="tool_call", data={
            "name": name,
            "arguments": arguments,
            "call_id": call_id,
        })

    @classmethod
    def tool_result(cls, name: str, result: str, call_id: str, is_error: bool = False) -> "AgenticEvent":
        return cls(type="tool_result", data={
            "name": name,
            "result": result,
            "call_id": call_id,
            "is_error": is_error,
        })

    @classmethod
    def response(cls, content: str) -> "AgenticEvent":
        return cls(type="response", data={"content": content})

    @classmethod
    def error(cls, message: str, details: Optional[Dict[str, Any]] = None) -> "AgenticEvent":
        return cls(type="error", data={"message": message, "details": details or {}})

    @classmethod
    def max_iterations(cls, count: int) -> "AgenticEvent":
        return cls(type="max_iterations", data={"iterations": count})


# =============================================================================
# AgenticRunner
# =============================================================================


class AgenticRunner:
    """Agentic loop runtime for tool-using agents.

    The agentic runner implements a loop where:
    1. Send messages to LLM with available tools
    2. If LLM returns tool calls, execute them
    3. Add tool results to messages and repeat
    4. If LLM returns content without tool calls, we're done

    This pattern allows the LLM to dynamically decide what tools to use
    based on intermediate results, unlike pre-planned DAG execution.

    Attributes:
        llm_client: LLM client with tool calling support.
        tool_executor: Executor for running tools.
        config: Runner configuration.
    """

    def __init__(
        self,
        llm_client: LLMClientWithTools,
        tool_executor: ToolExecutor,
        config: Optional[AgenticConfig] = None,
    ):
        """Initialize the agentic runner.

        Args:
            llm_client: LLM client with complete_with_tools support.
            tool_executor: Tool executor for running tools.
            config: Optional configuration.
        """
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.config = config or AgenticConfig()

    async def run(
        self,
        goal: str,
        context: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[AgenticEvent]:
        """Run the agentic loop.

        Args:
            goal: The user's goal/request.
            context: Optional additional context.
            history: Optional conversation history.

        Yields:
            AgenticEvent for each step of execution.
        """
        yield AgenticEvent.start(goal)

        # Build initial messages
        messages: List[Dict[str, Any]] = []

        # Add history if provided
        if history:
            messages.extend(history)

        # Add current goal
        user_content = goal
        if context:
            user_content = f"{context}\n\n{goal}"

        messages.append({
            "role": "user",
            "content": user_content,
        })

        # Get tool schemas
        tools = self._get_filtered_tools()

        # Build system instruction
        system_instruction = self._build_system_instruction()

        # Agentic loop
        iteration = 0
        while iteration < self.config.max_iterations:
            iteration += 1
            logger.debug(f"Agentic iteration {iteration}/{self.config.max_iterations}")

            try:
                # Call LLM with tools
                response = await self.llm_client.complete_with_tools(
                    messages=messages,
                    tools=tools,
                    system_instruction=system_instruction,
                    temperature=self.config.temperature,
                )

                # Check for tool calls
                if response.has_tool_calls:
                    # Add assistant message with tool calls to history
                    assistant_msg: Dict[str, Any] = {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments),
                                }
                            }
                            for tc in response.tool_calls
                        ]
                    }
                    messages.append(assistant_msg)

                    # Execute each tool call
                    for tool_call in response.tool_calls[:self.config.max_tool_calls_per_turn]:
                        yield AgenticEvent.tool_call(
                            name=tool_call.name,
                            arguments=tool_call.arguments,
                            call_id=tool_call.id,
                        )

                        # Execute tool
                        try:
                            result = await self.tool_executor.execute(
                                tool_name=tool_call.name,
                                arguments=tool_call.arguments,
                                workspace=self.config.workspace,
                            )
                            is_error = False
                        except Exception as e:
                            result = f"Error executing {tool_call.name}: {str(e)}"
                            is_error = True
                            logger.warning(f"Tool execution error: {e}")

                        yield AgenticEvent.tool_result(
                            name=tool_call.name,
                            result=result,
                            call_id=tool_call.id,
                            is_error=is_error,
                        )

                        # Add tool result to messages
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        })

                else:
                    # No tool calls - this is the final response
                    final_content = response.content or ""
                    yield AgenticEvent.response(final_content)
                    return

            except Exception as e:
                logger.error(f"Agentic loop error: {e}")
                yield AgenticEvent.error(str(e))
                return

        # Reached max iterations
        yield AgenticEvent.max_iterations(iteration)

    def _get_filtered_tools(self) -> List[Dict[str, Any]]:
        """Get tool schemas, filtered by allowed_tools config.

        Returns:
            List of tool schemas in OpenAI format.
        """
        all_tools = self.tool_executor.get_tool_schemas()

        if self.config.allowed_tools is None:
            return all_tools

        return [
            tool for tool in all_tools
            if tool.get("function", {}).get("name") in self.config.allowed_tools
        ]

    def _build_system_instruction(self) -> str:
        """Build the system instruction for the agent.

        Returns:
            System instruction string.
        """
        base = self.config.system_instruction or ""

        # Add default agent instructions if not provided
        if not base:
            base = """You are a helpful coding assistant. You have access to tools for reading, writing, and searching files.

When given a task:
1. First understand what needs to be done
2. Use tools to gather information (read files, search code)
3. Based on what you learn, take appropriate actions
4. Verify your changes work correctly

Be precise and thorough. If something fails, try a different approach."""

        return base


# =============================================================================
# Tool Executor Adapter
# =============================================================================


class ToolRegistryExecutor:
    """Adapter to use ToolRegistry as a ToolExecutor.

    This bridges the existing ToolRegistry with the AgenticRunner.

    Example:
        ```python
        from nimbus.tools import ToolRegistry
        from nimbus.core.runtime.agentic import ToolRegistryExecutor, AgenticRunner

        registry = ToolRegistry()
        registry.register_decorated(read_file)
        registry.register_decorated(write_file)

        executor = ToolRegistryExecutor(registry)
        runner = AgenticRunner(llm_client, executor)
        ```
    """

    def __init__(self, registry: Any, workspace: Optional[Path] = None):
        """Initialize the executor.

        Args:
            registry: ToolRegistry instance.
            workspace: Default workspace for tool execution.
        """
        self.registry = registry
        self.workspace = workspace

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        workspace: Optional[Path] = None,
    ) -> str:
        """Execute a tool from the registry.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Arguments to pass to the tool.
            workspace: Working directory (overrides default).

        Returns:
            Tool execution result as string.

        Raises:
            ValueError: If tool not found.
            Exception: If tool execution fails.
        """
        tool = self.registry.get(tool_name)
        if tool is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        # Add workspace to arguments if the tool accepts it
        ws = workspace or self.workspace
        if ws is not None:
            arguments["workspace"] = ws

        # Execute the tool
        result = await self.registry.execute(tool_name, arguments)

        # Convert result to string
        if isinstance(result, str):
            return result
        elif isinstance(result, (dict, list)):
            return json.dumps(result, indent=2, ensure_ascii=False)
        else:
            return str(result)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get OpenAI-format tool schemas from registry.

        Returns:
            List of tool definitions in OpenAI function calling format.
        """
        # Use the registry's built-in method to get OpenAI format
        result: List[Dict[str, Any]] = self.registry.get_definitions(format="openai")
        return result
