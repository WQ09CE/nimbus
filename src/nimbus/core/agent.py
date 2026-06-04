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

Pi-coding-agent inspired features:
- Message queuing: inject messages while agent is working
- Streaming tool output: on_tool_output callback for live bash output
- Partial results on abort: interrupt never loses accumulated work
- Split tool results: output (LLM) + ui_detail (UI) separation

Usage:
    agent = AgentOS(adapter)
    result = await agent.run("列出当前目录的文件")

    # With message queuing:
    loop = agent.stream_with_queue("fix the bug")
    loop.message_queue.enqueue("also update the tests")
    async for event in loop.stream():
        print(event)
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from .decoder import InstructionDecoder
from .gate import KernelGate
from .loop import FollowUpQueue, LoopConfig, RuntimeLoop, SteeringQueue
from .mmu import MMU, MMUConfig, PinnedContext
from .path_context import AgentPathContext
from .protocol import Event, ToolResult
from .tools.registry import ToolRegistry
from .vcpu import VCPU, VCPUConfig

logger = logging.getLogger("nimbus.agent")


# =============================================================================
# Agent Configuration
# =============================================================================


@dataclass
class AgentConfig:
    """Configuration for the agent."""

    # LLM
    model: str = "anthropic/claude-sonnet-4-20250514"
    provider: str = "anthropic"  # "anthropic" | "openai" | "google" | "ollama"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.0
    max_tokens: int = 8192

    # VCPU
    max_iterations: int = 200  # Relaxed from 50; compaction is the real resource limit
    max_consecutive_thoughts: int = 8
    llm_call_timeout: float = 300.0
    max_consecutive_errors: int = 3

    # MMU
    max_context_tokens: int = 200_000
    compress_threshold: float = 0.85

    # Loop
    max_compactions: int = 3

    # Gate
    tool_timeout: float = 60.0

    # Behavior
    text_is_final: bool = False  # In goal mode, pure text != done
    contract_mode: bool = False  # Sub-agent only: must exit via submit_result, not text


# =============================================================================
# Agent Context Manager
# =============================================================================


def _register_default_tools(registry: ToolRegistry):
    """Register the default tool set."""
    from .tools.bash import bash_command
    from .tools.edit import edit_file
    from .tools.grep import grep_search
    from .tools.read import read_file
    from .tools.spawn_agent import spawn_agent
    from .tools.write import write_file

    registry.register_decorated(read_file)
    registry.register_decorated(write_file)
    registry.register_decorated(edit_file)
    registry.register_decorated(bash_command)
    registry.register_decorated(grep_search)
    registry.register_decorated(spawn_agent)


# =============================================================================
# AgentOS — The Top-Level Orchestrator
# =============================================================================


class AgentOS:
    """
    AgentOS — The top-level orchestrator.

    Accepts an external LLM client (created via llm_factory) and wires up
    VCPU, MMU, Loop, and Tool Registry into a running agent.
    """

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        adapter: Any = None,
        tools: Optional[ToolRegistry] = None,
        system_prompt: str = "",
        memory: str = "",
        event_callback: Optional[Callable[[Event], None]] = None,
        on_tool_output: Optional[Callable[[str, str], None]] = None,
        on_text_delta: Optional[Callable[[str], None]] = None,
        path_context: Optional[AgentPathContext] = None,
    ):
        self.config = config or AgentConfig()
        self._event_cb = event_callback
        # Pi-style: callback for streaming tool output (tool_name, chunk)
        self._on_tool_output = on_tool_output
        # Token-level text streaming callback (chunk)
        self._on_text_delta = on_text_delta
        # Optional path context override (used by sub-agents to inherit parent scope)
        self._path_context = path_context

        # 1. Adapter (ALU)
        if adapter:
            self._adapter = adapter
        else:
            self._adapter = self._create_adapter()

        # Resolve model context window from registry
        self._context_window = self.config.max_context_tokens  # default fallback
        if adapter and hasattr(adapter, '_model'):
            from nimbus.core.models.registry import ModelRegistry
            model_key = getattr(adapter, '_model', '')
            if '/' in model_key:
                model_key = model_key.split('/', 1)[1]
            info = ModelRegistry.get(model_key)
            if info:
                self._context_window = min(info.context_window, self.config.max_context_tokens)

        # 2. Tool Registry
        self._registry = tools or ToolRegistry()
        if tools is None:
            _register_default_tools(self._registry)

        # 3. System prompt
        self._system_prompt = system_prompt or self._default_system_prompt()

        # 4. User memory (from memory.md, pinned into MMU alongside system rules)
        self._memory = memory

        # 5. Session State (MMUs)
        self._mmus: Dict[str, MMU] = {}

    def _create_adapter(self) -> Any:
        """Create a DirectAdapter via llm_factory (unified adapter path)."""
        from nimbus.adapters.direct_adapter import DirectAdapter
        from nimbus.adapters.types import LLMConfig

        config = LLMConfig(
            model=self.config.model,
            base_url=self.config.base_url,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
        )
        return DirectAdapter(config)

    def _default_system_prompt(self) -> str:
        return (
            "You are a capable AI coding assistant. "
            "Use the available tools to accomplish the user's goal. "
            "Think step by step. When the goal is complete, provide a concise summary."
        )
        
    def get_mmu(self, session_id: str = "default") -> Optional[MMU]:
        """Get the MMU for a specific session_id, if it has been instantiated via stream_with_queue or run."""
        return self._mmus.get(session_id)

    # --- Public API ---

    async def run(self, goal: str, session_id: str = "default") -> ToolResult:
        """Run the agent on a goal until completion. Returns final ToolResult."""
        loop = self._build_loop(goal, session_id=session_id)
        return await loop.run()

    async def stream(self, goal: str, session_id: str = "default") -> AsyncIterator[Dict[str, Any]]:
        """Run the agent, streaming fine-grained events (pi-style)."""
        loop = self._build_loop(goal, session_id=session_id)
        async for event in loop.stream():
            yield event

    def stream_with_queue(
        self, 
        goal: str, 
        session_id: str = "default",
        storage: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        initial_messages: Optional[List[Dict[str, Any]]] = None,
        initial_vcpu_state: Optional[Dict[str, Any]] = None,
    ) -> RuntimeLoop:
        """Build a RuntimeLoop with message queue access (pi-style).

        Returns the loop so callers can enqueue messages while streaming:
            loop = agent.stream_with_queue("fix the bug")
            loop.message_queue.enqueue("also update tests")
            async for event in loop.stream():
                ...
            # On interrupt, partial results are in loop.partial_results
        """
        return self._build_loop(
            goal, 
            session_id=session_id,
            storage=storage,
            metadata=metadata,
            initial_messages=initial_messages,
            initial_vcpu_state=initial_vcpu_state,
        )

    async def chat(self, message: str, session_id: str = "default") -> str:
        """Simple chat interface. Returns the text response."""
        loop = self._build_loop(message, text_is_final=True, session_id=session_id)
        result = await loop.run()
        return str(result.output) if result.output else ""

    # --- Build Pipeline ---

    def _build_loop(
        self, 
        goal: str, 
        text_is_final: Optional[bool] = None, 
        session_id: str = "default",
        storage: Optional[Any] = None,
        metadata: Optional[Dict[str, Any]] = None,
        initial_messages: Optional[List[Dict[str, Any]]] = None,
        initial_vcpu_state: Optional[Dict[str, Any]] = None,
    ) -> RuntimeLoop:
        """Assemble all components into a RuntimeLoop for one execution.

        Wiring (pi-coding-agent style):
        1. Create steering/followup queues and abort event first
        2. Create Gate with abort event
        3. Create VCPU with steering callback
        4. Create RuntimeLoop with both queues
        """
        pid = uuid.uuid4().hex[:8]

        # Path context for workspace isolation
        # Use externally provided context (e.g. from parent agent) or default to cwd
        path_context = self._path_context or AgentPathContext.from_cwd()

        # MMU Stateful Retrieval
        if session_id not in self._mmus:
            mmu_config = MMUConfig(
                max_context_tokens=self._context_window,
                compress_threshold=self.config.compress_threshold,
            )
            mmu = MMU(mmu_config)
            logger.info(f"MMU context budget: {self._context_window:,} tokens (model: {getattr(self._adapter, '_model', 'unknown')})")
            mmu.set_pinned(PinnedContext(
                system_rules=self._system_prompt,
                workspace_info=f"Working directory: {path_context.target_root}",
                user_memory=self._memory,
            ))
            
            # Rehydrate initial messages directly into MMU from Dict cache
            if initial_messages:
                from nimbus.core.mmu import Message
                mmu._messages = []
                for m_dict in initial_messages:
                    mmu._messages.append(
                        Message(
                            role=m_dict.get("role", "user"),
                            content=m_dict.get("content", ""),
                            name=m_dict.get("name"),
                            tool_call_id=m_dict.get("tool_call_id"),
                            tool_calls=m_dict.get("tool_calls"),
                            meta=m_dict.get("meta", {})
                        )
                    )

            # Restore MMU critical state (global_summary + goal) from metadata
            if metadata:
                mmu_state = metadata.get("mmu_state")
                if mmu_state:
                    mmu._global_summary = mmu_state.get("global_summary", "")
                    mmu._goal = mmu_state.get("goal", "")

            self._mmus[session_id] = mmu
        else:
            # MMU already exists -- restore messages if provided and MMU is empty
            # (fixes H002: prewarm creates empty MMU, then stream_chat skips restoration)
            existing_mmu = self._mmus[session_id]
            if initial_messages and existing_mmu.message_count == 0:
                from nimbus.core.mmu import Message
                for m_dict in initial_messages:
                    existing_mmu._messages.append(
                        Message(
                            role=m_dict.get("role", "user"),
                            content=m_dict.get("content", ""),
                            name=m_dict.get("name"),
                            tool_call_id=m_dict.get("tool_call_id"),
                            tool_calls=m_dict.get("tool_calls"),
                            meta=m_dict.get("meta", {})
                        )
                    )
                # Also restore MMU state if available
                if metadata:
                    mmu_state = metadata.get("mmu_state")
                    if mmu_state:
                        existing_mmu._global_summary = mmu_state.get("global_summary", "")
                        existing_mmu._goal = mmu_state.get("goal", "")

        mmu = self._mmus[session_id]

        # Always update goal to the CURRENT user message so compaction
        # preserves the active goal, not a stale initial greeting.
        if goal:
            mmu.set_goal(goal)
            mmu.add_user_message(goal)

        # Create steering/followup queues and abort event first
        wakeup_event = asyncio.Event()
        steering_queue = SteeringQueue(wakeup_event)
        followup_queue = FollowUpQueue()
        abort_event = asyncio.Event()

        # Steering callback: drain one message from the steering queue
        def drain_steering() -> List[str]:
            msg = steering_queue.drain_one()
            return [msg] if msg else []

        # Gate (with abort event for process group kill)
        # vcpu_ref: mutable container so submit_result can request interruption
        # (VCPU is created after tool_executor, so we bind via reference)
        vcpu_ref: List[Any] = [None]

        async def tool_executor(name: str, args: Dict) -> Any:
            # Inject internal params for submit_result in contract_mode (sub-agents)
            if name == "submit_result" and self.config.contract_mode:
                args.setdefault("_sub_session_id", session_id)
                if vcpu_ref[0] is not None:
                    args.setdefault("_vcpu", vcpu_ref[0])
            return await self._registry.execute(name, args)

        gate = KernelGate(
            pid=pid,
            tool_executor=tool_executor,
            event_callback=self._event_cb,
            default_timeout=self.config.tool_timeout,
            on_tool_output=self._on_tool_output,
            abort_event=abort_event,
            path_context=path_context,
            parent_model=getattr(self._adapter, "_model", self.config.model),
            parent_base_url=getattr(getattr(self._adapter, "config", None), "base_url", None),
        )

        # Decoder
        decoder = InstructionDecoder()

        # Tool schemas
        schema_format = "anthropic" if self.config.provider == "anthropic" else "openai"
        tool_schemas = self._registry.get_schemas(format=schema_format)

        # VCPU (with steering callback)
        vcpu_config = VCPUConfig(
            max_iterations=self.config.max_iterations,
            max_consecutive_thoughts=self.config.max_consecutive_thoughts,
            llm_call_timeout=self.config.llm_call_timeout,
            contract_mode=self.config.contract_mode,
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
            get_steering=drain_steering,
            initial_state=initial_vcpu_state,
            on_text_delta=self._on_text_delta,
        )

        # Bind VCPU to the mutable ref so tool_executor can inject it
        vcpu_ref[0] = vcpu

        # Loop (with both queues and abort event)
        loop_config = LoopConfig(max_compactions=self.config.max_compactions)
        return RuntimeLoop(
            vcpu=vcpu,
            mmu=mmu,
            config=loop_config,
            event_callback=self._event_cb,
            adapter=self._adapter,
            steering_queue=steering_queue,
            followup_queue=followup_queue,
            abort_event=abort_event,
            session_id=session_id,
            storage=storage,
            metadata=metadata,
        )

    # --- Registry access ---

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def register_tool(self, func: Callable) -> None:
        """Register a @tool-decorated function."""
        self._registry.register_decorated(func)
