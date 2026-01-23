"""Data types for OpenNotebook."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal
from datetime import datetime
from enum import Enum
import uuid


# =============================================================================
# Artifact Types (Phase 4)
# =============================================================================


class ArtifactType(str, Enum):
    """Artifact type enumeration.

    Artifacts are structured outputs produced by the agent during task execution.
    """
    FILE = "file"           # PPT, Word, PDF, etc.
    CHART = "chart"         # ECharts, Plotly configuration
    CODE = "code"           # Code blocks
    TABLE = "table"         # Tabular data
    IMAGE = "image"         # Images
    MARKDOWN = "markdown"   # Markdown documents


@dataclass
class Artifact:
    """Agent-produced structured artifact.

    Artifacts represent concrete outputs from task execution, such as:
    - Generated files (documents, presentations)
    - Chart configurations for visualization
    - Code snippets
    - Structured data tables
    - Images or diagrams

    Attributes:
        id: Unique identifier for the artifact.
        type: Type of artifact (file, chart, code, etc.).
        title: Human-readable title.
        data: Content payload (type depends on artifact type).
        mime_type: Optional MIME type for binary content.
        url: Optional download/access URL.
        metadata: Additional metadata (source task, timestamps, etc.).
    """
    id: str
    type: ArtifactType
    title: str
    data: Any
    mime_type: Optional[str] = None
    url: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize artifact to dictionary.

        Returns:
            Dictionary representation of the artifact.
        """
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, ArtifactType) else self.type,
            "title": self.title,
            "data": self.data,
            "mime_type": self.mime_type,
            "url": self.url,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        """Create artifact from dictionary.

        Args:
            data: Dictionary with artifact data.

        Returns:
            Artifact instance.
        """
        artifact_type = data.get("type")
        if isinstance(artifact_type, str):
            artifact_type = ArtifactType(artifact_type)

        return cls(
            id=data["id"],
            type=artifact_type,
            title=data["title"],
            data=data.get("data"),
            mime_type=data.get("mime_type"),
            url=data.get("url"),
            metadata=data.get("metadata", {}),
        )


class TaskType(Enum):
    """Types of tasks that can be executed."""
    CHAT = "chat"
    SEARCH = "search"
    ANALYZE = "analyze"
    GENERATE = "generate"


# =============================================================================
# DAG Execution Types (Phase 2)
# =============================================================================

class TaskStatus(Enum):
    """Status of a task node in the DAG."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"  # Skipped due to upstream failure


@dataclass
class TaskNode:
    """A node in the task DAG with dependencies."""
    id: str
    skill: str
    params: Dict[str, Any]
    depends_on: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    # Re-planning support
    is_checkpoint: bool = False  # Trigger re-plan when completed

    @property
    def duration_ms(self) -> Optional[int]:
        """Calculate execution duration in milliseconds."""
        if self.started_at and self.finished_at:
            delta = self.finished_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "skill": self.skill,
            "params": self.params,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "is_checkpoint": self.is_checkpoint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskNode":
        """Create TaskNode from dictionary.

        Args:
            data: Dictionary with node data.

        Returns:
            TaskNode instance.
        """
        started_at = None
        if data.get("started_at"):
            started_at = datetime.fromisoformat(data["started_at"])

        finished_at = None
        if data.get("finished_at"):
            finished_at = datetime.fromisoformat(data["finished_at"])

        return cls(
            id=data["id"],
            skill=data["skill"],
            params=data.get("params", {}),
            depends_on=data.get("depends_on", []),
            status=TaskStatus(data.get("status", "pending")),
            result=data.get("result"),
            error=data.get("error"),
            started_at=started_at,
            finished_at=finished_at,
            is_checkpoint=data.get("is_checkpoint", False),
        )


@dataclass
class TaskDAG:
    """Directed Acyclic Graph of tasks with dependencies."""
    id: str
    goal: str
    nodes: Dict[str, TaskNode]
    created_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def create(cls, goal: str, tasks: List[Dict[str, Any]]) -> "TaskDAG":
        """Create DAG from task definitions.

        Args:
            goal: User's original goal
            tasks: List of task dicts with id, skill, params, depends_on

        Returns:
            TaskDAG instance
        """
        dag_id = f"dag_{uuid.uuid4().hex[:8]}"
        nodes = {}

        for task in tasks:
            node = TaskNode(
                id=task["id"],
                skill=task["skill"],
                params=task.get("params", {}),
                depends_on=task.get("depends_on", []),
                is_checkpoint=task.get("is_checkpoint", False),
            )
            nodes[node.id] = node

        return cls(id=dag_id, goal=goal, nodes=nodes)

    @classmethod
    def create_simple(cls, skill: str, params: Dict[str, Any]) -> "TaskDAG":
        """Create a simple single-task DAG (for direct/fallback mode)."""
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        return cls.create(
            goal="direct_response",
            tasks=[{"id": task_id, "skill": skill, "params": params}]
        )

    def get_ready_tasks(self) -> List[TaskNode]:
        """Get all tasks whose dependencies are satisfied.

        Returns:
            List of TaskNode that can be executed now
        """
        ready = []
        for node in self.nodes.values():
            if node.status != TaskStatus.PENDING:
                continue

            # Check all dependencies are completed
            deps_satisfied = all(
                self.nodes[dep_id].status == TaskStatus.COMPLETED
                for dep_id in node.depends_on
                if dep_id in self.nodes
            )

            if deps_satisfied:
                ready.append(node)

        return ready

    def is_completed(self) -> bool:
        """Check if all tasks are in terminal state."""
        return all(
            node.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SKIPPED)
            for node in self.nodes.values()
        )

    def get_results(self) -> Dict[str, Any]:
        """Collect results from all completed tasks."""
        return {
            node_id: node.result
            for node_id, node in self.nodes.items()
            if node.status == TaskStatus.COMPLETED
        }

    def get_errors(self) -> Dict[str, str]:
        """Collect errors from all failed tasks."""
        return {
            node_id: node.error or "Unknown error"
            for node_id, node in self.nodes.items()
            if node.status == TaskStatus.FAILED
        }

    def get_downstream_tasks(self, task_id: str) -> List[TaskNode]:
        """Get all tasks that depend on the given task."""
        return [
            node for node in self.nodes.values()
            if task_id in node.depends_on
        ]

    def mark_downstream_skipped(self, failed_task_id: str) -> None:
        """Mark all downstream tasks as SKIPPED due to upstream failure."""
        to_skip = [failed_task_id]
        visited = set()

        while to_skip:
            current = to_skip.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for node in self.get_downstream_tasks(current):
                if node.status == TaskStatus.PENDING:
                    node.status = TaskStatus.SKIPPED
                    node.error = f"Skipped: upstream task '{current}' failed"
                    to_skip.append(node.id)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "goal": self.goal,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskDAG":
        """Create TaskDAG from dictionary.

        Args:
            data: Dictionary with DAG data.

        Returns:
            TaskDAG instance with restored nodes and state.
        """
        # Parse created_at
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        # Restore nodes
        nodes = {}
        nodes_data = data.get("nodes", {})
        for node_id, node_data in nodes_data.items():
            nodes[node_id] = TaskNode.from_dict(node_data)

        return cls(
            id=data["id"],
            goal=data.get("goal", ""),
            nodes=nodes,
            created_at=created_at,
        )

    @property
    def completed_count(self) -> int:
        """Count of completed nodes."""
        return sum(
            1 for node in self.nodes.values()
            if node.status == TaskStatus.COMPLETED
        )

    @property
    def pending_count(self) -> int:
        """Count of pending nodes."""
        return sum(
            1 for node in self.nodes.values()
            if node.status == TaskStatus.PENDING
        )


@dataclass
class RuntimeConfig:
    """Configuration for the async runtime."""
    default_timeout: float = 30.0     # Single task timeout in seconds
    max_retries: int = 2              # Max retry attempts
    retry_delay: float = 1.0          # Delay between retries
    max_concurrent: int = 10          # Max concurrent tasks


@dataclass
class ExecutionStats:
    """Statistics from DAG execution."""
    total_tasks: int
    completed: int
    failed: int
    skipped: int
    total_duration_ms: int
    parallel_efficiency: float = 0.0  # actual_time / serial_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "completed": self.completed,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_duration_ms": self.total_duration_ms,
            "parallel_efficiency": self.parallel_efficiency,
        }


@dataclass
class ExecutionResult:
    """Result of DAG execution."""
    dag_id: str
    status: Literal["success", "partial", "failed"]
    results: Dict[str, Any]
    errors: Dict[str, str]
    duration_ms: int
    stats: ExecutionStats

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dag_id": self.dag_id,
            "status": self.status,
            "results": self.results,
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "stats": self.stats.to_dict(),
        }


@dataclass
class Task:
    """A single executable task."""
    id: str
    type: TaskType
    skill: str
    params: dict
    result: Optional[Any] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert task to dictionary."""
        return {
            "id": self.id,
            "type": self.type.value,
            "skill": self.skill,
            "params": self.params,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class Plan:
    """Execution plan containing tasks or direct response."""
    mode: Literal["direct", "multi_step"]
    tasks: List[Task]
    direct_response: Optional[str] = None

    @classmethod
    def direct(cls, response: str) -> "Plan":
        """Create a direct response plan (no skill execution needed)."""
        return cls(mode="direct", tasks=[], direct_response=response)

    @classmethod
    def multi_step(cls, tasks: List[Task]) -> "Plan":
        """Create a multi-step plan with tasks to execute."""
        return cls(mode="multi_step", tasks=tasks)

    def is_direct(self) -> bool:
        """Check if this is a direct response plan."""
        return self.mode == "direct"


@dataclass
class NotebookResponse:
    """Response from the notebook agent.

    The primary response object returned by NotebookAgent.run() and run_stream().

    Attributes:
        text: Main text response to the user.
        error: Error message if execution failed.
        artifacts: List of structured artifacts produced during execution.
        suggestions: List of suggested follow-up actions/questions.
        dag: Optional TaskDAG reference for inspection.
        memory_stats: Optional memory usage statistics.
    """
    text: str
    error: Optional[str] = None
    artifacts: List[Artifact] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    dag: Optional["TaskDAG"] = None
    memory_stats: Optional[Dict[str, Any]] = None

    def is_error(self) -> bool:
        """Check if this response contains an error."""
        return self.error is not None

    def has_artifacts(self) -> bool:
        """Check if this response contains artifacts."""
        return len(self.artifacts) > 0

    def get_artifacts_by_type(self, artifact_type: ArtifactType) -> List[Artifact]:
        """Get artifacts filtered by type.

        Args:
            artifact_type: Type of artifacts to retrieve.

        Returns:
            List of matching artifacts.
        """
        return [a for a in self.artifacts if a.type == artifact_type]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize response to dictionary.

        Returns:
            Dictionary representation of the response.
        """
        return {
            "text": self.text,
            "error": self.error,
            "artifacts": [a.to_dict() for a in self.artifacts],
            "suggestions": self.suggestions,
            "dag": self.dag.to_dict() if self.dag else None,
            "memory_stats": self.memory_stats,
        }
