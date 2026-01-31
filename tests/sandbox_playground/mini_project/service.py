"""Business logic service."""
from typing import Dict, List, Optional
from .models import Task, Project


class TaskService:
    """Service for managing tasks."""

    def __init__(self):
        self.projects: Dict[str, Project] = {}

    def create_project(self, project_id: str, name: str) -> Project:
        """Create a new project."""
        project = Project(id=project_id, name=name)
        self.projects[project_id] = project
        return project

    def add_task_to_project(
        self,
        project_id: str,
        task_id: str,
        title: str,
        description: str = "",
        priority: int = 0,
    ) -> Optional[Task]:
        """Add a task to a project."""
        project = self.projects.get(project_id)
        if not project:
            return None

        task = Task(
            id=task_id,
            title=title,
            description=description,
            priority=priority,
        )
        project.add_task(task)
        return task

    def complete_task(self, project_id: str, task_id: str) -> bool:
        """Mark a task as completed."""
        project = self.projects.get(project_id)
        if not project:
            return False

        for task in project.tasks:
            if task.id == task_id:
                task.completed = True
                return True
        return False

    def get_all_tasks(self, project_id: str) -> List[Task]:
        """Get all tasks in a project."""
        project = self.projects.get(project_id)
        return project.tasks if project else []

    def get_high_priority_tasks(self, project_id: str) -> List[Task]:
        """Get all tasks with priority >= 5 from a project."""
        project = self.projects.get(project_id)
        if not project:
            return []
        return [task for task in project.tasks if task.priority >= 5]
