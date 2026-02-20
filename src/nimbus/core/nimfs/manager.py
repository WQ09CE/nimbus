"""
NimFSManager: Core Coordinator for the Nimbus Virtual Shared Disk

Manages two partitions:
  - artifacts/  : Short-lived pipeline products for Agent IPC (Claim-Check pattern)
  - memory/     : Long-term knowledge organized by 6 categories + L0/L1/L2 layers

Storage root: ~/.nimbus/fs/projects/{project_id}/
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from nimbus.core.nimfs.models import (
    ArtifactExpiredError,
    ArtifactManifest,
    ArtifactNotFoundError,
    ArtifactPendingError,
    ArtifactStatus,
    ArtifactTTL,
    MemoryCategory,
    MemoryEntry,
    MemoryScope,
    NimFSError,
)
from nimbus.core.nimfs.project_id import (
    get_global_root,
    get_nimfs_root,
    get_project_root,
    make_artifact_ref,
    parse_nimfs_ref,
)

# Whitelist for task_id and memory_id: alphanumeric, hyphens, underscores, dots
# Max 128 chars to prevent filesystem abuse
_SAFE_ID_RE = re.compile(r"^[\w\-\.]{1,128}$")

# Whitelist for artifact filenames: simple name + safe extension, no path separators
_SAFE_FILENAME_RE = re.compile(r"^[\w\-]{1,64}\.(py|md|diff|json|txt)$")


def _validate_id(value: str, label: str) -> None:
    """Raise NimFSError if value contains path traversal or invalid characters."""
    if not _SAFE_ID_RE.match(value):
        raise NimFSError(
            f"Invalid {label} '{value}': must match ^[\\w\\-.][1,128]$ "
            "(alphanumeric, hyphens, underscores, dots only)"
        )


def _validate_filename(filename: str) -> None:
    """Raise NimFSError if filename is unsafe (path traversal, bad chars)."""
    if not _SAFE_FILENAME_RE.match(filename):
        raise NimFSError(
            f"Invalid artifact filename '{filename}': "
            "must be a simple name with a safe extension (.py .md .diff .json .txt), "
            "no path separators allowed"
        )


def _validate_within_root(path: Path, root: Path) -> None:
    """Raise NimFSError if resolved path escapes the root directory."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise NimFSError(
            f"Path traversal detected: '{path}' is outside NimFS root '{root}'"
        )

# File extension mapping by artifact type
_EXT_MAP = {
    "code":   ".py",
    "report": ".md",
    "diff":   ".diff",
    "json":   ".json",
    "text":   ".txt",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class NimFSManager:
    """
    Core coordinator for NimFS.

    One instance per workspace. Cheap to construct — safe to instantiate
    per tool call (all state lives on disk).

    Usage:
        manager = NimFSManager("/Users/wangqing/sourcecode/nimbus")
        ref = manager.write_artifact(content="...", task_id="task-1", producer="impl-agent")
        content = manager.read_artifact(ref)
    """

    def __init__(self, workspace_path: str | Path):
        self.workspace_path = str(workspace_path)
        self.project_root = get_project_root(workspace_path)
        self.artifacts_root = self.project_root / "artifacts"
        self.memory_root = self.project_root / "memory"
        self.global_root = get_global_root()

    # =========================================================================
    # Artifact API  (IPC — short lifecycle)
    # =========================================================================

    def write_artifact(
        self,
        content: str,
        task_id: str,
        producer: str,
        artifact_type: str = "text",
        ttl: ArtifactTTL = ArtifactTTL.SESSION,
        summary: str = "",
        tags: Optional[List[str]] = None,
    ) -> str:
        """
        Write an agent pipeline product to NimFS.

        Follows Write-Once Immutable semantics with a two-phase commit:
          1. Write manifest with status=PENDING
          2. Write content file
          3. Update manifest to status=COMMITTED
          4. Append to index.json

        Args:
            content:       Raw content (no size limit).
            task_id:       Owning task identifier.
            producer:      Agent role name (e.g. "implement-agent").
            artifact_type: One of "code", "report", "diff", "json", "text".
            ttl:           Lifecycle level.
            summary:       Short human-readable summary (< 200 chars).
            tags:          Optional tag list.

        Returns:
            nimfs://artifact/{artifact_id} reference string.
        """
        # C001: validate task_id to prevent path traversal
        _validate_id(task_id, "task_id")

        artifact_id = f"{task_id}-{uuid.uuid4().hex[:8]}"
        ext = _EXT_MAP.get(artifact_type, ".txt")
        filename = f"content{ext}"

        # H001: validate generated filename and ensure it stays within artifacts root
        _validate_filename(filename)
        task_dir = self.artifacts_root / task_id
        _validate_within_root(task_dir, self.artifacts_root)
        task_dir.mkdir(parents=True, exist_ok=True)

        manifest = ArtifactManifest(
            artifact_id=artifact_id,
            task_id=task_id,
            producer=producer,
            type=artifact_type,
            filename=filename,
            size_bytes=len(content.encode("utf-8")),
            created_at=_now_iso(),
            ttl=ttl,
            status=ArtifactStatus.PENDING,
            summary=summary[:200] if summary else content[:100].replace("\n", " "),
            tags=tags or [],
        )

        manifest_path = task_dir / "manifest.json"

        # Phase 1: write PENDING manifest (atomicity start)
        _write_json(manifest_path, manifest.to_dict())

        # Phase 2: write content
        content_path = task_dir / filename
        content_path.write_text(content, encoding="utf-8")

        # Phase 3: commit
        manifest.status = ArtifactStatus.COMMITTED
        _write_json(manifest_path, manifest.to_dict())

        # Phase 4: update index
        self._append_to_index(manifest)

        return make_artifact_ref(artifact_id)

    def read_artifact(self, ref: str) -> str:
        """
        Read the full content of an artifact by its nimfs:// reference.

        Args:
            ref: "nimfs://artifact/{artifact_id}" or bare artifact_id.

        Returns:
            Full content string (no size limit).

        Raises:
            ArtifactNotFoundError: Reference does not exist.
            ArtifactExpiredError:  Artifact has been GC'd.
            ArtifactPendingError:  Artifact write is still in progress.
        """
        artifact_id = self._resolve_artifact_id(ref)
        manifest, task_dir = self._load_manifest(artifact_id)

        if manifest.status == ArtifactStatus.EXPIRED:
            raise ArtifactExpiredError(artifact_id)
        if manifest.status == ArtifactStatus.PENDING:
            raise ArtifactPendingError(artifact_id)

        # H001: validate filename from manifest before using it as a path component
        _validate_filename(manifest.filename)
        content_path = task_dir / manifest.filename
        _validate_within_root(content_path, self.artifacts_root)
        if not content_path.exists():
            raise NimFSError(f"Content file missing for artifact '{artifact_id}': {content_path}")

        return content_path.read_text(encoding="utf-8")

    def get_artifact_manifest(self, ref: str) -> ArtifactManifest:
        """Return the manifest of an artifact without reading its content."""
        artifact_id = self._resolve_artifact_id(ref)
        manifest, _ = self._load_manifest(artifact_id)
        return manifest

    def list_artifacts(self, task_id: Optional[str] = None) -> List[ArtifactManifest]:
        """
        List all COMMITTED artifacts, optionally filtered by task_id.

        Args:
            task_id: Optional filter. If None, returns all committed artifacts.

        Returns:
            List of ArtifactManifest sorted by created_at descending.
        """
        index_path = self.artifacts_root / "index.json"
        if not index_path.exists():
            return []

        records = _read_json(index_path)
        results = []
        for record in records:
            if task_id and record.get("task_id") != task_id:
                continue
            if record.get("status") != ArtifactStatus.COMMITTED.value:
                continue
            results.append(ArtifactManifest.from_dict(record))

        results.sort(key=lambda m: m.created_at, reverse=True)
        return results

    # =========================================================================
    # Memory API  (long-term knowledge)
    # =========================================================================

    def write_memory(
        self,
        category: MemoryCategory,
        title: str,
        content: str,
        summary: str = "",
        confidence: float = 1.0,
        source: str = "agent",
        tags: Optional[List[str]] = None,
        scope: MemoryScope = MemoryScope.PROJECT,
    ) -> str:
        """
        Write a long-term memory entry with L0/L1/L2 hierarchy.

        Directory structure:
            memory/{category}/{memory_id}/
            ├── meta.json       ← MemoryEntry metadata
            ├── l0.abstract     ← Ultra-compact summary (< ~100 tokens)
            ├── l1.overview.md  ← Structured overview with context
            └── l2.content.md   ← Full raw content

        Args:
            category:   Memory category (entities, events, cases, etc.)
            title:      Short title for keyword search.
            content:    Full content to store.
            summary:    Human-readable abstract. Used as L0 if provided.
            confidence: Reliability score 0.0–1.0.
            source:     Producing agent role.
            tags:       Tag list for filtering.
            scope:      PROJECT (default) or GLOBAL.

        Returns:
            memory_id string.
        """
        memory_id = f"{category.value}-{uuid.uuid4().hex[:8]}"
        now = _now_iso()

        # C002: validate generated memory_id (safe by construction, but belt-and-suspenders)
        _validate_id(memory_id, "memory_id")

        # Determine storage root based on scope
        if scope == MemoryScope.GLOBAL:
            base = self.global_root / category.value
        else:
            base = self.memory_root / category.value

        entry_dir = base / memory_id
        # Validate entry_dir stays within ~/.nimbus/fs/ (covers both project and global scopes)
        _validate_within_root(entry_dir, get_nimfs_root())
        entry_dir.mkdir(parents=True, exist_ok=True)

        # L0: ultra-compact abstract (used in Anchor injection)
        l0_text = summary if summary else content[:300].replace("\n", " ")
        (entry_dir / "l0.abstract").write_text(l0_text, encoding="utf-8")

        # L1: structured overview markdown
        l1_text = f"# {title}\n\n**Category:** {category.value}  \n**Source:** {source}  \n**Confidence:** {confidence:.2f}\n\n## Summary\n\n{summary or l0_text}\n\n## Overview\n\n{content[:1000]}\n"
        (entry_dir / "l1.overview.md").write_text(l1_text, encoding="utf-8")

        # L2: full content
        (entry_dir / "l2.content.md").write_text(content, encoding="utf-8")

        # Metadata
        entry = MemoryEntry(
            memory_id=memory_id,
            category=category,
            scope=scope,
            title=title,
            created_at=now,
            updated_at=now,
            confidence=confidence,
            source=source,
            valid_from=now,
            tags=tags or [],
        )
        _write_json(entry_dir / "meta.json", entry.to_dict())

        return memory_id

    def read_memory(self, memory_id: str, layer: int = 1) -> str:
        """
        Read a memory entry at the specified layer.

        Args:
            memory_id: The memory_id returned by write_memory().
            layer:     0 = l0.abstract, 1 = l1.overview.md (default), 2 = l2.content.md

        Returns:
            Content string at the requested layer.

        Raises:
            MemoryNotFoundError: If memory_id is not found.
            ValueError:          If layer is not 0, 1, or 2.
        """
        from nimbus.core.nimfs.models import MemoryNotFoundError

        if layer not in (0, 1, 2):
            raise ValueError(f"Invalid memory layer: {layer}. Must be 0, 1, or 2.")

        entry_dir = self._find_memory_dir(memory_id)
        if entry_dir is None:
            raise MemoryNotFoundError(memory_id)

        layer_files = {0: "l0.abstract", 1: "l1.overview.md", 2: "l2.content.md"}
        file_path = entry_dir / layer_files[layer]

        if not file_path.exists():
            raise NimFSError(f"Layer {layer} file missing for memory '{memory_id}': {file_path}")

        return file_path.read_text(encoding="utf-8")

    def get_memory_entry(self, memory_id: str) -> MemoryEntry:
        """Return the MemoryEntry metadata for a given memory_id."""
        from nimbus.core.nimfs.models import MemoryNotFoundError

        entry_dir = self._find_memory_dir(memory_id)
        if entry_dir is None:
            raise MemoryNotFoundError(memory_id)

        meta_path = entry_dir / "meta.json"
        return MemoryEntry.from_dict(_read_json(meta_path))

    def search_memory(
        self,
        query: str,
        category: Optional[MemoryCategory] = None,
        top_k: int = 5,
        min_confidence: float = 0.0,
        scope: str = "project",  # "project" | "global" | "all"
    ) -> List[MemoryEntry]:
        """
        Keyword search over memory entries (Phase 0: title + tags matching).

        Searches title and tags for case-insensitive substring matches.
        Scans the specified scope(s) and returns top_k results sorted by
        updated_at descending.

        Args:
            query:          Search query string.
            category:       Optional category filter.
            top_k:          Maximum number of results.
            min_confidence: Minimum confidence threshold.
            scope:          "project", "global", or "all".

        Returns:
            List of matching MemoryEntry, sorted by updated_at descending.

        Note:
            Phase 1 will upgrade this to hybrid vector + keyword search.
        """
        query_lower = query.lower()
        results: List[MemoryEntry] = []

        search_roots: List[Path] = []
        if scope in ("project", "all"):
            search_roots.append(self.memory_root)
        if scope in ("global", "all"):
            search_roots.append(self.global_root)

        for base_root in search_roots:
            if not base_root.exists():
                continue

            categories = [category] if category else list(MemoryCategory)
            for cat in categories:
                cat_dir = base_root / cat.value
                if not cat_dir.exists():
                    continue

                for entry_dir in cat_dir.iterdir():
                    if not entry_dir.is_dir():
                        continue
                    meta_path = entry_dir / "meta.json"
                    if not meta_path.exists():
                        continue

                    try:
                        entry = MemoryEntry.from_dict(_read_json(meta_path))
                    except Exception:
                        continue

                    if entry.confidence < min_confidence:
                        continue

                    # Match against title and tags
                    searchable = entry.title.lower() + " " + " ".join(entry.tags).lower()
                    if query_lower in searchable:
                        results.append(entry)

        results.sort(key=lambda e: e.updated_at, reverse=True)
        return results[:top_k]

    def load_context(self, current_goal: str, max_chars: int = 3000) -> str:
        """
        Assemble an optimal context injection package for the Nimbus Anchor.

        Loads L0 summaries from:
          1. global/profile and global/preferences (always included)
          2. Project memory entries relevant to current_goal (keyword match)

        Args:
            current_goal: The current task/goal description.
            max_chars:    Approximate character budget for the output.

        Returns:
            Formatted markdown string ready for Anchor injection.
        """
        sections: List[str] = []
        used_chars = 0

        def _append(text: str) -> bool:
            nonlocal used_chars
            if used_chars + len(text) > max_chars:
                return False
            sections.append(text)
            used_chars += len(text)
            return True

        # 1. Global profile
        profile_entries = self._load_l0_entries(self.global_root / "profile")
        if profile_entries:
            block = "### Profile\n" + "\n".join(f"- {e}" for e in profile_entries) + "\n"
            _append(block)

        # 2. Global preferences
        pref_entries = self._load_l0_entries(self.global_root / "preferences")
        if pref_entries:
            block = "### Preferences\n" + "\n".join(f"- {e}" for e in pref_entries) + "\n"
            _append(block)

        # 3. Relevant project memory (keyword search on goal)
        if current_goal.strip():
            relevant = self.search_memory(current_goal, top_k=8, scope="project")
            if relevant:
                lines = []
                for entry in relevant:
                    entry_dir = self._find_memory_dir(entry.memory_id)
                    if entry_dir is None:
                        continue
                    l0_path = entry_dir / "l0.abstract"
                    if l0_path.exists():
                        l0 = l0_path.read_text(encoding="utf-8").strip()
                        lines.append(f"- [{entry.category.value}] **{entry.title}**: {l0}")

                if lines:
                    block = "### Relevant Knowledge\n" + "\n".join(lines) + "\n"
                    _append(block)

        if not sections:
            return ""

        header = "## NimFS Context\n\n"
        return header + "\n".join(sections)

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    def _resolve_artifact_id(self, ref: str) -> str:
        """Extract artifact_id from a nimfs:// ref or return as-is."""
        if ref.startswith("nimfs://"):
            _, artifact_id = parse_nimfs_ref(ref)
            return artifact_id
        return ref

    def _load_manifest(self, artifact_id: str) -> tuple[ArtifactManifest, Path]:
        """Load manifest.json for an artifact_id. Searches all task subdirs."""
        # First try to find via index (fast path)
        index_path = self.artifacts_root / "index.json"
        if index_path.exists():
            records = _read_json(index_path)
            for record in records:
                if record.get("artifact_id") == artifact_id:
                    task_dir = self.artifacts_root / record["task_id"]
                    manifest_path = task_dir / "manifest.json"
                    if manifest_path.exists():
                        return ArtifactManifest.from_dict(_read_json(manifest_path)), task_dir

        # Fallback: scan all task directories
        if self.artifacts_root.exists():
            for task_dir in self.artifacts_root.iterdir():
                if not task_dir.is_dir():
                    continue
                manifest_path = task_dir / "manifest.json"
                if not manifest_path.exists():
                    continue
                try:
                    data = _read_json(manifest_path)
                    if data.get("artifact_id") == artifact_id:
                        return ArtifactManifest.from_dict(data), task_dir
                except Exception:
                    continue

        raise ArtifactNotFoundError(artifact_id)

    def _append_to_index(self, manifest: ArtifactManifest) -> None:
        """
        Append an artifact manifest record to artifacts/index.json.

        C003 fix: uses atomic write (write to temp file → os.replace) to prevent
        concurrent writers from corrupting or losing records. os.replace() is
        atomic on POSIX systems (same filesystem), so concurrent calls at worst
        result in one writer's update being applied last — no data corruption.
        """
        index_path = self.artifacts_root / "index.json"

        # Retry loop for TOCTTOU window on first-time creation
        for _attempt in range(3):
            try:
                records = _read_json(index_path)
            except Exception:
                records = []

            # Update existing record if same artifact_id (e.g. status change)
            updated = False
            for i, r in enumerate(records):
                if r.get("artifact_id") == manifest.artifact_id:
                    records[i] = manifest.to_dict()
                    updated = True
                    break

            if not updated:
                records.append(manifest.to_dict())

            # Atomic write: write to sibling temp file, then rename
            try:
                tmp_fd, tmp_path = tempfile.mkstemp(
                    dir=self.artifacts_root, prefix=".index_", suffix=".tmp"
                )
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                        json.dump(records, f, ensure_ascii=False, indent=2)
                except Exception:
                    os.unlink(tmp_path)
                    raise
                os.replace(tmp_path, index_path)
                return  # success
            except Exception:
                # Rare: concurrent rename conflict — retry
                continue

    def _find_memory_dir(self, memory_id: str) -> Optional[Path]:
        """Find the directory for a memory_id by scanning all categories."""
        # Extract category hint from memory_id prefix (e.g. "entities-abc123")
        parts = memory_id.split("-", 1)
        if len(parts) == 2:
            category_hint = parts[0]
            for base in (self.memory_root, self.global_root):
                candidate = base / category_hint / memory_id
                if candidate.exists() and (candidate / "meta.json").exists():
                    return candidate

        # Full scan fallback
        for base in (self.memory_root, self.global_root):
            if not base.exists():
                continue
            for cat_dir in base.iterdir():
                if not cat_dir.is_dir():
                    continue
                candidate = cat_dir / memory_id
                if candidate.exists() and (candidate / "meta.json").exists():
                    return candidate

        return None

    def _load_l0_entries(self, category_dir: Path) -> List[str]:
        """Load all L0 abstracts from a category directory."""
        entries = []
        if not category_dir.exists():
            return entries
        for entry_dir in sorted(category_dir.iterdir()):
            if not entry_dir.is_dir():
                continue
            l0_path = entry_dir / "l0.abstract"
            if l0_path.exists():
                text = l0_path.read_text(encoding="utf-8").strip()
                if text:
                    entries.append(text)
        return entries
