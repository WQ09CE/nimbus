"""Session Manager V2 - Using AgentOS.

This module provides a session manager that uses the v2 AgentOS architecture
while maintaining compatibility with the v1 server API.
"""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
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
        self._dispatch_tools: Dict[str, Any] = {}  # session_id -> DispatchTool (dual_agent mode only)
        self._lock = asyncio.Lock()
        self._shared_llm_client = None
        self._sub_tool_buffer: Dict[str, list] = {}  # session_id -> list of sub-tool events

    async def create_session(
        self,
        name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        memory_type: str = "tiered",
        planner_type: str = "dag",
        model_config: Optional[Dict[str, str]] = None,
        agent_mode: str = "standard",
    ) -> Dict[str, Any]:
        """Create a new session."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        # Store agent_mode in config_overrides
        config_overrides = {"agent_mode": agent_mode}

        session = await self._storage.create_session(
            session_id=session_id,
            name=name,
            workspace_path=workspace_path,
            memory_type=memory_type,
            planner_type=planner_type,
            model_config=model_config,
            config_overrides=config_overrides,
        )
        logger.info(f"✨ Created session {session_id} ({agent_mode})")
        return session

    async def update_session(self, session_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update session configuration."""
        async with self._lock:
            # 1. Get current session
            session = await self._storage.get_session(session_id)
            if not session:
                raise ValueError(f"Session not found: {session_id}")

            # Prepare storage updates
            storage_updates = {}

            # Handle model_config merging into config_overrides
            if "model_config" in updates:
                new_model_config = updates.pop("model_config")

                # Parse existing overrides
                config_overrides_raw = session.get("config_overrides")
                if isinstance(config_overrides_raw, str):
                    import json
                    config_overrides = json.loads(config_overrides_raw)
                else:
                    config_overrides = config_overrides_raw or {}

                # Ensure model_config exists
                if "model_config" not in config_overrides:
                    config_overrides["model_config"] = {}

                # Update it
                config_overrides["model_config"].update(new_model_config)

                storage_updates["config_overrides"] = config_overrides

            # Handle other fields (name, workspace_path, etc.)
            for k, v in updates.items():
                if k in ["name", "workspace_path"]:  # whitelisted fields
                    storage_updates[k] = v

            # Update storage
            if storage_updates:
                await self._storage.update_session(session_id, **storage_updates)

            # 2. Invalidate cache
            if session_id in self._sessions:
                logger.info(f"🔄 Invalidating cached session {session_id} due to config update")
                del self._sessions[session_id]
                self._dispatch_tools.pop(session_id, None)

            return await self._storage.get_session(session_id)

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
            # Clean up DispatchTool if exists (dual_agent mode)
            dispatch_tool = self._dispatch_tools.pop(session_id, None)
            if dispatch_tool:
                dispatch_tool.cleanup()

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

        # Parse config overrides
        config_overrides = session.get("config_overrides")
        overrides = {}
        if config_overrides:
            if isinstance(config_overrides, str):
                import json
                try:
                    overrides = json.loads(config_overrides)
                except json.JSONDecodeError:
                    pass
            elif isinstance(config_overrides, dict):
                overrides = config_overrides

        model_config = overrides.get("model_config") or {}
        agent_mode = overrides.get("agent_mode", "standard")

        # Extract model_id for prompt selection
        model_id = model_config.get("model_id", "default")

        # Create default LLM client if not provided
        if llm_client is None:
            if model_config:
                # Parse parameters
                temperature = model_config.get("temperature")
                if temperature is not None:
                    try:
                        temperature = float(temperature)
                    except (ValueError, TypeError):
                        temperature = None

                thinking = model_config.get("thinking")
                if thinking is not None:
                    if isinstance(thinking, str):
                        thinking = thinking.lower() == "true"
                    else:
                        thinking = bool(thinking)

                timeout = model_config.get("timeout")
                if timeout is not None:
                    try:
                        timeout = float(timeout)
                    except (ValueError, TypeError):
                        timeout = None

                # Construct full model name (provider/model_id)
                provider = model_config.get("provider", "google")
                full_model = f"{provider}/{model_id}"

                # Use factory to create DirectAdapter
                from nimbus.adapters.llm_factory import create_llm_client

                llm_client = await create_llm_client(
                    model=full_model,
                    temperature=temperature,
                    thinking=thinking,
                    timeout=timeout if timeout is not None else 120.0,
                )
            else:
                llm_client = await self._get_shared_llm_client()

        # Get workspace path from session
        workspace_path = session.get("workspace_path")
        workspace = None

        from pathlib import Path
        if workspace_path:
            import os
            workspace = Path(os.path.expanduser(workspace_path))
            logger.info(f"📁 Using workspace: {workspace}")

        # --- UNIFIED AGENT ARCHITECTURE ---
        # "dual_agent" maps to the "core" profile (Orchestrator).
        # "standard" maps to the "standard" profile (Generalist).

        # Default to "core" profile unless explicitly "standard"
        profile_name = "core"
        if agent_mode == "standard":
             # We can keep standard mode as a simple executor with all tools
             profile_name = "standard"

        # Create AgentOS using the factory and profile
        # Discover skill directories:
        # 1. User-level:     ~/.nimbus/skills
        # 2. Workspace-level: <workspace>/.nimbus/skills
        skill_paths = []
        user_skills = Path.home() / ".nimbus" / "skills"
        if user_skills.is_dir():
            skill_paths.append(user_skills)
        if workspace:
            ws_skills = workspace / ".nimbus" / "skills"
            if ws_skills.is_dir():
                skill_paths.append(ws_skills)

        agent_os = create_agent_os(
            llm_client=llm_client,
            tools={},
            max_processes=5,
            default_timeout=300.0,
            workspace=workspace,
            register_defaults=True, # Registers Read, Write, etc.
            profile=profile_name,   # Sets System Prompt & Config
            model_id=model_id,
            skill_paths=skill_paths,
        )

        # --- Dispatch Tool Integration (for Core/Dual mode) ---
        if profile_name == "core":
            from nimbus.orchestration.dispatch_tool import DispatchTool, DispatchToolConfig
            from nimbus.orchestration.tools import (
                DISPATCH_TOOL_DEF,
                VERIFY_TOOL_DEF,
            )
            
            # Register CoreBash (Standard Bash)
            # CoreBash removed as per Review P0. We use standard Bash for all roles now.
            # But the logic below was trying to import register_core_bash which we removed.
            # So we skip it. Standard Bash is already registered above via register_default_tools.
            
            # Initialize Dispatch Tool
            dispatch_config = DispatchToolConfig()
            dispatch_tool = DispatchTool(
                agent_os=agent_os,
                config=dispatch_config,
                workspace=workspace or Path.cwd(),
            )

            # Register Meta-Tools
            agent_os.register_tool(
                name="Dispatch",
                func=dispatch_tool.dispatch,
                description=DISPATCH_TOOL_DEF["description"],
                parameters=DISPATCH_TOOL_DEF["parameters"],
                roles=["core", "chat"],
            )
            agent_os.register_tool(
                name="Verify",
                func=dispatch_tool.verify,
                description=VERIFY_TOOL_DEF["description"],
                parameters=VERIFY_TOOL_DEF["parameters"],
                roles=["core", "chat"],
            )

            # Register ReviewCommittee
            from nimbus.orchestration.review_tool import REVIEW_TOOL_DEF, ReviewTool
            review_tool = ReviewTool(
                agent_os=agent_os,
                workspace=workspace or Path.cwd(),
            )
            agent_os.register_tool(
                name="ReviewCommittee",
                func=review_tool.review,
                description=REVIEW_TOOL_DEF["description"],
                parameters=REVIEW_TOOL_DEF["parameters"],
                roles=["core", "chat"],
            )

            # Keep reference for lifecycle management
            self._dispatch_tools[session_id] = dispatch_tool

        logger.info(f"🔧 Created AgentOS (profile={profile_name}) for session {session_id}")

        async with self._lock:
            self._sessions[session_id] = agent_os

        return agent_os

    async def _get_shared_llm_client(self):
        """Get or create shared LLM client (respects NIMBUS_LLM=mock)."""
        if self._shared_llm_client is None:
            import os

            if os.environ.get("NIMBUS_LLM") == "mock":
                from nimbus.testing.mock_llm import MockLLMAdapter

                adapter = MockLLMAdapter()
                await adapter.start()
                self._shared_llm_client = adapter
                logger.info("🤖 Shared MockLLMAdapter initialized (NIMBUS_LLM=mock)")
            else:
                from nimbus.adapters.llm_factory import create_llm_client
                from nimbus.config import get_config

                cfg = get_config()
                model = cfg.default_model

                # Use factory to create LLM (uses DirectAdapter)
                adapter = await create_llm_client(model=model)
                self._shared_llm_client = adapter
                logger.info(f"🤖 Shared DirectAdapter initialized (model={model})")

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
        message: "str | list",
    ):
        """
        Stream chat response with SSE events.

        Yields SSE events in the format expected by the API.
        """
        # Wait a bit to ensure SSE connection is established (fix race condition)
        await asyncio.sleep(0.5)

        logger.info(f"[stream_chat] Starting for session {session_id}")

        # Reset DispatchTool budget for this new message turn (dual_agent mode)
        dispatch_tool = self._dispatch_tools.get(session_id)
        if dispatch_tool:
            dispatch_tool.reset()

        # Get or create AgentOS
        agent_os = await self.get_or_create_agent(session_id)

        # Check if process needs restoration from checkpoint
        process = agent_os.get_process(session_id)
        logger.info(f"🔍 Checking process for session {session_id}: exists={process is not None}")

        if not process:
            # Try to restore from checkpoint
            try:
                checkpoint = await self._storage.load_latest_session_checkpoint(session_id)
                logger.info(f"🔍 Checkpoint loaded: {checkpoint is not None}")

                if checkpoint:
                    try:
                        logger.info(f"🔄 Restoring session {session_id} from checkpoint")
                        agent_os.restore_session(session_id, checkpoint)
                    except Exception as e:
                        logger.error(f"Failed to restore session: {e}", exc_info=True)
                        # Fallback to fresh start (will be created by chat())
            except Exception as e:
                logger.error(f"Error loading checkpoint: {e}", exc_info=True)


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
                if (
                    result.status == "OK"
                    and result.output == "[Instruction appended to running task]"
                ):
                    # This was an injection, not a full turn.
                    # We must save the user message immediately because it's not in MMU yet.
                    msg_id = f"msg_{uuid.uuid4().hex[:12]}"
                    await self._storage.add_message(
                        message_id=msg_id,
                        session_id=session_id,
                        role="user",
                        content=f"[Intervention] {message}",  # Mark as intervention
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
                    # Manual Fix: Drain pending injection queue from Process Inbox to MMU
                    try:
                        proc = agent_os.get_process(session_id)
                        if proc and hasattr(proc, "inbox"):
                            while proc.inbox:
                                msg = proc.inbox.pop(0)
                                logger.info(f"Draining pending injection: {msg}")
                                if proc.role == "chat":
                                    proc.mmu.add_user_message(msg)
                                else:
                                    proc.mmu.add_user_message(
                                        f"[User Intervention] {msg} (Cancelled)"
                                    )
                    except Exception as drain_err:
                        logger.warning(f"Failed to drain message queue: {drain_err}")
                else:
                    logger.info(f"[stream_chat] Completed with status: {result.status}")
            except asyncio.CancelledError:
                # User interrupted - cleanup incomplete state
                logger.info(f"[stream_chat] Cancelled by user for session {session_id}")

                # Save current progress before rollback
                # If the user cancels, we still want to keep what has been done so far.
                try:
                    # Get MMU from the process (not from agent_os._vcpu which doesn't exist)
                    process = agent_os.get_process(session_id)
                    if process and process.mmu:
                        # IMPORTANT: Save any pending messages in inbox that weren't processed yet
                        # These are injected user messages that arrived during execution
                        if process.inbox:
                            logger.info(
                                f"[stream_chat] Saving {len(process.inbox)} pending inbox messages..."
                            )
                            for pending_msg in process.inbox:
                                msg_id = f"msg_{uuid.uuid4().hex[:12]}"
                                await self._storage.add_message(
                                    message_id=msg_id,
                                    session_id=session_id,
                                    role="user",
                                    content=pending_msg,
                                )
                            process.inbox.clear()  # Clear after saving

                        # Save whatever state we have to database
                        logger.info("[stream_chat] Saving partial progress before interruption...")
                        await self._save_conversation_to_storage(session_id, agent_os, message)

                        # Save checkpoint on interruption
                        try:
                            if process.vcpu:
                                checkpoint = process.vcpu.create_checkpoint(
                                    session_id=session_id,
                                    reason="interrupted"
                                )
                                await self._storage.save_session_checkpoint(checkpoint)
                                logger.info(f"💾 Saved interrupted checkpoint for {session_id}")
                        except Exception as e:
                            logger.error(f"Failed to save interrupted checkpoint: {e}")

                        # NOTE: We intentionally do NOT rollback MMU here.
                        # If user continues the conversation, they need the full context.
                        # The saved messages in DB are for page refresh recovery.
                        logger.info("[stream_chat] Progress saved (MMU preserved for continuation)")
                    else:
                        logger.warning(
                            f"[stream_chat] No process/MMU found for session {session_id}, cannot save progress"
                        )
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
            elif not result.output and not (result.meta and result.meta.get("streamed")):
                # Only warn if there's no output AND content wasn't streamed
                logger.warning(f"[stream_chat] NO OUTPUT in result for session {session_id}")

            # Clear events for next turn
            agent_os.clear_events()

            # Save assistant messages to storage
            await self._save_conversation_to_storage(session_id, agent_os, message)

            # Save session checkpoint (for persistence/restore)
            try:
                process = agent_os.get_process(session_id)
                if process and process.vcpu:
                    checkpoint = process.vcpu.create_checkpoint(
                        session_id=session_id,
                        reason="turn_complete"
                    )
                    await self._storage.save_session_checkpoint(checkpoint)
                    logger.info(f"💾 Saved session checkpoint for {session_id}")
            except Exception as e:
                logger.error(f"Failed to save checkpoint: {e}")

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
        self, session_id: str, agent_os: AgentOS, user_message: str
    ):
        """
        Save conversation messages to storage after chat completion.

        This extracts messages from MMU and saves them to the database,
        so they can be restored when the user refreshes the page.
        """

        try:
            # Get the process for this session to access MMU
            process = agent_os.get_process(session_id)
            if not process or not process.mmu:
                logger.warning(
                    f"No process/MMU found for session {session_id}, cannot save messages"
                )
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
                # Skip ephemeral messages (internal system hints, not for user)
                if found_user_msg and not msg.meta.get("ephemeral", False):
                    messages_to_save.append(msg)

            # Save each message
            for msg in messages_to_save:
                msg_id = f"msg_{uuid.uuid4().hex[:12]}"

                # Prepare content - handle tool_calls specially
                content = msg.content or ""
                artifacts = None

                # If this is an assistant message with tool calls, store them as artifacts
                if msg.role == "assistant" and msg.tool_calls:
                    artifacts = [{"type": "tool_calls", "tool_calls": msg.tool_calls}]

                # For tool results, include the tool name
                if msg.role == "tool":
                    artifacts = [
                        {
                            "type": "tool_result",
                            "tool_call_id": msg.tool_call_id,
                            "name": msg.name,
                        }
                    ]
                    # Attach buffered sub-tool events to Dispatch results
                    if msg.name == "Dispatch" and session_id in self._sub_tool_buffer:
                        sub_events = self._sub_tool_buffer.pop(session_id)
                        if sub_events:
                            artifacts.append({
                                "type": "sub_tool_events",
                                "events": sub_events,
                            })

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

        # Detect sub-agent (executor) events by comparing pid with session_id.
        # Core/chat processes use session_id as pid; executor processes use "proc-xxx".
        is_sub_agent = event.pid != session_id

        # Map v2 event types to SSE event types
        type_mapping = {
            "tool_started": "tool_call",
            "tool_finished": "tool_result",
            "proc_spawned": "task_start",
            "proc_finished": "task_done",
            "step_started": "step_start",
            "thinking": "message",
            # Legacy/Alternative mappings
            "tool_call": "tool_call",
            "tool_result": "tool_result",
            "task_start": "task_start",
            "task_done": "task_done",
            "task_failed": "task_failed",
        }

        sse_type = type_mapping.get(event_type, "heartbeat")

        if is_sub_agent:
            # For sub-agent events, only forward tool calls/results (prefixed)
            # and lifecycle events. Suppress step_start/heartbeat to avoid
            # interfering with the frontend's message commit flow.
            if sse_type in ("tool_call", "tool_result"):
                sse_type = f"sub_{sse_type}"
            elif sse_type == "task_start":
                sse_type = "executor_start"
                # Inject executor metadata for the frontend
                event.data["_executor_pid"] = event.pid
            elif sse_type == "task_done":
                sse_type = "executor_done"
                event.data["_executor_pid"] = event.pid
            else:
                # Suppress other sub-agent events (step_start, heartbeat, etc.)
                return

        # Buffer sub-tool events for later persistence with Dispatch result
        if sse_type in ("sub_tool_call", "sub_tool_result"):
            if session_id not in self._sub_tool_buffer:
                self._sub_tool_buffer[session_id] = []
            self._sub_tool_buffer[session_id].append({
                "type": sse_type,
                "data": event.data,
            })
        elif sse_type == "executor_start":
            # Clear buffer for new executor run
            self._sub_tool_buffer[session_id] = []

        # Emit to SSE hub
        await self._sse_hub.publish(
            session_id,
            sse_type,
            event.data,
        )

    async def interrupt_session(self, session_id: str) -> Dict[str, Any]:
        """
        Interrupt a running session.
        """
        async with self._lock:
            agent_os = self._sessions.get(session_id)

        if not agent_os:
            return {"success": False, "error": "Session not loaded"}

        try:
            # Request interrupt via AgentOS (Phase 2 Kernel)
            interrupted = agent_os.interrupt(session_id)

            # Create and save checkpoint
            checkpoint_info = None
            # ... (checkpoint logic remains similar, but access via process)

            return {
                "success": True,
                "session_id": session_id,
                "interrupted_processes": 1 if interrupted else 0,
                "checkpoint": checkpoint_info,
            }
        except Exception as e:
            logger.error(f"Failed to interrupt session {session_id}: {e}")
            return {"success": False, "error": str(e)}

    async def inject_message(self, session_id: str, content: str) -> bool:
        """Inject user message into running session."""
        async with self._lock:
            agent_os = self._sessions.get(session_id)

        if not agent_os:
            return False

        # Inject via AgentOS (Phase 1 Kernel)
        # Note: In chat mode, session_id IS the pid
        return agent_os.inject_message(session_id, content)

    async def save_session_state(self, session_id: str) -> None:
        """Save session state (v2 handles this internally)."""
        # v2 AgentOS has built-in session persistence
        pass

    async def close_all(self) -> None:
        """Close all active sessions."""
        async with self._lock:
            self._sessions.clear()

        if self._shared_llm_client:
            logger.info("🔌 Closing shared LLM adapter")
            await self._shared_llm_client.__aexit__(None, None, None)
            self._shared_llm_client = None

    def get_active_count(self) -> int:
        """Get number of active agent instances."""
        return len(self._sessions)

    def is_session_loaded(self, session_id: str) -> bool:
        """Check if a session has an active agent."""
        return session_id in self._sessions
