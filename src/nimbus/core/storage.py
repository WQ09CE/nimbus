import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("nimbus.core.storage")

class SessionStorage:
    """Minimalist file-based storage for Agent processes (Core Dumps).

    All session state is stored as JSON files in ~/.nimbus/sessions/.
    """

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path.home() / ".nimbus" / "sessions"
        
        # Ensure the directory exists
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
    def _get_path(self, session_id: str) -> Path:
        return self.base_dir / f"{session_id}.json"

    def save_session(
        self,
        session_id: str,
        status: str,
        messages: List[Dict[str, Any]],
        vcpu_state: Dict[str, Any],
        vcpu_config: Optional[Dict[str, Any]] = None,
        llm_config: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Serialize complete process state into a JSON Core Dump."""
        path = self._get_path(session_id)
        
        dump = {
            "session_id": session_id,
            "status": status,
            "updated_at": datetime.now().isoformat(),
            "messages": messages,
            "vcpu_state": vcpu_state,
            "vcpu_config": vcpu_config or {},
            "llm_config": llm_config or {},
            "metadata": metadata or {},
        }
        
        # Use a temporary file for atomic write
        temp_path = path.with_suffix(".json.tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(dump, f, indent=2, ensure_ascii=False)
            temp_path.replace(path)
            logger.debug(f"Saved session '{session_id}' to {path}")
        except Exception as e:
            logger.error(f"Failed to save session '{session_id}': {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a session JSON Core Dump, injecting recovery messages if needed."""
        path = self._get_path(session_id)
        if not path.exists():
            return None
            
        try:
            with open(path, "r", encoding="utf-8") as f:
                dump = json.load(f)
                
            messages = dump.get("messages", [])
            
            # --- Syscall Interruption Recovery ---
            # If the last message is from the assistant and contains tool_calls,
            # but there are no corresponding tool results following it, it means
            # the process was interrupted during an in-flight syscall.
            # The LLM API requires exactly N tool results for N tool calls.
            if messages:
                last_msg = messages[-1]
                if last_msg.get("role") == "assistant" and "tool_calls" in last_msg:
                    tool_calls = last_msg["tool_calls"]
                    if tool_calls:
                        # Inject one recovery message per tool call
                        for tc in tool_calls:
                            tc_id = tc.get("id", "unknown")
                            tc_name = tc.get("function", {}).get("name", "unknown_tool")
                            recovery_msg = {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "name": tc_name,
                                "content": "[System Alert]: Execution was interrupted by process suspension or crash before completion. State unknown. Please verify the environment via view_file or other tools before proceeding.",
                            }
                            messages.append(recovery_msg)
                        dump["messages"] = messages
                        logger.info(f"Injected {len(tool_calls)} synthetic recovery message(s) for interrupted session {session_id}")

            return dump
        except Exception as e:
            logger.error(f"Failed to load session '{session_id}': {e}")
            return None

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List metadata for all stored process dumps without loading full messages.

        Only returns sessions with 'sess_' prefix (current format).
        Legacy v3 sessions (bare UUIDs) are ignored.
        """
        sessions = []
        for path in self.base_dir.glob("sess_*.json"):
            if path.is_file():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    sessions.append({
                        "session_id": data.get("session_id", path.stem),
                        "status": data.get("status", "unknown"),
                        "updated_at": data.get("updated_at", ""),
                        "vcpu_config": data.get("vcpu_config", {}),
                        "llm_config": data.get("llm_config", {}),
                        "metadata": data.get("metadata", {}),
                    })
                except Exception as e:
                    logger.warning(f"Could not read session file {path}: {e}")

        # Sort by most recently updated
        sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return sessions
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session Core Dump."""
        path = self._get_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False
