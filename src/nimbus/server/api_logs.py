"""Logging API endpoints.

Provides:
- Frontend log receiving and storage
- Real-time backend log streaming via WebSocket
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .log_hub import log_hub

router = APIRouter(tags=["Logs"])

# Log file path
LOG_DIR = Path(".logs/frontend")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "frontend.log"

logger = logging.getLogger(__name__)


class LogEntry(BaseModel):
    """Single log entry from frontend."""

    level: str = "info"  # debug, info, warn, error
    message: str
    data: Optional[dict] = None
    timestamp: Optional[str] = None


class LogBatch(BaseModel):
    """Batch of log entries."""

    entries: List[LogEntry]
    source: str = "frontend"


@router.post("/api/logs")
async def receive_logs(batch: LogBatch):
    """Receive and store frontend logs."""
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        for entry in batch.entries:
            ts = entry.timestamp or datetime.now().isoformat()
            log_line = {
                "timestamp": ts,
                "source": batch.source,
                "level": entry.level,
                "message": entry.message,
            }
            if entry.data:
                log_line["data"] = entry.data
            f.write(json.dumps(log_line, ensure_ascii=False) + "\n")

    logger.debug(f"Received {len(batch.entries)} log entries from {batch.source}")
    return {"status": "ok", "count": len(batch.entries)}


@router.get("/api/logs/tail")
async def tail_logs(lines: int = 50):
    """Get the last N lines of frontend logs."""
    if not LOG_FILE.exists():
        return {"lines": []}

    with open(LOG_FILE, "r", encoding="utf-8") as f:
        all_lines = f.readlines()
        return {"lines": all_lines[-lines:]}


# ═══════════════════════════════════════════════════════════════════════════
# Backend Log Streaming via WebSocket
# ═══════════════════════════════════════════════════════════════════════════


@router.websocket("/ws/logs")
async def logs_websocket(
    websocket: WebSocket,
    level: str = Query("INFO", description="Minimum log level"),
):
    """
    WebSocket endpoint for real-time backend log streaming.

    Connect: ws://localhost:8000/ws/logs?level=INFO

    Messages are JSON objects:
    {
        "ts": 1234567890.123,
        "level": "INFO",
        "msg": "Log message",
        "logger": "nimbus.server",
        ...
    }
    """
    # Accept all origins for WebSocket (CORS middleware doesn't handle WS)
    await websocket.accept(subprotocol=None)
    logger.info(f"Log WebSocket connected, level={level}")

    try:
        # Send recent logs first
        recent = log_hub.get_recent(count=50, min_level=level)
        for entry in recent:
            await websocket.send_json(entry)

        # Stream new logs
        async for entry in log_hub.subscribe(min_level=level):
            await websocket.send_json(entry)

    except WebSocketDisconnect:
        logger.info("Log WebSocket disconnected")
    except Exception as e:
        logger.error(f"Log WebSocket error: {e}")


@router.get("/api/logs/recent")
async def get_recent_logs(
    count: int = Query(50, ge=1, le=500),
    level: str = Query("INFO"),
):
    """Get recent backend logs (REST fallback)."""
    return {
        "items": log_hub.get_recent(count=count, min_level=level),
        "subscriber_count": log_hub.subscriber_count,
    }
