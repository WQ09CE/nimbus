"""Nimbus Config Command.

Manage Nimbus configuration via CLI.

Usage:
    nimbus config show
    nimbus config set <key> <value>
    nimbus config get <key>
    nimbus config reset
"""

import asyncio
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer()
console = Console()

# Configuration keys and their defaults
CONFIG_DEFAULTS = {
    "default_memory_type": "tiered",
    "default_planner_type": "dag",
    "max_concurrent_sessions": "10",
    "default_host": "127.0.0.1",
    "default_port": "8080",
}

# Valid values for each config key
CONFIG_VALID_VALUES = {
    "default_memory_type": ["simple", "tiered"],
    "default_planner_type": ["simple", "dag"],
}


def _get_db_path() -> str:
    """Get database path from environment or default."""
    return os.environ.get("NIMBUS_DB", ".nimbus/nimbus.db")


async def _show_config_async(db_path: str) -> None:
    """Show all configuration."""
    from nimbus.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    try:
        await storage.initialize()

        # Get all config from kv_store
        async with storage._get_connection() as db:
            cursor = await db.execute("SELECT key, value FROM kv_store WHERE key LIKE 'config.%'")
            rows = await cursor.fetchall()
            stored_config = {row["key"].replace("config.", ""): row["value"] for row in rows}

        # Merge with defaults
        config = {**CONFIG_DEFAULTS, **stored_config}

        # Create table
        table = Table(title="Nimbus Configuration")
        table.add_column("Key", style="cyan", no_wrap=True)
        table.add_column("Value", style="green")
        table.add_column("Source", style="dim")

        for key, default in CONFIG_DEFAULTS.items():
            value = config.get(key, default)
            source = "stored" if key in stored_config else "default"
            table.add_row(key, value, source)

        console.print(table)

        # Also show environment variables
        console.print("\n[bold]Environment Variables[/bold]")
        env_vars = [
            ("NIMBUS_HOST", os.environ.get("NIMBUS_HOST", "[dim]not set[/dim]")),
            ("NIMBUS_PORT", os.environ.get("NIMBUS_PORT", "[dim]not set[/dim]")),
            ("NIMBUS_DB", os.environ.get("NIMBUS_DB", "[dim]not set[/dim]")),
        ]
        for name, value in env_vars:
            console.print(f"  {name}: {value}")
    finally:
        await storage.close()


async def _get_config_async(db_path: str, key: str) -> None:
    """Get a specific configuration value."""
    from nimbus.storage.sqlite import SQLiteStorage

    if key not in CONFIG_DEFAULTS:
        console.print(f"[red]Unknown config key: {key}[/red]")
        console.print(f"Valid keys: {', '.join(CONFIG_DEFAULTS.keys())}")
        raise typer.Exit(code=1)

    storage = SQLiteStorage(db_path)
    try:
        await storage.initialize()

        async with storage._get_connection() as db:
            cursor = await db.execute(
                "SELECT value FROM kv_store WHERE key = ?", (f"config.{key}",)
            )
            row = await cursor.fetchone()

        value = row["value"] if row else CONFIG_DEFAULTS[key]
        console.print(value)
    finally:
        await storage.close()


async def _set_config_async(db_path: str, key: str, value: str) -> None:
    """Set a configuration value."""
    from nimbus.storage.sqlite import SQLiteStorage

    if key not in CONFIG_DEFAULTS:
        console.print(f"[red]Unknown config key: {key}[/red]")
        console.print(f"Valid keys: {', '.join(CONFIG_DEFAULTS.keys())}")
        raise typer.Exit(code=1)

    # Validate value if there are constraints
    if key in CONFIG_VALID_VALUES:
        if value not in CONFIG_VALID_VALUES[key]:
            console.print(f"[red]Invalid value for {key}: {value}[/red]")
            console.print(f"Valid values: {', '.join(CONFIG_VALID_VALUES[key])}")
            raise typer.Exit(code=1)

    # Validate numeric values
    if key in ["max_concurrent_sessions", "default_port"]:
        try:
            int(value)
        except ValueError:
            console.print(f"[red]Value must be a number: {value}[/red]")
            raise typer.Exit(code=1)

    storage = SQLiteStorage(db_path)
    try:
        await storage.initialize()

        async with storage._get_connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO kv_store (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (f"config.{key}", value),
            )
            await db.commit()

        console.print(f"[green]Set {key} = {value}[/green]")
    finally:
        await storage.close()


async def _reset_config_async(db_path: str) -> None:
    """Reset all configuration to defaults."""
    from nimbus.storage.sqlite import SQLiteStorage

    storage = SQLiteStorage(db_path)
    try:
        await storage.initialize()

        async with storage._get_connection() as db:
            await db.execute("DELETE FROM kv_store WHERE key LIKE 'config.%'")
            await db.commit()

        console.print("[green]Configuration reset to defaults.[/green]")
    finally:
        await storage.close()


@app.command("show")
def show_config(
    db: Optional[str] = typer.Option(
        None,
        "--db",
        "-d",
        help="Database file path",
        envvar="NIMBUS_DB",
    ),
) -> None:
    """Show all configuration.

    Displays both stored configuration and environment variables.

    Examples:
        nimbus config show
    """
    db_path = db if db else _get_db_path()
    asyncio.run(_show_config_async(db_path))


@app.command("get")
def get_config(
    key: str = typer.Argument(
        ...,
        help="Configuration key to get",
    ),
    db: Optional[str] = typer.Option(
        None,
        "--db",
        "-d",
        help="Database file path",
        envvar="NIMBUS_DB",
    ),
) -> None:
    """Get a specific configuration value.

    Examples:
        nimbus config get default_memory_type
        nimbus config get max_concurrent_sessions
    """
    db_path = db if db else _get_db_path()
    asyncio.run(_get_config_async(db_path, key))


@app.command("set")
def set_config(
    key: str = typer.Argument(
        ...,
        help="Configuration key to set",
    ),
    value: str = typer.Argument(
        ...,
        help="Value to set",
    ),
    db: Optional[str] = typer.Option(
        None,
        "--db",
        "-d",
        help="Database file path",
        envvar="NIMBUS_DB",
    ),
) -> None:
    """Set a configuration value.

    Valid keys:
        - default_memory_type: simple | tiered
        - default_planner_type: simple | dag
        - max_concurrent_sessions: number
        - default_host: hostname
        - default_port: port number

    Examples:
        nimbus config set default_memory_type tiered
        nimbus config set max_concurrent_sessions 20
    """
    db_path = db if db else _get_db_path()
    asyncio.run(_set_config_async(db_path, key, value))


@app.command("reset")
def reset_config(
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
    """Reset all configuration to defaults.

    Examples:
        nimbus config reset
        nimbus config reset --force
    """
    if not force:
        confirm = typer.confirm("Are you sure you want to reset all configuration?")
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            return

    db_path = db if db else _get_db_path()
    asyncio.run(_reset_config_async(db_path))


if __name__ == "__main__":
    app()
