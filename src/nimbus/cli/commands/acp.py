"""Nimbus ACP CLI Command.

Start Nimbus as an ACP (Agent Client Protocol) agent over stdio.

Usage:
    nimbus acp
    nimbus acp --cwd /path/to/project
    nimbus acp --model qwen3:8b --url http://localhost:11434
"""

import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

app = typer.Typer(invoke_without_command=True)
console = Console(stderr=True)  # Use stderr for messages, stdout for JSON-RPC


@app.callback(invoke_without_command=True)
def acp(
    ctx: typer.Context,
    cwd: Optional[Path] = typer.Option(
        None,
        "--cwd",
        "-c",
        help="Working directory for the agent",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help="LLM model name (e.g., claude-3-5-sonnet-20241022, qwen3:8b)",
        envvar="NIMBUS_MODEL",
    ),
    url: Optional[str] = typer.Option(
        None,
        "--url",
        "-u",
        help="LLM API URL (e.g., http://localhost:11434 for Ollama)",
        envvar="NIMBUS_LLM_URL",
    ),
    system_prompt: Optional[str] = typer.Option(
        None,
        "--system-prompt",
        "-s",
        help="Custom system prompt for the agent",
    ),
    api_key_env: str = typer.Option(
        "ANTHROPIC_API_KEY",
        "--api-key-env",
        help="Environment variable name for API key",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        "-d",
        help="Enable debug logging",
    ),
) -> None:
    """Start Nimbus as an ACP agent.

    This runs Nimbus in ACP (Agent Client Protocol) mode, communicating
    via stdio JSON-RPC. Use this to connect Nimbus to ACP-compatible
    clients like Toad TUI.

    The agent reads JSON-RPC requests from stdin and writes responses
    to stdout. All log messages are sent to stderr.

    Examples:

        # Start with defaults (current directory, Claude model)
        nimbus acp

        # Start with specific working directory
        nimbus acp --cwd /path/to/project

        # Use local Ollama model
        nimbus acp --model qwen3:8b --url http://localhost:11434

        # Use with custom system prompt
        nimbus acp --system-prompt "You are a helpful coding assistant"

    Environment Variables:
        NIMBUS_MODEL: Default LLM model
        NIMBUS_LLM_URL: Default LLM API URL
        ANTHROPIC_API_KEY: API key for Anthropic models
    """
    if ctx.invoked_subcommand is not None:
        return

    import logging

    # Configure logging level
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,  # Important: use stderr, not stdout
    )

    # Import here to avoid circular imports and speed up CLI startup
    from nimbus.acp import ACPConfig
    from nimbus.acp.server import run_server

    # Resolve working directory
    resolved_cwd = str(cwd) if cwd else os.getcwd()

    # Build configuration
    config = ACPConfig(
        cwd=resolved_cwd,
        llm_model=model,
        llm_url=url,
        system_prompt=system_prompt or "",
        api_key_env=api_key_env,
    )

    # Log startup info to stderr
    console.print("[bold green]Starting Nimbus ACP Agent[/bold green]", highlight=False)
    console.print(f"  Working directory: {resolved_cwd}", highlight=False)
    if model:
        console.print(f"  Model: {model}", highlight=False)
    if url:
        console.print(f"  API URL: {url}", highlight=False)
    console.print("[dim]Waiting for JSON-RPC requests on stdin...[/dim]", highlight=False)

    try:
        run_server(config)
    except KeyboardInterrupt:
        console.print("\n[yellow]ACP agent stopped.[/yellow]", highlight=False)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]", highlight=False)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
