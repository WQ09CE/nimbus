"""
AgentPathContext and PathResolver -- core path model for workspace isolation.

Every Agent instance is associated with an AgentPathContext that defines its
physical and logical boundaries.  PathResolver provides deterministic path
resolution and validation against that context.

Design goals:
  - Self-contained (no nimbus internal imports)
  - Standard-library only (dataclasses, pathlib, os, logging)
  - Thread-safe (all state is per-instance; PathResolver is stateless)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class PathOutOfScopeError(Exception):
    """Raised when a path operation targets a location outside the allowed scope."""

    def __init__(self, path: str, allowed_roots: List[str], operation: str = "access"):
        self.path = path
        self.allowed_roots = allowed_roots
        self.operation = operation
        roots_display = ", ".join(allowed_roots) if allowed_roots else "(none)"
        super().__init__(
            f"Path out of scope for {operation}: {path!r}. "
            f"Allowed roots: [{roots_display}]"
        )


# ---------------------------------------------------------------------------
# AgentPathContext
# ---------------------------------------------------------------------------

@dataclass
class AgentPathContext:
    """Defines the physical and logical path boundaries for an Agent.

    Attributes:
        workspace_root:  Physical hard boundary -- the agent's logical "/".
                         No operation may escape this root in strict mode.
        target_root:     Logical focus point -- the subdirectory the agent is
                         actively working in ("my project").
        execution_cwd:   Current working directory for Bash and relative-path
                         resolution.  Updated when ``cd`` succeeds in Bash.
        reference_roots: Read-only reference paths (e.g. other repos, vendored
                         libs).  Read/Grep are allowed here; Write is not.
        writable_roots:  Paths where Write/Edit are permitted.  Typically
                         ``[target_root]``.
        scope_mode:      ``"strict"`` raises on out-of-scope access;
                         ``"relaxed"`` logs a warning but allows it.
    """

    workspace_root: str
    target_root: str
    execution_cwd: str
    reference_roots: List[str] = field(default_factory=list)
    writable_roots: List[str] = field(default_factory=list)
    scope_mode: str = "strict"

    # -- lifecycle -----------------------------------------------------------

    def __post_init__(self) -> None:
        """Normalize all path fields to absolute, resolved, symlink-resolved paths."""
        self.workspace_root = _normalize(self.workspace_root)
        self.target_root = _normalize(self.target_root)
        self.execution_cwd = _normalize(self.execution_cwd)
        self.reference_roots = [_normalize(p) for p in self.reference_roots]
        self.writable_roots = [_normalize(p) for p in self.writable_roots]

        # Ensure writable_roots is populated -- default to [target_root]
        if not self.writable_roots:
            self.writable_roots = [self.target_root]

        # Validate scope_mode
        if self.scope_mode not in ("strict", "relaxed"):
            raise ValueError(
                f"scope_mode must be 'strict' or 'relaxed', got {self.scope_mode!r}"
            )

    # -- factory methods -----------------------------------------------------

    @classmethod
    def from_cwd(
        cls,
        *,
        scope_mode: str = "strict",
        reference_roots: Optional[List[str]] = None,
    ) -> AgentPathContext:
        """Create a default context rooted at the current working directory.

        This is the typical entry point for the top-level agent.  All roots
        point to ``os.getcwd()`` so the agent can read and write freely
        within the project directory.
        """
        cwd = os.getcwd()
        return cls(
            workspace_root=cwd,
            target_root=cwd,
            execution_cwd=cwd,
            reference_roots=reference_roots or [],
            writable_roots=[cwd],
            scope_mode=scope_mode,
        )

    # -- mutation helpers ----------------------------------------------------

    def update_cwd(self, new_cwd: str) -> None:
        """Track a successful ``cd`` in Bash by updating *execution_cwd*.

        The new cwd is normalized to an absolute resolved path.  In strict
        mode, raises ``PathOutOfScopeError`` if the new cwd is outside
        ``workspace_root``.  In relaxed mode, logs a warning.
        """
        normalized = _normalize(new_cwd)
        if not _is_under_any(normalized, [self.workspace_root]):
            if self.scope_mode == "strict":
                raise PathOutOfScopeError(
                    normalized, [self.workspace_root], operation="cd"
                )
            logger.warning(
                "cd outside workspace_root (relaxed mode): %s  workspace=%s",
                normalized,
                self.workspace_root,
            )
        self.execution_cwd = normalized

    # -- sub-agent derivation ------------------------------------------------

    def derive_for_sub_agent(
        self,
        target_sub_path: Optional[str] = None,
    ) -> AgentPathContext:
        """Derive a new context for a child agent.

        Args:
            target_sub_path: If given, the child's *target_root* is narrowed
                to ``self.target_root / target_sub_path``.  Its
                *execution_cwd* and *writable_roots* follow suit.
                If ``None``, the child inherits the parent's full context.

        Returns:
            A new ``AgentPathContext`` with inherited *workspace_root* and
            *reference_roots*, and optionally narrowed target.
        """
        if target_sub_path is not None:
            child_target = _normalize(
                os.path.join(self.target_root, target_sub_path)
            )
            return AgentPathContext(
                workspace_root=self.workspace_root,
                target_root=child_target,
                execution_cwd=child_target,
                reference_roots=list(self.reference_roots),
                writable_roots=[child_target],
                scope_mode=self.scope_mode,
            )

        # Full inheritance -- same boundaries, fresh list copies.
        return AgentPathContext(
            workspace_root=self.workspace_root,
            target_root=self.target_root,
            execution_cwd=self.execution_cwd,
            reference_roots=list(self.reference_roots),
            writable_roots=list(self.writable_roots),
            scope_mode=self.scope_mode,
        )


# ---------------------------------------------------------------------------
# PathResolver -- stateless helper with static methods
# ---------------------------------------------------------------------------

class PathResolver:
    """Deterministic path resolution and validation against an AgentPathContext.

    All methods are static -- PathResolver carries no state.
    """

    @staticmethod
    def resolve(path: str, context: AgentPathContext) -> str:
        """Resolve *path* to an absolute filesystem path.

        Resolution rules:
          1. ``~`` is expanded via ``os.path.expanduser``.
          2. Absolute paths (starting with ``/``) are used as-is.
          3. Relative paths are joined against ``context.execution_cwd``.
          4. The result is normalized (resolved symlinks, ``..``, etc.).
        """
        # Expand ~ first
        expanded = os.path.expanduser(path)

        if os.path.isabs(expanded):
            resolved = expanded
        else:
            resolved = os.path.join(context.execution_cwd, expanded)

        return _normalize(resolved)

    @staticmethod
    def resolve_file(path: str, context: AgentPathContext) -> str:
        """Resolve *path* for file operations, anchored to *target_root*.

        Unlike ``resolve()`` which anchors relative paths to ``execution_cwd``
        (for Bash), file operations (Read/Write/Edit/Grep) anchor relative
        paths to ``target_root`` -- the agent's logical project directory.

        This prevents shell ``cd`` from silently changing where file tools
        write to.
        """
        expanded = os.path.expanduser(path)
        if os.path.isabs(expanded):
            return _normalize(expanded)
        return _normalize(os.path.join(context.target_root, expanded))

    @staticmethod
    def validate_read(path: str, context: AgentPathContext) -> str:
        """Resolve and validate *path* for a read operation.

        Read is allowed if the resolved path falls under:
          - ``context.workspace_root``, **or**
          - any entry in ``context.reference_roots``.

        In ``strict`` mode, out-of-scope access raises
        ``PathOutOfScopeError``.  In ``relaxed`` mode, a warning is logged
        and the path is returned anyway.

        Returns:
            The resolved absolute path.
        """
        resolved = PathResolver.resolve_file(path, context)
        allowed = [context.workspace_root] + context.reference_roots

        if _is_under_any(resolved, allowed):
            return resolved

        # Out of scope
        if context.scope_mode == "strict":
            raise PathOutOfScopeError(resolved, allowed, operation="read")

        logger.warning(
            "Read path out of scope (relaxed mode): %s  allowed=%s",
            resolved,
            allowed,
        )
        return resolved

    @staticmethod
    def validate_write(path: str, context: AgentPathContext) -> str:
        """Resolve and validate *path* for a write operation.

        Write is allowed only if the resolved path falls under one of
        ``context.writable_roots``.  Out-of-scope writes **always** raise
        ``PathOutOfScopeError`` regardless of ``scope_mode``.

        Returns:
            The resolved absolute path.
        """
        resolved = PathResolver.resolve_file(path, context)

        if _is_under_any(resolved, context.writable_roots):
            return resolved

        # Out of scope -- write is ALWAYS blocked regardless of scope_mode.
        # Write operations are always strictly enforced to prevent accidental
        # file corruption outside the workspace.
        raise PathOutOfScopeError(
            resolved, context.writable_roots, operation="write"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize(path: str) -> str:
    """Return an absolute, resolved, symlink-resolved, ``~``-expanded path string."""
    return str(Path(os.path.expanduser(path)).resolve())


def _is_under_any(path: str, roots: List[str]) -> bool:
    """Check whether *path* is equal to or a descendant of any root in *roots*.

    Uses string prefix matching on the normalized path with a trailing
    separator to avoid partial-directory-name matches (e.g. ``/foo/bar``
    should not match root ``/foo/b``).
    """
    for root in roots:
        # Exact match or strict subdirectory
        if path == root or path.startswith(root + os.sep):
            return True
    return False
