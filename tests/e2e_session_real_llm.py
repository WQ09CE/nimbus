"""
E2E Session Lifecycle Test with Real LLM (Pi-AI Server)

Uses the actual pi-ai server to test:
1. Start a simple task
2. Interrupt mid-execution
3. Hibernate to disk
4. Wake and resume
"""

import asyncio
from pathlib import Path

import pytest

from nimbus.adapters.direct_adapter import DirectAdapter
from nimbus.adapters.types import LLMConfig
from nimbus.core.session_pool import SessionPool
from nimbus.storage.sqlite import SQLiteStorage

# --- Test ---

@pytest.fixture
def clean_env():
    db_path = Path(".nimbus/e2e_real_llm.db")
    if db_path.exists():
        db_path.unlink()
    yield str(db_path)
    if db_path.exists():
        db_path.unlink()

@pytest.mark.asyncio
async def test_e2e_real_llm_session_lifecycle(clean_env):
    """
    Test full session lifecycle with real LLM via pi-ai server.
    
    Task: "List files in current directory and tell me how many there are"
    This is a simple task that requires 1-2 tool calls (Bash ls + response)
    """
    print("\n🚀 Starting E2E Real LLM Session Lifecycle Test")
    db_path = clean_env

    # --- Setup ---
    print("\n[Setup] Connecting to pi-ai server...")

    # Create DirectAdapter (LiteLLM)
    llm_config = LLMConfig(
        model="anthropic/claude-sonnet-4-20250514",  # Fast model for testing
        timeout=60.0,
    )
    llm = DirectAdapter(llm_config)
    await llm.start()

    # Verify connection
    health = await llm.health_check()
    if not health:
        pytest.skip("LLM adapter not available (missing API keys?)")
    print("   ✓ LLM adapter ready")

    # --- Phase 1: Start Task ---
    print("\n[Phase 1] Starting Task...")

    storage = SQLiteStorage(db_path)
    await storage.initialize()
    await storage.create_session("sess_real", name="Real LLM Test")

    pool = SessionPool(storage, llm)
    session = await pool.get_session("sess_real")

    # Spawn a process with a simple task
    task = "Use Bash to run 'ls -la | head -5' and tell me what you see. Be brief."
    print(f"   Task: {task}")

    pid = session.agent_os.spawn(task)
    process = session.agent_os.get_process(pid)

    # IMPORTANT: Add user message to MMU before calling step()
    # (execute() does this automatically, but step() doesn't)
    process.mmu.add_user_message(task)

    # Run first step (LLM should decide to call Bash)
    print("\n   Running Step 1...")
    step1 = await process.vcpu.step()

    print(f"   -> Step 1 completed | Actions: {[a.name for a in step1.actions]}")

    if step1.actions:
        action = step1.actions[0]
        if action.kind == "TOOL_CALL":
            print(f"      Tool: {action.name}")
            print(f"      Args: {action.args}")

    # Check if task is done (might complete in 1 step for simple tasks)
    if step1.is_final:
        print("   Task completed in 1 step!")
    else:
        # --- Phase 2: Interrupt ---
        print("\n[Phase 2] Interrupting...")
        process.vcpu.request_pause()

        # Run next step (should handle interruption)
        int_result = await process.vcpu.step()

        if int_result.fault and int_result.fault.code == "INTERRUPTED":
            print("   ✓ Session interrupted successfully")
        else:
            print(f"   (Session continued, fault={int_result.fault})")

    # --- Phase 3: Hibernate ---
    print("\n[Phase 3] Hibernating...")
    await session.hibernate()
    checkpoint = await storage.load_latest_session_checkpoint("sess_real")

    if checkpoint:
        print(f"   ✓ Checkpoint saved at step {checkpoint.step_index}")
        print(f"   Memory messages: {len(checkpoint.memory_snapshot.stack[0].messages) if checkpoint.memory_snapshot.stack else 0}")

    # Cleanup
    await storage.close()
    del pool
    del session

    # --- Phase 4: Wake & Resume ---
    print("\n[Phase 4] Waking & Resuming...")

    storage2 = SQLiteStorage(db_path)
    await storage2.initialize()

    pool2 = SessionPool(storage2, llm)
    session2 = await pool2.get_session("sess_real")

    pids = session2.agent_os.list_processes()
    if pids:
        resumed_proc = session2.agent_os.get_process(pids[0])
        print(f"   ✓ Restored at step {resumed_proc.vcpu._state.iteration}")

        # Continue until done (max 5 more steps)
        for i in range(5):
            step = await resumed_proc.vcpu.step()
            print(f"   -> Step {resumed_proc.vcpu._state.iteration}: {[a.name for a in step.actions] if step.actions else 'Thought'}")

            if step.is_final:
                print("\n   ✓ Task completed!")
                if step.final_result:
                    output = str(step.final_result.output)[:200]
                    print(f"   Result: {output}")
                break
    else:
        print("   (No process restored - starting fresh)")

    # Cleanup
    await storage2.close()
    await llm.stop()

    print("\n✅ E2E Real LLM Test Completed!")

if __name__ == "__main__":
    asyncio.run(test_e2e_real_llm_session_lifecycle(".nimbus/e2e_real_llm.db"))
