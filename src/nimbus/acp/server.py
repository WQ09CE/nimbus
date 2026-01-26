"""ACP Server for Nimbus.

Provides stdio-based JSON-RPC server for ACP protocol communication.
This is the main entry point for running Nimbus as an ACP agent.

Usage:
    from nimbus.acp.server import run_server, ACPServer

    # Simple usage
    run_server()

    # With configuration
    from nimbus.acp import ACPConfig
    config = ACPConfig(cwd="/path/to/project", llm_model="qwen3:8b")
    run_server(config)
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapter import ACPConfig

log = logging.getLogger("nimbus.acp.server")


class ACPServer:
    """ACP JSON-RPC server over stdio.

    This server provides the standard ACP interface over stdin/stdout,
    allowing Nimbus to be used with ACP-compatible clients like Toad.

    Attributes:
        config: Server configuration.
        agent: The ACP agent instance handling protocol methods.
    """

    def __init__(self, config: ACPConfig | None = None) -> None:
        """Initialize the ACP server.

        Args:
            config: Optional server configuration. Uses defaults if not provided.
        """
        from .adapter import ACPConfig
        from .agent import NimbusACPAgent

        self.config = config or ACPConfig()
        self.agent = NimbusACPAgent(self.config)
        self._running = False
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start the ACP server, reading from stdin and writing to stdout.

        This is the main entry point for the server. It:
        1. Sets up signal handlers for graceful shutdown
        2. Delegates to the agent's serve_stdio method
        3. Handles shutdown gracefully
        """
        self._running = True

        # Setup signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()

        # Only set up signal handlers on non-Windows platforms
        if sys.platform != "win32":
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._handle_signal)

        log.info("Nimbus ACP Server starting...")

        try:
            # Delegate to agent's serve_stdio which handles the connection
            await self.agent.serve_stdio()
        except asyncio.CancelledError:
            log.info("Server cancelled")
        except EOFError:
            log.info("Connection closed (EOF)")
        except Exception as e:
            log.error(f"Server error: {e}")
            raise
        finally:
            self._running = False
            log.info("Nimbus ACP Server stopped")

    def _handle_signal(self) -> None:
        """Handle shutdown signal."""
        log.info("Shutdown signal received")
        self._running = False
        self._shutdown_event.set()

        # Cancel the current task to stop the server
        for task in asyncio.all_tasks():
            if task is not asyncio.current_task():
                task.cancel()

    async def stop(self) -> None:
        """Stop the server gracefully."""
        self._running = False
        self._shutdown_event.set()


def run_server(config: ACPConfig | None = None) -> None:
    """Run the ACP server (blocking).

    This is the main entry point for running the ACP server.
    It blocks until the server is stopped or the connection is closed.

    Args:
        config: Optional ACPConfig for server configuration.

    Example:
        from nimbus.acp.server import run_server
        from nimbus.acp import ACPConfig

        config = ACPConfig(
            cwd="/path/to/project",
            llm_model="claude-3-5-sonnet-20241022",
        )
        run_server(config)
    """
    # Configure logging to stderr (stdout is used for JSON-RPC)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    server = ACPServer(config)

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        log.info("Server interrupted by user")
    except Exception as e:
        log.error(f"Server failed: {e}")
        sys.exit(1)


async def run_server_async(config: ACPConfig | None = None) -> None:
    """Run the ACP server asynchronously.

    This is useful when you need to run the server within an existing
    async context.

    Args:
        config: Optional ACPConfig for server configuration.
    """
    server = ACPServer(config)
    await server.start()


if __name__ == "__main__":
    # Allow running directly: python -m nimbus.acp.server
    run_server()
