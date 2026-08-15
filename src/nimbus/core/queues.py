"""Input queues for the runtime loop — steering, follow-up, and the compat facade.

Delivery semantics (dsh Inbox vocabulary for orientation):
- SteeringQueue  ≈ steer:    injected while tools execute, checked between calls
- FollowUpQueue  ≈ followup: re-enters the loop after the agent finishes
- MessageQueue:   backward-compatible facade over SteeringQueue
"""

import asyncio
from typing import List, Optional

# =============================================================================
# Steering Queue (pi-style: checked between tool calls)
# =============================================================================


class SteeringQueue:
    """Messages injected while agent is executing tools.

    Checked between each tool call -- skips remaining tools.
    Pi-coding-agent uses a callback after each turn to ask for queued messages,
    supporting two modes: one-at-a-time or all-at-once.
    """

    def __init__(self, wakeup_event: Optional[asyncio.Event] = None) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._wakeup_event = wakeup_event

    def steer(self, message: str) -> None:
        """Add a steering message (thread-safe via asyncio.Queue).

        This triggers the wakeup event to interrupt the current LLM call
        if one is in progress.
        """
        self._queue.put_nowait(message)
        if self._wakeup_event:
            self._wakeup_event.set()

    def drain_one(self) -> Optional[str]:
        """Drain one message at a time (default mode)."""
        try:
            msg = self._queue.get_nowait()
            if self._wakeup_event and self._queue.empty():
                self._wakeup_event.clear()
            return msg
        except asyncio.QueueEmpty:
            return None

    def drain_all(self) -> List[str]:
        """Drain all queued messages at once."""
        messages: List[str] = []
        while not self._queue.empty():
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if self._wakeup_event and self._queue.empty():
            self._wakeup_event.clear()
        return messages

    @property
    def pending(self) -> int:
        return self._queue.qsize()


# =============================================================================
# Follow-Up Queue (pi-style: re-enters loop after agent finishes)
# =============================================================================


class FollowUpQueue:
    """Messages sent after agent finishes. Re-enters the loop."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    def follow_up(self, message: str) -> None:
        """Add a follow-up message."""
        self._queue.put_nowait(message)

    def drain(self) -> List[str]:
        """Drain all queued follow-up messages."""
        messages: List[str] = []
        while not self._queue.empty():
            try:
                messages.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return messages

    @property
    def pending(self) -> int:
        return self._queue.qsize()


# =============================================================================
# MessageQueue (backward-compatible facade)
# =============================================================================


class MessageQueue:
    """Backward-compatible facade over SteeringQueue.

    Existing code (e.g., session.py) uses message_queue.enqueue().
    This delegates to the steering queue transparently.
    """

    def __init__(self, steering_queue: SteeringQueue) -> None:
        self._steering = steering_queue

    def enqueue(self, message: str) -> None:
        """Add a message to the steering queue (backward compat)."""
        self._steering.steer(message)

    def drain(self) -> List[str]:
        """Drain all queued messages at once."""
        return self._steering.drain_all()

    def drain_one(self) -> Optional[str]:
        """Drain one message at a time."""
        return self._steering.drain_one()

    @property
    def pending(self) -> int:
        return self._steering.pending
