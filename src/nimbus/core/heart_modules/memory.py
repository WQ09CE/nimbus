from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Set, Optional
from nimbus.core.heart import HeartModule, HeartMessage, MessagePriority

if TYPE_CHECKING:
    from nimbus.core.heart import Heart

logger = logging.getLogger("nimbus.heart.memory")

class ReadLockContext:
    def __init__(self, rwlock: AsyncRWLock):
        self.rwlock = rwlock
    async def __aenter__(self):
        await self.rwlock.acquire_read()
    async def __aexit__(self, exc_type, exc, tb):
        await self.rwlock.release_read()

class WriteLockContext:
    def __init__(self, rwlock: AsyncRWLock):
        self.rwlock = rwlock
    async def __aenter__(self):
        await self.rwlock.acquire_write()
    async def __aexit__(self, exc_type, exc, tb):
        await self.rwlock.release_write()

class AsyncRWLock:
    def __init__(self):
        self._read_ready = asyncio.Condition()
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False

    async def acquire_read(self):
        async with self._read_ready:
            while self._writer_active or self._writers_waiting > 0:
                await self._read_ready.wait()
            self._readers += 1

    async def release_read(self):
        async with self._read_ready:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    async def acquire_write(self):
        async with self._read_ready:
            self._writers_waiting += 1
            while self._readers > 0 or self._writer_active:
                await self._read_ready.wait()
            self._writers_waiting -= 1
            self._writer_active = True

    async def release_write(self):
        async with self._read_ready:
            self._writer_active = False
            self._read_ready.notify_all()

    def read_lock(self):
        return ReadLockContext(self)

    def write_lock(self):
        return WriteLockContext(self)

class MemoryManagerModule(HeartModule):
    """
    Heart module for NimFS Memory management:
    - Incremental consolidation (using Dirty Bits)
    - Background GC
    - Embedding updates
    - Concurrency control via AsyncRWLock
    """
    def __init__(self, gc_interval_ticks: int = 60):
        self.gc_interval_ticks = gc_interval_ticks
        self.ticks_count = 0
        self.dirty_memories: Set[str] = set()
        self.rwlock = AsyncRWLock()
        self.brain_state = "idle"

    async def run_cron(self, heart: Heart):
        self.ticks_count += 1
        
        # 1. Process Dirty Bits
        if heart.dirty_bits:
            await self._process_dirty_memories(heart)
            
        # 2. Occasional GC
        if self.ticks_count % self.gc_interval_ticks == 0:
            await self._run_gc(heart)

    async def _process_dirty_memories(self, heart: Heart):
        # Collect keys that are dirty
        targets = [k for k, v in heart.dirty_bits.items() if v and k.startswith("mem:")]
        if not targets:
            return
            
        # Only consolidate if Brain is idle or we hold a write lock
        if self.brain_state != "idle":
            logger.debug("Brain is running, deferring consolidation.")
            return

        logger.info(f"Processing {len(targets)} dirty memory entries")
        async with self.rwlock.write_lock():
            for key in targets:
                # Placeholder for actual logic:
                # - Read memory from NimFS
                # - Generate L1/L2 summaries if missing
                # - Update vector store
                logger.debug(f"Consolidating memory: {key}")
                heart.clear_dirty(key)

    async def _run_gc(self, heart: Heart):
        if self.brain_state != "idle":
            logger.debug("Brain is running, deferring GC.")
            return

        async with self.rwlock.write_lock():
            logger.info("Running NimFS Artifact GC...")
            # heart.nimfs.defrag() or similar
            pass

    async def handle_message(self, heart: Heart, msg: HeartMessage):
        if msg.topic == "brain.state_change":
            state = msg.payload.get("state")
            if state:
                self.brain_state = state
                logger.debug(f"Brain state changed to: {self.brain_state}")

        elif msg.topic == "memory.mark_dirty":
            mem_id = msg.payload.get("id")
            if mem_id:
                heart.mark_dirty(f"mem:{mem_id}")
        
        elif msg.topic == "memory.compact_request":
            # Brain explicitly requested a compaction
            logger.info(f"Manual compaction requested for: {msg.payload}")
            async with self.rwlock.write_lock():
                # Logic to trigger LLM compaction
                pass
