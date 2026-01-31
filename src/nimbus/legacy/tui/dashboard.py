"""
TUI Dashboard Main Controller

Provides TUIDashboard - the main controller for the Nimbus V2 TUI Dashboard.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

from rich.console import Console
from rich.live import Live

from nimbus.tui.config import DashboardConfig
from nimbus.tui.input_handler import InputHandler
from nimbus.tui.layout import LayoutManager
from nimbus.tui.state import DashboardState, StateManager
from nimbus.tui.widgets.chat import ChatPanel
from nimbus.tui.widgets.dag import DAGWidget
from nimbus.tui.widgets.memory import MemoryWidget
from nimbus.tui.widgets.process import ProcessWidget
from nimbus.tui.widgets.status import HeaderBar, StatusBar
from nimbus.tui.widgets.vcpu import VCPUWidget

if TYPE_CHECKING:
    from nimbus.agentos import AgentOS


class TUIDashboard:
    """
    Main TUI Dashboard controller.

    Responsibilities:
    - Coordinate all UI components
    - Manage Rich Live display
    - Handle user input asynchronously
    - Subscribe to AgentOS events

    Usage:
        from nimbus.agentos import create_agent_os

        # Create AgentOS
        llm = create_llm_client()
        agent_os = create_agent_os(llm)

        # Create and run dashboard
        dashboard = TUIDashboard(agent_os)
        await dashboard.run()

        # Or with configuration
        config = DashboardConfig(refresh_rate=8.0)
        dashboard = TUIDashboard(agent_os, config=config)
        await dashboard.run()
    """

    def __init__(
        self,
        agent_os: Optional["AgentOS"] = None,
        config: Optional[DashboardConfig] = None,
    ):
        """
        Initialize TUIDashboard.

        Args:
            agent_os: AgentOS instance to connect to (optional for demo mode)
            config: Dashboard configuration
        """
        self.os = agent_os
        self.config = config or DashboardConfig()

        # UI Components
        self.layout_manager = LayoutManager()
        self.state_manager = StateManager(
            agent_os=agent_os,
            max_messages=self.config.max_chat_history,
            debounce_ms=self.config.debounce_ms,
        )
        self.input_handler = InputHandler()

        # Widgets
        self.chat_panel = ChatPanel(max_history=self.config.max_chat_history)
        self.process_widget = ProcessWidget()
        self.dag_widget = DAGWidget()
        self.vcpu_widget = VCPUWidget()
        self.memory_widget = MemoryWidget()
        self.status_bar = StatusBar()
        self.header_bar = HeaderBar()

        # Console
        self.console = Console()

        # State
        self._running = False
        self._current_input = ""

    async def run(self) -> None:
        """
        Main event loop.

        This starts the dashboard and runs until the user exits.
        """
        self._running = True

        # Start input handler
        loop = asyncio.get_event_loop()
        self.input_handler.start(loop)

        # Add welcome message
        self.state_manager.add_system_message(
            "Welcome to Nimbus V2 TUI Dashboard. Type your command and press Enter."
        )

        try:
            with Live(
                self.layout_manager.layout,
                console=self.console,
                refresh_per_second=self.config.refresh_rate,
                screen=True,
            ):
                while self._running:
                    # 1. Check for user input
                    user_input = await self.input_handler.get_input()
                    if user_input:
                        if user_input.lower() in ("exit", "quit", "q"):
                            break
                        await self.handle_input(user_input)

                    # 2. Get current state
                    state = self.state_manager.get_state()

                    # 3. Render all components
                    self._update_layout(state)

                    # 4. Yield to other tasks
                    await asyncio.sleep(1 / self.config.refresh_rate)

        except KeyboardInterrupt:
            # Handle Ctrl+C gracefully
            pass
        finally:
            self._running = False
            self.input_handler.stop()

    async def handle_input(self, text: str) -> None:
        """
        Process user input.

        Args:
            text: User input text
        """
        # Handle special commands
        if text.startswith("/"):
            await self._handle_command(text)
            return

        # Add to chat
        self.state_manager.add_user_message(text)

        # Check if AgentOS is available
        if self.os is None:
            self.state_manager.add_system_message("AgentOS not connected. Running in demo mode.")
            return

        # Update state
        self.state_manager.set_processing(True, "Processing request...")

        try:
            # Run through AgentOS
            result = await self.os.run(text)

            # Add result to chat
            if result.status == "OK":
                output = result.output
                if isinstance(output, str):
                    self.state_manager.add_agent_message(output)
                else:
                    self.state_manager.add_agent_message(str(output))
            else:
                error_msg = "Unknown error"
                if result.fault:
                    error_msg = result.fault.message
                self.state_manager.add_system_message(f"Error: {error_msg}")

        except Exception as e:
            self.state_manager.add_system_message(f"Error: {str(e)}")

        finally:
            self.state_manager.set_processing(False)

    async def _handle_command(self, cmd: str) -> None:
        """Handle special commands starting with /."""
        cmd = cmd.strip().lower()

        if cmd == "/help":
            help_text = """Available commands:
/help    - Show this help
/clear   - Clear chat history
/status  - Show AgentOS status
/exit    - Exit dashboard"""
            self.state_manager.add_system_message(help_text)

        elif cmd == "/clear":
            self.state_manager.clear_messages()
            self.state_manager.add_system_message("Chat cleared.")

        elif cmd == "/status":
            if self.os is None:
                self.state_manager.add_system_message("AgentOS not connected.")
            else:
                state = self.os.get_state()
                status_text = f"""AgentOS Status:
- Processes: {len(state.get("processes", {}))}
- Tools: {len(state.get("tools", []))}
- Events: {state.get("event_count", 0)}"""
                self.state_manager.add_system_message(status_text)

        elif cmd in ("/exit", "/quit"):
            self._running = False

        else:
            self.state_manager.add_system_message(f"Unknown command: {cmd}")

    def _update_layout(self, state: DashboardState) -> None:
        """Update the layout with current state."""
        self.layout_manager.update(
            header=self.header_bar.render(),
            chat=self.chat_panel.render(
                messages=state.messages,
                input_text=self._current_input,
            ),
            processes=self.process_widget.render(state.processes),
            dag=self.dag_widget.render(
                state.current_dag_id,
                state.dag_status,
                state.dag_tasks,
            ),
            vcpu=self.vcpu_widget.render(
                state.vcpu_iteration,
                state.vcpu_max_iterations,
                state.vcpu_is_running,
                state.vcpu_timing,
            ),
            memory=self.memory_widget.render(
                state.mmu_tokens,
                state.mmu_max_tokens,
                state.mmu_stack_depth,
            ),
            footer=self.status_bar.render(
                state.is_processing,
                state.status_text,
            ),
        )

    def stop(self) -> None:
        """Stop the dashboard."""
        self._running = False


# =============================================================================
# Demo/Test Entry Point
# =============================================================================


async def _demo_main() -> None:
    """Demo main function for testing without AgentOS."""
    print("Starting Nimbus V2 TUI Dashboard (Demo Mode)...")

    # Create dashboard without AgentOS (demo mode)
    config = DashboardConfig(refresh_rate=4.0)
    dashboard = TUIDashboard(config=config)

    # Add some demo messages
    dashboard.state_manager.add_system_message("Running in demo mode (no AgentOS connected)")

    # Simulate some state
    dashboard.state_manager._state.processes = {
        "proc-abc123": {"state": "RUNNING", "role": "eye", "goal": "Explore codebase"},
        "proc-def456": {"state": "PENDING", "role": "body", "goal": "Implement feature"},
    }
    dashboard.state_manager._state.current_dag_id = "dag-demo123"
    dashboard.state_manager._state.dag_status = {
        "total": 5,
        "succeeded": 2,
        "running": 1,
        "pending": 2,
        "failed": 0,
    }
    dashboard.state_manager._state.dag_tasks = {
        "SCAN": "SUCCEEDED",
        "PLAN": "SUCCEEDED",
        "CODE": "RUNNING",
        "TEST": "PENDING",
        "DEPLOY": "PENDING",
    }
    dashboard.state_manager._state.vcpu_iteration = 3
    dashboard.state_manager._state.vcpu_is_running = True
    dashboard.state_manager._state.mmu_tokens = 8200
    dashboard.state_manager._state.mmu_max_tokens = 128000
    dashboard.state_manager._state.mmu_stack_depth = 2

    await dashboard.run()


async def _real_main() -> None:
    """Real main function with AgentOS connection."""
    from nimbus.agentos import create_agent_os
    from nimbus.llm.anthropic import AnthropicLLMClient

    print("Starting Nimbus V2 TUI Dashboard...")

    # Create LLM client
    llm = AnthropicLLMClient()

    # Create AgentOS
    agent_os = create_agent_os(llm)

    # Create and run dashboard
    config = DashboardConfig(refresh_rate=4.0)
    dashboard = TUIDashboard(agent_os=agent_os, config=config)

    await dashboard.run()


if __name__ == "__main__":
    import sys

    # Check for --demo flag
    if "--demo" in sys.argv:
        asyncio.run(_demo_main())
    else:
        # Try to run with real AgentOS, fall back to demo
        try:
            asyncio.run(_real_main())
        except ImportError as e:
            print(f"Warning: Could not import AgentOS components: {e}")
            print("Running in demo mode...")
            asyncio.run(_demo_main())
        except Exception as e:
            print(f"Error: {e}")
            print("Running in demo mode...")
            asyncio.run(_demo_main())
