"""
CLI — Interactive terminal interface for Nimbus Next.

A minimal REPL that demonstrates the full pipeline:
User input → AgentOS → VCPU loop → Tool execution → Output

Features (pi-coding-agent inspired):
- Message queuing: type while the agent is working, messages are injected
  between steps (like pi's "message queuing while the agent is working")
- Streaming tool output: bash stdout displayed live
- Fine-grained events: text_delta, tool_call_start/done shown in real-time
- Partial results: Ctrl+C during execution shows what was done so far

Usage:
    python -m nimbus_next.cli
    python -m nimbus_next.cli --model claude-sonnet-4-20250514 --provider anthropic
    python -m nimbus_next.cli "列出当前目录的文件"  # one-shot mode
"""

import argparse
import asyncio
import sys
import threading
from typing import Optional

from .agent import AgentConfig, AgentOS
from .loop import RuntimeLoop
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
    MAGENTA = "\033[35m"


# =============================================================================
# Event Printer (fine-grained, pi-style)
# =============================================================================


def print_event(event: Event) -> None:
    """Print Gate-level tool events."""
    if event.type == "TOOL_STARTED":
        tool = event.data.get("tool", "?")
        print(f"  {Colors.DIM}⚙ {tool}...{Colors.RESET}", flush=True)
    elif event.type == "TOOL_FINISHED":
        tool = event.data.get("tool", "?")
        status = event.data.get("status", "?")
        ms = event.data.get("duration_ms", 0)
        color = Colors.GREEN if status == "OK" else Colors.RED
        print(f"  {Colors.DIM}⚙ {tool} → {color}{status}{Colors.RESET} {Colors.DIM}({ms}ms){Colors.RESET}", flush=True)
    elif event.type == "TOOL_CALL_DELTA":
        # Live streaming output from bash (pi-style)
        chunk = event.data.get("chunk", "")
        print(chunk, end="", flush=True)
    elif event.type == "INTERRUPTED":
        count = event.data.get("partial_results_count", 0)
        print(f"\n  {Colors.YELLOW}⚠ Interrupted ({count} partial results preserved){Colors.RESET}", flush=True)
    elif event.type == "CONTEXT_COMPACTED":
        print(f"  {Colors.DIM}📦 Context compacted{Colors.RESET}", flush=True)


def print_stream_event(event: dict) -> None:
    """Print fine-grained stream events (pi-style)."""
    etype = event.get("type")

    if etype == "text_delta":
        content = event.get("content", "")
        is_final = event.get("is_final", False)
        if content:
            print(content, end="" if not is_final else "\n", flush=True)

    elif etype == "tool_call_start":
        tool = event.get("tool", "?")
        args = event.get("args_preview", {})
        # Show a brief preview of what's being called
        preview_parts = []
        for k, v in args.items():
            preview_parts.append(f"{k}={v[:60]}")
        preview = ", ".join(preview_parts)
        print(f"\n  {Colors.CYAN}▶ {tool}{Colors.RESET}({Colors.DIM}{preview}{Colors.RESET})", flush=True)

    elif etype == "tool_call_done":
        tool = event.get("tool", "?")
        status = event.get("status", "?")
        ui = event.get("ui_detail")
        color = Colors.GREEN if status == "OK" else Colors.RED
        extra = ""
        if ui:
            # Show structured UI info (pi-style split result)
            if "exit_code" in ui and ui["exit_code"] != 0:
                extra = f" exit={ui['exit_code']}"
            if ui.get("truncated"):
                extra += " (truncated)"
            if ui.get("timed_out"):
                extra += " (timed out)"
        print(f"  {Colors.DIM}✓ {tool} → {color}{status}{Colors.RESET}{extra}", flush=True)

    elif etype == "message_queued":
        content = event.get("content", "")
        print(f"\n  {Colors.MAGENTA}📨 Queued: {content[:80]}{Colors.RESET}", flush=True)

    elif etype == "interrupted":
        result = event.get("result")
        partial = event.get("partial_results", [])
        print(f"\n{Colors.YELLOW}{Colors.BOLD}Interrupted.{Colors.RESET}", flush=True)
        if partial:
            print(f"  {Colors.DIM}{len(partial)} tool results preserved{Colors.RESET}", flush=True)
        if result and result.output:
            print(f"\n{result.output}", flush=True)

    elif etype == "context_compacted":
        count = event.get("compaction_count", "?")
        print(f"  {Colors.DIM}📦 Context compacted (#{count}){Colors.RESET}", flush=True)

    elif etype == "step":
        pass  # Step summaries are silent — fine-grained events handle the UI

    elif etype == "final":
        result = event.get("result")
        if result and result.output:
            # Only print if not already printed via text_delta
            output = str(result.output)
            if len(output) > 0 and not output.startswith("Execution interrupted"):
                print(f"\n{Colors.GREEN}{Colors.BOLD}Result:{Colors.RESET}")
                print(output)


# =============================================================================
# Input Thread for Message Queuing (pi-style)
# =============================================================================


def _start_input_thread(loop: RuntimeLoop, stop_event: threading.Event) -> threading.Thread:
    """Start a background thread that reads stdin and enqueues messages.

    This implements pi's "message queuing while the agent is working" pattern.
    While the agent is executing, user input goes into the message queue
    and gets injected between steps.
    """
    def _read_input() -> None:
        while not stop_event.is_set():
            try:
                # Use a short prompt to indicate queue mode
                line = input(f"{Colors.DIM}  (queue) >{Colors.RESET} ")
                line = line.strip()
                if line:
                    loop.message_queue.enqueue(line)
            except EOFError:
                break
            except KeyboardInterrupt:
                # Ctrl+C during input → interrupt the agent
                loop.request_interruption()
                break

    thread = threading.Thread(target=_read_input, daemon=True)
    thread.start()
    return thread


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


def _on_tool_output(tool_name: str, chunk: str) -> None:
    """Live-print streaming tool output (pi-style)."""
    print(chunk, end="", flush=True)


async def run_one_shot(agent: AgentOS, goal: str) -> None:
    """Execute a single goal and exit."""
    print(f"{Colors.CYAN}Goal:{Colors.RESET} {goal}\n")
    async for event in agent.stream(goal):
        print_stream_event(event)


async def run_repl(agent: AgentOS) -> None:
    """Interactive REPL with message queuing support.

    While the agent is working, user input goes into a message queue
    and gets injected between steps (pi-style message queuing).
    """
    print(f"{Colors.BOLD}Nimbus Next{Colors.RESET} — Interactive Agent OS")
    print(f"{Colors.DIM}Type your goal. Commands: /quit, /clear")
    print(f"While agent works, type to queue messages (injected between steps).{Colors.RESET}\n")

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
            print("\033[2J\033[H", end="")
            continue

        print()

        # Use stream_with_queue to enable message queuing (pi-style)
        loop = agent.stream_with_queue(user_input)
        stop_event = threading.Event()

        # Start background input thread for message queuing
        input_thread = _start_input_thread(loop, stop_event)

        try:
            async for event in loop.stream():
                print_stream_event(event)
        except KeyboardInterrupt:
            # Ctrl+C → interrupt agent, show partial results
            loop.request_interruption()
            if loop.partial_results:
                print(f"\n{Colors.YELLOW}Partial results ({len(loop.partial_results)} tool calls):{Colors.RESET}")
                for i, r in enumerate(loop.partial_results, 1):
                    preview = str(r.output)[:100] if r.output else "(no output)"
                    print(f"  {i}. [{r.status}] {preview}")
        finally:
            stop_event.set()

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
        on_tool_output=_on_tool_output,
    )

    if args.goal:
        asyncio.run(run_one_shot(agent, args.goal))
    else:
        asyncio.run(run_repl(agent))


if __name__ == "__main__":
    main()
