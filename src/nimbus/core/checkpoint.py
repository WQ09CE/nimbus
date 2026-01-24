"""Checkpoint persistence for DAG execution (inspired by LangGraph).

This module provides checkpoint saving and loading capabilities for TaskDAG,
enabling durable execution that can survive process crashes and restarts.
"""

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .types import TaskDAG


@dataclass
class CheckpointMeta:
    """Checkpoint metadata.

    Attributes:
        checkpoint_id: Unique identifier for this checkpoint (ISO timestamp).
        dag_id: The DAG this checkpoint belongs to.
        timestamp: When the checkpoint was created.
        completed_nodes: Number of completed nodes at checkpoint time.
        total_nodes: Total number of nodes in the DAG.
    """
    checkpoint_id: str
    dag_id: str
    timestamp: datetime
    completed_nodes: int
    total_nodes: int

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "checkpoint_id": self.checkpoint_id,
            "dag_id": self.dag_id,
            "timestamp": self.timestamp.isoformat(),
            "completed_nodes": self.completed_nodes,
            "total_nodes": self.total_nodes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointMeta":
        """Create from dictionary."""
        return cls(
            checkpoint_id=data["checkpoint_id"],
            dag_id=data["dag_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            completed_nodes=data["completed_nodes"],
            total_nodes=data["total_nodes"],
        )


class CheckpointSaver(ABC):
    """Abstract checkpoint saver interface (inspired by LangGraph BaseCheckpointSaver).

    Implementations of this interface provide different storage backends
    for persisting DAG execution state.
    """

    @abstractmethod
    def save(self, dag: "TaskDAG") -> str:
        """Save a DAG state snapshot.

        Args:
            dag: The TaskDAG to checkpoint.

        Returns:
            checkpoint_id: Unique identifier for this checkpoint.
        """
        pass

    @abstractmethod
    def load(self, dag_id: str, checkpoint_id: Optional[str] = None) -> Optional["TaskDAG"]:
        """Load a checkpoint.

        Args:
            dag_id: The DAG identifier.
            checkpoint_id: Specific checkpoint to load. If None, loads the latest.

        Returns:
            The restored TaskDAG, or None if not found.
        """
        pass

    @abstractmethod
    def list(self, dag_id: str) -> List[CheckpointMeta]:
        """List all checkpoints for a DAG.

        Args:
            dag_id: The DAG identifier.

        Returns:
            List of checkpoint metadata, sorted by timestamp (newest first).
        """
        pass

    @abstractmethod
    def delete(self, dag_id: str, checkpoint_id: Optional[str] = None) -> int:
        """Delete checkpoints.

        Args:
            dag_id: The DAG identifier.
            checkpoint_id: Specific checkpoint to delete. If None, deletes all.

        Returns:
            Number of checkpoints deleted.
        """
        pass


class JsonCheckpointSaver(CheckpointSaver):
    """JSON file-based checkpoint saver.

    Stores checkpoints as JSON files in a directory structure:

        {base_dir}/
        └── {dag_id}/
            ├── latest.json -> {checkpoint_id}.json (symlink)
            ├── {checkpoint_id_1}.json
            └── {checkpoint_id_2}.json

    Attributes:
        base_dir: Base directory for checkpoint storage.
    """

    def __init__(self, base_dir: Optional[str] = None):
        """Initialize JSON checkpoint saver.

        Args:
            base_dir: Directory to store checkpoints.
                      Defaults to ~/.nimbus/checkpoints/
        """
        self.base_dir = Path(base_dir or os.path.expanduser("~/.nimbus/checkpoints"))
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_dag_dir(self, dag_id: str) -> Path:
        """Get the directory for a specific DAG."""
        dag_dir = self.base_dir / dag_id
        dag_dir.mkdir(parents=True, exist_ok=True)
        return dag_dir

    def _generate_checkpoint_id(self) -> str:
        """Generate a unique checkpoint ID based on ISO timestamp."""
        return datetime.now().strftime("%Y%m%dT%H%M%S_%f")

    def _count_completed_nodes(self, dag: "TaskDAG") -> int:
        """Count completed nodes in a DAG."""
        from .types import TaskStatus
        return sum(
            1 for node in dag.nodes.values()
            if node.status == TaskStatus.COMPLETED
        )

    def save(self, dag: "TaskDAG") -> str:
        """Save a DAG state snapshot to JSON file.

        Args:
            dag: The TaskDAG to checkpoint.

        Returns:
            checkpoint_id: The generated checkpoint ID.
        """
        checkpoint_id = self._generate_checkpoint_id()
        dag_dir = self._get_dag_dir(dag.id)

        # Create checkpoint data with metadata
        checkpoint_data = {
            "meta": {
                "checkpoint_id": checkpoint_id,
                "dag_id": dag.id,
                "timestamp": datetime.now().isoformat(),
                "completed_nodes": self._count_completed_nodes(dag),
                "total_nodes": len(dag.nodes),
            },
            "dag": dag.to_dict(),
        }

        # Write checkpoint file
        checkpoint_file = dag_dir / f"{checkpoint_id}.json"
        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)

        # Update latest symlink
        latest_link = dag_dir / "latest.json"
        if latest_link.is_symlink() or latest_link.exists():
            latest_link.unlink()
        latest_link.symlink_to(checkpoint_file.name)

        return checkpoint_id

    def load(self, dag_id: str, checkpoint_id: Optional[str] = None) -> Optional["TaskDAG"]:
        """Load a checkpoint from JSON file.

        Args:
            dag_id: The DAG identifier.
            checkpoint_id: Specific checkpoint to load. If None, loads the latest.

        Returns:
            The restored TaskDAG, or None if not found.
        """
        from .types import TaskDAG

        dag_dir = self.base_dir / dag_id
        if not dag_dir.exists():
            return None

        # Determine which file to load
        if checkpoint_id:
            checkpoint_file = dag_dir / f"{checkpoint_id}.json"
        else:
            # Load latest
            latest_link = dag_dir / "latest.json"
            if latest_link.is_symlink():
                checkpoint_file = dag_dir / latest_link.resolve().name
            elif latest_link.exists():
                checkpoint_file = latest_link
            else:
                # No latest link, find the most recent file
                json_files = sorted(
                    dag_dir.glob("*.json"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True
                )
                # Filter out 'latest.json' if it's a regular file
                json_files = [f for f in json_files if f.name != "latest.json"]
                if not json_files:
                    return None
                checkpoint_file = json_files[0]

        if not checkpoint_file.exists():
            return None

        # Read and parse checkpoint
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            checkpoint_data = json.load(f)

        return TaskDAG.from_dict(checkpoint_data["dag"])

    def list(self, dag_id: str) -> List[CheckpointMeta]:
        """List all checkpoints for a DAG.

        Args:
            dag_id: The DAG identifier.

        Returns:
            List of checkpoint metadata, sorted by timestamp (newest first).
        """
        dag_dir = self.base_dir / dag_id
        if not dag_dir.exists():
            return []

        checkpoints = []
        for json_file in dag_dir.glob("*.json"):
            if json_file.name == "latest.json":
                continue

            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                meta = CheckpointMeta.from_dict(data["meta"])
                checkpoints.append(meta)
            except (json.JSONDecodeError, KeyError, ValueError):
                # Skip corrupted files
                continue

        # Sort by timestamp, newest first
        checkpoints.sort(key=lambda m: m.timestamp, reverse=True)
        return checkpoints

    def delete(self, dag_id: str, checkpoint_id: Optional[str] = None) -> int:
        """Delete checkpoints.

        Args:
            dag_id: The DAG identifier.
            checkpoint_id: Specific checkpoint to delete. If None, deletes all.

        Returns:
            Number of checkpoints deleted.
        """
        dag_dir = self.base_dir / dag_id
        if not dag_dir.exists():
            return 0

        deleted = 0

        if checkpoint_id:
            # Delete specific checkpoint
            checkpoint_file = dag_dir / f"{checkpoint_id}.json"
            if checkpoint_file.exists():
                checkpoint_file.unlink()
                deleted = 1

                # Update latest link if it pointed to this file
                latest_link = dag_dir / "latest.json"
                if latest_link.is_symlink():
                    try:
                        if latest_link.resolve().name == checkpoint_file.name:
                            latest_link.unlink()
                            # Point to next most recent
                            remaining = self.list(dag_id)
                            if remaining:
                                new_latest = dag_dir / f"{remaining[0].checkpoint_id}.json"
                                latest_link.symlink_to(new_latest.name)
                    except (OSError, ValueError):
                        pass
        else:
            # Delete all checkpoints for this DAG
            for json_file in dag_dir.glob("*.json"):
                try:
                    json_file.unlink()
                    if json_file.name != "latest.json":
                        deleted += 1
                except OSError:
                    pass

            # Remove directory if empty
            try:
                dag_dir.rmdir()
            except OSError:
                pass

        return deleted

    def cleanup_old(self, dag_id: str, keep_count: int = 5) -> int:
        """Remove old checkpoints, keeping only the most recent ones.

        Args:
            dag_id: The DAG identifier.
            keep_count: Number of checkpoints to keep.

        Returns:
            Number of deleted checkpoints.
        """
        checkpoints = self.list(dag_id)
        to_delete = checkpoints[keep_count:]

        deleted = 0
        for meta in to_delete:
            deleted += self.delete(dag_id, meta.checkpoint_id)

        return deleted
