# TUI Dashboard Design for Nimbus V2 AgentOS

> Architecture Design Document - Version 1.0
> Date: 2026-01-29

## Summary

Design a Claude Code-style TUI Dashboard using Rich library for Nimbus V2 AgentOS. The dashboard provides real-time visibility into AgentOS processes, DAG execution, VCPU state, and memory usage while maintaining a responsive chat interface.

## Design

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TUIDashboard                                       │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                        LayoutManager                                    │ │
│  │   ┌──────────────────────────────────┬───────────────────────────────┐ │ │
│  │   │         ChatPanel (70%)          │      InfoPanel (30%)          │ │ │
│  │   │   ┌──────────────────────────┐   │   ┌─────────────────────────┐ │ │ │
│  │   │   │    MessageRenderer       │   │   │   ProcessWidget         │ │ │ │
│  │   │   │    - User messages       │   │   │   - Process list        │ │ │ │
│  │   │   │    - Agent responses     │   │   │   - State indicators    │ │ │ │
│  │   │   │    - Tool outputs        │   │   ├─────────────────────────┤ │ │ │
│  │   │   └──────────────────────────┘   │   │   DAGWidget             │ │ │ │
│  │   │   ┌──────────────────────────┐   │   │   - Task progress       │ │ │ │
│  │   │   │    InputWidget           │   │   │   - Dependency view     │ │ │ │
│  │   │   │    - Cursor              │   │   ├─────────────────────────┤ │ │ │
│  │   │   │    - History             │   │   │   VCPUWidget            │ │ │ │
│  │   │   └──────────────────────────┘   │   │   - Iteration count     │ │ │ │
│  │   └──────────────────────────────────┤   │   - Timing stats        │ │ │ │
│  │                                       │   ├─────────────────────────┤ │ │ │
│  │                                       │   │   MemoryWidget          │ │ │ │
│  │                                       │   │   - Token usage         │ │ │ │
│  │                                       │   │   - Stack depth         │ │ │ │
│  │                                       │   └─────────────────────────┘ │ │ │
│  │                                       └───────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                         StatusBar                                       │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────────────┤
│                         StateManager                                         │
│   - AgentOS state subscription                                               │
│   - Event stream aggregation                                                 │
│   - Debounced UI updates                                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                         InputHandler                                         │
│   - Thread-based stdin reader                                                │
│   - Command queue                                                            │
│   - History management                                                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Core Components

#### 1. TUIDashboard (Main Controller)

```python
@dataclass
class DashboardConfig:
    """Configuration for TUI Dashboard."""
    refresh_rate: float = 4.0           # UI refresh per second
    chat_ratio: float = 0.7             # Chat panel width ratio
    max_chat_history: int = 100         # Max messages to keep
    max_events: int = 50                # Max events to display
    debounce_ms: int = 100              # State update debounce


class TUIDashboard:
    """
    Main TUI Dashboard controller.

    Responsibilities:
    - Coordinate all UI components
    - Manage Rich Live display
    - Handle user input asynchronously
    - Subscribe to AgentOS events
    """

    def __init__(
        self,
        agent_os: AgentOS,
        config: Optional[DashboardConfig] = None,
    ):
        self.os = agent_os
        self.config = config or DashboardConfig()

        # UI Components
        self.layout_manager = LayoutManager()
        self.state_manager = StateManager(agent_os)
        self.input_handler = InputHandler()

        # Widgets
        self.chat_panel = ChatPanel()
        self.process_widget = ProcessWidget()
        self.dag_widget = DAGWidget()
        self.vcpu_widget = VCPUWidget()
        self.memory_widget = MemoryWidget()
        self.status_bar = StatusBar()

    async def run(self) -> None:
        """Main event loop."""
        ...

    async def handle_input(self, text: str) -> None:
        """Process user input."""
        ...
```

#### 2. LayoutManager

```python
class LayoutManager:
    """
    Manages Rich Layout structure.

    Layout Structure:
    ┌────────────────────────────────────────────────────────────────┐
    │ header (size=3)                                                │
    ├──────────────────────────────────────────────┬─────────────────┤
    │ main.chat (ratio=7)                          │ main.info       │
    │                                              │ (ratio=3)       │
    │                                              │ ┌─────────────┐ │
    │                                              │ │ processes   │ │
    │                                              │ ├─────────────┤ │
    │                                              │ │ dag         │ │
    │                                              │ ├─────────────┤ │
    │                                              │ │ vcpu        │ │
    │                                              │ ├─────────────┤ │
    │                                              │ │ memory      │ │
    │                                              │ └─────────────┘ │
    ├──────────────────────────────────────────────┴─────────────────┤
    │ footer (size=3)                                                │
    └────────────────────────────────────────────────────────────────┘
    """

    def __init__(self):
        self.layout = Layout()
        self._build_layout()

    def _build_layout(self) -> None:
        self.layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=3),
        )

        self.layout["main"].split_row(
            Layout(name="chat", ratio=7),
            Layout(name="info", ratio=3),
        )

        self.layout["info"].split(
            Layout(name="processes", ratio=2),
            Layout(name="dag", ratio=2),
            Layout(name="vcpu", ratio=2),
            Layout(name="memory", ratio=2),
        )

    def update(
        self,
        header: RenderableType,
        chat: RenderableType,
        processes: RenderableType,
        dag: RenderableType,
        vcpu: RenderableType,
        memory: RenderableType,
        footer: RenderableType,
    ) -> Layout:
        """Update all layout regions."""
        self.layout["header"].update(header)
        self.layout["chat"].update(chat)
        self.layout["processes"].update(processes)
        self.layout["dag"].update(dag)
        self.layout["vcpu"].update(vcpu)
        self.layout["memory"].update(memory)
        self.layout["footer"].update(footer)
        return self.layout
```

#### 3. StateManager

```python
@dataclass
class DashboardState:
    """Aggregated state for dashboard."""
    # AgentOS state
    processes: Dict[str, ProcessState]
    tools: List[str]
    event_count: int

    # Active DAG state (if any)
    current_dag_id: Optional[str]
    dag_status: Optional[Dict[str, int]]  # {pending, ready, running, ...}
    dag_tasks: Optional[Dict[str, TaskState]]

    # Active VCPU state (from current process)
    vcpu_iteration: int
    vcpu_max_iterations: int
    vcpu_is_running: bool
    vcpu_timing: Dict[str, int]  # {think_ms, decode_ms, execute_ms}

    # Memory state
    mmu_tokens: int
    mmu_max_tokens: int
    mmu_stack_depth: int

    # Chat state
    messages: List[ChatMessage]
    is_processing: bool


class StateManager:
    """
    Manages dashboard state by subscribing to AgentOS events.

    Key Design:
    - Subscribe to EventStream for real-time updates
    - Poll component states for detailed info
    - Debounce updates to prevent UI flicker
    """

    def __init__(self, agent_os: AgentOS):
        self.os = agent_os
        self._state = DashboardState(...)
        self._last_update = 0.0
        self._subscribers: List[Callable] = []

        # Subscribe to AgentOS events
        self.os._events.subscribe(self._handle_event)

    def _handle_event(self, event: Event) -> None:
        """Handle incoming event and update state."""
        match event.type:
            case "PROC_SPAWNED":
                self._update_processes()
            case "PROC_FINISHED":
                self._update_processes()
            case "TASK_ASSIGNED" | "TASK_FINISHED":
                self._update_dag(event.data.get("dag_id"))
            case "STEP_STARTED":
                self._update_vcpu(event.pid)
            case "TOOL_STARTED" | "TOOL_FINISHED":
                self._update_vcpu(event.pid)

        self._notify_subscribers()

    def _update_processes(self) -> None:
        """Refresh process list from AgentOS."""
        state = self.os.get_state()
        self._state.processes = state["processes"]

    def _update_dag(self, dag_id: Optional[str]) -> None:
        """Refresh DAG status from Scheduler."""
        if dag_id:
            self._state.current_dag_id = dag_id
            self._state.dag_status = self.os._scheduler.get_dag_status(dag_id)
            dag = self.os._scheduler.get_dag(dag_id)
            if dag:
                self._state.dag_tasks = {
                    tid: task.state for tid, task in dag.tasks.items()
                }

    def _update_vcpu(self, pid: str) -> None:
        """Refresh VCPU state from active process."""
        process = self.os.get_process(pid)
        if process and process.vcpu:
            vcpu_state = process.vcpu.get_state()
            self._state.vcpu_iteration = vcpu_state["iteration"]
            self._state.vcpu_is_running = vcpu_state["is_running"]

            # Get timing from last step
            if process.mmu:
                mmu_state = process.mmu.get_state()
                self._state.mmu_tokens = mmu_state["estimated_tokens"]
                self._state.mmu_stack_depth = mmu_state["stack_depth"]

    def get_state(self) -> DashboardState:
        """Get current dashboard state."""
        return self._state

    def subscribe(self, callback: Callable[[DashboardState], None]) -> None:
        """Subscribe to state changes."""
        self._subscribers.append(callback)
```

#### 4. ChatPanel

```python
@dataclass
class ChatMessage:
    """A message in the chat history."""
    role: Literal["user", "agent", "tool", "system"]
    content: str
    timestamp: datetime
    tool_name: Optional[str] = None
    tool_status: Optional[str] = None  # "running", "success", "error"


class ChatPanel:
    """
    Renders the chat conversation area.

    Visual Design:
    ┌─ Chat ─────────────────────────────────────────────┐
    │                                                     │
    │  > User: Help me analyze this code                 │
    │                                                     │
    │  * Agent: Let me analyze the code...               │
    │    - First, I'll read the file                     │
    │    - Then examine the structure                    │
    │                                                     │
    │  [Tool: Read] src/main.py                          │
    │  + Success (234 lines)                             │
    │                                                     │
    │  * Agent: The code contains...                     │
    │                                                     │
    └─────────────────────────────────────────────────────┘
    """

    def __init__(self, max_history: int = 100):
        self.messages: List[ChatMessage] = []
        self.max_history = max_history
        self._scroll_offset = 0

    def add_message(self, message: ChatMessage) -> None:
        """Add a message to history."""
        self.messages.append(message)
        if len(self.messages) > self.max_history:
            self.messages.pop(0)

    def render(self, height: int) -> Panel:
        """Render the chat panel."""
        text = Text()

        for msg in self.messages:
            self._render_message(text, msg)

        return Panel(
            text,
            title="Chat",
            border_style="bright_blue",
            padding=(1, 2),
        )

    def _render_message(self, text: Text, msg: ChatMessage) -> None:
        """Render a single message."""
        match msg.role:
            case "user":
                text.append(f"\n> ", style="bold yellow")
                text.append(f"User: ", style="bold yellow")
                text.append(f"{msg.content}\n", style="white")

            case "agent":
                text.append(f"\n* ", style="bold green")
                text.append(f"Agent: ", style="bold green")
                text.append(f"{msg.content}\n", style="white")

            case "tool":
                icon = "+" if msg.tool_status == "success" else "-"
                style = "green" if msg.tool_status == "success" else "red"
                text.append(f"\n[Tool: {msg.tool_name}] ", style="dim cyan")
                text.append(f"{msg.content}\n", style="dim")
                if msg.tool_status:
                    text.append(f"{icon} {msg.tool_status}\n", style=style)

            case "system":
                text.append(f"\n[System] ", style="dim magenta")
                text.append(f"{msg.content}\n", style="dim")
```

#### 5. Info Panel Widgets

```python
class ProcessWidget:
    """
    Displays process list.

    Visual:
    ┌─ Processes ─────────────────┐
    │ > proc-a1b2 [RUNNING] eye   │
    │   proc-c3d4 [PENDING] body  │
    │   proc-e5f6 [SUCCEEDED]     │
    └─────────────────────────────┘
    """

    def render(self, processes: Dict[str, Dict]) -> Panel:
        table = Table(box=None, expand=True, show_header=False)
        table.add_column("Indicator", width=1)
        table.add_column("PID")
        table.add_column("State")
        table.add_column("Role")

        state_colors = {
            "RUNNING": "bold green",
            "PENDING": "yellow",
            "SUCCEEDED": "dim green",
            "FAILED": "red",
            "CANCELLED": "dim red",
        }

        for pid, info in processes.items():
            state = info.get("state", "PENDING")
            role = info.get("role", "")
            indicator = ">" if state == "RUNNING" else " "

            table.add_row(
                Text(indicator, style="bold cyan"),
                Text(pid[:12], style="dim"),
                Text(f"[{state}]", style=state_colors.get(state, "white")),
                Text(role, style="dim cyan"),
            )

        return Panel(table, title="Processes", border_style="cyan")


class DAGWidget:
    """
    Displays DAG execution progress.

    Visual:
    ┌─ DAG: job-12345 ────────────┐
    │ Progress: ████████░░ 8/10   │
    │                             │
    │ SCAN ──> PLAN ──> CODE      │
    │  [OK]     [OK]    [RUN]     │
    └─────────────────────────────┘
    """

    def render(
        self,
        dag_id: Optional[str],
        status: Optional[Dict[str, int]],
        tasks: Optional[Dict[str, str]],
    ) -> Panel:
        if not dag_id:
            return Panel(
                Text("No active DAG", style="dim"),
                title="DAG",
                border_style="cyan",
            )

        content = Text()

        # Progress bar
        total = status.get("total", 0)
        succeeded = status.get("succeeded", 0)
        failed = status.get("failed", 0)
        running = status.get("running", 0)

        if total > 0:
            progress = (succeeded + failed) / total
            bar_width = 20
            filled = int(progress * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)
            content.append(f"Progress: {bar} {succeeded}/{total}\n", style="cyan")

        # Task status line
        if tasks:
            content.append("\n")
            for tid, state in tasks.items():
                state_icon = {
                    "SUCCEEDED": "[OK]",
                    "RUNNING": "[>>]",
                    "FAILED": "[XX]",
                    "PENDING": "[..]",
                    "READY": "[>>]",
                }.get(state, "[??]")

                state_style = {
                    "SUCCEEDED": "green",
                    "RUNNING": "bold yellow",
                    "FAILED": "red",
                }.get(state, "dim")

                content.append(f"{tid[:8]} ", style="dim")
                content.append(f"{state_icon} ", style=state_style)

        return Panel(
            content,
            title=f"DAG: {dag_id[:12]}",
            border_style="cyan",
        )


class VCPUWidget:
    """
    Displays VCPU execution state.

    Visual:
    ┌─ VCPU ──────────────────────┐
    │ Iteration: 3/50             │
    │ Status: RUNNING             │
    │                             │
    │ Timing:                     │
    │   Think:   1.2s             │
    │   Decode:  0.01s            │
    │   Execute: 0.5s             │
    └─────────────────────────────┘
    """

    def render(
        self,
        iteration: int,
        max_iterations: int,
        is_running: bool,
        timing: Dict[str, int],
    ) -> Panel:
        content = Text()

        # Iteration
        content.append(f"Iteration: ", style="dim")
        content.append(f"{iteration}", style="bold cyan")
        content.append(f"/{max_iterations}\n", style="dim")

        # Status
        status = "RUNNING" if is_running else "IDLE"
        status_style = "bold green" if is_running else "dim"
        content.append(f"Status: ", style="dim")
        content.append(f"{status}\n\n", style=status_style)

        # Timing
        if timing:
            content.append("Timing:\n", style="dim")
            for key, ms in timing.items():
                seconds = ms / 1000
                content.append(f"  {key.capitalize()}: ", style="dim")
                content.append(f"{seconds:.2f}s\n", style="cyan")

        return Panel(content, title="VCPU", border_style="cyan")


class MemoryWidget:
    """
    Displays memory/token usage.

    Visual:
    ┌─ Memory ────────────────────┐
    │ Tokens: ████████░░ 8.2K/16K │
    │ Stack:  2 frames            │
    │                             │
    │ [root] -> [explore] ->      │
    └─────────────────────────────┘
    """

    def render(
        self,
        tokens: int,
        max_tokens: int,
        stack_depth: int,
    ) -> Panel:
        content = Text()

        # Token usage bar
        if max_tokens > 0:
            ratio = tokens / max_tokens
            bar_width = 15
            filled = int(ratio * bar_width)
            bar = "█" * filled + "░" * (bar_width - filled)

            tokens_k = tokens / 1000
            max_k = max_tokens / 1000

            bar_style = "green" if ratio < 0.7 else ("yellow" if ratio < 0.9 else "red")

            content.append(f"Tokens: ")
            content.append(f"{bar} ", style=bar_style)
            content.append(f"{tokens_k:.1f}K/{max_k:.0f}K\n", style="dim")

        # Stack depth
        content.append(f"Stack:  ", style="dim")
        content.append(f"{stack_depth} frames\n", style="cyan")

        return Panel(content, title="Memory", border_style="cyan")
```

#### 6. InputHandler

```python
class InputHandler:
    """
    Handles user input asynchronously.

    Design:
    - Uses a separate thread to read stdin
    - Queues input to main async loop
    - Supports command history
    - Non-blocking UI updates
    """

    def __init__(self):
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._history: List[str] = []
        self._history_index = 0
        self._current_input = ""
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the input handler thread."""
        self._running = True
        self._loop = loop
        self._thread = threading.Thread(target=self._input_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the input handler."""
        self._running = False

    def _input_loop(self) -> None:
        """Thread: Read input and queue to async loop."""
        while self._running:
            try:
                line = input()
                if line.strip():
                    # Queue to async loop
                    asyncio.run_coroutine_threadsafe(
                        self._queue.put(line),
                        self._loop
                    )
            except EOFError:
                break
            except Exception:
                pass

    async def get_input(self) -> Optional[str]:
        """Get next input from queue (non-blocking)."""
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def add_to_history(self, command: str) -> None:
        """Add command to history."""
        self._history.append(command)
        if len(self._history) > 100:
            self._history.pop(0)
        self._history_index = len(self._history)
```

#### 7. StatusBar

```python
class StatusBar:
    """
    Bottom status bar with state and shortcuts.

    Visual:
    ┌────────────────────────────────────────────────────────────────┐
    │ > Ready for input                           [Ctrl+C: Exit]    │
    └────────────────────────────────────────────────────────────────┘

    Or when processing:
    ┌────────────────────────────────────────────────────────────────┐
    │ [Processing] Executing task...              [Ctrl+C: Cancel]  │
    └────────────────────────────────────────────────────────────────┘
    """

    def render(self, is_processing: bool, status_text: str = "") -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=3)
        grid.add_column(justify="right", ratio=1)

        if is_processing:
            left = Text()
            left.append("[Processing] ", style="bold yellow")
            left.append(status_text or "Executing...", style="dim")
            right = Text("[Ctrl+C: Cancel]", style="dim red")
        else:
            left = Text()
            left.append("> ", style="bold green")
            left.append("Ready for input", style="dim")
            right = Text("[Ctrl+C: Exit]", style="dim")

        grid.add_row(left, right)

        return Panel(grid, style="on dark_blue")
```

### Data Flow

```
┌───────────────────────────────────────────────────────────────────────────┐
│                              Data Flow                                     │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  User Input (Thread)                                                      │
│       │                                                                   │
│       v                                                                   │
│  InputHandler.queue ──────> TUIDashboard.handle_input()                   │
│                                    │                                      │
│                                    v                                      │
│                              AgentOS.run(goal)                            │
│                                    │                                      │
│                 ┌──────────────────┼──────────────────┐                   │
│                 │                  │                  │                   │
│                 v                  v                  v                   │
│            Scheduler           VCPU              MMU                      │
│                 │                  │                  │                   │
│                 │    Events        │    Events        │                   │
│                 └────────┬─────────┴─────────┬────────┘                   │
│                          │                   │                            │
│                          v                   v                            │
│                    EventStream ────> StateManager                         │
│                                           │                               │
│                                           v                               │
│                                    DashboardState                         │
│                                           │                               │
│                                           v                               │
│                                    LayoutManager.update()                 │
│                                           │                               │
│                                           v                               │
│                                    Rich Live.refresh()                    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

### Main Event Loop

```python
class TUIDashboard:
    async def run(self) -> None:
        """Main event loop."""
        console = Console()

        # Start input handler
        loop = asyncio.get_event_loop()
        self.input_handler.start(loop)

        try:
            with Live(
                self.layout_manager.layout,
                console=console,
                refresh_per_second=self.config.refresh_rate,
                screen=True,
            ) as live:
                while True:
                    # 1. Check for user input
                    user_input = await self.input_handler.get_input()
                    if user_input:
                        if user_input.lower() in ("exit", "quit"):
                            break
                        await self.handle_input(user_input)

                    # 2. Get current state
                    state = self.state_manager.get_state()

                    # 3. Render all components
                    self.layout_manager.update(
                        header=self._render_header(),
                        chat=self.chat_panel.render(height=30),
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
                        footer=self.status_bar.render(state.is_processing),
                    )

                    # 4. Yield to other tasks
                    await asyncio.sleep(1 / self.config.refresh_rate)

        finally:
            self.input_handler.stop()

    async def handle_input(self, text: str) -> None:
        """Process user input."""
        # Add to chat
        self.chat_panel.add_message(ChatMessage(
            role="user",
            content=text,
            timestamp=datetime.now(),
        ))

        # Update state
        self.state_manager._state.is_processing = True

        try:
            # Run through AgentOS
            result = await self.os.run(text)

            # Add result to chat
            if result.status == "OK":
                self.chat_panel.add_message(ChatMessage(
                    role="agent",
                    content=str(result.output),
                    timestamp=datetime.now(),
                ))
            else:
                self.chat_panel.add_message(ChatMessage(
                    role="system",
                    content=f"Error: {result.fault.message if result.fault else 'Unknown'}",
                    timestamp=datetime.now(),
                ))

        finally:
            self.state_manager._state.is_processing = False
```

## Decisions

### Decision 1: Use Thread-based Input Handler

- **Decision**: Use a separate thread for stdin reading with asyncio queue for communication
- **Rationale**:
  - Python's `input()` is blocking and cannot be made async easily
  - Rich's Live display needs the main thread for rendering
  - Thread-safe queue allows clean async/sync boundary
- **Alternatives**:
  - Pure asyncio with `aioconsole` (adds dependency)
  - Polling stdin with select (complex, platform-specific)
- **Risk**: Thread synchronization complexity; mitigated by using queue

### Decision 2: Event-Driven State Updates

- **Decision**: Use AgentOS EventStream subscription for real-time updates
- **Rationale**:
  - AgentOS already emits events for all state changes
  - Avoids polling overhead
  - Consistent with V2 architecture design
- **Alternatives**: Periodic polling of component states
- **Risk**: Event flood during high activity; mitigated by debouncing

### Decision 3: Component-Based Widget Architecture

- **Decision**: Separate widget classes for each info panel section
- **Rationale**:
  - Single responsibility principle
  - Easy to test independently
  - Easy to add/remove widgets
- **Alternatives**: Monolithic render function
- **Risk**: Increased complexity; justified by maintainability

### Decision 4: Fixed Layout Ratios (70/30)

- **Decision**: Chat panel 70%, Info panel 30% of width
- **Rationale**:
  - Chat is primary interaction area
  - Info panels are supplementary monitoring
  - Matches Claude Code visual style
- **Alternatives**: Configurable/dynamic ratios
- **Risk**: May not fit all terminal sizes; consider minimum width checks

## Tradeoffs

1. **Simplicity vs Rich Features**: Chose simpler thread-based input over async stdin library to avoid new dependencies. This limits features like readline-style editing.

2. **Real-time vs Performance**: Chose event-driven updates with debouncing over pure polling. Higher accuracy but slightly more complexity.

3. **Modularity vs Coupling**: Chose tight integration with AgentOS internals (accessing `_events`, `_scheduler`) for better state visibility. This couples TUI to V2 implementation details.

4. **Screen Space vs Information Density**: Chose 4 info widgets with minimal detail over 2 detailed widgets. Better overview but less depth per component.

## Constraints

- **Technical Constraints**:
  - Must use Rich library only (no textual, urwid, etc.)
  - Must work with Python 3.10+
  - Must integrate with existing V2 component APIs

- **Performance Constraints**:
  - UI refresh rate: 4 Hz (configurable)
  - State update debounce: 100ms minimum
  - Max chat history: 100 messages (to limit memory)

- **Visual Constraints**:
  - Minimum terminal width: 120 columns recommended
  - Minimum terminal height: 30 rows recommended

## Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Input thread deadlock | Low | High | Use daemon thread with timeout |
| Event flood overwhelming UI | Medium | Medium | Debounce updates, drop old events |
| Terminal resize issues | Medium | Low | Rich handles resize; test edge cases |
| Memory leak from chat history | Low | Medium | Enforce max_history limit |
| State inconsistency | Medium | Medium | Use atomic state updates, snapshots |

## Evidence

Sources referenced:
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/tui/dashboard_v2.py` - Existing TUI implementation
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/v2/agentos.py:554-571` - AgentOS.get_state() method
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/v2/core/scheduler.py:628-652` - Scheduler.get_dag_status() method
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/v2/core/runtime/vcpu.py:608-617` - VCPU.get_state() method
- `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/v2/core/memory/mmu.py:395-404` - MMU.get_state() method

## Next Steps

1. **Implement base TUIDashboard class** with LayoutManager and main event loop
2. **Implement StateManager** with EventStream subscription
3. **Implement InputHandler** with thread-based stdin reading
4. **Implement ChatPanel** with message rendering
5. **Implement info panel widgets** (Process, DAG, VCPU, Memory)
6. **Add integration tests** with mock AgentOS
7. **Add E2E test** with real AgentOS execution

## Appendix: Module Structure

```
src/nimbus/tui/
├── __init__.py
├── dashboard.py          # TUIDashboard main class
├── layout.py             # LayoutManager
├── state.py              # StateManager, DashboardState
├── input.py              # InputHandler
├── widgets/
│   ├── __init__.py
│   ├── chat.py           # ChatPanel, ChatMessage
│   ├── process.py        # ProcessWidget
│   ├── dag.py            # DAGWidget
│   ├── vcpu.py           # VCPUWidget
│   ├── memory.py         # MemoryWidget
│   └── status.py         # StatusBar
└── utils.py              # Shared utilities
```

## Appendix: Interface Summary

```python
# Main entry point
async def main():
    llm = create_llm_client()
    agent_os = create_agent_os(llm)

    dashboard = TUIDashboard(agent_os)
    await dashboard.run()

# Or with configuration
config = DashboardConfig(
    refresh_rate=8.0,
    chat_ratio=0.65,
)
dashboard = TUIDashboard(agent_os, config=config)
```
