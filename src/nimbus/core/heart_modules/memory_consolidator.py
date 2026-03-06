import json
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

        # Fetch existing memories to allow deduplication (top recent memories across key categories)
        existing_memories = []
        try:
            # We fetch recent memories to let the LLM see what's already known
            # and decide whether to UPDATE an existing one or create a NEW one.
            recent = heart.nimfs.search_memory(query="*", top_k=20)
            for m in recent:
                if m.category in (MemoryCategory.PATTERNS, MemoryCategory.CASES, MemoryCategory.ENTITIES, MemoryCategory.EVENTS):
                    # provide memory_id, title, and the L0 abstract
                    existing_memories.append({
                        "memory_id": m.memory_id,
                        "category": m.category.value,
                        "title": m.title,
                        "abstract": m.abstract
                    })
        except Exception as e:
            logger.debug(f"[MemoryConsolidator] Failed to fetch existing memories for dedup: {e}")

        existing_memories_str = json.dumps(existing_memories, indent=2) if existing_memories else "[]"

        # Structured extraction prompt enforcing analytical breakdown and deduplication
        prompt = f"""
Analyze the following session execution summary.
Your goal is to extract ANY reusable, cross-session knowledge.
Examples of reusable knowledge:
- A new architectural pattern established
- A critical bug and its root cause/fix
- User preferences (e.g. "always use React 18")
- Important API or interface contracts

CRITICAL DEDUPLICATION RULE:
We do not want duplicate memories. I have provided a list of `Existing Memories` below. 
If the knowledge you want to extract is a continuation, update, or already covered by an existing memory, you MUST set "action" to "UPDATE" and provide its "target_id". 
If it is entirely new knowledge, set "action" to "NEW" and "target_id" to null.

If you find something worth remembering, output it in JSON format.
If nothing is worth remembering, output an empty JSON array: []

JSON Schema:
[
  {{
    "action": "NEW", // or "UPDATE"
    "target_id": null, // or the memory_id (e.g., "patterns-abc123") if action is "UPDATE"
    "title": "Short descriptive title",
    "problem_statement": "What was the core issue, bug, or user requirement discussed? (Be concise)",
    "solution_decision": "What was the exact solution implemented, or convention decided?",
    "context_rationale": "Why was this decided? Include any key code snippets, file paths, or side-effects.",
    "tags": ["tag1", "tag2"],
    "category": "PATTERNS"  // "PATTERNS", "EVENTS", "CASES", "PROFILE", "ENTITIES"
  }}
]

---
Existing Memories (For Deduplication):
{existing_memories_str}

---
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

                        action = memo.get("action", "NEW")
                        target_id = memo.get("target_id", None)

                        # Compose high-quality markdown from structured fields
                        title = memo.get("title", f"Auto-Memo: {session_id}")
                        problem = memo.get("problem_statement", "")
                        solution = memo.get("solution_decision", "")
                        context = memo.get("context_rationale", "")
                        
                        markdown_content = (
                            f"## 1. Problem / Context\n{problem}\n\n"
                            f"## 2. Decision / Solution\n{solution}\n\n"
                            f"## 3. Rationale / Artifacts\n{context}\n"
                        )

                        memory_id = heart.nimfs.write_memory(
                            category=category,
                            title=title,
                            content=markdown_content,
                            summary=problem[:180] if problem else "No summary available",
                            source="Auto-Consolidator",
                            tags=memo.get("tags", ["auto-generated"]),
                            scope=MemoryScope.PROJECT,
                            memory_id=target_id if action == "UPDATE" and target_id else None
                        )
                        action_str = "Merged/Updated" if action == "UPDATE" else "Created"
                        logger.info(f"[MemoryConsolidator] Successfully {action_str} structured Memo: {title} ({memory_id})")
                    except Exception as e:
                        logger.error(f"[MemoryConsolidator] Failed to parse/save individual memo: {e}")
            else:
                logger.debug(f"[MemoryConsolidator] No valid JSON array found in LLM output. Output was: {content}")

        except Exception as e:
            logger.error(f"[MemoryConsolidator] LLM request failed for session {session_id}: {e}")
