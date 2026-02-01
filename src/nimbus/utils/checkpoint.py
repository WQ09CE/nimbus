"""Checkpoint persistence for memory state."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Try to use aiofiles, fall back to sync if not available
try:
    import aiofiles

    HAS_AIOFILES = True
except ImportError:
    HAS_AIOFILES = False


class CheckpointManager:
    """Manages checkpoint persistence for memory state."""

    def __init__(self, base_path: str = "./.checkpoints"):
        """Initialize checkpoint manager.

        Args:
            base_path: Directory to store checkpoint files.
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def save(self, session_id: str, data: dict) -> str:
        """Save checkpoint data.

        Args:
            session_id: Session identifier.
            data: Data to checkpoint.

        Returns:
            Path to saved checkpoint file.
        """
        filename = f"{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.base_path / filename

        content = json.dumps(data, ensure_ascii=False, indent=2, default=self._json_default)

        if HAS_AIOFILES:
            async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
                await f.write(content)
        else:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

        return str(filepath)

    async def load_latest(self, session_id: str) -> Optional[dict]:
        """Load the most recent checkpoint for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Checkpoint data or None if no checkpoint exists.
        """
        files = sorted(self.base_path.glob(f"{session_id}_*.json"), reverse=True)
        if not files:
            return None

        if HAS_AIOFILES:
            async with aiofiles.open(files[0], "r", encoding="utf-8") as f:
                content = await f.read()
        else:
            with open(files[0], "r", encoding="utf-8") as f:
                content = f.read()

        return json.loads(content)

    def list_checkpoints(self, session_id: str) -> list[str]:
        """List all checkpoints for a session.

        Args:
            session_id: Session identifier.

        Returns:
            List of checkpoint file paths.
        """
        return [str(f) for f in sorted(self.base_path.glob(f"{session_id}_*.json"))]

    def delete_checkpoint(self, filepath: str) -> bool:
        """Delete a specific checkpoint file.

        Args:
            filepath: Path to checkpoint file.

        Returns:
            True if deleted, False otherwise.
        """
        try:
            Path(filepath).unlink()
            return True
        except FileNotFoundError:
            return False

    def cleanup_old(self, session_id: str, keep_count: int = 5) -> int:
        """Remove old checkpoints, keeping only the most recent ones.

        Args:
            session_id: Session identifier.
            keep_count: Number of checkpoints to keep.

        Returns:
            Number of deleted checkpoints.
        """
        files = sorted(self.base_path.glob(f"{session_id}_*.json"), reverse=True)
        to_delete = files[keep_count:]

        deleted = 0
        for f in to_delete:
            try:
                f.unlink()
                deleted += 1
            except Exception:
                pass

        return deleted

    def _json_default(self, obj: Any) -> Any:
        """JSON serialization helper for non-standard types."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        return str(obj)
