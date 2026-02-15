#!/usr/bin/env python3
"""
Local test script to verify Nimbus can complete the simple-coding-test task.

This tests the Nimbus agent without Harbor's container environment,
using the local pi-ai server.

Usage:
    python harbor/test_nimbus_task.py
"""
import asyncio
import os
import sys
import tempfile
import shutil
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nimbus.agentos import AgentOS
from nimbus.adapters.direct_adapter import DirectAdapter
from nimbus.adapters.types import LLMConfig


async def main():
    """Run the coding task test."""
    print("=" * 60)
    print("Testing Nimbus Agent with Harbor Task: simple-coding-test")
    print("=" * 60)

    # Read the instruction
    task_dir = Path(__file__).parent / "tasks" / "simple-coding-test"
    instruction_file = task_dir / "instruction.md"
    test_script = task_dir / "tests" / "test.sh"

    with open(instruction_file) as f:
        instruction = f.read()

    print(f"\n📝 Task instruction loaded from: {instruction_file}")
    print(f"   First 200 chars: {instruction[:200]}...")

    # Create a temporary working directory
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"\n📁 Working directory: {tmpdir}")
        os.chdir(tmpdir)

        # Initialize Nimbus
        print("\n🚀 Initializing Nimbus agent...")
        config = LLMConfig(model="anthropic/claude-sonnet-4-20250514")
        adapter = DirectAdapter(config=config)

        try:
            await adapter.start()
            print("   ✓ DirectAdapter started")

            agent = AgentOS(llm_client=adapter)
            print("   ✓ AgentOS initialized")

            # Run the task
            print("\n⏳ Running task...")
            print("-" * 60)

            result = await agent.run(
                f"""You are in the directory: {tmpdir}

{instruction}

IMPORTANT: Create the file 'solution.py' in the current directory.
After creating the file, verify it exists by listing the directory contents."""
            )

            print("-" * 60)
            print(f"\n✅ Task completed with status: {result.status}")
            if result.output:
                print(f"   Output: {result.output[:500]}...")

            # Check if solution.py was created
            solution_file = Path(tmpdir) / "solution.py"
            if solution_file.exists():
                print(f"\n📄 solution.py created! Size: {solution_file.stat().st_size} bytes")
                with open(solution_file) as f:
                    content = f.read()
                print(f"   First 300 chars:\n{content[:300]}")

                # Run verification
                print("\n🧪 Running verification tests...")
                os.environ["LOGS_DIR"] = tmpdir

                # Copy test script and run
                import subprocess
                result = subprocess.run(
                    ["bash", str(test_script)],
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "LOGS_DIR": tmpdir}
                )

                print(result.stdout)
                if result.returncode == 0:
                    # Read reward
                    reward_file = Path(tmpdir) / "reward.txt"
                    if reward_file.exists():
                        reward = float(reward_file.read_text().strip())
                        print(f"\n🏆 Final Score: {reward:.2f}")
                        if reward == 1.0:
                            print("🎉 PERFECT SCORE! Task completed successfully!")
                        elif reward > 0.5:
                            print("👍 PARTIAL SUCCESS")
                        else:
                            print("❌ FAILED")
                else:
                    print(f"Test script failed: {result.stderr}")
            else:
                print("\n❌ solution.py was NOT created!")
                print(f"   Files in {tmpdir}: {os.listdir(tmpdir)}")

        finally:
            await adapter.stop()
            print("\n🛑 Adapter stopped")


if __name__ == "__main__":
    asyncio.run(main())
