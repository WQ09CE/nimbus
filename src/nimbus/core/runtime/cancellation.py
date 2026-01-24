"""Cooperative cancellation support for async tasks.

This module provides a CancellationToken class for cooperative task cancellation.
Tasks should periodically check the token and exit gracefully when cancelled.

Example:
    ```python
    async def long_running_task(cancel_token: CancellationToken):
        while not cancel_token.is_cancelled():
            await do_work()
        # Clean up and exit
    ```
"""

from dataclasses import dataclass, field
from typing import Optional
import asyncio


@dataclass
class CancellationToken:
    """Token for cooperative task cancellation.

    Tasks should periodically check this token and exit gracefully
    when cancelled is True. This provides a clean way to cancel running
    tasks without using asyncio.Task.cancel() which can leave resources
    in an undefined state.

    Attributes:
        cancelled: Whether cancellation has been requested.
        reason: Human-readable reason for cancellation.

    Example:
        ```python
        token = CancellationToken()

        async def worker():
            while not token.is_cancelled():
                await process_item()
            print(f"Cancelled: {token.reason}")

        # Later, request cancellation
        token.cancel("replan requested")
        ```
    """

    cancelled: bool = False
    reason: Optional[str] = None
    _event: asyncio.Event = field(default_factory=asyncio.Event)

    def cancel(self, reason: str = "replan requested") -> None:
        """Request cancellation of the task.

        This sets the cancelled flag and wakes up any waiters.
        The task should check is_cancelled() and exit gracefully.

        Args:
            reason: Human-readable reason for cancellation.
        """
        self.cancelled = True
        self.reason = reason
        self._event.set()

    def is_cancelled(self) -> bool:
        """Check if cancellation was requested.

        Tasks should call this periodically to check if they should
        stop processing and exit gracefully.

        Returns:
            True if cancellation was requested, False otherwise.
        """
        return self.cancelled

    async def wait_for_cancel(self, timeout: Optional[float] = None) -> bool:
        """Wait for cancellation signal.

        Useful for tasks that want to block until cancelled, rather
        than polling is_cancelled().

        Args:
            timeout: Maximum time to wait in seconds. None means wait forever.

        Returns:
            True if cancellation was received, False if timeout expired.

        Example:
            ```python
            async def waiter(token: CancellationToken):
                if await token.wait_for_cancel(timeout=10.0):
                    print("Was cancelled")
                else:
                    print("Timeout expired")
            ```
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def reset(self) -> None:
        """Reset token for reuse.

        Clears the cancelled flag and reason, allowing the token to be
        reused for a new task. This is useful when pooling tokens.

        Note:
            Be careful when resetting tokens that might still be in use.
            Only reset after ensuring no tasks are still checking it.
        """
        self.cancelled = False
        self.reason = None
        self._event.clear()

    def __repr__(self) -> str:
        """Return string representation."""
        if self.cancelled:
            return f"CancellationToken(cancelled=True, reason={self.reason!r})"
        return "CancellationToken(cancelled=False)"
