"""Session Manager for Agent instance pooling.

This module provides:
- SessionManager: Manages session lifecycle and Agent instances
- Agent instance pooling and reuse
- Memory and DAG state synchronization
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .sse import SSEHub
from .permission import PermissionManager


class SessionManager:
    """
    Manages session lifecycle and Agent instances.

    Responsibilities:
    - Create/get/delete sessions
    - Pool and reuse Agent instances
    - Synchronize state with storage
    - Handle session cleanup
    """

    def __init__(
        self,
        storage,  # SQLiteStorage
        sse_hub: SSEHub,
        permission_manager: PermissionManager,
        max_sessions: int = 10,
    ):
        """
        Initialize session manager.

        Args:
            storage: Storage backend for persistence.
            sse_hub: SSE hub for event streaming.
            permission_manager: Permission manager for tool control.
            max_sessions: Maximum concurrent active sessions.
        """
        self._storage = storage
        self._sse_hub = sse_hub
        self._permission_manager = permission_manager
        self._max_sessions = max_sessions
        self._agents: Dict[str, Any] = {}  # session_id -> Agent
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        memory_type: str = "tiered",
        planner_type: str = "dag",
    ) -> Dict[str, Any]:
        """
        Create a new session.

        Args:
            name: Optional session name.
            workspace_path: Optional working directory.
            memory_type: Memory type (simple, tiered).
            planner_type: Planner type (simple, dag).

        Returns:
            Session info dictionary.
        """
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        session = await self._storage.create_session(
            session_id=session_id,
            name=name,
            workspace_path=workspace_path,
            memory_type=memory_type,
            planner_type=planner_type,
        )

        return session

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session by ID.

        Args:
            session_id: Session ID.

        Returns:
            Session info or None if not found.
        """
        return await self._storage.get_session(session_id)

    async def list_sessions(
        self,
        status: str = "active",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        List sessions with pagination.

        Args:
            status: Filter by status.
            limit: Maximum results.
            offset: Pagination offset.

        Returns:
            Tuple of (sessions, total_count).
        """
        return await self._storage.list_sessions(
            status=status,
            limit=limit,
            offset=offset,
        )

    async def delete_session(self, session_id: str) -> None:
        """
        Soft delete a session.

        Args:
            session_id: Session to delete.
        """
        # Remove agent if loaded
        async with self._lock:
            if session_id in self._agents:
                del self._agents[session_id]

        # Cancel pending permissions
        self._permission_manager.cancel_pending(session_id)

        # Mark as deleted in storage
        await self._storage.delete_session(session_id)

    async def get_or_create_agent(self, session_id: str, llm_client=None) -> Any:
        """
        Get or create an Agent instance for a session.

        Args:
            session_id: Session ID.
            llm_client: Optional LLM client to use.

        Returns:
            Agent instance.

        Raises:
            ValueError: If session not found.
        """
        async with self._lock:
            if session_id in self._agents:
                return self._agents[session_id]

        # Get session info
        session = await self._storage.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        # Import here to avoid circular imports
        from nimbus.core.agent import NotebookAgent
        from nimbus.core.memory import MemoryConfig

        # Create default LLM client if not provided
        if llm_client is None:
            llm_client = await self._create_default_llm_client()

        # Create agent with appropriate memory and planner type
        agent = NotebookAgent(
            llm_client=llm_client,
            memory_type=session.get("memory_type", "tiered"),
            memory_config=MemoryConfig(),
            planner_type=session.get("planner_type", "dag"),
            session_id=session_id,
        )

        async with self._lock:
            self._agents[session_id] = agent

        return agent

    async def _create_default_llm_client(self):
        """Create default Ollama LLM client."""
        import os
        import aiohttp

        ollama_model = os.environ.get("NIMBUS_LLM_MODEL", "qwen3:8b")
        ollama_url = os.environ.get("NIMBUS_LLM_URL", "http://localhost:11434")

        class OllamaClient:
            def __init__(self, model: str, base_url: str):
                self.model = model
                self.base_url = base_url

            async def complete(self, prompt: str) -> str:
                url = f"{self.base_url}/api/generate"
                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.7, "num_predict": 1024},
                }
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.post(
                        url, json=payload,
                        timeout=aiohttp.ClientTimeout(total=120)
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            raise RuntimeError(f"Ollama error: {resp.status} - {text}")
                        result = await resp.json()
                        return result.get("response", "")

        return OllamaClient(model=ollama_model, base_url=ollama_url)

    async def save_session_state(self, session_id: str) -> None:
        """
        Save current session state to storage.

        Args:
            session_id: Session to save.
        """
        async with self._lock:
            agent = self._agents.get(session_id)

        if agent and hasattr(agent, '_memory'):
            await self._storage.save_memory_checkpoint(
                session_id,
                agent._memory,
            )

    async def close_all(self) -> None:
        """Close all active sessions and save state."""
        async with self._lock:
            for session_id in list(self._agents.keys()):
                try:
                    await self.save_session_state(session_id)
                except Exception:
                    pass  # Log but continue
            self._agents.clear()

    def get_active_count(self) -> int:
        """Get number of active agent instances."""
        return len(self._agents)

    def is_session_loaded(self, session_id: str) -> bool:
        """Check if a session has an active agent."""
        return session_id in self._agents
