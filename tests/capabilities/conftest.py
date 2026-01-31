"""
Pytest configuration for capability tests.

Provides fixtures for:
- Temporary workspaces
- Agent execution
- Task loading
- Logging configuration
"""

import pytest
import tempfile
import shutil
import asyncio
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import yaml

# Configure logging for tests
from nimbus.core.logging import setup_logging

# Setup logging to file during tests
_log_path = setup_logging(
    level="DEBUG",
    log_dir=".logs",
    log_file="nimbus-test.log",
    console=False,  # Don't spam console, pytest captures stderr
)


@dataclass
class TaskDefinition:
    """A coding task definition."""
    task_id: str
    instruction: str
    difficulty: str
    category: str
    timeout_sec: float = 120.0
    workspace_files: dict = None  # Files to create in workspace
    
    @classmethod
    def from_yaml(cls, path: Path) -> "TaskDefinition":
        """Load task from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(
            task_id=path.parent.name,
            instruction=data["instruction"],
            difficulty=data.get("difficulty", "medium"),
            category=data.get("category", "coding"),
            timeout_sec=data.get("timeout_sec", 120.0),
            workspace_files=data.get("workspace_files", {}),
        )


@dataclass 
class TaskResult:
    """Result of running a task."""
    task_id: str
    success: bool
    output: str
    error: Optional[str] = None
    duration_sec: float = 0.0


@pytest.fixture
def task_workspace(tmp_path):
    """Create a temporary workspace for a task."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    yield workspace
    # Cleanup handled by tmp_path


@pytest.fixture
def tasks_dir():
    """Return path to tasks directory."""
    return Path(__file__).parent / "tasks"


def load_task(tasks_dir: Path, task_id: str) -> TaskDefinition:
    """Load a task definition by ID."""
    task_path = tasks_dir / task_id / "task.yaml"
    if not task_path.exists():
        raise ValueError(f"Task not found: {task_id}")
    return TaskDefinition.from_yaml(task_path)


def setup_workspace(workspace: Path, task: TaskDefinition) -> None:
    """Set up workspace with initial files."""
    if task.workspace_files:
        for filename, content in task.workspace_files.items():
            file_path = workspace / filename
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)


# run_agent_task moved to test_agent_capabilities.py
