"""Debug script to verify CodeAgent's Edit tool is working correctly.

This script:
1. Creates a temporary workspace (copies tests/data/refactoring/sample_project)
2. Creates a CodeAgent with that workspace
3. Runs a simple rename task (old_api -> new_api in core/client.py)
4. Prints detailed debug information
"""

import asyncio
import tempfile
import shutil
from pathlib import Path
import sys
import os

# Add project src to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from nimbus.apps.code_agent import CodeAgent


async def debug_refactoring():
    """Debug the Edit tool functionality."""
    print("=" * 60)
    print("DEBUG: CodeAgent Edit Tool Test")
    print("=" * 60)

    # Source data
    source_project = PROJECT_ROOT / "tests" / "data" / "refactoring" / "sample_project"

    if not source_project.exists():
        print(f"ERROR: Source project not found at {source_project}")
        return

    # Create temporary workspace
    temp_dir = tempfile.mkdtemp(prefix="nimbus_debug_")
    workspace = Path(temp_dir) / "sample_project"

    try:
        # Copy source project to temp workspace
        print(f"\n>>> Copying {source_project} -> {workspace}")
        shutil.copytree(source_project, workspace)

        target_file = workspace / "core" / "client.py"

        # Read original content
        print("\n=== ORIGINAL core/client.py (first 500 chars) ===")
        original = target_file.read_text()
        print(original[:500])
        print("...")

        # Check original contains old_api
        old_api_count = original.count("old_api")
        print(f"\n>>> 'old_api' appears {old_api_count} times in original file")

        # Create CodeAgent
        print("\n>>> Creating CodeAgent...")
        agent = CodeAgent(
            workspace=str(workspace),
            llm_provider="gemini",  # Use Gemini as default
        )

        # Run simple rename task
        print("\n>>> Running CodeAgent with Edit task...")
        print("Goal: Rename 'old_api' to 'new_api' in core/client.py")
        print("-" * 40)

        result = await agent.run(
            goal=(
                "In the file core/client.py, rename ALL occurrences of 'old_api' to 'new_api'. "
                "This includes the method name 'def old_api' and any references to it. "
                "First Read the file, then use Edit tool to make the changes."
            ),
            allowed_tools={"Read", "Edit"},
            max_turns=10,
        )

        # Print result details
        print("\n=== AGENT RESULT ===")
        print(f"Status: {result['status']}")
        print(f"Exit Code: {result['exit_code']}")
        print(f"Turns: {result['turns']}")
        print(f"Token Usage: {result['token_usage']}")

        if result.get("error"):
            print(f"Error: {result['error']}")

        print("\n--- Output ---")
        output = result.get("output", "")
        # Truncate long output
        if len(output) > 2000:
            print(output[:2000])
            print(f"... (truncated, total {len(output)} chars)")
        else:
            print(output if output else "(no output)")

        # Read modified content
        print("\n=== MODIFIED core/client.py (first 500 chars) ===")
        modified = target_file.read_text()
        print(modified[:500])
        print("...")

        # Check if modification was successful
        new_api_count = modified.count("new_api")
        old_api_still = modified.count("old_api")

        print(f"\n>>> After modification:")
        print(f"    'old_api' appears {old_api_still} times")
        print(f"    'new_api' appears {new_api_count} times")

        # Final verdict
        print("\n" + "=" * 60)
        if "def new_api" in modified and "def old_api" not in modified:
            print("SUCCESS: Method renamed correctly!")
            success = True
        elif old_api_still == 0 and new_api_count > 0:
            print("SUCCESS: All 'old_api' replaced with 'new_api'!")
            success = True
        else:
            print("FAILED: Renaming incomplete or failed")
            print(f"  - Expected: 'def new_api' present, 'def old_api' absent")
            print(f"  - Found: 'old_api' count={old_api_still}, 'new_api' count={new_api_count}")
            success = False
        print("=" * 60)

        # Close agent
        await agent.close()

        return success

    finally:
        # Cleanup
        print(f"\n>>> Cleaning up temp dir: {temp_dir}")
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    success = asyncio.run(debug_refactoring())
    sys.exit(0 if success else 1)
