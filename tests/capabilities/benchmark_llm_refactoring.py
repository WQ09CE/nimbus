#!/usr/bin/env python3
"""Direct LLM Refactoring Benchmark.

This script directly tests the LLM's ability to understand and generate
code refactoring changes, bypassing the agent's planner.

Usage:
    python tests/capabilities/benchmark_llm_refactoring.py --provider gemini
    python tests/capabilities/benchmark_llm_refactoring.py --provider ollama --model qwen3:8b
"""

import asyncio
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from nimbus.llm import create_llm_client

# Test data paths
TEST_DATA_DIR = Path(__file__).parent.parent / "data" / "refactoring"
SAMPLE_PROJECT_PATH = TEST_DATA_DIR / "sample_project"
GOLDEN_PATH = TEST_DATA_DIR / "golden"


# =============================================================================
# Test Prompts
# =============================================================================

SYSTEM_PROMPT = """You are an expert code refactoring assistant. You analyze code and identify all locations that need to be modified for a refactoring task."""

ANALYSIS_PROMPT = """I need to rename `old_api()` method of the `APIClient` class to `new_api()`.

Here are the files in the project:

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

IMPORTANT: The file `services/data.py` contains an INDEPENDENT function named `old_api()` that is NOT related to `APIClient.old_api()`. DO NOT modify this function.

Please analyze the code and list ALL locations where `APIClient.old_api` is called and needs to be renamed to `new_api`. For each location, specify:
1. File name
2. Line number or context
3. The old code
4. The new code after change

Format your response as a JSON array:
```json
[
  {{
    "file": "file_name.py",
    "line_context": "description or line number",
    "old_code": "original code snippet",
    "new_code": "modified code snippet"
  }}
]
```

Be thorough and find ALL occurrences. Do not include the independent `old_api()` function in `services/data.py`."""


# =============================================================================
# Expected Changes (Golden Standard)
# =============================================================================

EXPECTED_CHANGES = [
    # core/client.py - method definition
    {"file": "core/client.py", "type": "definition", "old": "def old_api", "new": "def new_api"},
    # core/utils.py - 4 calls
    {"file": "core/utils.py", "type": "call", "old": "client.old_api()", "count": 4},
    # services/auth.py - 4 calls
    {"file": "services/auth.py", "type": "call", "old": "self.client.old_api()", "count": 4},
    # tests/test_client.py - multiple calls
    {"file": "tests/test_client.py", "type": "call", "old": "client.old_api()", "count_min": 10},
    # README.md - documentation
    {"file": "README.md", "type": "doc", "old": "old_api", "count_min": 3},
]

TRAP_FILE = "services/data.py"


# =============================================================================
# Scoring Functions
# =============================================================================

def load_project_files(project_path: Path) -> Dict[str, str]:
    """Load all relevant project files."""
    files = {
        "client_content": (project_path / "core" / "client.py").read_text(),
        "utils_content": (project_path / "core" / "utils.py").read_text(),
        "auth_content": (project_path / "services" / "auth.py").read_text(),
        "data_content": (project_path / "services" / "data.py").read_text(),
        "test_content": (project_path / "tests" / "test_client.py").read_text(),
        "readme_content": (project_path / "README.md").read_text(),
    }
    return files


def parse_llm_response(response: str) -> List[Dict[str, str]]:
    """Parse LLM response to extract identified changes."""
    # Try to extract JSON from response
    import re

    # Find JSON array in response
    json_match = re.search(r'\[\s*\{.*?\}\s*\]', response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

    # Fallback: try to parse the entire response as JSON
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Return empty if parsing fails
    return []


def evaluate_response(changes: List[Dict[str, str]]) -> Dict[str, Any]:
    """Evaluate the LLM's identified changes against expected changes."""

    # Count files mentioned
    files_mentioned = set(c.get("file", "") for c in changes)

    # Check for trap (false positive)
    trap_mentioned = any(TRAP_FILE in c.get("file", "") for c in changes)

    # Check each expected location
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

    # Calculate scores
    scores = {
        "definition": 1.0 if found_definition else 0.0,
        "utils": min(found_utils / 4, 1.0),
        "auth": min(found_auth / 4, 1.0),
        "tests": min(found_tests / 10, 1.0),
        "readme": min(found_readme / 3, 1.0),
        "no_trap": 0.0 if trap_mentioned else 1.0,
    }

    # Weighted total
    weights = {
        "definition": 0.15,
        "utils": 0.15,
        "auth": 0.15,
        "tests": 0.15,
        "readme": 0.10,
        "no_trap": 0.30,  # Heavy penalty for false positive
    }

    total = sum(scores[k] * weights[k] for k in scores)

    return {
        "scores": scores,
        "weights": weights,
        "total": round(total, 3),
        "files_found": len(files_mentioned),
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
# Benchmark Runner
# =============================================================================

async def run_benchmark(
    provider: str,
    model: Optional[str] = None,
    verbose: bool = True
) -> Dict[str, Any]:
    """Run the refactoring analysis benchmark."""

    if verbose:
        print(f"\n{'='*60}")
        print(f"LLM Refactoring Analysis Benchmark")
        print(f"Provider: {provider}")
        if model:
            print(f"Model: {model}")
        print("="*60)

    # Create LLM client
    kwargs = {"provider": provider}
    if model:
        kwargs["model"] = model

    if verbose:
        print("Creating LLM client...")

    # Set longer timeout for large models
    llm_client = create_llm_client(**kwargs)
    if hasattr(llm_client, 'config') and hasattr(llm_client.config, 'timeout'):
        llm_client.config.timeout = 600.0  # 10 minutes for large models
    actual_model = getattr(llm_client, 'model', model or 'unknown')

    # Load project files
    if verbose:
        print("Loading project files...")

    files = load_project_files(SAMPLE_PROJECT_PATH)

    # Create prompt
    prompt = ANALYSIS_PROMPT.format(**files)

    if verbose:
        print("Sending request to LLM...")
        start_time = datetime.now()

    # Call LLM
    try:
        # Combine system prompt with user prompt
        full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"
        response = await llm_client.complete(prompt=full_prompt)

        if verbose:
            elapsed = (datetime.now() - start_time).total_seconds()
            print(f"Response received in {elapsed:.1f}s")

        # Parse response
        if verbose:
            print("Parsing response...")

        changes = parse_llm_response(response)

        if verbose:
            print(f"Identified {len(changes)} changes")

        # Evaluate
        if verbose:
            print("Evaluating...")

        evaluation = evaluate_response(changes)

        result = {
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "model": actual_model,
            "response_length": len(response),
            "changes_count": len(changes),
            "evaluation": evaluation,
            "total_score": evaluation["total"],
        }

        if verbose:
            print(f"\nResults for {provider}/{actual_model}:")
            print(f"  Method Definition:  {evaluation['scores']['definition']*100:.0f}%")
            print(f"  Utils Calls:        {evaluation['scores']['utils']*100:.0f}% ({evaluation['details']['utils_calls']}/4)")
            print(f"  Auth Calls:         {evaluation['scores']['auth']*100:.0f}% ({evaluation['details']['auth_calls']}/4)")
            print(f"  Test Calls:         {evaluation['scores']['tests']*100:.0f}% ({evaluation['details']['test_calls']}/10+)")
            print(f"  README Refs:        {evaluation['scores']['readme']*100:.0f}% ({evaluation['details']['readme_refs']}/3+)")
            print(f"  No False Positive:  {evaluation['scores']['no_trap']*100:.0f}%")
            print(f"  --------------------------------")
            print(f"  TOTAL SCORE:        {evaluation['total']*100:.1f}%")

        return result

    except Exception as e:
        if verbose:
            print(f"Error: {e}")
        return {
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "model": actual_model,
            "error": str(e),
            "total_score": 0.0,
        }


# =============================================================================
# Main
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(description="LLM Refactoring Analysis Benchmark")
    parser.add_argument("--provider", type=str, required=True, help="LLM provider")
    parser.add_argument("--model", type=str, default=None, help="Model name")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file")
    args = parser.parse_args()

    result = await run_benchmark(
        provider=args.provider,
        model=args.model,
        verbose=True
    )

    print("\n" + "="*60)
    print("BENCHMARK COMPLETE")
    print("="*60)
    print(f"Total Score: {result.get('total_score', 0)*100:.1f}%")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to: {args.output}")

    return 0


if __name__ == "__main__":
    asyncio.run(main())
