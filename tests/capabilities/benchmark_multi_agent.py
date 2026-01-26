#!/usr/bin/env python3
"""Multi-Agent Refactoring Benchmark.

This script tests whether a multi-agent architecture (Brain + Coder)
can improve performance on the refactoring task compared to single agent.

Architecture:
    Brain Agent: Analyzes the task, understands requirements, creates strategy
    Coder Agent: Follows Brain's instructions to identify specific changes

Usage:
    python tests/capabilities/benchmark_multi_agent.py --provider ollama --model qwen3:8b
"""

import asyncio
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from nimbus.llm import create_llm_client

# Test data paths
TEST_DATA_DIR = Path(__file__).parent.parent / "data" / "refactoring"
SAMPLE_PROJECT_PATH = TEST_DATA_DIR / "sample_project"


# =============================================================================
# Prompts for Multi-Agent System
# =============================================================================

BRAIN_SYSTEM = """You are a senior software architect. Your role is to:
1. Analyze refactoring tasks carefully
2. Identify key requirements and constraints
3. Create clear, step-by-step instructions for a junior developer

Be thorough and explicit about what should and should NOT be changed."""

BRAIN_PROMPT = """## Refactoring Task Analysis

I need to rename the `old_api()` method of the `APIClient` class to `new_api()`.

Here's the project structure:
- core/client.py: Contains the APIClient class definition
- core/utils.py: Utility functions that use the client
- services/auth.py: Authentication service using the client
- services/data.py: Data processing service (has its own old_api function)
- tests/test_client.py: Tests for APIClient
- README.md: Documentation

### CRITICAL CONSTRAINT
The file `services/data.py` contains an INDEPENDENT function named `old_api()`
that is NOT part of APIClient. This function must NOT be modified.

### Your Task
Create a detailed instruction list for a developer to:
1. Identify exactly which files need changes
2. What specific patterns to look for
3. What to change and what to leave alone

Be very explicit about the `services/data.py` trap - explain why it should be skipped.

Output a clear, numbered instruction list."""

CODER_SYSTEM = """You are a precise code analyst. You follow instructions exactly.
Output your findings as a JSON array with the exact format specified."""

CODER_PROMPT_TEMPLATE = """## Instructions from Senior Architect

{brain_instructions}

---

## Code Files to Analyze

### core/client.py
```python
{client_content}
```

### core/utils.py
```python
{utils_content}
```

### services/auth.py
```python
{auth_content}
```

### services/data.py
```python
{data_content}
```

### tests/test_client.py
```python
{test_content}
```

### README.md
```markdown
{readme_content}
```

---

## Your Task

Following the architect's instructions above, identify ALL locations where
`APIClient.old_api` needs to be renamed to `new_api`.

Output ONLY a JSON array with this format:
```json
[
  {{
    "file": "filename.py",
    "line_context": "description",
    "old_code": "original snippet",
    "new_code": "modified snippet"
  }}
]
```

Remember: Do NOT include the independent `old_api()` function in services/data.py!"""


# =============================================================================
# Helper Functions
# =============================================================================

def load_project_files(project_path: Path) -> Dict[str, str]:
    """Load all relevant project files."""
    return {
        "client_content": (project_path / "core" / "client.py").read_text(),
        "utils_content": (project_path / "core" / "utils.py").read_text(),
        "auth_content": (project_path / "services" / "auth.py").read_text(),
        "data_content": (project_path / "services" / "data.py").read_text(),
        "test_content": (project_path / "tests" / "test_client.py").read_text(),
        "readme_content": (project_path / "README.md").read_text(),
    }


def parse_llm_response(response: str) -> List[Dict[str, str]]:
    """Parse LLM response to extract identified changes."""
    import re

    json_match = re.search(r'\[\s*\{.*?\}\s*\]', response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    return []


def evaluate_response(changes: List[Dict[str, str]]) -> Dict[str, Any]:
    """Evaluate the identified changes."""
    TRAP_FILE = "services/data.py"

    trap_mentioned = any(TRAP_FILE in c.get("file", "") for c in changes)

    found_definition = False
    found_utils = 0
    found_auth = 0
    found_tests = 0
    found_readme = 0

    for change in changes:
        file_name = change.get("file", "")
        old_code = change.get("old_code", "")

        if "client.py" in file_name and "def" in old_code.lower():
            found_definition = True
        elif "utils.py" in file_name:
            found_utils += 1
        elif "auth.py" in file_name:
            found_auth += 1
        elif "test" in file_name.lower():
            found_tests += 1
        elif "readme" in file_name.lower():
            found_readme += 1

    scores = {
        "definition": 1.0 if found_definition else 0.0,
        "utils": min(found_utils / 4, 1.0),
        "auth": min(found_auth / 4, 1.0),
        "tests": min(found_tests / 10, 1.0),
        "readme": min(found_readme / 3, 1.0),
        "no_trap": 0.0 if trap_mentioned else 1.0,
    }

    weights = {
        "definition": 0.15,
        "utils": 0.15,
        "auth": 0.15,
        "tests": 0.15,
        "readme": 0.10,
        "no_trap": 0.30,
    }

    total = sum(scores[k] * weights[k] for k in scores)

    return {
        "scores": scores,
        "total": round(total, 3),
        "changes_identified": len(changes),
        "trap_triggered": trap_mentioned,
        "details": {
            "found_definition": found_definition,
            "utils_calls": found_utils,
            "auth_calls": found_auth,
            "test_calls": found_tests,
            "readme_refs": found_readme,
        }
    }


# =============================================================================
# Multi-Agent Benchmark
# =============================================================================

async def run_multi_agent_benchmark(
    provider: str,
    model: Optional[str] = None,
    brain_model: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """Run multi-agent refactoring benchmark.

    Args:
        provider: LLM provider
        model: Model for Coder agent
        brain_model: Model for Brain agent (defaults to same as coder)
        verbose: Print progress
    """
    if verbose:
        print(f"\n{'='*60}")
        print("Multi-Agent Refactoring Benchmark")
        print(f"Provider: {provider}")
        print(f"Coder Model: {model or 'default'}")
        print(f"Brain Model: {brain_model or model or 'default'}")
        print("="*60)

    # Create LLM clients
    kwargs = {"provider": provider}
    if model:
        kwargs["model"] = model

    coder_client = create_llm_client(**kwargs)

    # Use same or different model for brain
    if brain_model and brain_model != model:
        brain_kwargs = {"provider": provider, "model": brain_model}
        brain_client = create_llm_client(**brain_kwargs)
    else:
        brain_client = coder_client

    # Set longer timeout for large models
    for client in [coder_client, brain_client]:
        if hasattr(client, 'config') and hasattr(client.config, 'timeout'):
            client.config.timeout = 600.0

    actual_model = getattr(coder_client, 'model', model or 'unknown')

    # Load files
    if verbose:
        print("\nLoading project files...")
    files = load_project_files(SAMPLE_PROJECT_PATH)

    # Phase 1: Brain Agent
    if verbose:
        print("\n--- Phase 1: Brain Agent (Architect) ---")
        print("Analyzing task and creating instructions...")
        brain_start = datetime.now()

    try:
        brain_prompt = f"{BRAIN_SYSTEM}\n\n{BRAIN_PROMPT}"
        brain_response = await brain_client.complete(prompt=brain_prompt)

        if verbose:
            brain_elapsed = (datetime.now() - brain_start).total_seconds()
            print(f"Brain completed in {brain_elapsed:.1f}s")
            print(f"Instructions length: {len(brain_response)} chars")
            # Show first few lines of instructions
            preview = brain_response[:500].replace('\n', '\n  ')
            print(f"  Preview: {preview}...")

    except Exception as e:
        if verbose:
            print(f"Brain Agent failed: {e}")
        return {
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "model": actual_model,
            "architecture": "multi-agent",
            "error": f"Brain agent failed: {e}",
            "total_score": 0.0,
        }

    # Phase 2: Coder Agent
    if verbose:
        print("\n--- Phase 2: Coder Agent (Developer) ---")
        print("Following instructions to identify changes...")
        coder_start = datetime.now()

    try:
        coder_prompt = CODER_PROMPT_TEMPLATE.format(
            brain_instructions=brain_response,
            **files
        )
        full_prompt = f"{CODER_SYSTEM}\n\n{coder_prompt}"
        coder_response = await coder_client.complete(prompt=full_prompt)

        if verbose:
            coder_elapsed = (datetime.now() - coder_start).total_seconds()
            print(f"Coder completed in {coder_elapsed:.1f}s")

    except Exception as e:
        if verbose:
            print(f"Coder Agent failed: {e}")
        return {
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "model": actual_model,
            "architecture": "multi-agent",
            "error": f"Coder agent failed: {e}",
            "total_score": 0.0,
        }

    # Evaluate
    if verbose:
        print("\nParsing and evaluating...")

    changes = parse_llm_response(coder_response)
    evaluation = evaluate_response(changes)

    if verbose:
        print(f"Identified {len(changes)} changes")
        print(f"\nResults for {provider}/{actual_model} (Multi-Agent):")
        print(f"  Method Definition:  {evaluation['scores']['definition']*100:.0f}%")
        print(f"  Utils Calls:        {evaluation['scores']['utils']*100:.0f}% ({evaluation['details']['utils_calls']}/4)")
        print(f"  Auth Calls:         {evaluation['scores']['auth']*100:.0f}% ({evaluation['details']['auth_calls']}/4)")
        print(f"  Test Calls:         {evaluation['scores']['tests']*100:.0f}% ({evaluation['details']['test_calls']}/10+)")
        print(f"  README Refs:        {evaluation['scores']['readme']*100:.0f}% ({evaluation['details']['readme_refs']}/3+)")
        print(f"  No False Positive:  {evaluation['scores']['no_trap']*100:.0f}%")
        print(f"  --------------------------------")
        print(f"  TOTAL SCORE:        {evaluation['total']*100:.1f}%")

    return {
        "timestamp": datetime.now().isoformat(),
        "provider": provider,
        "model": actual_model,
        "architecture": "multi-agent",
        "brain_instructions_length": len(brain_response),
        "coder_response_length": len(coder_response),
        "changes_count": len(changes),
        "evaluation": evaluation,
        "total_score": evaluation["total"],
    }


# =============================================================================
# Single Agent Benchmark (for comparison)
# =============================================================================

async def run_single_agent_benchmark(
    provider: str,
    model: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """Run single-agent benchmark for comparison."""
    # Import from existing benchmark
    from benchmark_llm_refactoring import run_benchmark

    result = await run_benchmark(provider, model, verbose)
    result["architecture"] = "single-agent"
    return result


# =============================================================================
# Main
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(description="Multi-Agent Refactoring Benchmark")
    parser.add_argument("--provider", type=str, required=True, help="LLM provider")
    parser.add_argument("--model", type=str, default=None, help="Coder model")
    parser.add_argument("--brain-model", type=str, default=None, help="Brain model (optional)")
    parser.add_argument("--compare", action="store_true", help="Compare with single-agent")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file")
    args = parser.parse_args()

    results = []

    # Run multi-agent
    multi_result = await run_multi_agent_benchmark(
        provider=args.provider,
        model=args.model,
        brain_model=args.brain_model,
        verbose=True
    )
    results.append(multi_result)

    # Optionally compare with single-agent
    if args.compare:
        print("\n" + "="*60)
        print("Running Single-Agent for comparison...")
        print("="*60)

        single_result = await run_single_agent_benchmark(
            provider=args.provider,
            model=args.model,
            verbose=True
        )
        results.append(single_result)

        # Print comparison
        print("\n" + "="*60)
        print("COMPARISON")
        print("="*60)
        print(f"Multi-Agent:  {multi_result.get('total_score', 0)*100:.1f}%")
        print(f"Single-Agent: {single_result.get('total_score', 0)*100:.1f}%")

        diff = multi_result.get('total_score', 0) - single_result.get('total_score', 0)
        if diff > 0:
            print(f"Improvement:  +{diff*100:.1f}%")
        elif diff < 0:
            print(f"Regression:   {diff*100:.1f}%")
        else:
            print("No difference")

    print("\n" + "="*60)
    print("BENCHMARK COMPLETE")
    print("="*60)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Results saved to: {args.output}")

    return 0


if __name__ == "__main__":
    asyncio.run(main())
