#!/usr/bin/env python3
"""
End-to-End test for Dual-Agent Orchestration.

Tests the Core/Executor dual-agent architecture with a real LLM (pi-ai).

Requirements:
    - pi-ai server running on localhost:3031

Usage:
    python tests/e2e_dual_agent.py
    python tests/e2e_dual_agent.py --test write_and_verify
    python tests/e2e_dual_agent.py --test multi_file
    python tests/e2e_dual_agent.py --verbose
"""

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from nimbus.adapters.direct_adapter import DirectAdapter
from nimbus.adapters.types import LLMConfig
from nimbus.orchestration import DualAgentOrchestrator, OrchestratorConfig


# =============================================================================
# Test Helpers
# =============================================================================

def create_test_workspace() -> Path:
    """Create a temporary workspace for the test."""
    ws = Path(tempfile.mkdtemp(prefix="nimbus_dual_agent_test_"))
    print(f"📁 Test workspace: {ws}")
    return ws


def cleanup_workspace(ws: Path):
    """Remove temporary workspace."""
    try:
        shutil.rmtree(ws)
        print(f"🧹 Cleaned up: {ws}")
    except Exception as e:
        print(f"⚠️  Cleanup failed: {e}")


async def create_adapter() -> DirectAdapter:
    """Create and verify LLM adapter."""
    adapter = DirectAdapter(LLMConfig())
    await adapter.start()
    healthy = await adapter.health_check()
    if not healthy:
        await adapter.stop()
        raise RuntimeError("LLM adapter health check failed (missing API keys?)")
    return adapter


# =============================================================================
# Test 1: Simple Write & Verify
# =============================================================================

async def test_write_and_verify():
    """
    Test that Core can dispatch a file creation task and verify the result.

    Task: Create a Python file that implements a fibonacci function.
    Verify: File exists, contains 'def fibonacci', function is callable.
    """
    print("\n" + "=" * 70)
    print("TEST 1: Write & Verify — Create a fibonacci function")
    print("=" * 70)

    ws = create_test_workspace()
    adapter = await create_adapter()

    try:
        config = OrchestratorConfig(
            core_max_iterations=15,
            executor_max_iterations=15,
            max_dispatch_count=3,
            total_timeout=300,
        )

        orchestrator = DualAgentOrchestrator(
            llm_client=adapter,
            workspace=ws,
            config=config,
        )

        goal = (
            "Create a Python file at fibonacci.py that implements a function "
            "`fibonacci(n: int) -> int` which returns the n-th Fibonacci number. "
            "fibonacci(0)=0, fibonacci(1)=1, fibonacci(10)=55. "
            "The file should be executable and include a simple test at the bottom "
            "that prints fibonacci(10)."
        )

        result = await orchestrator.run(goal)

        # Check result
        print(f"\n📊 Result status: {result.status}")
        print(f"📊 Dispatches used: {orchestrator._dispatch_count}")
        print(f"📊 Output: {(result.output or '')[:500]}")

        # Verify manually
        fib_path = ws / "fibonacci.py"
        checks_passed = 0
        total_checks = 3

        if fib_path.exists():
            print("✅ fibonacci.py exists")
            checks_passed += 1
        else:
            print("❌ fibonacci.py NOT found")

        if fib_path.exists():
            content = fib_path.read_text()
            if "def fibonacci" in content:
                print("✅ contains 'def fibonacci'")
                checks_passed += 1
            else:
                print(f"❌ 'def fibonacci' not found in file")

            # Actually run it
            proc = await asyncio.create_subprocess_shell(
                f"cd {ws} && python3 fibonacci.py",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            output = stdout.decode()
            if "55" in output:
                print(f"✅ fibonacci(10) = 55 (output: {output.strip()})")
                checks_passed += 1
            else:
                print(f"❌ expected 55, got: {output.strip()} | stderr: {stderr.decode()[:200]}")

        success = checks_passed == total_checks
        print(f"\n{'✅ PASS' if success else '❌ FAIL'}: {checks_passed}/{total_checks} checks passed")
        return success

    finally:
        await adapter.stop()
        cleanup_workspace(ws)


# =============================================================================
# Test 2: Multi-File Task with Verification
# =============================================================================

async def test_multi_file():
    """
    Test a more complex task requiring multiple files and verification.

    Task: Create a Python package with __init__.py, a module, and a test.
    Verify: All files exist, module is importable, test passes.
    """
    print("\n" + "=" * 70)
    print("TEST 2: Multi-File — Create a Python calculator package")
    print("=" * 70)

    ws = create_test_workspace()
    adapter = await create_adapter()

    try:
        config = OrchestratorConfig(
            core_max_iterations=18,
            executor_max_iterations=20,
            max_dispatch_count=4,
            total_timeout=360,
        )

        orchestrator = DualAgentOrchestrator(
            llm_client=adapter,
            workspace=ws,
            config=config,
        )

        goal = (
            "Create a Python package called 'calculator' with the following structure:\n"
            "1. calculator/__init__.py — exports Calculator class\n"
            "2. calculator/core.py — Calculator class with methods:\n"
            "   - add(a, b) -> a + b\n"
            "   - subtract(a, b) -> a - b\n"
            "   - multiply(a, b) -> a * b\n"
            "   - divide(a, b) -> a / b (raise ValueError on divide by zero)\n"
            "3. test_calculator.py — tests all 4 operations including divide-by-zero\n"
            "\n"
            "After creating, run the tests with: python3 -m pytest test_calculator.py -v\n"
            "(install pytest first if needed)"
        )

        result = await orchestrator.run(goal)

        print(f"\n📊 Result status: {result.status}")
        print(f"📊 Dispatches used: {orchestrator._dispatch_count}")
        print(f"📊 Output: {(result.output or '')[:500]}")

        # Verify manually
        checks_passed = 0
        total_checks = 4

        # Check files exist
        init_path = ws / "calculator" / "__init__.py"
        core_path = ws / "calculator" / "core.py"
        test_path = ws / "test_calculator.py"

        if init_path.exists() and core_path.exists():
            print("✅ calculator/__init__.py and calculator/core.py exist")
            checks_passed += 1
        else:
            print(f"❌ Missing: init={init_path.exists()}, core={core_path.exists()}")

        if test_path.exists():
            print("✅ test_calculator.py exists")
            checks_passed += 1
        else:
            print("❌ test_calculator.py NOT found")

        # Check core.py has Calculator class
        if core_path.exists():
            content = core_path.read_text()
            if "class Calculator" in content and "def divide" in content:
                print("✅ Calculator class with divide method found")
                checks_passed += 1
            else:
                print("❌ Calculator class or divide method missing")

        # Run tests
        proc = await asyncio.create_subprocess_shell(
            f"cd {ws} && python3 -m pytest test_calculator.py -v 2>&1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        test_output = stdout.decode()
        if proc.returncode == 0 or "passed" in test_output.lower():
            print(f"✅ Tests passed")
            checks_passed += 1
        else:
            print(f"❌ Tests failed:\n{test_output[:500]}")

        success = checks_passed == total_checks
        print(f"\n{'✅ PASS' if success else '❌ FAIL'}: {checks_passed}/{total_checks} checks passed")
        return success

    finally:
        await adapter.stop()
        cleanup_workspace(ws)


# =============================================================================
# Test 3: Bug Fix — Tests Core's Independent Verification
# =============================================================================

async def test_bug_fix():
    """
    Test that Core catches and fixes a subtle bug through verification.

    Pre-create a buggy file, then ask to fix it.
    This tests the Core's ability to verify and re-dispatch.
    """
    print("\n" + "=" * 70)
    print("TEST 3: Bug Fix — Fix a buggy sorting function")
    print("=" * 70)

    ws = create_test_workspace()
    adapter = await create_adapter()

    try:
        # Pre-create the buggy file
        buggy_code = '''\
def sort_list(items):
    """Sort a list of numbers in ascending order."""
    # Bug: this sorts in descending order!
    return sorted(items, reverse=True)


def find_median(items):
    """Find the median of a list of numbers."""
    sorted_items = sort_list(items)
    n = len(sorted_items)
    if n % 2 == 0:
        return (sorted_items[n//2 - 1] + sorted_items[n//2]) / 2
    else:
        return sorted_items[n//2]


if __name__ == "__main__":
    test_data = [3, 1, 4, 1, 5, 9, 2, 6]
    print(f"Sorted: {sort_list(test_data)}")
    print(f"Median: {find_median(test_data)}")
'''
        (ws / "sorter.py").write_text(buggy_code)

        config = OrchestratorConfig(
            core_max_iterations=15,
            executor_max_iterations=12,
            max_dispatch_count=3,
            total_timeout=240,
        )

        orchestrator = DualAgentOrchestrator(
            llm_client=adapter,
            workspace=ws,
            config=config,
        )

        goal = (
            "Fix the bug in sorter.py. The sort_list function should sort "
            "in ASCENDING order (smallest to largest). "
            "After fixing, verify that:\n"
            "- sort_list([3,1,2]) returns [1,2,3]\n"
            "- find_median([1,2,3,4,5]) returns 3\n"
            "- find_median([1,2,3,4]) returns 2.5"
        )

        result = await orchestrator.run(goal)

        print(f"\n📊 Result status: {result.status}")
        print(f"📊 Dispatches used: {orchestrator._dispatch_count}")

        # Verify
        checks_passed = 0
        total_checks = 3

        sorter_path = ws / "sorter.py"
        if sorter_path.exists():
            content = sorter_path.read_text()
            # Bug should be fixed: no more reverse=True
            if "reverse=True" not in content:
                print("✅ reverse=True removed (bug fixed)")
                checks_passed += 1
            else:
                print("❌ reverse=True still present (bug NOT fixed)")

        # Test sort
        proc = await asyncio.create_subprocess_shell(
            f'cd {ws} && python3 -c "from sorter import sort_list; print(sort_list([3,1,2]))"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode().strip()
        if output == "[1, 2, 3]":
            print(f"✅ sort_list([3,1,2]) = {output}")
            checks_passed += 1
        else:
            print(f"❌ sort_list([3,1,2]) = {output} (expected [1, 2, 3]) stderr: {stderr.decode()[:200]}")

        # Test median
        proc = await asyncio.create_subprocess_shell(
            f'cd {ws} && python3 -c "from sorter import find_median; print(find_median([1,2,3,4,5]))"',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode().strip()
        if output == "3":
            print(f"✅ find_median([1,2,3,4,5]) = {output}")
            checks_passed += 1
        else:
            print(f"❌ find_median([1,2,3,4,5]) = {output} (expected 3) stderr: {stderr.decode()[:200]}")

        success = checks_passed == total_checks
        print(f"\n{'✅ PASS' if success else '❌ FAIL'}: {checks_passed}/{total_checks} checks passed")
        return success

    finally:
        await adapter.stop()
        cleanup_workspace(ws)


# =============================================================================
# Main
# =============================================================================

ALL_TESTS = {
    "write_and_verify": test_write_and_verify,
    "multi_file": test_multi_file,
    "bug_fix": test_bug_fix,
}


async def main():
    parser = argparse.ArgumentParser(description="Dual-Agent E2E Tests")
    parser.add_argument("--test", choices=list(ALL_TESTS.keys()), help="Run specific test")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    if args.verbose:
        from loguru import logger
        logger.enable("nimbus")
    else:
        from loguru import logger
        logger.disable("nimbus")

    print("=" * 70)
    print("🤖 DUAL-AGENT ORCHESTRATION E2E TESTS")
    print("=" * 70)

    # Check pi-ai
    try:
        adapter = DirectAdapter(LLMConfig())
        await adapter.start()
        ok = await adapter.health_check()
        await adapter.stop()
        if not ok:
            raise RuntimeError()
    except Exception:
        print("\n❌ LLM adapter health check failed (missing API keys?)")
        return 1

    print("✅ LLM adapter is healthy\n")

    tests_to_run = [ALL_TESTS[args.test]] if args.test else list(ALL_TESTS.values())
    passed = 0
    failed = 0

    for test_fn in tests_to_run:
        try:
            success = await test_fn()
            if success:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"\n💥 Test {test_fn.__name__} CRASHED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    print(f"📊 RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
