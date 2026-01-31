"""Tests for vCPU affinity mechanism.

This module tests the vCPU pool and process affinity routing features.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from nimbus.kernel import AgentOS, vCPU, vCPUPool, AgentProcess, ProcessState
from nimbus.kernel.proc import AgentProcess
from nimbus.tools.base import ToolRegistry


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, name: str = "mock"):
        self.name = name
        self.call_count = 0

    async def complete_with_tools(self, messages, tools):
        self.call_count += 1
        # Return a simple completion response (no tool calls = done)
        return MagicMock(
            content=f"Response from {self.name}",
            tool_calls=[],
            finish_reason="stop",
            is_complete=True,
            raw_response={},
        )


class TestAgentProcessAffinity:
    """Tests for AgentProcess.vcpu_affinity field."""

    def test_default_affinity_is_none(self):
        """Process should have None affinity by default."""
        proc = AgentProcess.create(role="test")
        assert proc.vcpu_affinity is None

    def test_set_affinity_on_create(self):
        """Process can be created with vcpu_affinity."""
        proc = AgentProcess.create(role="planner", vcpu_affinity="planning_vcpu")
        assert proc.vcpu_affinity == "planning_vcpu"

    def test_affinity_in_to_dict(self):
        """vcpu_affinity should be serialized in to_dict."""
        proc = AgentProcess.create(role="test", vcpu_affinity="my_vcpu")
        data = proc.to_dict()
        assert "vcpu_affinity" in data
        assert data["vcpu_affinity"] == "my_vcpu"


class TestVCPUPool:
    """Tests for vCPUPool."""

    def test_empty_pool(self):
        """Empty pool should have no vCPUs."""
        pool = vCPUPool()
        assert len(pool) == 0
        assert pool.default_id is None
        assert pool.get_default() is None

    def test_register_vcpu(self):
        """Can register vCPUs in pool."""
        pool = vCPUPool()
        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)
        vcpu2 = vCPU(MockLLMClient("vcpu2"), tools)

        pool.register("vcpu1", vcpu1)
        pool.register("vcpu2", vcpu2)

        assert len(pool) == 2
        assert "vcpu1" in pool
        assert "vcpu2" in pool
        assert pool.get("vcpu1") is vcpu1
        assert pool.get("vcpu2") is vcpu2

    def test_first_registered_is_default(self):
        """First registered vCPU becomes default."""
        pool = vCPUPool()
        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)
        vcpu2 = vCPU(MockLLMClient("vcpu2"), tools)

        pool.register("vcpu1", vcpu1)
        pool.register("vcpu2", vcpu2)

        assert pool.default_id == "vcpu1"
        assert pool.get_default() is vcpu1

    def test_explicit_default(self):
        """Can set explicit default vCPU."""
        pool = vCPUPool()
        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)
        vcpu2 = vCPU(MockLLMClient("vcpu2"), tools)

        pool.register("vcpu1", vcpu1)
        pool.register("vcpu2", vcpu2, is_default=True)

        assert pool.default_id == "vcpu2"
        assert pool.get_default() is vcpu2

    def test_set_default(self):
        """Can change default vCPU."""
        pool = vCPUPool()
        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)
        vcpu2 = vCPU(MockLLMClient("vcpu2"), tools)

        pool.register("vcpu1", vcpu1)
        pool.register("vcpu2", vcpu2)

        assert pool.default_id == "vcpu1"
        pool.set_default("vcpu2")
        assert pool.default_id == "vcpu2"

    def test_unregister_vcpu(self):
        """Can unregister vCPUs."""
        pool = vCPUPool()
        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)

        pool.register("vcpu1", vcpu1)
        assert len(pool) == 1

        removed = pool.unregister("vcpu1")
        assert removed is vcpu1
        assert len(pool) == 0
        assert "vcpu1" not in pool

    def test_duplicate_registration_fails(self):
        """Cannot register same ID twice."""
        pool = vCPUPool()
        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)

        pool.register("vcpu1", vcpu1)
        with pytest.raises(ValueError, match="already registered"):
            pool.register("vcpu1", vcpu1)

    def test_get_for_process_with_affinity(self):
        """Process with affinity gets specific vCPU."""
        pool = vCPUPool()
        tools = ToolRegistry()
        planner_vcpu = vCPU(MockLLMClient("planner"), tools)
        executor_vcpu = vCPU(MockLLMClient("executor"), tools)

        pool.register("planner", planner_vcpu)
        pool.register("executor", executor_vcpu)

        proc = AgentProcess.create(role="planning_task", vcpu_affinity="planner")
        vcpu = pool.get_for_process(proc)
        assert vcpu is planner_vcpu

        proc2 = AgentProcess.create(role="execute_task", vcpu_affinity="executor")
        vcpu2 = pool.get_for_process(proc2)
        assert vcpu2 is executor_vcpu

    def test_get_for_process_no_affinity(self):
        """Process without affinity gets default vCPU."""
        pool = vCPUPool()
        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)
        vcpu2 = vCPU(MockLLMClient("vcpu2"), tools)

        pool.register("vcpu1", vcpu1)
        pool.register("vcpu2", vcpu2)

        proc = AgentProcess.create(role="test")
        vcpu = pool.get_for_process(proc)
        assert vcpu is vcpu1  # default

    def test_get_for_process_unknown_affinity(self):
        """Process with unknown affinity falls back to default."""
        pool = vCPUPool()
        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)

        pool.register("vcpu1", vcpu1)

        proc = AgentProcess.create(role="test", vcpu_affinity="nonexistent")
        vcpu = pool.get_for_process(proc)
        assert vcpu is vcpu1  # fallback to default

    def test_list_vcpus(self):
        """Can list all vCPU IDs."""
        pool = vCPUPool()
        tools = ToolRegistry()

        pool.register("vcpu1", vCPU(MockLLMClient("1"), tools))
        pool.register("vcpu2", vCPU(MockLLMClient("2"), tools))
        pool.register("vcpu3", vCPU(MockLLMClient("3"), tools))

        ids = pool.list_vcpus()
        assert set(ids) == {"vcpu1", "vcpu2", "vcpu3"}

    def test_iteration(self):
        """Can iterate over pool."""
        pool = vCPUPool()
        tools = ToolRegistry()

        pool.register("vcpu1", vCPU(MockLLMClient("1"), tools))
        pool.register("vcpu2", vCPU(MockLLMClient("2"), tools))

        ids = list(pool)
        assert set(ids) == {"vcpu1", "vcpu2"}


class TestAgentOSSingleVCPU:
    """Tests for AgentOS backward compatibility with single vCPU."""

    def test_single_vcpu_mode(self):
        """AgentOS with llm_client creates single vCPU."""
        tools = ToolRegistry()
        llm = MockLLMClient("single")

        kernel = AgentOS(llm_client=llm, tool_registry=tools)

        assert kernel.vcpu is not None
        assert kernel.vcpu_pool is None
        assert kernel.llm_client is llm

    def test_no_vcpu_without_llm(self):
        """AgentOS without llm_client has no vCPU."""
        tools = ToolRegistry()

        kernel = AgentOS(tool_registry=tools)

        assert kernel.vcpu is None
        assert kernel.vcpu_pool is None


class TestAgentOSMultiVCPU:
    """Tests for AgentOS with multiple vCPUs."""

    def test_multi_vcpu_mode(self):
        """AgentOS with llm_clients creates vCPU pool."""
        tools = ToolRegistry()
        llm1 = MockLLMClient("llm1")
        llm2 = MockLLMClient("llm2")

        kernel = AgentOS(
            llm_clients={"planner": llm1, "executor": llm2},
            tool_registry=tools
        )

        assert kernel.vcpu_pool is not None
        assert len(kernel.vcpu_pool) == 2
        assert "planner" in kernel.vcpu_pool
        assert "executor" in kernel.vcpu_pool

    def test_first_llm_is_default(self):
        """First LLM client becomes default vCPU."""
        tools = ToolRegistry()
        llm1 = MockLLMClient("llm1")
        llm2 = MockLLMClient("llm2")

        kernel = AgentOS(
            llm_clients={"planner": llm1, "executor": llm2},
            tool_registry=tools
        )

        assert kernel.vcpu_pool.default_id == "planner"
        # self.vcpu should point to default for backward compat
        assert kernel.vcpu is kernel.vcpu_pool.get_default()

    @pytest.mark.asyncio
    async def test_spawn_with_affinity(self):
        """Can spawn process with vcpu_affinity."""
        tools = ToolRegistry()
        llm1 = MockLLMClient("llm1")
        llm2 = MockLLMClient("llm2")

        kernel = AgentOS(
            llm_clients={"planner": llm1, "executor": llm2},
            tool_registry=tools
        )

        pid = await kernel.spawn(
            role="Planner",
            goal="Create plan",
            vcpu_affinity="planner"
        )

        proc = kernel.getproc(pid)
        assert proc is not None
        assert proc.vcpu_affinity == "planner"

    @pytest.mark.asyncio
    async def test_affinity_routing_executes_correct_vcpu(self):
        """Process with affinity executes on correct vCPU."""
        tools = ToolRegistry()
        planner_llm = MockLLMClient("planner")
        executor_llm = MockLLMClient("executor")

        kernel = AgentOS(
            llm_clients={"planner": planner_llm, "executor": executor_llm},
            tool_registry=tools
        )

        # Spawn process bound to executor
        pid = await kernel.spawn(
            role="Executor",
            goal="Execute task",
            vcpu_affinity="executor"
        )

        result = await kernel.wait(pid, timeout=5.0)

        # The executor LLM should have been called
        assert executor_llm.call_count > 0
        # The planner LLM should NOT have been called
        assert planner_llm.call_count == 0

    @pytest.mark.asyncio
    async def test_no_affinity_uses_default(self):
        """Process without affinity uses default vCPU."""
        tools = ToolRegistry()
        planner_llm = MockLLMClient("planner")
        executor_llm = MockLLMClient("executor")

        kernel = AgentOS(
            llm_clients={"planner": planner_llm, "executor": executor_llm},
            tool_registry=tools
        )

        # Spawn process without affinity
        pid = await kernel.spawn(
            role="Default",
            goal="Do something",
        )

        result = await kernel.wait(pid, timeout=5.0)

        # The default (planner) LLM should have been called
        assert planner_llm.call_count > 0
        assert result["exit_code"] == 0


class TestProcessManagerWithVCPUPool:
    """Tests for ProcessManager with vCPU pool integration."""

    def test_fork_with_affinity(self):
        """Can fork process with vcpu_affinity."""
        from nimbus.kernel.scheduler import ProcessManager

        pm = ProcessManager()

        pid = pm.fork(
            parent_pid="proc_init",
            role="Worker",
            task="Do work",
            vcpu_affinity="worker_vcpu"
        )

        proc = pm.getproc(pid)
        assert proc is not None
        assert proc.vcpu_affinity == "worker_vcpu"

    @pytest.mark.asyncio
    async def test_exec_uses_pool_for_affinity(self):
        """exec() uses vCPU pool to select executor based on affinity."""
        from nimbus.kernel.scheduler import ProcessManager

        tools = ToolRegistry()
        vcpu1 = vCPU(MockLLMClient("vcpu1"), tools)
        vcpu2 = vCPU(MockLLMClient("vcpu2"), tools)

        pool = vCPUPool()
        pool.register("vcpu1", vcpu1)
        pool.register("vcpu2", vcpu2)

        pm = ProcessManager(vcpu_pool=pool)

        # Fork process with affinity to vcpu2
        pid = pm.fork(
            parent_pid="proc_init",
            role="Worker",
            task="Do work",
            vcpu_affinity="vcpu2"
        )

        await pm.exec(pid)
        result = await pm.wait(pid, timeout=5.0)

        # Check that vcpu2's LLM was called
        assert vcpu2.llm.call_count > 0
        # vcpu1's LLM should not have been called
        assert vcpu1.llm.call_count == 0
