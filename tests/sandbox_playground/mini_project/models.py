"""Data models."""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Task:
    """A task item."""
    id: str
    title: str
    description: str = ""
    completed: bool = False
    priority: int = 0
    tags: List[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class Project:
    """A project containing tasks."""
    id: str
    name: str
    tasks: List[Task] = None

    def __post_init__(self):
        if self.tasks is None:
            self.tasks = []

    def add_task(self, task: Task):
        self.tasks.append(task)

    def get_completed_tasks(self) -> List[Task]:
        return [t for t in self.tasks if t.completed]

    def get_pending_tasks(self) -> List[Task]:
        return [t for t in self.tasks if not t.completed]
