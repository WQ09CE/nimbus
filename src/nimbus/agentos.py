"""
Nimbus v2 AgentOS - The Top-Level Integration Layer

AgentOS is the unified entry point for the Nimbus v2 system.
It orchestrates all components: VCPU, MMU, Gate, Scheduler, Decoder.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Literal, Optional

from loguru import logger

if TYPE_CHECKING:
    pass

from nimbus.core.compaction import (
    CompactionConfig,
    CompactionEngine,
    DefaultCompactionLLM,
)
from nimbus.core.compaction_service import CompactionService
from nimbus.core.memory.context import PinnedContext
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.profile import AgentProfile  # NEW
from nimbus.core.models.manifest import get_model_manifest
from nimbus.core.models.registry import ModelRegistry
from nimbus.core.protocol import Event, Fault, ToolResult
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, VCPUConfig

# Session and Compaction
from nimbus.core.session import SessionManager
from nimbus.core.heart import Heart, HeartConfig, HeartMessage
from nimbus.core.heart_modules.session_monitor import SessionMonitorModule
from nimbus.core.heart_modules.memory import MemoryManagerModule
from nimbus.core.heart_modules.memory_consolidator import MemoryConsolidatorModule
from nimbus.os.gate import (
    KernelGate,
    SimpleEventStream,
)
from nimbus.skills.manager import SkillManager
from nimbus.tools.base import ToolDefinition, ToolParameter, ToolRegistry
# memo_tools (Memo/Recall/ReadMemo) are now registered via the global ToolRegistry
from nimbus.tools.composite import CompositeToolRegistry
from nimbus.core.ipc.mailbox import Mailbox
from nimbus.core.process.manager import ProcessManager
from nimbus.core.session.coordinator import SessionCoordinator
from nimbus.core.nimfs.gc import NimFSGC


# =============================================================================
# AgentOS Configuration
# =============================================================================


@dataclass
class OSConfig:
    """Configuration for AgentOS (alias for AgentOSConfig for backward compatibility)"""

    workspace_root: str = "."
    max_processes: int = 10


@dataclass
class AgentOSConfig:
    """Configuration for AgentOS."""

    max_processes: int = 10
    default_timeout: float = 300.0
    vcpu_config: VCPUConfig = field(default_factory=VCPUConfig)
    mmu_config: MMUConfig = field(default_factory=MMUConfig)
    # Kernel tools
    kernel_tools: bool = True
    # System prompt — centralized in prompts.py
    system_rules: str = ""

    def __post_init__(self):
        if not self.system_rules:
            from nimbus.orchestration.prompts import AGENTOS_SYSTEM_RULES
            self.system_rules = AGENTOS_SYSTEM_RULES
    workspace_info: str = ""
    capabilities: str = ""
    # Session persistence
    session_dir: Optional[Path] = None  # None = ephemeral mode
    enable_session: bool = True
    # Compaction
    # Compaction
    compaction_config: CompactionConfig = field(default_factory=CompactionConfig)
    # Skill System
    skill_paths: List[Path] = field(default_factory=list)


# =============================================================================
# Process State
# =============================================================================

ProcessState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


@dataclass
class Process:
    """A process managed by AgentOS."""

    pid: str
    goal: str
    role: str = ""              # Kept as pure label (logging/UI)
    is_interactive: bool = False  # Interactive session (replaces role=="chat")
    text_is_final: bool = True    # Pure text = final reply (replaces decoder role check)
    state: ProcessState = "PENDING"
    vcpu: Optional[VCPU] = None
    mmu: Optional[MMU] = None
    gate: Optional[KernelGate] = None
    result: Optional[ToolResult] = None
    task: Optional[asyncio.Task] = None
    inbox: Mailbox = field(default_factory=list)
    outbox: Mailbox = field(default_factory=list)
    signals: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)


# =============================================================================
# AgentOS
# =============================================================================


class AgentOS:
    """Agent Operating System - The Top-Level Integration Layer."""

    def __init__(
        self,
        llm_client: Any,
        tools: Optional[Dict[str, Callable]] = None,
        config: Optional[AgentOSConfig] = None,
    ):
        self._llm = llm_client
        self.config = config or AgentOSConfig()

        # Tool registry (Core + Extension)
        self._tools = ToolRegistry()

        # Skill Tool registry (Skills only - for easy hot reloading)
        self._skill_tools = ToolRegistry()
        
        # Unified view (Core/Ext + Skills)
        self._composite_tools = CompositeToolRegistry([self._tools, self._skill_tools])

        # Skill Manager
        self._skill_manager = SkillManager(self.config.skill_paths)
        self._skill_manager.load_all()
        self._skill_tool_names: List[str] = []

        # Register Skill Tools
        self._register_skill_tools()

        # Register ReloadSkills tool
        from nimbus.tools.base import ToolDefinition, ToolParameter

        async def reload_skills_wrapper(**kwargs):
            return self.reload_skills(**kwargs)

        reload_def = ToolDefinition(
            name="ReloadSkills",
            description="Reload all skills from disk. Use this after creating or modifying skills to make them available immediately.",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Optional: additional skill directory path to add and scan (e.g. 'skills' or '/absolute/path/to/skills')",
                    required=False,
                ),
            ],
            category="extension",
        )
        self._tools.register(reload_def, reload_skills_wrapper)

        # 2. Register User Tools
        if tools:
            from nimbus.tools.base import ToolDefinition

            for name, func in tools.items():
                try:
                    if hasattr(func, "_tool_definition"):
                        self._tools.register_decorated(func)
                    else:
                        definition = ToolDefinition(
                            name=name,
                            description=func.__doc__ or "No description",
                            parameters=[],
                        )
                        self._tools.register(definition, func)
                except ValueError:
                    pass

        self._processes: Dict[str, Process] = {}
        self._events = SimpleEventStream()
        self._current_session_id: Optional[str] = None

        if self.config.enable_session and self.config.session_dir:
            self._session_mgr: Optional[SessionManager] = SessionManager(
                session_dir=self.config.session_dir
            )
        else:
            self._session_mgr = None

        self._compaction_engine = CompactionEngine(
            config=self.config.compaction_config,
            llm=DefaultCompactionLLM(llm_client),
        )

        self._compaction_service = CompactionService(
            llm=self._llm,
            config=self.config,
            compaction_engine=self._compaction_engine,
            emit_event_fn=self._emit_event,
            session_mgr=self._session_mgr,
        )

        # Process Factory (unified component assembly)
        from nimbus.core.process.factory import ProcessFactory
        self._factory = ProcessFactory(
            llm=self._llm,
            config=self.config,
            composite_tools=self._composite_tools,
            events=self._events,
            create_gate_fn=self._create_gate,
        )

        # Initialize Heart daemon
        self.heart = Heart(
            HeartConfig(
                workspace=getattr(self.config, "workspace_root", "."),
                project_id="nimbus",
                tick_interval=1.0,
            )
        )
        self.heart.add_module(SessionMonitorModule(error_threshold=3))
        self.heart.add_module(MemoryManagerModule(llm_client=self._llm))
        self.heart.add_module(MemoryConsolidatorModule(llm_client=self._llm))
        self._heart_task: Optional[asyncio.Task] = None
        self._intervention_task: Optional[asyncio.Task] = None
        # --- Managers ---
        self.process_manager = ProcessManager(self)
        self.session_coordinator = SessionCoordinator(self)
        self.nimfs_gc = NimFSGC(self)


    def _ensure_heart_running(self) -> None:
        """Start the Heart background task if not already running."""
        if self._heart_task is None or self._heart_task.done():
            self._heart_task = asyncio.create_task(self.heart.start())
        if self._intervention_task is None or self._intervention_task.done():
            self._intervention_task = asyncio.create_task(self._handle_interventions())

    # =========================================================================
    # Event Management
    # =========================================================================

    def add_event_listener(self, listener: Callable[[Event], Any]) -> None:
        """Add an event listener to the OS global event stream."""
        self._events.add_listener(listener)

    def remove_event_listener(self, listener: Callable[[Event], Any]) -> None:
        """Remove an event listener from the OS global event stream."""
        self._events.remove_listener(listener)

    def clear_events(self) -> None:
        """Clear all buffered events from the OS global event stream."""
        self._events.clear()

    async def _handle_interventions(self):
        """Monitor Heart for intervention signals."""
        while True:
            msg: HeartMessage = await self.heart.outbox.get()
            if msg.topic == "system.intervention":
                payload = msg.payload
                itype = payload.get("type")
                sid = payload.get("session_id")
                
                logger.warning(f"[AgentOS] Intervention triggered: {itype} for {sid}")
                
                # SSE Feedback
                self._emit_event("SYSTEM_INTERVENTION", sid or "global", payload)
                
                if itype == "RATE_LIMIT_EXCEEDED" and sid:
                    # Logic to perturb or interrupt
                    process = self._processes.get(sid)
                    if process and process.state == "RUNNING":
                        logger.info(f"[AgentOS] Perturbing session {sid} due to stall...")
                        # Suggest perturbation to VCPU (if supported)
                        # For now, we inject a system message
                        process.inbox.append("[SYSTEM] Logic stall detected. Retrying with higher randomness...")
                
                elif itype == "LOCK_WATCHDOG":
                    # Global notification
                    logger.error(f"[AgentOS] Critical: {payload.get('reason')}")
            
            elif msg.topic == "system.escalate":
                sid = msg.payload.get("session_id")
                process = self._processes.get(sid)
                if process and process.vcpu:
                    current_model = process.vcpu.manifest.model_id
                    next_model = ModelRegistry.get_next_tier(current_model)
                    if next_model:
                        logger.info(f"[AgentOS] Escalating session {sid}: {current_model} -> {next_model}")
                        
                        # Apply escalation
                        new_manifest = get_model_manifest(next_model)
                        # Preserve role
                        from dataclasses import replace as _dc_replace
                        new_manifest = _dc_replace(new_manifest, role=process.vcpu.manifest.role)
                        
                        process.vcpu.manifest = new_manifest
                        # If the ALU (LLM client) depends on the manifest, we might need to recreate it.
                        # For most adapters in Nimbus, the manifest model_id is used in the next chat call.
                        
                        self._emit_event("MODEL_ESCALATED", sid, {
                            "from": current_model,
                            "to": next_model,
                            "reason": msg.payload.get("reason")
                        })
                    else:
                        logger.warning(f"[AgentOS] Escalation requested for {sid} but already at highest tier.")

            self.heart.outbox.task_done()

    def _register_skill_tools(self) -> None:
        """Register all loaded skill tools."""
        from nimbus.tools.base import ToolDefinition, ToolParameter

        # self._skill_tool_names should be initialized in __init__
        if not hasattr(self, "_skill_tool_names"):
             self._skill_tool_names = []

        # Clear existing skill tools before reloading (Unified Strategy A+)
        # This is safer than individual unregistration
        # We don't need to clear self._tools anymore because skills are in _skill_tools
        # But for backward compat with _skill_tool_names tracking, we'll keep the list clear
        # Actually, with separate registry, we can just clear it!
        # self._skill_tools = ToolRegistry() # Or method to clear
        # But ToolRegistry doesn't have clear(). Let's implement it or just iterate.

        # Reset tracking list
        self._skill_tool_names.clear()

        for tool_name, tool_func in self._skill_manager.tools.items():
            # Skip if already registered skill tool (avoid duplicates in list)
            # Actually with separate registry we don't worry about duplicates with core tools as much
            # but we should check if name conflicts with core tools (Phase 3 requirement)

            tool_inst = self._skill_manager.tools[tool_name]
            def_dict = tool_inst.tool_definition

            params = []
            if "parameters" in def_dict:
                props = def_dict["parameters"].get("properties", {})
                required = def_dict["parameters"].get("required", [])

                for pname, pdef in props.items():
                    params.append(ToolParameter(
                        name=pname,
                        type=pdef.get("type", "string"),
                        description=pdef.get("description", ""),
                        required=(pname in required)
                    ))

            definition = ToolDefinition(
                name=def_dict["name"],
                description=def_dict["description"],
                parameters=params,
                category="skill",  # Phase 1: Tag skills explicitly
            )

            # Register to SKILL registry
            self._skill_tools.register(definition, tool_inst.__call__)
            self._skill_tool_names.append(tool_name)

    def reload_skills(self, **kwargs) -> str:
        """Reload all skills from disk.
        
        This method:
        1. Optionally adds new skill directories.
        2. Unregisters all currently loaded skill tools.
        3. Reloads the SkillManager (re-scans directories).
        4. Registers the new set of skill tools.
        
        Returns:
            Status message describing the reload result.
        """
        # If a path is provided, add it to skill_dirs (avoid duplicates)
        from pathlib import Path
        added_paths = []
        for key in ("path", "paths"):
            val = kwargs.get(key)
            if val:
                if isinstance(val, str):
                    val = [val]
                for p in val:
                    new_dir = Path(p).resolve()
                    if new_dir.is_dir() and new_dir not in [d.resolve() for d in self._skill_manager.skill_dirs]:
                        self._skill_manager.skill_dirs.append(new_dir)
                        added_paths.append(str(new_dir))

        # Unregister current skills
        # Strategy A+: Direct clear of the skill registry
        removed_count = len(self._skill_tools)
        self._skill_tools.clear()
        self._skill_tool_names.clear()

        # Reload manager
        self._skill_manager.reload()

        # Re-register
        self._register_skill_tools()

        msg = f"Reloaded skills. Removed {removed_count} old tools. Loaded {len(self._skill_tool_names)} new tools from {len(self._skill_manager.skills)} skills."
        if added_paths:
            msg += f" Added skill dirs: {added_paths}"
        msg += f" Scanning: {[str(d) for d in self._skill_manager.skill_dirs]}"
        return msg

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        category: str = "custom",
    ) -> None:
        """Register a tool with the unified ToolRegistry (Compatibility layer)."""
        from nimbus.tools.base import ToolDefinition, ToolParameter

        params = []
        if isinstance(parameters, list):
            for pdef in parameters:
                if hasattr(pdef, "name"):
                    params.append(pdef)
                else:
                    params.append(
                        ToolParameter(
                            name=pdef.get("name", "unknown"),
                            type=pdef.get("type", "string"),
                            description=pdef.get("description", ""),
                            required=pdef.get("required", False),
                        )
                    )
        elif isinstance(parameters, dict):
            props = parameters.get("properties", {})
            required = parameters.get("required", [])
            for pname, pdef in props.items():
                params.append(
                    ToolParameter(
                        name=pname,
                        type=pdef.get("type", "string"),
                        description=pdef.get("description", ""),
                        required=(pname in required),
                    )
                )

        definition = ToolDefinition(
            name=name,
            description=description,
            parameters=params,
            category=category,
        )
        self._tools.register(definition, func)

    # =========================================================================
    # Main API
    # =========================================================================


    def _create_gate(
        self,
        pid: str,
        role: str,
        local_tools: Optional[Dict[str, Callable]] = None,
        write_filter: Optional[Callable[[str], bool]] = None
    ) -> "KernelGate":
        # Combine with local tools provided by spawn()
        all_funcs = self._composite_tools.get_all_funcs()
        if local_tools:
            all_funcs.update(local_tools)

        from nimbus.os.gate import KernelGate
        return KernelGate(
            pid=pid,
            tool_executor=self._composite_tools,
            event_stream=self._events,
            default_timeout=self.config.default_timeout,
            local_tools=all_funcs,
            write_filter=write_filter,
        )

    def _emit_event(self, event_type: str, pid: str, data: Dict[str, Any]) -> None:
        self._events.emit(
            Event(
                type=event_type,  # type: ignore
                pid=pid,
                data=data,
            )
        )

    # =========================================================================
    # Process Facade
    # =========================================================================

    def spawn(self, *args, **kwargs):
        return self.process_manager.spawn(*args, **kwargs)

    async def wait(self, *args, **kwargs):
        return await self.process_manager.wait(*args, **kwargs)

    async def wait_all(self, *args, **kwargs):
        return await self.process_manager.wait_all(*args, **kwargs)

    async def run(self, *args, **kwargs):
        return await self.process_manager.run(*args, **kwargs)

    def run_stream(self, *args, **kwargs):
        return self.process_manager.run_stream(*args, **kwargs)

    def terminate(self, *args, **kwargs):
        return self.process_manager.terminate(*args, **kwargs)

    def list_processes(self, *args, **kwargs):
        return self.process_manager.list_processes(*args, **kwargs)

    def get_active_processes(self, *args, **kwargs):
        return self.process_manager.get_active_processes(*args, **kwargs)

    def get_process(self, *args, **kwargs):
        return self.process_manager.get_process(*args, **kwargs)

    def interrupt(self, *args, **kwargs):
        return self.process_manager.interrupt(*args, **kwargs)

    def inject_message(self, *args, **kwargs):
        return self.process_manager.inject_message(*args, **kwargs)

    def _drain_process_inbox(self, *args, **kwargs):
        return self.process_manager._drain_process_inbox(*args, **kwargs)

    async def _run_process(self, *args, **kwargs):
        return await self.process_manager._run_process(*args, **kwargs)

    def _scavenge_partial_result(self, *args, **kwargs):
        return self.process_manager._scavenge_partial_result(*args, **kwargs)

    async def spawn_batch(self, *args, **kwargs):
        return await self.process_manager.spawn_batch(*args, **kwargs)

    # =========================================================================
    # Session Facade
    # =========================================================================

    async def chat(self, *args, **kwargs):
        return await self.session_coordinator.chat(*args, **kwargs)

    def new_session(self, *args, **kwargs):
        return self.session_coordinator.new_session(*args, **kwargs)

    def load_session(self, *args, **kwargs):
        return self.session_coordinator.load_session(*args, **kwargs)

    def restore_session(self, *args, **kwargs):
        return self.session_coordinator.restore_session(*args, **kwargs)

    def get_session_stats(self, *args, **kwargs):
        return self.session_coordinator.get_session_stats(*args, **kwargs)

    def list_recent_sessions(self, *args, **kwargs):
        return self.session_coordinator.list_recent_sessions(*args, **kwargs)

    def get_session(self, *args, **kwargs):
        return self.session_coordinator.get_session(*args, **kwargs)

    def end_session(self, *args, **kwargs):
        return self.session_coordinator.end_session(*args, **kwargs)

    # =========================================================================
    # Other internal proxies
    # =========================================================================
    
    async def compact(self, *args, **kwargs):
        return await self._compaction_service.compact(*args, **kwargs)

    async def _check_compaction(self, *args, **kwargs):
        return await self._compaction_service.check_compaction(*args, **kwargs)

    async def _compaction_for_process(self, *args, **kwargs):
        return await self._compaction_service.compact_process(*args, **kwargs)

    def _nimfs_gc_task(self, *args, **kwargs):
        return self.nimfs_gc._nimfs_gc_task(*args, **kwargs)

    def _nimfs_gc_session(self, *args, **kwargs):
        return self.nimfs_gc._nimfs_gc_session(*args, **kwargs)



# =============================================================================
# Factory Functions (re-exported from nimbus.orchestration.bootstrap)
# =============================================================================

# Re-export for backward compatibility
from nimbus.orchestration.bootstrap import create_agent_os  # noqa: F401
