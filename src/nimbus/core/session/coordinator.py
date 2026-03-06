import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger
from nimbus.core.protocol import ToolResult
from nimbus.core.session import SessionManager

class SessionCoordinator:
    """Coordinates human-agent sessions and maps them to processes."""

    def __init__(self, agent_os):
        self.agent_os = agent_os

    @property
    def _processes(self):
        return self.agent_os.process_manager._processes

    @property
    def _factory(self):
        return self.agent_os._factory

    @property
    def heart(self):
        return self.agent_os.heart

    @property
    def _session_mgr(self):
        return self.agent_os._session_mgr

    def _emit_event(self, *args, **kwargs):
        self.agent_os._emit_event(*args, **kwargs)

    def inject_message(self, *args, **kwargs):
        return self.agent_os.process_manager.inject_message(*args, **kwargs)

    async def _run_process(self, process):
        return await self.agent_os.process_manager._run_process(process)

    def _nimfs_gc_session(self, *args, **kwargs):
        self.agent_os._nimfs_gc_session(*args, **kwargs)

    async def chat(self, message: "str | list", session_id: str | None = None) -> ToolResult:
            from loguru import logger
            is_existing_process = False
            if session_id and session_id in self._processes:
                process = self._processes[session_id]
                is_existing_process = True
            else:
                if not session_id:
                    session_id = f"chat-{uuid.uuid4().hex[:8]}"
    
                # Create process via factory (unified component assembly)
                process = self._factory.build(
                    pid=session_id,
                    goal="Interactive chat session",
                    role="chat",
                    is_interactive=True,
                    text_is_final=True,
                )
                self._processes[session_id] = process
                self._emit_event("PROC_SPAWNED", session_id, {"goal": "chat", "role": "chat"})
    
            if is_existing_process and process.state == "RUNNING":
                logger.info(f"Process {session_id} is busy. Converting chat to injection.")
                self.inject_message(process.pid, message)
    
                if self._session_mgr:
                    from nimbus.core.memory.context import Message
    
                    self._session_mgr.append_message(Message(role="user", content=message))
    
                return ToolResult(
                    status="OK", output="[Instruction appended to running task]", is_final=True
                )
    
            # --- AUTO RECALL INJECTION ---
            try:
                search_query = message if isinstance(message, str) else "\n".join([p.get("text", "") for p in message if isinstance(p, dict) and p.get("type", "") == "text"])
                if search_query and len(search_query.strip()) > 5 and hasattr(self.heart, "nimfs"):
                    results = self.heart.nimfs.search_memory(query=search_query, top_k=3, scope="project")
                    if results:
                        recall_text = "# 🧠 RELEVANT PAST MEMORY\n"
                        added = 0
                        for entry in results:
                            try:
                                abstract = self.heart.nimfs.read_memory(entry.memory_id, layer=1)
                                if abstract:
                                    recall_text += f"## {entry.title}\n{abstract}\n\n"
                                    added += 1
                            except Exception:
                                pass
                        
                        if added > 0:
                            process.mmu.update_recalled_memory(recall_text.strip())
                            logger.info(f"[{session_id}] Auto-Recalled {added} past memories into context (pinned).")
            except Exception as e:
                logger.warning(f"[{session_id}] Auto-Recall failed non-fatally: {e}")
            # -----------------------------
    
            process.vcpu._reset()
    
            if self._session_mgr:
                from nimbus.core.memory.context import Message
    
                self._session_mgr.append_message(Message(role="user", content=message))
    
            if process.state == "RUNNING":
                self.inject_message(process.pid, message)
            else:
                # Process not RUNNING — add message directly to MMU
                process.mmu.add_user_message(message)
    
                # Start Execution
                process.state = "RUNNING"
                process.interrupt_event.clear()  # Clear stale signal
                logger.info(f"[{session_id}] State transition: RUNNING")
                return await self._run_process(process)
    
            return ToolResult(status="OK", output="[Already Running]")

    def new_session(self, parent_session: Optional[str] = None) -> str:
            if self._session_mgr:
                return self._session_mgr.new_session(parent_session)
            return f"ephemeral-{uuid.uuid4().hex[:8]}"

    def load_session(self, session_file: Path) -> bool:
            if not self._session_mgr:
                return False
            return self._session_mgr.load_session(session_file)

    def restore_session(self, session_id: str, checkpoint: Any) -> None:
            """
            Restore a session process from a checkpoint.
    
            Args:
                session_id: The session ID (used as PID)
                checkpoint: The SessionCheckpointModel object
            """
            # Create process via factory (unified component assembly)
            process = self._factory.build(
                pid=session_id,
                goal="Restored session",
                role="chat",
                checkpoint=checkpoint,
                is_interactive=True,
                text_is_final=True,
            )
            self._processes[session_id] = process
    
            from nimbus.core.logging import get_logger
            logger = get_logger("kernel.agentos")
            logger.info(f"♻️ Restored process {session_id} from checkpoint")

    def get_session_stats(self) -> Optional[Dict[str, Any]]:
            if self._session_mgr:
                return self._session_mgr.get_stats()
            return None

    def list_recent_sessions(self, limit: int = 20) -> List[Dict[str, Any]]:
            if self._session_mgr:
                return self._session_mgr.list_recent_sessions(limit)
            return []

    def get_session(self, session_id: str) -> "Process | None":
            return self._processes.get(session_id)

    def end_session(self, session_id: str) -> None:
            if session_id in self._processes:
                process = self._processes.pop(session_id)
                process.state = "COMPLETED"
                self._emit_event("PROC_FINISHED", session_id, {"reason": "session_ended"})
                # NimFS GC: clean up SESSION-level artifacts when session ends
                self._nimfs_gc_session(process)

