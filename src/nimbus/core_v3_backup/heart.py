from __future__ import annotations

import asyncio
import logging
import signal
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from nimbus.core.protocol import ActionIR, ToolResult, IPCMessage
from nimbus.core.nimfs import NimFSManager

# Logger setup
logger = logging.getLogger("nimbus.heart")

class HeartState(Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"

@dataclass
class HeartConfig:
    """Configuration for the Heart daemon."""
    workspace: str
    project_id: str
    tick_interval: float = 1.0  # seconds
    cron_jobs: List[Dict[str, Any]] = field(default_factory=list)
    
class MessagePriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3

@dataclass
class HeartMessage:
    """Internal message format for the Heart Inbox."""
    id: str
    topic: str
    payload: Any
    priority: MessagePriority = MessagePriority.NORMAL
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

class HeartInbox:
    """
    Asynchronous message queue for Heart to receive commands/events from Brain.
    Supports priority and basic filtering.
    """
    def __init__(self):
        self._queue = asyncio.PriorityQueue()

    async def put(self, topic: str, payload: Any, priority: MessagePriority = MessagePriority.NORMAL):
        msg = HeartMessage(
            id=f"msg-{int(time.time()*1000)}",
            topic=topic,
            payload=payload,
            priority=priority
        )
        # PriorityQueue in Python is min-heap, so we invert priority value
        # Add tie-breaker to avoid comparing HeartMessage objects
        await self._queue.put((-priority.value, time.time_ns(), msg))
        logger.debug(f"Inbox received message: {topic} [{priority.name}]")

    async def get(self) -> HeartMessage:
        _, _, msg = await self._queue.get()
        return msg

    def empty(self) -> bool:
        return self._queue.empty()

class HeartModule(ABC):
    """Base class for Heart background modules (Health, Memory, etc.)"""
    @abstractmethod
    async def run_cron(self, heart: "Heart"):
        """Executed on every cron tick if criteria met."""
        pass

    @abstractmethod
    async def handle_message(self, heart: "Heart", msg: HeartMessage):
        """Executed when a relevant message is received."""
        pass

class Heart:
    """
    The "Heart" of Nimbus: System-level resident daemon.
    
    Implements a Hybrid Cron/Event loop:
    1. Cron: Fixed-interval maintenance tasks (Health check, Memory GC).
    2. Event: Reactive tasks triggered by Brain (via Inbox).
    """
    def __init__(self, config: HeartConfig):
        self.config = config
        self.state = HeartState.STARTING
        self.inbox = HeartInbox()
        self.outbox = asyncio.Queue() # Added for upstream intervention signals
        self.nimfs = NimFSManager(config.workspace)
        self.modules: List[HeartModule] = []
        self._stop_event = asyncio.Event()
        
        # Internal state tracking
        self.last_cron_run = 0
        self.dirty_bits: Dict[str, bool] = {} # For incremental processing

    def add_module(self, module: HeartModule):
        self.modules.append(module)

    async def start(self):
        """Start the Heart daemon loop."""
        logger.info(f"Heart starting in workspace: {self.config.workspace}")
        self.state = HeartState.RUNNING
        
        # Register signal handlers
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self.stop)
        except NotImplementedError:
            # Not supported on Windows or some environments
            pass

        try:
            while not self._stop_event.is_set():
                await self._tick()
                await asyncio.sleep(self.config.tick_interval)
        except Exception as e:
            logger.exception(f"Heart loop crashed: {e}")
        finally:
            self.state = HeartState.STOPPED
            logger.info("Heart stopped.")

    def stop(self):
        """Gracefully stop the Heart daemon."""
        logger.info("Heart stopping...")
        self.state = HeartState.STOPPING
        self._stop_event.set()

    async def _tick(self):
        """Single iteration of the heart beat."""
        # 1. Process Inbox (Events)
        while not self.inbox.empty():
            msg = await self.inbox.get()
            await self._dispatch_message(msg)
        
        # 2. Process Cron Jobs (Maintenance)
        now = time.time()
        # For now, we just run all modules' cron every tick. 
        # Future: implement actual scheduling/throttling.
        for module in self.modules:
            try:
                await module.run_cron(self)
            except Exception as e:
                logger.error(f"Module {module.__class__.__name__} cron failed: {e}")

    async def _dispatch_message(self, msg: HeartMessage):
        """Dispatch incoming message to interested modules."""
        logger.debug(f"Dispatching message: {msg.topic}")
        for module in self.modules:
            try:
                await module.handle_message(self, msg)
            except Exception as e:
                logger.error(f"Module {module.__class__.__name__} failed to handle message {msg.topic}: {e}")

    # --- Dirty Bit Helpers ---
    def mark_dirty(self, key: str):
        self.dirty_bits[key] = True

    def is_dirty(self, key: str) -> bool:
        return self.dirty_bits.get(key, False)

    def clear_dirty(self, key: str):
        self.dirty_bits[key] = False
