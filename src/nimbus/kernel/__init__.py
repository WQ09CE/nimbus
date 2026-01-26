"""
Nimbus Agent OS Kernel.

Architecture Layer: 1 (Agent OS - Kernel)

This module provides the core kernel abstractions for Agent OS:
- AgentProcess: Process Control Block (PCB)
- ProcessManager: Process lifecycle management (fork/exec/wait/kill)
- AgentOS: Unified kernel interface (spawn/wait/ps)
- IPC: Inter-process communication primitives

Usage Example:

    from nimbus.kernel import AgentOS

    async def main():
        kernel = AgentOS()

        # Spawn a process
        pid = await kernel.spawn(role="Brain", goal="Analyze code")

        # Wait for completion
        result = await kernel.wait(pid)
        print(result)

        # View process tree
        print(kernel.ps())
"""

__layer__ = 1
__version__ = "0.1.0"

from typing import Any, Callable, Dict, List, Optional, Set

from .ipc import IPCMessage, MessageType, Signal
from .proc import AgentProcess, ProcessState
from .scheduler import ProcessManager


class AgentOS:
    """
    Agent Operating System - Unified Kernel Interface.

    Provides a high-level API for managing agent processes,
    abstracting the complexity of process management.

    Attributes:
        process_manager: The underlying process manager
    """

    def __init__(self) -> None:
        """Initialize Agent OS with default process manager."""
        self.process_manager = ProcessManager()

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

        Returns:
            Process ID of the spawned process

        Example:
            pid = await kernel.spawn(
                role="Coder",
                goal="Implement feature X",
                allowed_tools={"Read", "Write", "Bash"},
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
