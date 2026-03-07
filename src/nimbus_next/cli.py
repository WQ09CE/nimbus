"""
CLI — Interactive terminal interface for Nimbus Next.

A minimal REPL that demonstrates the full pipeline:
User input → AgentOS → VCPU loop → Tool execution → Output

Usage:
    python -m nimbus_next.cli
    python -m nimbus_next.cli --model claude-sonnet-4-20250514 --provider anthropic
    python -m nimbus_next.cli "列出当前目录的文件"  # one-shot mode
"""

import argparse
import asyncio
import sys
from typing import Optional

from .agent import AgentConfig, AgentOS
from .protocol import Event


# =============================================================================
# Terminal Colors
# =============================================================================


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    BLUE = "\033[34m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    CYAN = "\033[36m"


# =============================================================================
# Event Printer
# =============================================================================


def print_event(event: Event) -> None:
    """Print tool events in a readable format."""
    if event.type == "TOOL_STARTED":
        tool = event.data.get("tool", "?")
        print(f"  {Colors.DIM}⚙ {tool}...{Colors.RESET}", flush=True)
    elif event.type == "TOOL_FINISHED":
        tool = event.data.get("tool", "?")
        status = event.data.get("status", "?")
        ms = event.data.get("duration_ms", 0)
        color = Colors.GREEN if status == "OK" else Colors.RED
        print(f"  {Colors.DIM}⚙ {tool} → {color}{status}{Colors.RESET} {Colors.DIM}({ms}ms){Colors.RESET}", flush=True)


def print_step_event(event: dict) -> None:
    """Print step events from the stream."""
    if event.get("type") == "step":
        actions = event.get("actions", [])
        for a in actions:
            kind = a.get("kind", "")
            name = a.get("name", "")
            if kind == "THOUGHT":
                pass  # Thoughts shown in final output
            elif kind == "TOOL_CALL":
                pass  # Shown via event_callback
    elif event.get("type") == "final":
        result = event.get("result")
        if result and result.output:
            print(f"\n{Colors.GREEN}{Colors.BOLD}Result:{Colors.RESET}")
            print(result.output)


# =============================================================================
# CLI
# =============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nimbus Next — Minimal Agent OS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("goal", nargs="?", help="One-shot goal (skip REPL)")
    parser.add_argument("--model", default="gpt-4o", help="LLM model name")
    parser.add_argument("--provider", default="openai", choices=["openai", "anthropic"])
    parser.add_argument("--max-iterations", type=int, default=50)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--system-prompt", default="", help="Custom system prompt")
    return parser.parse_args()


async def run_one_shot(agent: AgentOS, goal: str) -> None:
    """Execute a single goal and exit."""
    print(f"{Colors.CYAN}Goal:{Colors.RESET} {goal}\n")
    async for event in agent.stream(goal):
        print_step_event(event)


async def run_repl(agent: AgentOS) -> None:
    """Interactive REPL loop."""
    print(f"{Colors.BOLD}Nimbus Next{Colors.RESET} — Interactive Agent OS")
    print(f"{Colors.DIM}Type your goal. Commands: /quit, /clear{Colors.RESET}\n")

    while True:
        try:
            user_input = input(f"{Colors.BLUE}> {Colors.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Colors.DIM}Goodbye!{Colors.RESET}")
            break

        if not user_input:
            continue
        if user_input in ("/quit", "/exit", "/q"):
            print(f"{Colors.DIM}Goodbye!{Colors.RESET}")
            break
        if user_input == "/clear":
            print("\033[2J\033[H", end="")  # Clear terminal
            continue

        print()
        async for event in agent.stream(user_input):
            print_step_event(event)
        print()


def main() -> None:
    args = parse_args()

    config = AgentConfig(
        model=args.model,
        provider=args.provider,
        max_iterations=args.max_iterations,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )

    agent = AgentOS(
        config=config,
        system_prompt=args.system_prompt,
        event_callback=print_event,
    )

    if args.goal:
        asyncio.run(run_one_shot(agent, args.goal))
    else:
        asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()
