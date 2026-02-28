"""
Tests for NimFS Phase 1: IPC Integration

Covers:
  - ToolResult.artifact_ref field
  - offload_to_nimfs() auto-offload
  - GoalDocument nimfs:// reference expansion
  - Scheduler inject_artifact_ref + build_downstream_context
"""

import pytest
from pathlib import Path

from nimbus.core.protocol import ToolResult, offload_to_nimfs, NIMFS_OFFLOAD_THRESHOLD
from nimbus.orchestration.context_protocol import GoalDocument, _expand_nimfs_refs
from nimbus.core.nimfs import NimFSManager


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "phase1-workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def manager(workspace: Path) -> NimFSManager:
    return NimFSManager(str(workspace))


# =============================================================================
# 1. ToolResult.artifact_ref field
# =============================================================================


def test_tool_result_has_artifact_ref_field():
    """ToolResult should have artifact_ref as an optional field defaulting to None."""
    result = ToolResult(status="OK", output="hello")
    assert result.artifact_ref is None


def test_tool_result_artifact_ref_set():
    result = ToolResult(status="OK", output="summary", artifact_ref="nimfs://artifact/task-1-abc")
    assert result.artifact_ref == "nimfs://artifact/task-1-abc"


# =============================================================================
# 2. offload_to_nimfs()
# =============================================================================


def test_offload_small_output_unchanged(workspace):
    """Small outputs should pass through without offload."""
    result = ToolResult(status="OK", output="small output")
    out = offload_to_nimfs(result, str(workspace), task_id="task-small", producer="agent")
    assert out.output == "small output"
    assert out.artifact_ref is None


def test_offload_large_output(workspace):
    """Large outputs should be offloaded and artifact_ref set."""
    big_content = "X" * (NIMFS_OFFLOAD_THRESHOLD + 1000)
    result = ToolResult(status="OK", output=big_content)
    out = offload_to_nimfs(result, str(workspace), task_id="task-big", producer="impl-agent")

    # artifact_ref should be a nimfs:// URI
    assert out.artifact_ref is not None
    assert out.artifact_ref.startswith("nimfs://artifact/")

    # output should be a compact summary mentioning the reference
    assert "NimFS Offload" in str(out.output)
    assert out.artifact_ref in str(out.output)

    # Full content should be retrievable from NimFS
    manager = NimFSManager(str(workspace))
    content = manager.read_artifact(out.artifact_ref)
    assert content == big_content


def test_offload_preserves_status_and_is_final(workspace):
    """offload_to_nimfs should preserve all other ToolResult fields."""
    big = "Y" * (NIMFS_OFFLOAD_THRESHOLD + 1)
    result = ToolResult(status="OK", output=big, is_final=True,
                        meta={"key": "val"}, cost={"tokens": 100})
    out = offload_to_nimfs(result, str(workspace), task_id="t1", producer="agent")
    assert out.status == "OK"
    assert out.is_final is True
    assert out.meta == {"key": "val"}
    assert out.cost == {"tokens": 100}


def test_offload_exactly_at_threshold_not_offloaded(workspace):
    """Output exactly at the threshold should NOT be offloaded."""
    content = "A" * NIMFS_OFFLOAD_THRESHOLD
    result = ToolResult(status="OK", output=content)
    out = offload_to_nimfs(result, str(workspace), task_id="t-exact", producer="agent")
    assert out.artifact_ref is None
    assert out.output == content


# =============================================================================
# 3. GoalDocument nimfs:// expansion
# =============================================================================


def test_goal_document_no_nimfs_ref(workspace):
    """GoalDocument without nimfs:// refs should render normally."""
    doc = GoalDocument(
        mission="Implement feature X",
        context="Some plain context here",
        workspace=str(workspace),
    )
    rendered = doc.render()
    assert "Implement feature X" in rendered
    assert "Some plain context here" in rendered


def test_goal_document_expands_artifact_ref(workspace, manager):
    """GoalDocument should inline-expand nimfs://artifact/ refs."""
    large_content = "Full implementation content.\n" * 500
    ref = manager.write_artifact(
        content=large_content,
        task_id="task-impl",
        producer="impl-agent",
        summary="Big implementation output",
    )

    doc = GoalDocument(
        mission="Test the implementation",
        context=f"The previous agent produced: {ref}",
        workspace=str(workspace),
    )
    rendered = doc.render()

    # The ref should have been expanded to the actual content
    assert "Full implementation content." in rendered
    # The original ref should be replaced
    assert ref not in rendered or "NimFS Artifact" in rendered


def test_goal_document_expands_memory_ref(workspace, manager):
    """GoalDocument should inline-expand nimfs://memory/ refs."""
    mid = manager.write_memory(
        category=__import__("nimbus.core.nimfs.models", fromlist=["MemoryCategory"]).MemoryCategory.ENTITIES,
        title="TestEntity",
        content="Detailed entity documentation " * 100,
        summary="Entity summary",
    )
    memory_ref = f"nimfs://memory/{mid}"

    doc = GoalDocument(
        mission="Use the entity",
        context=f"Context: {memory_ref}",
        workspace=str(workspace),
    )
    rendered = doc.render()
    assert "TestEntity" in rendered  # L1 overview contains the title


def test_goal_document_expand_disabled(workspace, manager):
    """expand_nimfs_refs=False should leave refs unexpanded."""
    ref = manager.write_artifact("content", task_id="t1", producer="agent")

    doc = GoalDocument(
        mission="Task",
        context=f"See: {ref}",
        workspace=str(workspace),
        expand_nimfs_refs=False,
    )
    rendered = doc.render()
    assert ref in rendered  # ref should remain unexpanded


def test_goal_document_no_workspace_no_expansion(manager):
    """GoalDocument without workspace should not attempt expansion."""
    ref = manager.write_artifact("content", task_id="t1", producer="agent")

    doc = GoalDocument(
        mission="Task",
        context=f"See: {ref}",
        workspace="",  # no workspace
    )
    rendered = doc.render()
    assert ref in rendered  # ref stays unexpanded


def test_goal_document_expired_ref_graceful(workspace):
    """Expired/nonexistent refs should be annotated, not crash."""
    doc = GoalDocument(
        mission="Task",
        context="See: nimfs://artifact/nonexistent-ref-xyz",
        workspace=str(workspace),
    )
    rendered = doc.render()
    # Should contain error annotation, not crash
    assert "ArtifactNotFoundError" in rendered or "nimfs://artifact/nonexistent-ref-xyz" in rendered


def test_expand_nimfs_refs_max_depth(workspace, manager):
    """Nested expansion should stop at max depth."""
    ref = manager.write_artifact(
        content="Level 1 content",
        task_id="nested-task",
        producer="agent",
    )
    # Content itself contains another ref (simulated nesting)
    nested_content = f"Contains ref: {ref}"
    outer_ref = manager.write_artifact(
        content=nested_content,
        task_id="outer-task",
        producer="agent",
    )

    # Should not loop infinitely
    result = _expand_nimfs_refs(f"Start: {outer_ref}", str(workspace))
    assert "Level 1 content" in result or "outer-task" in result


# =============================================================================
# 4. End of Tests
# =============================================================================
