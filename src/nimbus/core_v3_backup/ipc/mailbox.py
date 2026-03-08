import asyncio
from typing import Optional, List
from nimbus.core.logging import get_logger
from .message import IPCMessage

logger = get_logger("nimbus.core.ipc")

class Mailbox:
    """
    An asynchronous message queue attached to an AgentOS Process.
    Sub-Agents can block on `receive()` until their Dispatcher sends a Contract.
    """
    def __init__(self, owner_pid: str):
        self.owner_pid = owner_pid
        self._queue: asyncio.Queue[IPCMessage] = asyncio.Queue()
        
    async def send(self, message: IPCMessage) -> None:
        """Enqueue a message to this mailbox."""
        logger.debug(f"[{self.owner_pid}] Inbox received message {message.id} from {message.sender_pid}")
        await self._queue.put(message)
        
    async def receive(self, timeout: Optional[float] = None) -> IPCMessage:
        """Block and wait for the next message in the mailbox."""
        if timeout:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()
        
    def peek_all(self) -> List[IPCMessage]:
        """Return all messages currently in the queue without consuming them."""
        # Note: asyncio.Queue doesn't have a direct iter, so we access internal _queue
        return list(self._queue._queue)
        
    def qsize(self) -> int:
        return self._queue.qsize()
