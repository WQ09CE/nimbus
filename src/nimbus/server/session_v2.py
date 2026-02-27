"""Session Manager V2 - Using AgentOS.

This module provides a session manager that uses the v2 AgentOS architecture
while maintaining compatibility with the v1 server API.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
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
        self._active_tasks: Dict[str, asyncio.Task] = {}  # session_id -> running task
        self._lock = asyncio.Lock()
        self._shared_llm_lock = asyncio.Lock()
        self._shared_llm_client = None
        self._sub_tool_buffer: Dict[str, list] = {}  # session_id -> list of sub-tool events
        self._turn_counters: Dict[str, int] = {}  # session_id -> turn index for NimFS conversation log
        self._conv_tracers: Dict[str, "ConversationTracer"] = {}  # session_id -> ConversationTracer
        self._injection_persisted_sessions: set = set()  # sessions where injection already persisted user message

    def register_task(self, session_id: str, task: asyncio.Task):
        """Register a running task for a session."""
        self._active_tasks[session_id] = task

    def unregister_task(self, session_id: str):
        """Unregister a completed task for a session."""
        self._active_tasks.pop(session_id, None)

    def is_session_running(self, session_id: str) -> bool:
        """Check if a session has an active running task."""
        task = self._active_tasks.get(session_id)
        return task is not None and not task.done()

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

        # Pre-warm AgentOS in background
        asyncio.create_task(self._prewarm_agent(session_id))

        return session

    async def _prewarm_agent(self, session_id: str) -> None:
        """Pre-warm AgentOS for a session in background."""
        try:
            await self.get_or_create_agent(session_id)
            logger.info(f"Pre-warmed AgentOS for session {session_id}")
        except Exception as e:
            logger.warning(f"Pre-warm failed for {session_id}: {e}")

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
                if provider:
                    full_model = f"{provider}/{model_id}"
                else:
                    full_model = model_id

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
        # Profile is read from config (default: "orchestrator").
        # agent_mode == "standard" overrides to "standard" profile.
        from nimbus.config import get_config
        nimbus_config = get_config()
        profile_name = nimbus_config.agent_profile  # default "orchestrator"
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

        logger.info(f"🔧 Created AgentOS (profile={profile_name}) for session {session_id}")

        async with self._lock:
            self._sessions[session_id] = agent_os

        return agent_os

    async def _get_shared_llm_client(self):
        """Get or create shared LLM client (respects NIMBUS_LLM=mock)."""
        async with self._shared_llm_lock:
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

    async def _auto_generate_title(self, session_id: str):
        """用 LLM 根据对话内容自动生成/更新 session 标题。前几轮每次都重新生成以获得更准确的标题。"""
        try:
            # 读取所有消息作为上下文
            all_msgs = await self._storage.get_messages(session_id, limit=20)
            if not all_msgs:
                return

            # 拼接对话摘要（每条消息取前 100 字，最多用 5 条）
            conversation = []
            for msg in all_msgs[:5]:
                role = msg.get("role", "user")
                content = (msg.get("content") or "")[:100]
                if content.strip():
                    conversation.append(f"{role}: {content}")

            if not conversation:
                return

            conversation_text = "\n".join(conversation)

            # Use a lightweight flash model for title generation to avoid
            # competing with the user's main request for rate limits.
            llm = None
            try:
                from nimbus.adapters.llm_factory import create_llm_client
                llm = await create_llm_client("google/gemini-3-flash-preview", timeout=30.0)
            except Exception as e:
                logger.warning(f"Failed to create flash client for title generation, falling back to shared: {e}")
                llm = await self._get_shared_llm_client()

            has_chinese = any("\u4e00" <= c <= "\u9fff" for c in conversation_text)
            if has_chinese:
                prompt = f"请根据以下对话内容，用一个简短的标题（5-15个字）概括这个对话的主题。只返回标题本身，不要引号：\n\n{conversation_text}"
            else:
                prompt = f"Based on this conversation, generate a short title (3-8 words) summarizing the topic. Return only the title, no quotes:\n\n{conversation_text}"

            messages = [{"role": "user", "content": prompt}]
            response = await llm.chat(messages, tools=[])

            if response.content:
                title = response.content.strip().strip('"').strip("'")
                if len(title) > 50:
                    title = title[:50]
                await self._storage.update_session(session_id, name=title)
                logger.info(f"Auto-titled session {session_id}: {title}")
        except Exception as e:
            logger.warning(f"Auto-title failed for {session_id}: {e}")

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
        logger.info(f"[stream_chat] Starting for session {session_id}")

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
            {"session_id": session_id, "timestamp": datetime.now(timezone.utc).isoformat()},
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

            # Record message watermark before chat (for reliable save boundary)
            # For new sessions (process not yet created), watermark is 0 — correct
            # because chat() will create a new process and all messages are new.
            # For restored sessions, watermark equals the count of restored old messages.
            _msg_watermark = 0
            _pre_process = agent_os.get_process(session_id)
            if _pre_process and _pre_process.mmu and _pre_process.mmu._stack:
                _msg_watermark = len(_pre_process.mmu.current_frame.messages)

            _chat_start_ms = int(time.monotonic() * 1000)
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
                    # Store complete content (multimodal JSON or plain string)
                    if isinstance(message, list):
                        storage_content = json.dumps(message, ensure_ascii=False)
                    else:
                        storage_content = message
                    await self._storage.add_message(
                        message_id=msg_id,
                        session_id=session_id,
                        role="user",
                        content=storage_content,
                    )
                    # Mark that user message has already been persisted for this injection
                    # so late_messages path and _save_conversation_to_storage won't save it again
                    self._injection_persisted_sessions.add(session_id)
                    preview = storage_content[:20]
                    logger.info(f"💾 Saved injected message '{preview}...' to storage")

                    # Emit completion and exit
                    await self._sse_hub.publish(session_id, "dag_complete", {"status": "OK"})
                    await self._sse_hub.close_session(session_id)
                    return

                if result.status == "ERROR":
                    fault = result.fault
                    logger.error(f"[stream_chat] Result Error: {fault}")
                    # Send structured error event to frontend so it can display a meaningful message
                    try:
                        if fault and hasattr(fault, 'domain'):
                            error_payload = {
                                "code": f"{fault.domain.lower()}_{fault.code.lower()}",
                                "message": fault.message,
                                "retryable": fault.retryable,
                            }
                        else:
                            error_payload = {
                                "code": "agent_error",
                                "message": str(fault) if fault else "Agent execution failed",
                                "retryable": False,
                            }
                        await self._sse_hub.publish(session_id, "error", error_payload)
                    except Exception as _pub_err:
                        logger.warning(f"Failed to publish error event: {_pub_err}")
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
                        await self._save_conversation_to_storage(session_id, agent_os, message, _msg_watermark)

                        # Persist cancelled turn to NimFS
                        _cancel_ms = int(time.monotonic() * 1000) - _chat_start_ms
                        await self._save_turn_to_nimfs(session_id, agent_os, message, status="CANCELLED", duration_ms=_cancel_ms)

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

            # Note: LLM output is already streamed via THINKING events in real-time.
            # No need to re-emit result.output here — doing so causes duplicate messages.
            if not result.output:
                logger.warning(f"[stream_chat] NO OUTPUT in result for session {session_id}")

            # Clear events for next turn
            agent_os.clear_events()

            # Save assistant messages to storage
            await self._save_conversation_to_storage(session_id, agent_os, message, _msg_watermark)

            # Persist this turn to NimFS for conversation history
            _ok_ms = int(time.monotonic() * 1000) - _chat_start_ms
            await self._save_turn_to_nimfs(session_id, agent_os, message, status="OK", duration_ms=_ok_ms)

            # Check for late-arriving injected messages (race condition fix)
            # There's a narrow window where inject_message() succeeds (state was still
            # RUNNING) but _run_process already exited its loop. Those messages sit in
            # inbox unconsumed. Atomic-swap and persist them here.
            process = agent_os.get_process(session_id)
            if process and process.inbox:
                # Atomic swap — no await between read and clear
                late_messages, process.inbox = process.inbox, []
                logger.warning(
                    f"[stream_chat] Found {len(late_messages)} late-arriving message(s) "
                    f"in inbox after process finished. Persisting to storage + MMU."
                )
                # Skip storage if injection path already persisted this user message
                if session_id in self._injection_persisted_sessions:
                    self._injection_persisted_sessions.discard(session_id)
                    # Still add to MMU for context, but don't re-persist to storage
                    for late_msg in late_messages:
                        if process.mmu:
                            process.mmu.add_user_message(late_msg)
                        logger.info(f"[stream_chat] Skipped re-saving late message (injection already persisted): {str(late_msg)[:50]}...")
                else:
                    for late_msg in late_messages:
                        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
                        await self._storage.add_message(
                            message_id=msg_id,
                            session_id=session_id,
                            role="user",
                            content=late_msg if isinstance(late_msg, str) else json.dumps(late_msg, ensure_ascii=False),
                        )
                        if process.mmu:
                            process.mmu.add_user_message(late_msg)
                        logger.info(f"[stream_chat] Saved late message: {str(late_msg)[:50]}...")
                # Re-check: more messages may have arrived during the await calls above
                if process.inbox:
                    extras, process.inbox = process.inbox, []
                    for extra_msg in extras:
                        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
                        await self._storage.add_message(
                            message_id=msg_id,
                            session_id=session_id,
                            role="user",
                            content=extra_msg if isinstance(extra_msg, str) else json.dumps(extra_msg, ensure_ascii=False),
                        )
                        if process.mmu:
                            process.mmu.add_user_message(extra_msg)
                        logger.info(f"[stream_chat] Saved extra late message: {str(extra_msg)[:50]}...")

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
            import uuid as _uuid
            _error_id = _uuid.uuid4().hex[:8]
            logger.error(f"Error in stream_chat [#{_error_id}]: {e}", exc_info=True)
            _err_str = str(e).lower()
            if "rate limit" in _err_str or "429" in _err_str or "resource exhausted" in _err_str:
                _code, _msg, _retry = "llm_rate_limit", "模型请求过频，请稍后重试", True
            elif "timeout" in _err_str or "timed out" in _err_str:
                _code, _msg, _retry = "resource_timeout", "请求超时，请重试", True
            elif "budget" in _err_str or "ctx_overflow" in _err_str or "context" in _err_str:
                _code, _msg, _retry = "llm_ctx_overflow", "上下文长度已超限", False
            elif "auth" in _err_str or "401" in _err_str or "403" in _err_str or "forbidden" in _err_str:
                _code, _msg, _retry = "auth_error", "认证失败，请检查 API 密钥", False
            else:
                _code, _msg, _retry = "kernel_system_error", f"系统错误 [#{_error_id}]", False
            await self._sse_hub.publish(
                session_id,
                "error",
                {"code": _code, "message": _msg, "retryable": _retry, "error_id": _error_id},
            )

    async def _save_turn_to_nimfs(
        self,
        session_id: str,
        agent_os: AgentOS,
        user_message: "str | list",
        status: str = "OK",
        duration_ms: int = 0,
    ) -> None:
        """
        Persist one conversation turn (user message + assistant reply) to NimFS.

        Each call writes a new immutable artifact grouped under task_id=f"conv-{session_id}".
        Failures are silently logged to avoid disrupting the main flow.
        Delegates to ConversationTracer for formatting and writing.
        """
        try:
            from nimbus.core.nimfs.manager import NimFSManager
            from nimbus.server.conv_tracer import ConversationTracer

            # Resolve NimFSManager from the process MMU
            process = agent_os.get_process(session_id)
            nimfs_manager: Optional[NimFSManager] = None
            if process and process.mmu and hasattr(process.mmu, "_nimfs_manager"):
                nimfs_manager = process.mmu._nimfs_manager

            if nimfs_manager is None:
                logger.warning(
                    f"[_save_turn_to_nimfs] No NimFSManager found for session {session_id}, skipping"
                )
                return

            # Get or create ConversationTracer for this session
            if session_id not in self._conv_tracers:
                self._conv_tracers[session_id] = ConversationTracer(session_id, nimfs_manager)
            tracer = self._conv_tracers[session_id]

            # Extract last assistant reply from MMU
            assistant_text = ""
            if process and process.mmu and process.mmu._stack:
                messages = process.mmu.current_frame.messages
                for msg in reversed(messages):
                    if msg.role == "assistant" and msg.content:
                        assistant_text = (
                            msg.content
                            if isinstance(msg.content, str)
                            else json.dumps(msg.content, ensure_ascii=False)
                        )
                        break

            tracer.record_turn(
                user_message=user_message,
                assistant_reply=assistant_text,
                duration_ms=duration_ms,
                status=status,
            )
        except Exception as e:
            logger.warning(f"[_save_turn_to_nimfs] Failed to write to NimFS (non-fatal): {e}")

    async def _save_conversation_to_storage(
        self, session_id: str, agent_os: AgentOS, user_message: "str | list", msg_watermark: int = 0
    ):
        """
        Save conversation messages to storage after chat completion.

        This extracts messages from MMU and saves them to the database,
        so they can be restored when the user refreshes the page.

        Args:
            session_id: The session ID.
            agent_os: The AgentOS instance.
            user_message: The user message that triggered this chat turn.
            msg_watermark: The message count in the frame BEFORE this chat turn
                started. Messages at index >= msg_watermark are from this turn.
                Default 0 means save all non-user messages (backward compatible).
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

            # Find new messages using watermark (index-based, more reliable than content matching).
            # msg_watermark is the message count BEFORE this chat turn started.
            # Messages at index >= msg_watermark are from this turn.
            messages_to_save = []

            start_idx = msg_watermark
            for msg in messages[start_idx:]:
                # Skip the user message itself (already saved by frontend)
                if msg.role == "user":
                    # If injection path already persisted this user message, skip entirely
                    if session_id in self._injection_persisted_sessions:
                        self._injection_persisted_sessions.discard(session_id)
                        continue
                    # Skip the triggering user message to avoid duplicates.
                    # The frontend already shows it via optimistic update.
                    # Use robust comparison: handle both str and list (multimodal) content.
                    def _content_matches(stored: any, original: any) -> bool:
                        if stored == original:
                            return True
                        # Both might be list (multimodal) in different serialization forms
                        if isinstance(original, list) and isinstance(stored, str):
                            try:
                                return json.loads(stored) == original
                            except Exception:
                                pass
                        if isinstance(original, str) and isinstance(stored, list):
                            try:
                                return stored == json.loads(original)
                            except Exception:
                                pass
                        # Fallback: compare text content only
                        if isinstance(original, list):
                            orig_text = " ".join(p.get("text", "") for p in original if isinstance(p, dict) and p.get("type") == "text").strip()
                        else:
                            orig_text = str(original).strip()
                        if isinstance(stored, list):
                            stored_text = " ".join(p.get("text", "") for p in stored if isinstance(p, dict) and p.get("type") == "text").strip()
                        elif isinstance(stored, str):
                            try:
                                parsed = json.loads(stored)
                                if isinstance(parsed, list):
                                    stored_text = " ".join(p.get("text", "") for p in parsed if isinstance(p, dict) and p.get("type") == "text").strip()
                                else:
                                    stored_text = str(stored).strip()
                            except Exception:
                                stored_text = str(stored).strip()
                        else:
                            stored_text = str(stored).strip()
                        return orig_text == stored_text and bool(orig_text)

                    if _content_matches(msg.content, user_message):
                        continue
                # Skip ephemeral messages (internal system hints, not for user)
                if msg.meta.get("ephemeral", False):
                    continue
                messages_to_save.append(msg)

            # Save each message
            for msg in messages_to_save:
                msg_id = f"msg_{uuid.uuid4().hex[:12]}"

                # Prepare content - handle tool_calls specially
                # Multimodal messages (with images) have list content; serialize for SQLite.
                content = msg.content or ""
                if isinstance(content, list):
                    content = json.dumps(content)
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
                    # Only pop sub_tool_buffer for meta-tools (specialist agents)
                    # Non-meta tools (Bash, Read, etc.) should NOT consume the buffer
                    META_TOOLS = {"Dispatch", "Explore", "Implement", "Design", "Test", "Verify",
                                  "ReviewCommittee", "ParallelDispatch"}
                    if session_id in self._sub_tool_buffer and msg.name in META_TOOLS:
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

        # Debug: Log tool events for SSE tracing
        if event_type in ("tool_started", "tool_finished"):
            logger.info(
                f"SSE emit: {event_type} -> pid={event.pid}, session={session_id}, "
                f"tool={event.data.get('tool', '?')}, action_id={event.data.get('action_id', '?')}"
            )

        # Detect sub-agent (executor) events by comparing pid with session_id.
        # Core/chat processes use session_id as pid; executor processes use "proc-xxx".
        is_sub_agent = event.pid != session_id

        # Inject batch metadata for sub-agent events (parallel routing)
        if is_sub_agent:
            agent_os = self._sessions.get(session_id)
            if agent_os:
                proc = agent_os._processes.get(event.pid)
                if proc:
                    for key in ("parent_action_id", "batch_slot_index", "specialist", "resolved_model"):
                        val = proc.signals.get(key)
                        if val is not None:
                            event.data[key] = val

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

        # Special handling for "thinking" token streams to ensure proper text streaming
        if event_type == "thinking":
            sse_type = "message"
            chunk_text = event.data.get("chunk", "")
            # Forward chunk correctly instead of nesting inside another generic data dict
            if chunk_text:
                await self._sse_hub.publish(session_id, "message", {"chunk": chunk_text})
            return

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
            # Only clear buffer for non-batch executors (single Dispatch/Explore/etc.)
            # Batch executors (parallel native calls) share the buffer -- don't clear per-executor
            if not event.data.get("parent_action_id"):
                self._sub_tool_buffer[session_id] = []

        # Emit to SSE hub
        sent_count = await self._sse_hub.publish(
            session_id,
            sse_type,
            event.data,
        )

        # Debug: Log tool event delivery
        if sse_type in ("tool_call", "tool_result"):
            logger.info(
                f"SSE delivered: {sse_type} -> {sent_count} connections, "
                f"tool={event.data.get('tool', '?')}"
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

            # Also cancel the asyncio task to force-stop the agent
            # (cooperative interrupt only takes effect at next iteration boundary,
            # but user expects immediate stop)
            task = self._active_tasks.get(session_id)
            if task and not task.done():
                task.cancel()
                logger.info(f"🛑 Cancelled active task for session {session_id}")

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

    async def inject_message(self, session_id: str, content: "str | list") -> bool:
        """Inject user message (text or multimodal) into running session."""
        async with self._lock:
            agent_os = self._sessions.get(session_id)

        if not agent_os:
            return False

        # Inject via AgentOS (Phase 1 Kernel)
        # Note: In chat mode, session_id IS the pid
        # content can be str or list[dict] for multimodal messages
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
