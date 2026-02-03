"""
Nimbus Session Pool - Multi-Session Management

The Session Pool manages the lifecycle of SessionInstances:
- Active sessions (in-memory)
- Hibernated sessions (on-disk)
- Resource quota enforcement

Architecture:
    ┌──────────────────────────────────────────────┐
    │                Session Pool                  │
    │  ┌───────────────┐      ┌─────────────────┐  │
    │  │ Active (Dict) │      │ Storage (DB)    │  │
    │  └───────┬───────┘      └────────┬────────┘  │
    │          │                       │           │
    │  ┌───────▼────────┐     ┌────────▼────────┐  │
    │  │ SessionInstance│ ◄── │ Checkpoint JSON │  │
    │  └────────────────┘     └─────────────────┘  │
    └──────────────────────────────────────────────┘
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.runtime.vcpu import LLMClient
from nimbus.storage.sqlite import SQLiteStorage

logger = logging.getLogger("nimbus.pool")

@dataclass
class ResourceQuota:
    """Resource limits for a session."""
    max_memory_mb: int = 512
    max_cpu_time: float = 3600.0  # seconds
    max_concurrent_tools: int = 5

@dataclass
class SessionConfig:
    """Configuration for a session instance."""
    session_id: str
    workspace_path: str = "./workspace"
    quota: ResourceQuota = field(default_factory=ResourceQuota)
    enable_auto_hibernate: bool = True
    hibernate_after_seconds: float = 3600.0

class SessionInstance:
    """
    An active session instance wrapping AgentOS.

    This is the unit of isolation. In the future, this could wrap
    a separate process or container. For now, it wraps an AgentOS object.
    """

    def __init__(self, config: SessionConfig, storage: SQLiteStorage, llm_client: LLMClient):
        self.config = config
        self.storage = storage
        self.llm_client = llm_client
        self.agent_os: Optional[AgentOS] = None
        self.last_accessed: float = time.time()
        self.is_active: bool = False

        # Resource tracking
        self.start_time: float = 0.0

    async def initialize(self) -> None:
        """Initialize the AgentOS instance."""
        if self.agent_os:
            return

        logger.info(f"Initializing session {self.config.session_id}")

        # Initialize AgentOS with vCPU/MMU
        self.agent_os = AgentOS(
            llm_client=self.llm_client,
            config=AgentOSConfig(
                workspace_info=f"Workspace: {self.config.workspace_path}",
                # Additional config mapping here
            )
        )

        # Hook up storage for persistence if needed
        # self.agent_os.set_storage(self.storage)

        self.is_active = True
        self.start_time = time.time()
        self.last_accessed = time.time()

    async def touch(self) -> None:
        """Update last accessed time."""
        self.last_accessed = time.time()

    async def hibernate(self) -> bool:
        """
        Hibernate the session: Save state to DB and release memory.
        """
        if not self.agent_os:
            return False

        logger.info(f"Hibernating session {self.config.session_id}")

        # 1. Find active process (Phase 2 constraint: only 1 process supported)
        active_processes = self.agent_os.get_active_processes()
        if not active_processes and not self.agent_os.list_processes():
            # Nothing to save, just release
            self.agent_os = None
            self.is_active = False
            return True

        target_pid = active_processes[0] if active_processes else self.agent_os.list_processes()[0]
        process = self.agent_os.get_process(target_pid)

        if process and process.vcpu:
             checkpoint = process.vcpu.create_checkpoint(
                 session_id=self.config.session_id,
                 reason="hibernation"
             )

             # Save PID in metadata so we can restore it correctly (hacky)
             # Phase 3 should add 'processes' list to SessionCheckpointModel

             # 2. Save to Storage
             await self.storage.save_session_checkpoint(checkpoint)

             # 3. Release resources
             self.agent_os = None
             self.is_active = False
             return True

        logger.warning(f"Session {self.config.session_id} has no valid process, hibernate skipped.")
        return False

    async def wake(self) -> bool:
        """
        Wake the session: Restore state from DB.
        """
        if self.is_active and self.agent_os:
            return True

        logger.info(f"Waking session {self.config.session_id}")

        # 1. Initialize fresh AgentOS
        await self.initialize()

        # 2. Load latest checkpoint
        checkpoint = await self.storage.load_latest_session_checkpoint(self.config.session_id)

        if checkpoint and self.agent_os:
            # 3. Spawn a process to hold the restored state
            # We assume it was the main process. Goal doesn't matter as state overwrites it.
            pid = self.agent_os.spawn(goal="Resumed Session", role="resumed")
            process = self.agent_os.get_process(pid)

            if process and process.vcpu:
                # 4. Restore State
                process.vcpu.restore_from_checkpoint(checkpoint)
                logger.info(f"Session {self.config.session_id} restored to step {checkpoint.step_index}")
                return True

        logger.info(f"No checkpoint found for {self.config.session_id}, starting fresh.")
        return True

    async def interrupt(self) -> bool:
        """
        Request graceful interruption of the session.
        """
        if not self.is_active or not self.agent_os:
            return False

        logger.info(f"Requesting interruption for session {self.config.session_id}")
        # In multi-process AgentOS, we might need to specify PID, but if we assume 1 main session process
        # matching session_id or just interrupt all:
        return self.agent_os.interrupt()

    async def inject_message(self, content: str) -> bool:
        """
        Inject a user message into the running session for human-in-the-loop steering.
        """
        if not self.is_active or not self.agent_os:
            return False

        await self.touch()

        # Find active process (Assume 1 main process for now)
        active_processes = self.agent_os.get_active_processes()

        target_pid = None
        if active_processes:
            target_pid = active_processes[0]
        elif self.agent_os.list_processes():
            target_pid = self.agent_os.list_processes()[0]

        if not target_pid:
             logger.warning(f"No process found for session {self.config.session_id} to inject message")
             return False

        process = self.agent_os.get_process(target_pid)
        if process and process.vcpu:
            process.vcpu.inject_message(content)
            return True

        return False


class SessionPool:
    """
    Manages a pool of SessionInstances.
    """

    def __init__(self, storage: SQLiteStorage, llm_client: LLMClient):
        self.storage = storage
        self.llm_client = llm_client
        self._sessions: Dict[str, SessionInstance] = {}
        self._lock = asyncio.Lock()

    async def get_session(self, session_id: str, auto_create: bool = True) -> Optional[SessionInstance]:
        """
        Get a session instance, waking it if necessary.
        """
        async with self._lock:
            instance = self._sessions.get(session_id)

            if instance:
                await instance.touch()
                if not instance.is_active:
                    await instance.wake()
                return instance

            if not auto_create:
                return None

            # Create new instance
            config = SessionConfig(session_id=session_id)
            instance = SessionInstance(config, self.storage, self.llm_client)

            # Try to restore from checkpoint BEFORE initialize
            checkpoint = await self.storage.load_latest_session_checkpoint(session_id)

            # Initialize AgentOS
            await instance.initialize()

            # If checkpoint exists, spawn process and restore state
            if checkpoint and instance.agent_os:
                logger.info(f"Found checkpoint for {session_id}, restoring...")
                pid = instance.agent_os.spawn(goal="Resumed Session", role="resumed")
                process = instance.agent_os.get_process(pid)
                if process and process.vcpu:
                    process.vcpu.restore_from_checkpoint(checkpoint)
                    # Clear interruption flag from previous session
                    process.vcpu._state.interruption_requested = False
                    logger.info(f"Session {session_id} restored to step {checkpoint.step_index}")

            self._sessions[session_id] = instance
            return instance

    async def hibernate_all(self) -> None:
        """Hibernate all active sessions."""
        async with self._lock:
            for session_id, instance in self._sessions.items():
                if instance.is_active:
                    await instance.hibernate()

    async def cleanup_idle_sessions(self, max_idle_seconds: float = 3600.0) -> int:
        """Hibernate sessions that have been idle too long."""
        count = 0
        now = time.time()
        async with self._lock:
             for session_id, instance in self._sessions.items():
                 if instance.is_active and (now - instance.last_accessed > max_idle_seconds):
                     if await instance.hibernate():
                         count += 1
        return count

    async def inject_message(self, session_id: str, content: str) -> bool:
        """Inject message into a running session."""
        async with self._lock:
            instance = self._sessions.get(session_id)
            if instance and instance.is_active:
                return await instance.inject_message(content)
        return False
