"""Nimbus Config Command.

Manage Nimbus configuration via CLI.

Usage:
    nimbus config show
    nimbus config set <key> <value>
    nimbus config get <key>
    nimbus config reset

Note: SQLite-based persistent storage has been removed in nimbus-next.
Configuration is managed via ~/.nimbus/config.json and environment variables.
"""

import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer()
console = Console()


@app.command("show")
def show_config() -> None:
    """Show current configuration.

    Displays environment variables and config file settings.

    Examples:
        nimbus config show
    """
    from nimbus.config import get_config

    config = get_config(_force_reload=True)

    table = Table(title="Nimbus Configuration")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")
    table.add_column("Source", style="dim")

    table.add_row("default_model", config.default_model, "config")
    table.add_row("agent_profile", config.agent_profile, "config")
    table.add_row("max_tokens", str(config.max_tokens), "config")
    table.add_row("timeout", str(config.timeout), "config")
    table.add_row("server_port", str(config.server_port), "config")
    table.add_row("anthropic_use_oauth", str(config.anthropic_use_oauth), "config")
    table.add_row("codex_use_oauth", str(config.codex_use_oauth), "config")

    console.print(table)

    # Also show environment variables
    console.print("\n[bold]Environment Variables[/bold]")
    env_vars = [
        ("NIMBUS_MODEL", os.environ.get("NIMBUS_MODEL", "[dim]not set[/dim]")),
        ("NIMBUS_MAX_TOKENS", os.environ.get("NIMBUS_MAX_TOKENS", "[dim]not set[/dim]")),
        ("NIMBUS_SERVER_PORT", os.environ.get("NIMBUS_SERVER_PORT", "[dim]not set[/dim]")),
        ("NIMBUS_AGENT_PROFILE", os.environ.get("NIMBUS_AGENT_PROFILE", "[dim]not set[/dim]")),
        ("GEMINI_API_KEY", "[dim]set[/dim]" if os.environ.get("GEMINI_API_KEY") else "[dim]not set[/dim]"),
    ]
    for name, value in env_vars:
        console.print(f"  {name}: {value}")


@app.command("get")
def get_config_cmd(
    key: str = typer.Argument(
        ...,
        help="Configuration key to get",
    ),
) -> None:
    """Get a specific configuration value.

    Examples:
        nimbus config get default_model
        nimbus config get server_port
    """
    from nimbus.config import get_config

    config = get_config(_force_reload=True)

    if not hasattr(config, key):
        console.print(f"[red]Unknown config key: {key}[/red]")
        raise typer.Exit(code=1)

    console.print(str(getattr(config, key)))


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
) -> None:
    """Set a configuration value.

    Note: Persistent storage (SQLite) has been removed.
    Use environment variables or edit ~/.nimbus/config.json directly.

    Examples:
        export NIMBUS_MODEL=anthropic/claude-sonnet-4
        export NIMBUS_MAX_TOKENS=8192
    """
    console.print(
        "[yellow]Persistent config storage is not available in this version.[/yellow]\n"
        "Please set configuration via environment variables or edit ~/.nimbus/config.json.\n\n"
        "Examples:\n"
        f"  export NIMBUS_MODEL={value}\n"
        "  Edit ~/.nimbus/config.json"
    )


@app.command("reset")
def reset_config(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Reset configuration.

    Note: Persistent storage (SQLite) has been removed.
    Delete ~/.nimbus/config.json to reset to defaults.

    Examples:
        nimbus config reset
    """
    console.print(
        "[yellow]Persistent config storage is not available in this version.[/yellow]\n"
        "To reset, delete ~/.nimbus/config.json"
    )


if __name__ == "__main__":
    app()
