"""
NimFS Garbage Collector

Handles TTL-based cleanup of expired artifacts and memory defragmentation.

TTL Thresholds:
    TASK      → 30 minutes after created_at
    SESSION   → Triggered externally at session end
    PROJECT   → Only via defrag()
    PERMANENT → Never auto-GC
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from nimbus.core.nimfs.models import ArtifactManifest, ArtifactStatus, ArtifactTTL
from nimbus.core.nimfs.project_id import get_project_root

# TTL thresholds (in minutes)
_TTL_MINUTES: Dict[ArtifactTTL, int] = {
    ArtifactTTL.TASK:    30,
    ArtifactTTL.SESSION: 60 * 24,   # 24h as safety ceiling; normally triggered at session end
}

_TTL_ORDER = [ArtifactTTL.TASK, ArtifactTTL.SESSION, ArtifactTTL.PROJECT, ArtifactTTL.PERMANENT]


def _ttl_level(ttl: ArtifactTTL) -> int:
    return _TTL_ORDER.index(ttl)


def _is_expired(manifest: ArtifactManifest, ttl_level_limit: ArtifactTTL) -> bool:
    """Return True if the artifact should be GC'd at this TTL pass."""
    if manifest.status == ArtifactStatus.EXPIRED:
        return True  # Already expired (tombstone), safe to fully remove

    artifact_ttl_level = _ttl_level(manifest.ttl)
    limit_level = _ttl_level(ttl_level_limit)

    # PERMANENT is never auto-GC'd
    if manifest.ttl == ArtifactTTL.PERMANENT:
        return False

    # Only GC artifacts at or below the requested TTL level
    if artifact_ttl_level > limit_level:
        return False

    threshold_minutes = _TTL_MINUTES.get(manifest.ttl)
    if threshold_minutes is None:
        return False

    try:
        created = datetime.fromisoformat(manifest.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - created
        return age > timedelta(minutes=threshold_minutes)
    except ValueError:
        return False


class NimFSGC:
    """
    Garbage collector for NimFS artifacts.

    Usage:
        gc = NimFSGC()
        cleaned = gc.gc_artifacts("/path/to/workspace", ttl_level=ArtifactTTL.TASK)
        stats = gc.defrag("/path/to/workspace")
    """

    def gc_artifacts(
        self,
        workspace_path: str | Path,
        ttl_level: ArtifactTTL = ArtifactTTL.TASK,
        dry_run: bool = False,
    ) -> int:
        """
        Clean up expired artifacts up to and including the given TTL level.

        Strategy:
          1. Read artifacts/index.json
          2. For each artifact, check if it should be expired at this TTL level
          3. Mark manifest.json status=EXPIRED (tombstone)
          4. Delete content file (but keep manifest as tombstone for auditability)
          5. Update index.json with new status

        Args:
            workspace_path: Workspace root directory.
            ttl_level:      Clean artifacts at this TTL level and below.
                            e.g. TASK cleans only TASK-level artifacts.
                            SESSION cleans TASK + SESSION artifacts.
            dry_run:        If True, report what would be cleaned without deleting.

        Returns:
            Number of artifacts cleaned (or that would be cleaned in dry_run mode).
        """
        project_root = get_project_root(workspace_path)
        artifacts_root = project_root / "artifacts"
        index_path = artifacts_root / "index.json"

        if not index_path.exists():
            return 0

        try:
            records: List[dict] = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            return 0

        cleaned = 0
        updated_records = []

        for record in records:
            try:
                manifest = ArtifactManifest.from_dict(record)
            except Exception:
                updated_records.append(record)
                continue

            if _is_expired(manifest, ttl_level):
                cleaned += 1
                if not dry_run:
                    # Mark as expired in manifest.json (tombstone)
                    task_dir = artifacts_root / manifest.task_id
                    manifest_path = task_dir / "manifest.json"
                    if manifest_path.exists():
                        manifest.status = ArtifactStatus.EXPIRED
                        manifest_path.write_text(
                            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )

                    # Delete content file (preserve manifest as audit trail)
                    content_path = task_dir / manifest.filename
                    if content_path.exists():
                        content_path.unlink()

                    # Update index record
                    record["status"] = ArtifactStatus.EXPIRED.value
            updated_records.append(record)

        if not dry_run:
            index_path.write_text(
                json.dumps(updated_records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return cleaned

    def gc_session(self, workspace_path: str | Path, dry_run: bool = False) -> int:
        """
        Clean up all SESSION-level (and TASK-level) artifacts.

        Should be called at session end by AgentOS.
        """
        return self.gc_artifacts(workspace_path, ttl_level=ArtifactTTL.SESSION, dry_run=dry_run)

    def defrag(self, workspace_path: str | Path) -> Dict[str, int]:
        """
        Defragment NimFS storage.

        Operations:
          1. Remove EXPIRED tombstones and empty task directories from index.json
          2. Clean up orphaned directories (directories with no manifest.json)
          3. Report statistics

        Note: Memory deduplication (merging similar entries) is a Phase 2 feature
        requiring LLM assistance.

        Args:
            workspace_path: Workspace root directory.

        Returns:
            Dict with statistics: {
                "tombstones_removed": int,
                "orphans_cleaned": int,
            }
        """
        project_root = get_project_root(workspace_path)
        artifacts_root = project_root / "artifacts"
        index_path = artifacts_root / "index.json"

        stats = {"tombstones_removed": 0, "orphans_cleaned": 0}

        # 1. Remove EXPIRED tombstones from index
        if index_path.exists():
            try:
                records = json.loads(index_path.read_text(encoding="utf-8"))
                live_records = []
                for record in records:
                    if record.get("status") == ArtifactStatus.EXPIRED.value:
                        stats["tombstones_removed"] += 1
                        # Also remove the task directory if it's now empty
                        task_id = record.get("task_id", "")
                        task_dir = artifacts_root / task_id
                        if task_dir.exists():
                            remaining = list(task_dir.iterdir())
                            # Only manifest tombstone left → remove entire dir
                            if len(remaining) <= 1:
                                for f in remaining:
                                    f.unlink()
                                task_dir.rmdir()
                    else:
                        live_records.append(record)
                index_path.write_text(
                    json.dumps(live_records, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

        # 2. Clean orphaned task directories (no manifest.json)
        if artifacts_root.exists():
            for task_dir in artifacts_root.iterdir():
                if not task_dir.is_dir() or task_dir.name == "index.json":
                    continue
                if not (task_dir / "manifest.json").exists():
                    try:
                        for f in task_dir.iterdir():
                            f.unlink()
                        task_dir.rmdir()
                        stats["orphans_cleaned"] += 1
                    except Exception:
                        pass

        return stats
