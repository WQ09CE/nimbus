import asyncio

class ReadLockContext:
    def __init__(self, rwlock):
        self.rwlock = rwlock
    async def __aenter__(self):
        await self.rwlock.acquire_read()
    async def __aexit__(self, exc_type, exc, tb):
        await self.rwlock.release_read()

class WriteLockContext:
    def __init__(self, rwlock):
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

async def test():
    lock = AsyncRWLock()
    async with lock.read_lock():
        print("Read lock acquired")
    async with lock.write_lock():
        print("Write lock acquired")

asyncio.run(test())
import asyncio
from typing import Optional

class ReadLockContext:
    def __init__(self, rwlock):
        self.rwlock = rwlock
    async def __aenter__(self):
        await self.rwlock.acquire_read()
    async def __aexit__(self, exc_type, exc, tb):
        await self.rwlock.release_read()

class WriteLockContext:
    def __init__(self, rwlock):
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
