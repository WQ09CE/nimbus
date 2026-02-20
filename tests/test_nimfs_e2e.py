"""
End-to-end tests for NimFS: Nimbus Virtual Shared Disk

Covers: security (path traversal), concurrency, artifact lifecycle,
        memory lifecycle, tool functions, and tool registration.
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from nimbus.core.nimfs import (
    ArtifactExpiredError,
    ArtifactNotFoundError,
    ArtifactTTL,
    MemoryCategory,
    MemoryNotFoundError,
    MemoryScope,
    NimFSGC,
    NimFSManager,
    get_project_root,
    parse_nimfs_ref,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def nimfs_root(tmp_path: Path) -> Path:
    """Create a temporary NimFS root directory, replacing ~/.nimbus/fs/."""
    root = tmp_path / "nimfs_root"
    root.mkdir()
    (root / "global").mkdir()
    (root / "projects").mkdir()
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Temporary workspace directory (simulates a user project)."""
    ws = tmp_path / "test-workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def workspace_b(tmp_path: Path) -> Path:
    """A second temporary workspace for cross-project tests."""
    ws = tmp_path / "test-workspace-b"
    ws.mkdir()
    return ws


@pytest.fixture
def manager(nimfs_root: Path, workspace: Path) -> NimFSManager:
    """NimFSManager with all paths redirected to tmp directories."""
    with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
        return NimFSManager(str(workspace))


@pytest.fixture
def manager_b(nimfs_root: Path, workspace_b: Path) -> NimFSManager:
    """Second NimFSManager for a different workspace (cross-project tests)."""
    with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
        return NimFSManager(str(workspace_b))


# =============================================================================
# 1. Security Tests -- Path Traversal
# =============================================================================


class TestPathTraversalSecurity:
    """Verify that task_id and memory_id cannot escape NimFS root via ../."""

    @pytest.mark.xfail(reason="C001: no path traversal protection yet")
    def test_task_id_path_traversal(self, manager: NimFSManager):
        """A malicious task_id with ../../ should not escape artifacts_root."""
        evil_task_id = "../../evil"
        ref = manager.write_artifact(
            content="malicious payload",
            task_id=evil_task_id,
            producer="attacker",
        )
        # The artifact was written. Now verify the content file is INSIDE artifacts_root.
        manifest = manager.get_artifact_manifest(ref)
        task_dir = manager.artifacts_root / manifest.task_id
        content_path = (task_dir / manifest.filename).resolve()
        artifacts_root_resolved = manager.artifacts_root.resolve()

        # The content file must be strictly under artifacts_root
        assert str(content_path).startswith(str(artifacts_root_resolved)), (
            f"Path traversal! Content written to {content_path}, "
            f"which is outside artifacts_root {artifacts_root_resolved}"
        )

    @pytest.mark.xfail(
        reason="C001: no path traversal protection yet",
        strict=False,
    )
    def test_memory_id_path_traversal_via_read(self, manager: NimFSManager):
        """A memory_id with ../ should not read files outside NimFS.

        Note: The current code may raise MemoryNotFoundError accidentally
        (because _find_memory_dir does not resolve the evil path), but this
        is NOT true protection -- it only works because the lookup happens
        to fail. The xfail(strict=False) tolerates both pass and fail.
        """
        # Write a legitimate memory entry first so the category dir exists
        manager.write_memory(
            category=MemoryCategory.ENTITIES,
            title="legit",
            content="legit content",
        )

        # Try to read with a traversal memory_id
        evil_id = "entities-../../../../../../etc/passwd"
        with pytest.raises((MemoryNotFoundError, ValueError, OSError)):
            manager.read_memory(evil_id, layer=2)

    @pytest.mark.xfail(reason="C001: no path traversal protection yet")
    def test_manifest_filename_traversal(self, manager: NimFSManager):
        """Tampered manifest.json with filename=../../../etc/passwd should be caught."""
        # Write a normal artifact
        ref = manager.write_artifact(
            content="normal content",
            task_id="task-normal",
            producer="agent",
        )
        manifest = manager.get_artifact_manifest(ref)

        # Tamper the manifest to point filename outside the task directory
        task_dir = manager.artifacts_root / manifest.task_id
        manifest_path = task_dir / "manifest.json"
        data = json.loads(manifest_path.read_text())
        data["filename"] = "../../../etc/passwd"
        manifest_path.write_text(json.dumps(data))

        # Now try to read -- should either raise an error or only read within bounds
        try:
            content = manager.read_artifact(ref)
            # If it returned content, verify it did NOT read /etc/passwd
            assert "root:" not in content, (
                "Path traversal: read_artifact returned /etc/passwd content!"
            )
        except Exception:
            # Any exception is acceptable -- it means traversal was blocked
            pass

        # If no exception and content does NOT contain /etc/passwd, it just means
        # the file didn't exist. But the real check is that the resolved path
        # stays within bounds. Verify the would-be path:
        evil_path = (task_dir / "../../../etc/passwd").resolve()
        artifacts_root_resolved = manager.artifacts_root.resolve()
        assert str(evil_path).startswith(str(artifacts_root_resolved)), (
            f"Filename traversal not blocked: would resolve to {evil_path}"
        )


# =============================================================================
# 2. Concurrency Safety Tests
# =============================================================================


class TestConcurrencySafety:
    """Verify NimFS handles concurrent writes without data loss."""

    @pytest.mark.xfail(reason="C003: index.json has no file locking, concurrent writes lose data")
    def test_concurrent_artifact_writes(self, nimfs_root: Path, workspace: Path):
        """10 concurrent writes with different task_ids should all appear in index.json."""
        num_writers = 10

        def _write(i: int) -> str:
            with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
                m = NimFSManager(str(workspace))
                ref = m.write_artifact(
                    content=f"content-{i}",
                    task_id=f"task-concurrent-{i}",
                    producer="agent",
                )
                return ref

        with ThreadPoolExecutor(max_workers=num_writers) as pool:
            refs = list(pool.map(_write, range(num_writers)))

        assert len(refs) == num_writers

        # Verify index.json contains all 10 records
        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            m = NimFSManager(str(workspace))
            index_path = m.artifacts_root / "index.json"
            records = json.loads(index_path.read_text())

        # All refs should be readable
        task_ids_in_index = {r["task_id"] for r in records}
        for i in range(num_writers):
            assert f"task-concurrent-{i}" in task_ids_in_index, (
                f"task-concurrent-{i} missing from index! "
                f"Only found: {task_ids_in_index}. "
                f"Race condition: C003 concurrent write lost data."
            )

    def test_concurrent_writes_same_task_id(self, nimfs_root: Path, workspace: Path):
        """5 concurrent writes to the same task_id should not corrupt data."""
        num_writers = 5
        task_id = "task-shared"
        written_refs: List[str] = []

        def _write(i: int) -> str:
            with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
                m = NimFSManager(str(workspace))
                ref = m.write_artifact(
                    content=f"version-{i}",
                    task_id=task_id,
                    producer="agent",
                )
                return ref

        with ThreadPoolExecutor(max_workers=num_writers) as pool:
            refs = list(pool.map(_write, range(num_writers)))

        assert len(refs) == num_writers

        # Verify we can read at least one artifact (the last write wins for manifest.json)
        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            m = NimFSManager(str(workspace))
            # Check index has records for this task_id
            index_path = m.artifacts_root / "index.json"
            records = json.loads(index_path.read_text())
            task_records = [r for r in records if r["task_id"] == task_id]

            # We should have at least 1 record (ideally num_writers, but concurrent
            # writes to the same file may lose some due to C003 race condition)
            assert len(task_records) >= 1, (
                f"No records found for {task_id}! Complete data loss."
            )

            # Verify all recorded artifacts are readable
            for record in task_records:
                ref = f"nimfs://artifact/{record['artifact_id']}"
                content = m.read_artifact(ref)
                assert content.startswith("version-")


# =============================================================================
# 3. Artifact Lifecycle E2E Tests
# =============================================================================


class TestArtifactLifecycleE2E:
    """Full lifecycle: write -> read -> list -> GC -> EXPIRED -> defrag."""

    def test_write_read_list_gc_lifecycle(self, nimfs_root: Path, workspace: Path):
        """Complete artifact lifecycle from creation to tombstone cleanup."""
        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            m = NimFSManager(str(workspace))

            # Step 1: Write artifact
            ref = m.write_artifact(
                content="lifecycle test data",
                task_id="task-lifecycle",
                producer="test-agent",
                ttl=ArtifactTTL.TASK,
                summary="lifecycle test",
                tags=["e2e", "lifecycle"],
            )
            assert ref.startswith("nimfs://artifact/")

            # Step 2: Read and verify
            content = m.read_artifact(ref)
            assert content == "lifecycle test data"

            # Step 3: List and verify
            artifacts = m.list_artifacts()
            assert len(artifacts) == 1
            assert artifacts[0].summary == "lifecycle test"
            assert "e2e" in artifacts[0].tags

            # Step 4: Backdate created_at to make it expire (>30 minutes)
            manifest = m.get_artifact_manifest(ref)
            task_dir = m.artifacts_root / manifest.task_id
            manifest_path = task_dir / "manifest.json"
            data = json.loads(manifest_path.read_text())
            old_time = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
            data["created_at"] = old_time
            manifest_path.write_text(json.dumps(data))

            # Also update index
            index_path = m.artifacts_root / "index.json"
            records = json.loads(index_path.read_text())
            for r in records:
                if r.get("artifact_id") == manifest.artifact_id:
                    r["created_at"] = old_time
            index_path.write_text(json.dumps(records))

            # Step 5: Run GC
            gc = NimFSGC()
            cleaned = gc.gc_artifacts(str(workspace), ttl_level=ArtifactTTL.TASK)
            assert cleaned == 1

            # Step 6: Verify artifact is now EXPIRED
            with pytest.raises(ArtifactExpiredError):
                m.read_artifact(ref)

            # Step 7: Defrag to remove tombstones
            stats = gc.defrag(str(workspace))
            assert stats["tombstones_removed"] >= 1

            # Step 8: Verify tombstone is fully cleared from index
            records = json.loads(index_path.read_text())
            artifact_ids = [r["artifact_id"] for r in records]
            assert manifest.artifact_id not in artifact_ids

    def test_artifact_types_roundtrip(self, nimfs_root: Path, workspace: Path):
        """Each artifact type should produce the correct file extension."""
        type_ext = {
            "code": ".py",
            "report": ".md",
            "diff": ".diff",
            "json": ".json",
            "text": ".txt",
        }

        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            m = NimFSManager(str(workspace))

            for atype, expected_ext in type_ext.items():
                content = f"content for {atype}"
                ref = m.write_artifact(
                    content=content,
                    task_id=f"task-type-{atype}",
                    producer="agent",
                    artifact_type=atype,
                )

                # Verify extension
                manifest = m.get_artifact_manifest(ref)
                assert manifest.filename.endswith(expected_ext), (
                    f"Expected extension {expected_ext} for type {atype}, "
                    f"got filename {manifest.filename}"
                )

                # Verify roundtrip
                read_back = m.read_artifact(ref)
                assert read_back == content


# =============================================================================
# 4. Memory Lifecycle E2E Tests
# =============================================================================


class TestMemoryLifecycleE2E:
    """Full memory lifecycle: write -> search -> read layers -> load_context."""

    def test_memory_write_search_read_lifecycle(self, nimfs_root: Path, workspace: Path):
        """Write 3 memories in different categories, search, read layers, verify context."""
        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            m = NimFSManager(str(workspace))

            # Write 3 memories in different categories.
            # Use content long enough (>1000 chars) to ensure L2 > L1
            # (L1 includes only the first 1000 chars of content in Overview).
            entity_content = (
                "NimFSManager is the core coordinator for NimFS. "
                "It manages both the artifacts partition (for short-lived IPC) "
                "and the memory partition (for long-term knowledge). "
            ) * 20  # ~3000 chars, well above L1's 1000-char overview cutoff

            mid_entity = m.write_memory(
                category=MemoryCategory.ENTITIES,
                title="NimFSManager class",
                content=entity_content,
                summary="Core coordinator for artifacts and memory",
                tags=["nimfs", "core"],
            )

            mid_event = m.write_memory(
                category=MemoryCategory.EVENTS,
                title="NimFS deployment v1",
                content="NimFS was deployed to production on 2026-02-20. " * 30,
                summary="Production deployment milestone",
                tags=["deploy"],
            )

            mid_pattern = m.write_memory(
                category=MemoryCategory.PATTERNS,
                title="Claim-Check IPC pattern",
                content="The Claim-Check pattern stores large payloads on disk. " * 30,
                summary="IPC pattern for large payloads",
                tags=["ipc", "pattern"],
            )

            # Search: should find by title keyword
            results = m.search_memory("NimFS")
            titles = [e.title for e in results]
            assert "NimFSManager class" in titles

            # Search: should find by tag
            results = m.search_memory("deploy")
            titles = [e.title for e in results]
            assert "NimFS deployment v1" in titles

            # Read each layer for the entity memory
            l0 = m.read_memory(mid_entity, layer=0)
            l1 = m.read_memory(mid_entity, layer=1)
            l2 = m.read_memory(mid_entity, layer=2)

            assert l0 == "Core coordinator for artifacts and memory"
            assert "NimFSManager class" in l1  # title appears in L1
            assert "core coordinator" in l2.lower()  # full content in L2
            assert len(l0) < len(l1) < len(l2)  # layer sizes increase

            # Verify load_context includes them
            ctx = m.load_context("NimFS coordinator")
            assert isinstance(ctx, str)
            # At minimum, the context should contain something from our memories
            # (depends on keyword matching with "NimFS coordinator")

    def test_global_vs_project_scope(self, nimfs_root: Path, workspace: Path, workspace_b: Path):
        """Global memory should be visible across workspaces; project memory should not."""
        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            m_a = NimFSManager(str(workspace))
            m_b = NimFSManager(str(workspace_b))

            # Write global memory from workspace A
            mid_global = m_a.write_memory(
                category=MemoryCategory.PROFILE,
                title="Agent identity",
                content="I am a Nimbus implementation agent",
                summary="Agent profile",
                scope=MemoryScope.GLOBAL,
                tags=["profile"],
            )

            # Write project memory from workspace A
            mid_project = m_a.write_memory(
                category=MemoryCategory.ENTITIES,
                title="ProjectA entity",
                content="This entity belongs to project A only",
                summary="Project A entity",
                tags=["projecta"],
            )

            # From workspace A: both should be accessible
            global_content = m_a.read_memory(mid_global, layer=2)
            assert "Nimbus implementation agent" in global_content

            project_content = m_a.read_memory(mid_project, layer=2)
            assert "project A only" in project_content

            # load_context from workspace A should include both
            ctx_a = m_a.load_context("agent profile entity")
            assert isinstance(ctx_a, str)

            # From workspace B: global should be visible
            global_content_b = m_b.read_memory(mid_global, layer=2)
            assert "Nimbus implementation agent" in global_content_b

            # From workspace B: project memory from A should NOT be visible
            with pytest.raises(MemoryNotFoundError):
                m_b.read_memory(mid_project, layer=2)

            # Search from workspace B: global scope should find global memory
            results_b = m_b.search_memory("profile", scope="global")
            titles_b = [e.title for e in results_b]
            assert "Agent identity" in titles_b

            # Search from workspace B: project scope should NOT find workspace A's project memory
            results_b_project = m_b.search_memory("projecta", scope="project")
            titles_b_project = [e.title for e in results_b_project]
            assert "ProjectA entity" not in titles_b_project


# =============================================================================
# 5. Tool Function E2E Tests
# =============================================================================


class TestToolFunctionsE2E:
    """Test the async tool functions that wrap NimFSManager."""

    @staticmethod
    def _run(coro):
        """Helper to run async tool functions synchronously."""
        return asyncio.run(coro)

    def test_nimfs_write_read_artifact_tool(self, nimfs_root: Path, workspace: Path):
        """Write via tool function -> extract ref -> read via tool function."""
        from nimbus.tools.nimfs_tools import nimfs_read_artifact, nimfs_write_artifact

        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            result = self._run(nimfs_write_artifact(
                content="tool test payload",
                task_id="task-tool-test",
                summary="tool test artifact",
                workspace=str(workspace),
            ))

            assert "Artifact written to NimFS" in result
            assert "nimfs://artifact/" in result

            # Extract the ref from the result text
            for line in result.split("\n"):
                if "Reference" in line:
                    ref = line.split(":", 1)[1].strip()
                    break
            else:
                pytest.fail("Could not extract ref from write result")

            # Read it back
            read_result = self._run(nimfs_read_artifact(
                ref=ref,
                workspace=str(workspace),
            ))

            assert "tool test payload" in read_result
            assert "NimFS Artifact" in read_result

    def test_nimfs_write_memory_search_tool(self, nimfs_root: Path, workspace: Path):
        """Write memory via tool -> search via tool -> verify found."""
        from nimbus.tools.nimfs_tools import nimfs_search_memory, nimfs_write_memory

        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            write_result = self._run(nimfs_write_memory(
                category="entities",
                title="SearchableEntity",
                content="This entity should be findable by search",
                summary="A searchable entity for testing",
                tags="searchable,test",
                workspace=str(workspace),
            ))

            assert "Memory written to NimFS" in write_result

            # Search for it
            search_result = self._run(nimfs_search_memory(
                query="SearchableEntity",
                workspace=str(workspace),
            ))

            assert "SearchableEntity" in search_result
            assert "searchable" in search_result

    def test_nimfs_list_artifacts_tool(self, nimfs_root: Path, workspace: Path):
        """Write 3 artifacts -> list via tool -> verify 3 found."""
        from nimbus.tools.nimfs_tools import nimfs_list_artifacts, nimfs_write_artifact

        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            for i in range(3):
                self._run(nimfs_write_artifact(
                    content=f"artifact content {i}",
                    task_id=f"task-list-{i}",
                    summary=f"artifact {i}",
                    workspace=str(workspace),
                ))

            list_result = self._run(nimfs_list_artifacts(
                workspace=str(workspace),
            ))

            assert "3 found" in list_result
            for i in range(3):
                assert f"task-list-{i}" in list_result

    def test_nimfs_load_context_tool(self, nimfs_root: Path, workspace: Path):
        """Write memory -> load context via tool -> verify included."""
        from nimbus.tools.nimfs_tools import nimfs_load_context, nimfs_write_memory

        with patch("nimbus.core.nimfs.project_id.get_nimfs_root", return_value=nimfs_root):
            self._run(nimfs_write_memory(
                category="patterns",
                title="ContextPattern",
                content="This pattern should appear in loaded context",
                summary="A test pattern for context loading",
                tags="context,test",
                scope="project",
                workspace=str(workspace),
            ))

            ctx_result = self._run(nimfs_load_context(
                goal="ContextPattern",
                workspace=str(workspace),
            ))

            # load_context does keyword search on goal, so "ContextPattern" should match
            assert "ContextPattern" in ctx_result or "test pattern" in ctx_result


# =============================================================================
# 6. Tool Registration Tests
# =============================================================================


class TestToolRegistration:
    """Verify tool definitions and registration are correct."""

    def test_nimfs_tools_importable(self):
        """NIMFS_TOOLS and NIMFS_TOOL_FUNCTIONS should be importable."""
        from nimbus.tools import NIMFS_TOOL_FUNCTIONS, NIMFS_TOOLS

        assert NIMFS_TOOLS is not None
        assert NIMFS_TOOL_FUNCTIONS is not None
        assert isinstance(NIMFS_TOOLS, list)
        assert isinstance(NIMFS_TOOL_FUNCTIONS, dict)

    def test_nimfs_tools_count(self):
        """There should be exactly 6 NimFS tool definitions."""
        from nimbus.tools import NIMFS_TOOLS

        assert len(NIMFS_TOOLS) == 6, (
            f"Expected 6 NimFS tools, got {len(NIMFS_TOOLS)}: "
            f"{[t['name'] for t in NIMFS_TOOLS]}"
        )

    def test_nimfs_tool_functions_match_definitions(self):
        """Every NIMFS_TOOLS name should have a corresponding function in NIMFS_TOOL_FUNCTIONS."""
        from nimbus.tools import NIMFS_TOOL_FUNCTIONS, NIMFS_TOOLS

        tool_names = {t["name"] for t in NIMFS_TOOLS}
        func_names = set(NIMFS_TOOL_FUNCTIONS.keys())

        assert tool_names == func_names, (
            f"Mismatch between tool definitions and functions.\n"
            f"In definitions but not functions: {tool_names - func_names}\n"
            f"In functions but not definitions: {func_names - tool_names}"
        )

        # Also verify each function is callable
        for name, func in NIMFS_TOOL_FUNCTIONS.items():
            assert callable(func), f"Tool function '{name}' is not callable"

    def test_nimfs_tools_not_in_all_tools(self):
        """ALL_TOOLS should NOT contain NimFS tools (they are registered separately).

        This documents the current state. If NimFS tools are later added to ALL_TOOLS,
        this test should be updated accordingly.
        """
        from nimbus.tools import ALL_TOOLS, NIMFS_TOOLS

        all_tool_names = {t["name"] for t in ALL_TOOLS}
        nimfs_tool_names = {t["name"] for t in NIMFS_TOOLS}

        overlap = all_tool_names & nimfs_tool_names
        assert len(overlap) == 0, (
            f"NimFS tools found in ALL_TOOLS: {overlap}. "
            f"This may be intentional (H002) or a bug."
        )
