"""
AgentOS — The facade that assembles and orchestrates all components.

This is the entry point for users. It wires together:
- ToolRegistry → defines what tools the agent has
- MMU → manages context window
- KernelGate → executes tools with safety checks
- InstructionDecoder → validates LLM output
- Adapter (ALU) → communicates with the LLM
- VCPU → runs the Think-Act-Observe FSM
- RuntimeLoop → drives VCPU to completion

Usage:
    agent = AgentOS(adapter)
    result = await agent.run("列出当前目录的文件")
"""

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .adapter import AdapterConfig, AnthropicAdapter, OpenAIAdapter
from .decoder import InstructionDecoder
from .gate import KernelGate
from .loop import LoopConfig, RuntimeLoop
from .mmu import MMU, MMUConfig, PinnedContext
from .protocol import Event, ToolResult
from .tools.registry import ToolRegistry
from .vcpu import VCPU, VCPUConfig

logger = logging.getLogger("nimbus.agent")


# =============================================================================
# Agent Configuration
# =============================================================================


@dataclass
class AgentConfig:
    """Top-level configuration for the agent."""
    # LLM
    model: str = "gpt-4o"
    provider: str = "openai"  # "openai" or "anthropic"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.0
    max_tokens: int = 4096

    # VCPU
    max_iterations: int = 50
    max_consecutive_thoughts: int = 8
    llm_call_timeout: float = 300.0

    # MMU
    max_context_tokens: int = 100_000
    compress_threshold: float = 0.85

    # Loop
    max_compactions: int = 3

    # Gate
    tool_timeout: float = 60.0

    # Behavior
    text_is_final: bool = False  # In task mode, pure text != done


# =============================================================================
# Default Tools
# =============================================================================


def _register_default_tools(registry: ToolRegistry) -> None:
    """Register the built-in tool set."""
    from .tools.read import read_file
    from .tools.write import write_file
    from .tools.edit import edit_file
    from .tools.bash import bash_command
    from .tools.grep import grep_search

    registry.register_decorated(read_file)
    registry.register_decorated(write_file)
    registry.register_decorated(edit_file)
    registry.register_decorated(bash_command)
    registry.register_decorated(grep_search)


# =============================================================================
# AgentOS — The Facade
# =============================================================================


class AgentOS:
    """Minimal agent OS that wires all components together.

    Example:
        agent = AgentOS()
        result = await agent.run("Read the file config.py and explain it")
    """

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        adapter: Any = None,
        tools: Optional[ToolRegistry] = None,
        system_prompt: str = "",
        event_callback: Optional[Callable[[Event], None]] = None,
    ):
        self.config = config or AgentConfig()
        self._event_cb = event_callback

        # 1. Adapter (ALU)
        if adapter:
            self._adapter = adapter
        else:
            self._adapter = self._create_adapter()

        # 2. Tool Registry
        self._registry = tools or ToolRegistry()
        if tools is None:
            _register_default_tools(self._registry)

        # 3. System prompt
        self._system_prompt = system_prompt or self._default_system_prompt()

    def _create_adapter(self) -> Any:
        """Create the appropriate LLM adapter based on config."""
        adapter_config = AdapterConfig(
            model=self.config.model,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        if self.config.provider == "anthropic":
            return AnthropicAdapter(adapter_config)
        return OpenAIAdapter(adapter_config)

    def _default_system_prompt(self) -> str:
        return (
            "You are a capable AI coding assistant. "
            "Use the available tools to accomplish the user's task. "
            "Think step by step. When the task is complete, provide a concise summary."
        )

    # --- Public API ---

    async def run(self, goal: str) -> ToolResult:
        """Run the agent on a goal until completion. Returns final ToolResult."""
        loop = self._build_loop(goal)
        return await loop.run()

    async def stream(self, goal: str) -> AsyncIterator[Dict[str, Any]]:
        """Run the agent, streaming step events."""
        loop = self._build_loop(goal)
        async for event in loop.stream():
            yield event

    async def chat(self, message: str) -> str:
        """Simple chat interface. Returns the text response."""
        loop = self._build_loop(message, text_is_final=True)
        result = await loop.run()
        return str(result.output) if result.output else ""

    # --- Build Pipeline ---

    def _build_loop(self, goal: str, text_is_final: Optional[bool] = None) -> RuntimeLoop:
        """Assemble all components into a RuntimeLoop for one execution."""
        pid = uuid.uuid4().hex[:8]

        # MMU
        mmu_config = MMUConfig(
            max_context_tokens=self.config.max_context_tokens,
            compress_threshold=self.config.compress_threshold,
        )
        mmu = MMU(mmu_config)
        mmu.set_pinned(PinnedContext(
            system_rules=self._system_prompt,
            workspace_info=f"Working directory: {os.getcwd()}",
        ))
        mmu.set_goal(goal)
        mmu.add_user_message(goal)

        # Gate
        async def tool_executor(name: str, args: Dict) -> Any:
            return await self._registry.execute(name, args)

        gate = KernelGate(
            pid=pid,
            tool_executor=tool_executor,
            event_callback=self._event_cb,
            default_timeout=self.config.tool_timeout,
        )

        # Decoder
        decoder = InstructionDecoder()

        # Tool schemas
        schema_format = "anthropic" if self.config.provider == "anthropic" else "openai"
        tool_schemas = self._registry.get_schemas(format=schema_format)

        # VCPU
        vcpu_config = VCPUConfig(
            max_iterations=self.config.max_iterations,
            max_consecutive_thoughts=self.config.max_consecutive_thoughts,
            llm_call_timeout=self.config.llm_call_timeout,
        )
        final = text_is_final if text_is_final is not None else self.config.text_is_final
        vcpu = VCPU(
            alu=self._adapter,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            tools=tool_schemas,
            config=vcpu_config,
            text_is_final=final,
        )

        # Loop
        loop_config = LoopConfig(max_compactions=self.config.max_compactions)
        return RuntimeLoop(
            vcpu=vcpu,
            mmu=mmu,
            config=loop_config,
            event_callback=self._event_cb,
        )

    # --- Registry access ---

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def register_tool(self, func: Callable) -> None:
        """Register a @tool-decorated function."""
        self._registry.register_decorated(func)
