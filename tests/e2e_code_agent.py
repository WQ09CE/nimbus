#!/usr/bin/env python3
"""
End-to-end test for Code Agent Application.

This script tests the full Code Agent flow with a real LLM.
Requires GEMINI_API_KEY environment variable.

Usage:
    # Run with default Gemini
    python tests/e2e_code_agent.py

    # Run with verbose output
    NIMBUS_LOG_LEVEL=DEBUG python tests/e2e_code_agent.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from nimbus.apps import CodeAgent


# Configure logging
log_level = os.environ.get("NIMBUS_LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_basic_glob():
    """Test basic file search with Glob."""
    print("\n" + "=" * 60)
    print("TEST 1: Basic File Search (Glob)")
    print("=" * 60)

    agent = CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    )

    result = await agent.run(
        goal="Find all Python files in the src/nimbus/tools directory. List them.",
        allowed_tools={"Glob", "Read"},
        timeout=60.0,
    )

    print(f"Status: {result['status']}")
    print(f"Exit code: {result['exit_code']}")
    print(f"Token usage: {result['token_usage']}")
    print(f"Turns: {result['turns']}")
    print(f"\nOutput:\n{result['output'][:500]}...")

    await agent.close()

    assert result['status'] == 'success', f"Test failed: {result['error']}"
    assert 'py' in result['output'].lower() or '.py' in result['output']
    print("\n[PASS] Test 1 passed!")
    return True


async def test_grep_search():
    """Test content search with Grep."""
    print("\n" + "=" * 60)
    print("TEST 2: Content Search (Grep)")
    print("=" * 60)

    agent = CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    )

    result = await agent.run(
        goal="Search for 'async def' in Python files under src/nimbus/tools/. How many matches are there?",
        allowed_tools={"Grep", "Glob"},
        timeout=60.0,
    )

    print(f"Status: {result['status']}")
    print(f"Exit code: {result['exit_code']}")
    print(f"\nOutput:\n{result['output'][:500]}...")

    await agent.close()

    assert result['status'] == 'success', f"Test failed: {result['error']}"
    print("\n[PASS] Test 2 passed!")
    return True


async def test_read_file():
    """Test file reading."""
    print("\n" + "=" * 60)
    print("TEST 3: File Reading (Read)")
    print("=" * 60)

    agent = CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    )

    result = await agent.run(
        goal="Read the file src/nimbus/apps/__init__.py and describe what it exports.",
        allowed_tools={"Read"},
        timeout=60.0,
    )

    print(f"Status: {result['status']}")
    print(f"Exit code: {result['exit_code']}")
    print(f"\nOutput:\n{result['output'][:500]}...")

    await agent.close()

    assert result['status'] == 'success', f"Test failed: {result['error']}"
    assert 'CodeAgent' in result['output'] or 'code' in result['output'].lower()
    print("\n[PASS] Test 3 passed!")
    return True


async def test_multi_tool_task():
    """Test task requiring multiple tools."""
    print("\n" + "=" * 60)
    print("TEST 4: Multi-Tool Task")
    print("=" * 60)

    agent = CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    )

    result = await agent.run(
        goal=(
            "1. Find all test files (test_*.py) in the tests directory\n"
            "2. Count how many test files there are\n"
            "3. Pick one test file and show its first 20 lines"
        ),
        allowed_tools={"Glob", "Read"},
        timeout=120.0,
    )

    print(f"Status: {result['status']}")
    print(f"Exit code: {result['exit_code']}")
    print(f"Turns: {result['turns']}")
    print(f"\nOutput:\n{result['output'][:800]}...")

    await agent.close()

    assert result['status'] == 'success', f"Test failed: {result['error']}"
    print("\n[PASS] Test 4 passed!")
    return True


async def test_codebase_analysis():
    """Test codebase analysis convenience method."""
    print("\n" + "=" * 60)
    print("TEST 5: Codebase Analysis")
    print("=" * 60)

    agent = CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    )

    # This test might take longer
    result = await agent.analyze_codebase()

    print(f"Status: {result['status']}")
    print(f"Exit code: {result['exit_code']}")
    print(f"Turns: {result['turns']}")
    print(f"\nOutput:\n{result['output'][:1000]}...")

    await agent.close()

    assert result['status'] == 'success', f"Test failed: {result['error']}"
    print("\n[PASS] Test 5 passed!")
    return True


async def main():
    """Run all e2e tests."""
    print("\n" + "=" * 60)
    print("CODE AGENT E2E TESTS")
    print("=" * 60)

    # Check for API key
    if not os.environ.get("GEMINI_API_KEY"):
        print("\n[SKIP] GEMINI_API_KEY not set. Skipping e2e tests.")
        print("Set GEMINI_API_KEY to run these tests.")
        return 0

    print(f"Project root: {project_root}")

    tests = [
        test_basic_glob,
        test_grep_search,
        test_read_file,
        test_multi_tool_task,
        # test_codebase_analysis,  # Uncomment for full test (takes longer)
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            success = await test()
            if success:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Test {test.__name__} failed with exception: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
