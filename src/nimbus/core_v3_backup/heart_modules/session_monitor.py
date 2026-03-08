import logging
import time
from typing import Dict, Any, List
from collections import defaultdict

from nimbus.core.heart import HeartModule, HeartMessage, Heart, MessagePriority
from nimbus.core.heart_modules.evolution import EvolutionProposal
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope

logger = logging.getLogger("nimbus.heart.session_monitor")

class SessionMonitorModule(HeartModule):
    """
    Monitors session errors and iteration rates.
    Triggers URGENT intervention if abnormal patterns (e.g. infinite loops) detected.
    """

    def __init__(self, error_threshold: int = 3, rate_limit_window: float = 10.0, rate_limit_count: int = 5):
        super().__init__()
        self.error_threshold = error_threshold
        self.rate_limit_window = rate_limit_window # seconds
        self.rate_limit_count = rate_limit_count
        
        # session_id -> list of error messages
        self.session_errors: Dict[str, List[HeartMessage]] = defaultdict(list)
        
        # session_id -> list of iteration timestamps
        self.iteration_history: Dict[str, List[float]] = defaultdict(list)

    async def run_cron(self, heart: Heart):
        """Periodic cleanup of old iteration history."""
        now = time.time()
        for sid in list(self.iteration_history.keys()):
            self.iteration_history[sid] = [t for t in self.iteration_history[sid] if now - t < self.rate_limit_window]
            if not self.iteration_history[sid]:
                del self.iteration_history[sid]

    async def handle_message(self, heart: Heart, msg: HeartMessage):
        payload = msg.payload
        if payload is None:
            logger.warning(f"[SessionMonitor] Received message {msg.topic} with null payload")
            return

        # Only process session-related topics
        if msg.topic not in ("session.error", "session.timeout", "session.failure", "session.iteration"):
            return

        # Ensure payload is a dictionary for consistent access for session topics
        if not hasattr(payload, "get"):
            # Attempt to convert Pydantic models to dict if needed, or just fail cleanly
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump()
            elif hasattr(payload, "dict"):
                payload = payload.dict()
            else:
                logger.error(f"[SessionMonitor] Expected dict payload for {msg.topic}, got {type(payload)}")
                return

        session_id = payload.get("session_id", "unknown_session")

        if msg.topic in ("session.error", "session.timeout", "session.failure"):
            await self._handle_session_error(heart, session_id, msg)

        elif msg.topic == "session.iteration":
            await self._handle_iteration(heart, session_id, payload)

    async def _handle_iteration(self, heart: Heart, session_id: str, payload: Dict[str, Any]):
        now = time.time()
        history = self.iteration_history[session_id]
        history.append(now)
        
        # Keep only within window
        self.iteration_history[session_id] = [t for t in history if now - t < self.rate_limit_window]
        
        count = len(self.iteration_history[session_id])
        has_output = payload.get("has_output", False)
        
        if count >= self.rate_limit_count and not has_output:
            logger.error(f"[SessionMonitor] CIRCUIT BREAKER: Session {session_id} iterated {count} times in {self.rate_limit_window}s with NO OUTPUT!")
            
            # 1. Trigger URGENT intervention (to AgentOS via Outbox)
            await heart.outbox.put(
                HeartMessage(
                    id=f"intv-{int(time.time()*1000)}",
                    topic="system.intervention",
                    payload={
                        "type": "RATE_LIMIT_EXCEEDED",
                        "session_id": session_id,
                        "iterations": count,
                        "window": self.rate_limit_window,
                        "action": "terminate_or_perturb"
                    },
                    priority=MessagePriority.URGENT
                )
            )
            
            # 2. Propose parameter perturbation (to Heart internally via Inbox)
            proposal = EvolutionProposal(
                title=f"Stall Detection: {session_id}",
                description="Detected high-frequency iterations without output. Suggest increasing temperature.",
                data={
                    "session_id": session_id,
                    "suggestion": "increase_perturbation",
                    "params": {"temperature": 0.9, "top_p": 0.95}
                }
            )
            await heart.inbox.put(topic="evolution.propose", payload=proposal)
            
            # Reset history to prevent double trigger
            self.iteration_history[session_id].clear()

    async def _handle_session_error(self, heart: Heart, session_id: str, msg: HeartMessage):
        self.session_errors[session_id].append(msg)
        error_count = len(self.session_errors[session_id])
        
        logger.warning(
            f"[SessionMonitor] Session '{session_id}' reported '{msg.topic}'. "
            f"Total errors: {error_count}"
        )

        # Trigger escalation before circuit breaker (e.g. at 2 errors)
        if error_count == 2:
            logger.info(f"[SessionMonitor] Requesting model ESCALATION for session {session_id}")
            await heart.outbox.put(
                HeartMessage(
                    id=f"esc-{int(time.time()*1000)}",
                    topic="system.escalate",
                    payload={
                        "session_id": session_id,
                        "error_count": error_count,
                        "reason": f"Recurrent errors ({error_count})"
                    },
                    priority=MessagePriority.HIGH
                )
            )

        if error_count >= self.error_threshold:
            await self._generate_alert(heart, session_id, error_count)

    async def _generate_alert(self, heart: Heart, session_id: str, count: int) -> None:
        errors = self.session_errors[session_id]
        error_logs = "\n".join([f"- [{msg.topic}]: {msg.payload}" for msg in errors])
        content = f"Session '{session_id}' failed {count} times.\n\nErrors:\n{error_logs}"

        try:
            await heart.nimfs.write_memory_async(
                category=MemoryCategory.CASES,
                title=f"Session Failure: {session_id}",
                content=content,
                summary=f"Auto-generated failure log for session {session_id} after {count} errors.",
                tags=["failure", "auto-generated"],
                scope=MemoryScope.PROJECT
            )
        except Exception as e:
            logger.error(f"Failed to persist session error to NimFS Memory: {e}")

        proposal = EvolutionProposal(
            title=f"Analyze failures for session {session_id}",
            description=f"Session reached error threshold ({count}). Check for recurrent patterns.",
            data={"session_id": session_id, "error_count": count, "logs": error_logs}
        )

        await asyncio.to_thread(heart.inbox.put, topic="evolution.propose", payload=proposal)
        self.session_errors[session_id].clear()
