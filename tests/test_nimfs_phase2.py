"""
Tests for NimFS Phase 2: MMU Auto-Offload + Session GC

Covers:
  - MMUConfig.nimfs_offload_threshold 配置
  - MMU.add_tool_result() 自动 offload 大结果
  - MMU.nimfs_workspace 注入
  - AgentOS._nimfs_gc_task / _nimfs_gc_session 静默执行
"""

import json
from pathlib import Path

import pytest

from nimbus.core.memory.mmu import MMU, MMUConfig
from nimbus.core.nimfs import NimFSManager, ArtifactTTL


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "phase2-ws"
    ws.mkdir()
    return ws


@pytest.fixture
def mmu_with_nimfs(workspace: Path) -> MMU:
    """MMU configured with NimFS offload enabled."""
    config = MMUConfig(nimfs_offload_threshold=500)  # low threshold for testing
    mmu = MMU(config=config, process_id="test-proc")
    mmu.nimfs_workspace = str(workspace)
    return mmu


@pytest.fixture
def mmu_no_nimfs() -> MMU:
    """MMU without NimFS workspace (offload disabled)."""
    config = MMUConfig(nimfs_offload_threshold=500)
    mmu = MMU(config=config, process_id="test-proc-no-nimfs")
    mmu.nimfs_workspace = None
    return mmu


# =============================================================================
# 1. MMUConfig
# =============================================================================


def test_mmu_config_default_threshold():
    """Default offload threshold should be 8000."""
    config = MMUConfig()
    assert config.nimfs_offload_threshold == 8_000


def test_mmu_config_disable_threshold():
    """Setting threshold to 0 should disable offload."""
    config = MMUConfig(nimfs_offload_threshold=0)
    assert config.nimfs_offload_threshold == 0


# =============================================================================
# 2. MMU nimfs_workspace 注入
# =============================================================================


def test_mmu_nimfs_workspace_default_none():
    """MMU should start with nimfs_workspace=None (offload disabled)."""
    mmu = MMU()
    assert mmu.nimfs_workspace is None


def test_mmu_nimfs_workspace_set(workspace):
    """Setting nimfs_workspace should enable offload."""
    mmu = MMU()
    mmu.nimfs_workspace = str(workspace)
    assert mmu.nimfs_workspace == str(workspace)


# =============================================================================
# 3. add_tool_result auto-offload
# =============================================================================


def test_add_tool_result_small_not_offloaded(mmu_with_nimfs, workspace):
    """Small tool results should be stored in MMU directly, not offloaded."""
    small = "x" * 100  # well below 500 threshold
    mmu_with_nimfs.add_tool_result("call-1", "ReadFile", small)

    messages = mmu_with_nimfs.current_frame.messages
    tool_msg = next((m for m in messages if m.role == "tool"), None)
    assert tool_msg is not None
    assert tool_msg.content == small  # unchanged

    # No artifacts should have been created
    manager = NimFSManager(str(workspace))
    artifacts = manager.list_artifacts()
    assert len(artifacts) == 0


def test_add_tool_result_large_offloaded(mmu_with_nimfs, workspace):
    """Large tool results should be offloaded to NimFS."""
    large = "A" * 1000  # above 500 threshold

    mmu_with_nimfs.add_tool_result("call-2", "BashCommand", large)

    messages = mmu_with_nimfs.current_frame.messages
    tool_msg = next((m for m in messages if m.role == "tool"), None)
    assert tool_msg is not None

    content = tool_msg.content
    # Should contain NimFS offload notice
    assert "NimFS Auto-Offload" in content
    assert "nimfs://artifact/" in content
    # Should NOT contain the original large content inline
    assert "A" * 1000 not in content

    # NimFS should have the artifact
    manager = NimFSManager(str(workspace))
    artifacts = manager.list_artifacts()
    assert len(artifacts) == 1
    assert artifacts[0].size_bytes >= 1000


def test_add_tool_result_large_retrieval(mmu_with_nimfs, workspace):
    """Full content should be retrievable from NimFS after offload."""
    large = "FULL_CONTENT_" * 100  # 1300 chars, above threshold

    mmu_with_nimfs.add_tool_result("call-3", "Explore", large)

    # Extract ref from MMU message
    messages = mmu_with_nimfs.current_frame.messages
    tool_msg = next((m for m in messages if m.role == "tool"), None)
    assert tool_msg is not None

    # Find the nimfs:// ref in the message
    import re
    refs = re.findall(r"nimfs://artifact/[\w\-]+", tool_msg.content)
    assert len(refs) >= 1
    ref = refs[0]  # same ref may appear multiple times in the message

    # Retrieve full content
    manager = NimFSManager(str(workspace))
    retrieved = manager.read_artifact(ref)
    assert retrieved == large


def test_add_tool_result_no_workspace_not_offloaded(mmu_no_nimfs):
    """Without workspace, large results should NOT be offloaded (graceful degradation)."""
    large = "B" * 1000  # above threshold

    mmu_no_nimfs.add_tool_result("call-4", "BigTool", large)

    messages = mmu_no_nimfs.current_frame.messages
    tool_msg = next((m for m in messages if m.role == "tool"), None)
    assert tool_msg is not None
    # Content should be unchanged
    assert tool_msg.content == large
    assert "NimFS" not in tool_msg.content


def test_add_tool_result_threshold_zero_not_offloaded(workspace):
    """threshold=0 should disable offload entirely."""
    config = MMUConfig(nimfs_offload_threshold=0)
    mmu = MMU(config=config)
    mmu.nimfs_workspace = str(workspace)

    large = "C" * 2000
    mmu.add_tool_result("call-5", "SomeTool", large)

    messages = mmu.current_frame.messages
    tool_msg = next((m for m in messages if m.role == "tool"), None)
    assert tool_msg.content == large  # not offloaded


def test_add_tool_result_offload_counter_increments(mmu_with_nimfs, workspace):
    """Each offload should use a unique task_id (counter-based)."""
    large = "D" * 1000

    mmu_with_nimfs.add_tool_result("c1", "ToolA", large)
    mmu_with_nimfs.add_tool_result("c2", "ToolB", large)

    assert mmu_with_nimfs._nimfs_offload_counter == 2

    manager = NimFSManager(str(workspace))
    artifacts = manager.list_artifacts()
    assert len(artifacts) == 2
    # Each should have a different task_id
    task_ids = {a.task_id for a in artifacts}
    assert len(task_ids) == 2


# =============================================================================
# 4. AgentOS GC helpers (smoke test — no real AgentOS needed)
# =============================================================================


def test_nimfs_gc_task_silent_on_no_workspace():
    """_nimfs_gc_task should not crash when workspace is unavailable."""
    from nimbus.core.nimfs.gc import NimFSGC
    from nimbus.core.nimfs.models import ArtifactTTL

    # Simulate calling gc on a nonexistent path — should not raise
    gc = NimFSGC()
    count = gc.gc_artifacts("/nonexistent/path/xyz", ttl_level=ArtifactTTL.TASK)
    assert count == 0


def test_nimfs_gc_session_cleans_session_artifacts(workspace):
    """gc_session should clean TASK + SESSION level artifacts."""
    from datetime import datetime, timedelta, timezone
    from nimbus.core.nimfs.gc import NimFSGC

    manager = NimFSManager(str(workspace))
    gc = NimFSGC()

    # Write one TASK artifact (backdated to 31 min ago)
    ref_task = manager.write_artifact("task data", task_id="task-gc", producer="agent",
                                       ttl=ArtifactTTL.TASK)
    manifest = manager.get_artifact_manifest(ref_task)
    _backdate_artifact(manager, manifest, minutes=31)

    # Write one SESSION artifact (backdated to 25 hours ago)
    ref_session = manager.write_artifact("session data", task_id="session-gc", producer="agent",
                                          ttl=ArtifactTTL.SESSION)
    manifest_s = manager.get_artifact_manifest(ref_session)
    _backdate_artifact(manager, manifest_s, minutes=25 * 60)

    # Write one PERMANENT artifact (should NOT be cleaned)
    ref_perm = manager.write_artifact("permanent data", task_id="perm-gc", producer="agent",
                                       ttl=ArtifactTTL.PERMANENT)

    cleaned = gc.gc_session(str(workspace))
    assert cleaned >= 2  # task + session

    # Permanent should still be readable
    content = manager.read_artifact(ref_perm)
    assert content == "permanent data"


# =============================================================================
# Helpers
# =============================================================================


def _backdate_artifact(manager: NimFSManager, manifest, minutes: int):
    """Backdate an artifact's created_at for GC testing."""
    from datetime import datetime, timedelta, timezone

    task_dir = manager.artifacts_root / manifest.task_id
    manifest_path = task_dir / "manifest.json"
    data = json.loads(manifest_path.read_text())
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    data["created_at"] = old_time
    manifest_path.write_text(json.dumps(data))

    # Also update index
    index_path = manager.artifacts_root / "index.json"
    records = json.loads(index_path.read_text())
    for r in records:
        if r.get("artifact_id") == manifest.artifact_id:
            r["created_at"] = old_time
    index_path.write_text(json.dumps(records))
