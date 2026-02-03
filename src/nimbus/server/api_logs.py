"""Logging API endpoints.

Provides:
- Frontend log receiving and storage
- Real-time backend log streaming via WebSocket
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from .log_hub import log_hub

router = APIRouter()  # No prefix here, will be included in main app with /api/v1 prefix

# Dedicated logger for frontend logs
frontend_logger = logging.getLogger("nimbus.frontend")
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


@router.post("/logs")
async def receive_logs(batch: LogBatch):
    """
    Receive logs from frontend and write to main server log.
    POST /api/v1/logs
    """
    for entry in batch.entries:
        # Convert frontend level to python log level
        lvl = entry.level.lower()
        log_func = frontend_logger.info

        if lvl == "debug":
            log_func = frontend_logger.debug
        elif lvl == "warn" or lvl == "warning":
            log_func = frontend_logger.warning
        elif lvl == "error":
            log_func = frontend_logger.error

        # Format: [UI] message (data)
        msg = f"[UI] {entry.message}"
        if entry.data:
            import json

            try:
                msg += f" | data={json.dumps(entry.data)}"
            except (TypeError, ValueError):
                msg += f" | data={str(entry.data)}"

        log_func(msg)

    return {"status": "ok", "count": len(batch.entries)}


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
