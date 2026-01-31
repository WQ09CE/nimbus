"""
TUI Dashboard Input Handler

Provides InputHandler for non-blocking stdin reading using a separate thread
with asyncio queue for communication with the main event loop.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from typing import List, Optional


class InputHandler:
    """
    Handles user input asynchronously.

    Design:
    - Uses a separate thread to read stdin
    - Queues input to main async loop
    - Supports command history
    - Non-blocking UI updates

    Usage:
        handler = InputHandler()
        handler.start(asyncio.get_event_loop())

        # In async context
        user_input = await handler.get_input()
        if user_input:
            print(f"User typed: {user_input}")

        handler.stop()
    """

    def __init__(self, max_history: int = 100):
        """
        Initialize InputHandler.

        Args:
            max_history: Maximum number of commands to keep in history
        """
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._history: List[str] = []
        self._max_history = max_history
        self._history_index = 0
        self._current_input = ""
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Start the input handler thread.

        Args:
            loop: The asyncio event loop to queue input to
        """
        if self._running:
            return

        self._running = True
        self._loop = loop
        self._thread = threading.Thread(target=self._input_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the input handler."""
        self._running = False
        # Note: The thread will exit on next input() return or EOFError

    def _input_loop(self) -> None:
        """Thread: Read input and queue to async loop."""
        while self._running:
            try:
                # Use sys.stdin.readline() for better compatibility
                line = sys.stdin.readline()
                if not line:  # EOF
                    break

                line = line.rstrip("\n")
                if line.strip():
                    # Add to history
                    self._add_to_history(line)
                    # Queue to async loop
                    if self._loop is not None:
                        asyncio.run_coroutine_threadsafe(self._queue.put(line), self._loop)
            except EOFError:
                break
            except KeyboardInterrupt:
                break
            except Exception:
                # Ignore other exceptions and continue
                pass

    async def get_input(self) -> Optional[str]:
        """
        Get next input from queue (non-blocking).

        Returns:
            User input string or None if no input available
        """
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    async def wait_for_input(self, timeout: Optional[float] = None) -> Optional[str]:
        """
        Wait for input from queue with optional timeout.

        Args:
            timeout: Maximum seconds to wait (None for no timeout)

        Returns:
            User input string or None if timeout
        """
        try:
            if timeout is not None:
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
            else:
                return await self._queue.get()
        except asyncio.TimeoutError:
            return None
        except asyncio.QueueEmpty:
            return None

    def _add_to_history(self, command: str) -> None:
        """Add command to history."""
        # Don't add duplicates of the last command
        if self._history and self._history[-1] == command:
            return

        self._history.append(command)
        if len(self._history) > self._max_history:
            self._history.pop(0)
        self._history_index = len(self._history)

    def get_history(self) -> List[str]:
        """Get command history."""
        return self._history.copy()

    def clear_history(self) -> None:
        """Clear command history."""
        self._history.clear()
        self._history_index = 0

    @property
    def is_running(self) -> bool:
        """Check if input handler is running."""
        return self._running
