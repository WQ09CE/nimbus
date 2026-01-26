"""Nimbus CLI Main Entry Point.

This module provides the main CLI application using Typer.

Usage:
    nimbus --help
    nimbus serve --port 8080
    nimbus session list
    nimbus config show
"""

import typer
from typing import Optional

from .commands import serve, session, config, acp

# Create main app
app = typer.Typer(
    name="nimbus",
    help="Nimbus Agent Framework CLI",
    add_completion=False,
    no_args_is_help=True,
)

# Register sub-commands
app.add_typer(serve.app, name="serve", help="Start the Nimbus HTTP server")
app.add_typer(session.app, name="session", help="Manage sessions")
app.add_typer(config.app, name="config", help="Manage configuration")
app.add_typer(acp.app, name="acp", help="Start Nimbus as an ACP agent")


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        from nimbus import __version__
        typer.echo(f"nimbus version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Nimbus Agent Framework CLI.

    A framework for building notebook-style AI assistants with DAG planning,
    tiered memory management, and skill-based execution.
    """
    pass


def cli() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    cli()
