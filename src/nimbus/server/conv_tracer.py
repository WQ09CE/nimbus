"""Orchestrator conversation tracer — writes each user↔AI turn to NimFS."""
import json
import logging
from datetime import datetime, timezone

from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import ArtifactTTL

logger = logging.getLogger(__name__)


class ConversationTracer:
    """
    Writes each user↔AI conversation turn as an immutable NimFS artifact.

    All turns for a session are grouped under task_id = f"conv-{session_id}",
    enabling full-chain retrieval via:
        nimfs.list_artifacts(task_id=f"conv-{session_id}")

    TTL defaults to SESSION so artifacts are cleaned up when the session ends.
    """

    def __init__(self, session_id: str, nimfs: NimFSManager):
        self.session_id = session_id
        self.nimfs = nimfs
        self._turn_index = 0

    def record_turn(
        self,
        user_message: "str | list",
        assistant_reply: str,
        duration_ms: int = 0,
        status: str = "OK",
    ) -> str:
        """
        Write one conversation turn to NimFS. Returns the artifact ref.

        Args:
            user_message:    Raw user input (str or multipart list).
            assistant_reply: Final assistant response text.
            duration_ms:     Elapsed time for this turn in milliseconds.
            status:          "OK" | "CANCELLED" | "ERROR"

        Returns:
            nimfs://artifact/{id} reference string.
        """
        self._turn_index += 1
        ts = datetime.now(timezone.utc).isoformat()
        task_id = f"conv-{self.session_id}"

        # Normalize user message to string
        if isinstance(user_message, list):
            user_text = json.dumps(user_message, ensure_ascii=False)
        else:
            user_text = str(user_message)

        md = self._to_markdown(ts, user_text, assistant_reply, duration_ms, status)

        user_preview = user_text[:60] + ("..." if len(user_text) > 60 else "")
        summary = f"Turn {self._turn_index}: {user_preview}"

        ref = self.nimfs.write_artifact(
            content=md,
            task_id=task_id,
            producer="conv-tracer",
            artifact_type="report",
            ttl=ArtifactTTL.SESSION,
            summary=summary,
            tags=["conversation", "orchestrator", f"session-{self.session_id}"],
        )
        logger.info(
            f"[ConversationTracer] Saved turn {self._turn_index} for session "
            f"{self.session_id} [{status}] → {ref}"
        )
        return ref

    def _to_markdown(
        self,
        ts: str,
        user_text: str,
        assistant_reply: str,
        duration_ms: int,
        status: str,
    ) -> str:
        status_icon = (
            "✅" if status == "OK"
            else "⚠️" if status == "CANCELLED"
            else "❌"
        )
        return (
            f"## Turn {self._turn_index} [{ts}] {status_icon} ({duration_ms}ms)\n\n"
            f"### 👤 User\n{user_text}\n\n"
            f"### 🤖 Assistant\n{assistant_reply}\n\n"
            f"---\n"
        )
