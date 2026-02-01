"""Nimbus Session Command.

Manage Nimbus sessions via CLI.

Usage:
    nimbus session list
    nimbus session create --name "my-session"
    nimbus session delete <session_id>
    nimbus session show <session_id>
"""

import asyncio
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer()
console = Console()


def _get_db_path() -> str:
    """Get database path from environment or default."""
    return os.environ.get("NIMBUS_DB", ".nimbus/nimbus.db")


async def _list_sessions_async(
    db_path: str,
    status: str,
    limit: int,
    offset: int,
) -> None:
    """List sessions from the database."""
    from nimbus.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    try:
        await storage.initialize()
        sessions, total = await storage.list_sessions(
            status=status,
            limit=limit,
            offset=offset,
        )

        if not sessions:
            console.print("[dim]No sessions found.[/dim]")
            return

        # Create table
        table = Table(title=f"Sessions ({total} total)")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Name", style="green")
        table.add_column("Status", style="yellow")
        table.add_column("Memory Type", style="blue")
        table.add_column("Messages", justify="right")
        table.add_column("Created At", style="dim")

        for session in sessions:
            table.add_row(
                session["id"],
                session.get("name") or "[dim]unnamed[/dim]",
                session["status"],
                session["memory_type"],
                str(session.get("message_count", 0)),
                str(session["created_at"]),
            )

        console.print(table)
    finally:
        await storage.close()


async def _create_session_async(
    db_path: str,
    name: Optional[str],
    workspace: Optional[str],
    memory_type: str,
    planner_type: str,
) -> None:
    """Create a new session."""
    import uuid

    from nimbus.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    try:
        await storage.initialize()

        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        session = await storage.create_session(
            session_id=session_id,
            name=name,
            workspace_path=workspace,
            memory_type=memory_type,
            planner_type=planner_type,
        )

        console.print("[green]Session created successfully![/green]")
        console.print(f"  ID: [cyan]{session['id']}[/cyan]")
        if name:
            console.print(f"  Name: {name}")
        console.print(f"  Memory Type: {memory_type}")
        console.print(f"  Planner Type: {planner_type}")
        if workspace:
            console.print(f"  Workspace: {workspace}")
    finally:
        await storage.close()


async def _delete_session_async(
    db_path: str,
    session_id: str,
    force: bool,
) -> None:
    """Delete a session."""
    from nimbus.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    try:
        await storage.initialize()

        # Check if session exists
        session = await storage.get_session(session_id)
        if not session:
            console.print(f"[red]Session not found: {session_id}[/red]")
            raise typer.Exit(code=1)

        if not force:
            confirm = typer.confirm(
                f"Are you sure you want to delete session '{session.get('name') or session_id}'?"
            )
            if not confirm:
                console.print("[dim]Cancelled.[/dim]")
                return

        await storage.delete_session(session_id)
        console.print(f"[green]Session deleted: {session_id}[/green]")
    finally:
        await storage.close()


async def _show_session_async(
    db_path: str,
    session_id: str,
) -> None:
    """Show session details."""
    from nimbus.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    try:
        await storage.initialize()

        session = await storage.get_session(session_id)
        if not session:
            console.print(f"[red]Session not found: {session_id}[/red]")
            raise typer.Exit(code=1)

        messages = await storage.get_messages(session_id, limit=100)

        console.print("\n[bold]Session Details[/bold]")
        console.print(f"  ID: [cyan]{session['id']}[/cyan]")
        console.print(f"  Name: {session.get('name') or '[dim]unnamed[/dim]'}")
        console.print(f"  Status: [yellow]{session['status']}[/yellow]")
        console.print(f"  Memory Type: {session['memory_type']}")
        console.print(f"  Planner Type: {session['planner_type']}")
        console.print(f"  Workspace: {session.get('workspace_path') or '[dim]not set[/dim]'}")
        console.print(f"  Created: {session['created_at']}")
        console.print(f"  Updated: {session['updated_at']}")
        console.print(f"  Messages: {len(messages)}")

        if messages:
            console.print("\n[bold]Recent Messages[/bold]")
            for msg in messages[-5:]:  # Show last 5 messages
                role_color = "green" if msg["role"] == "assistant" else "blue"
                content = (
                    msg["content"][:100] + "..." if len(msg["content"]) > 100 else msg["content"]
                )
                console.print(f"  [{role_color}]{msg['role']}[/{role_color}]: {content}")
    finally:
        await storage.close()


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
    db: Optional[str] = typer.Option(
        None,
        "--db",
        "-d",
        help="Database file path",
        envvar="NIMBUS_DB",
    ),
) -> None:
    """List all sessions.

    Examples:
        nimbus session list
        nimbus session list --status archived
        nimbus session list --limit 50
    """
    db_path = db if db else _get_db_path()
    asyncio.run(_list_sessions_async(db_path, status, limit, offset))


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
    memory_type: str = typer.Option(
        "tiered",
        "--memory",
        "-m",
        help="Memory type (simple, tiered)",
    ),
    planner_type: str = typer.Option(
        "dag",
        "--planner",
        "-p",
        help="Planner type (simple, dag)",
    ),
    db: Optional[str] = typer.Option(
        None,
        "--db",
        "-d",
        help="Database file path",
        envvar="NIMBUS_DB",
    ),
) -> None:
    """Create a new session.

    Examples:
        nimbus session create
        nimbus session create --name "my-project"
        nimbus session create --name "my-project" --workspace /path/to/project
    """
    db_path = db if db else _get_db_path()
    asyncio.run(_create_session_async(db_path, name, workspace, memory_type, planner_type))


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
    db: Optional[str] = typer.Option(
        None,
        "--db",
        "-d",
        help="Database file path",
        envvar="NIMBUS_DB",
    ),
) -> None:
    """Delete a session.

    Examples:
        nimbus session delete sess_abc123
        nimbus session delete sess_abc123 --force
    """
    db_path = db if db else _get_db_path()
    asyncio.run(_delete_session_async(db_path, session_id, force))


@app.command("show")
def show_session(
    session_id: str = typer.Argument(
        ...,
        help="Session ID to show",
    ),
    db: Optional[str] = typer.Option(
        None,
        "--db",
        "-d",
        help="Database file path",
        envvar="NIMBUS_DB",
    ),
) -> None:
    """Show session details.

    Examples:
        nimbus session show sess_abc123
    """
    db_path = db if db else _get_db_path()
    asyncio.run(_show_session_async(db_path, session_id))


if __name__ == "__main__":
    app()
