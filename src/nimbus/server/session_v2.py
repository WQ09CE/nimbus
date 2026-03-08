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

from nimbus import AgentOS
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
        self._active_loops: Dict[str, Any] = {}  # session_id -> RuntimeLoop
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

        # Auto-downgrade: small models get standard mode + basic tools only
        _is_basic_model = False
        if model_id != "default":
            from nimbus.core.models.registry import ModelRegistry
            model_info = ModelRegistry.get(model_id)
            if model_info and model_info.basic_tools_only:
                _is_basic_model = True
                if agent_mode == "dual_agent":
                    logger.info(f"⚡ Auto-downgrade: {model_id} (basic_tools_only) → standard mode")
                    agent_mode = "standard"

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

        # Build the new nimbus-next AgentOS
        from nimbus.core.agent import AgentOS, AgentConfig
        
        agent_config = AgentConfig()
        # Ensure timeout config propagates to VCPU
        if isinstance(llm_client, getattr(__import__("nimbus.adapters.direct_adapter", fromlist=["DirectAdapter"]), "DirectAdapter", object)):
            agent_config.llm_call_timeout = 120.0
            
        system_prompt = getattr(nimbus_config, "system_prompt", "") or "You are a capable AI coding assistant. Use tools to solve the user's tasks. Always think step by step in Chinese."

        agent_os = AgentOS(
            config=agent_config,
            adapter=llm_client,
            system_prompt=system_prompt,
        )

        logger.info(f"🔧 Created nimbus-next AgentOS (profile={profile_name}) for session {session_id}")

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
                
                # Notify frontend about title change via SSE
                await self._sse_hub.publish(
                    session_id,
                    "session_updated",
                    {"session_id": session_id, "name": title},
                )
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
        Stream chat response with SSE events directly from nimbus-next RuntimeLoop.
        """
        logger.info(f"[stream_chat] Starting for session {session_id}")
        agent_os = await self.get_or_create_agent(session_id)

        # Emit connected and message_start
        await self._sse_hub.publish(
            session_id, "connected",
            {"session_id": session_id, "timestamp": datetime.now(timezone.utc).isoformat()},
        )
        await self._sse_hub.publish(session_id, "message_start", {"role": "assistant"})

        _msg_watermark = 0
        loop = None
        try:
            logger.info("[stream_chat] Calling agent_os.stream_with_queue...")
            
            # Stringify multimodal message list for now (nimbus-next uses string natively)
            if isinstance(message, list):
                str_msg = json.dumps(message, ensure_ascii=False)
            else:
                str_msg = message

            # Generate the RuntimeLoop (pi-style)
            loop = agent_os.stream_with_queue(str_msg, session_id=session_id)
            self._active_loops[session_id] = loop

            # Yield fine-grained events mapped to SSE UI format
            async for event in loop.stream():
                evt_type = event.get("type")
                
                if evt_type == "interrupted":
                    logger.info("[stream_chat] Execution cancelled by interrupt request")
                    await self._sse_hub.publish(session_id, "dag_complete", {"status": "CANCELLED"})
                    continue
                
                if evt_type == "message_queued":
                    logger.info(f"[stream_chat] Handled enqueued message: {str(event.get('content'))[:50]}...")
                    continue

                if evt_type == "text_delta":
                    await self._sse_hub.publish(
                        session_id, "text_delta", {"content": event.get("content", "")}
                    )
                elif evt_type == "tool_call_start":
                    await self._sse_hub.publish(
                        session_id, "tool_start",
                        {"tool": event.get("tool"), "args_preview": event.get("args_preview", {})}
                    )
                elif evt_type == "tool_call_done":
                    await self._sse_hub.publish(
                        session_id, "tool_complete",
                        {
                            "tool": event.get("tool"),
                            "status": event.get("status"),
                            "output": event.get("output_preview"),
                            "ui_detail": event.get("ui_detail")
                        }
                    )
                elif evt_type == "context_compacted":
                    summary = event.get("summary")
                    if summary:
                        from nimbus.core.storage.nimfs import NimFSManager
                        from nimbus.config import get_config
                        nimfs = NimFSManager(get_config().data_dir)
                        import uuid
                        await nimfs.write_memory(
                             obj_id=f"{session_id}_archive_{uuid.uuid4().hex[:8]}",
                             content=f"Archived Conversation Context:\n\n{summary}",
                             source="mmu_compaction",
                             tags=[session_id, "archive", "auto-generated"]
                        )
                        logger.info(f"📦 Saved MMU context compaction archive to NimFS for {session_id}")

                elif evt_type == "final":
                    result = event.get("result")
                    if result and result.status == "ERROR":
                        fault = getattr(result, "fault", None)
                        error_payload = {
                            "code": "agent_error",
                            "message": fault.message if fault else str(result.output),
                            "retryable": False,
                        }
                        await self._sse_hub.publish(session_id, "error", error_payload)
                    elif result and result.status == "OK":
                        logger.info(f"[stream_chat] Completed with status: OK")
            
            await self._sse_hub.publish(session_id, "dag_complete", {"status": "OK"})

        except asyncio.CancelledError:
            logger.info(f"[stream_chat] Cancelled by user for session {session_id}")
            raise
        except Exception as chat_err:
            logger.error(f"[stream_chat] Streaming failed: {chat_err}", exc_info=True)
            raise
        finally:
            if session_id in self._active_loops:
                del self._active_loops[session_id]

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



    async def interrupt_session(self, session_id: str) -> Dict[str, Any]:
        """
        Interrupt a running session.
        """
        async with self._lock:
            agent_os = self._sessions.get(session_id)

        if not agent_os:
            return {"success": False, "error": "Session not loaded"}

        try:
            loop = self._active_loops.get(session_id)
            interrupted = False
            if loop:
                loop.request_interruption()
                interrupted = True

            task = self._active_tasks.get(session_id)
            if task and not task.done():
                task.cancel()
                logger.info(f"🛑 Cancelled active task for session {session_id}")

            return {
                "success": True,
                "session_id": session_id,
                "interrupted_processes": 1 if interrupted else 0,
                "checkpoint": None,
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

        loop = self._active_loops.get(session_id)
        if loop and hasattr(loop, "message_queue"):
            if isinstance(content, list):
                content = json.dumps(content, ensure_ascii=False)
            loop.message_queue.enqueue(content)
            logger.info(f"💉 Injected message into running nimbus-next loop for {session_id}")
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
            logger.info("🔌 Closing shared LLM adapter")
            await self._shared_llm_client.__aexit__(None, None, None)
            self._shared_llm_client = None

    def get_active_count(self) -> int:
        """Get number of active agent instances."""
        return len(self._sessions)

    def is_session_loaded(self, session_id: str) -> bool:
        """Check if a session has an active agent."""
        return session_id in self._sessions
