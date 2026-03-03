from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Set, Optional, Dict
from nimbus.core.heart import HeartModule, HeartMessage, MessagePriority

if TYPE_CHECKING:
    from nimbus.core.heart import Heart

logger = logging.getLogger("nimbus.heart.memory")

class ReadLockContext:
    def __init__(self, rwlock: AsyncRWLock, timeout: Optional[float] = None):
        self.rwlock = rwlock
        self.timeout = timeout
    async def __aenter__(self):
        await self.rwlock.acquire_read(timeout=self.timeout)
    async def __aexit__(self, exc_type, exc, tb):
        await self.rwlock.release_read()

class WriteLockContext:
    def __init__(self, rwlock: AsyncRWLock, timeout: Optional[float] = None):
        self.rwlock = rwlock
        self.timeout = timeout
    async def __aenter__(self):
        await self.rwlock.acquire_write(timeout=self.timeout)
    async def __aexit__(self, exc_type, exc, tb):
        await self.rwlock.release_write()

class AsyncRWLock:
    def __init__(self):
        self._read_ready = asyncio.Condition()
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False
        self._owner_info: Dict[str, float] = {} # For debugging/watchdog: type -> start_time

    async def acquire_read(self, timeout: Optional[float] = None):
        start_time = time.time()
        async with self._read_ready:
            while self._writer_active or self._writers_waiting > 0:
                if timeout is not None and (time.time() - start_time) > timeout:
                    raise asyncio.TimeoutError("Read lock acquisition timed out")
                try:
                    wait_time = timeout - (time.time() - start_time) if timeout else None
                    if wait_time is not None and wait_time <= 0:
                        raise asyncio.TimeoutError("Read lock acquisition timed out")
                    await asyncio.wait_for(self._read_ready.wait(), timeout=wait_time)
                except asyncio.TimeoutError:
                    raise asyncio.TimeoutError("Read lock acquisition timed out")

            self._readers += 1
            if self._readers == 1:
                self._owner_info["read"] = time.time()

    async def release_read(self):
        async with self._read_ready:
            if self._readers > 0:
                self._readers -= 1
                if self._readers == 0:
                    self._owner_info.pop("read", None)
                    self._read_ready.notify_all()

    async def acquire_write(self, timeout: Optional[float] = None):
        start_time = time.time()
        async with self._read_ready:
            self._writers_waiting += 1
            try:
                while self._readers > 0 or self._writer_active:
                    if timeout is not None and (time.time() - start_time) > timeout:
                        raise asyncio.TimeoutError("Write lock acquisition timed out")
                    
                    wait_time = timeout - (time.time() - start_time) if timeout else None
                    if wait_time is not None and wait_time <= 0:
                        raise asyncio.TimeoutError("Write lock acquisition timed out")
                    
                    try:
                        await asyncio.wait_for(self._read_ready.wait(), timeout=wait_time)
                    except asyncio.TimeoutError:
                        raise asyncio.TimeoutError("Write lock acquisition timed out")
                
                self._writer_active = True
                self._owner_info["write"] = time.time()
            finally:
                self._writers_waiting -= 1

    async def release_write(self):
        async with self._read_ready:
            self._writer_active = False
            self._owner_info.pop("write", None)
            self._read_ready.notify_all()

    def force_release(self):
        """Forcefully release all locks to break deadlocks."""
        self._readers = 0
        self._writer_active = False
        self._owner_info.clear()
        # Note: we can't easily notify a specific condition if we don't hold it,
        # but calling this usually happens from a watchdog. 
        # We'll assume the caller knows what they are doing.
        logger.warning("AsyncRWLock: FORCE RELEASE triggered!")

    def read_lock(self, timeout: Optional[float] = None):
        return ReadLockContext(self, timeout=timeout)

    def write_lock(self, timeout: Optional[float] = None):
        return WriteLockContext(self, timeout=timeout)

    def get_lock_duration(self) -> float:
        """Returns the longest duration a lock has been held."""
        if not self._owner_info:
            return 0.0
        return time.time() - min(self._owner_info.values())

class MemoryManagerModule(HeartModule):
    """
    Heart module for NimFS Memory management:
    - Incremental consolidation (using Dirty Bits)
    - Background GC
    - Concurrency control via AsyncRWLock with Watchdog
    """
    def __init__(self, llm_client: Optional[Any] = None, gc_interval_ticks: int = 60, lock_timeout: float = 30.0):
        self.llm_client = llm_client
        self.gc_interval_ticks = gc_interval_ticks
        self.lock_timeout = lock_timeout
        self.ticks_count = 0
        self.dirty_memories: Set[str] = set()
        self.rwlock = AsyncRWLock()
        self.brain_state = "idle"

    async def run_cron(self, heart: Heart):
        self.ticks_count += 1
        
        # Lock Watchdog
        duration = self.rwlock.get_lock_duration()
        if duration > self.lock_timeout:
            logger.error(f"Lock held for {duration:.1f}s (threshold {self.lock_timeout}s). FORCING RELEASE.")
            self.rwlock.force_release()
            await heart.outbox.put(
                HeartMessage(
                    id=f"intv-{int(time.time()*1000)}",
                    topic="system.intervention",
                    payload={
                        "type": "LOCK_WATCHDOG",
                        "reason": f"Lock timeout after {duration:.1f}s",
                        "action": "force_release"
                    },
                    priority=MessagePriority.URGENT
                )
            )

        # 1. Process Dirty Bits
        if heart.dirty_bits:
            await self._process_dirty_memories(heart)
            
        # 2. Occasional GC
        if self.ticks_count % self.gc_interval_ticks == 0:
            await self._run_gc(heart)

    async def _process_dirty_memories(self, heart: Heart):
        targets = [k for k, v in heart.dirty_bits.items() if v and k.startswith("mem:")]
        if not targets:
            return
            
        if self.brain_state != "idle":
            return

        try:
            async with self.rwlock.write_lock(timeout=5.0):
                for key in targets:
                    logger.debug(f"Consolidating memory: {key}")
                    heart.clear_dirty(key)
        except asyncio.TimeoutError:
            logger.warning("MemoryManager: Could not acquire write lock for consolidation, skipping.")

    async def _run_gc(self, heart: Heart):
        if self.brain_state != "idle":
            return

        if not self.llm_client:
            logger.debug("MemoryManager: No LLM client attached, skipping reflection GC.")
            return

        try:
            async with self.rwlock.write_lock(timeout=10.0):
                logger.info("Running Agent Episodic Reflection & Profile Compression (GC)...")
                
                from nimbus.core.memory.episodic_store import EpisodicStore
                from nimbus.core.memory.profile_store import ProfileStore
                from nimbus.core.memory.procedural_store import ProceduralStore
                from nimbus.core.memory.profile_schema import ProfileEntityModel
                from nimbus.core.memory.strategy_schema import StrategyModel
                from pathlib import Path
                import json
                
                workspace = Path(heart.config.workspace)
                episodic = EpisodicStore(workspace)
                profile = ProfileStore(workspace)
                procedural = ProceduralStore(workspace)
                
                # Fetch recent unreflected episodes (dummy logic for MVP: just get latest session snippets)
                recent_logs = episodic.search("", limit=30)
                if not recent_logs:
                    return
                
                prompt = (
                    "You are a background memory processor. Review the following recent interaction logs "
                    "between an AI Agent and a User. Extract two types of information:\n"
                    "1. Important LONG-TERM facts about the user's preferences, project architecture, or technical decisions.\n"
                    "2. Reusable PROCEDURAL STRATEGIES if the agent successfully completed a complex sequence of actions, solved an error, or formed a reliable routine.\n\n"
                    "Respond EXACTLY with a JSON object containing two lists: 'entities' and 'strategies'.\n"
                    "For 'entities', each MUST have:\n"
                    "- 'key': A short slugname for the fact (e.g. 'frontend_framework')\n"
                    "- 'value': The value of the fact\n"
                    "- 'entity_type': One of ['preference', 'tech_stack', 'project_context', 'decision', 'other']\n\n"
                    "For 'strategies', each MUST have:\n"
                    "- 'condition': The trigger situation, goal, or error (e.g., 'When hitting 502 Bad Gateway')\n"
                    "- 'action': The successful sequence of steps or resolution taken.\n\n"
                    "If nothing important is found, return {\"entities\": [], \"strategies\": []}.\n\nLogs:\n"
                )
                for log in recent_logs:
                    prompt += f"[{log.get('role', 'unknown')}]: {log.get('snippet', '')}\n"

                def _call_llm():
                    return self.llm_client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "user", "content": prompt}],
                        response_format={"type": "json_object"}
                    )
                
                response = await asyncio.to_thread(_call_llm)
                
                try:
                    content = response.choices[0].message.content
                    data = json.loads(content)
                    entities = data.get("entities", [])
                    for e in data.get("entities", []):
                        model = ProfileEntityModel(
                            key=e["key"],
                            value=e["value"],
                            entity_type=e.get("entity_type", "other"),
                            confidence="inferred"
                        )
                        profile.upsert(model)
                        logger.info(f"Background GC extracted new Semantic Fact: {e['key']} -> {e['value']}")
                        
                    for s in data.get("strategies", []):
                        strat = StrategyModel(
                            condition=s["condition"],
                            action=s["action"]
                        )
                        procedural.upsert(strat)
                        logger.info(f"Background GC extracted new Procedural Strategy: {strat.id}")
                except Exception as ex:
                    logger.error(f"Failed to parse LLM reflection output: {ex}")
                
        except asyncio.TimeoutError:
            logger.warning("MemoryManager: Could not acquire write lock for GC, skipping.")

    async def handle_message(self, heart: Heart, msg: HeartMessage):
        if msg.topic == "brain.state_change":
            state = msg.payload.get("state")
            if state:
                self.brain_state = state
        elif msg.topic == "memory.mark_dirty":
            mem_id = msg.payload.get("id")
            if mem_id:
                heart.mark_dirty(f"mem:{mem_id}")
