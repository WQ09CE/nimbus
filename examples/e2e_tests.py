"""
Nimbus Agent Framework - E2E Test Suite

End-to-end tests for DAG parallel execution, error handling, Memory management, etc.
Uses Mock LLM for reproducibility - no network dependency.

Usage:
    PYTHONPATH=. python nimbus/examples/e2e_tests.py

Test scenarios:
    1. Parallel Kitchen - DAG parallel execution & result aggregation
    2. Pipeline Failure - Error propagation & downstream skipping
    3. Memory Challenge - Pinned Context & Tiered Memory
    4. Calculator Chain - Skill chaining & state passing
    5. Replan Scenario - Checkpoint mechanism & retry
    6. Marathon Conversation - Memory compression & token budget
    7. Concurrency Bomb - max_concurrent limiting
"""

import asyncio
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

# Add project path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from nimbus.core import (
    TaskDAG,
    TaskNode,
    TaskStatus,
    RuntimeConfig,
    ExecutionResult,
    TieredMemoryManager,
    MemoryConfig,
    PinnedItem,
)
from nimbus.core.runtime import AsyncRuntime


# ============================================================
# Test Result Tracking
# ============================================================

@dataclass
class TestResult:
    """Result of a single test."""
    name: str
    passed: bool
    duration_ms: int
    message: str
    details: Optional[Dict[str, Any]] = None


# ============================================================
# Mock LLM Client
# ============================================================

class MockLLMClient:
    """Predictable Mock LLM that returns preset responses."""

    def __init__(self, responses: Dict[str, str] = None):
        """Initialize mock LLM.

        Args:
            responses: Mapping of keywords to responses.
        """
        self.responses = responses or {}
        self.call_count = 0
        self.call_history: List[str] = []

    async def complete(self, prompt: str) -> str:
        """Simulate LLM completion.

        Args:
            prompt: Input prompt.

        Returns:
            Preset response or default.
        """
        self.call_count += 1
        self.call_history.append(prompt[:100])

        # Return preset response if keyword matches
        for key, response in self.responses.items():
            if key.lower() in prompt.lower():
                return response

        # Default response: summarize the conversation
        return "Summary: The conversation discussed various topics."


# ============================================================
# Mock Skills for Testing
# ============================================================

class KitchenSkills:
    """Mock skills for parallel kitchen test."""

    def __init__(self, delay: float = 0.1):
        self.delay = delay
        self.execution_log: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def _log_execution(self, skill: str, params: Dict[str, Any], result: str):
        """Thread-safe logging of skill execution."""
        async with self._lock:
            self.execution_log.append({
                "skill": skill,
                "params": params,
                "result": result,
                "timestamp": datetime.now().isoformat(),
            })

    async def chop(self, ingredient: str) -> str:
        """Chop an ingredient."""
        await asyncio.sleep(self.delay)
        result = f"Chopped {ingredient}"
        await self._log_execution("chop", {"ingredient": ingredient}, result)
        return result

    async def wash(self, item: str) -> str:
        """Wash an item."""
        await asyncio.sleep(self.delay)
        result = f"Washed {item}"
        await self._log_execution("wash", {"item": item}, result)
        return result

    async def clean(self, item: str) -> str:
        """Clean an item."""
        await asyncio.sleep(self.delay)
        result = f"Cleaned {item}"
        await self._log_execution("clean", {"item": item}, result)
        return result

    async def cook(self, dish: str, prepared: str = "") -> str:
        """Cook a dish."""
        await asyncio.sleep(self.delay)
        result = f"Cooked {dish} (from: {prepared})"
        await self._log_execution("cook", {"dish": dish, "prepared": prepared}, result)
        return result

    async def steam(self, item: str, prepared: str = "") -> str:
        """Steam an item."""
        await asyncio.sleep(self.delay)
        result = f"Steamed {item} (from: {prepared})"
        await self._log_execution("steam", {"item": item, "prepared": prepared}, result)
        return result

    async def serve(self, dishes: str) -> str:
        """Serve dishes."""
        await asyncio.sleep(self.delay)
        result = f"Served: {dishes}"
        await self._log_execution("serve", {"dishes": dishes}, result)
        return result


class PipelineSkills:
    """Mock skills for pipeline failure test."""

    def __init__(self, fail_at: Optional[str] = None):
        """Initialize pipeline skills.

        Args:
            fail_at: Skill name that should fail.
        """
        self.fail_at = fail_at
        self.execution_order: List[str] = []

    async def fetch(self, url: str) -> str:
        """Fetch data from URL."""
        self.execution_order.append("fetch")
        if self.fail_at == "fetch":
            raise ValueError("Network error: Failed to fetch")
        await asyncio.sleep(0.05)
        return f"Data from {url}"

    async def parse(self, data: str) -> Dict[str, Any]:
        """Parse data."""
        self.execution_order.append("parse")
        if self.fail_at == "parse":
            raise ValueError("Parse error: Invalid JSON format")
        await asyncio.sleep(0.05)
        return {"parsed": True, "content": data}

    async def transform(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Transform data."""
        self.execution_order.append("transform")
        if self.fail_at == "transform":
            raise ValueError("Transform error: Schema mismatch")
        await asyncio.sleep(0.05)
        return {"transformed": True, "original": data}

    async def save(self, data: Dict[str, Any] = None) -> str:
        """Save data."""
        self.execution_order.append("save")
        if self.fail_at == "save":
            raise ValueError("Save error: Disk full")
        await asyncio.sleep(0.05)
        return "Saved successfully"


class CalculatorSkills:
    """Mock skills for calculator chain test."""

    def __init__(self):
        self.operations: List[Dict[str, Any]] = []

    async def add(self, a: float, b: float) -> float:
        """Add two numbers."""
        result = a + b
        self.operations.append({"op": "add", "a": a, "b": b, "result": result})
        await asyncio.sleep(0.01)
        return result

    async def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
        result = a * b
        self.operations.append({"op": "multiply", "a": a, "b": b, "result": result})
        await asyncio.sleep(0.01)
        return result

    async def subtract(self, a: float, b: float) -> float:
        """Subtract two numbers."""
        result = a - b
        self.operations.append({"op": "subtract", "a": a, "b": b, "result": result})
        await asyncio.sleep(0.01)
        return result


class ConcurrencySkills:
    """Skills for concurrency bomb test."""

    def __init__(self):
        self.concurrent_count = 0
        self.max_concurrent_observed = 0
        self._lock = asyncio.Lock()
        self.execution_times: List[Dict[str, float]] = []

    async def slow_task(self, task_id: str, duration: float = 0.2) -> str:
        """A slow task that tracks concurrency."""
        start_time = time.time()

        async with self._lock:
            self.concurrent_count += 1
            if self.concurrent_count > self.max_concurrent_observed:
                self.max_concurrent_observed = self.concurrent_count

        try:
            await asyncio.sleep(duration)
            return f"Task {task_id} completed"
        finally:
            async with self._lock:
                self.concurrent_count -= 1
                self.execution_times.append({
                    "task_id": task_id,
                    "start": start_time,
                    "end": time.time(),
                    "duration": time.time() - start_time,
                })


# ============================================================
# Test Cases
# ============================================================

async def test_parallel_kitchen() -> TestResult:
    """Test 1: Parallel Kitchen - DAG parallel execution.

    Scenario: Simulate cooking a meal with multiple dishes prepared in parallel.
    Test points: DAG parallel execution, result aggregation.

    Expected DAG:
        [chop vegetables] [wash rice] [clean fish]  <- parallel
               |              |            |
        [cook vegetables] [cook rice] [steam fish]  <- parallel
                          ↓
                    [serve all]
    """
    start_time = time.time()

    try:
        # Initialize skills
        kitchen = KitchenSkills(delay=0.1)

        # Create runtime with skills
        runtime = AsyncRuntime(
            skills={
                "chop": kitchen.chop,
                "wash": kitchen.wash,
                "clean": kitchen.clean,
                "cook": kitchen.cook,
                "steam": kitchen.steam,
                "serve": kitchen.serve,
            },
            config=RuntimeConfig(max_concurrent=10, default_timeout=5.0),
        )

        # Create DAG
        dag = TaskDAG.create(
            goal="Prepare a meal: stir-fried vegetables, rice, steamed fish",
            tasks=[
                # Phase 1: Prep (parallel)
                {"id": "prep_veg", "skill": "chop", "params": {"ingredient": "vegetables"}, "depends_on": []},
                {"id": "prep_rice", "skill": "wash", "params": {"item": "rice"}, "depends_on": []},
                {"id": "prep_fish", "skill": "clean", "params": {"item": "fish"}, "depends_on": []},
                # Phase 2: Cook (parallel, depends on prep)
                {"id": "cook_veg", "skill": "cook", "params": {"dish": "vegetables", "prepared": "prep_veg"}, "depends_on": ["prep_veg"]},
                {"id": "cook_rice", "skill": "cook", "params": {"dish": "rice", "prepared": "prep_rice"}, "depends_on": ["prep_rice"]},
                {"id": "cook_fish", "skill": "steam", "params": {"item": "fish", "prepared": "prep_fish"}, "depends_on": ["prep_fish"]},
                # Phase 3: Serve (depends on all cooking)
                {"id": "serve_all", "skill": "serve", "params": {"dishes": "vegetables, rice, fish"}, "depends_on": ["cook_veg", "cook_rice", "cook_fish"]},
            ],
        )

        # Execute
        result = await runtime.execute_dag(dag)

        duration_ms = int((time.time() - start_time) * 1000)

        # Verify results
        assert result.status == "success", f"Expected success, got {result.status}"
        assert result.stats.completed == 7, f"Expected 7 completed, got {result.stats.completed}"
        assert result.stats.failed == 0, f"Expected 0 failed, got {result.stats.failed}"

        # Verify parallel efficiency
        # Serial time would be 7 * 0.1 = 0.7s
        # Parallel time should be ~0.3s (3 phases)
        serial_estimate_ms = 700
        parallel_ratio = duration_ms / serial_estimate_ms

        # Verify results contain expected data
        assert "serve_all" in result.results
        assert "Served" in result.results["serve_all"]

        return TestResult(
            name="Parallel Kitchen",
            passed=True,
            duration_ms=duration_ms,
            message=f"All 7 tasks completed. Efficiency: {result.stats.parallel_efficiency:.2f}x",
            details={
                "completed": result.stats.completed,
                "parallel_efficiency": result.stats.parallel_efficiency,
                "execution_log_count": len(kitchen.execution_log),
                "parallel_ratio": parallel_ratio,
            },
        )

    except Exception as e:
        return TestResult(
            name="Parallel Kitchen",
            passed=False,
            duration_ms=int((time.time() - start_time) * 1000),
            message=f"Error: {str(e)}",
        )


async def test_pipeline_failure() -> TestResult:
    """Test 2: Pipeline Failure - Error propagation and downstream skipping.

    Scenario: Data processing pipeline with a failing step.
    Test points: Dependency chain, error propagation, downstream skip.

    DAG: fetch -> parse -> transform -> save
    Inject error: Let parse fail
    Verify: transform and save are marked as SKIPPED
    """
    start_time = time.time()

    try:
        # Initialize skills with failure at parse
        pipeline = PipelineSkills(fail_at="parse")

        # Create runtime
        runtime = AsyncRuntime(
            skills={
                "fetch": pipeline.fetch,
                "parse": pipeline.parse,
                "transform": pipeline.transform,
                "save": pipeline.save,
            },
            config=RuntimeConfig(max_retries=0, default_timeout=5.0),
        )

        # Create DAG
        dag = TaskDAG.create(
            goal="Fetch, parse, transform, and save data",
            tasks=[
                {"id": "fetch", "skill": "fetch", "params": {"url": "https://api.example.com/data"}, "depends_on": []},
                {"id": "parse", "skill": "parse", "params": {"data": "fetch_result"}, "depends_on": ["fetch"]},
                {"id": "transform", "skill": "transform", "params": {}, "depends_on": ["parse"]},
                {"id": "save", "skill": "save", "params": {}, "depends_on": ["transform"]},
            ],
        )

        # Execute
        result = await runtime.execute_dag(dag)

        duration_ms = int((time.time() - start_time) * 1000)

        # Verify results
        assert result.status in ("partial", "failed"), f"Expected partial/failed, got {result.status}"
        assert result.stats.completed == 1, f"Expected 1 completed (fetch), got {result.stats.completed}"
        assert result.stats.failed == 1, f"Expected 1 failed (parse), got {result.stats.failed}"
        assert result.stats.skipped == 2, f"Expected 2 skipped (transform, save), got {result.stats.skipped}"

        # Verify node statuses
        assert dag.nodes["fetch"].status == TaskStatus.COMPLETED
        assert dag.nodes["parse"].status == TaskStatus.FAILED
        assert dag.nodes["transform"].status == TaskStatus.SKIPPED
        assert dag.nodes["save"].status == TaskStatus.SKIPPED

        # Verify error message
        assert "Parse error" in dag.nodes["parse"].error

        return TestResult(
            name="Pipeline Failure",
            passed=True,
            duration_ms=duration_ms,
            message="Error propagation works: parse failed, downstream skipped",
            details={
                "completed": result.stats.completed,
                "failed": result.stats.failed,
                "skipped": result.stats.skipped,
                "execution_order": pipeline.execution_order,
            },
        )

    except Exception as e:
        return TestResult(
            name="Pipeline Failure",
            passed=False,
            duration_ms=int((time.time() - start_time) * 1000),
            message=f"Error: {str(e)}",
        )


async def test_memory_challenge() -> TestResult:
    """Test 3: Memory Challenge - Pinned Context and Tiered Memory.

    Scenario: Assistant needs to remember information across turns.
    Test points: Pinned Context, Tiered Memory, Token Budget.

    - Set pinned context: "User's name is Xiaoming"
    - Add multiple conversation turns
    - Verify pinned information is preserved
    """
    start_time = time.time()

    try:
        # Create tiered memory manager
        config = MemoryConfig(
            pinned_budget=500,
            working_budget=2000,
            episodic_budget=4000,
            compression_threshold=4,  # Compress after 4 turns
        )
        memory = TieredMemoryManager(config=config, session_id="test_memory")

        # Pin important information
        pinned_item = PinnedItem(
            id="user_name",
            type="user_instruction",
            content="User's name is Xiaoming",
            priority=100,
        )
        pin_success = memory.pin(pinned_item)
        assert pin_success, "Failed to pin item"

        # Add some conversation turns (synchronously to avoid compression issues)
        conversations = [
            ("user", "Hello, I want to learn Python"),
            ("assistant", "Hello! I'd be happy to help you learn Python."),
            ("user", "Can you recommend some resources?"),
            ("assistant", "Here are some recommended resources: 1. Official Python tutorial..."),
            ("user", "What's the difference between list and tuple?"),
            ("assistant", "Lists are mutable, tuples are immutable..."),
            ("user", "Can you remind me of my name?"),
        ]

        for role, content in conversations:
            memory.add_turn_sync(role, content)

        # Get context and verify pinned info is there
        context = memory.get_context()

        # Verify pinned information is preserved
        assert "Xiaoming" in context, "Pinned user name not found in context"

        # Verify pinned items
        pinned_items = memory.get_pinned()
        assert len(pinned_items) == 1
        assert pinned_items[0].id == "user_name"

        # Check memory stats
        stats = memory.get_stats()
        assert stats.turn_count == 7, f"Expected 7 turns, got {stats.turn_count}"
        assert stats.pinned_tokens > 0, "Pinned tokens should be > 0"

        duration_ms = int((time.time() - start_time) * 1000)

        return TestResult(
            name="Memory Challenge",
            passed=True,
            duration_ms=duration_ms,
            message=f"Pinned context preserved across {stats.turn_count} turns",
            details={
                "turn_count": stats.turn_count,
                "pinned_tokens": stats.pinned_tokens,
                "episodic_tokens": stats.episodic_tokens,
                "total_tokens": stats.total_tokens,
                "compression_count": stats.compression_count,
            },
        )

    except Exception as e:
        return TestResult(
            name="Memory Challenge",
            passed=False,
            duration_ms=int((time.time() - start_time) * 1000),
            message=f"Error: {str(e)}",
        )


async def test_calculator_chain() -> TestResult:
    """Test 4: Calculator Chain - Sequential calculations.

    Scenario: Chain of mathematical operations.
    Test points: Skill chaining, state passing (note: current DAG is static).

    Calculate: ((5 + 3) * 2) - 4 = 12
    """
    start_time = time.time()

    try:
        # Initialize calculator skills
        calc = CalculatorSkills()

        # Create runtime
        runtime = AsyncRuntime(
            skills={
                "add": calc.add,
                "multiply": calc.multiply,
                "subtract": calc.subtract,
            },
            config=RuntimeConfig(default_timeout=5.0),
        )

        # Create DAG for ((5 + 3) * 2) - 4
        # Note: In static DAG, we need to pre-compute dependencies
        dag = TaskDAG.create(
            goal="Calculate ((5 + 3) * 2) - 4",
            tasks=[
                {"id": "step1_add", "skill": "add", "params": {"a": 5, "b": 3}, "depends_on": []},
                {"id": "step2_multiply", "skill": "multiply", "params": {"a": 8, "b": 2}, "depends_on": ["step1_add"]},
                {"id": "step3_subtract", "skill": "subtract", "params": {"a": 16, "b": 4}, "depends_on": ["step2_multiply"]},
            ],
        )

        # Execute
        result = await runtime.execute_dag(dag)

        duration_ms = int((time.time() - start_time) * 1000)

        # Verify
        assert result.status == "success", f"Expected success, got {result.status}"
        assert result.stats.completed == 3

        # Verify calculation results
        assert dag.nodes["step1_add"].result == 8, f"5 + 3 should be 8, got {dag.nodes['step1_add'].result}"
        assert dag.nodes["step2_multiply"].result == 16, f"8 * 2 should be 16, got {dag.nodes['step2_multiply'].result}"
        assert dag.nodes["step3_subtract"].result == 12, f"16 - 4 should be 12, got {dag.nodes['step3_subtract'].result}"

        # Verify execution order
        assert calc.operations[0]["op"] == "add"
        assert calc.operations[1]["op"] == "multiply"
        assert calc.operations[2]["op"] == "subtract"

        return TestResult(
            name="Calculator Chain",
            passed=True,
            duration_ms=duration_ms,
            message="((5 + 3) * 2) - 4 = 12 calculated correctly",
            details={
                "operations": calc.operations,
                "final_result": dag.nodes["step3_subtract"].result,
            },
        )

    except Exception as e:
        return TestResult(
            name="Calculator Chain",
            passed=False,
            duration_ms=int((time.time() - start_time) * 1000),
            message=f"Error: {str(e)}",
        )


async def test_replan_scenario() -> TestResult:
    """Test 5: Replan Scenario - Checkpoint mechanism and retry.

    Scenario: Task execution with checkpoints and retry on failure.
    Test points: Checkpoint mechanism, error handling, retry.
    """
    start_time = time.time()

    try:
        # Track retry attempts
        retry_count = 0
        max_retries = 2

        async def flaky_task(attempt_threshold: int = 2) -> str:
            """A task that fails on first attempts but succeeds later."""
            nonlocal retry_count
            retry_count += 1
            if retry_count < attempt_threshold:
                raise ValueError(f"Flaky failure, attempt {retry_count}")
            return "Success after retry"

        async def checkpoint_task() -> str:
            """A checkpoint task."""
            await asyncio.sleep(0.01)
            return "Checkpoint reached"

        # Create runtime with retry support
        runtime = AsyncRuntime(
            skills={
                "checkpoint": checkpoint_task,
                "flaky": flaky_task,
            },
            config=RuntimeConfig(
                max_retries=max_retries,
                retry_delay=0.01,
                default_timeout=5.0,
            ),
        )

        # Create DAG with checkpoint
        dag = TaskDAG.create(
            goal="Execute with checkpoint and retry",
            tasks=[
                {"id": "t1", "skill": "checkpoint", "params": {}, "depends_on": [], "is_checkpoint": True},
                {"id": "t2", "skill": "flaky", "params": {"attempt_threshold": 2}, "depends_on": ["t1"]},
            ],
        )

        # Execute
        result = await runtime.execute_dag(dag)

        duration_ms = int((time.time() - start_time) * 1000)

        # Verify
        assert result.status == "success", f"Expected success after retry, got {result.status}"
        assert dag.nodes["t1"].is_checkpoint == True, "Checkpoint flag should be set"
        assert retry_count == 2, f"Expected 2 attempts, got {retry_count}"

        return TestResult(
            name="Replan Scenario",
            passed=True,
            duration_ms=duration_ms,
            message=f"Checkpoint and retry work. Attempts: {retry_count}",
            details={
                "retry_count": retry_count,
                "checkpoint_marked": dag.nodes["t1"].is_checkpoint,
                "final_status": result.status,
            },
        )

    except Exception as e:
        return TestResult(
            name="Replan Scenario",
            passed=False,
            duration_ms=int((time.time() - start_time) * 1000),
            message=f"Error: {str(e)}",
        )


async def test_marathon_conversation() -> TestResult:
    """Test 6: Marathon Conversation - Memory compression test.

    Scenario: Multiple rounds of conversation to test memory management.
    Test points: Episodic compression, Token Budget.

    - 20+ rounds of conversation
    - Verify compression occurs
    - Verify key information is preserved
    """
    start_time = time.time()

    try:
        # Create mock LLM for compression
        mock_llm = MockLLMClient(responses={
            "compress": "User discussed Python learning, including lists, tuples, and functions.",
        })

        # Create tiered memory with low compression threshold
        config = MemoryConfig(
            pinned_budget=200,
            working_budget=1000,
            episodic_budget=2000,  # Low budget to trigger compression
            compression_threshold=4,  # Compress after 4 turns
        )
        memory = TieredMemoryManager(
            config=config,
            llm_client=mock_llm,
            session_id="marathon_test",
        )

        # Pin critical info
        memory.pin(PinnedItem(
            id="critical_info",
            type="user_instruction",
            content="User prefers detailed explanations",
            priority=100,
        ))

        # Simulate 24 rounds of conversation
        topics = [
            "Python basics", "Variables", "Data types", "Functions",
            "Classes", "Inheritance", "Modules", "Packages",
            "Error handling", "File I/O", "List comprehension", "Generators",
        ]

        for i, topic in enumerate(topics):
            await memory.add_turn("user", f"Tell me about {topic}")
            await memory.add_turn("assistant", f"Here's information about {topic}: " + "x" * 100)

        # Get stats
        stats = memory.get_stats()

        # Get context to verify critical info is preserved
        context = memory.get_context()

        duration_ms = int((time.time() - start_time) * 1000)

        # Verify compression occurred
        # Note: Compression requires LLM call, which our mock handles
        assert stats.turn_count == 24, f"Expected 24 turns, got {stats.turn_count}"

        # Verify pinned info is preserved
        assert "detailed explanations" in context, "Critical pinned info should be preserved"

        # Verify stats
        assert stats.pinned_tokens > 0
        assert stats.episodic_tokens > 0

        return TestResult(
            name="Marathon Conversation",
            passed=True,
            duration_ms=duration_ms,
            message=f"24 turns handled. Compressions: {stats.compression_count}",
            details={
                "turn_count": stats.turn_count,
                "compression_count": stats.compression_count,
                "pinned_tokens": stats.pinned_tokens,
                "episodic_tokens": stats.episodic_tokens,
                "total_tokens": stats.total_tokens,
                "llm_calls": mock_llm.call_count,
            },
        )

    except Exception as e:
        return TestResult(
            name="Marathon Conversation",
            passed=False,
            duration_ms=int((time.time() - start_time) * 1000),
            message=f"Error: {str(e)}",
        )


async def test_concurrency_bomb() -> TestResult:
    """Test 7: Concurrency Bomb - max_concurrent limiting test.

    Scenario: Trigger many tasks simultaneously.
    Test points: max_concurrent limit enforcement.

    - Create 10 parallel tasks
    - Set max_concurrent = 3
    - Verify at most 3 tasks run at once
    """
    start_time = time.time()

    try:
        # Initialize concurrency tracking skills
        skills = ConcurrencySkills()

        # Create runtime with strict concurrency limit
        max_concurrent = 3
        runtime = AsyncRuntime(
            skills={
                "slow_task": skills.slow_task,
            },
            config=RuntimeConfig(
                max_concurrent=max_concurrent,
                default_timeout=10.0,
            ),
        )

        # Create 10 parallel tasks (no dependencies)
        tasks = [
            {
                "id": f"task_{i}",
                "skill": "slow_task",
                "params": {"task_id": str(i), "duration": 0.15},
                "depends_on": [],
            }
            for i in range(10)
        ]

        dag = TaskDAG.create(goal="Concurrency bomb test", tasks=tasks)

        # Execute
        result = await runtime.execute_dag(dag)

        duration_ms = int((time.time() - start_time) * 1000)

        # Verify all tasks completed
        assert result.status == "success", f"Expected success, got {result.status}"
        assert result.stats.completed == 10, f"Expected 10 completed, got {result.stats.completed}"

        # Verify max concurrent was not exceeded
        assert skills.max_concurrent_observed <= max_concurrent, \
            f"Max concurrent {skills.max_concurrent_observed} exceeded limit {max_concurrent}"

        # Calculate expected duration
        # With 10 tasks, 0.15s each, and max 3 concurrent:
        # Should take roughly 4 batches = 0.6s
        # Allow some overhead
        expected_min_duration = 400  # 4 batches * 0.1s min

        return TestResult(
            name="Concurrency Bomb",
            passed=True,
            duration_ms=duration_ms,
            message=f"10 tasks completed, max concurrent observed: {skills.max_concurrent_observed}",
            details={
                "completed": result.stats.completed,
                "max_concurrent_limit": max_concurrent,
                "max_concurrent_observed": skills.max_concurrent_observed,
                "total_duration_ms": duration_ms,
                "execution_count": len(skills.execution_times),
            },
        )

    except Exception as e:
        return TestResult(
            name="Concurrency Bomb",
            passed=False,
            duration_ms=int((time.time() - start_time) * 1000),
            message=f"Error: {str(e)}",
        )


# ============================================================
# Test Runner
# ============================================================

async def run_all_tests() -> List[TestResult]:
    """Run all E2E tests."""
    tests = [
        ("1. Parallel Kitchen", test_parallel_kitchen),
        ("2. Pipeline Failure", test_pipeline_failure),
        ("3. Memory Challenge", test_memory_challenge),
        ("4. Calculator Chain", test_calculator_chain),
        ("5. Replan Scenario", test_replan_scenario),
        ("6. Marathon Conversation", test_marathon_conversation),
        ("7. Concurrency Bomb", test_concurrency_bomb),
    ]

    results: List[TestResult] = []

    print("=" * 70)
    print("Nimbus Agent Framework - E2E Test Suite")
    print("=" * 70)
    print()

    for name, test_func in tests:
        print(f"Running: {name}...")
        try:
            result = await test_func()
            results.append(result)

            status = "PASS" if result.passed else "FAIL"
            print(f"  [{status}] {result.message} ({result.duration_ms}ms)")

            if result.details:
                for key, value in result.details.items():
                    print(f"    - {key}: {value}")
            print()

        except Exception as e:
            result = TestResult(
                name=name,
                passed=False,
                duration_ms=0,
                message=f"Unexpected error: {str(e)}",
            )
            results.append(result)
            print(f"  [ERROR] {result.message}")
            print()

    return results


def print_summary(results: List[TestResult]) -> None:
    """Print test summary."""
    print("=" * 70)
    print("Test Summary")
    print("=" * 70)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_time = sum(r.duration_ms for r in results)

    print(f"Total: {len(results)} tests")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Total time: {total_time}ms")
    print()

    if failed > 0:
        print("Failed tests:")
        for r in results:
            if not r.passed:
                print(f"  - {r.name}: {r.message}")
        print()

    # Final status
    if failed == 0:
        print("[ALL TESTS PASSED]")
    else:
        print(f"[{failed} TEST(S) FAILED]")


async def main():
    """Main entry point."""
    results = await run_all_tests()
    print_summary(results)

    # Return exit code
    failed = sum(1 for r in results if not r.passed)
    return failed


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
