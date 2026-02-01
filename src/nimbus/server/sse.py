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
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

from .models import SSEEvent


@dataclass
class SSEConnection:
    """Represents an active SSE connection."""

    session_id: str
    queue: asyncio.Queue
    created_at: datetime = field(default_factory=datetime.now)
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

    # SSE Event Types
    EVENT_CONNECTED = "connected"
    EVENT_MESSAGE_START = "message_start"
    EVENT_PLANNING = "planning"
    EVENT_DAG_CREATED = "dag_created"
    EVENT_TASK_START = "task_start"
    EVENT_TOOL_CALL = "tool_call"
    EVENT_TOOL_RESULT = "tool_result"
    EVENT_TASK_DONE = "task_done"
    EVENT_TASK_FAILED = "task_failed"
    EVENT_PERMISSION_REQUEST = "permission_request"
    EVENT_DAG_COMPLETE = "dag_complete"
    EVENT_MESSAGE = "message"
    EVENT_ERROR = "error"
    EVENT_HEARTBEAT = "heartbeat"

    def __init__(self, heartbeat_interval: float = 30.0):
        """Initialize SSE Hub.

        Args:
            heartbeat_interval: Seconds between heartbeat events.
        """
        self._connections: Dict[str, List[SSEConnection]] = {}
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

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
            if session_id not in self._connections:
                self._connections[session_id] = []
            self._connections[session_id].append(connection)

        # Send connected event
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
                        self.EVENT_HEARTBEAT, {"timestamp": datetime.now().isoformat()}
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

        async with self._lock:
            connections = self._connections.get(session_id, [])
            for conn in connections:
                await self._send_to_connection(conn, event_type, data)
                sent_count += 1

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
                heartbeat_data = {"timestamp": datetime.now().isoformat()}
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
    def planning(status: str = "creating_plan") -> SSEEvent:
        """Create planning event."""
        return SSEEvent(event=SSEHub.EVENT_PLANNING, data={"status": status})

    @staticmethod
    def dag_created(
        dag_id: str, goal: str, total_tasks: int, nodes: Optional[List[Dict]] = None
    ) -> SSEEvent:
        """Create dag_created event."""
        return SSEEvent(
            event=SSEHub.EVENT_DAG_CREATED,
            data={
                "dag_id": dag_id,
                "goal": goal,
                "total_tasks": total_tasks,
                "nodes": nodes or [],
            },
        )

    @staticmethod
    def task_start(task_id: str, skill: str, params: Dict[str, Any]) -> SSEEvent:
        """Create task_start event."""
        return SSEEvent(
            event=SSEHub.EVENT_TASK_START,
            data={
                "task_id": task_id,
                "skill": skill,
                "params": params,
            },
        )

    @staticmethod
    def tool_call(tool: str, args: Dict[str, Any]) -> SSEEvent:
        """Create tool_call event."""
        return SSEEvent(event=SSEHub.EVENT_TOOL_CALL, data={"tool": tool, "args": args})

    @staticmethod
    def tool_result(tool: str, result: Any) -> SSEEvent:
        """Create tool_result event."""
        return SSEEvent(event=SSEHub.EVENT_TOOL_RESULT, data={"tool": tool, "result": result})

    @staticmethod
    def task_done(task_id: str, result: Any, duration_ms: int) -> SSEEvent:
        """Create task_done event."""
        return SSEEvent(
            event=SSEHub.EVENT_TASK_DONE,
            data={
                "task_id": task_id,
                "result": result,
                "duration_ms": duration_ms,
            },
        )

    @staticmethod
    def task_failed(task_id: str, error: str) -> SSEEvent:
        """Create task_failed event."""
        return SSEEvent(event=SSEHub.EVENT_TASK_FAILED, data={"task_id": task_id, "error": error})

    @staticmethod
    def permission_request(request_id: str, tool: str, args: Dict[str, Any]) -> SSEEvent:
        """Create permission_request event."""
        return SSEEvent(
            event=SSEHub.EVENT_PERMISSION_REQUEST,
            data={
                "request_id": request_id,
                "tool": tool,
                "args": args,
            },
        )

    @staticmethod
    def dag_complete(dag_id: str, stats: Dict[str, int]) -> SSEEvent:
        """Create dag_complete event."""
        return SSEEvent(event=SSEHub.EVENT_DAG_COMPLETE, data={"dag_id": dag_id, "stats": stats})

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
            event=SSEHub.EVENT_HEARTBEAT, data={"timestamp": datetime.now().isoformat()}
        )
