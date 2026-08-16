import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .session_log import (
    SessionLog,
    derive_state,
    grade_unanswered_calls,
    interrupted_turn_closers,
)

logger = logging.getLogger("nimbus.core.storage")

class SessionStorage:
    """Minimalist file-based storage for Agent processes (Core Dumps).

    All session state is stored as JSON files in ~/.nimbus/sessions/.
    """

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        elif env_dir := os.environ.get("NIMBUS_SESSIONS_DIR"):
            # Overrides the default location. Set by the test suite to keep
            # test-created loops out of the real ~/.nimbus/sessions (running
            # pytest used to litter it with orphan session logs), and usable
            # in containers/deployments to relocate session storage.
            self.base_dir = Path(env_dir)
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

    def _state_from_log(
        self, session_id: str, snapshot_messages: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Derive {"messages", "summary"} from the session's event log.

        Phase 2 authority inversion: the log is the truth for the message
        surface. Returns None (→ snapshot fallback) when the log is absent,
        corrupt, or has fallen behind the snapshot (append swallows I/O
        errors, so a log can be incomplete — it may claim authority only
        when it is at least as complete as the snapshot).

        Read-only: a crashed open tail is repaired as a VIEW here; the
        closers are written to disk by SessionLog.open() when a loop
        actually resumes the session.
        """
        log_path = self.base_dir / f"{session_id}.jsonl"
        if not log_path.exists():
            return None
        try:
            log = SessionLog.load(log_path)
            events = log.events + interrupted_turn_closers(log.events)
            state = derive_state(events)
        except Exception as e:
            logger.warning(
                f"Session log for '{session_id}' unusable ({e}); using snapshot"
            )
            return None

        def _real_count(msgs: List[Dict[str, Any]]) -> int:
            return sum(
                1 for m in msgs if not (m.get("meta") or {}).get("synthetic")
            )

        if _real_count(state["messages"]) < _real_count(snapshot_messages):
            logger.warning(
                f"Session log for '{session_id}' is behind the snapshot "
                f"({_real_count(state['messages'])} < "
                f"{_real_count(snapshot_messages)} messages); using snapshot"
            )
            return None
        return state

    def fork_session(
        self,
        parent_id: str,
        new_id: str,
        at_seq: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new session seeded from a parent — the one primitive
        behind fork, resume-as-new, and replay-from-point (dsh seed).

        The parent's repaired log view (truncated to at_seq when given, with
        synthetic closers so a mid-turn cut still yields a balanced surface)
        is derived into a single seed/applied event at seq 0 of the NEW
        session's log, plus a snapshot carrying lineage in metadata. Falls
        back to the parent snapshot's messages when the parent has no usable
        log. Returns the new session's dump (via load_session), or None if
        the parent doesn't exist.
        """
        parent = self.load_session(parent_id)
        if parent is None:
            return None

        state = None
        log_path = self.base_dir / f"{parent_id}.jsonl"
        if log_path.exists():
            try:
                log = SessionLog.load(log_path)
                events = log.events if at_seq is None else log.events[:at_seq]
                events = events + interrupted_turn_closers(events)
                state = derive_state(events)
            except Exception as e:
                logger.warning(
                    f"fork_session: parent log unusable ({e}); seeding from snapshot"
                )
        if state is None:
            state = {
                "messages": parent.get("messages", []),
                "summary": parent.get("metadata", {})
                .get("mmu_state", {})
                .get("global_summary", ""),
            }

        lineage = {
            "parent": parent_id,
            "at_seq": at_seq,
            "forked_at": datetime.now().isoformat(),
        }

        # Seed is the new log's first event; a stale log at new_id must not
        # be merged into, so it is replaced outright.
        new_log_path = self.base_dir / f"{new_id}.jsonl"
        if new_log_path.exists():
            new_log_path.unlink()
        new_log = SessionLog(new_log_path)
        new_log.append("seed/applied", {
            "messages": state["messages"],
            "summary": state["summary"],
            "plan": state.get("plan", ""),
            "lineage": lineage,
        })

        metadata = dict(parent.get("metadata", {}))
        mmu_state = dict(metadata.get("mmu_state", {}))
        mmu_state["global_summary"] = state["summary"]
        mmu_state["plan"] = state.get("plan", "")
        metadata["mmu_state"] = mmu_state
        metadata["lineage"] = lineage

        self.save_session(
            session_id=new_id,
            status="forked",
            messages=state["messages"],
            vcpu_state={},
            vcpu_config=parent.get("vcpu_config", {}),
            llm_config=parent.get("llm_config", {}),
            metadata=metadata,
        )
        return self.load_session(new_id)

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a session Core Dump. The event log, when present and complete,
        is the authoritative source for messages + summary (Phase 2); the
        snapshot supplies metadata/vcpu_state and is the messages fallback."""
        path = self._get_path(session_id)
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                dump = json.load(f)

            messages = dump.get("messages", [])

            log_state = self._state_from_log(session_id, messages)
            if log_state is not None:
                messages = log_state["messages"]
                dump["messages"] = messages
                # Summary + plan flow to the restore path via metadata.mmu_state.
                mmu_state = dump.setdefault("metadata", {}).setdefault("mmu_state", {})
                mmu_state["global_summary"] = log_state["summary"]
                mmu_state["plan"] = log_state.get("plan", "")

            # --- Syscall Interruption Recovery ---
            # A crash can leave the latest assistant tool_calls batch with some
            # or all results missing (the LLM API requires exactly N results
            # for N calls). Scan back to the latest batch — not just the last
            # message — so partially-answered batches are repaired too. Grades
            # (serial gate execution): first unanswered call may have been in
            # flight (TOOL_OUTCOME_UNKNOWN — verify before retrying); later
            # ones provably never started (TOOL_NOT_STARTED — safe to retry).
            idx = None
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
                    idx = i
                    break
            if idx is not None:
                answered = {
                    m.get("tool_call_id")
                    for m in messages[idx + 1:]
                    if m.get("role") == "tool"
                }
                graded = grade_unanswered_calls(messages[idx]["tool_calls"], answered)
                for tc, code, text in graded:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", "unknown"),
                        "name": tc.get("function", {}).get("name", "unknown_tool"),
                        "content": text,
                        "meta": {"synthetic": True, "code": code},
                    })
                if graded:
                    dump["messages"] = messages
                    logger.info(
                        f"Injected {len(graded)} graded recovery message(s) "
                        f"for interrupted session {session_id}"
                    )

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
