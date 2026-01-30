"""Nimbus Serve Command.

Start the Nimbus HTTP server.

Usage:
    nimbus serve
    nimbus serve --port 8080
    nimbus serve --host 0.0.0.0 --port 8080 --db ./data/nimbus.db
"""

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(invoke_without_command=True)
console = Console()


def _get_default_port() -> int:
    """Get default port from environment or fallback.

    Default is 4096 to match OpenCode's default port for OpenWork compatibility.
    """
    return int(os.environ.get("NIMBUS_PORT", "4096"))


def _get_default_host() -> str:
    """Get default host from environment or fallback."""
    return os.environ.get("NIMBUS_HOST", "127.0.0.1")


def _get_default_db() -> str:
    """Get default database path from environment or fallback."""
    return os.environ.get("NIMBUS_DB", ".nimbus/nimbus.db")


def _setup_signal_handlers(shutdown_event: asyncio.Event) -> None:
    """Setup graceful shutdown signal handlers."""
    def signal_handler(sig: signal.Signals) -> None:
        console.print(f"\n[yellow]Received {sig.name}, shutting down gracefully...[/yellow]")
        shutdown_event.set()

    # Handle SIGINT (Ctrl+C) and SIGTERM
    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
    else:
        # Windows doesn't support add_signal_handler
        signal.signal(signal.SIGINT, lambda s, f: signal_handler(signal.SIGINT))
        signal.signal(signal.SIGTERM, lambda s, f: signal_handler(signal.SIGTERM))


async def _run_server(
    host: str,
    port: int,
    db_path: str,
    reload: bool,
    workers: int,
    quiet: bool = False,
    log_level: str = "info",
) -> None:
    """Run the server asynchronously."""
    import uvicorn

    # Ensure database directory exists
    db_dir = Path(db_path).parent
    db_dir.mkdir(parents=True, exist_ok=True)

    # Set environment variables for the server
    os.environ["NIMBUS_DB"] = db_path
    os.environ["NIMBUS_HOST"] = host
    os.environ["NIMBUS_PORT"] = str(port)
    
    # If quiet mode, disable console logging in Nimbus
    if quiet:
        os.environ["NIMBUS_LOG_CONSOLE"] = "false"

    # Configure uvicorn
    config = uvicorn.Config(
        app="nimbus.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        workers=workers if not reload else 1,
        log_level="warning" if quiet else log_level,
        access_log=not quiet,
    )

    server = uvicorn.Server(config)

    # Run server
    await server.serve()


@app.callback(invoke_without_command=True)
def serve(
    ctx: typer.Context,
    port: int = typer.Option(
        None,
        "--port",
        "-p",
        help="Port to listen on",
        envvar="NIMBUS_PORT",
    ),
    host: str = typer.Option(
        None,
        "--host",
        "-h",
        help="Host to bind to",
        envvar="NIMBUS_HOST",
    ),
    db: str = typer.Option(
        None,
        "--db",
        "-d",
        help="Database file path",
        envvar="NIMBUS_DB",
    ),
    reload: bool = typer.Option(
        False,
        "--reload",
        "-r",
        help="Enable auto-reload for development",
    ),
    workers: int = typer.Option(
        1,
        "--workers",
        "-w",
        help="Number of worker processes (ignored if --reload is set)",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress console logging (logs still written to file)",
    ),
    log_level: str = typer.Option(
        "info",
        "--log-level",
        "-l",
        help="Log level: debug, info, warning, error",
    ),
) -> None:
    """Start the Nimbus HTTP server.

    The server provides a RESTful API for managing sessions and interacting
    with the Nimbus agent. It also supports SSE (Server-Sent Events) for
    real-time updates.

    Environment Variables:
        NIMBUS_PORT: Default port (default: 4096, OpenCode compatible)
        NIMBUS_HOST: Default host (default: 127.0.0.1)
        NIMBUS_DB: Default database path (default: .nimbus/nimbus.db)

    Examples:
        # Start with defaults
        nimbus serve

        # Start on all interfaces with custom port
        nimbus serve --host 0.0.0.0 --port 3000

        # Development mode with auto-reload
        nimbus serve --reload
    """
    # If invoked without subcommand, run the server
    if ctx.invoked_subcommand is None:
        # Apply defaults
        actual_port = port if port is not None else _get_default_port()
        actual_host = host if host is not None else _get_default_host()
        actual_db = db if db is not None else _get_default_db()

        if not quiet:
            console.print(f"[bold green]Starting Nimbus Server[/bold green]")
            console.print(f"  Host: {actual_host}")
            console.print(f"  Port: {actual_port}")
            console.print(f"  Database: {actual_db}")
            console.print(f"  Reload: {reload}")
            console.print(f"  Workers: {workers}")
            console.print(f"  Log Level: {log_level}")
            console.print()
            console.print(f"[dim]API docs: http://{actual_host}:{actual_port}/docs[/dim]")
            console.print(f"[dim]Health check: http://{actual_host}:{actual_port}/api/v1/health[/dim]")
            console.print()

        try:
            asyncio.run(_run_server(
                host=actual_host,
                port=actual_port,
                db_path=actual_db,
                reload=reload,
                workers=workers,
                quiet=quiet,
                log_level=log_level,
            ))
        except KeyboardInterrupt:
            console.print("\n[yellow]Server stopped.[/yellow]")
        except Exception as e:
            console.print(f"[red]Error starting server: {e}[/red]")
            raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
