"""
Nimbus V2 TUI CLI - Command Line Entry Point

Usage:
    # Start TUI with Gemini LLM
    python -m nimbus.tui.cli

    # Specify model
    python -m nimbus.tui.cli --model gemini-2.0-flash

    # Demo mode (no LLM)
    python -m nimbus.tui.cli --demo
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from nimbus.tui.config import DashboardConfig


def load_config() -> dict[str, Any]:
    """Load config from ~/.nimbus/config.json"""
    config_path = Path.home() / ".nimbus" / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load config: {e}")
    return {}


def get_api_key_from_config(provider: str = "gemini") -> str | None:
    """Get API key from config file."""
    config = load_config()
    providers = config.get("llm", {}).get("providers", {})
    provider_config = providers.get(provider, {})
    return provider_config.get("api_key")


def get_model_from_config(provider: str = "gemini") -> str | None:
    """Get model from config file."""
    config = load_config()
    providers = config.get("llm", {}).get("providers", {})
    provider_config = providers.get(provider, {})
    return provider_config.get("model")


def create_agent_os_with_gemini(model: str | None = None, workspace: Path | None = None):
    """Create AgentOS with Gemini LLM and default tools."""
    from nimbus.agentos import create_agent_os
    from nimbus.llm import GeminiV2Client

    # Get API key: env var > config file
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or get_api_key_from_config("gemini")
    )
    if not api_key:
        print("Error: Gemini API key not found.")
        print("Set GEMINI_API_KEY env var or add to ~/.nimbus/config.json")
        sys.exit(1)

    # Get model: arg > config file > default
    if model is None:
        model = get_model_from_config("gemini") or "gemini-2.0-flash"

    # Create LLM client
    llm = GeminiV2Client(api_key=api_key, model=model)

    # Create AgentOS with default tools
    workspace = workspace or Path.cwd()
    agent_os = create_agent_os(
        llm_client=llm,
        system_rules="""You are a helpful coding assistant with access to tools.
When you need to interact with files or run commands, use the available tools.
Be concise and helpful.""",
        workspace=workspace,
        register_defaults=True,
    )

    return agent_os, llm


async def run_demo(use_textual: bool = True):
    """Run TUI in demo mode without LLM."""
    print("Starting Nimbus V2 TUI (Demo Mode)...")
    print("Note: No LLM connected. Use --help for usage info.\n")

    if use_textual:
        from nimbus.tui.textual_app import NimbusTUI
        app = NimbusTUI(agent_os=None, workspace=Path.cwd())
        await app.run_async()
    else:
        from nimbus.tui.simple_tui import SimpleTUI
        tui = SimpleTUI(agent_os=None, workspace=Path.cwd())
        await tui.run()


async def run_with_llm(model: str | None = None, workspace: Path | None = None, use_textual: bool = True):
    """Run TUI with real LLM connection."""
    # Resolve model from config if not specified
    if model is None:
        model = get_model_from_config("gemini") or "gemini-2.0-flash"

    print(f"Starting Nimbus V2 TUI with Gemini ({model})...")
    print(f"Workspace: {workspace or Path.cwd()}")
    print()

    try:
        agent_os, llm = create_agent_os_with_gemini(model=model, workspace=workspace)
    except Exception as e:
        print(f"Error creating AgentOS: {e}")
        sys.exit(1)

    try:
        if use_textual:
            from nimbus.tui.textual_app import NimbusTUI
            app = NimbusTUI(agent_os=agent_os, workspace=workspace or Path.cwd())
            await app.run_async()
        else:
            from nimbus.tui.simple_tui import SimpleTUI
            tui = SimpleTUI(agent_os=agent_os, workspace=workspace or Path.cwd())
            await tui.run()
    finally:
        # Cleanup
        await llm.close()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Nimbus V2 TUI Dashboard - AI Agent Terminal Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m nimbus.tui.cli                    # Start with Gemini
  python -m nimbus.tui.cli --model gemini-2.0-flash-lite  # Use different model
  python -m nimbus.tui.cli --demo             # Demo mode (no LLM)
  python -m nimbus.tui.cli --workspace /path  # Set workspace directory

Environment Variables:
  GEMINI_API_KEY or GOOGLE_API_KEY    Required for LLM connection
        """
    )

    parser.add_argument(
        "--model", "-m",
        default=None,
        help="Gemini model to use (default: from ~/.nimbus/config.json or gemini-2.0-flash)"
    )
    parser.add_argument(
        "--workspace", "-w",
        type=Path,
        default=None,
        help="Workspace directory (default: current directory)"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run in demo mode without LLM connection"
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Use simple TUI instead of Textual (fallback mode)"
    )

    args = parser.parse_args()

    use_textual = not args.simple

    if args.demo:
        asyncio.run(run_demo(use_textual=use_textual))
    else:
        asyncio.run(run_with_llm(model=args.model, workspace=args.workspace, use_textual=use_textual))


if __name__ == "__main__":
    main()
