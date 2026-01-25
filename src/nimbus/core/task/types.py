"""Core types for Subagent DAG orchestration.

This module defines the data structures for task-level DAG orchestration:
- SubagentType: Enum of available subagent types
- SubagentNode: A node representing a subagent task in the DAG
- SubagentDAG: DAG of subagent tasks
- SubagentResult: Result from subagent execution
- SubagentExecutionResult: Result from DAG execution
- SubagentReplanRecord: Record of replan events

Example:
    >>> from nimbus.core.task.types import SubagentType, SubagentNode, SubagentDAG
    >>>
    >>> dag = SubagentDAG.create(
    ...     goal="Implement caching layer",
    ...     nodes=[
    ...         {"id": "t1", "type": "eye", "goal": "Explore code"},
    ...         {"id": "t2", "type": "body", "goal": "Implement", "depends_on": ["t1"]},
    ...     ]
    ... )
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class SubagentType(str, Enum):
    """Types of subagents with predefined capabilities.

    Each subagent type has a default set of allowed tools and
    is optimized for specific task categories.

    Attributes:
        EYE: Code exploration (Read, Glob, Grep).
        BODY: Code implementation (Read, Write, Edit, Bash, Glob, Grep).
        MIND: Architecture design (Read, Write, Glob, Grep).
        TONGUE: Testing (Read, Glob, Bash).
        NOSE: Code review (Read, Glob, Grep).
        EAR: Requirements analysis (Read).
    """

    EYE = "eye"
    BODY = "body"
    MIND = "mind"
    TONGUE = "tongue"
    NOSE = "nose"
    EAR = "ear"


# Default tool permissions for each subagent type
SUBAGENT_TOOLS: Dict[SubagentType, Set[str]] = {
    SubagentType.EYE: {"Read", "Glob", "Grep"},
    SubagentType.BODY: {"Read", "Write", "Edit", "Bash", "Glob", "Grep"},
    SubagentType.MIND: {"Read", "Write", "Glob", "Grep"},
    SubagentType.TONGUE: {"Read", "Glob", "Bash"},
    SubagentType.NOSE: {"Read", "Glob", "Grep"},
    SubagentType.EAR: {"Read"},
}


class SubagentStatus(str, Enum):
    """Status of a subagent node in the DAG."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class SubagentResult:
    """Result from subagent execution.

    Attributes:
        agent_id: Unique identifier for the subagent execution.
        summary: Concise summary of what was accomplished.
        result: Full result data (may be large).
        files_accessed: List of files read during execution.
        files_modified: List of files written/edited during execution.
        turn_count: Number of LLM turns used.
        duration_ms: Total execution time in milliseconds.
    """

    agent_id: str
    summary: str
    result: Optional[Any] = None
    files_accessed: List[str] = field(default_factory=list)
    files_modified: List[str] = field(default_factory=list)
    turn_count: int = 0
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "agent_id": self.agent_id,
            "summary": self.summary,
            "result": self.result,
            "files_accessed": self.files_accessed,
            "files_modified": self.files_modified,
            "turn_count": self.turn_count,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubagentResult":
        """Create from dictionary."""
        return cls(
            agent_id=data.get("agent_id", ""),
            summary=data.get("summary", ""),
            result=data.get("result"),
            files_accessed=data.get("files_accessed", []),
            files_modified=data.get("files_modified", []),
            turn_count=data.get("turn_count", 0),
            duration_ms=data.get("duration_ms", 0),
        )


@dataclass
class SubagentNode:
    """A node representing a subagent task in the DAG.

    Unlike TaskNode which has fixed params, SubagentNode has a goal
    that the subagent interprets dynamically.

    Attributes:
        id: Unique identifier for the node.
        subagent_type: Type of subagent (eye, body, mind, etc.).
        goal: Task description for the subagent.
        depends_on: List of node IDs this node depends on.
        status: Current execution status.
        result: Result from subagent execution.
        error: Error message if failed.
        started_at: When execution started.
        finished_at: When execution finished.
        allowed_tools: Optional tool override (default uses type's tools).
        model: Optional model override for this node.
        max_turns: Maximum agentic loop turns.
        timeout: Timeout in seconds.
        context_sources: IDs of nodes whose results to inject into context.
        on_failure: Fallback node ID to execute on failure.
        max_retries: Maximum retry attempts.
        retry_count: Current retry count.
        retry_strategy: Retry strategy ("same", "alternate", "escalate").
    """

    id: str
    subagent_type: SubagentType
    goal: str
    depends_on: List[str] = field(default_factory=list)

    # Execution state
    status: SubagentStatus = SubagentStatus.PENDING
    result: Optional[SubagentResult] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    # Configuration
    allowed_tools: Optional[Set[str]] = None
    model: Optional[str] = None
    max_turns: int = 50
    timeout: float = 300.0

    # Context management
    context_sources: List[str] = field(default_factory=list)

    # Failure handling
    on_failure: Optional[str] = None
    max_retries: int = 1
    retry_count: int = 0
    retry_strategy: str = "same"

    # Cached signature
    _signature: Optional[str] = field(default=None, repr=False)

    @property
    def duration_ms(self) -> Optional[int]:
        """Calculate execution duration in milliseconds."""
        if self.started_at and self.finished_at:
            delta = self.finished_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None

    def get_allowed_tools(self) -> Set[str]:
        """Get allowed tools, falling back to type default."""
        if self.allowed_tools is not None:
            return self.allowed_tools
        return SUBAGENT_TOOLS.get(self.subagent_type, set())

    def get_signature(self) -> str:
        """Get semantic signature for node comparison.

        The signature is a hash of (subagent_type, goal) that can be used
        to compare nodes across replans.

        Returns:
            A 16-character hex string signature.
        """
        if self._signature is None:
            content = json.dumps(
                {
                    "type": self.subagent_type.value,
                    "goal": self.goal,
                },
                sort_keys=True,
            )
            self._signature = hashlib.sha256(content.encode()).hexdigest()[:16]
        return self._signature

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "subagent_type": self.subagent_type.value,
            "goal": self.goal,
            "depends_on": self.depends_on,
            "status": self.status.value,
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self.duration_ms,
            "allowed_tools": list(self.allowed_tools) if self.allowed_tools else None,
            "model": self.model,
            "max_turns": self.max_turns,
            "timeout": self.timeout,
            "context_sources": self.context_sources,
            "on_failure": self.on_failure,
            "max_retries": self.max_retries,
            "retry_count": self.retry_count,
            "retry_strategy": self.retry_strategy,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubagentNode":
        """Create SubagentNode from dictionary."""
        # Parse subagent_type
        subagent_type = data.get("subagent_type", "eye")
        if isinstance(subagent_type, str):
            subagent_type = SubagentType(subagent_type)

        # Parse status
        status = data.get("status", "pending")
        if isinstance(status, str):
            status = SubagentStatus(status)

        # Parse timestamps
        started_at = data.get("started_at")
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at)

        finished_at = data.get("finished_at")
        if isinstance(finished_at, str):
            finished_at = datetime.fromisoformat(finished_at)

        # Parse result
        result = data.get("result")
        if result and isinstance(result, dict):
            result = SubagentResult.from_dict(result)

        # Parse allowed_tools
        allowed_tools = data.get("allowed_tools")
        if allowed_tools and isinstance(allowed_tools, list):
            allowed_tools = set(allowed_tools)

        return cls(
            id=data["id"],
            subagent_type=subagent_type,
            goal=data.get("goal", ""),
            depends_on=data.get("depends_on", []),
            status=status,
            result=result,
            error=data.get("error"),
            started_at=started_at,
            finished_at=finished_at,
            allowed_tools=allowed_tools,
            model=data.get("model"),
            max_turns=data.get("max_turns", 50),
            timeout=data.get("timeout", 300.0),
            context_sources=data.get("context_sources", []),
            on_failure=data.get("on_failure"),
            max_retries=data.get("max_retries", 1),
            retry_count=data.get("retry_count", 0),
            retry_strategy=data.get("retry_strategy", "same"),
        )


@dataclass
class SubagentReplanRecord:
    """Record of a replan event at the subagent level.

    Attributes:
        timestamp: When the replan occurred.
        trigger: What triggered the replan ("failure", "checkpoint", "manual").
        trigger_node_id: ID of the node that triggered the replan.
        old_node_count: Number of nodes before replan.
        new_node_count: Number of nodes after replan.
        nodes_cancelled: List of node IDs that were cancelled.
        nodes_added: List of node IDs that were added.
        reason: Human-readable reason for the replan.
    """

    timestamp: datetime
    trigger: str
    trigger_node_id: Optional[str]
    old_node_count: int
    new_node_count: int
    nodes_cancelled: List[str]
    nodes_added: List[str]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "trigger": self.trigger,
            "trigger_node_id": self.trigger_node_id,
            "old_node_count": self.old_node_count,
            "new_node_count": self.new_node_count,
            "nodes_cancelled": self.nodes_cancelled,
            "nodes_added": self.nodes_added,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubagentReplanRecord":
        """Create from dictionary."""
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)
        elif timestamp is None:
            timestamp = datetime.now()

        return cls(
            timestamp=timestamp,
            trigger=data.get("trigger", "unknown"),
            trigger_node_id=data.get("trigger_node_id"),
            old_node_count=data.get("old_node_count", 0),
            new_node_count=data.get("new_node_count", 0),
            nodes_cancelled=data.get("nodes_cancelled", []),
            nodes_added=data.get("nodes_added", []),
            reason=data.get("reason", ""),
        )


@dataclass
class SubagentDAG:
    """DAG of subagent tasks.

    Unlike TaskDAG which orchestrates tool calls, SubagentDAG
    orchestrates higher-level subagent tasks.

    Attributes:
        id: Unique identifier for the DAG.
        user_goal: Original user goal/request.
        nodes: Dictionary mapping node ID to SubagentNode.
        created_at: When the DAG was created.
        complexity: Estimated complexity ("simple", "moderate", "complex").
        estimated_duration: Estimated total duration in seconds.
        replan_history: History of replans applied to this DAG.
    """

    id: str
    user_goal: str
    nodes: Dict[str, SubagentNode]
    created_at: datetime = field(default_factory=datetime.now)

    # Metadata
    complexity: str = "moderate"
    estimated_duration: Optional[int] = None

    # Replan support
    replan_history: List[SubagentReplanRecord] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        goal: str,
        nodes: List[Dict[str, Any]],
        complexity: str = "moderate",
    ) -> "SubagentDAG":
        """Create SubagentDAG from node definitions.

        Args:
            goal: User's original goal.
            nodes: List of node dicts with id, type, goal, depends_on, etc.
            complexity: Estimated complexity level.

        Returns:
            SubagentDAG instance.
        """
        dag_id = f"sdag_{uuid.uuid4().hex[:8]}"
        node_dict: Dict[str, SubagentNode] = {}

        for node_data in nodes:
            # Parse subagent_type
            subagent_type = node_data.get("type", node_data.get("subagent_type", "eye"))
            if isinstance(subagent_type, str):
                subagent_type = SubagentType(subagent_type)

            # Parse allowed_tools
            allowed_tools = node_data.get("allowed_tools")
            if allowed_tools and isinstance(allowed_tools, list):
                allowed_tools = set(allowed_tools)

            node = SubagentNode(
                id=node_data["id"],
                subagent_type=subagent_type,
                goal=node_data.get("goal", ""),
                depends_on=node_data.get("depends_on", []),
                allowed_tools=allowed_tools,
                model=node_data.get("model"),
                max_turns=node_data.get("max_turns", 50),
                timeout=node_data.get("timeout", 300.0),
                context_sources=node_data.get("context_sources", node_data.get("depends_on", [])),
                on_failure=node_data.get("on_failure"),
                max_retries=node_data.get("max_retries", 1),
                retry_strategy=node_data.get("retry_strategy", "same"),
            )
            node_dict[node.id] = node

        return cls(
            id=dag_id,
            user_goal=goal,
            nodes=node_dict,
            complexity=complexity,
        )

    def get_ready_nodes(self) -> List[SubagentNode]:
        """Get nodes whose dependencies are satisfied.

        Returns:
            List of SubagentNode that can be executed now.
        """
        ready = []
        for node in self.nodes.values():
            if node.status != SubagentStatus.PENDING:
                continue

            # Check all dependencies are completed
            deps_satisfied = all(
                self.nodes[dep_id].status == SubagentStatus.COMPLETED
                for dep_id in node.depends_on
                if dep_id in self.nodes
            )

            if deps_satisfied:
                ready.append(node)

        return ready

    def get_context_for_node(self, node_id: str) -> str:
        """Build context from completed dependency results.

        Args:
            node_id: ID of the node to build context for.

        Returns:
            Formatted context string with dependency results.
        """
        node = self.nodes.get(node_id)
        if node is None:
            return ""

        context_parts = []

        for source_id in node.context_sources:
            source_node = self.nodes.get(source_id)
            if source_node and source_node.result:
                context_parts.append(
                    f"## Result from {source_node.subagent_type.value} ({source_id})\n"
                    f"{source_node.result.summary}"
                )

        return "\n\n".join(context_parts)

    def is_completed(self) -> bool:
        """Check if all nodes are in terminal state."""
        return all(
            node.status
            in (SubagentStatus.COMPLETED, SubagentStatus.FAILED, SubagentStatus.SKIPPED)
            for node in self.nodes.values()
        )

    def get_results(self) -> Dict[str, SubagentResult]:
        """Collect results from all completed nodes."""
        return {
            node_id: node.result
            for node_id, node in self.nodes.items()
            if node.status == SubagentStatus.COMPLETED and node.result is not None
        }

    def get_errors(self) -> Dict[str, str]:
        """Collect errors from all failed nodes."""
        return {
            node_id: node.error or "Unknown error"
            for node_id, node in self.nodes.items()
            if node.status == SubagentStatus.FAILED
        }

    def get_downstream_nodes(self, node_id: str) -> List[SubagentNode]:
        """Get all nodes that depend on the given node."""
        return [node for node in self.nodes.values() if node_id in node.depends_on]

    def mark_downstream_skipped(self, failed_node_id: str) -> None:
        """Mark all downstream nodes as SKIPPED due to upstream failure."""
        to_skip = [failed_node_id]
        visited: Set[str] = set()

        while to_skip:
            current = to_skip.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for node in self.get_downstream_nodes(current):
                if node.status == SubagentStatus.PENDING:
                    node.status = SubagentStatus.SKIPPED
                    node.error = f"Skipped: upstream node '{current}' failed"
                    to_skip.append(node.id)

    @property
    def completed_count(self) -> int:
        """Count of completed nodes."""
        return sum(
            1 for node in self.nodes.values() if node.status == SubagentStatus.COMPLETED
        )

    @property
    def pending_count(self) -> int:
        """Count of pending nodes."""
        return sum(
            1 for node in self.nodes.values() if node.status == SubagentStatus.PENDING
        )

    @property
    def failed_count(self) -> int:
        """Count of failed nodes."""
        return sum(
            1 for node in self.nodes.values() if node.status == SubagentStatus.FAILED
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "user_goal": self.user_goal,
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "created_at": self.created_at.isoformat(),
            "complexity": self.complexity,
            "estimated_duration": self.estimated_duration,
            "replan_history": [r.to_dict() for r in self.replan_history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SubagentDAG":
        """Create SubagentDAG from dictionary."""
        # Parse created_at
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now()

        # Parse nodes
        nodes: Dict[str, SubagentNode] = {}
        for node_id, node_data in data.get("nodes", {}).items():
            nodes[node_id] = SubagentNode.from_dict(node_data)

        # Parse replan history
        replan_history = [
            SubagentReplanRecord.from_dict(r) for r in data.get("replan_history", [])
        ]

        return cls(
            id=data["id"],
            user_goal=data.get("user_goal", ""),
            nodes=nodes,
            created_at=created_at,
            complexity=data.get("complexity", "moderate"),
            estimated_duration=data.get("estimated_duration"),
            replan_history=replan_history,
        )


@dataclass
class SubagentExecutionStats:
    """Statistics from SubagentDAG execution."""

    total_nodes: int
    completed: int
    failed: int
    skipped: int
    total_duration_ms: int
    total_turns: int = 0
    parallel_efficiency: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_nodes": self.total_nodes,
            "completed": self.completed,
            "failed": self.failed,
            "skipped": self.skipped,
            "total_duration_ms": self.total_duration_ms,
            "total_turns": self.total_turns,
            "parallel_efficiency": self.parallel_efficiency,
        }


@dataclass
class SubagentExecutionResult:
    """Result from SubagentDAG execution.

    Attributes:
        dag_id: ID of the executed DAG.
        status: Overall execution status ("success", "partial", "failed").
        results: Dictionary mapping node ID to SubagentResult.
        errors: Dictionary mapping node ID to error message.
        duration_ms: Total execution time in milliseconds.
        stats: Execution statistics.
        final_summary: Synthesized summary of all results.
    """

    dag_id: str
    status: str  # "success", "partial", "failed"
    results: Dict[str, SubagentResult]
    errors: Dict[str, str]
    duration_ms: int
    stats: SubagentExecutionStats
    final_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dag_id": self.dag_id,
            "status": self.status,
            "results": {k: v.to_dict() for k, v in self.results.items()},
            "errors": self.errors,
            "duration_ms": self.duration_ms,
            "stats": self.stats.to_dict(),
            "final_summary": self.final_summary,
        }
