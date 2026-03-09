"""SSE Event Hub for real-time streaming.

This module provides:
- SSEHub: Manages SSE connections and broadcasts events
- Event types: connected, planning, dag_created, task_start, etc.
- Connection lifecycle management with heartbeat
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from .models import SSEEvent


@dataclass
class SSEConnection:
    """Represents an active SSE connection."""

    session_id: str
    queue: asyncio.Queue
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: float = field(default_factory=time.time)


class SSEHub:
    """
    Server-Sent Events hub for managing real-time event streaming.

    Supports:
    - Per-session event subscriptions
    - Broadcast to all connections
    - Connection lifecycle management
    - Heartbeat for keeping connections alive
    """

    # SSE Event Types (pi-style minimal set)
    EVENT_CONNECTED = "connected"
    EVENT_MESSAGE_START = "message_start"
    EVENT_MESSAGE = "message"
    EVENT_TOOL_CALL = "tool_call"
    EVENT_TOOL_RESULT = "tool_result"
    EVENT_DONE = "done"
    EVENT_ERROR = "error"
    EVENT_HEARTBEAT = "heartbeat"

    def __init__(self, heartbeat_interval: float = 15.0):
        """Initialize SSE Hub.

        Args:
            heartbeat_interval: Seconds between heartbeat events.
        """
        self._connections: Dict[str, List[SSEConnection]] = {}
        self._pending_events: Dict[str, List[str]] = {}  # session_id -> buffered event strings
        self._event_log: Dict[str, List[str]] = {}  # session_id -> all events for replay on reconnect
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._closed_sessions = set()

    def prepare_session(self, session_id: str) -> None:
        """Prepare event buffer for a session. Call before starting background work."""
        if session_id not in self._pending_events:
            self._pending_events[session_id] = []
        self._event_log[session_id] = []  # Reset log for new session run
        self._closed_sessions.discard(session_id)

    async def start(self) -> None:
        """Start the heartbeat task."""
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        """Stop the heartbeat task and close all connections."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Close all connections
        async with self._lock:
            for connections in self._connections.values():
                for conn in connections:
                    await conn.queue.put(None)  # Signal to close
            self._connections.clear()

    async def subscribe(self, session_id: str) -> AsyncIterator[str]:
        """
        Subscribe to events for a session.

        Args:
            session_id: Session to subscribe to.

        Yields:
            SSE-formatted event strings.
        """
        connection = SSEConnection(
            session_id=session_id,
            queue=asyncio.Queue(),
        )

        async with self._lock:
            # Allow multiple SSE connections per session (multi-tab support)
            if session_id not in self._connections:
                self._connections[session_id] = []
            self._connections[session_id].append(connection)

            # Replay full event log (reconnect scenario: client refreshed mid-stream)
            # Event log already contains pending events, so skip pending when log exists
            event_log = self._event_log.get(session_id, [])
            if event_log:
                for event_str in event_log:
                    await connection.queue.put(event_str)
                # Discard pending since they are already in the event log
                self._pending_events.pop(session_id, None)
            else:
                # No event log (first connect): replay buffered pending events
                pending = self._pending_events.pop(session_id, [])
                for event_str in pending:
                    await connection.queue.put(event_str)

            if session_id in self._closed_sessions:
                await connection.queue.put(None)

        # Send connected event (after replay, so order is: buffered events -> connected)
        await self._send_to_connection(connection, self.EVENT_CONNECTED, {"session_id": session_id})

        try:
            while True:
                try:
                    # Wait for event with timeout for connection check
                    event_str = await asyncio.wait_for(
                        connection.queue.get(), timeout=self._heartbeat_interval + 5
                    )

                    if event_str is None:
                        # Connection closing signal
                        break

                    yield event_str

                except asyncio.TimeoutError:
                    # Send heartbeat if no events
                    heartbeat = self._format_sse(
                        self.EVENT_HEARTBEAT, {"timestamp": datetime.now(timezone.utc).isoformat()}
                    )
                    yield heartbeat

        finally:
            # Cleanup connection
            async with self._lock:
                if session_id in self._connections:
                    self._connections[session_id] = [
                        c for c in self._connections[session_id] if c is not connection
                    ]
                    if not self._connections[session_id]:
                        del self._connections[session_id]

    async def publish(self, session_id: str, event_type: str, data: Dict[str, Any]) -> int:
        """
        Publish an event to all subscribers of a session.

        Args:
            session_id: Target session.
            event_type: Event type string.
            data: Event data payload.

        Returns:
            Number of connections the event was sent to.
        """
        sent_count = 0
        event_str = self._format_sse(event_type, data)

        async with self._lock:
            # Append to event log for replay on reconnect (skip heartbeats)
            if event_type != self.EVENT_HEARTBEAT and session_id in self._event_log:
                self._event_log[session_id].append(event_str)

            connections = self._connections.get(session_id, [])
            if connections:
                for conn in connections:
                    await conn.queue.put(event_str)
                    conn.last_heartbeat = time.time()
                    sent_count += 1
            elif session_id in self._pending_events:
                # No subscriber yet, buffer the event
                self._pending_events[session_id].append(event_str)

        return sent_count

    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> int:
        """
        Broadcast an event to all connected sessions.

        Args:
            event_type: Event type string.
            data: Event data payload.

        Returns:
            Number of connections the event was sent to.
        """
        sent_count = 0

        async with self._lock:
            for connections in self._connections.values():
                for conn in connections:
                    await self._send_to_connection(conn, event_type, data)
                    sent_count += 1

        return sent_count

    async def _send_to_connection(
        self, connection: SSEConnection, event_type: str, data: Dict[str, Any]
    ) -> None:
        """Send an event to a specific connection."""
        event_str = self._format_sse(event_type, data)
        await connection.queue.put(event_str)
        connection.last_heartbeat = time.time()

    def _format_sse(self, event_type: str, data: Dict[str, Any]) -> str:
        """
        Format data as SSE event string.

        Args:
            event_type: Event type.
            data: Event data.

        Returns:
            SSE-formatted string.
        """
        json_data = json.dumps(data, default=str, ensure_ascii=False)
        return f"event: {event_type}\ndata: {json_data}\n\n"

    async def _heartbeat_loop(self) -> None:
        """Background task to send heartbeats."""
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval)

                # Send heartbeat to all connections
                heartbeat_data = {"timestamp": datetime.now(timezone.utc).isoformat()}
                await self.broadcast(self.EVENT_HEARTBEAT, heartbeat_data)

            except asyncio.CancelledError:
                break
            except Exception:
                # Log but don't crash
                pass

    def get_connection_count(self, session_id: Optional[str] = None) -> int:
        """
        Get the number of active connections.

        Args:
            session_id: Optional session to filter by.

        Returns:
            Number of connections.
        """
        if session_id:
            return len(self._connections.get(session_id, []))
        return sum(len(conns) for conns in self._connections.values())

    async def reset_session_log(self, session_id: str) -> None:
        """Reset event log for a session after a task completes.

        Keeps existing SSE connections alive so multi-client subscribers
        continue receiving events on the next task without reconnecting.

        Args:
            session_id: Session whose log should be cleared.
        """
        async with self._lock:
            self._event_log.pop(session_id, None)
            self._pending_events.pop(session_id, None)
            self._closed_sessions.discard(session_id)

    async def close_session(self, session_id: str) -> None:
        """Close all connections for a session.
        
        Args:
            session_id: Session to close.
        """
        async with self._lock:
            connections = self._connections.get(session_id, [])
            for conn in connections:
                await conn.queue.put(None)  # Signal to close
            if session_id in self._connections:
                del self._connections[session_id]
            self._closed_sessions.add(session_id)
            # Event log and pending events are kept so late-subscribing clients can still replay.
            # They will be reset by `prepare_session` on the next run.

    def get_active_sessions(self) -> List[str]:
        """Get list of session IDs with active connections."""
        return list(self._connections.keys())


class SSEEventBuilder:
    """Helper class to build standardized SSE events."""

    @staticmethod
    def connected(session_id: str) -> SSEEvent:
        """Create connected event."""
        return SSEEvent(event=SSEHub.EVENT_CONNECTED, data={"session_id": session_id})

    @staticmethod
    def message_start(message_id: str) -> SSEEvent:
        """Create message_start event."""
        return SSEEvent(event=SSEHub.EVENT_MESSAGE_START, data={"message_id": message_id})

    @staticmethod
    def tool_call(tool: str, args: Dict[str, Any], action_id: str = None) -> SSEEvent:
        """Create tool_call event."""
        return SSEEvent(event=SSEHub.EVENT_TOOL_CALL, data={"tool": tool, "args": args, "action_id": action_id})

    @staticmethod
    def tool_result(tool: str, result: Any, action_id: str = None, status: str = "OK") -> SSEEvent:
        """Create tool_result event."""
        return SSEEvent(event=SSEHub.EVENT_TOOL_RESULT, data={"tool": tool, "result": result, "action_id": action_id, "status": status})

    @staticmethod
    def done(status: str = "OK") -> SSEEvent:
        """Create done event."""
        return SSEEvent(event=SSEHub.EVENT_DONE, data={"status": status})

    @staticmethod
    def message(content: str, artifacts: Optional[List] = None) -> SSEEvent:
        """Create message event."""
        return SSEEvent(
            event=SSEHub.EVENT_MESSAGE,
            data={
                "content": content,
                "artifacts": artifacts or [],
            },
        )

    @staticmethod
    def error(code: str, message: str) -> SSEEvent:
        """Create error event."""
        return SSEEvent(event=SSEHub.EVENT_ERROR, data={"code": code, "message": message})

    @staticmethod
    def heartbeat() -> SSEEvent:
        """Create heartbeat event."""
        return SSEEvent(
            event=SSEHub.EVENT_HEARTBEAT, data={"timestamp": datetime.now(timezone.utc).isoformat()}
        )
