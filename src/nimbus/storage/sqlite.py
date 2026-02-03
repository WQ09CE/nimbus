"""SQLite-based persistent storage for Nimbus sessions.

This module implements the storage layer using SQLite with async support via aiosqlite.
It provides CRUD operations for sessions, messages, DAGs, memory checkpoints, and permissions.
"""

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import aiosqlite

from ..core.memory_legacy import MemoryConfig, Message, PinnedItem, TieredMemoryManager
from ..core.persistence import SessionCheckpointModel
from ..core.types import TaskDAG, TaskStatus


class SQLiteStorage:
    """SQLite-based persistent storage for Nimbus sessions.

    This class provides async methods for:
    - Session CRUD operations
    - Message storage and retrieval
    - DAG state persistence
    - Memory checkpoint serialization/deserialization
    - Permission rule and request management

    The database is automatically initialized with the schema on first use.

    Example:
        storage = SQLiteStorage(".nimbus/nimbus.db")
        await storage.initialize()

        # Create a session
        session = await storage.create_session(
            session_id="sess_abc123",
            name="my-session",
            workspace_path="/path/to/workspace"
        )

        # Add a message
        await storage.add_message(
            message_id="msg_001",
            session_id="sess_abc123",
            role="user",
            content="Hello, world!"
        )
    """

    def __init__(self, db_path: str = ".nimbus/nimbus.db"):
        """Initialize SQLite storage.

        Args:
            db_path: Path to the SQLite database file. Parent directories
                     will be created automatically if they don't exist.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Initialize database with schema.

        Creates all tables and indices defined in schema.sql.
        This method is idempotent - it can be called multiple times safely.
        """
        async with self._get_connection() as db:
            schema_path = Path(__file__).parent / "schema.sql"
            schema = schema_path.read_text()
            await db.executescript(schema)
            await db.commit()

    @asynccontextmanager
    async def _get_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get database connection with row factory.

        This context manager ensures proper connection handling and
        sets up the row factory for dictionary-style access.

        Yields:
            aiosqlite.Connection: Database connection with row factory.
        """
        if self._connection is None:
            self._connection = await aiosqlite.connect(str(self.db_path))
            self._connection.row_factory = aiosqlite.Row
            # Enable foreign keys
            await self._connection.execute("PRAGMA foreign_keys = ON")
        yield self._connection

    async def close(self) -> None:
        """Close database connection.

        Should be called when the storage is no longer needed.
        """
        if self._connection:
            await self._connection.close()
            self._connection = None

    # =========================================================================
    # Session Operations
    # =========================================================================

    async def create_session(
        self,
        session_id: str,
        name: Optional[str] = None,
        workspace_path: Optional[str] = None,
        memory_type: str = "tiered",
        planner_type: str = "dag",
        config_overrides: Optional[Dict[str, Any]] = None,
        model_config: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create a new session.

        Args:
            session_id: Unique session identifier.
            name: Optional human-readable name for the session.
            workspace_path: Optional path to the workspace directory.
            memory_type: Memory manager type ("simple" or "tiered").
            planner_type: Planner type ("simple" or "dag").
            config_overrides: Optional configuration overrides as JSON.
            model_config: Optional model configuration as JSON.

        Returns:
            Dictionary with created session data.

        Raises:
            aiosqlite.IntegrityError: If session_id already exists.
        """
        async with self._get_connection() as db:
            config_json = json.dumps(config_overrides) if config_overrides else None
            json.dumps(model_config) if model_config else None

            # We need to add model_config column if it doesn't exist
            # But for now let's store it in config_overrides if schema not updated
            # Or assume schema update. Let's check schema.sql or just use config_overrides

            # Actually, let's merge it into config_overrides to avoid schema change for now
            if model_config:
                if not config_overrides:
                    config_overrides = {}
                config_overrides["model_config"] = model_config
                config_json = json.dumps(config_overrides)

            await db.execute(
                """
                INSERT INTO sessions (id, name, workspace_path, memory_type, planner_type, config_overrides)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, name, workspace_path, memory_type, planner_type, config_json),
            )
            await db.commit()

            cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()

            # Unpack model_config from config_overrides for caller convenience
            result = self._row_to_dict(row)
            if result.get("config_overrides"):
                overrides = json.loads(result["config_overrides"])
                if "model_config" in overrides:
                    result["model_config"] = overrides["model_config"]

            return result

    async def get_session(
        self, session_id: str, include_deleted: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Get session by ID.

        Args:
            session_id: Session identifier.
            include_deleted: If True, also return deleted sessions.

        Returns:
            Session data dictionary, or None if not found.
        """
        async with self._get_connection() as db:
            if include_deleted:
                cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            else:
                cursor = await db.execute(
                    "SELECT * FROM sessions WHERE id = ? AND status != 'deleted'", (session_id,)
                )
            row = await cursor.fetchone()
            if row:
                result = self._row_to_dict(row)
                # Parse JSON fields
                if result.get("config_overrides"):
                    result["config_overrides"] = json.loads(result["config_overrides"])
                    # Extract model_config for convenience
                    if "model_config" in result["config_overrides"]:
                        result["model_config"] = result["config_overrides"]["model_config"]

                if result.get("memory_state"):
                    result["memory_state"] = json.loads(result["memory_state"])
                return result
            return None

    async def list_sessions(
        self,
        status: str = "active",
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """List sessions with pagination.

        Args:
            status: Filter by session status ("active", "archived", "deleted").
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.

        Returns:
            Tuple of (list of session dictionaries, total count).
        """
        async with self._get_connection() as db:
            # Get total count
            cursor = await db.execute("SELECT COUNT(*) FROM sessions WHERE status = ?", (status,))
            row = await cursor.fetchone()
            total = row[0] if row else 0

            # Get paginated results with message stats
            cursor = await db.execute(
                """
                SELECT s.*,
                    (SELECT MAX(created_at) FROM messages WHERE session_id = s.id) as last_message_at,
                    (SELECT COUNT(*) FROM messages WHERE session_id = s.id) as message_count
                FROM sessions s
                WHERE s.status = ?
                ORDER BY s.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (status, limit, offset),
            )
            rows = await cursor.fetchall()
            sessions = [self._row_to_dict(row) for row in rows]

            return sessions, total

    async def update_session(self, session_id: str, **kwargs: Any) -> None:
        """Update session fields.

        Args:
            session_id: Session identifier.
            **kwargs: Fields to update. Special handling for:
                - config_overrides: Will be JSON-serialized
                - memory_state: Will be JSON-serialized
        """
        if not kwargs:
            return

        # Handle JSON fields
        if "config_overrides" in kwargs and kwargs["config_overrides"] is not None:
            kwargs["config_overrides"] = json.dumps(kwargs["config_overrides"])
        if "memory_state" in kwargs and kwargs["memory_state"] is not None:
            kwargs["memory_state"] = json.dumps(kwargs["memory_state"])

        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [session_id]

        async with self._get_connection() as db:
            await db.execute(
                f"UPDATE sessions SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )
            await db.commit()

    async def delete_session(self, session_id: str, hard_delete: bool = False) -> None:
        """Delete session.

        Args:
            session_id: Session identifier.
            hard_delete: If True, permanently delete. If False, soft delete
                        by setting status to "deleted".
        """
        if hard_delete:
            async with self._get_connection() as db:
                await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                await db.commit()
        else:
            await self.update_session(session_id, status="deleted")

    async def archive_session(self, session_id: str) -> None:
        """Archive a session.

        Args:
            session_id: Session identifier.
        """
        await self.update_session(
            session_id, status="archived", archived_at=datetime.now().isoformat()
        )

    # =========================================================================
    # Message Operations
    # =========================================================================

    async def add_message(
        self,
        message_id: str,
        session_id: str,
        role: str,
        content: str,
        dag_id: Optional[str] = None,
        artifacts: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Add a message to session.

        Args:
            message_id: Unique message identifier.
            session_id: Session to add message to.
            role: Message role ("user", "assistant", "system").
            content: Message content.
            dag_id: Optional DAG ID for assistant messages.
            artifacts: Optional list of artifact dictionaries.

        Returns:
            Dictionary with created message data.
        """
        async with self._get_connection() as db:
            artifacts_json = json.dumps(artifacts) if artifacts else None

            await db.execute(
                """
                INSERT INTO messages (id, session_id, role, content, dag_id, artifacts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, dag_id, artifacts_json),
            )
            await db.commit()

            cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
            row = await cursor.fetchone()
            result = self._row_to_dict(row)
            if result.get("artifacts"):
                result["artifacts"] = json.loads(result["artifacts"])
            return result

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        order: str = "ASC",
    ) -> List[Dict[str, Any]]:
        """Get messages for session.

        Args:
            session_id: Session identifier.
            limit: Maximum number of messages to return.
            offset: Number of messages to skip.
            order: Sort order ("ASC" or "DESC").

        Returns:
            List of message dictionaries.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                f"""
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY created_at {order}
                LIMIT ? OFFSET ?
                """,
                (session_id, limit, offset),
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                r = self._row_to_dict(row)
                if r.get("artifacts"):
                    r["artifacts"] = json.loads(r["artifacts"])
                results.append(r)
            return results

    async def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get a single message by ID.

        Args:
            message_id: Message identifier.

        Returns:
            Message dictionary, or None if not found.
        """
        async with self._get_connection() as db:
            cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
            row = await cursor.fetchone()
            if row:
                result = self._row_to_dict(row)
                if result.get("artifacts"):
                    result["artifacts"] = json.loads(result["artifacts"])
                return result
            return None

    async def delete_message(self, message_id: str) -> None:
        """Delete a message.

        Args:
            message_id: Message identifier.
        """
        async with self._get_connection() as db:
            await db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            await db.commit()

    async def get_message_count(self, session_id: str) -> int:
        """Get message count for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Number of messages in the session.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    # =========================================================================
    # DAG Operations
    # =========================================================================

    async def save_dag(self, session_id: str, dag: TaskDAG) -> None:
        """Save or update DAG state.

        Args:
            session_id: Session identifier.
            dag: TaskDAG instance to save.
        """
        # Calculate statistics
        total = len(dag.nodes)
        completed = sum(1 for n in dag.nodes.values() if n.status == TaskStatus.COMPLETED)
        failed = sum(1 for n in dag.nodes.values() if n.status == TaskStatus.FAILED)

        # Determine status
        if dag.is_completed():
            status = "completed" if failed == 0 else "failed"
        elif any(n.status == TaskStatus.RUNNING for n in dag.nodes.values()):
            status = "running"
        else:
            status = "pending"

        # Calculate duration if completed
        duration_ms = None
        if dag.is_completed():
            started_times = [n.started_at for n in dag.nodes.values() if n.started_at is not None]
            finished_times = [
                n.finished_at for n in dag.nodes.values() if n.finished_at is not None
            ]
            if started_times and finished_times:
                start = min(started_times)
                end = max(finished_times)
                duration_ms = int((end - start).total_seconds() * 1000)

        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO dags
                (id, session_id, goal, status, state, total_tasks, completed_tasks, failed_tasks, duration_ms, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dag.id,
                    session_id,
                    dag.goal,
                    status,
                    json.dumps(dag.to_dict()),
                    total,
                    completed,
                    failed,
                    duration_ms,
                    datetime.now().isoformat() if dag.is_completed() else None,
                ),
            )
            await db.commit()

    async def get_dag(self, dag_id: str) -> Optional[TaskDAG]:
        """Load DAG by ID.

        Args:
            dag_id: DAG identifier.

        Returns:
            TaskDAG instance, or None if not found.
        """
        async with self._get_connection() as db:
            cursor = await db.execute("SELECT state FROM dags WHERE id = ?", (dag_id,))
            row = await cursor.fetchone()
            if row:
                return TaskDAG.from_dict(json.loads(row["state"]))
            return None

    async def get_dag_info(self, dag_id: str) -> Optional[Dict[str, Any]]:
        """Get DAG metadata without full state.

        Args:
            dag_id: DAG identifier.

        Returns:
            Dictionary with DAG metadata, or None if not found.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                """
                SELECT id, session_id, goal, status, total_tasks, completed_tasks,
                       failed_tasks, duration_ms, created_at, completed_at
                FROM dags WHERE id = ?
                """,
                (dag_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_dict(row) if row else None

    async def list_dags(
        self,
        session_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List DAGs for a session.

        Args:
            session_id: Session identifier.
            limit: Maximum number of DAGs to return.
            offset: Number of DAGs to skip.

        Returns:
            List of DAG metadata dictionaries.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                """
                SELECT id, session_id, goal, status, total_tasks, completed_tasks,
                       failed_tasks, duration_ms, created_at, completed_at
                FROM dags
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (session_id, limit, offset),
            )
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    async def delete_dag(self, dag_id: str) -> None:
        """Delete a DAG.

        Args:
            dag_id: DAG identifier.
        """
        async with self._get_connection() as db:
            await db.execute("DELETE FROM dags WHERE id = ?", (dag_id,))
            await db.commit()

    # =========================================================================
    # Memory Checkpoint Operations
    # =========================================================================

    async def save_memory_checkpoint(
        self,
        session_id: str,
        memory: TieredMemoryManager,
    ) -> str:
        """Save memory checkpoint.

        Serializes the entire TieredMemoryManager state to the database.

        Args:
            session_id: Session identifier.
            memory: TieredMemoryManager instance to save.

        Returns:
            Checkpoint ID.
        """
        async with self._get_connection() as db:
            # Get next checkpoint number
            cursor = await db.execute(
                "SELECT MAX(checkpoint_num) FROM memory_checkpoints WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            checkpoint_num = (row[0] or 0) + 1

            checkpoint_id = f"ckpt_{session_id}_{checkpoint_num}"

            # Serialize pinned items
            pinned_data = [
                {
                    "id": p.id,
                    "type": p.type,
                    "content": p.content,
                    "priority": p.priority,
                    "created_at": p.created_at.isoformat(),
                    "description": p.description,
                    "read_only": p.read_only,
                }
                for p in memory.pinned
            ]

            # Serialize episodic messages
            episodic_data = [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp.isoformat(),
                }
                for m in memory.episodic
            ]

            await db.execute(
                """
                INSERT INTO memory_checkpoints
                (id, session_id, checkpoint_num, pinned, working, episodic, summaries,
                 semantic_cache, turn_count, compression_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_id,
                    session_id,
                    checkpoint_num,
                    json.dumps(pinned_data),
                    json.dumps(memory.working),
                    json.dumps(episodic_data),
                    json.dumps(memory.episodic_summaries),
                    json.dumps(memory.semantic_cache),
                    memory._turn_count,
                    memory._compression_count,
                ),
            )
            await db.commit()

            return checkpoint_id

    async def load_memory_checkpoint(
        self,
        session_id: str,
        config: Optional[MemoryConfig] = None,
        checkpoint_num: Optional[int] = None,
    ) -> Optional[TieredMemoryManager]:
        """Load memory checkpoint.

        Restores a TieredMemoryManager from a saved checkpoint.

        Args:
            session_id: Session identifier.
            config: Optional MemoryConfig for the restored manager.
            checkpoint_num: Specific checkpoint number to load. If None,
                           loads the latest checkpoint.

        Returns:
            TieredMemoryManager instance, or None if no checkpoint found.
        """
        async with self._get_connection() as db:
            if checkpoint_num is not None:
                cursor = await db.execute(
                    """
                    SELECT * FROM memory_checkpoints
                    WHERE session_id = ? AND checkpoint_num = ?
                    """,
                    (session_id, checkpoint_num),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT * FROM memory_checkpoints
                    WHERE session_id = ?
                    ORDER BY checkpoint_num DESC
                    LIMIT 1
                    """,
                    (session_id,),
                )

            row = await cursor.fetchone()

            if not row:
                return None

            row_dict = self._row_to_dict(row)

            # Create new memory manager
            memory = TieredMemoryManager(
                config=config or MemoryConfig(),
                session_id=session_id,
            )

            # Restore pinned items
            pinned_data = json.loads(row_dict["pinned"]) if row_dict["pinned"] else []
            for p in pinned_data:
                created_at = datetime.fromisoformat(p["created_at"])
                memory.pinned.append(
                    PinnedItem(
                        id=p["id"],
                        type=p["type"],
                        content=p["content"],
                        priority=p.get("priority", 0),
                        created_at=created_at,
                        description=p.get("description", ""),
                        read_only=p.get("read_only", False),
                    )
                )

            # Restore working memory
            memory.working = json.loads(row_dict["working"]) if row_dict["working"] else {}

            # Restore episodic memory
            episodic_data = json.loads(row_dict["episodic"]) if row_dict["episodic"] else []
            for m in episodic_data:
                timestamp = datetime.fromisoformat(m["timestamp"])
                memory.episodic.append(
                    Message(
                        role=m["role"],
                        content=m["content"],
                        timestamp=timestamp,
                    )
                )

            # Restore summaries and semantic cache
            memory.episodic_summaries = (
                json.loads(row_dict["summaries"]) if row_dict["summaries"] else []
            )
            memory.semantic_cache = (
                json.loads(row_dict["semantic_cache"]) if row_dict["semantic_cache"] else {}
            )

            # Restore counters
            memory._turn_count = row_dict["turn_count"]
            memory._compression_count = row_dict["compression_count"]

            return memory

    async def list_memory_checkpoints(
        self,
        session_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """List memory checkpoints for a session.

        Args:
            session_id: Session identifier.
            limit: Maximum number of checkpoints to return.

        Returns:
            List of checkpoint metadata dictionaries.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                """
                SELECT id, checkpoint_num, turn_count, compression_count, created_at
                FROM memory_checkpoints
                WHERE session_id = ?
                ORDER BY checkpoint_num DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    async def delete_memory_checkpoint(
        self,
        checkpoint_id: str,
    ) -> None:
        """Delete a memory checkpoint.

        Args:
            checkpoint_id: Checkpoint identifier.
        """
        async with self._get_connection() as db:
            await db.execute("DELETE FROM memory_checkpoints WHERE id = ?", (checkpoint_id,))
            await db.commit()

    async def prune_old_checkpoints(
        self,
        session_id: str,
        keep_count: int = 5,
    ) -> int:
        """Delete old checkpoints, keeping only the most recent ones.

        Args:
            session_id: Session identifier.
            keep_count: Number of checkpoints to keep.

        Returns:
            Number of checkpoints deleted.
        """
        async with self._get_connection() as db:
            # Get checkpoints to delete
            cursor = await db.execute(
                """
                SELECT id FROM memory_checkpoints
                WHERE session_id = ?
                ORDER BY checkpoint_num DESC
                LIMIT -1 OFFSET ?
                """,
                (session_id, keep_count),
            )
            rows = await cursor.fetchall()
            ids_to_delete = [row["id"] for row in rows]

            if ids_to_delete:
                placeholders = ",".join("?" * len(ids_to_delete))
                await db.execute(
                    f"DELETE FROM memory_checkpoints WHERE id IN ({placeholders})",
                    ids_to_delete,
                )
                await db.commit()

            return len(ids_to_delete)

    # =========================================================================
    # Session Checkpoint Operations (v2)
    # =========================================================================

    async def save_session_checkpoint(self, checkpoint: SessionCheckpointModel) -> str:
        """Save a full session checkpoint (vCPU + MMU).

        Args:
            checkpoint: The SessionCheckpointModel to save

        Returns:
            Checkpoint ID (generated as uuid if not present)
        """
        import uuid
        if not hasattr(checkpoint, "checkpoint_id") or not checkpoint.checkpoint_id:
             str(uuid.uuid4())
             # Hack: Model doesn't have checkpoint_id field but table does?
             # Ah, SessionCheckpointModel from persistence.py doesn't seem to have 'checkpoint_id' field defined in the previous edit?
             # Let's check persistence.py again.
             pass

        # Checking SessionCheckpointModel definition in previous turn:
        # class SessionCheckpointModel(BaseModel):
        #     schema_version: int = 1
        #     session_id: str
        #     timestamp: float = Field(default_factory=time.time)
        #     step_index: int
        #     execution_state: ExecutionStateModel
        #     memory_snapshot: MemorySnapshotModel
        #     reason: str = "periodic"
        #     can_resume: bool = True

        # It is missing 'id' or 'checkpoint_id'. The table has 'id' (pk) and 'checkpoint_id'.
        # We should use a unique ID for the PK, and maybe reuse it for checkpoint_id column
        # or add it to the model.
        # For now, let's generate an ID here.

        pk_id = f"ckpt_{checkpoint.session_id}_{checkpoint.step_index}_{int(checkpoint.timestamp)}"

        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT INTO session_checkpoints (
                    id, session_id, checkpoint_id, timestamp, step_index,
                    execution_state, memory_snapshot, reason, can_resume, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pk_id,
                    checkpoint.session_id,
                    pk_id, # Use same ID for now
                    checkpoint.timestamp,
                    checkpoint.step_index,
                    checkpoint.execution_state.model_dump_json(),
                    checkpoint.memory_snapshot.model_dump_json(),
                    checkpoint.reason,
                    checkpoint.can_resume,
                    checkpoint.schema_version
                )
            )
            await db.commit()
        return pk_id

    async def load_latest_session_checkpoint(self, session_id: str) -> Optional[SessionCheckpointModel]:
        """Load the most recent checkpoint for a session."""
        async with self._get_connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM session_checkpoints
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (session_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None

            r = self._row_to_dict(row)

            # Reconstruct model
            # Note: We need to parse the JSON fields first
            return SessionCheckpointModel(
                schema_version=r["schema_version"],
                session_id=r["session_id"],
                timestamp=r["timestamp"],
                step_index=r["step_index"],
                execution_state=json.loads(r["execution_state"]),
                memory_snapshot=json.loads(r["memory_snapshot"]),
                reason=r["reason"],
                can_resume=bool(r["can_resume"])
            )

    # =========================================================================
    # Permission Operations
    # =========================================================================

    async def get_permission_rule(self, tool: str) -> Optional[str]:
        """Get permission decision for tool.

        Args:
            tool: Tool name.

        Returns:
            Permission decision string, or None if not set.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT decision FROM permission_rules WHERE tool = ?", (tool,)
            )
            row = await cursor.fetchone()
            return row["decision"] if row else None

    async def set_permission_rule(self, tool: str, decision: str) -> None:
        """Set permission rule for tool.

        Args:
            tool: Tool name.
            decision: Permission decision ("ask", "allow_always", "deny").
        """
        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO permission_rules (tool, decision, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (tool, decision),
            )
            await db.commit()

    async def delete_permission_rule(self, tool: str) -> None:
        """Delete permission rule for tool.

        Args:
            tool: Tool name.
        """
        async with self._get_connection() as db:
            await db.execute("DELETE FROM permission_rules WHERE tool = ?", (tool,))
            await db.commit()

    async def get_all_permission_rules(self) -> List[Dict[str, str]]:
        """Get all permission rules.

        Returns:
            List of dictionaries with "tool" and "decision" keys.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT tool, decision, updated_at FROM permission_rules ORDER BY tool"
            )
            rows = await cursor.fetchall()
            return [self._row_to_dict(row) for row in rows]

    async def create_permission_request(
        self,
        request_id: str,
        session_id: str,
        tool: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create pending permission request.

        Args:
            request_id: Unique request identifier.
            session_id: Session identifier.
            tool: Tool name.
            args: Tool arguments.

        Returns:
            Dictionary with created request data.
        """
        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT INTO permission_requests (id, session_id, tool, args)
                VALUES (?, ?, ?, ?)
                """,
                (request_id, session_id, tool, json.dumps(args)),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM permission_requests WHERE id = ?", (request_id,)
            )
            row = await cursor.fetchone()
            result = self._row_to_dict(row)
            result["args"] = json.loads(result["args"])
            return result

    async def get_permission_request(
        self,
        request_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a permission request by ID.

        Args:
            request_id: Request identifier.

        Returns:
            Request dictionary, or None if not found.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT * FROM permission_requests WHERE id = ?", (request_id,)
            )
            row = await cursor.fetchone()
            if row:
                result = self._row_to_dict(row)
                result["args"] = json.loads(result["args"])
                return result
            return None

    async def resolve_permission_request(
        self,
        request_id: str,
        decision: str,
    ) -> Optional[Dict[str, Any]]:
        """Resolve a pending permission request.

        Args:
            request_id: Request identifier.
            decision: Permission decision ("allow_once", "allow_always", "deny").

        Returns:
            Updated request dictionary, or None if not found.
        """
        async with self._get_connection() as db:
            await db.execute(
                """
                UPDATE permission_requests
                SET decision = ?, resolved_at = CURRENT_TIMESTAMP
                WHERE id = ? AND resolved_at IS NULL
                """,
                (decision, request_id),
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM permission_requests WHERE id = ?", (request_id,)
            )
            row = await cursor.fetchone()
            if row:
                result = self._row_to_dict(row)
                result["args"] = json.loads(result["args"])
                return result
            return None

    async def get_pending_permission_requests(
        self,
        session_id: str,
    ) -> List[Dict[str, Any]]:
        """Get all pending (unresolved) permission requests for a session.

        Args:
            session_id: Session identifier.

        Returns:
            List of pending request dictionaries.
        """
        async with self._get_connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM permission_requests
                WHERE session_id = ? AND resolved_at IS NULL
                ORDER BY created_at ASC
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                r = self._row_to_dict(row)
                r["args"] = json.loads(r["args"])
                results.append(r)
            return results

    # =========================================================================
    # Key-Value Store Operations
    # =========================================================================

    async def kv_get(self, key: str) -> Optional[str]:
        """Get value from key-value store.

        Args:
            key: Key name.

        Returns:
            Value string, or None if not found.
        """
        async with self._get_connection() as db:
            cursor = await db.execute("SELECT value FROM kv_store WHERE key = ?", (key,))
            row = await cursor.fetchone()
            return row["value"] if row else None

    async def kv_set(self, key: str, value: str) -> None:
        """Set value in key-value store.

        Args:
            key: Key name.
            value: Value string.
        """
        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO kv_store (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (key, value),
            )
            await db.commit()

    async def kv_delete(self, key: str) -> None:
        """Delete key from key-value store.

        Args:
            key: Key name.
        """
        async with self._get_connection() as db:
            await db.execute("DELETE FROM kv_store WHERE key = ?", (key,))
            await db.commit()

    async def kv_get_json(self, key: str) -> Optional[Any]:
        """Get JSON value from key-value store.

        Args:
            key: Key name.

        Returns:
            Deserialized JSON value, or None if not found.
        """
        value = await self.kv_get(key)
        if value is not None:
            return json.loads(value)
        return None

    async def kv_set_json(self, key: str, value: Any) -> None:
        """Set JSON value in key-value store.

        Args:
            key: Key name.
            value: Value to serialize as JSON.
        """
        await self.kv_set(key, json.dumps(value))

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def _row_to_dict(self, row: Optional[aiosqlite.Row]) -> Dict[str, Any]:
        """Convert SQLite row to dictionary.

        Args:
            row: SQLite row object.

        Returns:
            Dictionary with column names as keys.
        """
        if row is None:
            return {}
        return dict(row)

    async def vacuum(self) -> None:
        """Vacuum the database to reclaim space.

        Should be called periodically after deleting large amounts of data.
        """
        async with self._get_connection() as db:
            await db.execute("VACUUM")

    async def get_database_stats(self) -> Dict[str, Any]:
        """Get database statistics.

        Returns:
            Dictionary with table counts and database size.
        """
        async with self._get_connection() as db:
            stats = {}

            # Get table counts
            tables = [
                "sessions",
                "messages",
                "dags",
                "memory_checkpoints",
                "permission_rules",
                "permission_requests",
            ]
            for table in tables:
                cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
                row = await cursor.fetchone()
                stats[f"{table}_count"] = row[0] if row else 0

            # Get database size
            stats["database_size_bytes"] = self.db_path.stat().st_size

            return stats
