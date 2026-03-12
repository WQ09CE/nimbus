"""Nimbus Session Command.

Manage Nimbus sessions via CLI.

Note: SQLite-based persistent session storage has been removed in nimbus-next.
Sessions are now managed in-memory by the server's SessionManagerV2.

Usage:
    nimbus session list
    nimbus session create --name "my-session"
    nimbus session delete <session_id>
    nimbus session show <session_id>
"""

from typing import Optional

import typer
from rich.console import Console

app = typer.Typer()
console = Console()

_NOT_AVAILABLE_MSG = (
    "[yellow]Persistent session storage is not available in this version.[/yellow]\n"
    "Sessions are managed in-memory by the server.\n"
    "Use the HTTP API (POST /api/v1/sessions) to manage sessions when the server is running."
)


@app.command("list")
def list_sessions(
    status: str = typer.Option(
        "active",
        "--status",
        "-s",
        help="Filter by status (active, archived, deleted)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of sessions to show",
    ),
    offset: int = typer.Option(
        0,
        "--offset",
        "-o",
        help="Offset for pagination",
    ),
) -> None:
    """List all sessions.

    Examples:
        nimbus session list
    """
    console.print(_NOT_AVAILABLE_MSG)


@app.command("create")
def create_session(
    name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help="Session name",
    ),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Working directory for the session",
    ),
) -> None:
    """Create a new session.

    Examples:
        nimbus session create
        nimbus session create --name "my-project"
    """
    console.print(_NOT_AVAILABLE_MSG)


@app.command("delete")
def delete_session(
    session_id: str = typer.Argument(
        ...,
        help="Session ID to delete",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Delete a session.

    Examples:
        nimbus session delete sess_abc123
    """
    console.print(_NOT_AVAILABLE_MSG)


@app.command("show")
def show_session(
    session_id: str = typer.Argument(
        ...,
        help="Session ID to show",
    ),
) -> None:
    """Show session details.

    Examples:
        nimbus session show sess_abc123
    """
    console.print(_NOT_AVAILABLE_MSG)


if __name__ == "__main__":
    app()
