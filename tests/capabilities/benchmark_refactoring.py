#!/usr/bin/env python3
"""Cross-File Refactoring Benchmark Runner.

This script runs the cross-file refactoring benchmark with different LLM providers
to measure their capability differences.

Usage:
    python tests/capabilities/benchmark_refactoring.py --provider gemini
    python tests/capabilities/benchmark_refactoring.py --provider ollama --model qwen3:8b
    python tests/capabilities/benchmark_refactoring.py --all
"""

import asyncio
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from nimbus.llm import create_llm_client
from nimbus.core.agent import CodeAgent
from nimbus.tools import ToolRegistry, read_file, glob_files, grep_content, edit_file, write_file

# Import evaluation tools
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
from evaluation.refactoring_metrics import (
    RefactoringEvaluator,
    create_api_migration_expectation,
)


# =============================================================================
# Constants
# =============================================================================

TEST_DATA_DIR = Path(__file__).parent.parent / "data" / "refactoring"
SAMPLE_PROJECT_PATH = TEST_DATA_DIR / "sample_project"
GOLDEN_PATH = TEST_DATA_DIR / "golden"

BENCHMARK_TASK = """
## Task: API Migration

Rename the `old_api()` method of the `APIClient` class to `new_api()`.

### Requirements

1. **Method Definition**: Rename `def old_api(...)` to `def new_api(...)`
   in `core/client.py`

2. **Call Sites**: Update all calls to `client.old_api()` or
   `self.client.old_api()` across the codebase

3. **Documentation**: Update references in README.md

4. **Tests**: Update test method names and assertions

### CRITICAL WARNING

The file `services/data.py` contains an INDEPENDENT function named
`old_api()` that is NOT related to `APIClient.old_api()`.

DO NOT modify `services/data.py`. This function handles legacy data
format conversion and must remain unchanged.

### Success Criteria

- All `APIClient.old_api()` calls are renamed to `new_api()`
- `services/data.py` is NOT modified
- All tests pass after refactoring
"""


# =============================================================================
# Helper Functions
# =============================================================================


def create_workspace() -> Path:
    """Create a temporary workspace with the sample project."""
    workspace = Path(tempfile.mkdtemp(prefix="refactor_bench_"))
    shutil.copytree(SAMPLE_PROJECT_PATH, workspace / "project")
    return workspace / "project"


def cleanup_workspace(workspace: Path):
    """Clean up the temporary workspace."""
    parent = workspace.parent
    if parent.name.startswith("refactor_bench_"):
        shutil.rmtree(parent, ignore_errors=True)


def run_tests_in_workspace(workspace: Path) -> bool:
    """Run pytest in the workspace."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(workspace / "tests"), "-v", "--tb=short"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0
    except Exception as e:
        print(f"  Error running tests: {e}")
        return False


# =============================================================================
# Agent Runner
# =============================================================================


async def run_agent_refactoring(
    provider: str,
    model: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """Run the refactoring benchmark with a specific provider.

    Args:
        provider: LLM provider name (gemini, ollama, openrouter)
        model: Optional model name override
        verbose: Whether to print progress

    Returns:
        Benchmark results dictionary
    """
    if verbose:
        print(f"\n{'='*60}")
        print(f"Running benchmark with provider: {provider}")
        if model:
            print(f"Model: {model}")
        print("="*60)

    # Create workspace
    workspace = create_workspace()
    if verbose:
        print(f"Workspace: {workspace}")

    try:
        # Create LLM client
        kwargs = {"provider": provider}
        if model:
            kwargs["model"] = model

        if verbose:
            print("Creating LLM client...")
        llm_client = create_llm_client(**kwargs)

        # Get actual model name
        actual_model = getattr(llm_client, 'model', model or 'unknown')

        # Create agent components
        if verbose:
            print("Creating agent...")

        # Create tool registry with file operation tools
        tool_registry = ToolRegistry()
        tool_registry.register_decorated(read_file)
        tool_registry.register_decorated(glob_files)
        tool_registry.register_decorated(grep_content)
        tool_registry.register_decorated(edit_file)
        tool_registry.register_decorated(write_file)

        # Create agent with workspace set to the test project
        agent = CodeAgent(
            llm_client=llm_client,
            tool_registry=tool_registry,
            workspace=workspace,
            memory_type="tiered",
            planner_type="dag",
        )

        # Prepare task with workspace context
        full_task = f"""
You are working in the directory: {workspace}

{BENCHMARK_TASK}

IMPORTANT: You MUST use the Edit tool to modify files. Do not just analyze - actually make the changes.

Steps to complete:
1. Use Glob to find all Python files
2. Use Grep to find all occurrences of "old_api"
3. Use Read to understand each file's context
4. Use Edit to rename APIClient.old_api to new_api in each file
5. Update README.md documentation

Remember:
- ONLY modify APIClient.old_api, NOT the independent old_api function in services/data.py
- You must call the Edit tool for each file that needs changes
"""

        # Run agent
        if verbose:
            print("Running agent...")
            start_time = datetime.now()

        result = await agent.run(full_task)

        if verbose:
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"Agent completed in {elapsed:.1f}s")

        # Evaluate results
        if verbose:
            print("Evaluating results...")

        expectation = create_api_migration_expectation()
        evaluator = RefactoringEvaluator(GOLDEN_PATH, expectation)

        tests_passed = run_tests_in_workspace(workspace)
        score = evaluator.evaluate(workspace, tests_passed=tests_passed)

        # Build results
        results = {
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "model": actual_model,
            "task": "api_migration",
            "score": {
                "location_accuracy": round(score.location_accuracy, 3),
                "modification_accuracy": round(score.modification_accuracy, 3),
                "no_false_positive": round(score.no_false_positive, 3),
                "tests_pass": round(score.tests_pass, 3),
                "total": round(score.total, 3),
            },
            "tests_passed": tests_passed,
            "details": score.details,
        }

        if verbose:
            print(f"\nResults for {provider}/{actual_model}:")
            print(f"  Location Accuracy:     {score.location_accuracy*100:.1f}%")
            print(f"  Modification Accuracy: {score.modification_accuracy*100:.1f}%")
            print(f"  No False Positives:    {score.no_false_positive*100:.1f}%")
            print(f"  Tests Pass:            {score.tests_pass*100:.1f}%")
            print(f"  --------------------------------")
            print(f"  TOTAL SCORE:           {score.total*100:.1f}%")

        return results

    except Exception as e:
        if verbose:
            print(f"Error: {e}")
        return {
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "model": model or "unknown",
            "error": str(e),
            "score": {"total": 0.0},
        }
    finally:
        cleanup_workspace(workspace)


# =============================================================================
# Main
# =============================================================================


async def main():
    parser = argparse.ArgumentParser(description="Cross-File Refactoring Benchmark")
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="LLM provider (gemini, ollama, openrouter)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model name override",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run benchmark with all available providers",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output",
    )
    args = parser.parse_args()

    results = []

    if args.all:
        # Test multiple providers
        providers = [
            ("gemini", None),
            ("ollama", "qwen3:8b"),
        ]
        for provider, model in providers:
            try:
                result = await run_agent_refactoring(
                    provider=provider,
                    model=model,
                    verbose=not args.quiet
                )
                results.append(result)
            except Exception as e:
                print(f"Failed for {provider}: {e}")
                results.append({
                    "provider": provider,
                    "model": model,
                    "error": str(e),
                    "score": {"total": 0.0},
                })
    elif args.provider:
        result = await run_agent_refactoring(
            provider=args.provider,
            model=args.model,
            verbose=not args.quiet
        )
        results.append(result)
    else:
        parser.print_help()
        return 1

    # Print summary
    print("\n" + "="*60)
    print("BENCHMARK SUMMARY")
    print("="*60)

    for r in results:
        provider = r.get("provider", "unknown")
        model = r.get("model", "unknown")
        score = r.get("score", {}).get("total", 0.0)
        error = r.get("error")

        if error:
            print(f"{provider}/{model}: ERROR - {error}")
        else:
            print(f"{provider}/{model}: {score*100:.1f}%")

    # Save results
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to: {output_path}")

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
