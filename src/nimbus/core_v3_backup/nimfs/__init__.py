"""
NimFS: Nimbus Virtual Shared Disk

Provides two partitions:
  - artifacts/  : Short-lived pipeline products for Agent IPC (Claim-Check pattern)
  - memory/     : Long-term knowledge with 6 categories + L0/L1/L2 layers

Storage root: ~/.nimbus/fs/projects/{project_id}/

Quick start:
    from nimbus.core.nimfs import NimFSManager

    manager = NimFSManager("/path/to/workspace")

    # IPC: write a large artifact and share its reference
    ref = manager.write_artifact(content="...", task_id="task-1", producer="impl-agent")
    content = manager.read_artifact(ref)   # any agent can read this

    # Memory: store long-term knowledge
    mid = manager.write_memory(MemoryCategory.ENTITIES, "MyClass", "Full docs...")
    overview = manager.read_memory(mid, layer=1)
"""

from nimbus.core.nimfs.gc import NimFSGC
from nimbus.core.nimfs.manager import NimFSManager
from nimbus.core.nimfs.models import (
    ArtifactExpiredError,
    ArtifactManifest,
    ArtifactNotFoundError,
    ArtifactPendingError,
    ArtifactStatus,
    ArtifactTTL,
    MemoryCategory,
    MemoryEntry,
    MemoryNotFoundError,
    MemoryScope,
    NimFSError,
)
from nimbus.core.nimfs.project_id import (
    get_global_root,
    get_nimfs_root,
    get_project_id,
    get_project_root,
    make_artifact_ref,
    make_memory_ref,
    parse_nimfs_ref,
)

__all__ = [
    # Core manager
    "NimFSManager",
    "NimFSGC",
    # Models
    "ArtifactManifest",
    "ArtifactStatus",
    "ArtifactTTL",
    "MemoryEntry",
    "MemoryCategory",
    "MemoryScope",
    # Exceptions
    "NimFSError",
    "ArtifactNotFoundError",
    "ArtifactExpiredError",
    "ArtifactPendingError",
    "MemoryNotFoundError",
    # Path utilities
    "get_project_id",
    "get_nimfs_root",
    "get_global_root",
    "get_project_root",
    "parse_nimfs_ref",
    "make_artifact_ref",
    "make_memory_ref",
]
