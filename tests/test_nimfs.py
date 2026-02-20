"""
Tests for NimFS: Nimbus Virtual Shared Disk

Covers: project_id, directory init, artifact write/read/list,
        memory write/read/search/load_context, GC, and error handling.
"""

import json
import time
from pathlib import Path

import pytest

from nimbus.core.nimfs import (
    ArtifactExpiredError,
    ArtifactNotFoundError,
    ArtifactTTL,
    MemoryCategory,
    MemoryScope,
    NimFSGC,
    NimFSManager,
    get_project_id,
    get_project_root,
    parse_nimfs_ref,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Temporary workspace directory."""
    ws = tmp_path / "test-workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def manager(workspace: Path) -> NimFSManager:
    return NimFSManager(str(workspace))


# =============================================================================
# 1. project_id conversion
# =============================================================================


def test_get_project_id_standard():
    pid = get_project_id("/Users/wangqing/sourcecode/nimbus")
    assert pid == "Users-wangqing-sourcecode-nimbus"


def test_get_project_id_short(tmp_path):
    # Use tmp_path so the path actually exists and resolves correctly on all platforms
    ws = tmp_path / "user" / "project"
    ws.mkdir(parents=True)
    pid = get_project_id(str(ws))
    # Should contain the path segments separated by '-', no '/' allowed
    assert "/" not in pid
    assert "project" in pid
    assert "user" in pid


def test_get_project_id_path_object(tmp_path):
    pid = get_project_id(tmp_path)
    assert "-" in pid
    assert "/" not in pid


# =============================================================================
# 2. Directory initialization
# =============================================================================


def test_project_root_creates_memory_dirs(workspace):
    root = get_project_root(str(workspace))
    for cat in ("profile", "preferences", "entities", "events", "cases", "patterns"):
        assert (root / "memory" / cat).is_dir()


def test_project_root_creates_artifacts_index(workspace):
    root = get_project_root(str(workspace))
    index = root / "artifacts" / "index.json"
    assert index.exists()
    assert json.loads(index.read_text()) == []


# =============================================================================
# 3. parse_nimfs_ref
# =============================================================================


def test_parse_nimfs_ref_artifact():
    kind, id_ = parse_nimfs_ref("nimfs://artifact/task-1-abc12345")
    assert kind == "artifact"
    assert id_ == "task-1-abc12345"


def test_parse_nimfs_ref_memory():
    kind, id_ = parse_nimfs_ref("nimfs://memory/entities-xyz98765")
    assert kind == "memory"
    assert id_ == "entities-xyz98765"


def test_parse_nimfs_ref_invalid():
    with pytest.raises(ValueError):
        parse_nimfs_ref("https://not-nimfs/something")

    with pytest.raises(ValueError):
        parse_nimfs_ref("nimfs://unknown/id")


# =============================================================================
# 4. Artifact write / read / list
# =============================================================================


def test_write_artifact_returns_ref(manager):
    ref = manager.write_artifact(
        content="print('hello nimfs')",
        task_id="task-test",
        producer="test-agent",
    )
    assert ref.startswith("nimfs://artifact/")
    assert "task-test" in ref


def test_write_read_artifact_roundtrip(manager):
    content = "A" * 50_000  # 50K — well above 16K ToolResult limit
    ref = manager.write_artifact(content=content, task_id="task-big", producer="impl-agent")
    result = manager.read_artifact(ref)
    assert result == content


def test_read_artifact_not_found(manager):
    with pytest.raises(ArtifactNotFoundError):
        manager.read_artifact("nimfs://artifact/nonexistent-id")


def test_artifact_manifest_committed(manager):
    ref = manager.write_artifact(
        content="data",
        task_id="task-status",
        producer="agent",
        ttl=ArtifactTTL.TASK,
        summary="test artifact",
        tags=["test"],
    )
    manifest = manager.get_artifact_manifest(ref)
    assert manifest.status.value == "committed"
    assert manifest.ttl == ArtifactTTL.TASK
    assert manifest.summary == "test artifact"
    assert "test" in manifest.tags


def test_list_artifacts_all(manager):
    manager.write_artifact("content-a", task_id="task-a", producer="agent")
    manager.write_artifact("content-b", task_id="task-b", producer="agent")
    artifacts = manager.list_artifacts()
    assert len(artifacts) >= 2


def test_list_artifacts_filtered_by_task(manager):
    manager.write_artifact("content-x", task_id="task-x", producer="agent")
    manager.write_artifact("content-y", task_id="task-y", producer="agent")
    results = manager.list_artifacts(task_id="task-x")
    assert all(m.task_id == "task-x" for m in results)


def test_artifact_type_extension(manager, workspace):
    """Verify correct file extension per artifact type."""
    type_ext = {"code": ".py", "report": ".md", "diff": ".diff", "json": ".json", "text": ".txt"}
    for atype, ext in type_ext.items():
        ref = manager.write_artifact("content", task_id=f"task-{atype}", producer="agent", artifact_type=atype)
        manifest = manager.get_artifact_manifest(ref)
        assert manifest.filename.endswith(ext), f"Expected {ext} for type {atype}"


# =============================================================================
# 5. Memory write / read / search
# =============================================================================


def test_write_memory_returns_id(manager):
    mid = manager.write_memory(
        category=MemoryCategory.ENTITIES,
        title="NimFSManager class",
        content="Full documentation of NimFSManager...",
        summary="Core coordinator for NimFS artifacts and memory",
    )
    assert mid.startswith("entities-")


def test_write_read_memory_layers(manager):
    content = "Detailed content. " * 200  # large content
    summary = "Compact summary"
    mid = manager.write_memory(
        category=MemoryCategory.CASES,
        title="Test case",
        content=content,
        summary=summary,
    )
    l0 = manager.read_memory(mid, layer=0)
    l1 = manager.read_memory(mid, layer=1)
    l2 = manager.read_memory(mid, layer=2)

    assert l0 == summary                   # L0 is the summary
    assert "Test case" in l1              # L1 contains title
    assert content in l2                  # L2 is full content
    assert len(l0) < len(l1) < len(l2)   # Layer sizes increase


def test_write_memory_invalid_layer(manager):
    mid = manager.write_memory(MemoryCategory.EVENTS, "event", "content")
    with pytest.raises(ValueError):
        manager.read_memory(mid, layer=3)


def test_search_memory_finds_by_title(manager):
    manager.write_memory(MemoryCategory.PATTERNS, "异步蒸馏模式", "Content about async distillation")
    manager.write_memory(MemoryCategory.PATTERNS, "同步阻塞反模式", "Content about sync blocking")

    results = manager.search_memory("蒸馏")
    titles = [e.title for e in results]
    assert "异步蒸馏模式" in titles
    assert "同步阻塞反模式" not in titles


def test_search_memory_by_tag(manager):
    manager.write_memory(
        MemoryCategory.ENTITIES, "Tagged entity", "content",
        tags=["nimfs", "ipc"],
    )
    results = manager.search_memory("nimfs")
    assert len(results) >= 1


def test_search_memory_category_filter(manager):
    manager.write_memory(MemoryCategory.EVENTS, "deploy event", "content")
    manager.write_memory(MemoryCategory.CASES, "deploy case", "content")

    results = manager.search_memory("deploy", category=MemoryCategory.EVENTS)
    assert all(e.category == MemoryCategory.EVENTS for e in results)


def test_search_memory_empty(manager):
    results = manager.search_memory("zzz-no-match-zzz")
    assert results == []


def test_memory_global_scope(manager):
    mid = manager.write_memory(
        category=MemoryCategory.PROFILE,
        title="Global profile",
        content="I am a Nimbus agent",
        scope=MemoryScope.GLOBAL,
    )
    assert mid.startswith("profile-")
    content = manager.read_memory(mid, layer=2)
    assert "I am a Nimbus agent" in content


# =============================================================================
# 6. load_context
# =============================================================================


def test_load_context_returns_string(manager):
    manager.write_memory(MemoryCategory.PROFILE, "Agent role", "I am an implementation agent",
                         summary="Implementation agent", scope=MemoryScope.GLOBAL)
    ctx = manager.load_context("implement NimFS module")
    assert isinstance(ctx, str)


def test_load_context_respects_budget(manager):
    for i in range(20):
        manager.write_memory(MemoryCategory.ENTITIES, f"Entity {i}", "x" * 500)
    ctx = manager.load_context("entity", max_chars=500)
    assert len(ctx) <= 600  # small tolerance


def test_load_context_empty_nimfs(manager):
    result = manager.load_context("some goal")
    # Should not crash, may return empty or minimal string
    assert isinstance(result, str)


# =============================================================================
# 7. GC
# =============================================================================


def test_gc_dry_run(manager):
    gc = NimFSGC()
    # Write a TASK artifact and manually backdate its created_at
    ref = manager.write_artifact("data", task_id="old-task", producer="agent", ttl=ArtifactTTL.TASK)
    manifest = manager.get_artifact_manifest(ref)

    # Backdate to 31 minutes ago
    task_dir = manager.artifacts_root / manifest.task_id
    manifest_path = task_dir / "manifest.json"
    data = json.loads(manifest_path.read_text())
    from datetime import datetime, timedelta, timezone
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    data["created_at"] = old_time
    manifest_path.write_text(json.dumps(data))

    # Update index too
    index_path = manager.artifacts_root / "index.json"
    records = json.loads(index_path.read_text())
    for r in records:
        if r.get("artifact_id") == manifest.artifact_id:
            r["created_at"] = old_time
    index_path.write_text(json.dumps(records))

    # dry_run should report 1 to clean but not delete
    cleaned = gc.gc_artifacts(str(manager.workspace_path), ttl_level=ArtifactTTL.TASK, dry_run=True)
    assert cleaned == 1

    # Content should still be readable
    content = manager.read_artifact(ref)
    assert content == "data"


def test_gc_actually_cleans(manager):
    gc = NimFSGC()
    ref = manager.write_artifact("delete-me", task_id="expired-task", producer="agent", ttl=ArtifactTTL.TASK)
    manifest = manager.get_artifact_manifest(ref)

    # Backdate
    task_dir = manager.artifacts_root / manifest.task_id
    manifest_path = task_dir / "manifest.json"
    data = json.loads(manifest_path.read_text())
    from datetime import datetime, timedelta, timezone
    data["created_at"] = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    manifest_path.write_text(json.dumps(data))

    index_path = manager.artifacts_root / "index.json"
    records = json.loads(index_path.read_text())
    for r in records:
        if r.get("artifact_id") == manifest.artifact_id:
            r["created_at"] = data["created_at"]
    index_path.write_text(json.dumps(records))

    cleaned = gc.gc_artifacts(str(manager.workspace_path), ttl_level=ArtifactTTL.TASK)
    assert cleaned == 1

    # Should now raise ArtifactExpiredError
    with pytest.raises(ArtifactExpiredError):
        manager.read_artifact(ref)


def test_defrag_removes_tombstones(manager):
    gc = NimFSGC()
    ref = manager.write_artifact("to-defrag", task_id="defrag-task", producer="agent", ttl=ArtifactTTL.TASK)
    manifest = manager.get_artifact_manifest(ref)

    # Manually expire
    task_dir = manager.artifacts_root / manifest.task_id
    manifest_path = task_dir / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["status"] = "expired"
    manifest_path.write_text(json.dumps(data))

    index_path = manager.artifacts_root / "index.json"
    records = json.loads(index_path.read_text())
    for r in records:
        if r.get("artifact_id") == manifest.artifact_id:
            r["status"] = "expired"
    index_path.write_text(json.dumps(records))

    stats = gc.defrag(str(manager.workspace_path))
    assert stats["tombstones_removed"] >= 1

    # Artifact should no longer be in index
    remaining = json.loads(index_path.read_text())
    ids = [r["artifact_id"] for r in remaining]
    assert manifest.artifact_id not in ids


# =============================================================================
# 8. Import smoke test
# =============================================================================


def test_tools_import():
    from nimbus.tools import (
        nimfs_write_artifact,
        nimfs_read_artifact,
        nimfs_list_artifacts,
        nimfs_write_memory,
        nimfs_search_memory,
        nimfs_load_context,
        NIMFS_TOOLS,
        NIMFS_TOOL_FUNCTIONS,
    )
    assert len(NIMFS_TOOLS) == 6
    assert "NimFSWriteArtifact" in NIMFS_TOOL_FUNCTIONS


# =============================================================================
# 9. Security: path traversal & concurrent write (C001/C003/H001/H002)
# =============================================================================


def test_c001_task_id_path_traversal_blocked(workspace):
    """C001: task_id with path traversal characters must be rejected."""
    manager = NimFSManager(str(workspace))
    malicious_ids = [
        "../../../etc/passwd",
        "..%2F..%2Fetc",
        "task/../../etc",
        "task\x00null",
        "a" * 200,          # too long
    ]
    for bad_id in malicious_ids:
        with pytest.raises(Exception):  # NimFSError or similar
            manager.write_artifact("content", task_id=bad_id, producer="attacker")


def test_h001_manifest_filename_traversal_blocked(workspace):
    """H001: crafted manifest.filename with path traversal must be rejected on read."""
    import json
    manager = NimFSManager(str(workspace))

    # Write a legitimate artifact first
    ref = manager.write_artifact("safe content", task_id="safe-task", producer="agent")
    manifest = manager.get_artifact_manifest(ref)

    # Tamper with manifest.filename to point outside artifacts root
    task_dir = manager.artifacts_root / manifest.task_id
    manifest_path = task_dir / "manifest.json"
    data = json.loads(manifest_path.read_text())
    data["filename"] = "../../../etc/passwd"
    manifest_path.write_text(json.dumps(data))

    # Attempting to read should raise NimFSError (path traversal detected)
    from nimbus.core.nimfs.models import NimFSError
    with pytest.raises(NimFSError):
        manager.read_artifact(ref)


def test_c003_concurrent_write_no_data_loss(workspace):
    """C003: concurrent writes to index.json must not lose records (atomic rename)."""
    import asyncio
    manager = NimFSManager(str(workspace))

    async def write_one(i: int):
        manager.write_artifact(f"content-{i}", task_id=f"task-{i}", producer="agent")

    async def run_all():
        await asyncio.gather(*[write_one(i) for i in range(10)])

    asyncio.run(run_all())

    # All 10 artifacts should survive
    artifacts = manager.list_artifacts()
    assert len(artifacts) == 10, f"Expected 10, got {len(artifacts)} (data loss detected)"


def test_h002_nimfs_tools_in_all_tools():
    """H002: all 6 NimFS tools must be registered in ALL_TOOLS."""
    from nimbus.tools import ALL_TOOLS, TOOL_FUNCTIONS
    names = {t["name"] for t in ALL_TOOLS}
    nimfs_names = {
        "NimFSWriteArtifact", "NimFSReadArtifact", "NimFSListArtifacts",
        "NimFSWriteMemory", "NimFSSearchMemory", "NimFSLoadContext",
    }
    assert nimfs_names.issubset(names), f"Missing from ALL_TOOLS: {nimfs_names - names}"
    # Also verify in TOOL_FUNCTIONS
    assert nimfs_names.issubset(set(TOOL_FUNCTIONS.keys()))
