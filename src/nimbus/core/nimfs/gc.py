from __future__ import annotations
from typing import Any, TYPE_CHECKING
from loguru import logger

if TYPE_CHECKING:
    from nimbus.core.process.state import Process

class NimFSGC:
    """Garbage Collector for NimFS artifacts tied to tasks or sessions."""

    def __init__(self, agent_os):
        self.agent_os = agent_os

    def _nimfs_gc_task(self, process: "Process") -> None:
            """
            Clean up TASK-level NimFS artifacts after a sub-process finishes.
            Called from _run_process() on normal completion.
            Runs silently — any error is swallowed to avoid disrupting the main flow.
            """
            try:
                workspace = getattr(process.mmu, "nimfs_workspace", None) if process.mmu else None
                if not workspace:
                    workspace = str(Path.cwd())
                from nimbus.core.nimfs.gc import NimFSGC
                from nimbus.core.nimfs.models import ArtifactTTL
                NimFSGC().gc_artifacts(workspace, ttl_level=ArtifactTTL.TASK)
            except Exception:
                pass

    def _nimfs_gc_session(self, process: "Process") -> None:
            """
            Clean up SESSION-level (and TASK-level) NimFS artifacts when a session ends.
            Called from end_session().
            Runs silently — any error is swallowed to avoid disrupting the main flow.
            """
            try:
                workspace = getattr(process.mmu, "nimfs_workspace", None) if process.mmu else None
                if not workspace:
                    workspace = str(Path.cwd())
                from nimbus.core.nimfs.gc import NimFSGC
                NimFSGC().gc_session(workspace)
            except Exception:
                pass

