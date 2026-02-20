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
# 4. Scheduler: inject_artifact_ref + build_downstream_context
# =============================================================================


def test_scheduler_inject_artifact_ref():
    """inject_artifact_ref should store the ref in the result store."""
    from nimbus.core.scheduler import Scheduler

    sched = Scheduler()
    # inject_artifact_ref uses inject_result internally
    sched.inject_artifact_ref("dag-1", "task-a", "nimfs://artifact/task-a-abc123")

    val = sched.get_injected_result("dag-1", "task-a", "artifact_ref")
    assert val == "nimfs://artifact/task-a-abc123"


def test_scheduler_build_downstream_context_with_artifact_ref():
    """build_downstream_context should include nimfs:// refs from upstream tasks."""
    from nimbus.core.scheduler import Scheduler, Task, TaskSpec, create_dag

    sched = Scheduler()

    task_a = Task(id="task-a", spec=TaskSpec(goal="Implement"))
    task_b = Task(id="task-b", spec=TaskSpec(goal="Test"), depends_on=["task-a"])
    dag = create_dag([task_a, task_b], root_task_id="task-b", dag_id="dag-ipc-1")
    sched._dags[dag.id] = dag
    sched._results[dag.id] = {}

    # Mark task-a as succeeded with an artifact_ref
    ref = "nimfs://artifact/task-a-abc123"
    sched.complete_task(
        dag.id, "task-a",
        ToolResult(status="OK", output="summary output", artifact_ref=ref),
    )

    ctx = sched.build_downstream_context(dag.id, "task-b")
    assert ref in ctx
    assert "task-a" in ctx


def test_scheduler_build_downstream_context_fallback_to_output():
    """When no artifact_ref, build_downstream_context should use raw output."""
    from nimbus.core.scheduler import Scheduler, Task, TaskSpec, create_dag

    sched = Scheduler()
    task_a = Task(id="task-a", spec=TaskSpec(goal="Impl"))
    task_b = Task(id="task-b", spec=TaskSpec(goal="Test"), depends_on=["task-a"])
    dag = create_dag([task_a, task_b], root_task_id="task-b", dag_id="dag-fallback-1")
    sched._dags[dag.id] = dag
    sched._results[dag.id] = {}

    sched.complete_task(
        dag.id, "task-a",
        ToolResult(status="OK", output="Plain output result", artifact_ref=None),
    )

    ctx = sched.build_downstream_context(dag.id, "task-b")
    assert "Plain output result" in ctx
    assert "task-a" in ctx


def test_scheduler_build_downstream_context_no_deps():
    """Tasks with no dependencies should get empty context."""
    from nimbus.core.scheduler import Scheduler, Task, TaskSpec, create_dag

    sched = Scheduler()
    task = Task(id="standalone", spec=TaskSpec(goal="Solo"))
    dag = create_dag([task], root_task_id="standalone", dag_id="dag-nodeps-1")
    sched._dags[dag.id] = dag

    ctx = sched.build_downstream_context(dag.id, "standalone")
    assert ctx == ""


def test_scheduler_build_downstream_context_truncates_large_output():
    """Raw output longer than 2000 chars should be truncated in context."""
    from nimbus.core.scheduler import Scheduler, Task, TaskSpec, create_dag

    sched = Scheduler()
    ta = Task(id="ta", spec=TaskSpec(goal="A"))
    tb = Task(id="tb", spec=TaskSpec(goal="B"), depends_on=["ta"])
    dag = create_dag([ta, tb], root_task_id="tb", dag_id="dag-truncate-1")
    sched._dags[dag.id] = dag
    sched._results[dag.id] = {}

    big_output = "Z" * 5000
    sched.complete_task(dag.id, "ta", ToolResult(status="OK", output=big_output))

    ctx = sched.build_downstream_context(dag.id, "tb")
    assert len(ctx) < 5000  # truncated
    assert "..." in ctx
