"""
Nimbus Agent OS Kernel.

Architecture Layer: 1 (Agent OS - Kernel)

This module provides the core kernel abstractions for Agent OS:
- AgentProcess: Process Control Block (PCB)
- ProcessManager: Process lifecycle management (fork/exec/wait/kill)
- AgentOS: Unified kernel interface (spawn/wait/ps)
- vCPU: Virtual processor for executing processes
- IPC: Inter-process communication primitives

Von Neumann Architecture Mapping:
- vCPU = Control Unit + MMU + Interrupt Handler
- LLMClient = ALU (Arithmetic Logic Unit)
- ToolRegistry = ISA (Instruction Set Architecture)
- AgentProcess.memory = Registers (context window)

Usage Example:

    from nimbus.kernel import AgentOS
    from nimbus.llm.factory import create_llm_client
    from nimbus.tools.base import ToolRegistry

    async def main():
        # Create kernel with LLM and tools
        llm = create_llm_client()
        tools = ToolRegistry()
        kernel = AgentOS(llm_client=llm, tool_registry=tools)

        # Spawn a process
        pid = await kernel.spawn(role="Brain", goal="Analyze code")

        # Wait for completion
        result = await kernel.wait(pid)
        print(result)

        # View process tree
        print(kernel.ps())
"""

__layer__ = 1
__version__ = "0.2.0"

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

from .ipc import IPCMessage, MessageType, Signal
from .proc import AgentProcess, ProcessState
from .scheduler import ProcessManager
from .vcpu import vCPU, vCPUConfig, vCPUError, ResourceLimitError, MaxIterationsError
from .vcpu_pool import vCPUPool


class AgentOS:
    """
    Agent Operating System - Unified Kernel Interface.

    Provides a high-level API for managing agent processes,
    abstracting the complexity of process management.

    The AgentOS integrates all kernel components:
    - ProcessManager: Process lifecycle management
    - vCPU: Virtual processor for execution
    - LLMClient: ALU for reasoning
    - ToolRegistry: ISA for actions

    Attributes:
        process_manager: The underlying process manager
        vcpu: Virtual CPU for process execution (optional)
        llm_client: LLM client for reasoning (optional)
        tool_registry: Tool registry for actions (optional)
    """

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        tool_registry: Optional[Any] = None,
        max_iterations: int = 50,
        workspace: Optional[Path] = None,
        llm_clients: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize Agent OS with optional vCPU components.

        Supports two modes:
        1. Single vCPU mode (legacy): Pass llm_client + tool_registry
        2. Multi vCPU mode: Pass llm_clients dict for multiple vCPUs

        Args:
            llm_client: LLM client (ALU) for reasoning. If provided with
                       tool_registry, enables vCPU execution (single vCPU mode).
            tool_registry: Tool registry (ISA) for actions.
            max_iterations: Maximum iterations per process for vCPU.
            workspace: Working directory for tool execution (file operations).
            llm_clients: Dict mapping vcpu_id to LLM client for multi-vCPU mode.
                        First key becomes the default vCPU.
                        Example: {"planner": planner_llm, "executor": executor_llm}

        Example (single vCPU - backward compatible):
            kernel = AgentOS(llm_client=llm, tool_registry=tools)

        Example (multi vCPU):
            kernel = AgentOS(
                llm_clients={"planner": planner_llm, "executor": executor_llm},
                tool_registry=tools
            )
        """
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.workspace = workspace

        # vCPU pool for multi-vCPU support
        self.vcpu_pool: Optional[vCPUPool] = None

        # Legacy single vCPU (for backward compatibility)
        self.vcpu: Optional[vCPU] = None

        # Multi-vCPU mode: llm_clients dict provided
        if llm_clients is not None and tool_registry is not None:
            self.vcpu_pool = vCPUPool()
            is_first = True
            for vcpu_id, client in llm_clients.items():
                vcpu = vCPU(
                    llm_client=client,
                    tool_registry=tool_registry,
                    max_iterations=max_iterations,
                    workspace=workspace,
                )
                self.vcpu_pool.register(vcpu_id, vcpu, is_default=is_first)
                is_first = False

            # Also set the first vCPU as self.vcpu for backward compat
            if self.vcpu_pool.default_id:
                self.vcpu = self.vcpu_pool.get_default()

        # Single vCPU mode (legacy): llm_client provided
        elif llm_client is not None and tool_registry is not None:
            self.vcpu = vCPU(
                llm_client=llm_client,
                tool_registry=tool_registry,
                max_iterations=max_iterations,
                workspace=workspace,
            )

        # Initialize process manager with optional vCPU pool
        self.process_manager = ProcessManager(vcpu_pool=self.vcpu_pool)

        # Connect vCPU pool or single vCPU as executor
        if self.vcpu_pool is not None:
            # Multi-vCPU mode: pool handles routing
            pass  # ProcessManager already has vcpu_pool
        elif self.vcpu is not None:
            # Single vCPU mode: use legacy executor
            self.process_manager.set_executor(self.vcpu.execute)

    def set_executor(self, executor: Callable) -> None:
        """
        Set the vCPU executor for process execution.

        Args:
            executor: Async function that executes a process
                     Signature: async def executor(proc: AgentProcess) -> None
        """
        self.process_manager.set_executor(executor)

    async def spawn(
        self,
        role: str,
        goal: str,
        allowed_tools: Optional[Set[str]] = None,
        parent_pid: Optional[str] = None,
        max_token_budget: int = 50000,
        max_turns: int = 50,
        system_prompt: str = "",
        priority: int = 0,
        vcpu_affinity: Optional[str] = None,
    ) -> str:
        """
        Spawn a new agent process (fork + exec).

        This is the primary way to create new agent processes.
        Combines Unix fork() and exec() into a single operation.

        Args:
            role: Process role (e.g., "Brain", "Coder", "Reviewer")
            goal: Task description / instruction
            allowed_tools: Set of allowed tool names (None = inherit)
            parent_pid: Parent process ID (None = use current)
            max_token_budget: Maximum token budget
            max_turns: Maximum conversation turns
            system_prompt: System prompt for the agent
            priority: Scheduling priority (higher = more priority)
            vcpu_affinity: vCPU ID to bind process to (None = use default)

        Returns:
            Process ID of the spawned process

        Example:
            pid = await kernel.spawn(
                role="Coder",
                goal="Implement feature X",
                allowed_tools={"Read", "Write", "Bash"},
            )

        Example (with vCPU affinity):
            pid = await kernel.spawn(
                role="Planner",
                goal="Create execution plan",
                vcpu_affinity="planner",
            )
        """
        if parent_pid is None:
            parent_pid = self.process_manager.getpid()

        # Fork
        pid = self.process_manager.fork(
            parent_pid=parent_pid,
            role=role,
            task=goal,
            allowed_tools=allowed_tools,
            max_token_budget=max_token_budget,
            max_turns=max_turns,
            system_prompt=system_prompt,
            priority=priority,
            vcpu_affinity=vcpu_affinity,
        )

        # Exec
        await self.process_manager.exec(pid)

        return pid

    async def wait(
        self,
        pid: str,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Wait for process to complete.

        Blocks until the process reaches a terminal state or timeout.

        Args:
            pid: Process ID to wait for
            timeout: Maximum seconds to wait (None = infinite)

        Returns:
            Dict containing:
            - pid: Process ID
            - role: Process role
            - exit_code: 0 for success, non-zero for failure
            - result: Process result (if completed)
            - error: Error message (if failed)
            - state: Final process state
            - token_usage: Total tokens consumed

        Raises:
            ValueError: If process not found
            asyncio.TimeoutError: If timeout exceeded

        Example:
            result = await kernel.wait(pid, timeout=60.0)
            if result["exit_code"] == 0:
                print(f"Success: {result['result']}")
            else:
                print(f"Failed: {result['error']}")
        """
        return await self.process_manager.wait(pid, timeout)

    def ps(self, parent_pid: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all processes.

        Args:
            parent_pid: If provided, list only children of this process

        Returns:
            List of process info dictionaries

        Example:
            # List all processes
            processes = kernel.ps()

            # List children of specific process
            children = kernel.ps(parent_pid="proc_abc123")
        """
        return self.process_manager.ps(parent_pid)

    def kill(self, pid: str, recursive: bool = True) -> bool:
        """
        Kill a process.

        Args:
            pid: Process ID to kill
            recursive: If True, also kill all descendants

        Returns:
            True if process was killed, False if not found

        Example:
            kernel.kill(pid)  # Kill process and all children
            kernel.kill(pid, recursive=False)  # Kill only this process
        """
        return self.process_manager.kill(pid, recursive)

    def tree(self) -> str:
        """
        Get process tree as formatted string.

        Returns:
            Formatted process tree

        Example:
            print(kernel.tree())
            # [*] proc_init (init)
            #   [*] proc_abc123 (Brain)
            #     [+] proc_def456 (Coder)
        """
        return self.process_manager.tree()

    def getpid(self) -> str:
        """Get current process ID."""
        return self.process_manager.getpid()

    def getproc(self, pid: str) -> Optional[AgentProcess]:
        """Get process by PID."""
        return self.process_manager.getproc(pid)

    @property
    def process_count(self) -> int:
        """Get total number of processes."""
        return self.process_manager.process_count


__all__ = [
    # Main interface
    "AgentOS",
    # vCPU
    "vCPU",
    "vCPUConfig",
    "vCPUPool",
    "vCPUError",
    "ResourceLimitError",
    "MaxIterationsError",
    # Process
    "AgentProcess",
    "ProcessState",
    # Manager
    "ProcessManager",
    # IPC
    "IPCMessage",
    "MessageType",
    "Signal",
]
