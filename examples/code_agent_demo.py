#!/usr/bin/env python3
"""
Code Agent Demo.

Demonstrates the CodeAgent application built on Agent OS.

Usage:
    # Set your API key
    export GEMINI_API_KEY=your-api-key

    # Run the demo
    python examples/code_agent_demo.py

Requirements:
    - GEMINI_API_KEY environment variable
    - Or use OpenRouter: set OPENROUTER_API_KEY
"""

import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from nimbus.apps import CodeAgent


async def demo_basic_search():
    """Demo: Basic file search."""
    print("\n" + "=" * 60)
    print("Demo 1: Basic File Search")
    print("=" * 60)

    agent = CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    )

    result = await agent.run(
        goal="Find all Python files in the src/nimbus/tools directory",
        allowed_tools={"Glob"},
        timeout=30.0,
    )

    print(f"Status: {result['status']}")
    print(f"Output:\n{result['output'][:500]}")

    await agent.close()
    return result


async def demo_code_search():
    """Demo: Search code content."""
    print("\n" + "=" * 60)
    print("Demo 2: Code Content Search")
    print("=" * 60)

    agent = CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    )

    result = await agent.search_code(
        pattern="async def",
        file_type="py",
        path="src/nimbus/tools",
    )

    print(f"Status: {result['status']}")
    print(f"Output:\n{result['output'][:500]}")

    await agent.close()
    return result


async def demo_file_analysis():
    """Demo: Read and analyze a file."""
    print("\n" + "=" * 60)
    print("Demo 3: File Analysis")
    print("=" * 60)

    async with CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    ) as agent:
        result = await agent.run(
            goal=(
                "Read src/nimbus/apps/code_agent.py and explain:\n"
                "1. What class is defined?\n"
                "2. What tools does it register?\n"
                "3. What are the main methods?"
            ),
            allowed_tools={"Read"},
            timeout=60.0,
        )

        print(f"Status: {result['status']}")
        print(f"Output:\n{result['output'][:800]}")

    return result


async def demo_multi_step():
    """Demo: Multi-step task requiring multiple tools."""
    print("\n" + "=" * 60)
    print("Demo 4: Multi-Step Task")
    print("=" * 60)

    async with CodeAgent(
        workspace=str(project_root),
        llm_provider="gemini",
    ) as agent:
        result = await agent.run(
            goal=(
                "1. Find test files (test_*.py) in the tests directory\n"
                "2. Count how many test files there are\n"
                "3. Show the first 10 lines of one test file"
            ),
            allowed_tools={"Glob", "Read"},
            timeout=120.0,
        )

        print(f"Status: {result['status']}")
        print(f"Turns: {result.get('turns', 'N/A')}")
        print(f"Output:\n{result['output'][:800]}")

    return result


async def main():
    """Run all demos."""
    print("\n" + "=" * 60)
    print("CODE AGENT DEMO")
    print("=" * 60)

    # Check for API key
    if not os.environ.get("GEMINI_API_KEY"):
        print("\nError: GEMINI_API_KEY not set.")
        print("Please set your Gemini API key:")
        print("  export GEMINI_API_KEY=your-api-key")
        print("\nOr use OpenRouter:")
        print("  export OPENROUTER_API_KEY=your-api-key")
        return 1

    print(f"Workspace: {project_root}")

    demos = [
        demo_basic_search,
        demo_code_search,
        demo_file_analysis,
        demo_multi_step,
    ]

    for demo in demos:
        try:
            await demo()
        except Exception as e:
            print(f"\nDemo failed: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
