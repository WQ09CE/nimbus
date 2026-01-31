import asyncio
import time
import sys
import os
from rich.live import Live
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.console import Console
from rich.text import Text
from rich.prompt import Prompt
from rich.align import Align

# Ensure nimbus source is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../src")))

from nimbus.v2.core.scheduler import Scheduler, DAG, Task, TaskSpec, create_dag
from nimbus.v2.core.protocol import ToolResult

class NimbusProConsole:
    def __init__(self):
        self.scheduler = Scheduler()
        self.console = Console()
        self.layout = Layout()
        self.events = []
        self.chat_history = []  # Store user commands and agent results
        self.current_dag_id = None
        self.is_processing = False
        
        # Subscribe to scheduler events
        self.scheduler.events.subscribe(self.handle_event)

    def handle_event(self, event):
        timestamp = time.strftime("%H:%M:%S")
        self.events.append(f"[{timestamp}] {event.type}: {event.data.get('task_id')} -> {event.data.get('task_state')}")
        if len(self.events) > 12:
            self.events.pop(0)

    def make_layout(self):
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="input_area", size=3)
        )
        self.layout["main"].split_row(
            Layout(name="chat", ratio=2),
            Layout(name="kernel", ratio=1)
        )

    def generate_chat_view(self):
        chat_render = Text()
        for msg in self.chat_history:
            if msg['role'] == 'user':
                chat_render.append(f"\n> {msg['text']}\n", style="bold yellow")
            else:
                # Result panel formatting
                chat_render.append(f"└─ [Agent Result]: ", style="bold green")
                chat_render.append(f"{msg['text']}\n", style="white")
        
        return Panel(chat_render, title="Mission Log (Execution & Results)", border_style="bright_blue", padding=(1, 2))

    def generate_kernel_view(self):
        dag = self.scheduler.get_dag(self.current_dag_id) if self.current_dag_id else None
        
        # DAG Table
        table = Table(expand=True, border_style="cyan", box=None)
        table.add_column("Task", style="dim")
        table.add_column("State")
        
        if dag:
            for tid, task in dag.tasks.items():
                state_color = "green" if task.state == "SUCCEEDED" else "yellow"
                if task.state == "RUNNING": state_color = "bold pulse blue"
                table.add_row(tid, f"[{state_color}]{task.state}[/{state_color}]")
        else:
            table.add_row("-", "Kernel Idle")

        # Events text
        event_text = Text()
        for e in self.events:
            event_text.append(e + "\n", style="dim cyan")

        kernel_layout = Layout()
        kernel_layout.split(
            Layout(Panel(table, title="Active DAG")),
            Layout(Panel(event_text, title="Event Bus Trace"))
        )
        return kernel_layout

    def generate_header(self):
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            "[bold cyan]NIMBUS V2[/bold cyan]",
            "[bold white]AGENT OPERATING SYSTEM[/bold white]",
            f"[dim]{time.strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
        )
        return Panel(grid, style="on blue")

    async def mock_executor(self, task):
        """Simulate real agent work and return ToolResult."""
        await asyncio.sleep(1.2)
        return ToolResult(status="OK", output=f"Task '{task.spec.goal}' executed successfully.")

    async def run_task(self, user_cmd):
        self.is_processing = True
        self.chat_history.append({"role": "user", "text": user_cmd})
        
        self.current_dag_id = f"job-{int(time.time())}"
        
        # Create a simple 3-step DAG for the command
        tasks = [
            Task(id="SCAN", spec=TaskSpec(goal=f"Context scan for: {user_cmd}")),
            Task(id="PLAN", spec=TaskSpec(goal=f"Architecture plan"), depends_on=["SCAN"]),
            Task(id="CODE", spec=TaskSpec(goal=f"Implementation"), depends_on=["PLAN"])
        ]
        dag = create_dag(tasks, root_task_id="CODE", dag_id=self.current_dag_id)
        
        await self.scheduler.submit_dag(dag)
        result = await self.scheduler.run_dag(dag.id, executor=self.mock_executor)
        
        # Display Final Result
        final_output = result.output if result.status == "OK" else f"Error: {result.status}"
        self.chat_history.append({"role": "agent", "text": final_output})
        
        if len(self.chat_history) > 10: self.chat_history.pop(0)
        self.is_processing = False

    async def main_loop(self):
        self.make_layout()
        with Live(self.layout, refresh_per_second=4, screen=True) as live:
            while True:
                self.layout["header"].update(self.generate_header())
                self.layout["chat"].update(self.generate_chat_view())
                self.layout["kernel"].update(self.generate_kernel_view())
                
                prompt_text = "[bold yellow]Ready for mission[/bold yellow] (Type command and press Enter)"
                if self.is_processing:
                    prompt_text = "[bold pulse blue]Kernel busy - Executing DAG...[/bold pulse blue]"
                
                self.layout["input_area"].update(Panel(Align.center(prompt_text), border_style="white"))
                
                await asyncio.sleep(0.1)

    async def start(self):
        # Start UI
        asyncio.create_task(self.main_loop())
        
        # Start Input Listener (using thread to avoid blocking event loop)
        while True:
            await asyncio.sleep(0.5)
            if not self.is_processing:
                try:
                    # Clear terminal input line properly
                    user_input = await asyncio.to_thread(input, "")
                    if user_input.strip():
                        if user_input.lower() in ['exit', 'quit']:
                            break
                        await self.run_task(user_input)
                except EOFError:
                    break

if __name__ == "__main__":
    app = NimbusProConsole()
    asyncio.run(app.start())
