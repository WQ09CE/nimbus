"""Tests for AgentPathContext and PathResolver."""

import os
import tempfile

import pytest

from nimbus.core.path_context import (
    AgentPathContext,
    PathOutOfScopeError,
    PathResolver,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a realistic workspace tree and return the root."""
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "main.py").write_text("# main")
    (tmp_path / "src" / "utils" / "helper.py").write_text("# helper")
    return tmp_path


@pytest.fixture
def strict_ctx(tmp_workspace):
    """A strict-mode context rooted at tmp_workspace."""
    return AgentPathContext(
        workspace_root=str(tmp_workspace),
        target_root=str(tmp_workspace / "src"),
        execution_cwd=str(tmp_workspace / "src"),
        reference_roots=[],
        writable_roots=[str(tmp_workspace / "src")],
        scope_mode="strict",
    )


@pytest.fixture
def relaxed_ctx(tmp_workspace):
    """A relaxed-mode context rooted at tmp_workspace."""
    return AgentPathContext(
        workspace_root=str(tmp_workspace),
        target_root=str(tmp_workspace / "src"),
        execution_cwd=str(tmp_workspace / "src"),
        reference_roots=[],
        writable_roots=[str(tmp_workspace / "src")],
        scope_mode="relaxed",
    )


# ---------------------------------------------------------------------------
# AgentPathContext -- __post_init__
# ---------------------------------------------------------------------------

class TestPostInit:
    def test_paths_normalized_to_absolute(self, tmp_workspace):
        ctx = AgentPathContext(
            workspace_root=str(tmp_workspace),
            target_root=str(tmp_workspace) + "/./src/../src",
            execution_cwd=str(tmp_workspace) + "/src/./",
        )
        assert os.path.isabs(ctx.target_root)
        assert ".." not in ctx.target_root
        assert "/." not in ctx.target_root

    def test_default_writable_roots_is_target(self, tmp_workspace):
        ctx = AgentPathContext(
            workspace_root=str(tmp_workspace),
            target_root=str(tmp_workspace / "src"),
            execution_cwd=str(tmp_workspace),
        )
        assert ctx.writable_roots == [str((tmp_workspace / "src").resolve())]

    def test_invalid_scope_mode_raises(self, tmp_workspace):
        with pytest.raises(ValueError, match="scope_mode"):
            AgentPathContext(
                workspace_root=str(tmp_workspace),
                target_root=str(tmp_workspace),
                execution_cwd=str(tmp_workspace),
                scope_mode="invalid",
            )


# ---------------------------------------------------------------------------
# AgentPathContext.from_cwd
# ---------------------------------------------------------------------------

class TestFromCwd:
    def test_all_roots_equal_cwd(self):
        ctx = AgentPathContext.from_cwd()
        cwd = str(os.path.realpath(os.getcwd()))
        assert ctx.workspace_root == cwd
        assert ctx.target_root == cwd
        assert ctx.execution_cwd == cwd
        assert ctx.writable_roots == [cwd]

    def test_default_scope_mode_is_strict(self):
        ctx = AgentPathContext.from_cwd()
        assert ctx.scope_mode == "strict"

    def test_strict_mode_override(self):
        ctx = AgentPathContext.from_cwd(scope_mode="strict")
        assert ctx.scope_mode == "strict"

    def test_reference_roots_passthrough(self, tmp_workspace):
        ref = str(tmp_workspace / "docs")
        ctx = AgentPathContext.from_cwd(reference_roots=[ref])
        assert any(r.endswith("docs") for r in ctx.reference_roots)


# ---------------------------------------------------------------------------
# AgentPathContext.update_cwd
# ---------------------------------------------------------------------------

class TestUpdateCwd:
    def test_updates_execution_cwd(self, strict_ctx, tmp_workspace):
        new_dir = str(tmp_workspace / "tests")
        strict_ctx.update_cwd(new_dir)
        assert strict_ctx.execution_cwd == str((tmp_workspace / "tests").resolve())

    def test_normalizes_path(self, strict_ctx, tmp_workspace):
        dotty = str(tmp_workspace / "src" / "utils" / ".." / "core")
        strict_ctx.update_cwd(dotty)
        assert ".." not in strict_ctx.execution_cwd
        assert strict_ctx.execution_cwd.endswith("core")


# ---------------------------------------------------------------------------
# AgentPathContext.derive_for_sub_agent
# ---------------------------------------------------------------------------

class TestDeriveForSubAgent:
    def test_full_inheritance(self, strict_ctx):
        child = strict_ctx.derive_for_sub_agent()
        assert child.workspace_root == strict_ctx.workspace_root
        assert child.target_root == strict_ctx.target_root
        assert child.execution_cwd == strict_ctx.execution_cwd
        assert child.writable_roots == strict_ctx.writable_roots
        assert child.scope_mode == strict_ctx.scope_mode

    def test_full_inheritance_creates_copies(self, strict_ctx):
        """Mutating child lists must not affect parent."""
        child = strict_ctx.derive_for_sub_agent()
        child.reference_roots.append("/tmp/injected")
        assert "/tmp/injected" not in strict_ctx.reference_roots

    def test_target_narrowing(self, strict_ctx, tmp_workspace):
        child = strict_ctx.derive_for_sub_agent(target_sub_path="utils")
        expected_target = str((tmp_workspace / "src" / "utils").resolve())
        assert child.target_root == expected_target
        assert child.execution_cwd == expected_target
        assert child.writable_roots == [expected_target]
        # workspace_root stays the same
        assert child.workspace_root == strict_ctx.workspace_root

    def test_narrowing_inherits_scope_mode(self, strict_ctx):
        child = strict_ctx.derive_for_sub_agent(target_sub_path="core")
        assert child.scope_mode == "strict"


# ---------------------------------------------------------------------------
# PathResolver.resolve
# ---------------------------------------------------------------------------

class TestResolve:
    def test_absolute_path_unchanged(self, strict_ctx, tmp_workspace):
        abs_path = str(tmp_workspace / "src" / "main.py")
        assert PathResolver.resolve(abs_path, strict_ctx) == str(
            (tmp_workspace / "src" / "main.py").resolve()
        )

    def test_relative_path_from_cwd(self, strict_ctx, tmp_workspace):
        result = PathResolver.resolve("utils/helper.py", strict_ctx)
        expected = str((tmp_workspace / "src" / "utils" / "helper.py").resolve())
        assert result == expected

    def test_tilde_expansion(self, strict_ctx):
        result = PathResolver.resolve("~/some_file", strict_ctx)
        assert result.startswith(os.path.expanduser("~"))
        assert result.endswith("some_file")

    def test_dot_dot_collapsed(self, strict_ctx, tmp_workspace):
        result = PathResolver.resolve("../tests", strict_ctx)
        expected = str((tmp_workspace / "tests").resolve())
        assert result == expected


# ---------------------------------------------------------------------------
# PathResolver.validate_read
# ---------------------------------------------------------------------------

class TestValidateRead:
    def test_read_within_workspace_ok(self, strict_ctx, tmp_workspace):
        path = str(tmp_workspace / "docs")
        result = PathResolver.validate_read(path, strict_ctx)
        assert result == str((tmp_workspace / "docs").resolve())

    def test_read_in_reference_root_ok(self, tmp_workspace):
        ref_dir = tempfile.mkdtemp()
        ctx = AgentPathContext(
            workspace_root=str(tmp_workspace),
            target_root=str(tmp_workspace / "src"),
            execution_cwd=str(tmp_workspace),
            reference_roots=[ref_dir],
            scope_mode="strict",
        )
        result = PathResolver.validate_read(ref_dir + "/some_file", ctx)
        assert result.startswith(str(os.path.realpath(ref_dir)))

    def test_read_outside_strict_raises(self, strict_ctx):
        with pytest.raises(PathOutOfScopeError) as exc_info:
            PathResolver.validate_read("/etc/passwd", strict_ctx)
        assert exc_info.value.operation == "read"

    def test_read_outside_relaxed_warns(self, relaxed_ctx, caplog):
        with caplog.at_level("WARNING"):
            result = PathResolver.validate_read("/etc/passwd", relaxed_ctx)
        assert result  # path returned despite warning
        assert "out of scope" in caplog.text.lower()

    def test_traversal_attack_blocked(self, strict_ctx, tmp_workspace):
        """../../.ssh/id_rsa must be blocked in strict mode."""
        evil = str(tmp_workspace / "src" / ".." / ".." / ".ssh" / "id_rsa")
        with pytest.raises(PathOutOfScopeError):
            PathResolver.validate_read(evil, strict_ctx)


# ---------------------------------------------------------------------------
# PathResolver.validate_write
# ---------------------------------------------------------------------------

class TestValidateWrite:
    def test_write_within_writable_ok(self, strict_ctx, tmp_workspace):
        path = str(tmp_workspace / "src" / "new_file.py")
        result = PathResolver.validate_write(path, strict_ctx)
        assert result == str((tmp_workspace / "src" / "new_file.py").resolve())

    def test_write_outside_writable_strict_raises(self, strict_ctx, tmp_workspace):
        # docs is inside workspace but not in writable_roots
        with pytest.raises(PathOutOfScopeError) as exc_info:
            PathResolver.validate_write(str(tmp_workspace / "docs" / "x.md"), strict_ctx)
        assert exc_info.value.operation == "write"

    def test_write_outside_relaxed_still_raises(self, relaxed_ctx, tmp_workspace):
        """validate_write() ALWAYS raises PathOutOfScopeError regardless of scope_mode."""
        with pytest.raises(PathOutOfScopeError) as exc_info:
            PathResolver.validate_write(
                str(tmp_workspace / "docs" / "x.md"), relaxed_ctx
            )
        assert exc_info.value.operation == "write"

    def test_write_traversal_blocked(self, strict_ctx, tmp_workspace):
        evil = str(tmp_workspace / "src" / ".." / ".." / "etc" / "evil")
        with pytest.raises(PathOutOfScopeError):
            PathResolver.validate_write(evil, strict_ctx)

    def test_write_to_reference_root_blocked(self, tmp_workspace):
        ref_dir = tempfile.mkdtemp()
        ctx = AgentPathContext(
            workspace_root=str(tmp_workspace),
            target_root=str(tmp_workspace / "src"),
            execution_cwd=str(tmp_workspace),
            reference_roots=[ref_dir],
            scope_mode="strict",
        )
        with pytest.raises(PathOutOfScopeError):
            PathResolver.validate_write(ref_dir + "/hack.py", ctx)


# ---------------------------------------------------------------------------
# PathOutOfScopeError
# ---------------------------------------------------------------------------

class TestPathOutOfScopeError:
    def test_attributes(self):
        err = PathOutOfScopeError("/bad/path", ["/good/root"], operation="write")
        assert err.path == "/bad/path"
        assert err.allowed_roots == ["/good/root"]
        assert err.operation == "write"
        assert "write" in str(err)
        assert "/bad/path" in str(err)

    def test_default_operation(self):
        err = PathOutOfScopeError("/x", ["/y"])
        assert err.operation == "access"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_partial_directory_name_no_false_positive(self, tmp_workspace):
        """Root /foo/bar must NOT match path /foo/bar_extra."""
        ctx = AgentPathContext(
            workspace_root=str(tmp_workspace),
            target_root=str(tmp_workspace / "src"),
            execution_cwd=str(tmp_workspace),
            scope_mode="strict",
        )
        # Create a sibling directory whose name starts with "src"
        sibling = tmp_workspace / "src_extra"
        sibling.mkdir(exist_ok=True)
        with pytest.raises(PathOutOfScopeError):
            PathResolver.validate_write(str(sibling / "file.py"), ctx)

    def test_workspace_root_itself_is_readable(self, strict_ctx):
        result = PathResolver.validate_read(strict_ctx.workspace_root, strict_ctx)
        assert result == strict_ctx.workspace_root

    def test_target_root_itself_is_writable(self, strict_ctx):
        result = PathResolver.validate_write(strict_ctx.target_root, strict_ctx)
        assert result == strict_ctx.target_root


# ---------------------------------------------------------------------------
# Sub-agent narrowing override tests
# ---------------------------------------------------------------------------

class TestSubAgentNarrowing:
    """Verify that sub-agent path narrowing correctly restricts scope."""

    def test_child_cannot_write_parent_sibling_directory(self, tmp_path):
        """A child narrowed to src/utils cannot write to project/other/."""
        ws = tmp_path / "ws"
        project = ws / "project"
        (project / "src" / "utils").mkdir(parents=True)
        (project / "other").mkdir(parents=True)

        parent = AgentPathContext(
            workspace_root=str(ws),
            target_root=str(project),
            execution_cwd=str(project),
            scope_mode="strict",
        )
        child = parent.derive_for_sub_agent("src/utils")

        # Child writable_roots is narrowed to project/src/utils only
        expected_writable = str((project / "src" / "utils").resolve())
        assert child.writable_roots == [expected_writable]

        # Writing to a sibling directory (project/other/) must be blocked
        sibling_file = str((project / "other" / "file.py").resolve())
        with pytest.raises(PathOutOfScopeError) as exc_info:
            PathResolver.validate_write(sibling_file, child)
        assert exc_info.value.operation == "write"

    def test_child_cannot_write_to_reference_roots(self, tmp_path):
        """Reference roots are read-only; child must not write to them."""
        ws = tmp_path / "ws"
        ref = tmp_path / "ref"
        (ws / "project" / "src").mkdir(parents=True)
        ref.mkdir(parents=True)
        (ref / "README.md").write_text("# reference")

        parent = AgentPathContext(
            workspace_root=str(ws),
            target_root=str(ws / "project"),
            execution_cwd=str(ws / "project"),
            reference_roots=[str(ref)],
            scope_mode="strict",
        )
        child = parent.derive_for_sub_agent("src")

        # Child inherits reference_roots
        assert len(child.reference_roots) == 1

        # Read from reference root is allowed
        resolved_ref = str((ref / "README.md").resolve())
        result = PathResolver.validate_read(resolved_ref, child)
        assert result == resolved_ref

        # Write to reference root is blocked
        with pytest.raises(PathOutOfScopeError) as exc_info:
            PathResolver.validate_write(resolved_ref, child)
        assert exc_info.value.operation == "write"

    def test_child_inherits_workspace_root(self, tmp_path):
        """Child inherits workspace_root from parent; target_root is narrowed."""
        ws = tmp_path / "ws"
        project = ws / "project"
        (project / "src").mkdir(parents=True)

        parent = AgentPathContext(
            workspace_root=str(ws),
            target_root=str(project),
            execution_cwd=str(project),
            scope_mode="strict",
        )
        child = parent.derive_for_sub_agent("src")

        assert child.workspace_root == parent.workspace_root
        assert child.target_root == str((project / "src").resolve())

    def test_narrowed_child_write_within_scope(self, tmp_path):
        """Child can write files within its narrowed target_root."""
        ws = tmp_path / "ws"
        project = ws / "project"
        src = project / "src"
        src.mkdir(parents=True)

        parent = AgentPathContext(
            workspace_root=str(ws),
            target_root=str(project),
            execution_cwd=str(project),
            scope_mode="strict",
        )
        child = parent.derive_for_sub_agent("src")

        # Writing within narrowed scope succeeds
        result = PathResolver.validate_write("main.py", child)
        expected = str((src / "main.py").resolve())
        assert result == expected

    def test_resolve_file_uses_target_root_not_execution_cwd(self, tmp_path):
        """resolve_file anchors to target_root; resolve anchors to execution_cwd."""
        ws = tmp_path / "ws"
        project = ws / "project"
        build_dir = project / "build"
        project.mkdir(parents=True)
        build_dir.mkdir(parents=True)

        ctx = AgentPathContext(
            workspace_root=str(ws),
            target_root=str(project),
            execution_cwd=str(build_dir),
            scope_mode="strict",
        )

        # resolve_file anchors relative paths to target_root
        file_result = PathResolver.resolve_file("main.py", ctx)
        assert file_result == str((project / "main.py").resolve())

        # resolve anchors relative paths to execution_cwd
        cwd_result = PathResolver.resolve("main.py", ctx)
        assert cwd_result == str((build_dir / "main.py").resolve())
