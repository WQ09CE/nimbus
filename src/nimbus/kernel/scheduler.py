"""
Process Manager - fork/wait/kill operations.

Architecture Layer: 1 (Agent OS - Kernel)
Von Neumann Role: Process Manager (Kernel Service)

This module implements Unix-like process management for Agent OS:
- fork(): Create child process with context inheritance
- exec(): Start process execution
- wait(): Block until process completes
- kill(): Terminate process
- ps(): List processes
"""

__layer__ = 1
__role__ = "Process_Manager"

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, TYPE_CHECKING

from .proc import AgentProcess, ProcessState

if TYPE_CHECKING:
    from .vcpu_pool import vCPUPool

logger = logging.getLogger(__name__)


class ProcessManager:
    """
    Process Manager for Agent OS.

    Manages the process table and provides Unix-like process operations.
    Thread-safe for concurrent access via asyncio locks.
    """

    # Maximum process hierarchy depth to prevent infinite recursion
    MAX_DEPTH = 10

    # Default resource quotas
    DEFAULT_TOKEN_BUDGET = 50000
    DEFAULT_MAX_TURNS = 50

    def __init__(self, vcpu_pool: Optional["vCPUPool"] = None) -> None:
        """Initialize process manager with init process.

        Args:
            vcpu_pool: Optional vCPU pool for multi-vCPU support.
                      If provided, exec() will use the pool to select vCPU
                      based on process vcpu_affinity.
        """
        self._process_table: Dict[str, AgentProcess] = {}
        self._current_pid: Optional[str] = None
        self._lock = asyncio.Lock()
        self._executor: Optional[Callable] = None  # vCPU callback (legacy single vCPU)
        self._vcpu_pool: Optional["vCPUPool"] = vcpu_pool
        self._init_process()

    def _init_process(self) -> None:
        """Create init process (PID 1) - the root of all processes."""
        init = AgentProcess.create(role="init", parent_pid=None, depth=0)
        init.pid = "proc_init"
        init.state = ProcessState.RUNNING
        init.started_at = datetime.now()
        init.system_prompt = "Agent OS Init Process"
        self._process_table[init.pid] = init
        self._current_pid = init.pid
        logger.debug(f"Init process created: {init.pid}")

    def set_executor(self, executor: Callable) -> None:
        """
        Set the vCPU executor callback (legacy single vCPU mode).

        Args:
            executor: Async function that executes a process
                     Signature: async def executor(proc: AgentProcess) -> None
        """
        self._executor = executor

    def set_vcpu_pool(self, vcpu_pool: "vCPUPool") -> None:
        """
        Set the vCPU pool for multi-vCPU execution.

        When a vCPU pool is set, exec() will select the appropriate vCPU
        based on process vcpu_affinity.

        Args:
            vcpu_pool: The vCPU pool to use
        """
        self._vcpu_pool = vcpu_pool

    def _get_executor_for_process(self, proc: AgentProcess) -> Optional[Callable]:
        """
        Get the executor function for a process.

        Routing priority:
        1. If vCPU pool is set, use pool to select based on affinity
        2. Otherwise, use legacy single executor

        Args:
            proc: The process to get executor for

        Returns:
            Executor function, or None if no executor available
        """
        # Try vCPU pool first (affinity-aware routing)
        if self._vcpu_pool is not None:
            vcpu = self._vcpu_pool.get_for_process(proc)
            if vcpu is not None:
                return vcpu.execute

        # Fall back to legacy single executor
        return self._executor

    def fork(
        self,
        parent_pid: str,
        role: str,
        task: str,
        allowed_tools: Optional[Set[str]] = None,
        max_token_budget: int = DEFAULT_TOKEN_BUDGET,
        max_turns: int = DEFAULT_MAX_TURNS,
        system_prompt: str = "",
        priority: int = 0,
        vcpu_affinity: Optional[str] = None,
    ) -> str:
        """
        Create a child process (fork).

        Follows Unix fork semantics:
        - Child inherits parent's depth + 1
        - Child has its own memory space (context isolation)
        - Child is added to parent's children list

        Args:
            parent_pid: Parent process ID
            role: Child process role
            task: Task instruction for child
            allowed_tools: Set of allowed tool names
            max_token_budget: Maximum token budget
            max_turns: Maximum conversation turns
            system_prompt: System prompt for child
            priority: Scheduling priority
            vcpu_affinity: Optional vCPU ID to bind process to

        Returns:
            Child process ID

        Raises:
            ValueError: If parent not found or max depth exceeded
            PermissionError: If parent cannot fork (not running)
        """
        parent = self._process_table.get(parent_pid)
        if not parent:
            raise ValueError(f"Parent process {parent_pid} not found")

        if not parent.can_fork() and parent_pid != "proc_init":
            raise PermissionError(f"Process {parent_pid} cannot fork in state {parent.state.value}")

        if parent.depth >= self.MAX_DEPTH:
            raise ValueError(
                f"Maximum process depth ({self.MAX_DEPTH}) exceeded. Parent depth: {parent.depth}"
            )

        # Create child process with inherited context
        child = AgentProcess.create(
            role=role,
            parent_pid=parent_pid,
            depth=parent.depth + 1,
            task_instruction=task,
            allowed_tools=allowed_tools or set(),
            max_token_budget=max_token_budget,
            max_turns=max_turns,
            system_prompt=system_prompt,
            priority=priority,
            vcpu_affinity=vcpu_affinity,
        )

        # Create completion event for wait()
        child._completion_event = asyncio.Event()

        # Add to process table
        self._process_table[child.pid] = child
        parent.children.append(child.pid)

        logger.info(
            f"Process forked: {child.pid} (parent={parent_pid}, role={role}, depth={child.depth})"
        )

        return child.pid

    async def exec(self, pid: str) -> None:
        """
        Execute a created process (exec after fork).

        Transitions process from CREATED to READY/RUNNING.
        If executor is set, starts async execution.

        Args:
            pid: Process ID to execute

        Raises:
            ValueError: If process not found or not in CREATED state
        """
        proc = self._process_table.get(pid)
        if not proc:
            raise ValueError(f"Process {pid} not found")

        if proc.state != ProcessState.CREATED:
            raise ValueError(f"Process {pid} is in state {proc.state.value}, expected CREATED")

        proc.state = ProcessState.READY
        proc.started_at = datetime.now()
        logger.debug(f"Process {pid} ready for execution")

        # Select executor: vCPU pool (affinity-aware) or legacy single executor
        executor = self._get_executor_for_process(proc)

        if executor is not None:
            proc.state = ProcessState.RUNNING

            async def run_with_completion():
                try:
                    await executor(proc)
                finally:
                    if proc._completion_event:
                        proc._completion_event.set()

            proc._async_task = asyncio.create_task(run_with_completion())
            logger.debug(f"Process {pid} execution started")
        else:
            # Mock execution for testing
            proc.state = ProcessState.RUNNING
            # Simulate immediate completion
            proc.complete(result=f"Mock result for {proc.role}")
            if proc._completion_event:
                proc._completion_event.set()

    async def wait(
        self,
        pid: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Wait for process to complete.

        Blocks until process reaches terminal state or timeout.

        Args:
            pid: Process ID to wait for
            timeout: Maximum seconds to wait (None = infinite)

        Returns:
            Dict with pid, exit_code, result, error

        Raises:
            ValueError: If process not found
            asyncio.TimeoutError: If timeout exceeded
        """
        proc = self._process_table.get(pid)
        if not proc:
            raise ValueError(f"Process {pid} not found")

        # If already terminal, return immediately
        if proc.is_terminal():
            return self._wait_result(proc)

        # Wait for completion event
        if proc._completion_event:
            try:
                await asyncio.wait_for(
                    proc._completion_event.wait(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Wait for process {pid} timed out")
                raise

        # If async task exists, ensure it's done
        if proc._async_task:
            try:
                await asyncio.wait_for(
                    proc._async_task,
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(f"Process {pid} task timed out")
                raise
            except Exception as e:
                if not proc.is_terminal():
                    proc.fail(str(e))

        return self._wait_result(proc)

    def _wait_result(self, proc: AgentProcess) -> Dict[str, Any]:
        """Build wait result dictionary."""
        return {
            "pid": proc.pid,
            "role": proc.role,
            "exit_code": proc.exit_code,
            "result": proc.result,
            "error": proc.error,
            "state": proc.state.value,
            "token_usage": proc.token_usage,
            "turns": proc.current_turn,
        }

    def kill(self, pid: str, recursive: bool = True) -> bool:
        """
        Kill a process.

        Args:
            pid: Process ID to kill
            recursive: If True, also kill all descendants

        Returns:
            True if process was killed, False if not found
        """
        proc = self._process_table.get(pid)
        if not proc:
            return False

        if proc.pid == "proc_init":
            logger.warning("Cannot kill init process")
            return False

        # Kill children first if recursive
        if recursive:
            for child_pid in list(proc.children):
                self.kill(child_pid, recursive=True)

        # Cancel async task if running
        if proc._async_task and not proc._async_task.done():
            proc._async_task.cancel()

        proc.cancel()

        # Signal completion event
        if proc._completion_event:
            proc._completion_event.set()

        logger.info(f"Process {pid} killed")
        return True

    def reap(self, pid: str) -> Optional[Dict[str, Any]]:
        """
        Reap a zombie process (remove from process table).

        Should be called after wait() to clean up finished processes.

        Args:
            pid: Process ID to reap

        Returns:
            Process info dict if reaped, None if not found or not terminal
        """
        proc = self._process_table.get(pid)
        if not proc:
            return None

        if not proc.is_terminal():
            return None

        if proc.pid == "proc_init":
            return None

        # Remove from parent's children list
        if proc.parent_pid:
            parent = self._process_table.get(proc.parent_pid)
            if parent and pid in parent.children:
                parent.children.remove(pid)

        # Remove from process table
        result = proc.to_dict()
        del self._process_table[pid]

        logger.debug(f"Process {pid} reaped")
        return result

    def ps(self, parent_pid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List processes.

        Args:
            parent_pid: If provided, list only children of this process

        Returns:
            List of process info dictionaries
        """
        if parent_pid:
            parent = self._process_table.get(parent_pid)
            if not parent:
                return []
            return [
                self._process_table[pid].to_dict()
                for pid in parent.children
                if pid in self._process_table
            ]
        return [p.to_dict() for p in self._process_table.values()]

    def getpid(self) -> str:
        """Get current process ID."""
        return self._current_pid or "proc_init"

    def getproc(self, pid: str) -> Optional[AgentProcess]:
        """Get process by PID."""
        return self._process_table.get(pid)

    def setpid(self, pid: str) -> None:
        """Set current process context (for fork)."""
        if pid in self._process_table:
            self._current_pid = pid

    @property
    def process_count(self) -> int:
        """Get total number of processes."""
        return len(self._process_table)

    def tree(self, pid: str = "proc_init", indent: int = 0) -> str:
        """
        Get process tree as formatted string.

        Args:
            pid: Root process ID
            indent: Current indentation level

        Returns:
            Formatted process tree string
        """
        proc = self._process_table.get(pid)
        if not proc:
            return ""

        prefix = "  " * indent
        state_icon = {
            ProcessState.RUNNING: "[*]",
            ProcessState.COMPLETED: "[+]",
            ProcessState.FAILED: "[!]",
            ProcessState.CANCELLED: "[x]",
            ProcessState.READY: "[ ]",
            ProcessState.CREATED: "[ ]",
            ProcessState.BLOCKED: "[~]",
            ProcessState.ZOMBIE: "[z]",
        }.get(proc.state, "[?]")

        lines = [f"{prefix}{state_icon} {proc.pid} ({proc.role})"]
        for child_pid in proc.children:
            lines.append(self.tree(child_pid, indent + 1))

        return "\n".join(lines)
