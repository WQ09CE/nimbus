"""Session Manager V2 - Using AgentOS.

This module provides a session manager that uses the v2 AgentOS architecture
while maintaining compatibility with the v1 server API.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from nimbus import AgentOS, AgentOSConfig, create_agent_os
from nimbus.core.protocol import Event
from .sse import SSEHub
from .permission import PermissionManager

logger = logging.getLogger(__name__)


class SessionManagerV2:
    """
    Session manager using AgentOS v2.
    
    Responsibilities:
    - Create/manage sessions
    - Each session has its own AgentOS instance
    - Stream events to SSE hub
    """

    def __init__(
        self,
        storage,
        sse_hub: SSEHub,
        permission_manager: PermissionManager,
        max_sessions: int = 10,
    ):
        self._storage = storage
        self._sse_hub = sse_hub
        self._permission_manager = permission_manager
        self._max_sessions = max_sessions
        self._sessions: Dict[str, AgentOS] = {}  # session_id -> AgentOS
        self._lock = asyncio.Lock()
        self._shared_llm_client = None

    async def create_session(
        self,
        name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        memory_type: str = "tiered",
        planner_type: str = "dag",
        session_id: Optional[str] = None,
        model_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create a new session."""
        if session_id is None:
            session_id = f"sess_{uuid.uuid4().hex[:12]}"

        session = await self._storage.create_session(
            session_id=session_id,
            name=name,
            workspace_path=workspace_path,
            memory_type=memory_type,
            planner_type=planner_type,
            model_config=model_config,
        )

        logger.info(f"✨ Created session {session_id} (v2)")
        return session

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID."""
        return await self._storage.get_session(session_id)

    async def list_sessions(
        self,
        status: str = "active",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        """List sessions with pagination."""
        return await self._storage.list_sessions(
            status=status,
            limit=limit,
            offset=offset,
        )

    async def delete_session(self, session_id: str) -> None:
        """Soft delete a session."""
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]

        self._permission_manager.cancel_pending(session_id)
        await self._storage.delete_session(session_id)
        logger.info(f"🗑️ Deleted session {session_id}")

    async def get_or_create_agent(self, session_id: str, llm_client=None) -> AgentOS:
        """Get or create an AgentOS instance for a session."""
        async with self._lock:
            if session_id in self._sessions:
                agent = self._sessions[session_id]
                logger.info(f"📦 Returning cached AgentOS for session {session_id}")
                return agent

        # Get session info
        session = await self._storage.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        # Check for model config in session or use default
        model_config = session.get("model_config")
        
        # Create default LLM client if not provided
        if llm_client is None:
            # We need to get a new client if we have specific model config
            # otherwise we can use the shared one (if it matches default)
            if model_config:
                from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig
                config = PiLLMConfig(
                    provider=model_config.get("provider", "anthropic"),
                    model_id=model_config.get("model_id", "claude-sonnet-4-20250514")
                )
                adapter = PiLLMAdapter(config)
                await adapter.__aenter__()
                llm_client = adapter # This client will need to be closed manually or attached to session
                # TODO: Manage lifecycle of per-session LLM clients
            else:
                llm_client = await self._get_shared_llm_client()

        # Create AgentOS with default tools
        logger.info(f"🔧 Creating AgentOS for session {session_id}")
        agent_os = create_agent_os(
            llm_client=llm_client,
            tools={},
            max_processes=5,
            default_timeout=300.0,
            register_defaults=True,  # Auto-register default tools
        )

        async with self._lock:
            self._sessions[session_id] = agent_os

        return agent_os

    async def _get_shared_llm_client(self):
        """Get or create shared PiLLMAdapter."""
        if self._shared_llm_client is None:
            from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig
            
            # Create and start shared client
            # Using defaults which point to anthropic/claude-sonnet
            adapter = PiLLMAdapter()
            await adapter.__aenter__()
            self._shared_llm_client = adapter
            logger.info("🤖 Shared PiLLMAdapter initialized")
            
        return self._shared_llm_client

    def _create_default_llm_client(self):
        """DEPRECATED: Create default LLM client (v2-compatible)."""
        # Use v1 LLM client wrapped in adapter for v2 compatibility
        from nimbus.llm import create_llm_client
        from .llm_adapter import V1ToV2LLMAdapter
        
        # Create v1 client (with configured API keys)
        v1_client = create_llm_client()
        
        # Wrap it for v2 compatibility
        return V1ToV2LLMAdapter(v1_client)

    async def stream_chat(
        self,
        session_id: str,
        message: str,
    ):
        """
        Stream chat response with SSE events.
        
        Yields SSE events in the format expected by the API.
        """
        # Wait a bit to ensure SSE connection is established (fix race condition)
        await asyncio.sleep(0.5)

        logger.info(f"[stream_chat] Starting for session {session_id}")
        
        # Get or create AgentOS
        agent_os = await self.get_or_create_agent(session_id)
        
        # Emit connected event
        await self._sse_hub.publish(
            session_id,
            "connected",
            {"session_id": session_id, "timestamp": datetime.utcnow().isoformat()},
        )

        # Emit message_start
        await self._sse_hub.publish(
            session_id,
            "message_start",
            {"role": "assistant"},
        )

        try:
            # Setup real-time event streaming
            queue = asyncio.Queue()
            
            def event_listener(event):
                queue.put_nowait(event)
            
            # Add listener if supported
            if hasattr(agent_os, "add_event_listener"):
                agent_os.add_event_listener(event_listener)
            
            # Start consumer task
            async def event_consumer():
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    await self._emit_v2_event(session_id, event)
            
            consumer_task = asyncio.create_task(event_consumer())

            # Use chat method for multi-turn conversation
            logger.info(f"[stream_chat] Calling agent_os.chat...")
            try:
                result = await agent_os.chat(message, session_id=session_id)
                if result.status == "ERROR":
                    logger.error(f"[stream_chat] Result Error: {result.fault}")
                else:
                    logger.info(f"[stream_chat] Completed with status: {result.status}")
            except asyncio.CancelledError:
                # User interrupted - cleanup incomplete state
                logger.info(f"[stream_chat] Cancelled by user for session {session_id}")
                
                # Rollback incomplete messages from MMU
                if hasattr(agent_os, '_vcpu') and agent_os._vcpu:
                    mmu = agent_os._vcpu.mmu
                    # Remove the last incomplete assistant message and any pending tool results
                    mmu.rollback_incomplete_turn()
                    logger.info(f"[stream_chat] Rolled back incomplete turn")
                
                # Re-raise to propagate cancellation
                raise
            except Exception as chat_err:
                logger.error(f"[stream_chat] agent_os.chat FAILED: {chat_err}")
                raise
            finally:
                # Cleanup listener and consumer
                if hasattr(agent_os, "remove_event_listener"):
                    agent_os.remove_event_listener(event_listener)
                queue.put_nowait(None)
                await consumer_task

            # Emit the final message content
            if result.output:
                # Stream output character by character (simulating typing)
                content = str(result.output)
                chunk_size = 10  # Send 10 chars at a time
                for i in range(0, len(content), chunk_size):
                    chunk = content[i:i+chunk_size]
                    await self._sse_hub.publish(
                        session_id,
                        "message",
                        {"content": chunk},
                    )
                    await asyncio.sleep(0.01)  # Small delay for smooth streaming
            else:
                logger.warning(f"[stream_chat] NO OUTPUT in result for session {session_id}")

            # Clear events for next turn
            agent_os.clear_events()

            # Emit completion
            await self._sse_hub.publish(
                session_id,
                "dag_complete",
                {"status": result.status},
            )

        except asyncio.CancelledError:
            # Already handled above, just propagate
            raise
        except Exception as e:
            logger.error(f"Error in stream_chat: {e}", exc_info=True)
            await self._sse_hub.publish(
                session_id,
                "error",
                {"message": str(e)},
            )

    async def _emit_v2_event(self, session_id: str, event: Event):
        """Convert v2 Event to SSE event."""
        event_type = event.type.lower()
        
        # Map v2 event types to SSE event types
        type_mapping = {
            "tool_started": "tool_call",
            "tool_finished": "tool_result",
            "proc_spawned": "task_start",
            "proc_finished": "task_done",
            # Legacy/Alternative mappings
            "tool_call": "tool_call",
            "tool_result": "tool_result",
            "task_start": "task_start",
            "task_done": "task_done",
            "task_failed": "task_failed",
        }

        sse_type = type_mapping.get(event_type, "heartbeat")

        # Emit to SSE hub
        await self._sse_hub.publish(
            session_id,
            sse_type,
            event.data,
        )

    async def save_session_state(self, session_id: str) -> None:
        """Save session state (v2 handles this internally)."""
        # v2 AgentOS has built-in session persistence
        pass

    async def close_all(self) -> None:
        """Close all active sessions."""
        async with self._lock:
            self._sessions.clear()
            
        if self._shared_llm_client:
            logger.info("🔌 Closing shared PiLLMAdapter")
            await self._shared_llm_client.__aexit__(None, None, None)
            self._shared_llm_client = None

    def get_active_count(self) -> int:
        """Get number of active agent instances."""
        return len(self._sessions)

    def is_session_loaded(self, session_id: str) -> bool:
        """Check if a session has an active agent."""
        return session_id in self._sessions
