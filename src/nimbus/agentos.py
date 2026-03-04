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

    def _ensure_heart_running(self) -> None:
        """Start the Heart background task if not already running."""
        if self._heart_task is None or self._heart_task.done():
            self._heart_task = asyncio.create_task(self.heart.start())
        if self._intervention_task is None or self._intervention_task.done():
            self._intervention_task = asyncio.create_task(self._handle_interventions())

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

    # =========================================================================
    # Main API
    # =========================================================================

    def spawn(
        self,
        goal: str,
        role: str = "",
        system_rules: Optional[str] = None,
        max_iterations: Optional[int] = None,
        llm_client: Optional[Any] = None,
        tools_override: Optional[List] = None,
        profile: Optional[AgentProfile] = None,  # NEW
    ) -> str:
        """
        Spawn a new process with the given goal and role.
        
        Args:
            goal: The goal/task for the process
            role: Optional role identifier for the process
            system_rules: Optional custom system rules (overrides kernel default)
            max_iterations: Optional max VCPU iterations (overrides kernel default)
            llm_client: Optional LLM client for this process (overrides kernel default)
            tools_override: Optional explicit tools list. None=inherit from kernel, []=no tools (pure reasoning)
            profile: Optional AgentProfile to configure the process (recommended for v2)
            
        Returns:
            Process ID (pid) of the spawned process
        """
        # Generate unique process ID
        pid = f"proc-{uuid.uuid4().hex[:8]}"

        # --- Profile Resolution ---
        _role = role
        _system_rules = system_rules
        _max_iterations = max_iterations
        _tools_filter = tools_override

        if profile:
            _role = profile.role or _role
            _system_rules = profile.system_prompt or _system_rules
            _max_iterations = profile.max_iterations or _max_iterations
            # If profile defines allowed tools, treat them as a filter/override
            # Note: This logic assumes tools are already registered in kernel.
            # Phase 3 will handle dynamic tool loading from profile.
            if profile.allowed_tools:
                _tools_filter = profile.allowed_tools

        # Default to "standard" if no role was resolved from profile or caller
        if not _role:
            _role = "standard"

        # Extract write_filter from profile
        _write_filter = None
        if profile and profile.write_filter:
            _write_filter = profile.write_filter

        # Extract is_interactive / text_is_final from profile
        _is_interactive = False
        _text_is_final = True
        if profile:
            _is_interactive = profile.is_interactive
            _text_is_final = profile.text_is_final

        # Create process via factory (unified component assembly)
        process = self._factory.build(
            pid=pid,
            goal=goal,
            role=_role,
            system_rules=_system_rules,
            llm_client=llm_client,
            profile=profile,
            tools_override=_tools_filter,
            max_iterations=_max_iterations,
            write_filter=_write_filter,
            enable_ipc=True,
            agent_os=self,
            is_interactive=_is_interactive,
            text_is_final=_text_is_final,
        )

        # Register process
        self._processes[pid] = process

        # Emit spawn event
        manifest = process.vcpu.manifest
        _manifest_model = getattr(manifest, "model_id", "") or ""
        _model_short = _manifest_model.split("/")[-1] if "/" in _manifest_model else _manifest_model

        self._emit_event("PROC_SPAWNED", pid, {"goal": goal, "role": _role, "model": _model_short, "model_full": _manifest_model})

        return pid

    async def wait(self, pid: str, timeout: Optional[float] = None) -> ToolResult:
        """Wait for a process to complete and return its result.

        Args:
            pid: Process ID to wait for
            timeout: Optional timeout in seconds

        Returns:
            ToolResult from the process

        Raises:
            RuntimeError: If process not found
        """
        process = self._processes.get(pid)
        if not process:
            raise RuntimeError(f"Process {pid} not found")

        def _timeout_result() -> ToolResult:
            process.state = "CANCELLED"
            # Get last messages for post-mortem
            post_mortem = []
            if process.mmu:
                try:
                    post_mortem = process.mmu.get_last_messages(3)
                except Exception:
                    pass
                    
            return ToolResult(
                status="TIMEOUT",
                output={"post_mortem": post_mortem},
                is_final=True,
                fault=Fault(
                    domain="KERNEL",
                    code="TIMEOUT",
                    message=f"Process {pid} timed out after {timeout}s",
                ),
            )

        async def _run_with_soft_timeout(coro):
            """Two-phase timeout: soft signal at 85%, hard kill in remaining 15%."""
            if not timeout:
                return await coro

            # Phase durations
            t_soft = timeout * 0.85
            t_finalize = min(max(timeout * 0.15, 30.0), 60.0)

            task = asyncio.ensure_future(coro)

            # Phase 1: wait for soft timeout
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=t_soft)
            except asyncio.TimeoutError:
                # Signal the vCPU to finalize with partial results
                if process.vcpu:
                    process.vcpu.signals["soft_timeout"] = True
                # The process.signals dict is removed, so this line is removed.
                # process.signals["soft_timeout"] = True
                logger.warning(
                    f"Process {pid} hit soft timeout at {t_soft:.0f}s, "
                    f"giving {t_finalize:.0f}s to finalize"
                )

            # Phase 2: wait for finalize window
            try:
                return await asyncio.wait_for(task, timeout=t_finalize)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                raise asyncio.TimeoutError()

        # If process is pending, start it
        if process.state == "PENDING":
            self._ensure_heart_running()
            process.state = "RUNNING"
            try:
                return await _run_with_soft_timeout(self._run_process(process))
            except asyncio.TimeoutError:
                return _timeout_result()

        # If process is already running with a task, wait for it
        if process.task and not process.task.done():
            try:
                return await _run_with_soft_timeout(process.task)
            except asyncio.TimeoutError:
                return _timeout_result()

        # If process is completed, return the result
        if process.state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return process.result or ToolResult(status="OK")

        # Otherwise, run the process
        process.state = "RUNNING"
        try:
            return await _run_with_soft_timeout(self._run_process(process))
        except asyncio.TimeoutError:
            return _timeout_result()

    async def wait_all(
        self,
        pids: List[str],
        timeout: Optional[float] = None,
    ) -> Dict[str, "ToolResult"]:
        """
        Wait for multiple processes to complete in parallel.

        Args:
            pids: List of process IDs to wait for
            timeout: Optional timeout in seconds (applies to each process)

        Returns:
            Dict mapping pid -> ToolResult
        """
        from nimbus.core.protocol import Fault

        async def _safe_wait(pid: str) -> tuple:
            try:
                result = await self.wait(pid, timeout=timeout)
                return pid, result
            except Exception as e:
                return pid, ToolResult(
                    status="ERROR",
                    fault=Fault(domain="KERNEL", code="WAIT_FAIL", message=str(e)),
                )

        tasks = [_safe_wait(pid) for pid in pids]
        completed = await asyncio.gather(*tasks)
        return dict(completed)

    async def run(self, goal: str, role: str = "") -> ToolResult:
        pid = self.spawn(goal, role=role)
        return await self.wait(pid)

    async def run_stream(self, goal: str, role: str = ""):
        from nimbus.core.process.loop import RuntimeLoop

        pid = self.spawn(goal, role=role)
        process = self._processes.get(pid)
        if not process:
            yield {"type": "error", "message": f"Process {pid} not found"}
            return

        async def transform_context_hook(ctx) -> None:
            self._drain_process_inbox(process)
            await self._check_compaction(process)

        if process.vcpu:
            process.vcpu.transform_context_hook = transform_context_hook

        loop = RuntimeLoop(
            process=process,
            compaction_fn=self._compaction_for_process,
            check_compaction_fn=self._check_compaction,
            heart=self.heart,
            emit_event_fn=self._emit_event,
            nimfs_gc_fn=self._nimfs_gc_task,
            scavenge_fn=self._scavenge_partial_result,
        )
        async for event in loop.stream():
            yield event

    async def chat(self, message: "str | list", session_id: str | None = None) -> ToolResult:
        from loguru import logger
        is_existing_process = False
        if session_id and session_id in self._processes:
            process = self._processes[session_id]
            is_existing_process = True
        else:
            if not session_id:
                session_id = f"chat-{uuid.uuid4().hex[:8]}"

            # Create process via factory (unified component assembly)
            process = self._factory.build(
                pid=session_id,
                goal="Interactive chat session",
                role="chat",
                is_interactive=True,
                text_is_final=True,
            )
            self._processes[session_id] = process
            self._emit_event("PROC_SPAWNED", session_id, {"goal": "chat", "role": "chat"})

        if is_existing_process and process.state == "RUNNING":
            logger.info(f"Process {session_id} is busy. Converting chat to injection.")
            self.inject_message(process.pid, message)

            if self._session_mgr:
                from nimbus.core.memory.context import Message

                self._session_mgr.append_message(Message(role="user", content=message))

            return ToolResult(
                status="OK", output="[Instruction appended to running task]", is_final=True
            )

        # --- AUTO RECALL INJECTION ---
        try:
            search_query = message if isinstance(message, str) else "\n".join([p.get("text", "") for p in message if isinstance(p, dict) and p.get("type", "") == "text"])
            if search_query and len(search_query.strip()) > 5 and hasattr(self.heart, "nimfs"):
                results = self.heart.nimfs.search_memory(query=search_query, top_k=3, scope="project")
                if results:
                    recall_text = "# 🧠 RELEVANT PAST MEMORY\n"
                    added = 0
                    for entry in results:
                        try:
                            abstract = self.heart.nimfs.read_memory(entry.memory_id, layer=1)
                            if abstract:
                                recall_text += f"## {entry.title}\n{abstract}\n\n"
                                added += 1
                        except Exception:
                            pass
                    
                    if added > 0:
                        process.mmu.add_user_message(recall_text.strip())
                        logger.info(f"[{session_id}] Auto-Recalled {added} past memories into context.")
        except Exception as e:
            logger.warning(f"[{session_id}] Auto-Recall failed non-fatally: {e}")
        # -----------------------------

        process.vcpu._reset()

        if self._session_mgr:
            from nimbus.core.memory.context import Message

            self._session_mgr.append_message(Message(role="user", content=message))

        if process.state == "RUNNING":
            self.inject_message(process.pid, message)
        else:
            # Process not RUNNING — add message directly to MMU
            process.mmu.add_user_message(message)

            # Start Execution
            process.state = "RUNNING"
            process.interrupt_event.clear()  # Clear stale signal
            logger.info(f"[{session_id}] State transition: RUNNING")
            return await self._run_process(process)

        return ToolResult(status="OK", output="[Already Running]")

    def terminate(self, pid: str, reason: str = "manual_terminate") -> bool:
        """
        Safely stop a running process.
        """
        process = self._processes.get(pid)
        if not process:
            return False

        if process.state == "RUNNING":
            process.state = "CANCELLED"
            if process.vcpu:
                process.vcpu.signals["hard_timeout"] = True
            
            if process.task and not process.task.done():
                process.task.cancel()
            
            logger.info(f"[AgentOS] Process {pid} terminated. Reason: {reason}")
            self._emit_event("PROC_TERMINATED", pid, {"reason": reason})
            return True
        return False

    # =========================================================================
    # Process Management
    # =========================================================================

    def list_processes(self) -> List[str]:
        """List all active process IDs."""
        return list(self._processes.keys())

    def get_active_processes(self) -> List[str]:
        """Get list of currently running process IDs."""
        return [pid for pid, p in self._processes.items() if p.state == "RUNNING"]

    def get_process(self, pid: str) -> "Process | None":
        """Get a process by ID."""
        return self._processes.get(pid)

    def get_session(self, session_id: str) -> "Process | None":
        return self._processes.get(session_id)

    def end_session(self, session_id: str) -> None:
        if session_id in self._processes:
            process = self._processes.pop(session_id)
            process.state = "COMPLETED"
            self._emit_event("PROC_FINISHED", session_id, {"reason": "session_ended"})
            # NimFS GC: clean up SESSION-level artifacts when session ends
            self._nimfs_gc_session(process)

    # =========================================================================
    # NimFS GC Helpers
    # =========================================================================

    def _nimfs_gc_task(self, process: "Process") -> None:
        """
        Clean up TASK-level NimFS artifacts after a sub-process finishes.
        Called from _run_process() on normal completion.
        Runs silently — any error is swallowed to avoid disrupting the main flow.
        """
        try:
            workspace = getattr(process.mmu, "nimfs_workspace", None) if process.mmu else None
            if not workspace:
                workspace = str(Path.cwd())
            from nimbus.core.nimfs.gc import NimFSGC
            from nimbus.core.nimfs.models import ArtifactTTL
            NimFSGC().gc_artifacts(workspace, ttl_level=ArtifactTTL.TASK)
        except Exception:
            pass

    def _nimfs_gc_session(self, process: "Process") -> None:
        """
        Clean up SESSION-level (and TASK-level) NimFS artifacts when a session ends.
        Called from end_session().
        Runs silently — any error is swallowed to avoid disrupting the main flow.
        """
        try:
            workspace = getattr(process.mmu, "nimfs_workspace", None) if process.mmu else None
            if not workspace:
                workspace = str(Path.cwd())
            from nimbus.core.nimfs.gc import NimFSGC
            NimFSGC().gc_session(workspace)
        except Exception:
            pass

    # =========================================================================
    # Session Management (NEW)
    # =========================================================================

    def restore_session(self, session_id: str, checkpoint: Any) -> None:
        """
        Restore a session process from a checkpoint.

        Args:
            session_id: The session ID (used as PID)
            checkpoint: The SessionCheckpointModel object
        """
        # Create process via factory (unified component assembly)
        process = self._factory.build(
            pid=session_id,
            goal="Restored session",
            role="chat",
            checkpoint=checkpoint,
            is_interactive=True,
            text_is_final=True,
        )
        self._processes[session_id] = process

        from nimbus.core.logging import get_logger
        logger = get_logger("kernel.agentos")
        logger.info(f"♻️ Restored process {session_id} from checkpoint")

    def new_session(self, parent_session: Optional[str] = None) -> str:
        if self._session_mgr:
            return self._session_mgr.new_session(parent_session)
        return f"ephemeral-{uuid.uuid4().hex[:8]}"

    def load_session(self, session_file: Path) -> bool:
        if not self._session_mgr:
            return False
        return self._session_mgr.load_session(session_file)

    def get_session_stats(self) -> Optional[Dict[str, Any]]:
        if self._session_mgr:
            return self._session_mgr.get_stats()
        return None

    def list_recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        if self._session_mgr:
            return self._session_mgr.list_recent_sessions(limit)
        return []

    # =========================================================================
    # Compaction (NEW)
    # =========================================================================

    async def compact(
        self,
        session_id: Optional[str] = None,
        custom_instructions: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        target_session = session_id or self._current_session_id
        if not target_session:
            return None
        process = self._processes.get(target_session)
        if not process or not process.mmu:
            return None
        return await self._compaction_service.compact(process, custom_instructions)

    async def _check_compaction(self, process: Process) -> None:
        """Proactive auto-compaction (delegated to CompactionService)."""
        await self._compaction_service.check_compaction(process)

    async def _compaction_for_process(self, pid: str, mmu: MMU) -> bool:
        """Process-level compaction (delegated to CompactionService)."""
        return await self._compaction_service.compact_process(pid, mmu)

    def interrupt(self, session_id: Optional[str] = None) -> bool:
        signalled = False

        targets = []
        if session_id:
            targets.append(session_id)
        else:
            targets = list(self._processes.keys())

        for pid in targets:
            process = self._processes.get(pid)
            if process and process.state == "RUNNING":
                process.interrupt_event.set()
                signalled = True
                logger.info(f"[{pid}] Interrupt event set")

        return signalled

    def inject_message(self, pid: str, message: "str | list") -> bool:
        """Inject a user message into a running process's inbox.

        Returns True if message was accepted (process is RUNNING and will consume it).
        Returns False if process doesn't exist or is not RUNNING.
        When False is returned, inbox is NOT modified — caller is responsible for
        persisting the message via storage/MMU directly.
        """
        process = self._processes.get(pid)
        if not process:
            return False

        if process.state != "RUNNING":
            logger.warning(f"[{pid}] Process not running (state={process.state}), cannot inject")
            return False

        if isinstance(message, str):
            # Backwards compatibility: Wrap simple strings as 'user' interventions
            from nimbus.core.ipc.message import IPCMessage
            msg_obj = IPCMessage(
                sender_pid="user",
                target_pid=pid,
                type="event",
                payload={"content": message}
            )
            process.inbox.append(msg_obj)
        else:
            # Full IPCMessage injection
            process.inbox.append(message)

        preview = str(message)[:50]
        logger.info(f"[{pid}] Message injected into inbox: {preview}...")
        return True

    def _drain_process_inbox(self, process: Process) -> None:
        """Drain inbox messages (IPC messages + plain strings).

        Inbox may be a Mailbox (from spawn) or list (from chat) -- duck typed.
        """
        import logging
        logger = logging.getLogger("kernel.os")

        while process.inbox:
            if hasattr(process.inbox, "qsize"):
                # Mailbox (from spawn)
                if process.inbox.qsize() == 0:
                    break
                try:
                    msg = process.inbox._queue.get_nowait()
                except Exception:
                    break
            else:
                # List (from chat)
                msg = process.inbox.pop(0)

            if not msg:
                break

            # Handle both IPCMessage objects and plain strings
            if hasattr(msg, "type") and hasattr(msg, "payload"):
                # IPCMessage from inject_message() or IPC system
                if msg.type == "request":
                    task_goal = msg.payload.get("goal", "")
                    process.mmu.add_user_message(f"[Task Assignment] {task_goal}")
                elif msg.type == "response":
                    result_data = msg.payload.get("result", "")
                    process.mmu.add_user_message(
                        f"[Sub-Agent Result from {msg.sender_pid}] {result_data}"
                    )
                else:
                    content = msg.payload.get("content", str(msg.payload))
                    if process.is_interactive:
                        process.mmu.add_user_message(content)
                    else:
                        process.mmu.add_user_message(f"[User Intervention] {content}")

                self._emit_event(
                    "USER_INTERVENTION", process.pid, {"content": str(msg.payload)}
                )
                logger.info(
                    f"[{process.pid}] Processed inbox message {msg.id} from {msg.sender_pid}"
                )
            else:
                # Plain string from _handle_interventions() or legacy code
                content = str(msg)
                if process.is_interactive:
                    process.mmu.add_user_message(content)
                else:
                    process.mmu.add_user_message(f"[User Intervention] {content}")
                self._emit_event(
                    "USER_INTERVENTION", process.pid, {"content": content}
                )
                logger.info(
                    f"[{process.pid}] Processed inbox string message: {content[:50]}..."
                )

    async def _run_process(self, process: Process) -> ToolResult:
        from nimbus.core.process.loop import RuntimeLoop

        self._ensure_heart_running()

        async def transform_context_hook(ctx) -> None:
            self._drain_process_inbox(process)
            await self._check_compaction(process)

        if process.vcpu:
            process.vcpu.transform_context_hook = transform_context_hook

        loop = RuntimeLoop(
            process=process,
            compaction_fn=self._compaction_for_process,
            check_compaction_fn=self._check_compaction,
            heart=self.heart,
            emit_event_fn=self._emit_event,
            nimfs_gc_fn=self._nimfs_gc_task,
            scavenge_fn=self._scavenge_partial_result,
        )
        return await loop.run()

    # =========================================================================
    # Parallel Dispatch & Scavenging
    # =========================================================================

    def _scavenge_partial_result(self, process: Process) -> ToolResult:
        """
        Scavenge partial results from a timed-out process before it is destroyed.

        Accesses the process's current MMU frame, extracts any internal monologue
        (assistant messages) and artifact references produced so far, and packages
        them into a ToolResult with ``is_partial=True`` so callers can distinguish
        salvaged data from a normal completion.

        Also implements 'Post-Mortem' analysis: captures last 3 messages as diagnosis context.

        Args:
            process: The timed-out Process whose frame will be inspected.

        Returns:
            ToolResult with status="TIMEOUT", is_partial=True, and whatever
            partial data could be recovered.
        """
        try:
            frame = process.mmu.current_frame if process.mmu else None
            internal_monologue: List[str] = []
            artifacts = []
            post_mortem_context = []

            if frame is not None:
                # Post-Mortem: capture last 3 messages
                all_msgs = frame.messages
                last_msgs = all_msgs[-3:] if len(all_msgs) >= 3 else all_msgs
                for m in last_msgs:
                    post_mortem_context.append({
                        "role": m.role,
                        "content": str(m.content)[:500] + "..." if m.content and len(str(m.content)) > 500 else m.content
                    })

                for msg in frame.messages:
                    if msg.role == "assistant" and msg.content:
                        internal_monologue.append(msg.content)
                    # Collect any artifact refs stored in message metadata
                    if hasattr(msg, "meta") and msg.meta:
                        for ref in msg.meta.get("artifacts", []):
                            artifacts.append(ref)

            partial_output = {
                "is_partial": True,
                "pid": process.pid,
                "goal": process.goal,
                "internal_monologue": internal_monologue,
                "salvaged_artifacts": artifacts,
                "frame_id": frame.frame_id if frame else None,
                "post_mortem": post_mortem_context
            }

            logger.info(
                f"[scavenge] PID={process.pid} salvaged "
                f"{len(internal_monologue)} thought(s), "
                f"{len(artifacts)} artifact(s)."
            )

            return ToolResult(
                status="TIMEOUT",
                output=partial_output,
                is_final=False,
                fault=Fault(
                    domain="KERNEL",
                    code="TIMEOUT",
                    message=f"Process {process.pid} timed out; post-mortem diagnostics attached.",
                    retryable=True,
                ),
            )

        except Exception as exc:  # pragma: no cover – best-effort scavenge
            logger.warning(f"[scavenge] Failed to salvage partial result for PID={process.pid}: {exc}")
            return ToolResult(
                status="TIMEOUT",
                output={"is_partial": True, "pid": process.pid, "goal": process.goal},
                is_final=False,
                fault=Fault(
                    domain="KERNEL",
                    code="TIMEOUT",
                    message=f"Process {process.pid} timed out; scavenge also failed: {exc}",
                    retryable=True,
                ),
            )

    async def spawn_batch(
        self,
        tasks: List[Dict[str, Any]],
        timeout: Optional[float] = None,
        strategy: Literal["wait_all", "wait_any", "wait_threshold"] = "wait_all",
        threshold: float = 0.6,
        parent_action_id: Optional[str] = None,
        specialist_names: Optional[List[str]] = None,
        original_indices: Optional[List[int]] = None,
    ) -> List[ToolResult]:
        """
        Spawn and run multiple sub-processes concurrently (parallel dispatch).

        Each entry in *tasks* is a dict accepted by :meth:`spawn` – at minimum
        it must contain a ``"goal"`` key.  Every process is given its own
        ``sub_session_id`` (equal to its ``pid``) so SSE events can be filtered
        per sub-task.

        Args:
            tasks:    List of task dicts.  Supported keys mirror :meth:`spawn`
                      parameters: ``goal``, ``role``, ``system_rules``,
                      ``max_iterations``, ``llm_client``, ``tools_override``,
                      ``profile``.
            timeout:  Per-task wall-clock timeout in seconds.  When a task
                      exceeds this limit ``asyncio.wait_for`` raises
                      ``asyncio.TimeoutError`` which triggers
                      :meth:`_scavenge_partial_result` inside
                      :meth:`_run_process`.
            strategy: Aggregation strategy:
                      - ``"wait_all"``       – wait for every task (default).
                      - ``"wait_any"``       – return as soon as the first
                        task finishes; cancel the rest.
                      - ``"wait_threshold"`` – return when at least
                        ``threshold`` fraction of tasks have finished.
            threshold: Fraction of tasks that must complete for
                       ``"wait_threshold"`` strategy (default 0.6).

        Returns:
            List of :class:`ToolResult` in the same order as *tasks*.
            Failed / timed-out entries contain the salvaged partial result.
        """
        if not tasks:
            return []

        batch_id = f"batch-{uuid.uuid4().hex[:8]}"
        logger.info(
            f"[spawn_batch] batch_id={batch_id} launching {len(tasks)} tasks "
            f"strategy={strategy} timeout={timeout}"
        )

        # 1. Spawn all processes (synchronous, non-blocking).
        pids: List[str] = []
        for i, task_spec in enumerate(tasks):
            goal = task_spec.get("goal", "")
            if not goal:
                raise ValueError(f"tasks[{i}] is missing required 'goal' key")

            pid = self.spawn(
                goal=goal,
                role=task_spec.get("role", ""),
                system_rules=task_spec.get("system_rules"),
                max_iterations=task_spec.get("max_iterations"),
                llm_client=task_spec.get("llm_client"),
                tools_override=task_spec.get("tools_override"),
                profile=task_spec.get("profile"),
            )
            pids.append(pid)
            # Tag the process so SSE listeners can filter by sub_session_id
            proc = self._processes[pid]
            proc.signals["batch_id"] = batch_id          # type: ignore[assignment]
            proc.signals["sub_session_id"] = pid         # type: ignore[assignment]
            # Batch routing metadata for frontend (parallel native calls)
            if parent_action_id:
                proc.signals["parent_action_id"] = parent_action_id  # type: ignore[assignment]
            slot_index = original_indices[i] if original_indices and i < len(original_indices) else i
            proc.signals["batch_slot_index"] = slot_index  # type: ignore[assignment]
            if specialist_names and i < len(specialist_names):
                proc.signals["specialist"] = specialist_names[i]  # type: ignore[assignment]
            self._emit_event(
                "BATCH_TASK_SPAWNED",
                pid,
                {"batch_id": batch_id, "sub_session_id": pid, "index": i, "goal": goal},
            )

        # 2. Build coroutines with per-task timeout wrapping.
        async def _run_with_timeout(pid: str) -> ToolResult:
            proc = self._processes[pid]
            coro = self._run_process(proc)
            if timeout is not None:
                try:
                    return await asyncio.wait_for(coro, timeout=timeout)
                except asyncio.TimeoutError:
                    # asyncio.wait_for cancelled the inner coroutine; scavenge now.
                    proc.state = "TIMEOUT"
                    partial_result = self._scavenge_partial_result(proc)
                    asyncio.create_task(self.heart.inbox.put(
                        topic="session.timeout",
                        payload={
                            "session_id": pid,
                            "error": "Process timed out (spawn_batch)",
                            "partial_salvaged": partial_result.output is not None,
                        },
                    ))
                    self._emit_event(
                        "PROC_TIMEOUT",
                        pid,
                        {"batch_id": batch_id, "partial_salvaged": partial_result.output is not None},
                    )
                    proc.result = partial_result
                    return partial_result
            return await coro

        coroutines = [_run_with_timeout(pid) for pid in pids]

        # 3. Execute according to strategy.
        results: List[Optional[ToolResult]] = [None] * len(pids)

        if strategy == "wait_any":
            # asyncio.wait FIRST_COMPLETED – cancel remaining tasks
            aws = {asyncio.ensure_future(c): i for i, c in enumerate(coroutines)}
            done, pending = await asyncio.wait(
                list(aws.keys()), return_when=asyncio.FIRST_COMPLETED
            )
            for fut in done:
                idx = aws[fut]
                try:
                    results[idx] = fut.result()
                except Exception as exc:
                    results[idx] = ToolResult(
                        status="ERROR",
                        fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message=str(exc)),
                    )
            for fut in pending:
                fut.cancel()
                idx = aws[fut]
                results[idx] = ToolResult(
                    status="CANCELLED",
                    fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message="Cancelled by wait_any strategy"),
                )

        elif strategy == "wait_threshold":
            need = max(1, int(len(pids) * threshold))
            aws = {asyncio.ensure_future(c): i for i, c in enumerate(coroutines)}
            completed = 0
            pending = set(aws.keys())
            while completed < need and pending:
                done, pending = await asyncio.wait(
                    list(pending), return_when=asyncio.FIRST_COMPLETED
                )
                for fut in done:
                    idx = aws[fut]
                    completed += 1
                    try:
                        results[idx] = fut.result()
                    except Exception as exc:
                        results[idx] = ToolResult(
                            status="ERROR",
                            fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message=str(exc)),
                        )
            # Cancel remaining if threshold reached
            if completed >= need:
                for fut in pending:
                    fut.cancel()
                    idx = aws[fut]
                    results[idx] = ToolResult(
                        status="CANCELLED",
                        fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message="Cancelled after threshold reached"),
                    )

        else:  # wait_all (default)
            gathered = await asyncio.gather(*coroutines, return_exceptions=True)
            for i, res in enumerate(gathered):
                if isinstance(res, Exception):
                    results[i] = ToolResult(
                        status="ERROR",
                        fault=Fault(domain="KERNEL", code="SYSTEM_ERROR", message=str(res)),
                    )
                else:
                    results[i] = res  # type: ignore[assignment]

        self._emit_event(
            "BATCH_FINISHED",
            batch_id,
            {
                "batch_id": batch_id,
                "total": len(pids),
                "succeeded": sum(1 for r in results if r and r.status == "OK"),
                "failed": sum(1 for r in results if r and r.status in ("ERROR", "TIMEOUT", "CANCELLED")),
            },
        )

        logger.info(
            f"[spawn_batch] batch_id={batch_id} finished. "
            f"results={[r.status if r else 'None' for r in results]}"
        )

        return [r or ToolResult(status="ERROR") for r in results]

    # =========================================================================
    # Tool Management
    # =========================================================================

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        roles: Optional[List[str]] = None,  # Deprecated: kept for backward compat
        category: Optional[str] = None,
    ) -> None:
        """
        Register a tool.

        Args:
            name: Tool name
            func: Tool function
            description: Tool description
            parameters: Tool parameters schema
            roles: Deprecated, ignored. Kept for backward compatibility.
            category: Tool category
        """
        if name in self._tools:
            self._tools.unregister(name)

        if hasattr(func, "_tool_definition"):
            defn = func._tool_definition
            self._tools.register_decorated(func)
        else:
            # Parse parameters from JSON Schema dict if provided
            param_list = []
            if parameters and isinstance(parameters, dict):
                props = parameters.get("properties", {})
                required_params = parameters.get("required", [])
                for pname, pspec in props.items():
                    param_list.append(
                        ToolParameter(
                            name=pname,
                            type=pspec.get("type", "string"),
                            description=pspec.get("description", ""),
                            required=(pname in required_params),
                            enum=pspec.get("enum"),
                            items=pspec.get("items"),
                            properties=pspec.get("properties"),
                        )
                    )

            definition = ToolDefinition(
                name=name,
                description=description or func.__doc__ or "",
                parameters=param_list,
                category=category,
            )
            self._tools.register(definition, func)

    def unregister_tool(self, name: str) -> bool:
        """Unregister a tool."""
        return bool(self._tools.unregister(name))

    def list_tools(self) -> List[str]:
        """List all registered tools."""
        return self._composite_tools.list_tools()

    # =========================================================================
    # Event & State Access
    # =========================================================================

    def add_event_listener(self, listener: Callable[[Event], Any]) -> None:
        """Add a listener for real-time events."""
        if hasattr(self._events, "add_listener"):
            self._events.add_listener(listener)

    def remove_event_listener(self, listener: Callable[[Event], Any]) -> None:
        """Remove an event listener."""
        if hasattr(self._events, "remove_listener"):
            self._events.remove_listener(listener)

    def get_events(self) -> List[Event]:
        """Get all collected events."""
        return self._events.events.copy()

    def clear_events(self) -> None:
        """Clear collected events."""
        self._events.clear()

    def get_state(self) -> Dict[str, Any]:
        """Get AgentOS state for debugging."""
        return {
            "config": {
                "max_processes": self.config.max_processes,
                "default_timeout": self.config.default_timeout,
            },
            "processes": {
                pid: {
                    "goal": p.goal,
                    "role": p.role,
                    "state": p.state,
                }
                for pid, p in self._processes.items()
            },
            "tools": self._tools.list_tools(),
            "event_count": len(self._events.events),
        }

    async def shutdown(self) -> None:
        """Gracefully shut down AgentOS and background tasks."""
        if self._heart_task and not self._heart_task.done():
            self.heart.stop()
            try:
                await asyncio.wait_for(self._heart_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._heart_task.cancel()
            logger.info("AgentOS shutdown complete.")

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _create_gate(
        self,
        pid: str,
        role: str,
        local_tools: Optional[Dict[str, Callable]] = None,
        write_filter: Optional[Callable[[str], bool]] = None
    ) -> KernelGate:
        """Create a KernelGate for a process, injecting OS-level tools context."""
        from nimbus.os.gate import SimpleEventStream
        
        # Combine with local tools provided by spawn()
        all_funcs = self._composite_tools.get_all_funcs()
        if local_tools:
            all_funcs.update(local_tools)

        return KernelGate(
            pid=pid,
            tool_executor=self._composite_tools,
            event_stream=self._events,
            default_timeout=self.config.default_timeout,
            local_tools=all_funcs,
            write_filter=write_filter,
        )

    def _emit_event(self, event_type: str, pid: str, data: Dict[str, Any]) -> None:
        """Emit an event."""
        self._events.emit(
            Event(
                type=event_type,  # type: ignore
                pid=pid,
                data=data,
            )
        )


# =============================================================================
# Factory Functions (re-exported from nimbus.orchestration.bootstrap)
# =============================================================================

# Re-export for backward compatibility
from nimbus.orchestration.bootstrap import create_agent_os  # noqa: F401
