"""
Test for Session Pool and Storage Integration (Phase 2)

Verifies:
1. SQLiteStorage checkpoint save/load
2. SessionPool lifecycle (get -> use -> hibernate -> wake)
3. State persistence across hibernation
"""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

import pytest

from nimbus.core.persistence import ExecutionStateModel, MemorySnapshotModel, SessionCheckpointModel
from nimbus.core.session_pool import SessionPool
from nimbus.storage.sqlite import SQLiteStorage

# --- Mock AgentOS and vCPU ---

class MockLLMClient:
    async def chat(self, messages, tools=None):
        return MockResponse("response")

@dataclass
class MockResponse:
    content: str
    tool_calls: Optional[List[Any]] = None

class MockVCPU:
    def __init__(self):
        self.iteration = 0
        self.memory_content = "initial"

    def create_checkpoint(self, session_id: str, reason: str) -> SessionCheckpointModel:
        # Create minimal valid models
        exec_state = ExecutionStateModel(
            iteration=self.iteration,
            max_iterations=10,
            is_running=True,
            is_done=False,
            final_result=None,
            consecutive_thoughts=0,
            consecutive_errors=0,
            consecutive_empty_responses=0,
            compaction_count=0,
            max_compactions=10,
            tool_failure_counts={},
            path_not_found_count=0
        )
        # Minimal memory snapshot
        mem_snapshot = MemorySnapshotModel(
            process_id="proc_1",
            tool_markers={"content": self.memory_content} # Abuse this field for simple test
        )

        return SessionCheckpointModel(
            session_id=session_id,
            timestamp=time.time(),
            step_index=self.iteration,
            execution_state=exec_state,
            memory_snapshot=mem_snapshot,
            reason=reason,
            can_resume=True
        )

    def restore_from_checkpoint(self, checkpoint: SessionCheckpointModel):
        self.iteration = checkpoint.execution_state.iteration
        self.memory_content = checkpoint.memory_snapshot.tool_markers.get("content", "")

class MockProcess:
    def __init__(self):
        self.vcpu = MockVCPU()

class MockAgentOS:
    def __init__(self, config=None, llm_client=None):
        self.config = config
        self._processes = {"proc_1": MockProcess()}

    def get_active_processes(self):
        return ["proc_1"]

    def list_processes(self):
        return ["proc_1"]

    def get_process(self, pid):
        return self._processes.get(pid)

    def spawn(self, goal, role):
        # Always return the fixed proc for simplicity in mock
        return "proc_1"

# --- Tests ---

@pytest.fixture
def test_db_path():
    path = Path(".nimbus/test_session_pool.db")
    if path.exists():
        path.unlink()
    yield str(path)
    if path.exists():
        path.unlink()

@pytest.mark.asyncio
async def test_storage_checkpoint_operations(test_db_path):
    storage = SQLiteStorage(test_db_path)
    await storage.initialize()

    # 0. Create Session First (FK Constraint)
    await storage.create_session("sess_1", name="Test Session")

    # 1. Create dummy checkpoint
    exec_state = ExecutionStateModel(
        iteration=5,
        max_iterations=10,
        is_running=True,
        is_done=False,
        final_result=None,
        consecutive_thoughts=0,
        consecutive_errors=0,
        consecutive_empty_responses=0,
        compaction_count=0,
        max_compactions=10,
        tool_failure_counts={},
        path_not_found_count=0
    )
    mem_snapshot = MemorySnapshotModel(process_id="test_proc")

    ckpt = SessionCheckpointModel(
        session_id="sess_1",
        timestamp=time.time(),
        step_index=5,
        execution_state=exec_state,
        memory_snapshot=mem_snapshot,
        reason="test"
    )

    # 2. Save
    ckpt_id = await storage.save_session_checkpoint(ckpt)
    assert ckpt_id is not None

    # 3. Load
    loaded = await storage.load_latest_session_checkpoint("sess_1")
    assert loaded is not None
    assert loaded.session_id == "sess_1"
    assert loaded.step_index == 5
    assert loaded.execution_state.iteration == 5

    # 4. Load non-existent
    empty = await storage.load_latest_session_checkpoint("sess_none")
    assert empty is None

    await storage.close()

@pytest.mark.asyncio
async def test_session_pool_lifecycle(test_db_path):
    storage = SQLiteStorage(test_db_path)
    await storage.initialize()

    # 0. Create Session First (FK Constraint for checkpoints)
    await storage.create_session("sess_A", name="Test Session A")

    # Mock LLM Client
    llm = MockLLMClient()

    pool = SessionPool(storage, llm)

    # 1. Get Session (Create)
    session = await pool.get_session("sess_A")
    assert session is not None
    assert session.is_active

    # Mock the AgentOS inside session to simulate work
    # We replace the REAL AgentOS (initialized by get_session) with our Mock
    session.agent_os = MockAgentOS(llm_client=llm)
    session.agent_os._processes["proc_1"].vcpu.iteration = 10
    session.agent_os._processes["proc_1"].vcpu.memory_content = "processed data"

    # 2. Hibernate
    success = await session.hibernate()
    assert success is True
    assert not session.is_active
    assert session.agent_os is None

    # Verify DB has checkpoint
    loaded_ckpt = await storage.load_latest_session_checkpoint("sess_A")
    assert loaded_ckpt.step_index == 10

    # 3. Wake (Restore)
    # This will create a REAL AgentOS and spawn a REAL process
    # But for our test assertions we want to verify it called restore_from_checkpoint

    # To verify restore, we need to inspect the real VCPU.
    # The real VCPU won't have "memory_content" attribute from our MockVCPU (unless we hack it)
    # But it WILL have `iteration`.

    woken_session = await pool.get_session("sess_A")
    assert woken_session is not None
    assert woken_session.is_active

    # Find the process (should have spawned one)
    active_pids = woken_session.agent_os.list_processes()
    assert len(active_pids) > 0
    proc = woken_session.agent_os.get_process(active_pids[0])

    # Verify execution state was restored
    # The restored iteration should be 10
    assert proc.vcpu._state.iteration == 10

    await storage.close()

if __name__ == "__main__":
    asyncio.run(test_storage_checkpoint_operations(".nimbus/test_manual.db"))
