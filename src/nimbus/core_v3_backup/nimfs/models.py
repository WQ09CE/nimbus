"""
NimFS Data Models

Core data structures for NimFS: the shared virtual disk for Nimbus agents.
Covers both Artifact (IPC pipeline products) and Memory (long-term knowledge).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

# =============================================================================
# Enumerations
# =============================================================================


class ArtifactTTL(str, Enum):
    """Lifecycle level for artifacts."""
    TASK      = "task"       # Auto-GC 30 minutes after task completes
    SESSION   = "session"    # Cleaned up when session ends
    PROJECT   = "project"    # Manual trigger or defrag()
    PERMANENT = "permanent"  # Never auto-GC (promoted to memory)


class ArtifactStatus(str, Enum):
    """Write state machine for artifacts."""
    PENDING   = "pending"    # Write in progress (atomicity guard)
    COMMITTED = "committed"  # Fully written, safe to read
    EXPIRED   = "expired"    # TTL exceeded, pending GC (tombstone)


class MemoryCategory(str, Enum):
    """Six-category memory taxonomy (inspired by OpenViking)."""
    PROFILE      = "profile"      # Agent identity & role definition
    PREFERENCES  = "preferences"  # User preferences, style, constraints
    ENTITIES     = "entities"     # Key objects, components, file associations
    EVENTS       = "events"       # State changes, milestones
    CASES        = "cases"        # Success/failure experience cases
    PATTERNS     = "patterns"     # Abstract architecture patterns, tech specs


class MemoryScope(str, Enum):
    """Storage scope for memory entries."""
    GLOBAL  = "global"   # Cross-project (profile, preferences)
    PROJECT = "project"  # Project-specific (entities, events, cases, patterns)


# =============================================================================
# Artifact Models
# =============================================================================


@dataclass
class ArtifactManifest:
    """
    Metadata descriptor for a NimFS artifact.

    Stored as manifest.json inside artifacts/{task_id}/.
    Follows Write-Once Immutable semantics: once COMMITTED, content never changes.
    New versions create new artifacts with supersedes pointing to the old one.
    """
    artifact_id: str              # Unique ID: {task_id}-{uuid[:8]}
    task_id: str                  # Owning task ID
    producer: str                 # Producer agent role (e.g. "implement-agent")
    type: str                     # "code" | "report" | "diff" | "json" | "text"
    filename: str                 # Actual filename (e.g. "content.py")
    size_bytes: int               # Content size in bytes
    created_at: str               # ISO8601 timestamp
    ttl: ArtifactTTL              # Lifecycle level
    status: ArtifactStatus        # Write state
    summary: str                  # Human-readable summary, < 200 chars
    tags: List[str]               # Tag list for filtering
    supersedes: Optional[str] = None   # artifact_id of the older version this replaces

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "task_id": self.task_id,
            "producer": self.producer,
            "type": self.type,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "ttl": self.ttl.value,
            "status": self.status.value,
            "summary": self.summary,
            "tags": self.tags,
            "supersedes": self.supersedes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ArtifactManifest":
        return cls(
            artifact_id=data["artifact_id"],
            task_id=data["task_id"],
            producer=data["producer"],
            type=data["type"],
            filename=data["filename"],
            size_bytes=data["size_bytes"],
            created_at=data["created_at"],
            ttl=ArtifactTTL(data["ttl"]),
            status=ArtifactStatus(data["status"]),
            summary=data.get("summary", ""),
            tags=data.get("tags", []),
            supersedes=data.get("supersedes"),
        )


# =============================================================================
# Memory Models
# =============================================================================


@dataclass
class MemoryEntry:
    """
    Metadata descriptor for a NimFS long-term memory entry.

    Stored as meta.json inside memory/{category}/{memory_id}/.
    Alongside: l0.abstract, l1.overview.md, l2.content.md
    """
    memory_id: str                # Unique ID: {category}-{uuid[:8]}
    category: MemoryCategory      # Memory category
    scope: MemoryScope            # global or project
    title: str                    # Title (used for keyword search)
    created_at: str               # ISO8601
    updated_at: str               # ISO8601
    confidence: float             # Reliability score 0.0 ~ 1.0
    source: str                   # Source agent role
    valid_from: str               # ISO8601, effective from
    valid_until: Optional[str] = None   # None = never expires
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id,
            "category": self.category.value,
            "scope": self.scope.value,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confidence": self.confidence,
            "source": self.source,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        return cls(
            memory_id=data["memory_id"],
            category=MemoryCategory(data["category"]),
            scope=MemoryScope(data.get("scope", "project")),
            title=data["title"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            confidence=data.get("confidence", 1.0),
            source=data.get("source", "agent"),
            valid_from=data["valid_from"],
            valid_until=data.get("valid_until"),
            tags=data.get("tags", []),
        )


# =============================================================================
# Exceptions
# =============================================================================


class NimFSError(Exception):
    """Base exception for all NimFS errors."""


class ArtifactNotFoundError(NimFSError):
    """Raised when a nimfs:// reference does not exist."""
    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        super().__init__(f"Artifact '{artifact_id}' not found in NimFS")


class ArtifactExpiredError(NimFSError):
    """Raised when a nimfs:// reference has been GC'd or TTL has expired."""
    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        super().__init__(
            f"Artifact '{artifact_id}' has expired or been GC'd. "
            "It can no longer be read. Check if a newer version exists."
        )


class ArtifactPendingError(NimFSError):
    """Raised when trying to read an artifact that is still being written."""
    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        super().__init__(f"Artifact '{artifact_id}' is still in PENDING state (write not complete)")


class MemoryNotFoundError(NimFSError):
    """Raised when a memory_id does not exist."""
    def __init__(self, memory_id: str):
        self.memory_id = memory_id
        super().__init__(f"Memory entry '{memory_id}' not found in NimFS")
