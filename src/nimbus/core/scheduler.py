"""
Nimbus v2 Scheduler - DAG Task Scheduler

The Scheduler manages task execution in a Directed Acyclic Graph (DAG) structure:

    - Tracks task states: PENDING -> READY -> RUNNING -> SUCCEEDED/FAILED/CANCELLED
    - Resolves dependencies: tasks become READY when all dependencies complete
    - Supports concurrent execution of independent tasks
    - Handles cancellation with downstream propagation
    - Provides IPC for result injection

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                       Scheduler                              │
    │  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐     │
    │  │  DAG Store   │   │  Ready Queue │   │  Result Store│     │
    │  │  (tasks)     │   │  (to run)    │   │  (outputs)   │     │
    │  └──────────────┘   └──────────────┘   └──────────────┘     │
    │         │                  │                  │              │
    │         └──────────┬───────┴──────────┬──────┘              │
    │                    │                  │                      │
    │              submit_dag()       run_dag(executor)            │
    └─────────────────────────────────────────────────────────────┘

Key Responsibilities:
    - Manage Task lifecycle (state transitions)
    - DAG dependency resolution
    - Concurrent execution of ready tasks
    - Cancellation with downstream propagation
    - Event emission for observability
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional

from nimbus.core.protocol import Event, Fault, ToolResult

# =============================================================================
# Task State Machine
# =============================================================================

TaskState = Literal["PENDING", "READY", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]

# Valid state transitions
VALID_TRANSITIONS: Dict[TaskState, List[TaskState]] = {
    "PENDING": ["READY", "CANCELLED"],
    "READY": ["RUNNING", "CANCELLED"],
    "RUNNING": ["SUCCEEDED", "FAILED", "CANCELLED"],
    "SUCCEEDED": [],  # Terminal state
    "FAILED": [],  # Terminal state
    "CANCELLED": [],  # Terminal state
}


def is_terminal_state(state: TaskState) -> bool:
    """Check if a state is terminal (no further transitions)."""
    return state in ("SUCCEEDED", "FAILED", "CANCELLED")


def is_success_state(state: TaskState) -> bool:
    """Check if a state is a successful terminal state."""
    return state == "SUCCEEDED"


# =============================================================================
# Task Specification
# =============================================================================


@dataclass
class TaskSpec:
    """
    Specification for a task to be executed.

    Attributes:
        goal: The goal/objective for the task
        process_role: Expected execution role (e.g., "eye", "body", "mind")
        input: Input data for the task
        budget: Resource budget constraints (tokens, time, etc.)
    """

    goal: str
    process_role: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    budget: Dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Task
# =============================================================================


@dataclass
class Task:
    """
    A task in the DAG.

    Attributes:
        id: Unique task identifier
        spec: Task specification
        state: Current task state
        depends_on: List of task IDs this task depends on
        result: Task result (if completed)
        error: Error message (if failed)
        created_at: Creation timestamp (ms)
        started_at: Start timestamp (ms)
        finished_at: Completion timestamp (ms)
    """

    id: str
    spec: TaskSpec
    state: TaskState = "PENDING"
    depends_on: List[str] = field(default_factory=list)
    result: Optional[ToolResult] = None
    error: Optional[str] = None
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    started_at: Optional[int] = None
    finished_at: Optional[int] = None

    def transition_to(self, new_state: TaskState) -> bool:
        """
        Attempt to transition to a new state.

        Returns:
            True if transition was valid, False otherwise
        """
        if new_state in VALID_TRANSITIONS.get(self.state, []):
            self.state = new_state
            if new_state == "RUNNING":
                self.started_at = int(time.time() * 1000)
            elif is_terminal_state(new_state):
                self.finished_at = int(time.time() * 1000)
            return True
        return False


# =============================================================================
# DAG
# =============================================================================


@dataclass
class DAG:
    """
    A Directed Acyclic Graph of tasks.

    Attributes:
        id: Unique DAG identifier
        tasks: Map of task_id -> Task
        root_task_id: The root task that represents the overall goal
        created_at: Creation timestamp (ms)
    """

    id: str
    tasks: Dict[str, Task]
    root_task_id: str
    created_at: int = field(default_factory=lambda: int(time.time() * 1000))

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self.tasks.get(task_id)

    def get_downstream_tasks(self, task_id: str) -> List[str]:
        """Get all tasks that depend on the given task."""
        downstream = []
        for tid, task in self.tasks.items():
            if task_id in task.depends_on:
                downstream.append(tid)
        return downstream


# =============================================================================
# Scheduler Configuration
# =============================================================================


@dataclass
class SchedulerConfig:
    """
    Configuration for the Scheduler.

    Attributes:
        max_concurrent_tasks: Maximum number of tasks running concurrently
        default_timeout: Default timeout for task execution (seconds)
        emit_events: Whether to emit lifecycle events
    """

    max_concurrent_tasks: int = 10
    default_timeout: float = 300.0
    emit_events: bool = True


# =============================================================================
# Event Stream
# =============================================================================


class EventStream:
    """
    Simple event stream for emitting scheduler events.

    This is a lightweight implementation for testing and local use.
    Production systems should use a more robust event bus.
    """

    def __init__(self) -> None:
        self._events: List[Event] = []
        self._listeners: List[Callable[[Event], None]] = []

    def emit(self, event: Event) -> None:
        """Emit an event to all listeners."""
        self._events.append(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass  # Swallow listener errors

    def subscribe(self, listener: Callable[[Event], None]) -> None:
        """Subscribe to events."""
        self._listeners.append(listener)

    def get_events(self) -> List[Event]:
        """Get all emitted events."""
        return self._events.copy()

    def clear(self) -> None:
        """Clear all events."""
        self._events.clear()


# =============================================================================
# Executor Protocol
# =============================================================================

# Type alias for executor function
TaskExecutor = Callable[[Task], Awaitable[ToolResult]]


# =============================================================================
# Scheduler
# =============================================================================


class Scheduler:
    """
    DAG Task Scheduler.

    The Scheduler manages the execution of tasks in a DAG structure,
    handling dependencies, concurrency, and lifecycle management.

    Example:
        scheduler = Scheduler(config=SchedulerConfig())

        # Create a DAG
        dag = DAG(
            id="dag-1",
            tasks={
                "t1": Task(id="t1", spec=TaskSpec(goal="Task 1")),
                "t2": Task(id="t2", spec=TaskSpec(goal="Task 2"), depends_on=["t1"]),
            },
            root_task_id="t2"
        )

        # Submit and run
        await scheduler.submit_dag(dag)
        result = await scheduler.run_dag(dag.id, executor=my_executor)
    """

    def __init__(
        self,
        config: Optional[SchedulerConfig] = None,
        events: Optional[EventStream] = None,
    ):
        """
        Initialize the Scheduler.

        Args:
            config: Scheduler configuration
            events: Event stream for emitting events
        """
        self.config = config or SchedulerConfig()
        self.events = events or EventStream()

        # DAG storage
        self._dags: Dict[str, DAG] = {}

        # Result storage (for IPC)
        self._results: Dict[str, Dict[str, Any]] = {}  # dag_id -> task_id -> result

        # Running tasks tracking
        self._running_tasks: Dict[str, asyncio.Task] = {}  # task_id -> asyncio.Task

    # =========================================================================
    # DAG Management
    # =========================================================================

    async def submit_dag(self, dag: DAG) -> str:
        """
        Submit a DAG for execution.

        Validates the DAG and initializes task states.

        Args:
            dag: The DAG to submit

        Returns:
            The DAG ID

        Raises:
            ValueError: If the DAG is invalid
        """
        # Validate DAG
        self._validate_dag(dag)

        # Store DAG
        self._dags[dag.id] = dag

        # Initialize result store for this DAG
        self._results[dag.id] = {}

        # Initialize task states - tasks with no dependencies are READY
        for task_id, task in dag.tasks.items():
            if not task.depends_on:
                task.transition_to("READY")
            # Emit TASK_CREATED event
            self._emit_task_event("TASK_CREATED", dag.id, task)

        return dag.id

    async def run_dag(
        self,
        dag_id: str,
        executor: TaskExecutor,
    ) -> ToolResult:
        """
        Run a DAG until completion.

        Executes tasks in dependency order, running independent tasks
        concurrently up to the configured limit.

        Args:
            dag_id: The DAG ID to run
            executor: Function to execute individual tasks

        Returns:
            ToolResult from the root task
        """
        dag = self._dags.get(dag_id)
        if not dag:
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=f"DAG not found: {dag_id}",
                    retryable=False,
                ),
            )

        try:
            # Run until all tasks complete or failure
            while True:
                # Check if root task is done
                root_task = dag.get_task(dag.root_task_id)
                if root_task and is_terminal_state(root_task.state):
                    return root_task.result or ToolResult(
                        status="OK" if is_success_state(root_task.state) else "ERROR",
                        output="DAG completed",
                    )

                # Get ready tasks
                ready_tasks = self.get_ready_tasks(dag_id)

                if not ready_tasks and not self._running_tasks:
                    # No ready tasks and no running tasks - deadlock or all done
                    return self._get_dag_result(dag)

                # Execute ready tasks concurrently
                if ready_tasks:
                    await self._execute_ready_tasks(dag, ready_tasks, executor)

                # Small yield to allow other coroutines
                await asyncio.sleep(0)

        except asyncio.CancelledError:
            # Cancel all running tasks
            for task_id in list(self._running_tasks.keys()):
                self.cancel_task(dag_id, task_id)
            return ToolResult(
                status="CANCELLED",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message="DAG execution cancelled",
                    retryable=True,
                ),
            )

    # =========================================================================
    # Task State Management
    # =========================================================================

    def get_ready_tasks(self, dag_id: str) -> List[Task]:
        """
        Get all tasks that are ready to execute.

        A task is ready when:
        - It is in PENDING state with all dependencies succeeded, OR
        - It is already in READY state (and not yet running or completed)

        Args:
            dag_id: The DAG ID

        Returns:
            List of ready tasks
        """
        dag = self._dags.get(dag_id)
        if not dag:
            return []

        ready = []
        for task_id, task in dag.tasks.items():
            # Skip terminal or running states
            if is_terminal_state(task.state) or task.state == "RUNNING":
                continue

            if task.state == "PENDING":
                # Check if all dependencies are satisfied
                all_deps_succeeded = all(
                    dag.tasks.get(dep_id) is not None and dag.tasks[dep_id].state == "SUCCEEDED"
                    for dep_id in task.depends_on
                )
                if all_deps_succeeded:
                    task.transition_to("READY")
                    ready.append(task)
            elif task.state == "READY":
                ready.append(task)

        return ready

    def complete_task(
        self,
        dag_id: str,
        task_id: str,
        result: ToolResult,
    ) -> None:
        """
        Mark a task as completed.

        Args:
            dag_id: The DAG ID
            task_id: The task ID
            result: The task result
        """
        dag = self._dags.get(dag_id)
        if not dag:
            return

        task = dag.get_task(task_id)
        if not task:
            return

        # Can only complete non-terminal tasks
        if is_terminal_state(task.state):
            return

        # Store result
        task.result = result
        self._results[dag_id][task_id] = result

        # Ensure task is in RUNNING state before completing
        # (handles manual completion of READY tasks)
        if task.state == "READY":
            task.transition_to("RUNNING")

        # Transition state based on result
        if result.status == "OK":
            task.transition_to("SUCCEEDED")
        elif result.status == "CANCELLED":
            task.transition_to("CANCELLED")
            task.error = "Task was cancelled"
        else:
            task.transition_to("FAILED")
            task.error = result.fault.message if result.fault else "Unknown error"

        # Remove from running tasks
        if task_id in self._running_tasks:
            del self._running_tasks[task_id]

        # Emit TASK_FINISHED event
        self._emit_task_event("TASK_FINISHED", dag_id, task)

        # If failed or cancelled, propagate to downstream
        if task.state in ("FAILED", "CANCELLED"):
            self._cancel_downstream(dag, task_id)

    def cancel_task(self, dag_id: str, task_id: str) -> bool:
        """
        Cancel a task and its downstream dependencies.

        Args:
            dag_id: The DAG ID
            task_id: The task ID

        Returns:
            True if cancellation was successful
        """
        dag = self._dags.get(dag_id)
        if not dag:
            return False

        task = dag.get_task(task_id)
        if not task:
            return False

        # Can only cancel non-terminal tasks
        if is_terminal_state(task.state):
            return False

        # Cancel the asyncio task if running
        if task_id in self._running_tasks:
            self._running_tasks[task_id].cancel()
            del self._running_tasks[task_id]

        # Transition to cancelled
        task.transition_to("CANCELLED")
        task.error = "Task was cancelled"
        task.result = ToolResult(
            status="CANCELLED",
            fault=Fault(
                domain="KERNEL", code="SYSTEM_ERROR", message="Task was cancelled", retryable=True
            ),
        )

        # Emit TASK_FINISHED event
        self._emit_task_event("TASK_FINISHED", dag_id, task)

        # Cancel downstream tasks
        self._cancel_downstream(dag, task_id)

        return True

    def inject_result(
        self,
        dag_id: str,
        task_id: str,
        key: str,
        value: Any,
    ) -> None:
        """
        Inject a result into a task's output (IPC).

        This allows external processes to provide results to tasks.

        Args:
            dag_id: The DAG ID
            task_id: The task ID
            key: The result key
            value: The result value
        """
        if dag_id not in self._results:
            self._results[dag_id] = {}

        result_key = f"{task_id}.{key}"
        self._results[dag_id][result_key] = value

    def get_injected_result(
        self,
        dag_id: str,
        task_id: str,
        key: str,
    ) -> Optional[Any]:
        """
        Get an injected result.

        Args:
            dag_id: The DAG ID
            task_id: The task ID
            key: The result key

        Returns:
            The injected value, or None if not found
        """
        if dag_id not in self._results:
            return None

        result_key = f"{task_id}.{key}"
        return self._results[dag_id].get(result_key)

    def get_task_result(
        self,
        dag_id: str,
        task_id: str,
    ) -> Optional[ToolResult]:
        """
        Get a task's result.

        Args:
            dag_id: The DAG ID
            task_id: The task ID

        Returns:
            The task result, or None if not found
        """
        dag = self._dags.get(dag_id)
        if not dag:
            return None

        task = dag.get_task(task_id)
        if not task:
            return None

        return task.result

    # =========================================================================
    # DAG Accessors
    # =========================================================================

    def get_dag(self, dag_id: str) -> Optional[DAG]:
        """Get a DAG by ID."""
        return self._dags.get(dag_id)

    def get_task(self, dag_id: str, task_id: str) -> Optional[Task]:
        """Get a task by DAG ID and task ID."""
        dag = self._dags.get(dag_id)
        if dag:
            return dag.get_task(task_id)
        return None

    def get_dag_status(self, dag_id: str) -> Dict[str, Any]:
        """
        Get the status of a DAG.

        Returns:
            Dict with task counts by state
        """
        dag = self._dags.get(dag_id)
        if not dag:
            return {}

        status: Dict[str, int] = {
            "total": len(dag.tasks),
            "pending": 0,
            "ready": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
        }

        for task in dag.tasks.values():
            status[task.state.lower()] = status.get(task.state.lower(), 0) + 1

        return status

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _validate_dag(self, dag: DAG) -> None:
        """
        Validate a DAG.

        Checks:
        - Root task exists
        - All dependency references are valid
        - No cycles

        Raises:
            ValueError: If validation fails
        """
        # Check root task exists
        if dag.root_task_id not in dag.tasks:
            raise ValueError(f"Root task not found: {dag.root_task_id}")

        # Check all dependencies reference existing tasks
        for task_id, task in dag.tasks.items():
            for dep_id in task.depends_on:
                if dep_id not in dag.tasks:
                    raise ValueError(f"Task {task_id} depends on non-existent task: {dep_id}")

        # Check for cycles using topological sort
        visited: set = set()
        rec_stack: set = set()

        def has_cycle(task_id: str) -> bool:
            visited.add(task_id)
            rec_stack.add(task_id)

            task = dag.tasks[task_id]
            for dep_id in task.depends_on:
                if dep_id not in visited:
                    if has_cycle(dep_id):
                        return True
                elif dep_id in rec_stack:
                    return True

            rec_stack.remove(task_id)
            return False

        for task_id in dag.tasks:
            if task_id not in visited:
                if has_cycle(task_id):
                    raise ValueError("DAG contains a cycle")

    async def _execute_ready_tasks(
        self,
        dag: DAG,
        ready_tasks: List[Task],
        executor: TaskExecutor,
    ) -> None:
        """
        Execute ready tasks concurrently.

        Respects the max_concurrent_tasks limit.
        """
        # Calculate how many more tasks we can run
        available_slots = self.config.max_concurrent_tasks - len(self._running_tasks)
        tasks_to_run = ready_tasks[:available_slots]

        for task in tasks_to_run:
            if task.state == "READY":
                # Transition to RUNNING
                task.transition_to("RUNNING")

                # Emit TASK_ASSIGNED event
                self._emit_task_event("TASK_ASSIGNED", dag.id, task)

                # Create execution coroutine
                async def run_task(t: Task) -> None:
                    try:
                        result = await asyncio.wait_for(
                            executor(t), timeout=self.config.default_timeout
                        )
                        self.complete_task(dag.id, t.id, result)
                    except asyncio.TimeoutError:
                        self.complete_task(
                            dag.id,
                            t.id,
                            ToolResult(
                                status="TIMEOUT",
                                fault=Fault(
                                    domain="RESOURCE",
                                    code="TIMEOUT",
                                    message=f"Task {t.id} timed out",
                                    retryable=True,
                                ),
                            ),
                        )
                    except asyncio.CancelledError:
                        self.complete_task(
                            dag.id,
                            t.id,
                            ToolResult(
                                status="CANCELLED",
                                fault=Fault(
                                    domain="KERNEL",
                                    code="SYSTEM_ERROR",
                                    message="Task was cancelled",
                                    retryable=True,
                                ),
                            ),
                        )
                    except Exception as e:
                        self.complete_task(
                            dag.id,
                            t.id,
                            ToolResult(
                                status="ERROR",
                                fault=Fault(
                                    domain="KERNEL",
                                    code="SYSTEM_ERROR",
                                    message=str(e),
                                    retryable=False,
                                ),
                            ),
                        )

                # Start task
                asyncio_task = asyncio.create_task(run_task(task))
                self._running_tasks[task.id] = asyncio_task

        # Wait for at least one task to complete
        if self._running_tasks:
            done, _ = await asyncio.wait(
                self._running_tasks.values(), return_when=asyncio.FIRST_COMPLETED
            )

    def _cancel_downstream(self, dag: DAG, task_id: str) -> None:
        """
        Cancel all tasks that depend on the given task.

        Recursively cancels downstream tasks.
        """
        downstream = dag.get_downstream_tasks(task_id)
        for dep_id in downstream:
            task = dag.get_task(dep_id)
            if task and not is_terminal_state(task.state):
                # Cancel this task
                if dep_id in self._running_tasks:
                    self._running_tasks[dep_id].cancel()
                    del self._running_tasks[dep_id]

                task.transition_to("CANCELLED")
                task.error = f"Cancelled due to dependency {task_id} failure"
                task.result = ToolResult(
                    status="CANCELLED",
                    fault=Fault(
                        domain="KERNEL",
                        code="SYSTEM_ERROR",
                        message=f"Dependency {task_id} failed",
                        retryable=False,
                    ),
                )

                # Emit event
                self._emit_task_event("TASK_FINISHED", dag.id, task)

                # Recursively cancel downstream
                self._cancel_downstream(dag, dep_id)

    def _get_dag_result(self, dag: DAG) -> ToolResult:
        """
        Get the overall result of a DAG.

        Returns the root task's result or an error if not complete.
        """
        root_task = dag.get_task(dag.root_task_id)
        if not root_task:
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message="Root task not found",
                    retryable=False,
                ),
            )

        if root_task.result:
            return root_task.result

        if root_task.state == "SUCCEEDED":
            return ToolResult(status="OK", output="DAG completed successfully")
        elif root_task.state == "FAILED":
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=root_task.error or "Unknown error",
                    retryable=False,
                ),
            )
        elif root_task.state == "CANCELLED":
            return ToolResult(
                status="CANCELLED",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message="DAG was cancelled",
                    retryable=True,
                ),
            )
        else:
            return ToolResult(
                status="ERROR",
                fault=Fault(
                    domain="KERNEL",
                    code="SYSTEM_ERROR",
                    message=f"DAG incomplete, root task state: {root_task.state}",
                    retryable=False,
                ),
            )

    def _emit_task_event(
        self,
        event_type: str,
        dag_id: str,
        task: Task,
    ) -> None:
        """Emit a task lifecycle event."""
        if not self.config.emit_events:
            return

        self.events.emit(
            Event(
                type=event_type,  # type: ignore
                pid=dag_id,
                data={
                    "dag_id": dag_id,
                    "task_id": task.id,
                    "task_state": task.state,
                    "task_goal": task.spec.goal,
                    "error": task.error,
                },
            )
        )


# =============================================================================
# Factory Functions
# =============================================================================


def create_dag(
    tasks: List[Task],
    root_task_id: Optional[str] = None,
    dag_id: Optional[str] = None,
) -> DAG:
    """
    Create a DAG from a list of tasks.

    Args:
        tasks: List of tasks
        root_task_id: The root task ID (defaults to last task)
        dag_id: Optional DAG ID (auto-generated if not provided)

    Returns:
        A new DAG
    """
    if not tasks:
        raise ValueError("DAG must have at least one task")

    task_dict = {task.id: task for task in tasks}

    return DAG(
        id=dag_id or f"dag-{uuid.uuid4().hex[:8]}",
        tasks=task_dict,
        root_task_id=root_task_id or tasks[-1].id,
    )


def create_linear_dag(
    goals: List[str],
    dag_id: Optional[str] = None,
) -> DAG:
    """
    Create a linear DAG where each task depends on the previous one.

    Args:
        goals: List of goal strings
        dag_id: Optional DAG ID

    Returns:
        A new linear DAG
    """
    if not goals:
        raise ValueError("Goals list cannot be empty")

    tasks = []
    prev_id: Optional[str] = None

    for i, goal in enumerate(goals):
        task_id = f"t{i + 1}"
        task = Task(
            id=task_id,
            spec=TaskSpec(goal=goal),
            depends_on=[prev_id] if prev_id else [],
        )
        tasks.append(task)
        prev_id = task_id

    return create_dag(tasks, dag_id=dag_id)
