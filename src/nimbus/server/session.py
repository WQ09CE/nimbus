"""Session Manager — Manages agent sessions with nimbus-next AgentOS.

Creates per-session AgentOS instances, streams events via SSE, and handles
message injection and interruption.
"""

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from nimbus import AgentOS
from nimbus.core.session_log import ContractNewerError, OwnershipLostError
from nimbus.core.storage import SessionStorage

from .permission import PermissionManager
from .sse import SSEHub
from .tool_policy import SessionToolAuthorizer

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
        ledger=None,
    ):
        self._sse_hub = sse_hub
        self._ledger = ledger  # nimbus.infra.ledger.Ledger or None (single-pod)
        self._ingest: Dict[str, int] = {}  # session_id -> tool output bytes not yet charged to the ledger (R4)
        self._streamed: Dict[str, int] = {}  # call_id -> bytes already metered while streaming
        self._charging: set = set()  # sessions with a ledger charge in flight
        self._permission_manager = permission_manager
        self._max_sessions = max_sessions
        self._sessions: Dict[str, AgentOS] = {}  # session_id -> AgentOS
        self._active_tasks: Dict[str, asyncio.Task] = {}  # session_id -> running task
        self._active_loops: Dict[str, Any] = {}  # session_id -> RuntimeLoop
        # Pause can arrive while model/AgentOS setup is still constructing the
        # loop; retain the intent and apply it at the first clean seam.
        self._pause_requests: set[str] = set()
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
        skills: Optional[List[str]] = None,
        plugins: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new session in memory."""
        session_id = f"sess_{uuid.uuid4().hex[:12]}"

        from nimbus.config import get_config
        config = get_config()
        enabled_skills = list(skills if skills is not None else config.enabled_skills)
        enabled_plugins = list(plugins if plugins is not None else config.enabled_plugins)

        # Store agent_mode in config_overrides
        config_overrides = {
            "agent_mode": agent_mode,
            "skills": enabled_skills,
            "plugins": enabled_plugins,
        }

        now_iso = datetime.now(timezone.utc).isoformat()

        session = {
            "id": session_id,
            "name": name or "New Chat",
            "workspace_path": workspace_path,
            "llm_config": model_config or {},
            "skills": enabled_skills,
            "plugins": enabled_plugins,
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
                "skills": enabled_skills,
                "plugins": enabled_plugins,
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
                elif k == "skills":
                    meta["skills"] = list(v or [])
                    config_overrides = meta.get("config_overrides", {})
                    if isinstance(config_overrides, dict):
                        config_overrides["skills"] = list(v or [])
                        meta["config_overrides"] = config_overrides
                    need_rebuild = True
                elif k == "plugins":
                    meta["plugins"] = list(v or [])
                    config_overrides = meta.get("config_overrides", {})
                    if isinstance(config_overrides, dict):
                        config_overrides["plugins"] = list(v or [])
                        meta["config_overrides"] = config_overrides
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
            meta_overrides = meta.get("config_overrides", {})
            if not isinstance(meta_overrides, dict):
                meta_overrides = {}
            return {
                "id": session_id,
                "status": dump.get("status", "unknown"),
                "name": meta.get("name", "Unknown"),
                "workspace_path": meta.get("workspace_path"),
                "llm_config": dump.get("llm_config", {}),
                "skills": meta.get("skills", meta_overrides.get("skills", [])),
                "plugins": meta.get("plugins", meta_overrides.get("plugins", [])),
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
            meta_overrides = meta.get("config_overrides", {})
            if not isinstance(meta_overrides, dict):
                meta_overrides = {}
            sessions.append({
                "id": d.get("session_id"),
                "status": d.get("status", "unknown"),
                "name": meta.get("name", "Unknown"),
                "workspace_path": meta.get("workspace_path"),
                "llm_config": d.get("llm_config", {}),
                "skills": meta.get("skills", meta_overrides.get("skills", [])),
                "plugins": meta.get("plugins", meta_overrides.get("plugins", [])),
                "config_overrides": meta.get("config_overrides", {}),
                "created_at": meta.get("created_at") or d.get("updated_at"),
                "updated_at": d.get("updated_at"),
                "message_count": len(d.get("messages", [])),
            })

        # Sort is already handled by list_sessions
        return sessions[offset:offset+limit], len(sessions)

    async def fork_session(
        self,
        parent_id: str,
        at_seq: Optional[int] = None,
        name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fork a session from its event log (Phase 3 seed primitive).

        at_seq replays from that point (mid-turn cuts are closed with graded
        synthetic results by the storage layer). Returns the new session's
        metadata dict, or None if the parent doesn't exist.
        """
        new_id = f"sess_{uuid.uuid4().hex[:12]}"
        dump = self._storage.fork_session(parent_id, new_id, at_seq=at_seq)
        if dump is None:
            return None

        # Rebrand the copied parent metadata for the new session.
        metadata = dict(dump.get("metadata", {}))
        parent_name = metadata.get("name") or "Unknown"
        metadata["name"] = name or f"{parent_name} (fork)"
        metadata["created_at"] = datetime.now(timezone.utc).isoformat()
        self._storage.save_session(
            session_id=new_id,
            status="active",
            messages=dump.get("messages", []),
            vcpu_state=dump.get("vcpu_state", {}),
            vcpu_config=dump.get("vcpu_config", {}),
            llm_config=dump.get("llm_config", {}),
            metadata=metadata,
        )
        logger.info(f"🌱 Forked session {parent_id} → {new_id} (at_seq={at_seq})")
        return await self.get_session(new_id)

    async def get_session_log(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Read the session's event log: events + invariants + derived stats.

        Returns None when no log exists. A corrupt log is reported, not
        raised — the UI should surface it, not 500.
        """
        from nimbus.core.session_log import check_invariants, load_session_log

        try:
            log = load_session_log(self._storage.base_dir, session_id)
            if not log.events:
                return None
        except ValueError as e:
            return {"events": [], "corrupt": str(e), "invariant_violations": [], "stats": {}}

        events = log.events
        counts: Dict[str, int] = {}
        for e in events:
            counts[e.type] = counts.get(e.type, 0) + 1
        reasons = [
            (e.data.get("reason") or {}).get("kind")
            for e in events if e.type == "turn/end"
        ]
        return {
            "events": [e.to_dict() for e in events],
            "corrupt": None,
            "invariant_violations": check_invariants(events, allow_open_tail=True),
            "stats": {
                "events": len(events),
                "turns": counts.get("turn/start", 0),
                "steps": counts.get("step/start", 0),
                "tool_results": counts.get("tool/result", 0),
                "compactions": counts.get("compaction/applied", 0),
                "policy_requests": counts.get("policy/requested", 0),
                "policy_denials": sum(
                    1 for e in events
                    if e.type == "policy/decision" and not e.data.get("allowed", False)
                ),
                "sandbox_events": counts.get("sandbox/result", 0),
                "turn_end_reasons": reasons,
            },
        }

    async def delete_session(self, session_id: str) -> None:
        """Soft delete a session."""
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
            self._storage.delete_session(session_id)
        self._pause_requests.discard(session_id)
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

                # Construct full model name (provider/model_id). A missing or
                # "default" model_id means "use the configured default model" —
                # composing "provider/default" would hit a nonexistent model
                # (e.g. after the UI PATCHes only thinking_effort on a
                # default-model session).
                if model_id in ("default", "", None):
                    from nimbus.config import get_config
                    full_model = get_config().default_model
                else:
                    provider = model_config.get("provider", "google")
                    if provider:
                        full_model = f"{provider}/{model_id}"
                    else:
                        full_model = model_id

                # Use factory to create DirectAdapter
                from nimbus.adapters.llm_factory import create_llm_client

                thinking_effort = model_config.get("thinking_effort")
                if thinking_effort not in (None, "off", "low", "medium", "high"):
                    logger.warning(f"Ignoring invalid thinking_effort: {thinking_effort!r}")
                    thinking_effort = None

                llm_client = await create_llm_client(
                    model=full_model,
                    temperature=temperature,
                    thinking=thinking,
                    thinking_effort=thinking_effort,
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
        skill_names = session.get("skills")
        if skill_names is None:
            skill_names = overrides.get("skills")
        if skill_names is None:
            skill_names = nimbus_config.enabled_skills
        skill_names = list(skill_names or [])

        plugin_names = session.get("plugins")
        if plugin_names is None:
            plugin_names = overrides.get("plugins")
        if plugin_names is None:
            plugin_names = nimbus_config.enabled_plugins
        plugin_names = list(plugin_names or [])

        # Build path context from session workspace (if set)
        from nimbus.core.path_context import AgentPathContext
        if workspace:
            path_ctx = AgentPathContext(
                workspace_root=str(workspace),
                target_root=str(workspace),
                execution_cwd=str(workspace),
            )
        else:
            path_ctx = AgentPathContext.from_cwd()

        # Build the new nimbus-next AgentOS
        from nimbus.core.agent import AgentConfig, AgentOS

        agent_config = AgentConfig()
        agent_config.llm_call_timeout = 120.0
        agent_config.text_is_final = True  # Chat mode: pure text = final response, don't poke
        agent_config.max_consecutive_thoughts = 2  # Safety net: stop after 2 thoughts max
        # Our context-window cap (0 = use the model's full window; compaction is ours).
        agent_config.max_context_tokens = nimbus_config.max_context_tokens
        agent_config.sandbox_mode = nimbus_config.sandbox_mode

        system_prompt = (
            "You are a capable AI assistant. Use tools to solve the user's tasks. Think step by step.\n"
            "Always reply in the same language the user uses (请使用与用户相同的语言回复)。\n"
            "If you say you will call a tool or spawn an agent, emit that tool call in the SAME "
            "response — never stop at a plan. A plain-text reply with no tool call is your FINAL answer.\n\n"
            "# Task Management\n"
            "Use `update_plan` as working memory for tasks requiring multiple steps. Call it once "
            "at the start with a concise TODO list, and replace the full plan as work completes or "
            "key findings change. Do not create a scratchpad with Write/Edit/Read unless the task "
            "itself genuinely needs a separate artifact.\n\n"
            "# Agent Collaboration\n"
            "You are an **orchestrator**. Prefer delegating execution to sub-agents; "
            "reserve direct tool use for trivial one-shot actions (single Read, quick Bash).\n\n"
            "Spawn rules:\n"
            "1. **Parallel by default** — independent sub-tasks MUST be spawned in the same function_calls block.\n"
            "2. **Right role** — `reader` (Read/Grep/Glob) for inspecting and listing files, `worker` (Read/Grep/Glob + Write/Edit/Bash) for anything that runs commands or modifies files. If unsure, use `worker`.\n"
            "3. **No nesting** — sub-agents MUST NOT spawn further sub-agents.\n"
            "4. **Rich context** — every spawn task must include: goal, relevant file paths, and expected output format.\n"
            "5. **Verify after delegate** — after a worker completes, always validate the result (read file, run test).\n\n"
            "If a sub-agent times out, read its scratchpad to recover partial progress."
        )

        from nimbus.skills import SkillManager

        skill_manager = SkillManager.from_config(nimbus_config)
        active_skills = skill_manager.load_enabled(skill_names)

        from nimbus.plugins import PluginContext, PluginManager

        plugin_manager = PluginManager.from_config(nimbus_config)
        plugin_snapshot = plugin_manager.snapshot(
            plugin_names,
            context=PluginContext(
                plugin_name="",
                generation=0,
                session_id=session_id,
                workspace=str(path_ctx.target_root),
            ),
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

        # Coalescing window for high-frequency delta events. Token-level text
        # deltas (and streamed tool output) arrive at 50-100 events/s; batching
        # them for up to this window collapses bursts into single SSE events
        # (fewer frames over the wire, smaller replay log) with imperceptible
        # latency — the UI already buffers paints per animation frame.
        COALESCE_WINDOW_S = 0.025

        def _coalesce_key(payload):
            """Events sharing a key are merged; None means never merge."""
            etype, data = payload
            if etype == "message":
                return ("message",)
            # ui_detail entries carry per-event structure — keep them atomic
            if etype == "tool_output_chunk" and not data.get("ui_detail"):
                return ("tool_output_chunk", data.get("action_id"))
            return None

        def _coalesce_merge(acc, nxt):
            etype, data = acc
            if etype == "message":
                return (etype, {**data, "content": data["content"] + nxt[1]["content"]})
            return (etype, {**data, "chunk": data.get("chunk", "") + nxt[1].get("chunk", "")})

        async def _publish_payload(payload):
            if payload[0] == "__barrier__":
                future = payload[1]
                if not future.done():
                    future.set_result(None)
                return
            await self._sse_hub.publish(session_id, payload[0], payload[1])

        async def _publisher_task():
            loop = asyncio.get_running_loop()
            stopped = False
            while not stopped:
                try:
                    payload = await event_queue.get()
                    if payload is None:
                        break
                    key = _coalesce_key(payload)
                    if key is not None:
                        deadline = loop.time() + COALESCE_WINDOW_S
                        while True:
                            timeout = deadline - loop.time()
                            if timeout <= 0:
                                break
                            try:
                                nxt = await asyncio.wait_for(event_queue.get(), timeout)
                            except asyncio.TimeoutError:
                                break
                            if nxt is None:
                                stopped = True
                                break
                            if _coalesce_key(nxt) == key:
                                payload = _coalesce_merge(payload, nxt)
                            else:
                                # Different event type: flush the batch, then the
                                # interleaving event — order is preserved.
                                await _publish_payload(payload)
                                payload = nxt
                                key = _coalesce_key(payload)
                                if key is None:
                                    break
                                deadline = loop.time() + COALESCE_WINDOW_S
                    await _publish_payload(payload)
                except Exception as e:
                    logger.error(f"Event publisher error: {e}")

        pub_task = asyncio.create_task(_publisher_task())

        async def _flush_publisher() -> None:
            """FIFO barrier: all queued deltas precede lifecycle terminal events."""
            future = asyncio.get_running_loop().create_future()
            event_queue.put_nowait(("__barrier__", future))
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Timed out draining SSE publisher for %s", session_id)

        # Real-time gate callback: queue tool events to SSE as they happen
        def _gate_event_cb(event):
            if event.type == "TOOL_STARTED":
                event_queue.put_nowait(("tool_call", {
                    "tool": event.data.get("tool"),
                    "args": event.data.get("args", {}),
                    "action_id": event.data.get("call_id"),
                }))
            elif event.type == "TOOL_CALL_DELTA":
                chunk = event.data.get("chunk")
                if isinstance(chunk, str):
                    self._meter_ingest(session_id, event.data.get("call_id"), len(chunk))
                delta_payload = {
                    "tool": event.data.get("tool"),
                    "chunk": event.data.get("chunk"),
                    "action_id": event.data.get("call_id"),
                }
                if event.data.get("ui_detail"):
                    delta_payload["ui_detail"] = event.data["ui_detail"]
                event_queue.put_nowait(("tool_output_chunk", delta_payload))
            elif event.type == "TOOL_FINISHED":
                # R4: per-turn ingest — the bytes this result pulled into the process (the
                # untruncated raw kept for the UI, else the model-facing output)
                ud = event.data.get("ui_detail") or {}
                raw = ud.get("raw_text_output") if isinstance(ud, dict) else None
                n = len(raw) if isinstance(raw, str) else len(event.data.get("output") or "")
                self._meter_ingest(session_id, event.data.get("call_id"), n, final=True)
                event_queue.put_nowait(("tool_result", {
                    "tool": event.data.get("tool"),
                    "status": event.data.get("status"),
                    "output": event.data.get("output"),
                    "action_id": event.data.get("call_id"),
                    "duration_ms": event.data.get("duration_ms"),
                    "authorization_ms": event.data.get("authorization_ms"),
                    "execution_ms": event.data.get("execution_ms"),
                    "ui_detail": event.data.get("ui_detail"),
                    "fault": event.data.get("fault"),
                }))
            elif event.type == "POLICY_REQUESTED":
                event_queue.put_nowait(("permission_request", dict(event.data)))
            elif event.type == "POLICY_DECIDED":
                event_queue.put_nowait(("policy_decision", dict(event.data)))
            elif event.type == "SANDBOX_STATUS":
                event_queue.put_nowait(("sandbox_status", dict(event.data)))

        tool_authorizer = SessionToolAuthorizer(
            self._permission_manager,
            session_id,
            path_ctx,
            nimbus_config.sandbox_mode,
        )

        # Token-level text streaming callback
        def _text_delta_cb(chunk: str):
            event_queue.put_nowait(("message", {"content": chunk}))

        # Tool output streaming callback (enables on_update injection for spawn_agent)
        def _tool_output_cb(tool_name: str, chunk: str):
            pass  # Actual SSE emission is handled by gate's TOOL_CALL_DELTA event

        agent_os = AgentOS(
            config=agent_config,
            adapter=llm_client,
            system_prompt=system_prompt,
            memory=memory_content,
            skills=active_skills,
            skill_context={
                "session_id": session_id,
                "workspace": str(path_ctx.target_root),
            },
            plugin_snapshot=plugin_snapshot,
            event_callback=_gate_event_cb,
            on_text_delta=_text_delta_cb,
            on_tool_output=_tool_output_cb,
            path_context=path_ctx,
            tool_authorizer=tool_authorizer,
        )

        # Attach publisher lifecycle/barrier to the AgentOS cached for this
        # session. Runtime terminal events use the barrier before publishing
        # paused/error/done, preventing them from overtaking queued deltas.
        agent_os._pub_task = pub_task
        agent_os._flush_publisher = _flush_publisher

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
                if mmu and mmu.message_count:
                    for msg in mmu.messages_view():
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
        message: "str | list | None",
        tools: Optional[List[str]] = None,
        *,
        resume: bool = False,
        resume_interrupted: bool = False,
    ):
        """
        Stream chat response with SSE events directly from nimbus-next RuntimeLoop.
        """
        logger.info(f"[stream_chat] Starting for session {session_id}")
        agent_os = await self.get_or_create_agent(session_id)

        # A resume continues the balanced paused surface without inventing a
        # synthetic user message. A normal chat still publishes its input for
        # multi-client observers.
        if not resume:
            user_content = message if isinstance(message, str) else "[multimodal message]"
            if isinstance(message, list):
                text_parts = [
                    p.get("text", "") for p in message
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                user_content = " ".join(text_parts) if text_parts else "[multimodal message]"
            await self._sse_hub.publish(
                session_id, "user_message", {"content": user_content}
            )

        await self._sse_hub.publish(
            session_id,
            "message_start",
            {"role": "assistant", "resumed": resume},
        )

        loop = None
        terminal_status = "OK"

        async def flush_realtime_events() -> None:
            flush = getattr(agent_os, "_flush_publisher", None)
            if flush is not None:
                await flush()

        request_id = uuid.uuid4().hex[:12]
        log_epoch = None
        if self._ledger is not None:
            try:
                log_epoch = await self._ledger.claim(session_id, request_id, fresh=not resume)
            except Exception as e:  # bookkeeping must never block a turn
                logger.warning("[stream_chat] ledger claim failed: %s", e)

        try:
            logger.info("[stream_chat] Calling agent_os.stream_with_queue...")

            # Fire off auto-titling only for a real new user interaction.
            session = await self.get_session(session_id)
            if (
                not resume
                and session
                and session.get("name", "").startswith("New Chat")
            ):
                asyncio.create_task(self._auto_generate_title(session_id, agent_os))

            # Retrieve previous state if any
            dump = self._storage.load_session(session_id) or {}

            # Inject llm_config into metadata so RuntimeLoop._save_core_dump()
            # can preserve it when writing vcpu_config (which is VCPU runtime state)
            loop_metadata = dump.get("metadata", {})
            loop_metadata["llm_config"] = dump.get("llm_config", {})
            if log_epoch is not None:
                loop_metadata["log_epoch"] = log_epoch  # fence token for every log write this run
            if resume_interrupted:
                loop_metadata["resume_interrupted"] = True  # re-execute graded resumable calls, then continue

            # A fresh user turn resets task counters. Resume restores the
            # checkpoint verbatim, including the remaining iteration budget.
            vcpu_state = dict(dump.get("vcpu_state", {}))
            if not resume:
                vcpu_state["iteration"] = 0
                vcpu_state["consecutive_thoughts"] = 0
                vcpu_state["consecutive_errors"] = 0

            # Generate the RuntimeLoop. Empty goal means no new user message;
            # the VCPU continues from the latest paired tool result.
            loop = agent_os.stream_with_queue(
                "" if resume else message,
                session_id=session_id,
                storage=self._storage,
                metadata=loop_metadata,
                initial_messages=dump.get("messages", []),
                initial_vcpu_state=vcpu_state,
            )
            self._active_loops[session_id] = loop
            if session_id in self._pause_requests:
                self._pause_requests.discard(session_id)
                loop.request_pause()

            # Yield fine-grained events mapped to SSE UI format
            async for event in loop.stream():
                evt_type = event.get("type")

                if evt_type == "interrupted":
                    logger.info("[stream_chat] Execution cancelled by interrupt request")
                    terminal_status = "CANCELLED"
                    await flush_realtime_events()
                    await self._sse_hub.publish(
                        session_id, "done", {"status": terminal_status}
                    )
                    break

                if evt_type == "paused":
                    terminal_status = "PAUSED"
                    result = event.get("result")
                    # Two-phase cut: the loop just quiesced at a clean seam —
                    # bind the machine state before announcing the pause, so
                    # a durable checkpoint precedes its advertisement.
                    await self._snapshot_sandbox_at_seam(session_id)
                    await flush_realtime_events()
                    await self._sse_hub.publish(session_id, "paused", {
                        "status": "PAUSED",
                        "message": getattr(result, "output", None),
                    })
                    continue

                if evt_type == "step_end":
                    # Layer-3 binding at every dirty step seam (not only at PAUSE):
                    # a clean seam is a metadata write, a dirty one a workspace
                    # snapshot — the price of being restorable after a crash.
                    await self._snapshot_sandbox_at_seam(session_id)
                    await self._charge_ingest(session_id)
                    continue
                if evt_type == "resume_replay":
                    # interrupted-turn resume re-executed a graded call
                    await self._sse_hub.publish(session_id, "resume_replay", {
                        "tool": event.get("tool"), "call_id": event.get("call_id"),
                        "status": event.get("status"), "graded": event.get("graded"),
                    })
                    continue
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
                        "context_window": event.get("context_window"),
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
                    if result:
                        terminal_status = result.status
                    if result and result.status == "ERROR":
                        fault = getattr(result, "fault", None)
                        error_payload = {
                            "code": "agent_error",
                            "message": fault.message if fault else str(result.output),
                            "retryable": False,
                        }
                        await flush_realtime_events()
                        await self._sse_hub.publish(session_id, "error", error_payload)
                    elif result and result.status == "OK":
                        logger.info("[stream_chat] Completed with status: OK")

            # Normal completion

        except asyncio.CancelledError:
            logger.info(f"[stream_chat] Cancelled by user for session {session_id}")
            raise
        except OwnershipLostError as lost:
            # A newer epoch owns this session (another pod took over while we
            # were frozen/partitioned). The zombie stops here: no core dump, no
            # more tool execution, and the client is told the truth — not "done: OK".
            terminal_status = "OWNERSHIP_LOST"
            logger.warning(f"[stream_chat] ownership lost for {session_id}: {lost}")
        except Exception as chat_err:
            terminal_status = "ERROR"
            logger.error(f"[stream_chat] Streaming failed: {chat_err}", exc_info=True)
            raise
        finally:
            if self._ledger is not None:
                await self._charge_ingest(session_id)
                try:
                    await self._ledger.release(session_id, request_id)
                except Exception as e:
                    logger.warning("[stream_chat] ledger release failed: %s", e)

            loop = self._active_loops.get(session_id)
            was_interrupted = bool(
                loop and getattr(loop, "_interrupted", False)
            )

            self._active_loops.pop(session_id, None)

            # The interrupted branch already emitted CANCELLED. Preserve the
            # last-run SSE replay log after completion so a resume caller or
            # refreshed observer can attach late and still receive PAUSED/done.
            # prepare_session() resets it at the beginning of the next run.
            if not was_interrupted:
                await flush_realtime_events()
                await self._sse_hub.publish(
                    session_id, "done", {"status": terminal_status}
                )


    INGEST_FLUSH_BYTES = 1 << 20

    def _meter_ingest(self, session_id: str, call_id: Optional[str], n: int, final: bool = False) -> None:
        """Meter tool output at the door — as it streams in — not at the step seam: a pod
        that dies of a result never reaches the seam (R4 drill: the suspect's ledger said 0).
        Streamed bytes are charged once; the final result adds only what was not streamed.
        Past INGEST_FLUSH_BYTES the count is posted to the ledger without waiting for a seam."""
        seen = self._streamed.get(call_id or "", 0)
        if final:
            self._streamed.pop(call_id or "", None)
            n = max(0, n - seen)
        elif call_id:
            self._streamed[call_id] = seen + n
        if n <= 0:
            return
        self._ingest[session_id] = self._ingest.get(session_id, 0) + n
        if self._ingest[session_id] >= self.INGEST_FLUSH_BYTES and session_id not in self._charging:
            self._charging.add(session_id)
            asyncio.get_running_loop().create_task(self._charge_ingest(session_id))

    async def _charge_ingest(self, session_id: str) -> None:
        """Post the bytes ingested since the last charge to the ledger's turn record."""
        n = self._ingest.pop(session_id, 0)
        try:
            if n and self._ledger is not None:
                await self._ledger.account(session_id, n)
        except Exception as e:  # bookkeeping never blocks a turn
            logger.warning("[stream_chat] ledger account failed: %s", e)
        finally:
            self._charging.discard(session_id)

    # -- interrupted-turn recovery (nimbus-lab R3): admission + first-tier resume --

    async def on_orphan(self, rec: Dict[str, Any]) -> None:
        """Ledger callback: another pod's turn lost its owner. Decide by the
        in-flight call's repeat class — free/keyed → resume here; once →
        fast-fail (repair-on-open, user told), never rerun automatically."""
        from nimbus.core.session_log import (
            TOOL_OUTCOME_UNKNOWN,
            interrupted_turn_closers,
            load_session_log,
        )

        session_id = rec.get("session_id", "")
        if self._ledger is None or not session_id:
            return
        if rec.get("pod") == self._ledger.pod_id or self.is_session_running(session_id):
            await self._ledger.resolve(session_id, "skipped:local")
            return
        try:
            events = load_session_log(self._storage.base_dir, session_id).events
        except ContractNewerError as e:
            # R5.2: written by a newer contract — not ours to repair or resume. Refuse loudly;
            # a rollout that rolled the contract back must drain (or roll forward) first.
            logger.warning("[on_orphan] %s: %s — refused", session_id, e)
            await self._sse_hub.publish(session_id, "interrupted", {
                "reason": "contract_newer", "resumable": False,
                "hint": f"This session was written by a newer runtime (contract {e.log_contract}); this pod "
                        f"(contract {e.mine}) cannot continue it.",
            })
            await self._ledger.resolve(session_id, f"refused:contract={e.log_contract}")
            return
        except Exception as e:
            await self._ledger.resolve(session_id, f"skipped:log_unreadable:{type(e).__name__}")
            return
        closers = interrupted_turn_closers(events)
        if not closers:
            await self._ledger.resolve(session_id, "skipped:balanced")  # nothing was in flight
            return
        blocked = [c for c in closers if c.type == "tool/result" and c.data.get("code") == TOOL_OUTCOME_UNKNOWN]
        if blocked:
            tool = blocked[0].data.get("message", {}).get("name")
            logger.warning("[on_orphan] %s: in-flight %s is 'once' — fast-fail, not resuming", session_id, tool)
            await self.fast_fail_interrupted(session_id)
            await self._ledger.resolve(session_id, f"fast_fail:{tool}")
            return
        # R4: attempts budget (Temporal: RetryPolicy.maximum_attempts). Each takeover is a
        # new epoch; a turn that keeps killing or stalling its owners (poison turn) must not
        # bounce between pods forever — after max attempts it is closed and the user told.
        attempts = int(rec.get("epoch") or 1)
        max_attempts = int(os.environ.get("NIMBUS_RESUME_MAX_ATTEMPTS", "3"))
        if attempts >= max_attempts:
            logger.warning("[on_orphan] %s: %d owners lost (max %d) — quarantined, not resumed",
                           session_id, attempts, max_attempts)
            await self.fast_fail_interrupted(
                session_id, reason="max_attempts",
                hint=f"This turn lost {attempts} owner pods in a row (last: {rec.get('pod')}). Not resumed"
                     f" automatically; something about this turn kills or stalls the pod that runs it.")
            await self._ledger.resolve(session_id, f"quarantine:attempts={attempts}")
            return
        # R4: the cost axis. A turn that has already ingested more than the budget is the
        # likeliest reason its pod died hot; resuming it here would put the same bytes on this
        # pod (the 0903 cascade: the rescuer dies of the rescued). Quarantine, tell the user.
        ingest = int(rec.get("bytes") or 0)
        budget = int(os.environ.get("NIMBUS_RESUME_INGEST_BUDGET_MB", "8")) << 20
        if ingest >= budget:
            logger.warning("[on_orphan] %s: ingested %d B >= budget %d B (owner mem %s%%) — quarantined, not resumed",
                           session_id, ingest, budget, rec.get("owner_mem_pct") or "?")
            await self.fast_fail_interrupted(
                session_id, reason="oom_suspect",
                hint=f"This turn had pulled {ingest >> 20} MB of tool output into pod {rec.get('pod')} when it died"
                     f" (memory {rec.get('owner_mem_pct') or '?'}%). Not resumed automatically; retry deliberately.")
            await self._ledger.resolve(session_id, f"quarantine:ingest={ingest >> 20}MB")
            return
        logger.warning("[on_orphan] %s: resuming interrupted turn on pod %s", session_id, self._ledger.pod_id)
        started = await self.resume_interrupted(session_id)
        await self._ledger.resolve(session_id, "resume" if started else "skipped:resume_refused")

    async def fast_fail_interrupted(self, session_id: str, reason: str = "owner_pod_died",
                                    hint: Optional[str] = None) -> None:
        """Second tier: close the crashed turn now (graded synthetic results,
        turn/end interrupted) under our epoch and tell any attached client."""
        from nimbus.core.session_log import open_session_log

        request_id = uuid.uuid4().hex[:12]
        epoch = await self._ledger.claim(session_id, request_id)
        try:
            log = open_session_log(self._storage.base_dir, session_id, epoch=epoch)  # repair-on-open
            log.close()
        finally:
            await self._ledger.release(session_id, request_id)
        await self._sse_hub.publish(session_id, "interrupted", {
            "reason": reason, "resumable": False,
            "hint": hint or "The previous step may have executed; verify before retrying.",
        })

    async def resume_interrupted(self, session_id: str) -> bool:
        """First tier: continue the crashed turn on this pod — graded resumable
        calls are re-executed, then the model carries on. No user message needed."""
        if self.is_session_running(session_id):
            return False
        self._sessions.pop(session_id, None)  # never reuse another pod's cached surface
        self._sse_hub.prepare_session(session_id)
        await self._restore_sandbox_from_binding(session_id)

        async def run() -> None:
            try:
                await self.stream_chat(session_id, None, resume=True, resume_interrupted=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Interrupted-turn resume failed for %s: %s", session_id, exc, exc_info=True)

        task = asyncio.create_task(run())
        self.register_task(session_id, task)
        task.add_done_callback(lambda t: self.unregister_task(session_id))
        return True

    # -- graceful handoff (nimbus-lab R3.3): SIGTERM -> pause at seams -> announce --

    async def pause_all(self, timeout_s: float = 30.0) -> List[str]:
        """Request a step-seam pause on every running session and wait (bounded)
        until they have quiesced. Returns the session ids now durably paused."""
        running = [sid for sid in list(self._active_tasks) if self.is_session_running(sid)]
        for sid in running:
            await self.pause_session(sid)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and any(self.is_session_running(s) for s in running):
            await asyncio.sleep(0.2)
        paused = []
        for sid in running:
            session = await self.get_session(sid)
            if session and session.get("status") == "paused":
                paused.append(sid)
            else:
                logger.warning("[pause_all] %s did not reach a seam in time (status=%s)",
                               sid, session and session.get("status"))
        return paused

    async def handoff_all(self, bus, timeout_s: float = 30.0) -> List[str]:
        """Graceful shutdown: leave the handoff queue group (never take our own
        announcements), pause every session at a seam (binding included), then
        announce each paused session for another pod to resume."""
        await bus.stop_consuming()
        paused = await self.pause_all(timeout_s)
        for sid in paused:
            try:
                await bus.announce(sid, reason="graceful_shutdown")
            except Exception as e:
                logger.error("[handoff_all] announce failed for %s: %s", sid, e)
        return paused

    async def on_handoff(self, payload: Dict[str, Any]) -> bool:
        """Handoff consumer: resume a paused session announced by a dying pod.
        True = ack (taken, or nothing left to do); False = nak for redelivery."""
        sid = payload.get("session_id", "")
        if not sid:
            return True
        if self.is_session_running(sid):
            return True  # already ours
        self._sessions.pop(sid, None)  # rebuild from durable state, never from a cached surface
        try:
            result = await self.resume_session(sid)
        except ContractNewerError as e:
            logger.warning("[handoff] %s: %s — refused, redeliver", sid, e)
            await self._sse_hub.publish(sid, "interrupted", {
                "reason": "contract_newer", "resumable": False,
                "hint": f"Announced to a pod on contract {e.mine}; the session needs contract {e.log_contract}.",
            })
            return False  # nak: a capable member may still take it
        if result.get("success"):
            logger.warning("[handoff] resumed %s from pod %s", sid, payload.get("from_pod"))
            return True
        err = str(result.get("error", ""))
        if "not paused" in err or "already running" in err:
            return True  # someone else took it (or it completed) — nothing to redeliver
        logger.warning("[handoff] could not resume %s: %s", sid, err)
        return False

    async def _save_sandbox_binding(
        self, session_id: str, binding: Optional[Dict[str, Any]],
    ) -> None:
        """Persist (or clear) the layer-3 binding in session metadata."""
        # The running loop dumps ITS metadata (captured at run start) on every
        # core dump; keep it in step or the completion dump restores the binding
        # the run started with (R5: a handed-off turn finished on the new pod
        # with the old pod's pause snapshot still bound).
        loop = self._active_loops.get(session_id)
        if loop is not None and isinstance(getattr(loop, "metadata", None), dict):
            if binding is None:
                loop.metadata.pop("sandbox_binding", None)
            else:
                loop.metadata["sandbox_binding"] = binding
        async with self._lock:
            dump = self._storage.load_session(session_id)
            if not dump:
                return
            meta = dump.get("metadata", {})
            if binding is None:
                meta.pop("sandbox_binding", None)
            else:
                meta["sandbox_binding"] = binding
            self._storage.save_session(
                session_id=session_id,
                status=dump.get("status", "active"),
                messages=dump.get("messages", []),
                vcpu_state=dump.get("vcpu_state", {}),
                vcpu_config=dump.get("vcpu_config", {}),
                llm_config=dump.get("llm_config", {}),
                metadata=meta,
            )

    async def _snapshot_sandbox_at_seam(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Two-phase cut, phase 2: the runtime has quiesced at a PAUSED seam;
        snapshot the sandbox and persist the (session_ckpt, sandbox_snap_id)
        binding. Mounted leases skip — a mounted workspace is durable by
        itself. Any failure degrades to no binding; pause is never blocked.
        """
        agent = self._sessions.get(session_id)
        backend = agent.sandbox_backend() if hasattr(agent, "sandbox_backend") else None
        if backend is None or getattr(backend, "_lease", None) is None:
            return None
        if getattr(backend, "lease_mounted", False):
            return None
        # Crab-style side-effect awareness (memex Phase 2.5): a seam with no
        # side-effecting dispatch since the last snapshot reuses it. The
        # runtime knows exactly whether machine state could have moved
        # (traits.side_effects → backend.dirty), so a clean pause costs a
        # metadata write instead of a workspace tar. Unknown backends lack
        # the properties and default to dirty — conservative.
        last_snapshot = getattr(backend, "last_snapshot_id", None)
        if last_snapshot and not getattr(backend, "dirty", True):
            binding = {
                "backend": getattr(backend, "backend_id", "?"),
                "snapshot_id": last_snapshot,
                "lease_id": backend._lease.lease_id,
                "taken_at": datetime.now(timezone.utc).isoformat(),
                "reused": True,
            }
            await self._save_sandbox_binding(session_id, binding)
            logger.info(
                "Clean seam: reusing snapshot %s for %s", last_snapshot, session_id,
            )
            return binding
        try:
            snapshot_id = await backend.snapshot_lease()
        except Exception:
            logger.warning(
                "Sandbox snapshot at pause seam failed; binding skipped",
                exc_info=True,
            )
            return None
        binding = {
            "backend": getattr(backend, "backend_id", "?"),
            "snapshot_id": snapshot_id,
            "lease_id": backend._lease.lease_id,
            "taken_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._save_sandbox_binding(session_id, binding)
        logger.info(
            "Sandbox bound at seam: %s -> %s", session_id, snapshot_id,
        )
        return binding

    async def _restore_sandbox_from_binding(self, session_id: str) -> bool:
        """Resume's other half: replay BOTH sides together. The session
        checkpoint restores via the log; the sandbox restores from the bound
        snapshot. Restoring only one side is the split-brain the
        consistency-cut vertical demonstrated."""
        dump = self._storage.load_session(session_id) or {}
        binding = (dump.get("metadata") or {}).get("sandbox_binding")
        if not binding:
            return False
        agent = await self.get_or_create_agent(session_id)
        backend = agent.sandbox_backend() if hasattr(agent, "sandbox_backend") else None
        if backend is None:
            # The backend is built lazily by the first loop; arm a deferred
            # restore so its FIRST lease open replays the bound snapshot.
            if hasattr(agent, "set_sandbox_restore_hint"):
                agent.set_sandbox_restore_hint(binding["snapshot_id"])
                logger.info(
                    "Sandbox restore deferred to first lease open for %s (%s)",
                    session_id, binding["snapshot_id"],
                )
                return True
            logger.warning(
                "Session %s carries a sandbox binding but no compute backend; "
                "resuming without machine state", session_id,
            )
            return False
        try:
            await backend.restore_lease(binding["snapshot_id"])
        except Exception:
            logger.warning(
                "Sandbox restore from binding failed; resuming degraded",
                exc_info=True,
            )
            return False
        logger.info(
            "Sandbox restored for %s from %s", session_id, binding["snapshot_id"],
        )
        return True

    async def pause_session(self, session_id: str) -> Dict[str, Any]:
        """Request a soft stop at the next RuntimeLoop step seam."""
        session = await self.get_session(session_id)
        if session is None:
            return {"success": False, "error": "Session not found"}
        if session.get("status") == "paused" and not self.is_session_running(session_id):
            return {
                "success": True,
                "session_id": session_id,
                "status": "paused",
                "already_paused": True,
            }
        if not self.is_session_running(session_id):
            return {"success": False, "error": "Session is not running"}

        loop = self._active_loops.get(session_id)
        if loop is not None:
            loop.request_pause()
        else:
            self._pause_requests.add(session_id)
        return {
            "success": True,
            "session_id": session_id,
            "status": "pause_requested",
            "already_paused": False,
        }

    async def resume_session(self, session_id: str) -> Dict[str, Any]:
        """Start an assistant-only run from a durable PAUSED checkpoint.

        The task runs in the background and publishes through the normal SSE
        hub. Its replay buffer is retained after completion, so the caller can
        attach immediately after this JSON response without a race.
        """
        session = await self.get_session(session_id)
        if session is None:
            return {"success": False, "error": "Session not found"}
        if self.is_session_running(session_id):
            return {"success": False, "error": "Session is already running"}
        if session.get("status") != "paused":
            return {
                "success": False,
                "error": f"Session is {session.get('status')!r}, not paused",
            }

        dump = self._storage.load_session(session_id) or {}
        restored = dict(dump.get("vcpu_state", {}))
        self._pause_requests.discard(session_id)
        self._sse_hub.prepare_session(session_id)

        # Replay both sides together: session checkpoint via the log (below),
        # machine state via the bound snapshot (no-op when no binding).
        await self._restore_sandbox_from_binding(session_id)

        async def run_resume() -> None:
            try:
                await self.stream_chat(session_id, None, resume=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "Resume failed for %s: %s", session_id, exc,
                    exc_info=True,
                )
                await self._sse_hub.publish(session_id, "error", {
                    "code": "resume_failed",
                    "message": str(exc),
                    "retryable": True,
                })
            finally:
                self.unregister_task(session_id)

        task = asyncio.create_task(run_resume())
        self.register_task(session_id, task)
        return {
            "success": True,
            "session_id": session_id,
            "status": "resuming",
            "restored_step": restored.get("iteration", 0),
            "restored_iteration": restored.get("iteration", 0),
        }

    async def interrupt_session(self, session_id: str) -> Dict[str, Any]:
        """
        Interrupt a running session.
        """
        async with self._lock:
            agent_os = self._sessions.get(session_id)

        if not agent_os:
            return {"success": False, "error": "Session not loaded"}

        try:
            self._pause_requests.discard(session_id)
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
            self._pause_requests.clear()

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
