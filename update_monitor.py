import sys

filepath = "src/nimbus/core/heart_modules/session_monitor.py"
with open(filepath, "r") as f:
    code = f.read()

# imports
imports_to_add = """from nimbus.core.heart_modules.evolution import EvolutionProposal
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope
"""

code = code.replace("from nimbus.core.heart import HeartModule, HeartMessage, Heart\n", "from nimbus.core.heart import HeartModule, HeartMessage, Heart\n" + imports_to_add)

# change _generate_alert call
code = code.replace("self._generate_alert(session_id, error_count)", "await self._generate_alert(heart, session_id, error_count)")

# replace _generate_alert definition
old_alert = """    def _generate_alert(self, session_id: str, count: int) -> None:
        \"\"\"Generate an alert or evolution proposal.\"\"\"
        logger.error(
            f"[SessionMonitor ALERT] Session '{session_id}' has reached {count} errors! "
            "Consider generating an evolution proposal or adjusting parameters."
        )
        self.session_errors[session_id].clear()
"""

new_alert = """    async def _generate_alert(self, heart: Heart, session_id: str, count: int) -> None:
        \"\"\"Generate an alert, persist to NimFS Memory, and send an evolution proposal.\"\"\"
        errors = self.session_errors[session_id]
        logger.error(
            f"[SessionMonitor ALERT] Session '{session_id}' has reached {count} errors! "
            "Persisting to NimFS Memory and proposing evolution."
        )

        error_logs = "\\n".join([f"- [{msg.topic}]: {msg.payload}" for msg in errors])
        content = f"Session '{session_id}' failed {count} times.\\n\\nErrors:\\n{error_logs}"

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
"""

code = code.replace(old_alert, new_alert)

with open(filepath, "w") as f:
    f.write(code)

