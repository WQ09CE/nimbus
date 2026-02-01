"""Log Hub for real-time log streaming via WebSocket."""

import asyncio
import logging
import time
from enum import IntEnum
from typing import Any, AsyncIterator, Dict, List, Optional


class LogLevel(IntEnum):
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


class LogHub:
    """
    Log publish-subscribe hub.

    - Supports multiple WebSocket subscribers
    - Supports log level filtering
    - Keeps last N logs for initial loading
    """

    def __init__(self, buffer_size: int = 200):
        self._subscribers: List[asyncio.Queue] = []
        self._buffer: List[Dict[str, Any]] = []
        self._buffer_size = buffer_size
        self._lock = asyncio.Lock()

    def emit(self, level: str, message: str, logger_name: str = "", **extra) -> None:
        """Send log to all subscribers."""
        entry = {"ts": time.time(), "level": level, "msg": message, "logger": logger_name, **extra}

        # Add to buffer
        self._buffer.append(entry)
        if len(self._buffer) > self._buffer_size:
            self._buffer = self._buffer[-self._buffer_size :]

        # Send to all subscribers
        for queue in self._subscribers:
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                pass  # Drop if queue is full

    def get_recent(self, count: int = 50, min_level: str = "INFO") -> List[Dict[str, Any]]:
        """Get recent logs."""
        min_level_num = getattr(LogLevel, min_level.upper(), LogLevel.INFO)
        filtered = [
            e for e in self._buffer if getattr(LogLevel, e["level"].upper(), 0) >= min_level_num
        ]
        return filtered[-count:]

    async def subscribe(self, min_level: str = "INFO") -> AsyncIterator[Dict[str, Any]]:
        """Subscribe to log stream."""
        min_level_num = getattr(LogLevel, min_level.upper(), LogLevel.INFO)
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        self._subscribers.append(queue)
        try:
            while True:
                entry = await queue.get()
                entry_level = getattr(LogLevel, entry["level"].upper(), 0)
                if entry_level >= min_level_num:
                    yield entry
        finally:
            self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Global instance
log_hub = LogHub()


class LogHubHandler(logging.Handler):
    """Python logging Handler that sends logs to LogHub."""

    def __init__(self, hub: LogHub):
        super().__init__()
        self._hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._hub.emit(
                level=record.levelname,
                message=msg,
                logger_name=record.name,
                pathname=record.pathname,
                lineno=record.lineno,
                func_name=record.funcName,
            )
        except Exception:
            self.handleError(record)


def setup_log_hub_handler(hub: Optional[LogHub] = None) -> LogHubHandler:
    """Set up LogHub handler on root logger."""
    hub = hub or log_hub
    handler = LogHubHandler(hub)
    handler.setFormatter(logging.Formatter("%(message)s"))

    # Add to root logger
    root = logging.getLogger()
    root.addHandler(handler)

    # Also add to nimbus logger
    nimbus_logger = logging.getLogger("nimbus")
    nimbus_logger.addHandler(handler)

    return handler
