"""Nimbus CLI Run Command.

One-shot execution mode for running a single task.
Used by Terminal-Bench and other automation tools.

Usage:
    nimbus run "fix the bug in main.py"
    nimbus run --model anthropic/claude-sonnet-4 "analyze this code"
    nimbus run --max-iterations 20 "write tests for utils.py"
"""

import asyncio
from pathlib import Path
from typing import Optional

import typer


def run_command(
    instruction: str = typer.Argument(
        ...,
        help="The task instruction to execute",
    ),
    model: str = typer.Option(
        "google/gemini-3-flash-preview",
        "--model",
        "-m",
        help="Model to use (provider/model format)",
    ),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Working directory (defaults to current directory)",
    ),
    max_iterations: int = typer.Option(
        50,
        "--max-iterations",
        help="Maximum number of iterations",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output result as JSON",
    ),
) -> None:
    """
    Execute a single task in one-shot mode.

    This command creates an agent, runs the task, and exits.
    Designed for automation and benchmarking (e.g., Terminal-Bench).

    Examples:
        nimbus run "list all Python files"
        nimbus run --model openai/gpt-4 "fix the syntax error"
        nimbus run --workspace ./my-project "run the tests"
    """
    import json
    import logging
    from datetime import datetime, timezone

    # Setup logging
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)

    # Resolve workspace
    work_dir = workspace or Path.cwd()
    work_dir = work_dir.resolve()

    if not work_dir.exists():
        typer.echo(f"Error: Workspace does not exist: {work_dir}", err=True)
        raise typer.Exit(1)

    typer.echo("🚀 Nimbus Run Mode", err=True)
    typer.echo(f"   Model: {model}", err=True)
    typer.echo(f"   Workspace: {work_dir}", err=True)
    typer.echo(f"   Max iterations: {max_iterations}", err=True)
    typer.echo(f"   Task: {instruction[:100]}{'...' if len(instruction) > 100 else ''}", err=True)
    typer.echo("", err=True)

    # Run the task
    result = asyncio.run(
        _run_task(
            instruction=instruction,
            model=model,
            workspace=work_dir,
            max_iterations=max_iterations,
            verbose=verbose,
        )
    )

    if json_output:
        output = {
            "status": result["status"],
            "output": result.get("output"),
            "error": result.get("error"),
            "iterations": result.get("iterations", 0),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        typer.echo(json.dumps(output, indent=2))
    else:
        if result["status"] == "OK":
            typer.echo("✅ Task completed successfully", err=True)
            if result.get("output"):
                typer.echo("\n--- Result ---")
                typer.echo(result["output"])
        else:
            typer.echo(f"❌ Task failed: {result.get('error', 'Unknown error')}", err=True)
            raise typer.Exit(1)


async def _run_task(
    instruction: str,
    model: str,
    workspace: Path,
    max_iterations: int,
    verbose: bool,
) -> dict:
    """Run a single task asynchronously."""
    import os

    # Change to workspace directory
    original_dir = os.getcwd()
    os.chdir(workspace)

    try:
        # Import here to avoid circular imports
        from nimbus.agentos import AgentOS, AgentOSConfig
        from nimbus.core.runtime.vcpu import VCPUConfig

        # Create LLM adapter using factory (LiteLLM)
        from nimbus.adapters.llm_factory import create_llm_client

        # Create LLM client
        llm = await create_llm_client(model=model)

        # Start the adapter
        await llm.start()

        try:
            # Create AgentOS config with max_iterations
            vcpu_config = VCPUConfig(max_iterations=max_iterations)
            config = AgentOSConfig(
                vcpu_config=vcpu_config,
                workspace_info=f"Workspace: {workspace}",
            )

            # Create agent with default tools
            from nimbus.tools import register_default_tools

            agent = AgentOS(llm_client=llm, config=config)
            register_default_tools(agent, workspace=workspace)

            # Run the task
            result = await agent.run(instruction)

            return {
                "status": result.status,
                "output": result.output,
                "error": str(result.fault) if result.fault else None,
                "iterations": getattr(agent, "_iterations", 0),
            }
        finally:
            await llm.stop()

    except Exception as e:
        import traceback

        return {
            "status": "ERROR",
            "error": str(e),
            "traceback": traceback.format_exc() if verbose else None,
        }
    finally:
        os.chdir(original_dir)
