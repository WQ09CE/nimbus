import logging
import time
import asyncio
from typing import Dict, Any, List

from nimbus.core.heart import HeartModule, HeartMessage, Heart, MessagePriority
from nimbus.core.nimfs.models import MemoryCategory, MemoryScope

logger = logging.getLogger("nimbus.heart.memory_consolidator")

class MemoryConsolidatorModule(HeartModule):
    """
    Listens for session completion events.
    When a session completes, it runs a background LLM process to extract
    useful long-term memory (Memos) from the session's final context/summary,
    and writes it to NimFS.
    """

    def __init__(self, llm_client: Any):
        super().__init__()
        self._llm = llm_client

    async def run_cron(self, heart: Heart):
        """No periodic action needed for now."""
        pass

    async def handle_message(self, heart: Heart, msg: HeartMessage):
        payload = msg.payload
        if payload is None:
            return

        # We listen for session completion signals (e.g., session.status payload status=COMPLETED)
        # or AgentOS emitting "session.completed" or "session.success"
        if msg.topic in ("session.completed", "session.success", "session.archived"):
            await self._consolidate_memory(heart, payload)
        elif msg.topic == "session.status":
            if isinstance(payload, dict) and payload.get("status") in ("COMPLETED", "SUCCEEDED", "ARCHIVED"):
                await self._consolidate_memory(heart, payload)

    async def _consolidate_memory(self, heart: Heart, payload: Dict[str, Any]):
        session_id = payload.get("session_id", "unknown_session")
        logger.info(f"[MemoryConsolidator] Analyzing session {session_id} for LTM consolidation...")

        # Find the global summary or latest context for this session
        # We assume the payload might contain 'summary' or we can fetch it via NimFS
        summary = payload.get("summary", "")
        if not summary:
            logger.debug(f"[MemoryConsolidator] No summary found in payload for session {session_id}. Attempting to fetch from DB if possible.")
            # If we don't have the summary in the payload, we might need a reference to the DB
            # For now, we expect the emitter to provide the summary.
            return

        # Simple structured extraction prompt
        prompt = f"""
Analyze the following session execution summary.
Your goal is to extract ANY reusable, cross-session knowledge.
Examples of reusable knowledge:
- A new architectural pattern established
- A critical bug and its root cause/fix
- User preferences (e.g. "always use React 18")

If you find something worth remembering, output it in JSON format.
If nothing is worth remembering, output an empty JSON array: []

JSON Schema:
[
  {{
    "title": "Short descriptive title",
    "content": "Detailed markdown content of the memo",
    "tags": ["tag1", "tag2"],
    "category": "PATTERNS"  // "PATTERNS", "EVENTS", "CASES", "PROFILE", "ENTITIES"
  }}
]

Session Summary:
{summary}
"""

        try:
            # We use the generic chat interface of the LLM wrapper
            response = await self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                tools=None
            )
            
            content = response.content
            if not content:
                return
                
            # Attempt to parse JSON from the response
            # A robust implementation would use a JSON extractor, but for now we try a basic fast parse
            import json
            import re
            
            match = re.search(r'\[\s*\{.*\}\s*\]', content, re.DOTALL)
            if match:
                memos = json.loads(match.group(0))
                for memo in memos:
                    try:
                        category_str = memo.get("category", "PATTERNS")
                        try:
                            category = MemoryCategory(category_str)
                        except:
                            category = MemoryCategory.PATTERNS

                        heart.nimfs.write_memory(
                            category=category,
                            title=memo.get("title", f"Auto-Memo: {session_id}"),
                            content=memo.get("content", ""),
                            summary=memo.get("content", "")[:180],
                            source="Auto-Consolidator",
                            tags=memo.get("tags", ["auto-generated"]),
                            scope=MemoryScope.PROJECT
                        )
                        logger.info(f"[MemoryConsolidator] Successfully saved Memo: {memo.get('title')}")
                    except Exception as e:
                        logger.error(f"[MemoryConsolidator] Failed to parse/save individual memo: {e}")
            else:
                logger.debug(f"[MemoryConsolidator] No valid JSON array found in LLM output. Output was: {content}")

        except Exception as e:
            logger.error(f"[MemoryConsolidator] LLM request failed for session {session_id}: {e}")
