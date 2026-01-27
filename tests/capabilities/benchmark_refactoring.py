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

from nimbus.apps.code_agent import CodeAgent

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

BENCHMARK_TASK_TEMPLATE = """
## MANDATORY: Execute Edit Operations - DO NOT STOP UNTIL ALL EDITS ARE DONE

You MUST use the Edit tool to rename `old_api` to `new_api` in multiple files.

### CRITICAL INSTRUCTION

1. DO NOT respond with analysis or explanation
2. DO NOT stop after just looking at files
3. You MUST invoke Edit tool for each file
4. Keep going until ALL files are edited

### Step 1: Read core/client.py

First, use Read to see {workspace}/core/client.py content.

### Step 2: Edit core/client.py

Use Edit tool with these EXACT parameters:
- file_path: "{workspace}/core/client.py"
- old_string: "def old_api("
- new_string: "def new_api("

Then use Edit again:
- file_path: "{workspace}/core/client.py"
- old_string: ".old_api("
- new_string: ".new_api("
- replace_all: true

### Step 3: Edit core/utils.py

Use Read to see {workspace}/core/utils.py, then Edit:
- file_path: "{workspace}/core/utils.py"
- old_string: ".old_api("
- new_string: ".new_api("
- replace_all: true

### Step 4: Edit services/auth.py

Use Read to see {workspace}/services/auth.py, then Edit:
- file_path: "{workspace}/services/auth.py"
- old_string: ".old_api("
- new_string: ".new_api("
- replace_all: true

### Step 5: Edit tests/test_client.py

Use Read to see {workspace}/tests/test_client.py, then Edit:
- file_path: "{workspace}/tests/test_client.py"
- old_string: ".old_api("
- new_string: ".new_api("
- replace_all: true

### Step 6: Edit README.md

Use Read to see {workspace}/README.md, then Edit to update old_api references.

### WARNING

- DO NOT modify services/data.py - it has an independent old_api() function
- You MUST call Edit tool - text analysis is NOT acceptable
- Complete ALL 6 steps before responding with final answer
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
        # Create agent with workspace set to the test project
        if verbose:
            print("Creating agent...")

        llm_kwargs = {}
        if model:
            llm_kwargs["model"] = model

        agent = CodeAgent(
            workspace=str(workspace),
            llm_provider=provider,
            max_iterations=100,  # Increased from 50 for complex refactoring
            **llm_kwargs,
        )

        # Get actual model name
        actual_model = model or provider

        # Prepare task with workspace context - format template with workspace path
        benchmark_task = BENCHMARK_TASK_TEMPLATE.format(workspace=workspace)
        full_task = f"""
{benchmark_task}

IMPORTANT REMINDER:
- You have Edit tool access - USE IT
- Do NOT stop after analyzing - execute the edits
- Call Edit for each file that needs changes
- Use replace_all=true for multiple occurrences
"""

        # Run agent with full tools for refactoring
        if verbose:
            print("Running agent...")
            start_time = datetime.now()

        agent_result = await agent.run(
            goal=full_task,
            allowed_tools={"Read", "Glob", "Grep", "Write", "Edit"},
            max_turns=100,  # Increased turns for complex refactoring
        )

        if verbose:
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"Agent completed in {elapsed:.1f}s")
            print(f"Agent status: {agent_result.get('status', 'unknown')}")
            print(f"Agent turns: {agent_result.get('turns', 0)}")

        # Evaluate results
        if verbose:
            print("Evaluating results...")
            # Debug: Show workspace file count
            import os
            py_files = list(workspace.rglob("*.py"))
            print(f"  Workspace Python files: {len(py_files)}")
            for pf in py_files[:10]:
                print(f"    - {pf.relative_to(workspace)}")

            # Debug: Check if key files have old_api
            key_files = ["core/client.py", "core/utils.py", "services/auth.py"]
            for kf in key_files:
                kf_path = workspace / kf
                if kf_path.exists():
                    content = kf_path.read_text()
                    old_count = content.count(".old_api(")
                    new_count = content.count(".new_api(")
                    print(f"  {kf}: old_api={old_count}, new_api={new_count}")

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
