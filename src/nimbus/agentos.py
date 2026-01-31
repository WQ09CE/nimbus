"""
Nimbus v2 AgentOS - The Top-Level Integration Layer

AgentOS is the unified entry point for the Nimbus v2 system.
It orchestrates all components: VCPU, MMU, Gate, Scheduler, Decoder.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                         AgentOS                              │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │                     Process Pool                       │   │
    │  │   ┌─────────┐   ┌─────────┐   ┌─────────┐            │   │
    │  │   │ Process │   │ Process │   │ Process │   ...      │   │
    │  │   │  (VCPU) │   │  (VCPU) │   │  (VCPU) │            │   │
    │  │   └─────────┘   └─────────┘   └─────────┘            │   │
    │  └──────────────────────────────────────────────────────┘   │
    │                            │                                  │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │                Component Factory                       │   │
    │  │   Decoder | Gate | MMU | EventStream | IPCBus        │   │
    │  └──────────────────────────────────────────────────────┘   │
    │                            │                                  │
    │  ┌──────────────────────────────────────────────────────┐   │
    │  │                   Tool Registry                        │   │
    │  │         { tool_name: callable, ... }                  │   │
    │  └──────────────────────────────────────────────────────┘   │
    └─────────────────────────────────────────────────────────────┘

Key Responsibilities:
1. Unified Entry Point: Simple API to run goals (run, run_dag)
2. Component Orchestration: Create and wire all components
3. Process Management: spawn, wait, kill subprocesses
4. Tool Registration: Manage available tools
5. Event Aggregation: Collect events from all components

Usage:
    # Create AgentOS
    os = AgentOS(llm_client=my_llm, tools={"Read": read_fn, "Bash": bash_fn})

    # Run a simple goal
    result = await os.run("List all Python files in src/")

    # Run a DAG
    dag = create_linear_dag(["explore codebase", "find auth module"])
    result = await os.run_dag(dag)

    # Spawn subprocess
    pid = os.spawn("explore_codebase", role="eye")
    result = await os.wait(pid)
"""

import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path as PathType

from nimbus.core.memory.context import PinnedContext
from nimbus.core.memory.mmu import MMU, MMUConfig
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
from nimbus.os.gate import (
    KernelGate,
    SimpleEventStream,
    SimpleIPCBus,
    SimplePermissionManager,
)
# Session and Compaction
from nimbus.core.session import SessionManager, InMemorySessionManager
from nimbus.core.compaction import (
    CompactionConfig,
    CompactionEngine,
    DefaultCompactionLLM,
)

# =============================================================================
# AgentOS Configuration
# =============================================================================


@dataclass
class AgentOSConfig:
    """
    Configuration for AgentOS.

    Attributes:
        max_processes: Maximum number of concurrent processes
        default_timeout: Default timeout for task execution (seconds)
        vcpu_config: Configuration for VCPUs
        scheduler_config: Configuration for the scheduler
        mmu_config: Configuration for MMUs
        compaction_config: Configuration for context compaction
        system_rules: System rules for pinned context
        workspace_info: Workspace information for pinned context
        capabilities: Capabilities description for pinned context
        session_dir: Directory for session persistence (None = ephemeral)
        enable_session: Whether to enable session persistence
    """

    max_processes: int = 10
    default_timeout: float = 300.0
    vcpu_config: VCPUConfig = field(default_factory=VCPUConfig)
    scheduler_config: SchedulerConfig = field(default_factory=SchedulerConfig)
    mmu_config: MMUConfig = field(default_factory=MMUConfig)
    system_rules: str = """You are a code assistant with access to tools.

BEHAVIOR - TAKE ACTION IMMEDIATELY:
- When user gives a clear instruction → EXECUTE IT DIRECTLY, DO NOT ask for confirmation
- Do NOT ask "Would you like me to..." or "Should I..." unless the action is:
  1. Deleting important files/data
  2. Modifying system-critical configurations
  3. The instruction is genuinely ambiguous or unclear
- Examples of IMMEDIATE ACTIONS (no asking):
  - Reading files → Just read them
  - Searching code → Just search
  - Running commands → Just run
  - Writing/editing code → Just do it
  - Debugging → Start debugging immediately
- Be decisive and proactive. User expects you to act, not to seek permission for routine tasks.

CRITICAL RULES:
1. Use function calling API to invoke tools. NEVER simulate tool calls in text.
2. When you need to read/search/run commands, call the appropriate tool function.
3. AFTER Edit/Write SUCCESS: Call return_result IMMEDIATELY with a summary. Do NOT read the file again to verify, do NOT call more tools.
4. The workflow is: Read → Edit/Write (if needed) → return_result (ALWAYS end with this).
5. Do NOT keep calling tools indefinitely. Once task is done, call return_result.

AVOID INFINITE LOOPS:
- Edit/Read/Grep/Glob are DETERMINISTIC. Do NOT call them repeatedly with the same arguments.
- If a tool fails or returns empty results, try a DIFFERENT approach or broaden your search. Do NOT retry with identical arguments.
- Trust tool results. If Edit says "success", the file IS modified. Do NOT re-read to verify.
- After successful Edit: call return_result IMMEDIATELY, not another Edit.

GLOB/SEARCH TIPS:
- If Glob returns no files, try a recursive pattern (e.g., "**/*.py") or search the parent directory.
- Verify the current working directory if paths are relative.

EXAMPLE:
- Task: "Fix bug in foo.py"
- Step 1: Read foo.py
- Step 2: Edit foo.py (once)
- Step 3: return_result("Fixed by changing X to Y")  <-- REQUIRED immediately after Edit!

WRONG PATTERN (causes infinite loop):
- Read → Edit → Read → Edit → Read...  <-- NEVER do this!
- Edit → Edit → Edit...  <-- NEVER retry identical Edit!"""
    workspace_info: str = ""
    capabilities: str = ""
    # Session persistence
    session_dir: Optional[Path] = None  # None = ephemeral mode
    enable_session: bool = True
    # Compaction
    compaction_config: CompactionConfig = field(default_factory=CompactionConfig)


# =============================================================================
# Process State
# =============================================================================

ProcessState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


@dataclass
class Process:
    """
    A process managed by AgentOS.

    Attributes:
        pid: Unique process identifier
        goal: The goal this process is working on
        role: Process role (e.g., "eye", "body", "mind")
        state: Current process state
        vcpu: The VCPU executing this process
        mmu: Memory management unit for this process
        gate: Kernel gate for this process
        result: Process result (if completed)
        task: The asyncio task running this process
    """

    pid: str
    goal: str
    role: str = ""
    state: ProcessState = "PENDING"
    vcpu: Optional[VCPU] = None
    mmu: Optional[MMU] = None
    gate: Optional[KernelGate] = None
    result: Optional[ToolResult] = None
    task: Optional[asyncio.Task] = None


# =============================================================================
# Tool Executor
# =============================================================================


class ToolRegistry:
    """
    Registry for tools available to the AgentOS.

    This class manages tool registration and provides an executor
    interface for the KernelGate.
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Callable] = {}
        self._tool_defs: List[Dict[str, Any]] = []

    def register(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Register a tool.

        Args:
            name: Tool name
            func: Tool function (sync or async)
            description: Tool description
            parameters: Tool parameters schema
        """
        self._tools[name] = func

        # Build tool definition for LLM
        tool_def = {
            "type": "function",
            "function": {
                "name": name,
                "description": description or f"Execute {name}",
                "parameters": parameters or {"type": "object", "properties": {}},
            },
        }
        self._tool_defs.append(tool_def)

    def unregister(self, name: str) -> bool:
        """Unregister a tool."""
        if name in self._tools:
            del self._tools[name]
            self._tool_defs = [d for d in self._tool_defs if d["function"]["name"] != name]
            return True
        return False

    def get(self, name: str) -> Optional[Callable]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get tool definitions for LLM."""
        return self._tool_defs

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """
        Execute a tool.

        Args:
            tool_name: Name of the tool to execute
            args: Tool arguments

        Returns:
            Tool execution result

        Raises:
            Fault: If tool not found or execution fails
        """
        func = self._tools.get(tool_name)
        if func is None:
            raise Fault(
                domain="TOOL",
                code="TOOL_NOT_FOUND",
                message=f"Tool '{tool_name}' not found",
                retryable=False,
            )

        # Execute (handle both sync and async)
        if asyncio.iscoroutinefunction(func):
            return await func(**args)
        else:
            return func(**args)


# =============================================================================
# AgentOS
# =============================================================================


class AgentOS:
    """
    Agent Operating System - The Top-Level Integration Layer.

    AgentOS provides a unified interface for running agent tasks.
    It manages processes, tools, and orchestrates all v2 components.

    Example:
        # Simple usage
        os = AgentOS(llm_client=my_llm, tools={"Read": read_fn})
        result = await os.run("Find all Python files")

        # With configuration
        config = AgentOSConfig(max_processes=5, default_timeout=60.0)
        os = AgentOS(llm_client=my_llm, tools={}, config=config)

        # Register tools after creation
        os.register_tool("Bash", bash_fn, description="Execute shell commands")

        # Run with DAG
        dag = create_linear_dag(["step1", "step2", "step3"])
        result = await os.run_dag(dag)
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: Optional[Dict[str, Callable]] = None,
        config: Optional[AgentOSConfig] = None,
    ):
        """
        Initialize AgentOS.

        Args:
            llm_client: LLM client for vCPUs
            tools: Initial tool registry {name: callable}
            config: AgentOS configuration
        """
        self._llm = llm_client
        self.config = config or AgentOSConfig()

        # Tool registry
        self._tools = ToolRegistry()
        if tools:
            for name, func in tools.items():
                self._tools.register(name, func)

        # Scheduler
        self._scheduler = Scheduler(
            config=self.config.scheduler_config,
            events=EventStream(),
        )

        # Process pool
        self._processes: Dict[str, Process] = {}

        # Shared event stream and IPC
        self._events = SimpleEventStream()
        self._ipc = SimpleIPCBus()

        # Chat session tracking
        self._current_session_id: Optional[str] = None
        
        # Session management (NEW)
        if self.config.enable_session and self.config.session_dir:
            self._session_mgr: Optional[SessionManager] = SessionManager(
                session_dir=self.config.session_dir
            )
        else:
            self._session_mgr = None
        
        # Compaction engine (NEW)
        self._compaction_engine = CompactionEngine(
            config=self.config.compaction_config,
            llm=DefaultCompactionLLM(llm_client),
        )

    # =========================================================================
    # Main API
    # =========================================================================

    async def run(self, goal: str, role: str = "") -> ToolResult:
        """
        Execute a single goal.

        This is the simplest entry point - just provide a goal string.

        Args:
            goal: The goal to achieve
            role: Optional process role (e.g., "eye", "body")

        Returns:
            ToolResult with the final result or error
        """
        # Spawn a process
        pid = self.spawn(goal, role=role)

        # Wait for completion
        return await self.wait(pid)

    async def chat(self, message: str, session_id: str | None = None) -> ToolResult:
        """
        Multi-turn chat with persistent context.

        This maintains conversation history across multiple calls.
        Use this for interactive chat sessions.

        Args:
            message: User message
            session_id: Optional session ID to resume. If None, creates new session.

        Returns:
            ToolResult with the response
        """
        # Get or create session process
        if session_id and session_id in self._processes:
            process = self._processes[session_id]
        else:
            # Create new session process
            # Use user-provided session_id if given, otherwise generate one
            if not session_id:
                session_id = f"chat-{uuid.uuid4().hex[:8]}"
            mmu = self._create_mmu(session_id)
            gate = self._create_gate(session_id, "chat")
            decoder = InstructionDecoder()

            vcpu = VCPU(
                alu=self._llm,
                decoder=decoder,
                gate=gate,
                mmu=mmu,
                config=self.config.vcpu_config,
                tools=self._tools.get_tool_definitions(),
            )

            process = Process(
                pid=session_id,
                goal="Interactive chat session",
                role="chat",
                state="RUNNING",
                vcpu=vcpu,
                mmu=mmu,
                gate=gate,
            )
            self._processes[session_id] = process
            self._emit_event("PROC_SPAWNED", session_id, {"goal": "chat", "role": "chat"})

        # Reset VCPU state for new turn (but keep MMU history)
        process.vcpu._reset()
        
        # Session persistence: 追加用户消息
        if self._session_mgr:
            from nimbus.core.memory.context import Message
            self._session_mgr.append_message(Message(role="user", content=message))

        # Execute one turn
        # Note: vcpu.execute will add the user message to MMU, so we don't add it here
        try:
            result = await process.vcpu.execute(message)

            # Session persistence: 追加助手响应
            if self._session_mgr and result.output:
                from nimbus.core.memory.context import Message
                self._session_mgr.append_message(
                    Message(role="assistant", content=str(result.output))
                )
            
            # Check if compaction needed
            await self._check_compaction(process)

            # Create a new result with session_id in output if needed
            # We use a simple convention: if the result needs session tracking,
            # we store session_id separately
            self._current_session_id = session_id

            return result

        except Exception as e:
            import traceback
            import logging
            logger = logging.getLogger(__name__)
            error_detail = traceback.format_exc()
            logger.error(f"❌ [AgentOS.chat] Exception in vcpu.execute: {e}")
            logger.error(f"Traceback:\n{error_detail}")
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=str(e),
                    retryable=False,
                    context={"traceback": error_detail},
                ),
            )

    def get_session(self, session_id: str) -> "Process | None":
        """Get a chat session process by ID."""
        return self._processes.get(session_id)

    def end_session(self, session_id: str) -> None:
        """End a chat session and cleanup resources."""
        if session_id in self._processes:
            process = self._processes.pop(session_id)
            process.state = "COMPLETED"
            self._emit_event("PROC_FINISHED", session_id, {"reason": "session_ended"})
    
    # =========================================================================
    # Session Management (NEW)
    # =========================================================================
    
    def new_session(self, parent_session: Optional[str] = None) -> str:
        """
        创建新会话。
        
        Args:
            parent_session: 可选的父会话路径
        
        Returns:
            新会话 ID
        """
        if self._session_mgr:
            return self._session_mgr.new_session(parent_session)
        return f"ephemeral-{uuid.uuid4().hex[:8]}"
    
    def load_session(self, session_file: Path) -> bool:
        """
        加载已有会话。
        
        Args:
            session_file: 会话文件路径
        
        Returns:
            是否成功加载
        """
        if not self._session_mgr:
            return False
        return self._session_mgr.load_session(session_file)
    
    def get_session_stats(self) -> Optional[Dict[str, Any]]:
        """获取当前会话统计"""
        if self._session_mgr:
            return self._session_mgr.get_stats()
        return None
    
    def list_recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """列出最近的会话"""
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
        """
        手动触发 compaction。
        
        Args:
            session_id: 要压缩的会话 ID（默认当前会话）
            custom_instructions: 自定义压缩指令
        
        Returns:
            压缩结果或 None
        """
        target_session = session_id or self._current_session_id
        if not target_session:
            return None
        
        process = self._processes.get(target_session)
        if not process or not process.mmu:
            return None
        
        # 获取所有消息
        from nimbus.core.memory.context import Message
        all_messages = []
        for frame in process.mmu._stack:
            for msg in frame.messages:
                all_messages.append(msg)
        
        # 执行压缩
        new_messages, result = await self._compaction_engine.compact(
            all_messages, custom_instructions
        )
        
        if result.messages_removed > 0:
            # 更新 MMU（简化处理：清除并重新添加）
            process.mmu.clear()
            for msg in new_messages:
                process.mmu.add_message(msg)
            
            # 持久化压缩记录
            if self._session_mgr:
                self._session_mgr.append_compaction(
                    summary=result.summary,
                    first_kept_entry_id=result.first_kept_entry_id or "",
                    tokens_before=result.tokens_before,
                    details=result.details,
                )
            
            self._emit_event("COMPACTION", target_session, {
                "tokens_before": result.tokens_before,
                "tokens_after": result.tokens_after,
                "messages_removed": result.messages_removed,
            })
        
        return {
            "summary": result.summary,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "messages_removed": result.messages_removed,
            "compression_ratio": result.compression_ratio,
        }
    
    async def _check_compaction(self, process: Process) -> None:
        """
        检查是否需要自动 compaction。
        """
        if not process.mmu:
            return
        
        # 获取当前 token 使用量
        current_tokens = process.mmu.estimate_tokens()
        max_tokens = self.config.mmu_config.max_context_tokens
        
        # 检查是否达到阈值
        if self._compaction_engine.should_compact(
            [msg for frame in process.mmu._stack for msg in frame.messages],
            max_tokens,
        ):
            self._emit_event("AUTO_COMPACTION_START", process.pid, {
                "tokens": current_tokens,
                "max_tokens": max_tokens,
            })
            
            await self.compact(session_id=process.pid)
            
            self._emit_event("AUTO_COMPACTION_END", process.pid, {})

    async def run_dag(self, dag: DAG) -> ToolResult:
        """
        Execute a DAG of tasks.

        Args:
            dag: The DAG to execute

        Returns:
            ToolResult from the root task
        """
        # Submit DAG to scheduler
        await self._scheduler.submit_dag(dag)

        # Create executor function
        async def executor(task: Task) -> ToolResult:
            return await self.run(task.spec.goal, role=task.spec.process_role)

        # Run DAG
        return await self._scheduler.run_dag(dag.id, executor)

    # =========================================================================
    # Process Management
    # =========================================================================

    def spawn(self, goal: str, role: str = "") -> str:
        """
        Spawn a new process.

        Creates a new process with its own VCPU, MMU, and Gate.
        The process is started but not awaited.

        Args:
            goal: The goal for this process
            role: Process role (e.g., "eye", "body", "mind")

        Returns:
            Process ID (pid)
        """
        # Check process limit
        active_count = sum(1 for p in self._processes.values() if p.state == "RUNNING")
        if active_count >= self.config.max_processes:
            raise RuntimeError(f"Process limit reached: {self.config.max_processes}")

        # Generate PID
        pid = f"proc-{uuid.uuid4().hex[:8]}"

        # Create components for this process
        mmu = self._create_mmu(pid)
        gate = self._create_gate(pid, role)
        decoder = InstructionDecoder()

        # Create VCPU
        vcpu = VCPU(
            alu=self._llm,
            decoder=decoder,
            gate=gate,
            mmu=mmu,
            config=self.config.vcpu_config,
            tools=self._tools.get_tool_definitions(),
        )

        # Create process
        process = Process(
            pid=pid,
            goal=goal,
            role=role,
            state="PENDING",
            vcpu=vcpu,
            mmu=mmu,
            gate=gate,
        )

        self._processes[pid] = process

        # Emit spawn event
        self._emit_event("PROC_SPAWNED", pid, {"goal": goal, "role": role})

        return pid

    async def wait(self, pid: str) -> ToolResult:
        """
        Wait for a process to complete.

        If the process hasn't started yet, this will start it.

        Args:
            pid: Process ID to wait for

        Returns:
            ToolResult from the process
        """
        process = self._processes.get(pid)
        if not process:
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=f"Process not found: {pid}",
                    retryable=False,
                ),
            )

        # Start if not yet running
        if process.state == "PENDING":
            process.state = "RUNNING"
            process.task = asyncio.create_task(self._run_process(process))

        # Wait for completion
        if process.task:
            try:
                result: ToolResult = await process.task
                return result
            except asyncio.CancelledError:
                return ToolResult(
                    status="CANCELLED",
                    fault=Fault(
                        domain="KERNEL",
                        code="SYSTEM_ERROR",
                        message="Process was cancelled",
                        retryable=True,
                    ),
                )

        # Already completed
        return process.result or ToolResult(status="OK", output="Process completed")

    def kill(self, pid: str) -> bool:
        """
        Kill a process.

        Args:
            pid: Process ID to kill

        Returns:
            True if process was killed, False if not found or already completed
        """
        process = self._processes.get(pid)
        if not process:
            return False

        if process.state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return False

        # Cancel the task
        if process.task and not process.task.done():
            process.task.cancel()

        process.state = "CANCELLED"
        process.result = ToolResult(
            status="CANCELLED",
            fault=Fault(
                domain="KERNEL",
                code="SYSTEM_ERROR",
                message="Process was killed",
                retryable=False,
            ),
        )

        # Emit event
        self._emit_event("PROC_FINISHED", pid, {"state": "CANCELLED"})

        return True

    def get_process(self, pid: str) -> Optional[Process]:
        """Get a process by PID."""
        return self._processes.get(pid)

    def list_processes(self) -> List[str]:
        """List all process IDs."""
        return list(self._processes.keys())

    def get_active_processes(self) -> List[str]:
        """List all running process IDs."""
        return [pid for pid, p in self._processes.items() if p.state == "RUNNING"]

    # =========================================================================
    # Tool Management
    # =========================================================================

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Register a tool.

        Args:
            name: Tool name
            func: Tool function
            description: Tool description
            parameters: Tool parameters schema
        """
        self._tools.register(name, func, description, parameters)

    def unregister_tool(self, name: str) -> bool:
        """Unregister a tool."""
        return self._tools.unregister(name)

    def list_tools(self) -> List[str]:
        """List all registered tools."""
        return self._tools.list_tools()

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

    def _create_mmu(self, pid: str) -> MMU:
        """Create an MMU for a process."""
        mmu = MMU(config=self.config.mmu_config, process_id=pid)

        # Set pinned context
        pinned = PinnedContext(
            system_rules=self.config.system_rules,
            workspace_info=self.config.workspace_info,
            capabilities=self.config.capabilities,
        )
        mmu.set_pinned(pinned)

        return mmu

    def _create_gate(self, pid: str, role: str = "") -> KernelGate:
        """Create a KernelGate for a process."""
        # Create permission manager based on role
        # By default, allow all tools. Subclasses can customize.
        perm = SimplePermissionManager(allowed_tools=["*"])

        return KernelGate(
            pid=pid,
            permission_mgr=perm,
            event_stream=self._events,
            tool_executor=self._tools,
            ipc_bus=self._ipc,
            default_timeout=self.config.default_timeout,
        )

    async def _run_process(self, process: Process) -> ToolResult:
        """
        Run a process to completion.

        Args:
            process: The process to run

        Returns:
            ToolResult from the process
        """
        try:
            if process.vcpu is None:
                raise RuntimeError("Process has no VCPU")

            # Execute via VCPU
            result = await process.vcpu.execute(process.goal)

            # Update state
            if result.status == "OK":
                process.state = "SUCCEEDED"
            elif result.status == "CANCELLED":
                process.state = "CANCELLED"
            else:
                process.state = "FAILED"

            process.result = result

            # Emit event
            self._emit_event(
                "PROC_FINISHED",
                process.pid,
                {
                    "state": process.state,
                    "status": result.status,
                },
            )

            return result

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

    Returns:
        Configured AgentOS instance with default tools registered
    """
    from pathlib import Path

    if workspace is None:
        workspace = Path.cwd()

    config = AgentOSConfig(
        max_processes=max_processes,
        default_timeout=default_timeout,
        system_rules=system_rules or AgentOSConfig.system_rules,
        workspace_info=f"Workspace: {workspace}",
    )

    os = AgentOS(llm_client=llm_client, tools=tools, config=config)

    # Register default v2 native tools
    if register_defaults:
        from nimbus.tools import register_default_tools
        register_default_tools(os, workspace=workspace)

    return os
