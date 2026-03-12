"""Session Manager — Manages agent sessions with nimbus-next AgentOS.

Creates per-session AgentOS instances, streams events via SSE, and handles
message injection and interruption.
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from nimbus import AgentOS

from .permission import PermissionManager
from .sse import SSEHub
from nimbus.core.storage import SessionStorage

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
        sse_hub: SSEHub,
        permission_manager: PermissionManager,
        max_sessions: int = 10,
    ):
        self._sse_hub = sse_hub
        self._permission_manager = permission_manager
        self._max_sessions = max_sessions
        self._sessions: Dict[str, AgentOS] = {}  # session_id -> AgentOS
        self._active_tasks: Dict[str, asyncio.Task] = {}  # session_id -> running task
        self._active_loops: Dict[str, Any] = {}  # session_id -> RuntimeLoop
        self._storage = SessionStorage()
        self._lock = asyncio.Lock()
        self._shared_llm_lock = asyncio.Lock()
        self._shared_llm_client = None

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
        model_config: Optional[Dict[str, str]] = None,
        agent_mode: str = "standard",
    ) -> Dict[str, Any]:
        """Create a new session in memory."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        # Store agent_mode in config_overrides
        config_overrides = {"agent_mode": agent_mode}

        now_iso = datetime.now(timezone.utc).isoformat()

        session = {
            "id": session_id,
            "name": name or "New Chat",
            "workspace_path": workspace_path,
            "llm_config": model_config or {},
            "config_overrides": config_overrides,
            "status": "active",
            "created_at": now_iso,
            "updated_at": now_iso,
        }

        # Save placeholder "created" dump
        self._storage.save_session(
            session_id=session_id,
            status="active",
            messages=[],
            vcpu_state={},
            vcpu_config={},
            llm_config=model_config or {},
            metadata={
                "name": name or "New Chat",
                "workspace_path": workspace_path,
                "config_overrides": config_overrides,
                "created_at": now_iso,
            }
        )
        logger.info(f"✨ Created session {session_id} ({agent_mode}) on disk")

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
        """Update session metadata."""
        async with self._lock:
            # Load full dump
            dump = self._storage.load_session(session_id)
            if not dump:
                raise ValueError(f"Session not found: {session_id}")

            meta = dump.get("metadata", {})
            need_rebuild = False
            for k, v in updates.items():
                if k in ("name", "workspace_path"):
                    meta[k] = v
                    if k == "workspace_path":
                        need_rebuild = True
                elif k == "model_config":
                    dump["llm_config"] = v
                    need_rebuild = True

            self._storage.save_session(
                session_id=session_id,
                status=dump.get("status", "active"),
                messages=dump.get("messages", []),
                vcpu_state=dump.get("vcpu_state", {}),
                vcpu_config=dump.get("vcpu_config", {}),
                llm_config=dump.get("llm_config", {}),
                metadata=meta,
            )

            if need_rebuild and session_id in self._sessions:
                logger.info(f"Invalidating cached AgentOS for {session_id}")
                del self._sessions[session_id]

            return dump

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session metadata from disk."""
        dump = self._storage.load_session(session_id)
        if dump:
            meta = dump.get("metadata", {})
            return {
                "id": session_id,
                "status": dump.get("status", "unknown"),
                "name": meta.get("name", "Unknown"),
                "workspace_path": meta.get("workspace_path"),
                "llm_config": dump.get("llm_config", {}),
                "config_overrides": meta.get("config_overrides", {}),
                "created_at": meta.get("created_at") or dump.get("updated_at"),
                "updated_at": dump.get("updated_at"),
                "message_count": len(dump.get("messages", [])),
            }
        return None

    async def list_sessions(
        self,
        status: str = "active",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        """List sessions from disk."""
        dumps = self._storage.list_sessions()
        sessions = []
        for d in dumps:
            meta = d.get("metadata", {})
            sessions.append({
                "id": d.get("session_id"),
                "status": d.get("status", "unknown"),
                "name": meta.get("name", "Unknown"),
                "workspace_path": meta.get("workspace_path"),
                "llm_config": d.get("llm_config", {}),
                "config_overrides": meta.get("config_overrides", {}),
                "created_at": meta.get("created_at") or d.get("updated_at"),
                "updated_at": d.get("updated_at"),
                "message_count": len(d.get("messages", [])),
            })
        
        # Sort is already handled by list_sessions
        return sessions[offset:offset+limit], len(sessions)

    async def delete_session(self, session_id: str) -> None:
        """Soft delete a session."""
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
            self._storage.delete_session(session_id)
        self._permission_manager.cancel_pending(session_id)
        logger.info(f"🗑️ Deleted session {session_id}")

    async def get_or_create_agent(self, session_id: str, llm_client=None) -> AgentOS:
        """Get or create an AgentOS instance for a session."""
        async with self._lock:
            if session_id in self._sessions:
                agent = self._sessions[session_id]
                logger.info(f"📦 Returning cached AgentOS for session {session_id}")
                return agent

        # Get session info
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        # Parse config overrides
        config_overrides = session.get("config_overrides")
        overrides = {}
        if config_overrides:
            if isinstance(config_overrides, str):
                try:
                    overrides = json.loads(config_overrides)
                except json.JSONDecodeError:
                    pass
            elif isinstance(config_overrides, dict):
                overrides = config_overrides

        model_config = session.get("llm_config") or overrides.get("model_config") or {}
        agent_mode = overrides.get("agent_mode", "standard")

        # Extract model_id for prompt selection
        model_id = model_config.get("model_id", "default")

        # Auto-downgrade: small models get standard mode + basic tools only
        if model_id != "default":
            from nimbus.core.models.registry import ModelRegistry
            model_info = ModelRegistry.get(model_id)
            if model_info and model_info.basic_tools_only:
                if agent_mode == "dual_agent":
                    logger.info(f"Auto-downgrade: {model_id} (basic_tools_only) -> standard mode")
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

        if workspace_path:
            workspace = Path(os.path.expanduser(workspace_path))
            logger.info(f"📁 Using workspace: {workspace}")

        from nimbus.config import get_config
        nimbus_config = get_config()

        # Build the new nimbus-next AgentOS
        from nimbus.core.agent import AgentConfig, AgentOS

        agent_config = AgentConfig()
        agent_config.llm_call_timeout = 120.0
        agent_config.text_is_final = True  # Chat mode: pure text = final response, don't poke
        agent_config.max_consecutive_thoughts = 2  # Safety net: stop after 2 thoughts max

        scratchpad_path = f".nimbus/sessions/{session_id}/scratchpad.md"
        system_prompt = (
            "You are a capable AI assistant. Use tools to solve the user's tasks. Think step by step.\n\n"
            "# Task Management & Scratchpad\n"
            f"You have a dedicated scratchpad at `{scratchpad_path}`.\n"
            "For any task requiring multiple steps, you MUST use the `Write`, `Edit`, and `Read` tools to maintain this file.\n"
            "1. **Plan First**: Write a TODO list in the scratchpad before executing complex actions.\n"
            "2. **Update Frequently**: Append intermediate findings, error logs, and checked-off TODOs.\n"
            "3. **State Recovery**: If you lose track of your progress, `Read` your scratchpad to recover your state.\n\n"
            "# Agent Collaboration\n"
            "You are an **orchestrator**. Prefer delegating execution to sub-agents; "
            "reserve direct tool use for trivial one-shot actions (single Read, quick Bash).\n\n"
            "Spawn rules:\n"
            "1. **Parallel by default** — independent sub-tasks MUST be spawned in the same function_calls block.\n"
            "2. **Right role** — `reader` (Read/Grep only) for scanning/analysis, `worker` (Write/Edit/Bash) for mutations and commands.\n"
            "3. **No nesting** — sub-agents MUST NOT spawn further sub-agents.\n"
            "4. **Rich context** — every spawn task must include: goal, relevant file paths, and expected output format.\n"
            "5. **Verify after delegate** — after a worker completes, always validate the result (read file, run test).\n\n"
            "If a sub-agent times out, read its scratchpad to recover partial progress."
        )

        # Load user memory file and append to system prompt as pinned context
        from nimbus.config import DEFAULT_MEMORY_PATH
        memory_path = Path(os.path.expanduser(getattr(nimbus_config, "memory_path", str(DEFAULT_MEMORY_PATH))))
        memory_content = ""
        if memory_path.exists():
            try:
                memory_content = memory_path.read_text(encoding="utf-8").strip()
                logger.info(f"📖 Loaded memory from {memory_path} ({len(memory_content)} chars)")
            except OSError as e:
                logger.warning(f"⚠️ Failed to read memory file {memory_path}: {e}")

        # Strict Ordering Publisher Task:
        # Prevents asyncio.create_task race conditions where tool events 
        # overtake text events due to lock acquisition.
        event_queue = asyncio.Queue()

        async def _publisher_task():
            while True:
                try:
                    payload = await event_queue.get()
                    if payload is None: break
                    await self._sse_hub.publish(session_id, payload[0], payload[1])
                except Exception as e:
                    logger.error(f"Event publisher error: {e}")

        pub_task = asyncio.create_task(_publisher_task())

        # Real-time gate callback: queue tool events to SSE as they happen
        def _gate_event_cb(event):
            if event.type == "TOOL_STARTED":
                event_queue.put_nowait(("tool_call", {
                    "tool": event.data.get("tool"),
                    "args": event.data.get("args", {}),
                    "action_id": event.data.get("call_id"),
                }))
            elif event.type == "TOOL_CALL_DELTA":
                event_queue.put_nowait(("tool_output_chunk", {
                    "tool": event.data.get("tool"),
                    "chunk": event.data.get("chunk"),
                    "action_id": event.data.get("call_id"),
                }))
            elif event.type == "TOOL_FINISHED":
                event_queue.put_nowait(("tool_result", {
                    "tool": event.data.get("tool"),
                    "status": event.data.get("status"),
                    "output": event.data.get("output"),
                    "action_id": event.data.get("call_id"),
                    "ui_detail": event.data.get("ui_detail"),
                }))

        # Token-level text streaming callback
        def _text_delta_cb(chunk: str):
            event_queue.put_nowait(("message", {"content": chunk}))

        # Let AgentOS know this session is being instantiated
        # (MMU and VCPU will be rehydrated when stream_with_queue is called)
        agent_os = AgentOS(
            config=agent_config,
            adapter=llm_client,
            system_prompt=system_prompt,
            memory=memory_content,
            event_callback=_gate_event_cb,
            on_text_delta=_text_delta_cb,
        )

        # Attach task reference to agent_os so it isn't garbage collected
        agent_os._pub_task = pub_task

        logger.info(f"Created nimbus-next AgentOS for session {session_id}")

        async with self._lock:
            self._sessions[session_id] = agent_os

        return agent_os

    async def _get_shared_llm_client(self):
        """Get or create shared LLM client (respects NIMBUS_LLM=mock)."""
        async with self._shared_llm_lock:
            if self._shared_llm_client is None:
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

    async def _auto_generate_title(self, session_id: str, agent_os: Any) -> None:
        """
        Generate a title for the session based on the first message context via AgentOS memory.
        """
        # Give it a tiny delay to ensure first message is processed
        await asyncio.sleep(2)

        session = await self.get_session(session_id)
        if session and session.get("name", "").startswith("New Chat"):
            try:
                # For nimbus-next, yank the goal from the MMU via public API
                title = "Conversation"
                mmu = agent_os.get_mmu(session_id)
                if mmu and mmu._messages:
                    for msg in mmu._messages:
                        if msg.role == "user" and msg.content:
                            title = str(msg.content)[:30].replace("\n", " ").strip()
                            break

                if title:
                    logger.info(f"Auto-generated title for {session_id}: {title}")
                    # Update without firing event
                    await self.update_session(session_id, {"name": title})
            except Exception as e:
                logger.warning(f"Failed to auto-generate title: {e}")

    async def stream_chat(
        self,
        session_id: str,
        message: "str | list",
        tools: Optional[List[str]] = None,
    ):
        """
        Stream chat response with SSE events directly from nimbus-next RuntimeLoop.
        """
        logger.info(f"[stream_chat] Starting for session {session_id}")
        agent_os = await self.get_or_create_agent(session_id)

        # Publish user message so multi-client subscribers can see it
        user_content = message if isinstance(message, str) else "[multimodal message]"
        if isinstance(message, list):
            # Extract text from multimodal content parts
            text_parts = [p.get("text", "") for p in message if isinstance(p, dict) and p.get("type") == "text"]
            user_content = " ".join(text_parts) if text_parts else "[multimodal message]"
        await self._sse_hub.publish(session_id, "user_message", {"content": user_content})

        # message_start signals a new assistant turn (connected is sent by SSEHub.subscribe automatically)
        await self._sse_hub.publish(session_id, "message_start", {"role": "assistant"})

        loop = None
        try:
            logger.info("[stream_chat] Calling agent_os.stream_with_queue...")
            
            # Fire off auto-titling if this is the first real interaction
            session = await self.get_session(session_id)
            if session and session.get("name", "").startswith("New Chat"):
                asyncio.create_task(self._auto_generate_title(session_id, agent_os))

            # Retrieve previous state if any
            dump = self._storage.load_session(session_id) or {}

            # Inject llm_config into metadata so RuntimeLoop._save_core_dump()
            # can preserve it when writing vcpu_config (which is VCPU runtime state)
            loop_metadata = dump.get("metadata", {})
            loop_metadata["llm_config"] = dump.get("llm_config", {})

            # Reset execution counters for new turn (iteration is per-task, not cumulative)
            vcpu_state = dump.get("vcpu_state", {})
            vcpu_state["iteration"] = 0
            vcpu_state["consecutive_thoughts"] = 0
            vcpu_state["consecutive_errors"] = 0

            # Generate the RuntimeLoop (pi-style)
            loop = agent_os.stream_with_queue(
                message,
                session_id=session_id,
                storage=self._storage,
                metadata=loop_metadata,
                initial_messages=dump.get("messages", []),
                initial_vcpu_state=vcpu_state,
            )
            self._active_loops[session_id] = loop

            # Yield fine-grained events mapped to SSE UI format
            async for event in loop.stream():
                evt_type = event.get("type")
                
                if evt_type == "interrupted":
                    logger.info("[stream_chat] Execution cancelled by interrupt request")
                    await self._sse_hub.publish(session_id, "done", {"status": "CANCELLED"})
                    break  # P0 fix: stop processing loop events after interrupt
                
                if evt_type == "message_queued":
                    logger.info(f"[stream_chat] Handled enqueued message: {str(event.get('content'))[:50]}...")
                    continue

                if evt_type == "steering_injected":
                    logger.info(f"[stream_chat] Steering injected: {str(event.get('content'))[:50]}...")
                    await self._sse_hub.publish(session_id, "user_message", {"content": event.get("content", ""), "injected": True})
                    continue

                if evt_type == "followup_injected":
                    logger.info(f"[stream_chat] Follow-up injected: {str(event.get('content'))[:50]}...")
                    continue

                if evt_type == "usage_update":
                    await self._sse_hub.publish(session_id, "usage_update", {
                        "step_usage": event.get("step_usage", {}),
                        "cumulative_usage": event.get("cumulative_usage", {}),
                    })
                    continue

                if evt_type == "text_delta":
                    # Text already published token-by-token via on_text_delta callback.
                    # Skip re-publishing the aggregated text to avoid duplicate content.
                    continue
                elif evt_type in ("tool_call_start", "tool_call_done"):
                    # Already published in real-time via gate callback
                    continue
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
            
            # Normal completion

        except asyncio.CancelledError:
            logger.info(f"[stream_chat] Cancelled by user for session {session_id}")
            raise
        except Exception as chat_err:
            logger.error(f"[stream_chat] Streaming failed: {chat_err}", exc_info=True)
            raise
        finally:
            # P0 fix: only publish done:OK if the loop was NOT interrupted
            # (interrupted path already published done:CANCELLED)
            loop = self._active_loops.get(session_id)
            was_interrupted = loop and getattr(loop, '_interrupted', False)

            if session_id in self._active_loops:
                del self._active_loops[session_id]

            if not was_interrupted:
                await self._sse_hub.publish(session_id, "done", {"status": "OK"})

            # Do NOT close the SSE connections — keep subscribers alive so they
            # can receive the next task's events without reconnecting.
            # Only reset the event log so the next prepare_session starts fresh.
            await self._sse_hub.reset_session_log(session_id)



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
                loop.abort()
                # Wait for the loop to finish cleanly (saves core dump on exit)
                try:
                    await asyncio.wait_for(loop.wait_for_idle(), timeout=10.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Timed out waiting for loop idle on {session_id}")
                interrupted = True

            # Do NOT cancel the task -- loop.abort() already causes clean shutdown.
            # Calling task.cancel() injects CancelledError that may bypass the core dump save.

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
            loop.message_queue.enqueue(content)
            logger.info(f"💉 Injected message into running nimbus-next loop for {session_id}")
            return True
            
        return False

    async def close_all(self) -> None:
        """Close all active sessions."""
        async with self._lock:
            self._sessions.clear()

        if self._shared_llm_client:
            logger.info("🔌 Closing shared LLM adapter")
            if hasattr(self._shared_llm_client, "stop"):
                await self._shared_llm_client.stop()
            elif hasattr(self._shared_llm_client, "__aexit__"):
                await self._shared_llm_client.__aexit__(None, None, None)
            self._shared_llm_client = None

    def get_active_count(self) -> int:
        """Get number of active agent instances."""
        return len(self._sessions)

    def is_session_loaded(self, session_id: str) -> bool:
        """Check if a session has an active agent."""
        return session_id in self._sessions
