"""Session Manager V2 - Using AgentOS.

This module provides a session manager that uses the v2 AgentOS architecture
while maintaining compatibility with the v1 server API.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from nimbus import AgentOS, create_agent_os
from nimbus.core.protocol import Event

from .permission import PermissionManager
from .sse import SSEHub

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
                    model_id=model_config.get("model_id", "claude-sonnet-4-20250514"),
                )
                adapter = PiLLMAdapter(config)
                await adapter.__aenter__()
                llm_client = (
                    adapter  # This client will need to be closed manually or attached to session
                )
                # TODO: Manage lifecycle of per-session LLM clients
            else:
                llm_client = await self._get_shared_llm_client()

        # Get workspace path from session
        workspace_path = session.get("workspace_path")
        workspace = None
        if workspace_path:
            from pathlib import Path
            import os
            workspace = Path(os.path.expanduser(workspace_path))
            logger.info(f"📁 Using workspace: {workspace}")

        # Create AgentOS with default tools
        logger.info(f"🔧 Creating AgentOS for session {session_id}")
        agent_os = create_agent_os(
            llm_client=llm_client,
            tools={},
            max_processes=5,
            default_timeout=300.0,
            workspace=workspace,  # Pass workspace for tool sandboxing
            register_defaults=True,  # Auto-register default tools
        )

        async with self._lock:
            self._sessions[session_id] = agent_os

        return agent_os

    async def _get_shared_llm_client(self):
        """Get or create shared PiLLMAdapter."""
        if self._shared_llm_client is None:
            from nimbus.adapters.pi_adapter import PiLLMAdapter

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
            logger.info("[stream_chat] Calling agent_os.chat...")
            try:
                result = await agent_os.chat(message, session_id=session_id)
                
                # SPECIAL HANDLING FOR INJECTION
                if result.status == "OK" and result.output == "[Instruction appended to running task]":
                     # This was an injection, not a full turn.
                     # We must save the user message immediately because it's not in MMU yet.
                     import uuid
                     msg_id = f"msg_{uuid.uuid4().hex[:12]}"
                     await self._storage.add_message(
                        message_id=msg_id,
                        session_id=session_id,
                        role="user",
                        content=f"[Intervention] {message}", # Mark as intervention
                     )
                     logger.info(f"💾 Saved injected message '{message[:20]}...' to storage")
                     
                     # Emit completion and exit
                     await self._sse_hub.publish(session_id, "dag_complete", {"status": "OK"})
                     await self._sse_hub.close_session(session_id)
                     return

                if result.status == "ERROR":
                    logger.error(f"[stream_chat] Result Error: {result.fault}")
                elif result.status == "CANCELLED":
                    logger.info("[stream_chat] Execution cancelled by interrupt request")
                    # Manual Fix: Drain pending injection queue from vCPU to MMU so they are saved
                    try:
                        proc = agent_os.get_process(session_id)
                        if proc and proc.vcpu and hasattr(proc.vcpu, "_message_queue"):
                            while proc.vcpu._message_queue:
                                msg = proc.vcpu._message_queue.pop(0)
                                logger.info(f"Draining pending injection: {msg}")
                                proc.vcpu.mmu.add_user_message(f"[User Intervention] {msg} (Cancelled)")
                    except Exception as drain_err:
                        logger.warning(f"Failed to drain message queue: {drain_err}")
                else:
                    logger.info(f"[stream_chat] Completed with status: {result.status}")
            except asyncio.CancelledError:
                # User interrupted - cleanup incomplete state
                logger.info(f"[stream_chat] Cancelled by user for session {session_id}")

                # NEW: Save current progress before rollback
                # If the user cancels, we still want to keep what has been done so far.
                try:
                    if hasattr(agent_os, "_vcpu") and agent_os._vcpu:
                        mmu = agent_os._vcpu.mmu
                        # Before rolling back, save whatever state we have
                        logger.info("[stream_chat] Saving partial progress before interruption...")
                        await self._save_conversation_to_storage(session_id, agent_os, message)
                        
                        # Then rollback incomplete messages from MMU (e.g. pending tool calls)
                        mmu.rollback_incomplete_turn()
                        logger.info("[stream_chat] Rolled back incomplete turn")
                except Exception as e:
                    logger.error(f"[stream_chat] Failed to save partial progress: {e}")

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

            # Emit the final message content (only if not already streamed)
            if result.output and not (result.meta and result.meta.get("streamed")):
                # Stream output character by character (simulating typing)
                content = str(result.output)
                chunk_size = 10  # Send 10 chars at a time
                for i in range(0, len(content), chunk_size):
                    chunk = content[i : i + chunk_size]
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

            # Save assistant messages to storage
            await self._save_conversation_to_storage(session_id, agent_os, message)

            # Emit completion
            await self._sse_hub.publish(
                session_id,
                "dag_complete",
                {"status": result.status},
            )

            # Close SSE connection to signal end of stream
            await self._sse_hub.close_session(session_id)

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

    async def _save_conversation_to_storage(
        self, 
        session_id: str, 
        agent_os: AgentOS,
        user_message: str
    ):
        """
        Save conversation messages to storage after chat completion.
        
        This extracts messages from MMU and saves them to the database,
        so they can be restored when the user refreshes the page.
        """
        import json
        import uuid
        
        try:
            # Get the active process to access MMU
            pids = agent_os.list_processes()
            if not pids:
                logger.warning(f"No processes found for session {session_id}, cannot save messages")
                return
            
            process = agent_os.get_process(pids[0])
            if not process or not process.mmu:
                logger.warning(f"No MMU found for session {session_id}")
                return
            
            mmu = process.mmu
            
            # Get messages from the current frame
            if not mmu._stack:
                return
                
            frame = mmu.current_frame
            messages = frame.messages
            
            # Find new messages (after the user message we just received)
            # We need to save assistant messages and tool results
            found_user_msg = False
            messages_to_save = []
            
            for msg in messages:
                # Skip until we find the user message we're responding to
                if msg.role == "user" and msg.content == user_message:
                    found_user_msg = True
                    continue
                
                # After finding user message, save subsequent messages
                if found_user_msg:
                    messages_to_save.append(msg)
            
            # Save each message
            for msg in messages_to_save:
                msg_id = f"msg_{uuid.uuid4().hex[:12]}"
                
                # Prepare content - handle tool_calls specially
                content = msg.content or ""
                artifacts = None
                
                # If this is an assistant message with tool calls, store them as artifacts
                if msg.role == "assistant" and msg.tool_calls:
                    artifacts = [{
                        "type": "tool_calls",
                        "tool_calls": msg.tool_calls
                    }]
                
                # For tool results, include the tool name
                if msg.role == "tool":
                    artifacts = [{
                        "type": "tool_result",
                        "tool_call_id": msg.tool_call_id,
                        "name": msg.name,
                    }]
                
                await self._storage.add_message(
                    message_id=msg_id,
                    session_id=session_id,
                    role=msg.role,
                    content=content,
                    artifacts=artifacts,
                )
            
            logger.info(f"💾 Saved {len(messages_to_save)} messages for session {session_id}")
            
        except Exception as e:
            logger.error(f"Failed to save conversation to storage: {e}", exc_info=True)

    async def _emit_v2_event(self, session_id: str, event: Event):
        """Convert v2 Event to SSE event."""
        event_type = event.type.lower()

        # Map v2 event types to SSE event types
        type_mapping = {
            "tool_started": "tool_call",
            "tool_finished": "tool_result",
            "proc_spawned": "task_start",
            "proc_finished": "task_done",
            "step_started": "step_start",  # NEW: Signal new turn
            "thinking": "message",
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

    async def interrupt_session(self, session_id: str) -> Dict[str, Any]:
        """
        Interrupt a running session.
        
        This will:
        1. Request the vCPU to pause at next step
        2. Create a checkpoint and save to DB
        
        Returns:
            Dict with success status and checkpoint info
        """
        async with self._lock:
            agent_os = self._sessions.get(session_id)
        
        if not agent_os:
            return {"success": False, "error": "Session not loaded"}
        
        try:
            # Request interrupt on all active processes
            interrupted_count = 0
            for pid in agent_os.list_processes():
                process = agent_os.get_process(pid)
                if process and process.vcpu:
                    process.vcpu.request_pause()
                    interrupted_count += 1
                    logger.info(f"🛑 Requested pause for process {pid} in session {session_id}")
            
            # Create and save checkpoint
            checkpoint_info = None
            for pid in agent_os.list_processes():
                process = agent_os.get_process(pid)
                if process and process.vcpu:
                    checkpoint = process.vcpu.create_checkpoint(session_id, reason="interruption")
                    await self._storage.save_session_checkpoint(checkpoint)
                    checkpoint_info = {
                        "step_index": checkpoint.step_index,
                        "iteration": checkpoint.execution_state.iteration,
                        "memory_messages": len(checkpoint.memory_snapshot.stack[0].messages) if checkpoint.memory_snapshot.stack else 0,
                    }
                    logger.info(f"💾 Saved checkpoint for session {session_id} at step {checkpoint.step_index}")
                    break  # Only save first process for now
            
            return {
                "success": True,
                "session_id": session_id,
                "interrupted_processes": interrupted_count,
                "checkpoint": checkpoint_info,
            }
        except Exception as e:
            logger.error(f"Failed to interrupt session {session_id}: {e}")
            return {"success": False, "error": str(e)}
    
    async def resume_session(self, session_id: str) -> Dict[str, Any]:
        """
        Resume an interrupted session.
        
        This will:
        1. Load checkpoint from DB
        2. Restore vCPU state
        
        Returns:
            Dict with success status and restored info
        """
        try:
            # Load checkpoint
            checkpoint = await self._storage.load_latest_session_checkpoint(session_id)
            if not checkpoint:
                return {"success": False, "error": "No checkpoint found"}
            
            # Get or create AgentOS
            agent_os = await self.get_or_create_agent(session_id)
            
            # Check if we need to spawn a new process
            pids = agent_os.list_processes()
            if not pids:
                # Spawn a resumed process
                pid = agent_os.spawn(goal="Resumed Session", role="resumed")
                process = agent_os.get_process(pid)
                if process and process.vcpu:
                    process.vcpu.restore_from_checkpoint(checkpoint)
                    # Clear interruption flag
                    process.vcpu._state.interruption_requested = False
                    logger.info(f"🔄 Restored session {session_id} to step {checkpoint.step_index}")
            else:
                # Restore to existing process
                process = agent_os.get_process(pids[0])
                if process and process.vcpu:
                    process.vcpu.restore_from_checkpoint(checkpoint)
                    process.vcpu._state.interruption_requested = False
            
            return {
                "success": True,
                "session_id": session_id,
                "restored_step": checkpoint.step_index,
                "restored_iteration": checkpoint.execution_state.iteration,
            }
        except Exception as e:
            logger.error(f"Failed to resume session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    async def inject_message(self, session_id: str, content: str) -> bool:
        """Inject user message into running session."""
        async with self._lock:
            agent_os = self._sessions.get(session_id)
            
        if not agent_os:
            return False
            
        # Find active process
        pids = agent_os.get_active_processes()
        if not pids:
            # Try listing all processes (maybe it's paused/thinking but active)
            pids = agent_os.list_processes()
            if not pids:
                return False
            
        process = agent_os.get_process(pids[0])
        if process and process.vcpu:
            process.vcpu.inject_message(content)
            logger.info(f"💉 Injected message into session {session_id}")
            return True
            
        return False

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
