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
from nimbus.core.memory.context import PinnedContext
from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.profile import AgentProfile  # NEW
from nimbus.core.protocol import Event, Fault, ToolResult
from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.vcpu import VCPU, LLMClient, VCPUConfig
from nimbus.core.scheduler import (
    DAG,
    EventStream,
    Scheduler,
    SchedulerConfig,
    Task,
)

# Session and Compaction
from nimbus.core.session import SessionManager
from nimbus.os.gate import (
    KernelGate,
    SimpleEventStream,
)
from nimbus.skills.manager import SkillManager
from nimbus.tools.base import ToolDefinition, ToolParameter, ToolRegistry
from nimbus.tools.memo import create_memo_tool
from nimbus.tools.composite import CompositeToolRegistry

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
    scheduler_config: SchedulerConfig = field(default_factory=SchedulerConfig)
    mmu_config: MMUConfig = field(default_factory=MMUConfig)
    # Kernel tools
    kernel_tools: bool = True
    # System prompt based on pi-coding-agent design
    system_rules: str = """You are an expert coding assistant. You help users by reading files, executing commands, editing code, and writing new files.

## ⚠️ CRITICAL: Memory Management
You have NO long-term memory. Your context window is LIMITED.
The ONLY way to remember things across conversations is your **Memo** tool.

**好记性不如烂笔头** - Use `Memo(action="append", content="...")` to save:
- Current task and progress
- Important file paths and variable names
- Key decisions and their reasons
- Errors encountered and how you solved them
- Next steps

If it's not in your Memo, you WILL forget it!

## Guidelines
- ALWAYS respond in CHINESE (简体中文), regardless of the user's language.
- Use Bash for file operations like ls, grep, find, rg
- Use Read to examine files before editing
- Use Edit for precise changes (old text must match exactly)
- Use Write only for new files or complete rewrites
- Be concise in your responses
- Show file paths clearly when working with files

## Workflow
1. Check Memo first if resuming a task: `Memo(action="read")`
2. Read files to understand the code
3. Edit/Write to make changes
4. Update Memo with progress: `Memo(action="append", content="...")`
5. Reply to the user when done

## Rules
- Act immediately on clear instructions, don't ask for confirmation
- After Edit/Write success, just reply to the user (don't re-read to verify)
- If a tool fails, try a different approach (don't retry with identical arguments)
- Trust tool results - if Edit says success, the file IS modified
- Before starting complex tasks, use Memo to outline your plan
- If you intend to use a tool, include the tool call in the same response. Do NOT first say "I'll do it now" then call the tool in the next turn. A response without tool calls = your final answer.
- When multiple tools are needed in sequence, call the first tool now. After its result, call the next."""
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
    role: str = ""
    state: ProcessState = "PENDING"
    vcpu: Optional[VCPU] = None
    mmu: Optional[MMU] = None
    gate: Optional[KernelGate] = None
    result: Optional[ToolResult] = None
    task: Optional[asyncio.Task] = None
    inbox: List[str] = field(default_factory=list)
    signals: Dict[str, bool] = field(default_factory=dict)


# =============================================================================
# AgentOS
# =============================================================================


class AgentOS:
    """Agent Operating System - The Top-Level Integration Layer."""

    def __init__(
        self,
        llm_client: LLMClient,
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

        # 1. Register Kernel Tools (Auto)
        kernel_tools_desc = ""
        if self.config.kernel_tools:
            from nimbus.tools import BASH_TOOL, EDIT_TOOL, READ_TOOL, WRITE_TOOL
            from nimbus.tools.base import ToolDefinition, ToolParameter

            kernel_tools_list = [READ_TOOL, WRITE_TOOL, EDIT_TOOL, BASH_TOOL]
            kernel_tools_desc = "Available tools:\n"

            # Helper to parse legacy tool dicts
            def _parse_legacy_tool(data: Dict[str, Any]) -> ToolDefinition:
                params = []
                schema = data.get("parameters", {})
                props = schema.get("properties", {})
                required = schema.get("required", [])

                for name, spec in props.items():
                    params.append(
                        ToolParameter(
                            name=name,
                            type=spec.get("type", "string"),
                            description=spec.get("description", ""),
                            required=(name in required),
                        )
                    )

                return ToolDefinition(
                    name=data["name"],
                    description=data.get("description", ""),
                    parameters=params,
                    category="core",
                )

            for tool_data in kernel_tools_list:
                try:
                    def_obj = _parse_legacy_tool(tool_data)
                    self._tools.register(def_obj, tool_data["function"])

                    params = []
                    for p in def_obj.parameters:
                        p_str = p.name
                        if not p.required:
                            p_str += "?"
                        params.append(p_str)

                    sig = f"{def_obj.name}({', '.join(params)})"
                    desc = def_obj.description.split(".")[0] + "."
                    kernel_tools_desc += f"- {sig}: {desc}\n"
                except ValueError:
                    pass

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

        # 3. Inject Kernel Tools into System Prompt
        if kernel_tools_desc and "Available tools:" not in self.config.system_rules:
            parts = self.config.system_rules.split("\n\n", 1)
            if len(parts) > 1:
                self.config.system_rules = parts[0] + "\n\n" + kernel_tools_desc + "\n" + parts[1]
            else:
                self.config.system_rules += "\n\n" + kernel_tools_desc

        self._scheduler = Scheduler(
            config=self.config.scheduler_config,
            events=EventStream(),
        )

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

        # Create process components
        mmu = self._create_mmu(pid, system_rules=_system_rules)

        # Create Memo tool (bound to workspace and session)
        # Note: workspace_info is a display string like "Workspace: .", not a path
        workspace = Path.cwd()
        memo_def, memo_func, memo_manager = create_memo_tool(workspace, pid)

        # Store memo manager for later access (e.g., prepending memo to context)
        mmu._memo_manager = memo_manager

        gate = self._create_gate(pid, _role, local_tools={
            "Memo": memo_func
        })
        decoder = InstructionDecoder()

        # Prepare tools list
        if _tools_filter is not None:
            # Explicit tools list (empty = pure reasoning, no tools)
            # If using names (strings), fetch definitions from registry
            if _tools_filter and isinstance(_tools_filter[0], str):
                tools_list = []
                for name in _tools_filter:
                    defn = self._tools.get_definition(name)
                    if defn:
                        tools_list.append(defn)
            else:
                tools_list = list(_tools_filter)
        else:
            # Inherit from kernel (filtered by role) + Memo
            tools_list = self._composite_tools.get_definitions(format="openai", role=_role)
            tools_list.append({
                "type": "function",
                "function": memo_def
            })

        # Determine VCPU config (allow per-process overrides)
        vcpu_config = self.config.vcpu_config
        if _max_iterations is not None:
            from dataclasses import replace as dc_replace
            # Executor-like sub-processes: set iteration limit and disable compaction.
            # They should stop at max_iterations, not compact and continue.
            vcpu_config = dc_replace(
                vcpu_config,
                max_iterations=_max_iterations,
                compact_on_limit=False,
            )

        # Create VCPU
        vcpu = VCPU(
            alu=llm_client or self._llm,
            config=vcpu_config,
            decoder=decoder,
            mmu=mmu,
            gate=gate,
            tools=tools_list,
            session_id=pid,
        )

        # Create process
        process = Process(
            pid=pid,
            goal=goal,
            role=_role,
            state="PENDING",
            vcpu=vcpu,
            mmu=mmu,
            gate=gate,
        )

        # Register process
        self._processes[pid] = process

        # Emit spawn event
        self._emit_event("PROC_SPAWNED", pid, {"goal": goal, "role": _role})

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
            return ToolResult(
                status="TIMEOUT",
                fault=Fault(
                    domain="KERNEL",
                    code="TIMEOUT",
                    message=f"Process {pid} timed out after {timeout}s",
                ),
            )

        # If process is pending, start it
        if process.state == "PENDING":
            process.state = "RUNNING"
            try:
                if timeout:
                    return await asyncio.wait_for(self._run_process(process), timeout=timeout)
                return await self._run_process(process)
            except asyncio.TimeoutError:
                return _timeout_result()

        # If process is already running with a task, wait for it
        if process.task and not process.task.done():
            try:
                if timeout:
                    return await asyncio.wait_for(process.task, timeout=timeout)
                return await process.task
            except asyncio.TimeoutError:
                return _timeout_result()

        # If process is completed, return the result
        if process.state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return process.result or ToolResult(status="OK")

        # Otherwise, run the process
        process.state = "RUNNING"
        try:
            if timeout:
                return await asyncio.wait_for(self._run_process(process), timeout=timeout)
            return await self._run_process(process)
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
        pid = self.spawn(goal, role=role)
        process = self._processes.get(pid)
        if not process:
            yield {"type": "error", "message": f"Process {pid} not found"}
            return

        vcpu = process.vcpu
        mmu = process.mmu

        if vcpu.config.pin_goal:
            pinned_goal = await vcpu._prepare_goal_for_pinning(goal)
            mmu.pin_user_goal(pinned_goal)
        mmu.add_user_message(goal)

        yield {"type": "planning", "content": "Starting execution..."}

        while True:
            result = await vcpu.step()

            # Handle CONTEXT_OVERFLOW fault - trigger compaction and retry
            if result.fault and result.fault.code == "CONTEXT_OVERFLOW":
                ctx = result.fault.context or {}
                yield {
                    "type": "compaction",
                    "message": f"Context overflow ({ctx.get('current_tokens')} tokens), compacting...",
                }
                success = await self._compaction_for_process(pid, mmu)
                if success:
                    yield {"type": "compaction_done", "message": "Compaction complete"}
                    continue  # Retry the step after compaction
                else:
                    yield {"type": "error", "message": "Compaction failed"}
                    return

            for i, action in enumerate(result.actions):
                action_kind = getattr(action, "kind", None)

                if action_kind == "TOOL_CALL":
                    tool_name = getattr(action, "name", "unknown")
                    tool_args = getattr(action, "args", {})
                    tool_id = getattr(action, "id", None)

                    yield {
                        "type": "tool_call",
                        "name": tool_name,
                        "args": tool_args,
                        "action_id": tool_id,
                    }
                    if i < len(result.results):
                        tool_result = result.results[i]
                        yield {
                            "type": "tool_result",
                            "name": tool_name,
                            "args": tool_args,
                            "action_id": tool_id,
                            "output": getattr(tool_result, "output", str(tool_result)),
                            "status": getattr(tool_result, "status", "OK"),
                            "duration_ms": getattr(tool_result, "meta", {}).get("duration_ms") if hasattr(tool_result, "meta") else None,
                        }
                elif action_kind == "THOUGHT":
                    content = action.args.get("content", action.args.get("text", "")) if action.args else ""
                    if content:
                        # Check if this thought was blocked by hallucination firewall
                        if i < len(result.results) and getattr(result.results[i], "meta", {}).get("hallucination_blocked"):
                            continue  # Skip — firewall blocked this
                        yield {"type": "text", "content": content}
                elif action_kind == "RETURN":
                    result_value = action.args.get("result", "") if action.args else ""
                    yield {
                        "type": "done",
                        "result": {
                            "status": "OK",
                            "output": result_value,
                        },
                    }
                    return

            if result.is_final:
                yield {
                    "type": "done",
                    "result": {
                        "status": "FAULT" if result.fault else "OK",
                        "output": result.final_result,
                        "error": str(result.fault) if result.fault else None,
                    },
                }
                return

    async def chat(self, message: "str | list", session_id: str | None = None) -> ToolResult:
        is_existing_process = False
        if session_id and session_id in self._processes:
            process = self._processes[session_id]
            is_existing_process = True
        else:
            if not session_id:
                session_id = f"chat-{uuid.uuid4().hex[:8]}"
            mmu = self._create_mmu(session_id)

            # Create Memo tool (bound to workspace and session)
            workspace = Path.cwd()
            memo_def, memo_func, memo_manager = create_memo_tool(workspace, session_id)
            mmu._memo_manager = memo_manager

            gate = self._create_gate(session_id, "chat", local_tools={
                "Memo": memo_func
            })
            decoder = InstructionDecoder()

            # Prepare tools list with Memo
            tools_list = self._composite_tools.get_definitions(format="openai", role="chat")
            tools_list.append({
                "type": "function",
                "function": memo_def
            })

            vcpu = VCPU(
                alu=self._llm,
                decoder=decoder,
                gate=gate,
                mmu=mmu,
                config=self.config.vcpu_config,
                tools=tools_list,
                session_id=session_id,
            )

            vcpu.set_compaction_callback(
                lambda sid=session_id, m=mmu: self._compaction_for_process(sid, m)
            )

            process = Process(
                pid=session_id,
                goal="Interactive chat session",
                role="chat",
                state="PENDING",
                vcpu=vcpu,
                mmu=mmu,
                gate=gate,
            )
            self._processes[session_id] = process
            self._emit_event("PROC_SPAWNED", session_id, {"goal": "chat", "role": "chat"})

        if is_existing_process and process.state == "RUNNING":
            from loguru import logger

            logger.info(f"Process {session_id} is busy. Converting chat to injection.")
            self.inject_message(process.pid, message)

            if self._session_mgr:
                from nimbus.core.memory.context import Message

                self._session_mgr.append_message(Message(role="user", content=message))

            return ToolResult(
                status="OK", output="[Instruction appended to running task]", is_final=True
            )

        process.vcpu._reset()

        if self._session_mgr:
            from nimbus.core.memory.context import Message

            self._session_mgr.append_message(Message(role="user", content=message))

        self.inject_message(process.pid, message)

        if process.state != "RUNNING":
            process.state = "RUNNING"
            return await self._run_process(process)

        return ToolResult(status="OK", output="[Already Running]")

    # =========================================================================
    # Process Management
    # =========================================================================

    def list_processes(self) -> List[str]:
        """List all active process IDs."""
        return list(self._processes.keys())

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
        # Re-create components similar to chat() initialization
        mmu = self._create_mmu(session_id)

        # Create Memo tool (bound to workspace and session)
        workspace = Path.cwd()
        memo_def, memo_func, memo_manager = create_memo_tool(workspace, session_id)
        mmu._memo_manager = memo_manager

        gate = self._create_gate(session_id, "chat", local_tools={
            "Memo": memo_func
        })
        decoder = InstructionDecoder()

        tools_list = self._composite_tools.get_definitions(format="openai")
        tools_list.append({
            "type": "function",
            "function": memo_def
        })

        vcpu = VCPU(
            alu=self._llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=self.config.vcpu_config,
            tools=tools_list,
            session_id=session_id,
        )

        vcpu.set_compaction_callback(
            lambda sid=session_id, m=mmu: self._compaction_for_process(sid, m)
        )

        # Restore state
        vcpu.restore_from_checkpoint(checkpoint)

        # Register process
        process = Process(
            pid=session_id,
            goal="Restored session",
            role="chat",
            state="PENDING",
            vcpu=vcpu,
            mmu=mmu,
            gate=gate,
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

        all_messages = []
        for frame in process.mmu._stack:
            for msg in frame.messages:
                all_messages.append(msg)

        new_messages, result = await self._compaction_engine.compact(
            all_messages, custom_instructions
        )

        if result.messages_removed > 0:
            process.mmu.clear()
            for msg in new_messages:
                process.mmu.add_message(msg)

            if self._session_mgr:
                self._session_mgr.append_compaction(
                    summary=result.summary,
                    first_kept_entry_id=result.first_kept_entry_id or "",
                    tokens_before=result.tokens_before,
                    details=result.details,
                )

            self._emit_event(
                "COMPACTION",
                target_session,
                {
                    "tokens_before": result.tokens_before,
                    "tokens_after": result.tokens_after,
                    "messages_removed": result.messages_removed,
                },
            )

        return {
            "summary": result.summary,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "messages_removed": result.messages_removed,
            "compression_ratio": result.compression_ratio,
        }

    async def _check_compaction(self, process: Process) -> None:
        """Proactive auto-compaction: compact before step() when tokens exceed threshold."""
        if not process.mmu:
            return

        mmu = process.mmu
        current_tokens = mmu.estimate_tokens()
        max_tokens = self.config.mmu_config.max_context_tokens
        threshold = int(max_tokens * self.config.mmu_config.compress_threshold)  # 90%

        if current_tokens < threshold:
            return

        # Guard: too few messages → sliding window handles it
        total_messages = sum(len(f.messages) for f in mmu._stack)
        if total_messages < 10:
            return

        # Guard: max compactions reached
        vcpu = process.vcpu
        if vcpu and vcpu._state.compaction_count >= vcpu.config.max_compactions:
            return

        # Execute
        logger.info(f"[{process.pid}] Auto-compaction: {current_tokens} tokens "
                    f"({current_tokens*100//max_tokens}% of {max_tokens}), {total_messages} msgs")
        self._emit_event("AUTO_COMPACTION_TRIGGERED", process.pid,
                         {"current_tokens": current_tokens, "threshold": threshold})

        tokens_before = current_tokens
        success = await self._compaction_for_process(process.pid, mmu)
        tokens_after = mmu.estimate_tokens()

        if success:
            if vcpu:
                vcpu._state.compaction_count += 1
            pct = 100 - (tokens_after * 100 // tokens_before) if tokens_before else 0
            logger.info(f"[{process.pid}] Auto-compaction done: "
                        f"{tokens_before}→{tokens_after} tokens (-{pct}%)")
        else:
            logger.warning(f"[{process.pid}] Auto-compaction failed, sliding window fallback")
        # Never crash — sliding window is the ultimate safety net

    async def _compaction_for_process(self, pid: str, mmu: MMU) -> bool:
        try:
            session_id = "unknown"
            if hasattr(self._llm, "_client") and hasattr(self._llm._client, "session_id"):
                session_id = self._llm._client.session_id

            # Calculate dynamic summary budget based on pinned context budget
            # Summary should take at most 30% of pinned budget to leave room for system rules
            pinned_budget = mmu.config.pinned_budget  # e.g., 2000 tokens
            summary_token_budget = int(pinned_budget * 0.3)  # e.g., 600 tokens
            # Rough estimate: 1 token ≈ 2-3 Chinese chars, 4 English chars
            summary_char_budget = summary_token_budget * 2  # Conservative for Chinese

            async def compress_summary(text: str, max_chars: int) -> str:
                """Use LLM to intelligently compress a summary that's too long."""
                compress_prompt = (
                    f"以下摘要过长，请精简到{max_chars}字符以内，保留最关键的信息：\n\n"
                    f"{text}\n\n"
                    f"要求：\n"
                    f"1. 优先保留：用户提供的密码/密钥、关键代码、配置信息\n"
                    f"2. 其次保留：当前任务状态、重要决策\n"
                    f"3. 可省略：过程细节、已解决的问题\n"
                    f"请直接输出精简后的摘要（不超过{max_chars}字符）："
                )
                try:
                    response = await self._llm.chat(
                        messages=[{"role": "user", "content": compress_prompt}],
                        tools=None,
                    )
                    if response and response.content:
                        return response.content[:max_chars]  # Final hard limit
                except Exception as e:
                    logger.warning(f"Summary compression failed: {e}")
                # Fallback: simple truncation at sentence boundary
                truncated = text[:max_chars]
                for sep in ["。", ".", "\n"]:
                    pos = truncated.rfind(sep)
                    if pos > max_chars * 0.7:
                        return truncated[: pos + 1] + "...[已压缩]"
                return truncated + "...[已压缩]"

            # Create a summarizer that uses the LLM to generate a summary
            # Read Memo content to include in summary (so passwords/key info survive)
            memo_context = ""
            if hasattr(mmu, '_memo_manager') and mmu._memo_manager:
                try:
                    memo_content = mmu._memo_manager.read()
                    if memo_content and memo_content.strip():
                        memo_context = memo_content.strip()
                except Exception:
                    pass

            async def generate_summary(messages: list) -> str:
                """Generate a summary of the conversation using LLM."""
                try:
                    # Extract any previous summary from messages (to preserve cascade info)
                    previous_summary = ""
                    for m in messages:
                        content = str(m.content) if m.content else ""
                        if "[Memory Recall]" in content or "关键信息摘要" in content:
                            previous_summary = content
                            break

                    # Build a prompt for summarization
                    context = "\n".join(
                        f"[{m.role.upper()}]: {str(m.content)[:500]}"
                        for m in messages[-20:]  # Last 20 messages
                    )

                    # Append Memo content so summarizer preserves key info from notes
                    if memo_context:
                        context += f"\n\n【用户备忘录 Memo】\n{memo_context[:500]}"

                    # Calculate target length based on whether we're merging
                    target_chars = summary_char_budget
                    if previous_summary:
                        # When merging, be more aggressive about compression
                        target_chars = int(summary_char_budget * 0.8)

                    # Include previous summary to prevent cascade loss
                    if previous_summary:
                        summary_prompt = (
                            "请作为任务管理者，合并并更新以下执行摘要。\n\n"
                            f"【之前的摘要】\n{previous_summary[:1000]}\n\n"
                            f"【新进展】\n{context}\n\n"
                            "**核心要求**：\n"
                            "1. 必须保留所有关键技术细节（代码路径、配置值、密码）。\n"
                            "2. 必须评估当前进度与最终目标的距离（防止任务漂移）。\n"
                            f"请用中文回复（{target_chars}字以内）。\n\n"
                            "**OUTPUT FORMAT**:\n"
                            "NEW_MILESTONES: [Milestone 1], [Milestone 2]\n"
                            "SUMMARY: [Your summary content here]"
                        )
                    else:
                        summary_prompt = (
                            "请作为任务管理者，总结当前执行状态。\n\n"
                            f"【对话内容】\n{context}\n\n"
                            "**核心要求**：\n"
                            "1. 提取所有关键技术细节（代码路径、配置值、密码）。\n"
                            "2. 明确下一步行动计划。\n"
                            f"请用中文回复（{target_chars}字以内）。\n\n"
                            "**OUTPUT FORMAT**:\n"
                            "NEW_MILESTONES: [Milestone 1]\n"
                            "SUMMARY: [Your summary content here]"
                        )

                    # Use LLM.chat() to generate summary (not complete())
                    response = await self._llm.chat(
                        messages=[{"role": "user", "content": summary_prompt}],
                        tools=None,  # No tools needed for summary
                    )

                    if response and response.content:
                        # Parse response for milestones
                        content = response.content
                        milestones = []
                        summary = content

                        if "NEW_MILESTONES:" in content and "SUMMARY:" in content:
                            try:
                                parts = content.split("SUMMARY:", 1)
                                milestone_part = parts[0].replace("NEW_MILESTONES:", "").strip()
                                summary = parts[1].strip()

                                if milestone_part and milestone_part.lower() != "none":
                                    milestones = [m.strip() for m in milestone_part.split(",") if m.strip()]
                            except Exception:
                                pass # Fallback to raw content if parsing fails

                        # Register milestones with MMU
                        if milestones:
                            mmu.add_milestones(milestones)
                            logger.info(f"🚩 Registered milestones: {milestones}")

                        # Smart budget check: if over budget, use LLM to re-compress
                        if len(summary) > summary_char_budget:
                            logger.warning(
                                f"Summary ({len(summary)} chars) exceeds budget ({summary_char_budget} chars), "
                                f"using LLM to compress..."
                            )
                            summary = await compress_summary(summary, summary_char_budget)
                            logger.info(f"Summary compressed to {len(summary)} chars")

                        return summary
                    return "Summary generation failed"
                except Exception as e:
                    logger.warning(f"Summary generation failed: {e}")
                    return f"[Summary unavailable: {e}]"

            archive_path = await mmu.archive_and_reset(session_id, summarizer=generate_summary)

            if archive_path:
                logger.info(
                    f"[{pid}] Memory compaction successful: Context archived to {archive_path}"
                )
                return True

            logger.warning(f"[{pid}] Memory archiving skipped (no messages?), but allowing reset")
            return True

        except Exception as e:
            logger.error(f"[{pid}] Compaction failed: {e}")
            return False

    async def run_dag(self, dag: DAG) -> ToolResult:
        await self._scheduler.submit_dag(dag)

        async def executor(task: Task) -> ToolResult:
            return await self.run(task.spec.goal, role=task.spec.process_role)

        return await self._scheduler.run_dag(dag.id, executor)

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
                process.signals["interrupt"] = True
                signalled = True
                logger.info(f"[{pid}] Signal 'interrupt' set")

        return signalled

    def inject_message(self, pid: str, message: "str | list") -> bool:
        process = self._processes.get(pid)
        if not process:
            return False

        process.inbox.append(message)
        logger.info(f"[{pid}] Message injected into inbox: {message[:50]}...")
        return True

    async def _run_process(self, process: Process) -> ToolResult:
        try:
            if process.vcpu is None:
                raise RuntimeError("Process has no VCPU")

            if not process.vcpu._state.is_running:
                process.vcpu._reset()
                process.vcpu._state.is_running = True

                if process.vcpu.config.pin_goal and process.role != "chat":
                    pinned_goal = await process.vcpu._prepare_goal_for_pinning(process.goal)
                    process.mmu.pin_user_goal(pinned_goal)
                    process.mmu.add_user_message(process.goal)

            final_result = None

            while process.vcpu._state.is_running and not process.vcpu._state.is_done:
                if process.signals.get("interrupt"):
                    logger.info(f"[{process.pid}] Interrupted by signal")
                    process.state = "CANCELLED"
                    process.signals["interrupt"] = False
                    process.vcpu._state.is_done = True
                    return ToolResult(
                        status="CANCELLED",
                        fault=Fault(
                            domain="KERNEL", code="INTERRUPTED", message="Interrupted by user"
                        ),
                    )

                while process.inbox:
                    msg = process.inbox.pop(0)
                    if process.role == "chat":
                        process.mmu.add_user_message(msg)
                    else:
                        process.mmu.add_user_message(f"[User Intervention] {msg}")

                    self._emit_event("USER_INTERVENTION", process.pid, {"content": msg})
                    logger.info(f"[{process.pid}] Processed inbox message: {msg[:50]}...")

                await self._check_compaction(process)

                # Check iteration limit (iteration-based budget instead of wall-clock timeout)
                vcpu = process.vcpu
                if vcpu._state.iteration >= vcpu.config.max_iterations:
                    if vcpu.config.compact_on_limit:
                        # For long-running processes (e.g. Core Agent), compact and continue
                        compacted = await self._compaction_for_process(process.pid, process.mmu)
                        if compacted:
                            logger.info(
                                f"[{process.pid}] Compaction #{vcpu._state.compaction_count} complete, "
                                f"resetting iteration counter (was {vcpu._state.iteration})"
                            )
                            vcpu._state.iteration = 0
                            vcpu._state.compaction_count += 1
                            continue
                    # Budget exceeded (or compaction disabled/failed)
                    # Give the LLM one final step to summarize what it did
                    logger.info(
                        f"[{process.pid}] Iteration budget reached "
                        f"({vcpu._state.iteration}/{vcpu.config.max_iterations}), "
                        f"requesting final summary..."
                    )
                    process.mmu.add_user_message(
                        "[SYSTEM] You have reached your iteration limit. "
                        "Do NOT call any more tools. Immediately respond with a summary of: "
                        "1) what you completed, 2) what remains unfinished."
                    )
                    # Run one final step for the summary
                    final_step = await process.vcpu.step()
                    process.state = "SUCCEEDED"
                    # Extract LLM's text response as the output
                    summary = ""
                    if final_step.is_final and final_step.final_result:
                        summary = final_step.final_result.output or ""
                    elif final_step.actions:
                        # LLM might have responded with text (RETURN action)
                        for action in final_step.actions:
                            if action.kind == "RETURN":
                                summary = action.args.get("result", "")
                                break
                            elif action.kind == "THOUGHT":
                                summary = action.args.get("content", "")
                    if not summary:
                        summary = (
                            f"Iteration budget reached ({vcpu.config.max_iterations} iterations). "
                            f"Task may be partially complete."
                        )
                    return ToolResult(status="OK", output=summary)

                step_result = await process.vcpu.step()

                # Handle CONTEXT_OVERFLOW fault - trigger compaction and retry
                if step_result.fault and step_result.fault.code == "CONTEXT_OVERFLOW":
                    ctx = step_result.fault.context or {}
                    overflow_tokens = ctx.get("current_tokens") or 0
                    logger.info(
                        f"[{process.pid}] Context overflow ({overflow_tokens} tokens), "
                        f"triggering compaction..."
                    )
                    self._emit_event(
                        "COMPACTION_TRIGGERED",
                        process.pid,
                        {"current_tokens": overflow_tokens, "threshold": ctx.get("threshold")},
                    )

                    # Measure tokens before compaction to verify effectiveness
                    tokens_before = process.mmu.estimate_tokens()
                    success = await self._compaction_for_process(process.pid, process.mmu)
                    tokens_after = process.mmu.estimate_tokens()

                    if success and tokens_after < tokens_before * 0.8:
                        pct = (
                            f"-{100 - tokens_after * 100 // tokens_before}%"
                            if tokens_before > 0
                            else f"freed {tokens_before - tokens_after}"
                        )
                        logger.info(
                            f"[{process.pid}] Compaction effective: "
                            f"{tokens_before} → {tokens_after} tokens "
                            f"({pct}), retrying step..."
                        )
                        continue  # Retry the step after compaction
                    else:
                        reason = (
                            f"tokens {tokens_before} → {tokens_after} (insufficient reduction)"
                            if success
                            else "compaction returned failure"
                        )
                        logger.error(f"[{process.pid}] Compaction ineffective: {reason}")
                        process.state = "FAILED"
                        return ToolResult(
                            status="ERROR",
                            fault=Fault(
                                domain="MEMORY",
                                code="COMPACTION_FAILED",
                                message=f"Context overflow and compaction ineffective: {reason}",
                            ),
                        )

                if step_result.fault and not step_result.fault.retryable:
                    process.state = "FAILED"
                    return ToolResult(status="ERROR", fault=step_result.fault)

                if step_result.is_final:
                    if process.inbox:
                        logger.info(
                            f"[{process.pid}] New messages arrived during final step, extending execution..."
                        )
                        process.vcpu._state.is_done = False
                        continue

                    process.state = "SUCCEEDED"
                    final_result = step_result.final_result or ToolResult(
                        status="OK", output="Completed"
                    )
                    break

                await asyncio.sleep(0)

            self._emit_event(
                "PROC_FINISHED",
                process.pid,
                {
                    "state": process.state,
                    "status": final_result.status if final_result else "UNKNOWN",
                },
            )

            return final_result or ToolResult(status="OK")

        except asyncio.CancelledError:
            process.state = "CANCELLED"
            process.result = ToolResult(
                status="CANCELLED",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message="Process was cancelled",
                    retryable=True,
                ),
            )
            raise

        except Exception as e:
            process.state = "FAILED"
            process.result = ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=str(e),
                    retryable=False,
                ),
            )
            return process.result

    # =========================================================================
    # Tool Management
    # =========================================================================

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        roles: Optional[List[str]] = None,
        category: Optional[str] = None,
    ) -> None:
        """
        Register a tool.

        Args:
            name: Tool name
            func: Tool function
            description: Tool description
            parameters: Tool parameters schema
            roles: List of allowed roles
        """
        if name in self._tools:
            self._tools.unregister(name)

        if hasattr(func, "_tool_definition"):
            defn = func._tool_definition
            if roles is not None:
                defn.roles = roles
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
                roles=roles,
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

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _create_mmu(self, pid: str, system_rules: Optional[str] = None) -> MMU:
        """Create an MMU for a process."""
        mmu = MMU(config=self.config.mmu_config, process_id=pid)

        sys_rules = system_rules if system_rules is not None else self.config.system_rules

        # Set pinned context
        pinned = PinnedContext(
            system_rules=sys_rules,
            workspace_info=self.config.workspace_info,
            capabilities=self.config.capabilities,
        )
        mmu.set_pinned(pinned)

        return mmu

    def _create_gate(self, pid: str, role: str = "", local_tools: Optional[Dict[str, Callable]] = None) -> KernelGate:
        """Create a KernelGate for a process."""
        return KernelGate(
            pid=pid,
            tool_executor=self._composite_tools,
            event_stream=self._events,
            default_timeout=self.config.default_timeout,
            local_tools=local_tools,
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
# Factory Functions
# =============================================================================


def create_agent_os(
    llm_client: LLMClient,
    tools: Optional[Dict[str, Callable]] = None,
    system_rules: str = "",
    max_processes: int = 10,
    default_timeout: float = 300.0,
    workspace: Optional["Path"] = None,
    register_defaults: bool = True,
    kernel_tools: bool = True,
    skill_paths: Optional[List[Path]] = None,
    # New arguments
    config: Optional[AgentOSConfig] = None,
    profile: Optional[str | AgentProfile] = None,
    model_id: str = "default",
) -> AgentOS:
    """
    Factory function to create an AgentOS with common defaults.

    Args:
        llm_client: LLM client for vCPUs
        tools: Initial tool registry (additional to defaults)
        system_rules: System rules for all processes
        max_processes: Maximum concurrent processes
        default_timeout: Default execution timeout
        workspace: Workspace path for tool sandboxing
        register_defaults: Whether to register default v2 tools (Read, Glob, etc.)
        kernel_tools: Whether to auto-register kernel tools (Read, Write, Edit, Bash)
        profile: AgentProfile configuration (overrides manual config)
        model_id: Model ID for dynamic prompt generation

    Returns:
        Configured AgentOS instance with default tools registered
    """
    from pathlib import Path

    if workspace is None:
        workspace = Path.cwd()

    if config is None:
        config = AgentOSConfig(
            max_processes=max_processes,
            default_timeout=default_timeout,
            system_rules=system_rules or AgentOSConfig.system_rules,
            workspace_info=f"Workspace: {workspace}",
            kernel_tools=kernel_tools,
            skill_paths=skill_paths or [],
        )

    # Allow overriding limits via environment variables (for testing compaction)
    import os as _os
    _max_ctx = _os.environ.get("NIMBUS_MAX_CONTEXT_TOKENS")
    if _max_ctx:
        config.mmu_config.max_context_tokens = int(_max_ctx)
        config.mmu_config.frame_budget = max(int(_max_ctx) - config.mmu_config.pinned_budget, 1000)
        logger.info(f"MMU override: max_context_tokens={config.mmu_config.max_context_tokens}, frame_budget={config.mmu_config.frame_budget}")
    _max_iter = _os.environ.get("NIMBUS_MAX_ITERATIONS")
    if _max_iter:
        config.vcpu_config.max_iterations = int(_max_iter)
        logger.info(f"VCPU override: max_iterations={config.vcpu_config.max_iterations}")

    # Handle profile overrides
    target_profile = None
    if isinstance(profile, str):
        if profile == "core":
            target_profile = AgentProfile.create_core(model_id)
        elif profile == "executor":
            target_profile = AgentProfile.create_executor(model_id)
        else:
            target_profile = AgentProfile.create_standard(model_id)
    elif isinstance(profile, AgentProfile):
        target_profile = profile

    if target_profile:
        config.system_rules = target_profile.system_prompt
        # Apply runtime config from profile to VCPU config
        # (env var overrides take precedence over profile defaults)
        if not _max_iter:
            config.vcpu_config.max_iterations = target_profile.max_iterations
        config.vcpu_config.max_consecutive_thoughts = target_profile.max_consecutive_thoughts

    os = AgentOS(llm_client=llm_client, tools=tools, config=config)

    if register_defaults:
        from nimbus.tools import register_default_tools
        ws = workspace

        # If profile is None, workspace is already set above
        # If profile is present, use it to determine tool registration

        if isinstance(profile, str) and profile == "core":
            # Core Profile: Split tools by role
            # Shared: Read + Bash (CoreBash removed, standard Bash is shared)
            register_default_tools(os, workspace=ws, tools=["Read", "Bash"])
            # Executor only: Write/Edit
            register_default_tools(os, workspace=ws, tools=["Write", "Edit"], roles=["executor"])

            # --- Auto-register Orchestration Tools for Core ---
            from nimbus.orchestration.dispatch_tool import DispatchTool, DispatchToolConfig
            from nimbus.orchestration.tools import (
                DISPATCH_TOOL_DEF,
                VERIFY_TOOL_DEF,
            )

            # Register Dispatch/Verify
            dispatch_config = DispatchToolConfig()
            dispatch_tool = DispatchTool(
                agent_os=os,
                config=dispatch_config,
                workspace=ws,
            )
            os.register_tool(
                name="Dispatch",
                func=dispatch_tool.dispatch,
                description=DISPATCH_TOOL_DEF["description"],
                parameters=DISPATCH_TOOL_DEF["parameters"],
                roles=["core", "chat"],
                category="extension",
            )
            os.register_tool(
                name="Verify",
                func=dispatch_tool.verify,
                description=VERIFY_TOOL_DEF["description"],
                parameters=VERIFY_TOOL_DEF["parameters"],
                roles=["core", "chat"],
                category="extension",
            )

            # Register ReviewCommittee
            from nimbus.orchestration.review_tool import REVIEW_TOOL_DEF, ReviewTool
            review_tool = ReviewTool(agent_os=os, workspace=ws)
            os.register_tool(
                name="ReviewCommittee",
                func=review_tool.review,
                description=REVIEW_TOOL_DEF["description"],
                parameters=REVIEW_TOOL_DEF["parameters"],
                roles=["core", "chat"],
                category="extension",
            )

        else:
            # Standard Profile: All tools for everyone
            register_default_tools(os, workspace=ws)

    return os
