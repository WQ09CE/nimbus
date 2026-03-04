"""
Nimbus Agent Capability Tests

These tests run the agent against coding tasks and verify the results.
Inspired by terminal-bench testing methodology.

Usage:
    # Run all capability tests
    pytest tests/capabilities/test_agent_capabilities.py -v
    
    # Run specific task
    pytest tests/capabilities/test_agent_capabilities.py -v -k "hello_world"
    
    # Skip slow tests
    pytest tests/capabilities/test_agent_capabilities.py -v -m "not slow"
"""

import asyncio
import importlib
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

# =============================================================================
# Task Configuration
# =============================================================================

TASKS_DIR = Path(__file__).parent / "tasks"

TASKS = [
    "hello_world",
    "fix_python_bug",
    "implement_function",
    "find_and_fix_bug",
]


@dataclass
class TaskConfig:
    """Configuration for a capability task."""
    task_id: str
    instruction: str
    difficulty: str
    category: str
    timeout_sec: float
    workspace_files: dict

    @classmethod
    def load(cls, task_id: str) -> "TaskConfig":
        """Load task configuration from YAML."""
        task_path = TASKS_DIR / task_id / "task.yaml"
        with open(task_path) as f:
            data = yaml.safe_load(f)
        return cls(
            task_id=task_id,
            instruction=data["instruction"],
            difficulty=data.get("difficulty", "medium"),
            category=data.get("category", "coding"),
            timeout_sec=data.get("timeout_sec", 120.0),
            workspace_files=data.get("workspace_files", {}),
        )


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def setup_workspace(workspace: Path, task: TaskConfig):
    """Set up initial files in workspace."""
    for filename, content in task.workspace_files.items():
        file_path = workspace / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)


async def run_agent(workspace: Path, instruction: str, timeout_sec: float = 120.0) -> dict:
    """
    Run the nimbus agent on a task.
    
    Returns dict with:
        - success: bool
        - output: str
        - error: Optional[str]
        - duration_sec: float
    """
    import os as os_module

    from nimbus.adapters.llm_factory import create_llm_client
    from nimbus.agentos import AgentOS, AgentOSConfig
    from nimbus.tools import register_default_tools

    start = time.time()
    adapter = None

    try:
        # Create LLM client via DirectAdapter (LiteLLM)
        model = os_module.environ.get("NIMBUS_MODEL", "gemini/gemini-2.5-flash")
        adapter = await create_llm_client(model=model)

        # Create config with workspace
        config = AgentOSConfig(
            default_timeout=timeout_sec,
            workspace_info=f"Workspace directory: {workspace}\nAll file operations should be relative to this directory.",
        )

        # Change to workspace directory before running
        original_cwd = os_module.getcwd()
        os_module.chdir(workspace)

        try:
            from nimbus.tools import iterate_tools
            # Gather workspace-sandboxed tools
            tools_dict = {}
            for name, func, _, _ in iterate_tools(workspace=workspace):
                tools_dict[name] = func

            # Create agent
            agent = AgentOS(
                llm_client=adapter,
                tools=tools_dict,
                config=config,
            )

            # Prepend workspace info to instruction
            full_instruction = f"Working directory: {workspace}\n\n{instruction}"

            
            # Spawn process
            pid = agent.spawn(
                goal=full_instruction,
                role="coding",
            )
            # Wait for completion using AgentOS.wait
            result = await agent.wait(pid, timeout=timeout_sec)
            process = agent._processes[pid]
            print(f"\nPROCESS STATE: {process.state}\nRESULT: {result}\nMETADATA: {process.metadata}\n")
    

            return {
                "success": result.status == "OK",
                "output": str(result.output) if result.output else "",
                "error": str(result.fault) if result.fault else None,
                "duration_sec": time.time() - start,
            }
        finally:
            os_module.chdir(original_cwd)

    except asyncio.TimeoutError:
        return {
            "success": False,
            "output": "",
            "error": f"Timeout after {timeout_sec}s",
            "duration_sec": timeout_sec,
        }
    except Exception as e:
        import traceback
        return {
            "success": False,
            "output": "",
            "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            "duration_sec": time.time() - start,
        }
    finally:
        if adapter:
            await adapter.stop()


def run_task_tests(workspace: Path, task_id: str) -> tuple[int, int, list]:
    """
    Run the test file for a task.
    
    Returns (passed, failed, errors)
    """
    test_module_path = TASKS_DIR / task_id / "task_tests.py"

    # Load the test module
    spec = importlib.util.spec_from_file_location(f"test_{task_id}", test_module_path)
    test_module = importlib.util.module_from_spec(spec)

    passed = 0
    failed = 0
    errors = []

    # Find and run all test functions
    spec.loader.exec_module(test_module)

    for name in dir(test_module):
        if name.startswith("test_"):
            test_func = getattr(test_module, name)
            try:
                test_func(workspace)
                passed += 1
            except AssertionError as e:
                failed += 1
                errors.append(f"{name}: {e}")
            except Exception as e:
                failed += 1
                errors.append(f"{name}: {type(e).__name__}: {e}")

    return passed, failed, errors


# =============================================================================
# Test Cases
# =============================================================================

class TestHelloWorld:
    """Test: Create a simple file."""

    TASK_ID = "hello_world"

    @pytest.mark.asyncio
    async def test_agent_creates_file(self, workspace):
        """Agent should create hello.txt with correct content."""
        task = TaskConfig.load(self.TASK_ID)
        setup_workspace(workspace, task)

        # Run agent
        result = await run_agent(workspace, task.instruction, task.timeout_sec)

        # Verify
        passed, failed, errors = run_task_tests(workspace, self.TASK_ID)

        assert failed == 0, f"Task tests failed: {errors}"
        assert passed > 0, "No tests passed"


class TestFixPythonBug:
    """Test: Fix a bug in Python code."""

    TASK_ID = "fix_python_bug"

    @pytest.mark.asyncio
    async def test_agent_fixes_bug(self, workspace):
        """Agent should fix the division by zero bug."""
        task = TaskConfig.load(self.TASK_ID)
        setup_workspace(workspace, task)

        # Run agent
        result = await run_agent(workspace, task.instruction, task.timeout_sec)

        # Verify
        passed, failed, errors = run_task_tests(workspace, self.TASK_ID)

        assert failed == 0, f"Task tests failed: {errors}"
        assert passed >= 3, f"Expected at least 3 tests to pass, got {passed}"


class TestImplementFunction:
    """Test: Implement a function from specification."""

    TASK_ID = "implement_function"

    @pytest.mark.asyncio
    async def test_agent_implements_fizzbuzz(self, workspace):
        """Agent should implement fizzbuzz correctly."""
        task = TaskConfig.load(self.TASK_ID)
        setup_workspace(workspace, task)

        # Run agent
        result = await run_agent(workspace, task.instruction, task.timeout_sec)

        # Verify
        passed, failed, errors = run_task_tests(workspace, self.TASK_ID)

        assert failed == 0, f"Task tests failed: {errors}"


@pytest.mark.slow
class TestFindAndFixBug:
    """Test: Find and fix a subtle bug."""

    TASK_ID = "find_and_fix_bug"

    @pytest.mark.asyncio
    async def test_agent_fixes_binary_search(self, workspace):
        """Agent should find and fix the binary search bug."""
        task = TaskConfig.load(self.TASK_ID)
        setup_workspace(workspace, task)

        # Run agent
        result = await run_agent(workspace, task.instruction, task.timeout_sec)

        # Verify
        passed, failed, errors = run_task_tests(workspace, self.TASK_ID)

        assert failed == 0, f"Task tests failed: {errors}"


# =============================================================================
# Batch Runner
# =============================================================================

@pytest.mark.slow
class TestAllTasks:
    """Run all capability tasks and report results."""

    @pytest.mark.asyncio
    async def test_run_all_tasks(self, tmp_path):
        """Run all tasks and generate a report."""
        results = []

        for task_id in TASKS:
            workspace = tmp_path / task_id
            workspace.mkdir()

            task = TaskConfig.load(task_id)
            setup_workspace(workspace, task)

            # Run agent
            agent_result = await run_agent(
                workspace,
                task.instruction,
                task.timeout_sec
            )

            # Run tests
            passed, failed, errors = run_task_tests(workspace, task_id)

            results.append({
                "task_id": task_id,
                "difficulty": task.difficulty,
                "agent_success": agent_result["success"],
                "agent_error": agent_result["error"],
                "tests_passed": passed,
                "tests_failed": failed,
                "test_errors": errors,
                "duration_sec": agent_result["duration_sec"],
            })

        # Print report
        print("\n" + "=" * 60)
        print("CAPABILITY TEST REPORT")
        print("=" * 60)

        total_passed = 0
        total_failed = 0

        for r in results:
            status = "✅ PASS" if r["tests_failed"] == 0 else "❌ FAIL"
            print(f"\n{r['task_id']} [{r['difficulty']}]: {status}")
            print(f"  Tests: {r['tests_passed']} passed, {r['tests_failed']} failed")
            print(f"  Duration: {r['duration_sec']:.1f}s")

            if r["test_errors"]:
                for err in r["test_errors"]:
                    print(f"  Error: {err}")

            if r["tests_failed"] == 0:
                total_passed += 1
            else:
                total_failed += 1

        print("\n" + "=" * 60)
        print(f"TOTAL: {total_passed}/{len(TASKS)} tasks passed")
        print("=" * 60)

        # Don't fail the test - just report
        # assert total_failed == 0, f"{total_failed} tasks failed"
