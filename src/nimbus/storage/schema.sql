-- Nimbus SQLite Schema
-- Version: 1.0
-- Date: 2026-01-23

-- =============================================================================
-- Sessions
-- =============================================================================

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    name TEXT,
    workspace_path TEXT,
    memory_type TEXT NOT NULL DEFAULT 'tiered',
    planner_type TEXT NOT NULL DEFAULT 'dag',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    archived_at TIMESTAMP,

    -- Memory state (serialized JSON)
    memory_state TEXT,

    -- Config overrides (serialized JSON)
    config_overrides TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC);


-- =============================================================================
-- Messages
-- =============================================================================

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- user | assistant | system
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- For assistant messages
    dag_id TEXT,
    artifacts TEXT,  -- JSON array of artifacts

    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at DESC);


-- =============================================================================
-- DAGs (Task Execution History)
-- =============================================================================

CREATE TABLE IF NOT EXISTS dags (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,

    -- Full DAG state (serialized JSON)
    state TEXT NOT NULL,

    -- Execution statistics
    total_tasks INTEGER NOT NULL DEFAULT 0,
    completed_tasks INTEGER NOT NULL DEFAULT 0,
    failed_tasks INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,

    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dags_session_id ON dags(session_id);
CREATE INDEX IF NOT EXISTS idx_dags_status ON dags(status);


-- =============================================================================
-- Memory Checkpoints (TieredMemory Snapshots)
-- =============================================================================

CREATE TABLE IF NOT EXISTS memory_checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    checkpoint_num INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Tiered memory state (serialized JSON)
    pinned TEXT,        -- JSON array of PinnedItem
    working TEXT,       -- JSON object
    episodic TEXT,      -- JSON array of Message
    summaries TEXT,     -- JSON array of strings
    semantic_cache TEXT, -- JSON object

    -- Statistics
    turn_count INTEGER NOT NULL DEFAULT 0,
    compression_count INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE(session_id, checkpoint_num)
);

CREATE INDEX IF NOT EXISTS idx_memory_checkpoints_session ON memory_checkpoints(session_id, checkpoint_num DESC);


-- =============================================================================
-- Permission Rules
-- =============================================================================

CREATE TABLE IF NOT EXISTS permission_rules (
    tool TEXT PRIMARY KEY,
    decision TEXT NOT NULL DEFAULT 'ask',  -- ask | allow_always | deny
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);


-- =============================================================================
-- Permission Requests (Pending)
-- =============================================================================

CREATE TABLE IF NOT EXISTS permission_requests (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    args TEXT NOT NULL,  -- JSON
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP,
    decision TEXT,  -- allow_once | allow_always | deny

    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_permission_requests_pending ON permission_requests(session_id)
    WHERE resolved_at IS NULL;


-- =============================================================================
-- Key-Value Store (For misc config/state)
-- =============================================================================

CREATE TABLE IF NOT EXISTS kv_store (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
