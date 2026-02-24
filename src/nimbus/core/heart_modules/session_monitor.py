import logging
from typing import Dict, Any, List
from collections import defaultdict

from nimbus.core.heart import HeartModule, HeartMessage, Heart
from nimbus.core.heart_modules.evolution import EvolutionProposal
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope

logger = logging.getLogger("nimbus.heart.session_monitor")

class SessionMonitorModule(HeartModule):
    """
    Monitors session errors (error, timeout, failures).
    Generates alerts or evolution proposals if errors are frequent.
    """

    def __init__(self, error_threshold: int = 3):
        super().__init__()
        self.error_threshold = error_threshold
        # session_id -> list of error messages
        self.session_errors: Dict[str, List[HeartMessage]] = defaultdict(list)

    async def run_cron(self, heart: Heart):
        """Periodic checks."""
        # For now, we only react to events.
        pass

    async def handle_message(self, heart: Heart, msg: HeartMessage):
        """Executed when a relevant message is received."""
        if msg.topic not in ("session.error", "session.timeout", "session.failure"):
            return

        payload = msg.payload or {}
        session_id = payload.get("session_id", "unknown_session")
        
        self.session_errors[session_id].append(msg)
        error_count = len(self.session_errors[session_id])
        
        logger.warning(
            f"[SessionMonitor] Session '{session_id}' reported '{msg.topic}'. "
            f"Total errors: {error_count}"
        )

        if error_count >= self.error_threshold:
            await self._generate_alert(heart, session_id, error_count)

    async def _generate_alert(self, heart: Heart, session_id: str, count: int) -> None:
        """Generate an alert, persist to NimFS Memory, and send an evolution proposal."""
        errors = self.session_errors[session_id]
        logger.error(
            f"[SessionMonitor ALERT] Session '{session_id}' has reached {count} errors! "
            "Persisting to NimFS Memory and proposing evolution."
        )

        error_logs = "\n".join([f"- [{msg.topic}]: {msg.payload}" for msg in errors])
        content = f"Session '{session_id}' failed {count} times.\n\nErrors:\n{error_logs}"

        # 1. Persist to NimFS
        try:
            heart.nimfs.write_memory(
                category=MemoryCategory.CASES,
                title=f"Session Failure: {session_id}",
                content=content,
                summary=f"Auto-generated failure log for session {session_id} after {count} errors.",
                tags=["failure", "auto-generated"],
                scope=MemoryScope.PROJECT
            )
        except Exception as e:
            logger.error(f"Failed to persist session error to NimFS Memory: {e}")

        # 2. Send evolution proposal to Inbox
        proposal = EvolutionProposal(
            title=f"Analyze failures for session {session_id}",
            description=f"Session reached error threshold ({count}). Check for recurrent patterns.",
            data={"session_id": session_id, "error_count": count, "logs": error_logs}
        )

        await heart.inbox.put(
            topic="evolution.propose",
            payload=proposal
        )

        self.session_errors[session_id].clear()
