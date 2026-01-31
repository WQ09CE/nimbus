"""
Basic kernel functionality tests.

Tests the core Agent OS kernel components:
- AgentProcess (PCB)
- ProcessManager (scheduler)
- AgentOS (unified interface)
"""

import asyncio

import pytest

from nimbus.kernel import AgentOS, AgentProcess, ProcessManager, ProcessState
from nimbus.kernel.ipc import IPCMessage, MessageType, Signal


class TestProcessState:
    """Test ProcessState enum and transitions."""

    def test_process_state_values(self):
        """Verify all process states exist."""
        assert ProcessState.CREATED.value == "created"
        assert ProcessState.READY.value == "ready"
        assert ProcessState.RUNNING.value == "running"
        assert ProcessState.BLOCKED.value == "blocked"
        assert ProcessState.COMPLETED.value == "completed"
        assert ProcessState.FAILED.value == "failed"
        assert ProcessState.ZOMBIE.value == "zombie"
        assert ProcessState.CANCELLED.value == "cancelled"


class TestAgentProcess:
    """Test AgentProcess (PCB) functionality."""

    def test_create_process(self):
        """Test process creation with factory method."""
        proc = AgentProcess.create(role="test")

        assert proc.pid.startswith("proc_")
        assert proc.role == "test"
        assert proc.parent_pid is None
        assert proc.state == ProcessState.CREATED
        assert proc.depth == 0

    def test_create_with_parent(self):
        """Test process creation with parent."""
        proc = AgentProcess.create(
            role="child",
            parent_pid="proc_parent",
            depth=1,
        )

        assert proc.parent_pid == "proc_parent"
        assert proc.depth == 1

    def test_is_terminal(self):
        """Test terminal state detection."""
        proc = AgentProcess.create(role="test")

        # Non-terminal states
        assert not proc.is_terminal()

        proc.state = ProcessState.RUNNING
        assert not proc.is_terminal()

        proc.state = ProcessState.READY
        assert not proc.is_terminal()

        # Terminal states
        proc.state = ProcessState.COMPLETED
        assert proc.is_terminal()

        proc.state = ProcessState.FAILED
        assert proc.is_terminal()

        proc.state = ProcessState.CANCELLED
        assert proc.is_terminal()

        proc.state = ProcessState.ZOMBIE
        assert proc.is_terminal()

    def test_is_runnable(self):
        """Test runnable state detection."""
        proc = AgentProcess.create(role="test")

        assert proc.is_runnable()  # CREATED is runnable

        proc.state = ProcessState.READY
        assert proc.is_runnable()

        proc.state = ProcessState.RUNNING
        assert not proc.is_runnable()

        proc.state = ProcessState.COMPLETED
        assert not proc.is_runnable()

    def test_can_fork(self):
        """Test fork permission."""
        proc = AgentProcess.create(role="test")

        assert not proc.can_fork()  # CREATED cannot fork

        proc.state = ProcessState.RUNNING
        assert proc.can_fork()

        proc.state = ProcessState.COMPLETED
        assert not proc.can_fork()

    def test_token_budget(self):
        """Test token budget management."""
        proc = AgentProcess.create(role="test")
        proc.max_token_budget = 1000

        assert proc.has_budget()
        assert proc.consume_tokens(500)
        assert proc.token_usage == 500

        assert proc.consume_tokens(400)
        assert proc.token_usage == 900

        # Over budget
        assert not proc.consume_tokens(200)
        assert proc.token_usage == 900  # Unchanged

    def test_turn_counter(self):
        """Test turn counter management."""
        proc = AgentProcess.create(role="test")
        proc.max_turns = 3

        assert proc.has_turns()
        assert proc.increment_turn()
        assert proc.current_turn == 1

        assert proc.increment_turn()
        assert proc.increment_turn()
        assert proc.current_turn == 3

        # Max turns reached
        assert not proc.increment_turn()
        assert proc.current_turn == 3  # Unchanged

    def test_complete(self):
        """Test process completion."""
        proc = AgentProcess.create(role="test")
        proc.state = ProcessState.RUNNING

        proc.complete(result="Success!")

        assert proc.state == ProcessState.COMPLETED
        assert proc.exit_code == 0
        assert proc.result == "Success!"
        assert proc.finished_at is not None

    def test_fail(self):
        """Test process failure."""
        proc = AgentProcess.create(role="test")
        proc.state = ProcessState.RUNNING

        proc.fail("Something went wrong", exit_code=42)

        assert proc.state == ProcessState.FAILED
        assert proc.exit_code == 42
        assert proc.error == "Something went wrong"
        assert proc.finished_at is not None

    def test_cancel(self):
        """Test process cancellation."""
        proc = AgentProcess.create(role="test")
        proc.state = ProcessState.RUNNING

        proc.cancel()

        assert proc.state == ProcessState.CANCELLED
        assert proc.exit_code == -1
        assert proc.finished_at is not None

    def test_to_dict(self):
        """Test serialization."""
        proc = AgentProcess.create(role="test")
        proc.token_usage = 100

        data = proc.to_dict()

        assert data["pid"] == proc.pid
        assert data["role"] == "test"
        assert data["state"] == "created"
        assert data["token_usage"] == 100


class TestProcessManager:
    """Test ProcessManager functionality."""

    def test_init_process(self):
        """Test init process creation."""
        pm = ProcessManager()

        assert pm.process_count == 1
        assert pm.getpid() == "proc_init"

        init = pm.getproc("proc_init")
        assert init is not None
        assert init.role == "init"
        assert init.state == ProcessState.RUNNING

    def test_fork(self):
        """Test process forking."""
        pm = ProcessManager()

        pid = pm.fork(
            parent_pid="proc_init",
            role="child",
            task="Test task",
        )

        assert pid.startswith("proc_")
        assert pm.process_count == 2

        child = pm.getproc(pid)
        assert child is not None
        assert child.role == "child"
        assert child.parent_pid == "proc_init"
        assert child.depth == 1
        assert child.task_instruction == "Test task"

        # Check parent's children list
        init = pm.getproc("proc_init")
        assert pid in init.children

    def test_fork_with_options(self):
        """Test forking with all options."""
        pm = ProcessManager()

        pid = pm.fork(
            parent_pid="proc_init",
            role="worker",
            task="Work task",
            allowed_tools={"Read", "Write"},
            max_token_budget=10000,
            max_turns=20,
            system_prompt="You are a worker.",
            priority=5,
        )

        proc = pm.getproc(pid)
        assert proc.allowed_tools == {"Read", "Write"}
        assert proc.max_token_budget == 10000
        assert proc.max_turns == 20
        assert proc.system_prompt == "You are a worker."
        assert proc.priority == 5

    def test_fork_max_depth(self):
        """Test maximum depth limit."""
        pm = ProcessManager()

        # Fork chain to max depth
        current_pid = "proc_init"
        for i in range(ProcessManager.MAX_DEPTH):
            new_pid = pm.fork(
                parent_pid=current_pid,
                role=f"level_{i}",
                task="Deep task",
            )
            # Set to running so it can fork next level
            pm.getproc(new_pid).state = ProcessState.RUNNING
            current_pid = new_pid

        # Should fail at max depth + 1
        with pytest.raises(ValueError, match="Maximum process depth"):
            pm.fork(
                parent_pid=current_pid,
                role="too_deep",
                task="Too deep",
            )

    def test_fork_invalid_parent(self):
        """Test forking with invalid parent."""
        pm = ProcessManager()

        with pytest.raises(ValueError, match="not found"):
            pm.fork(
                parent_pid="proc_nonexistent",
                role="child",
                task="Task",
            )

    @pytest.mark.asyncio
    async def test_exec(self):
        """Test process execution."""
        pm = ProcessManager()

        pid = pm.fork(
            parent_pid="proc_init",
            role="test",
            task="Test",
        )

        proc = pm.getproc(pid)
        assert proc.state == ProcessState.CREATED

        await pm.exec(pid)

        # Without executor, should mock complete
        assert proc.state == ProcessState.COMPLETED

    @pytest.mark.asyncio
    async def test_exec_with_executor(self):
        """Test process execution with custom executor."""
        pm = ProcessManager()
        executed_pids = []

        async def test_executor(proc: AgentProcess):
            executed_pids.append(proc.pid)
            await asyncio.sleep(0.01)  # Simulate work
            proc.complete(result=f"Executed {proc.role}")

        pm.set_executor(test_executor)

        pid = pm.fork(
            parent_pid="proc_init",
            role="test",
            task="Test",
        )

        await pm.exec(pid)
        result = await pm.wait(pid)

        assert pid in executed_pids
        assert result["exit_code"] == 0
        assert result["result"] == "Executed test"

    @pytest.mark.asyncio
    async def test_wait(self):
        """Test waiting for process."""
        pm = ProcessManager()

        pid = pm.fork(
            parent_pid="proc_init",
            role="test",
            task="Test",
        )

        await pm.exec(pid)
        result = await pm.wait(pid)

        assert result["pid"] == pid
        assert result["exit_code"] == 0
        assert "result" in result

    @pytest.mark.asyncio
    async def test_wait_timeout(self):
        """Test wait timeout."""
        pm = ProcessManager()

        async def slow_executor(proc: AgentProcess):
            await asyncio.sleep(10)  # Very slow
            proc.complete()

        pm.set_executor(slow_executor)

        pid = pm.fork(
            parent_pid="proc_init",
            role="slow",
            task="Slow task",
        )

        await pm.exec(pid)

        with pytest.raises(asyncio.TimeoutError):
            await pm.wait(pid, timeout=0.1)

    def test_kill(self):
        """Test killing a process."""
        pm = ProcessManager()

        pid = pm.fork(
            parent_pid="proc_init",
            role="victim",
            task="Task",
        )

        assert pm.kill(pid)

        proc = pm.getproc(pid)
        assert proc.state == ProcessState.CANCELLED

    def test_kill_recursive(self):
        """Test recursive killing."""
        pm = ProcessManager()

        parent_pid = pm.fork(
            parent_pid="proc_init",
            role="parent",
            task="Parent",
        )

        # Make parent running so it can fork
        pm.getproc(parent_pid).state = ProcessState.RUNNING

        child_pid = pm.fork(
            parent_pid=parent_pid,
            role="child",
            task="Child",
        )

        pm.kill(parent_pid, recursive=True)

        assert pm.getproc(parent_pid).state == ProcessState.CANCELLED
        assert pm.getproc(child_pid).state == ProcessState.CANCELLED

    def test_kill_init_blocked(self):
        """Test that init process cannot be killed."""
        pm = ProcessManager()

        assert not pm.kill("proc_init")

        init = pm.getproc("proc_init")
        assert init.state == ProcessState.RUNNING

    def test_ps(self):
        """Test process listing."""
        pm = ProcessManager()

        pid1 = pm.fork(parent_pid="proc_init", role="a", task="A")
        pid2 = pm.fork(parent_pid="proc_init", role="b", task="B")

        all_procs = pm.ps()
        assert len(all_procs) == 3  # init + 2 children

        children = pm.ps(parent_pid="proc_init")
        assert len(children) == 2
        pids = [p["pid"] for p in children]
        assert pid1 in pids
        assert pid2 in pids

    def test_tree(self):
        """Test process tree formatting."""
        pm = ProcessManager()

        pid1 = pm.fork(parent_pid="proc_init", role="Brain", task="Think")
        pm.getproc(pid1).state = ProcessState.RUNNING

        pid2 = pm.fork(parent_pid=pid1, role="Coder", task="Code")
        pm.getproc(pid2).complete("Done")

        tree = pm.tree()

        assert "proc_init" in tree
        assert "Brain" in tree
        assert "Coder" in tree
        assert "[*]" in tree  # Running
        assert "[+]" in tree  # Completed


class TestAgentOS:
    """Test AgentOS unified interface."""

    @pytest.mark.asyncio
    async def test_spawn_and_wait(self):
        """Test basic spawn and wait."""
        kernel = AgentOS()

        # Spawn process
        pid = await kernel.spawn(role="test", goal="Test task")

        # Check process created
        processes = kernel.ps()
        assert len(processes) >= 2  # init + test

        # Wait for completion (mock executor completes immediately)
        result = await kernel.wait(pid)
        assert result["pid"] == pid
        assert result["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_spawn_with_options(self):
        """Test spawn with all options."""
        kernel = AgentOS()

        pid = await kernel.spawn(
            role="worker",
            goal="Do work",
            allowed_tools={"Read", "Write"},
            max_token_budget=5000,
            max_turns=10,
            system_prompt="You are a worker.",
            priority=3,
        )

        proc = kernel.getproc(pid)
        assert proc.role == "worker"
        assert proc.allowed_tools == {"Read", "Write"}
        assert proc.max_token_budget == 5000
        assert proc.max_turns == 10
        assert proc.system_prompt == "You are a worker."
        assert proc.priority == 3

    @pytest.mark.asyncio
    async def test_fork_hierarchy(self):
        """Test parent-child relationship."""
        kernel = AgentOS()

        # Spawn parent
        parent_pid = await kernel.spawn(role="parent", goal="Parent task")

        # Wait for parent to complete (or it can't fork)
        # Actually, we need to use process manager directly for child
        pm = kernel.process_manager
        pm.getproc(parent_pid).state = ProcessState.RUNNING

        # Fork child
        child_pid = pm.fork(
            parent_pid=parent_pid,
            role="child",
            task="Child task",
        )

        # Verify relationship
        ps_output = kernel.ps(parent_pid)
        assert len(ps_output) == 1
        assert ps_output[0]["pid"] == child_pid

    @pytest.mark.asyncio
    async def test_spawn_with_executor(self):
        """Test spawn with custom executor."""
        kernel = AgentOS()
        results = []

        async def custom_executor(proc: AgentProcess):
            results.append(proc.role)
            await asyncio.sleep(0.01)
            proc.complete(result=f"Done by {proc.role}")

        kernel.set_executor(custom_executor)

        pid = await kernel.spawn(role="worker", goal="Work")
        result = await kernel.wait(pid)

        assert "worker" in results
        assert result["result"] == "Done by worker"

    def test_kill(self):
        """Test killing processes."""
        kernel = AgentOS()

        # Can't use spawn (async), use process manager
        pid = kernel.process_manager.fork(
            parent_pid="proc_init",
            role="victim",
            task="Task",
        )

        assert kernel.kill(pid)
        assert kernel.getproc(pid).state == ProcessState.CANCELLED

    def test_tree(self):
        """Test process tree display."""
        kernel = AgentOS()

        pid = kernel.process_manager.fork(
            parent_pid="proc_init",
            role="Brain",
            task="Think",
        )

        tree = kernel.tree()
        assert "proc_init" in tree
        assert "Brain" in tree

    def test_getpid(self):
        """Test getting current PID."""
        kernel = AgentOS()
        assert kernel.getpid() == "proc_init"

    def test_process_count(self):
        """Test process count."""
        kernel = AgentOS()
        assert kernel.process_count == 1

        kernel.process_manager.fork(
            parent_pid="proc_init",
            role="a",
            task="A",
        )
        assert kernel.process_count == 2


class TestIPCMessage:
    """Test IPC message functionality."""

    def test_create_message(self):
        """Test basic message creation."""
        msg = IPCMessage.create(
            msg_type=MessageType.STATUS,
            from_pid="proc_a",
            to_pid="proc_b",
            payload={"status": "running"},
        )

        assert msg.msg_id.startswith("msg_")
        assert msg.msg_type == MessageType.STATUS
        assert msg.from_pid == "proc_a"
        assert msg.to_pid == "proc_b"
        assert msg.payload == {"status": "running"}

    def test_spawn_request(self):
        """Test spawn request message."""
        msg = IPCMessage.spawn_request(
            from_pid="proc_parent",
            to_pid="proc_init",
            role="Coder",
            task="Write code",
            max_budget=10000,
        )

        assert msg.msg_type == MessageType.SPAWN
        assert msg.payload["role"] == "Coder"
        assert msg.payload["task"] == "Write code"
        assert msg.payload["max_budget"] == 10000

    def test_result_message(self):
        """Test result message."""
        msg = IPCMessage.result_message(
            from_pid="proc_child",
            to_pid="proc_parent",
            result="Task completed",
            exit_code=0,
            correlation_id="req_123",
        )

        assert msg.msg_type == MessageType.RESULT
        assert msg.payload["result"] == "Task completed"
        assert msg.payload["exit_code"] == 0
        assert msg.correlation_id == "req_123"

    def test_signal_message(self):
        """Test signal message."""
        msg = IPCMessage.signal_message(
            from_pid="proc_parent",
            to_pid="proc_child",
            signal=Signal.SIGTERM,
        )

        assert msg.msg_type == MessageType.SIGNAL
        assert msg.payload["signal"] == "SIGTERM"

    def test_error_message(self):
        """Test error message."""
        msg = IPCMessage.error_message(
            from_pid="proc_child",
            to_pid="proc_parent",
            error="Something broke",
            error_type="RuntimeError",
        )

        assert msg.msg_type == MessageType.ERROR
        assert msg.payload["error"] == "Something broke"
        assert msg.payload["error_type"] == "RuntimeError"

    def test_to_dict(self):
        """Test message serialization."""
        msg = IPCMessage.create(
            msg_type=MessageType.STATUS,
            from_pid="proc_a",
            to_pid="proc_b",
        )

        data = msg.to_dict()
        assert data["msg_id"] == msg.msg_id
        assert data["msg_type"] == "status"
        assert data["from_pid"] == "proc_a"
        assert data["to_pid"] == "proc_b"
