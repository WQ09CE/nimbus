# Nimbus-OpenWork Integration Design

> **Status**: Proposed
> **Author**: Architect Avatar
> **Date**: 2026-01-23
> **Version**: 1.0

---

## Summary

设计 Nimbus Agent 框架与 OpenWork 生态的集成架构。核心策略是**保留 Nimbus 优势的同时借鉴 OpenCode 的优秀设计**：
1. 保留: DAG 并行执行、分层内存压缩、自适应重规划
2. 借鉴: Client-Server 分离、MCP 协议、SSE 事件流、Permission 系统

---

## Design

### 架构概述

```
                            ┌─────────────────────────────────────────────┐
                            │              OpenWork / Custom UI           │
                            │         (Tauri / Web / CLI Client)          │
                            └─────────────────┬───────────────────────────┘
                                              │ HTTP + SSE
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Nimbus Server Layer                                │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                           nimbus/server/                                  │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │  │
│  │  │   Router    │  │  SSE Hub    │  │ Permission  │  │ OpenCode Compat │  │  │
│  │  │  (FastAPI)  │  │  (Events)   │  │  Manager    │  │    Adapter      │  │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘  │  │
│  └─────────┼────────────────┼────────────────┼──────────────────┼───────────┘  │
│            │                │                │                  │               │
│  ┌─────────▼────────────────▼────────────────▼──────────────────▼───────────┐  │
│  │                         Session Manager                                   │  │
│  │                    (Session State + Lifecycle)                           │  │
│  └─────────────────────────────┬────────────────────────────────────────────┘  │
│                                │                                               │
│  ┌─────────────────────────────▼────────────────────────────────────────────┐  │
│  │                          nimbus/storage/                                  │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐   │  │
│  │  │  SessionStore   │  │  MemoryStore    │  │      DAGStore           │   │  │
│  │  │   (SQLite)      │  │  (Serialized)   │  │   (Checkpoint)          │   │  │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────────┘   │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Nimbus Core Layer                                  │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                         NotebookAgent (Existing)                          │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │  │
│  │  │ DAGPlanner  │  │AsyncRuntime │  │TieredMemory │  │  SkillLoader    │  │  │
│  │  │ (Re-plan)   │  │ (Parallel)  │  │ (Compress)  │  │  (MCP Adapter)  │  │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                              │
                                              ▼
                            ┌─────────────────────────────────────┐
                            │           MCP Servers               │
                            │  (filesystem, git, search, ...)     │
                            └─────────────────────────────────────┘
```

### 核心组件

1. **Server Router** (`nimbus/server/api.py`)
   - FastAPI 应用入口
   - RESTful API 路由定义
   - 请求验证和错误处理

2. **SSE Hub** (`nimbus/server/sse.py`)
   - Server-Sent Events 管理
   - 事件广播和订阅
   - 连接生命周期管理

3. **Permission Manager** (`nimbus/server/permission.py`)
   - 工具执行权限控制
   - ask/allow_once/allow_always/deny 策略
   - 权限规则持久化

4. **Session Manager** (`nimbus/server/session.py`)
   - Session 生命周期管理
   - Agent 实例池
   - 内存和 DAG 状态同步

5. **Storage Layer** (`nimbus/storage/`)
   - SQLite 持久化
   - TieredMemory 序列化
   - DAG Checkpoint 存储

6. **MCP Adapter** (`nimbus/skills/mcp.py`)
   - Markdown Skill -> MCP Tool 转换
   - MCP Server 动态工具发现
   - 工具调用代理

### 数据流

```
1. Client 发送 Chat 请求
   POST /api/v1/sessions/{id}/chat
       │
       ▼
2. SessionManager 获取/创建 Agent
       │
       ▼
3. Agent.run_stream() 开始执行
       │
       ├── SSE: event=planning
       │
4. DAGPlanner 创建执行计划
       │
       ├── SSE: event=dag_created (Nimbus 扩展)
       │
5. AsyncRuntime 并行执行 Tasks
       │
       ├── SSE: event=task_start
       ├── SSE: event=tool_call (需要权限时)
       │      │
       │      ├── Client 响应: POST /api/v1/permissions/{id}/respond
       │      │
       ├── SSE: event=task_done
       │
6. 完成后更新 Memory 和 Storage
       │
       ├── SSE: event=dag_complete
       └── SSE: event=message (最终响应)
```

---

## API Interface Definition

### Base URL

```
http://localhost:8080/api/v1
```

### Session APIs

#### Create Session

```yaml
POST /sessions
Content-Type: application/json

Request:
{
  "name": "string",           # 可选，会话名称
  "workspace_path": "string", # 可选，工作目录
  "memory_type": "tiered",    # simple | tiered
  "planner_type": "dag"       # simple | dag
}

Response: 201 Created
{
  "id": "sess_abc123",
  "name": "fix-bug",
  "created_at": "2026-01-23T10:00:00Z",
  "status": "active",
  "memory_type": "tiered",
  "planner_type": "dag"
}
```

#### List Sessions

```yaml
GET /sessions?status=active&limit=20&offset=0

Response: 200 OK
{
  "items": [
    {
      "id": "sess_abc123",
      "name": "fix-bug",
      "created_at": "2026-01-23T10:00:00Z",
      "status": "active",
      "last_message_at": "2026-01-23T10:05:00Z"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

#### Get Session

```yaml
GET /sessions/{session_id}

Response: 200 OK
{
  "id": "sess_abc123",
  "name": "fix-bug",
  "created_at": "2026-01-23T10:00:00Z",
  "status": "active",
  "memory_stats": {
    "type": "tiered",
    "pinned_tokens": 500,
    "working_tokens": 1200,
    "episodic_tokens": 3500,
    "total_tokens": 5200
  },
  "message_count": 12
}
```

#### Delete Session

```yaml
DELETE /sessions/{session_id}

Response: 204 No Content
```

### Chat APIs

#### Send Message (Streaming)

```yaml
POST /sessions/{session_id}/chat
Content-Type: application/json

Request:
{
  "content": "string",        # 用户消息
  "attachments": [            # 可选，附件
    {
      "type": "file",
      "path": "/path/to/file.pdf",
      "name": "document.pdf"
    }
  ]
}

Response: 200 OK (SSE Stream)
# See SSE Events section
```

#### Get Messages

```yaml
GET /sessions/{session_id}/messages?limit=50

Response: 200 OK
{
  "items": [
    {
      "id": "msg_001",
      "role": "user",
      "content": "Fix the login bug",
      "created_at": "2026-01-23T10:00:00Z"
    },
    {
      "id": "msg_002",
      "role": "assistant",
      "content": "I'll analyze the login code...",
      "created_at": "2026-01-23T10:00:05Z",
      "artifacts": [...],
      "dag_id": "dag_xyz789"
    }
  ]
}
```

### Permission APIs

#### Respond to Permission Request

```yaml
POST /permissions/{request_id}/respond
Content-Type: application/json

Request:
{
  "decision": "allow_once"  # allow_once | allow_always | deny
}

Response: 200 OK
{
  "request_id": "perm_123",
  "decision": "allow_once",
  "tool": "bash",
  "resolved_at": "2026-01-23T10:00:10Z"
}
```

#### Get Permission Rules

```yaml
GET /permissions/rules

Response: 200 OK
{
  "rules": [
    {
      "tool": "read_file",
      "decision": "allow_always"
    },
    {
      "tool": "bash",
      "decision": "ask"
    }
  ]
}
```

#### Update Permission Rule

```yaml
PUT /permissions/rules/{tool}
Content-Type: application/json

Request:
{
  "decision": "allow_always"  # ask | allow_always | deny
}

Response: 200 OK
```

### DAG APIs (Nimbus Extension)

#### Get DAG Status

```yaml
GET /sessions/{session_id}/dags/{dag_id}

Response: 200 OK
{
  "id": "dag_xyz789",
  "goal": "Fix the login bug",
  "status": "running",
  "created_at": "2026-01-23T10:00:00Z",
  "nodes": [
    {
      "id": "task_001",
      "skill": "read_file",
      "status": "completed",
      "depends_on": [],
      "duration_ms": 120
    },
    {
      "id": "task_002",
      "skill": "analyze_code",
      "status": "running",
      "depends_on": ["task_001"],
      "duration_ms": null
    }
  ],
  "stats": {
    "total": 5,
    "completed": 2,
    "running": 1,
    "pending": 2,
    "failed": 0
  }
}
```

### Skill/Tool APIs

#### List Available Skills

```yaml
GET /skills

Response: 200 OK
{
  "skills": [
    {
      "name": "read_file",
      "description": "Read file contents",
      "source": "builtin",
      "parameters": [...]
    },
    {
      "name": "search_code",
      "description": "Search code in workspace",
      "source": "mcp:filesystem",
      "parameters": [...]
    }
  ]
}
```

#### List MCP Servers

```yaml
GET /mcp/servers

Response: 200 OK
{
  "servers": [
    {
      "name": "filesystem",
      "status": "connected",
      "tools": ["read_file", "write_file", "list_directory"]
    }
  ]
}
```

### Health & Config APIs

```yaml
GET /health
Response: 200 OK
{ "status": "healthy", "version": "0.1.0" }

GET /config
Response: 200 OK
{
  "default_memory_type": "tiered",
  "default_planner_type": "dag",
  "max_concurrent_sessions": 10,
  "mcp_servers": [...]
}
```

---

## SSE Event Types

### Event Stream Format

```
event: {event_type}
data: {json_payload}

```

### Event Types

| Event Type | Description | Payload |
|------------|-------------|---------|
| `connected` | 连接建立 | `{ "session_id": "..." }` |
| `message_start` | 开始处理消息 | `{ "message_id": "..." }` |
| `planning` | 规划中 | `{ "status": "creating_plan" }` |
| `dag_created` | DAG 已创建 (Nimbus) | `{ "dag_id": "...", "nodes": [...] }` |
| `task_start` | 任务开始 | `{ "task_id": "...", "skill": "...", "params": {...} }` |
| `tool_call` | 工具调用 | `{ "tool": "...", "args": {...} }` |
| `tool_result` | 工具结果 | `{ "tool": "...", "result": "..." }` |
| `task_done` | 任务完成 | `{ "task_id": "...", "result": "...", "duration_ms": N }` |
| `task_failed` | 任务失败 | `{ "task_id": "...", "error": "..." }` |
| `permission_request` | 权限请求 | `{ "request_id": "...", "tool": "...", "args": {...} }` |
| `dag_complete` | DAG 完成 (Nimbus) | `{ "dag_id": "...", "stats": {...} }` |
| `message` | 最终响应 | `{ "content": "...", "artifacts": [...] }` |
| `error` | 错误 | `{ "code": "...", "message": "..." }` |
| `heartbeat` | 心跳 | `{ "timestamp": "..." }` |

### Example SSE Stream

```
event: connected
data: {"session_id":"sess_abc123"}

event: message_start
data: {"message_id":"msg_002"}

event: planning
data: {"status":"creating_plan"}

event: dag_created
data: {"dag_id":"dag_xyz","goal":"Fix login bug","total_tasks":3}

event: task_start
data: {"task_id":"t1","skill":"read_file","params":{"path":"auth.py"}}

event: tool_call
data: {"tool":"read_file","args":{"path":"auth.py"}}

event: permission_request
data: {"request_id":"perm_001","tool":"bash","args":{"command":"pytest"}}

# Client responds via POST /permissions/perm_001/respond

event: tool_result
data: {"tool":"bash","result":"All tests passed"}

event: task_done
data: {"task_id":"t1","result":"file content...","duration_ms":150}

event: dag_complete
data: {"dag_id":"dag_xyz","stats":{"completed":3,"failed":0}}

event: message
data: {"content":"I've fixed the bug...","artifacts":[]}
```

---

## Data Models (Pydantic)

```python
# nimbus/server/models.py

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================

class SessionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class PermissionDecision(str, Enum):
    ASK = "ask"
    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


class TaskStatusEnum(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# =============================================================================
# Session Models
# =============================================================================

class SessionCreate(BaseModel):
    name: Optional[str] = None
    workspace_path: Optional[str] = None
    memory_type: str = "tiered"  # simple | tiered
    planner_type: str = "dag"    # simple | dag


class SessionResponse(BaseModel):
    id: str
    name: Optional[str]
    created_at: datetime
    status: SessionStatus
    memory_type: str
    planner_type: str
    last_message_at: Optional[datetime] = None
    message_count: int = 0


class SessionDetail(SessionResponse):
    memory_stats: Optional[Dict[str, Any]] = None
    workspace_path: Optional[str] = None


class SessionList(BaseModel):
    items: List[SessionResponse]
    total: int
    limit: int
    offset: int


# =============================================================================
# Message Models
# =============================================================================

class AttachmentCreate(BaseModel):
    type: str  # file, url, text
    path: Optional[str] = None
    url: Optional[str] = None
    content: Optional[str] = None
    name: Optional[str] = None


class ChatRequest(BaseModel):
    content: str
    attachments: List[AttachmentCreate] = Field(default_factory=list)


class ArtifactResponse(BaseModel):
    id: str
    type: str  # file, chart, code, table, image, markdown
    title: str
    data: Any
    mime_type: Optional[str] = None
    url: Optional[str] = None


class MessageResponse(BaseModel):
    id: str
    role: str  # user | assistant | system
    content: str
    created_at: datetime
    artifacts: List[ArtifactResponse] = Field(default_factory=list)
    dag_id: Optional[str] = None


class MessageList(BaseModel):
    items: List[MessageResponse]


# =============================================================================
# Permission Models
# =============================================================================

class PermissionRequest(BaseModel):
    request_id: str
    tool: str
    args: Dict[str, Any]
    session_id: str
    created_at: datetime


class PermissionRespond(BaseModel):
    decision: PermissionDecision


class PermissionRule(BaseModel):
    tool: str
    decision: PermissionDecision


class PermissionRuleList(BaseModel):
    rules: List[PermissionRule]


# =============================================================================
# DAG Models (Nimbus Extension)
# =============================================================================

class TaskNodeResponse(BaseModel):
    id: str
    skill: str
    params: Dict[str, Any]
    status: TaskStatusEnum
    depends_on: List[str]
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_ms: Optional[int] = None


class DAGStatsResponse(BaseModel):
    total: int
    completed: int
    running: int
    pending: int
    failed: int
    skipped: int


class DAGResponse(BaseModel):
    id: str
    goal: str
    status: str  # pending | running | completed | failed
    created_at: datetime
    nodes: List[TaskNodeResponse]
    stats: DAGStatsResponse


# =============================================================================
# Skill/Tool Models
# =============================================================================

class SkillParameter(BaseModel):
    name: str
    type: str
    description: str
    required: bool = False
    default: Optional[Any] = None


class SkillResponse(BaseModel):
    name: str
    description: str
    source: str  # builtin | mcp:{server_name} | markdown
    parameters: List[SkillParameter]


class SkillList(BaseModel):
    skills: List[SkillResponse]


class MCPServerStatus(BaseModel):
    name: str
    status: str  # connected | disconnected | error
    tools: List[str]
    error: Optional[str] = None


class MCPServerList(BaseModel):
    servers: List[MCPServerStatus]


# =============================================================================
# SSE Event Models
# =============================================================================

class SSEEvent(BaseModel):
    event: str
    data: Dict[str, Any]


# =============================================================================
# Config Models
# =============================================================================

class ServerConfig(BaseModel):
    default_memory_type: str = "tiered"
    default_planner_type: str = "dag"
    max_concurrent_sessions: int = 10
    mcp_servers: List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    version: str
```

---

## MCP Tool Schema

### Skill -> MCP Tool Conversion

```python
# nimbus/skills/mcp.py

from typing import Any, Dict, List
from .schema import SkillDefinition, SkillParameter


def skill_to_mcp_tool(skill: SkillDefinition) -> Dict[str, Any]:
    """Convert Nimbus SkillDefinition to MCP Tool schema.

    MCP Tool Schema follows JSON Schema format similar to OpenAI function calling.

    Args:
        skill: Nimbus skill definition

    Returns:
        MCP-compatible tool definition
    """
    properties = {}
    required = []

    for param in skill.parameters:
        properties[param.name] = {
            "type": param.type,
            "description": param.description,
        }
        if param.enum:
            properties[param.name]["enum"] = param.enum
        if param.default is not None:
            properties[param.name]["default"] = param.default
        if param.required:
            required.append(param.name)

    return {
        "name": skill.name,
        "description": skill.description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    }


def mcp_tool_to_skill(tool: Dict[str, Any], source: str) -> SkillDefinition:
    """Convert MCP Tool schema to Nimbus SkillDefinition.

    Args:
        tool: MCP tool definition
        source: Source identifier (e.g., "mcp:filesystem")

    Returns:
        Nimbus skill definition
    """
    input_schema = tool.get("inputSchema", {})
    properties = input_schema.get("properties", {})
    required_params = input_schema.get("required", [])

    parameters = []
    for name, prop in properties.items():
        param = SkillParameter(
            name=name,
            type=prop.get("type", "string"),
            description=prop.get("description", ""),
            required=name in required_params,
            enum=prop.get("enum"),
            default=prop.get("default"),
        )
        parameters.append(param)

    return SkillDefinition(
        name=tool["name"],
        description=tool.get("description", ""),
        parameters=parameters,
        source_path=source,
    )
```

### MCP Tool Example

```json
{
  "name": "read_file",
  "description": "Read the contents of a file at the specified path",
  "inputSchema": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "The path to the file to read"
      },
      "encoding": {
        "type": "string",
        "description": "File encoding",
        "default": "utf-8"
      }
    },
    "required": ["path"]
  }
}
```

---

## SQLite Schema

```sql
-- nimbus/storage/schema.sql

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

CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_sessions_created_at ON sessions(created_at DESC);


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

CREATE INDEX idx_messages_session_id ON messages(session_id);
CREATE INDEX idx_messages_created_at ON messages(created_at DESC);


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

CREATE INDEX idx_dags_session_id ON dags(session_id);
CREATE INDEX idx_dags_status ON dags(status);


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

CREATE INDEX idx_memory_checkpoints_session ON memory_checkpoints(session_id, checkpoint_num DESC);


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

CREATE INDEX idx_permission_requests_pending ON permission_requests(session_id)
    WHERE resolved_at IS NULL;


-- =============================================================================
-- Key-Value Store (For misc config/state)
-- =============================================================================

CREATE TABLE IF NOT EXISTS kv_store (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### Storage Layer Implementation

```python
# nimbus/storage/sqlite.py

import aiosqlite
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

from ..core.memory import TieredMemoryManager, PinnedItem, Message, MemoryConfig
from ..core.types import TaskDAG


class SQLiteStorage:
    """SQLite-based persistent storage for Nimbus sessions."""

    def __init__(self, db_path: str = ".nimbus/nimbus.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """Initialize database with schema."""
        async with self._get_connection() as db:
            schema_path = Path(__file__).parent / "schema.sql"
            schema = schema_path.read_text()
            await db.executescript(schema)
            await db.commit()

    @asynccontextmanager
    async def _get_connection(self):
        """Get database connection with row factory."""
        if self._connection is None:
            self._connection = await aiosqlite.connect(str(self.db_path))
            self._connection.row_factory = aiosqlite.Row
        yield self._connection

    async def close(self) -> None:
        """Close database connection."""
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
    ) -> Dict[str, Any]:
        """Create a new session."""
        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT INTO sessions (id, name, workspace_path, memory_type, planner_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, name, workspace_path, memory_type, planner_type)
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = await cursor.fetchone()
            return dict(row)

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session by ID."""
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_sessions(
        self,
        status: str = "active",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        """List sessions with pagination."""
        async with self._get_connection() as db:
            # Get total count
            cursor = await db.execute(
                "SELECT COUNT(*) FROM sessions WHERE status = ?",
                (status,)
            )
            total = (await cursor.fetchone())[0]

            # Get paginated results
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
                (status, limit, offset)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows], total

    async def update_session(
        self,
        session_id: str,
        **kwargs
    ) -> None:
        """Update session fields."""
        if not kwargs:
            return

        set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [session_id]

        async with self._get_connection() as db:
            await db.execute(
                f"UPDATE sessions SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values
            )
            await db.commit()

    async def delete_session(self, session_id: str) -> None:
        """Soft delete session."""
        await self.update_session(session_id, status="deleted")

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
        artifacts: Optional[List[Dict]] = None,
    ) -> Dict[str, Any]:
        """Add a message to session."""
        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT INTO messages (id, session_id, role, content, dag_id, artifacts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (message_id, session_id, role, content, dag_id,
                 json.dumps(artifacts) if artifacts else None)
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM messages WHERE id = ?",
                (message_id,)
            )
            row = await cursor.fetchone()
            result = dict(row)
            if result.get("artifacts"):
                result["artifacts"] = json.loads(result["artifacts"])
            return result

    async def get_messages(
        self,
        session_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get messages for session."""
        async with self._get_connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (session_id, limit)
            )
            rows = await cursor.fetchall()
            results = []
            for row in rows:
                r = dict(row)
                if r.get("artifacts"):
                    r["artifacts"] = json.loads(r["artifacts"])
                results.append(r)
            return results

    # =========================================================================
    # DAG Operations
    # =========================================================================

    async def save_dag(self, session_id: str, dag: TaskDAG) -> None:
        """Save DAG state."""
        stats = {
            "total": len(dag.nodes),
            "completed": dag.completed_count,
            "pending": dag.pending_count,
        }

        status = "completed" if dag.is_completed() else "running"

        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO dags
                (id, session_id, goal, status, state, total_tasks, completed_tasks)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (dag.id, session_id, dag.goal, status,
                 json.dumps(dag.to_dict()), stats["total"], stats["completed"])
            )
            await db.commit()

    async def get_dag(self, dag_id: str) -> Optional[TaskDAG]:
        """Load DAG by ID."""
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT state FROM dags WHERE id = ?",
                (dag_id,)
            )
            row = await cursor.fetchone()
            if row:
                return TaskDAG.from_dict(json.loads(row["state"]))
            return None

    # =========================================================================
    # Memory Checkpoint Operations
    # =========================================================================

    async def save_memory_checkpoint(
        self,
        session_id: str,
        memory: TieredMemoryManager,
    ) -> str:
        """Save memory checkpoint."""
        checkpoint_id = f"ckpt_{session_id}_{memory._turn_count}"

        async with self._get_connection() as db:
            # Get next checkpoint number
            cursor = await db.execute(
                "SELECT MAX(checkpoint_num) FROM memory_checkpoints WHERE session_id = ?",
                (session_id,)
            )
            row = await cursor.fetchone()
            checkpoint_num = (row[0] or 0) + 1

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
                    json.dumps([{
                        "id": p.id,
                        "type": p.type,
                        "content": p.content,
                        "priority": p.priority,
                        "description": p.description,
                        "read_only": p.read_only,
                    } for p in memory.pinned]),
                    json.dumps(memory.working),
                    json.dumps([{
                        "role": m.role,
                        "content": m.content,
                        "timestamp": m.timestamp.isoformat(),
                    } for m in memory.episodic]),
                    json.dumps(memory.episodic_summaries),
                    json.dumps(memory.semantic_cache),
                    memory._turn_count,
                    memory._compression_count,
                )
            )
            await db.commit()

        return checkpoint_id

    async def load_memory_checkpoint(
        self,
        session_id: str,
        config: Optional[MemoryConfig] = None,
    ) -> Optional[TieredMemoryManager]:
        """Load latest memory checkpoint."""
        async with self._get_connection() as db:
            cursor = await db.execute(
                """
                SELECT * FROM memory_checkpoints
                WHERE session_id = ?
                ORDER BY checkpoint_num DESC
                LIMIT 1
                """,
                (session_id,)
            )
            row = await cursor.fetchone()

            if not row:
                return None

            memory = TieredMemoryManager(
                config=config or MemoryConfig(),
                session_id=session_id,
            )

            # Restore pinned
            for p in json.loads(row["pinned"]):
                memory.pinned.append(PinnedItem(
                    id=p["id"],
                    type=p["type"],
                    content=p["content"],
                    priority=p.get("priority", 0),
                    description=p.get("description", ""),
                    read_only=p.get("read_only", False),
                ))

            # Restore working
            memory.working = json.loads(row["working"])

            # Restore episodic
            for m in json.loads(row["episodic"]):
                memory.episodic.append(Message(
                    role=m["role"],
                    content=m["content"],
                    timestamp=datetime.fromisoformat(m["timestamp"]),
                ))

            # Restore summaries and cache
            memory.episodic_summaries = json.loads(row["summaries"])
            memory.semantic_cache = json.loads(row["semantic_cache"])
            memory._turn_count = row["turn_count"]
            memory._compression_count = row["compression_count"]

            return memory

    # =========================================================================
    # Permission Operations
    # =========================================================================

    async def get_permission_rule(self, tool: str) -> Optional[str]:
        """Get permission decision for tool."""
        async with self._get_connection() as db:
            cursor = await db.execute(
                "SELECT decision FROM permission_rules WHERE tool = ?",
                (tool,)
            )
            row = await cursor.fetchone()
            return row["decision"] if row else None

    async def set_permission_rule(self, tool: str, decision: str) -> None:
        """Set permission rule for tool."""
        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO permission_rules (tool, decision, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (tool, decision)
            )
            await db.commit()

    async def get_all_permission_rules(self) -> List[Dict[str, str]]:
        """Get all permission rules."""
        async with self._get_connection() as db:
            cursor = await db.execute("SELECT tool, decision FROM permission_rules")
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def create_permission_request(
        self,
        request_id: str,
        session_id: str,
        tool: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Create pending permission request."""
        async with self._get_connection() as db:
            await db.execute(
                """
                INSERT INTO permission_requests (id, session_id, tool, args)
                VALUES (?, ?, ?, ?)
                """,
                (request_id, session_id, tool, json.dumps(args))
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM permission_requests WHERE id = ?",
                (request_id,)
            )
            row = await cursor.fetchone()
            result = dict(row)
            result["args"] = json.loads(result["args"])
            return result

    async def resolve_permission_request(
        self,
        request_id: str,
        decision: str,
    ) -> Optional[Dict[str, Any]]:
        """Resolve a pending permission request."""
        async with self._get_connection() as db:
            await db.execute(
                """
                UPDATE permission_requests
                SET decision = ?, resolved_at = CURRENT_TIMESTAMP
                WHERE id = ? AND resolved_at IS NULL
                """,
                (decision, request_id)
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM permission_requests WHERE id = ?",
                (request_id,)
            )
            row = await cursor.fetchone()
            if row:
                result = dict(row)
                result["args"] = json.loads(result["args"])
                return result
            return None
```

---

## Directory Structure

```
nimbus/
├── __init__.py
├── core/                          # Existing core modules
│   ├── agent.py                   # NotebookAgent
│   ├── runtime.py                 # AsyncRuntime
│   ├── memory.py                  # TieredMemoryManager
│   ├── planner.py                 # DAGPlanner
│   ├── types.py                   # Core types
│   └── ...
│
├── server/                        # NEW: HTTP Server Layer
│   ├── __init__.py
│   ├── app.py                     # FastAPI application factory
│   ├── api.py                     # API route definitions
│   ├── models.py                  # Pydantic request/response models
│   ├── sse.py                     # SSE event hub
│   ├── session.py                 # Session manager
│   ├── permission.py              # Permission manager
│   └── middleware.py              # CORS, auth, logging middleware
│
├── storage/                       # NEW: Persistence Layer
│   ├── __init__.py
│   ├── schema.sql                 # SQLite schema
│   ├── sqlite.py                  # SQLite storage implementation
│   └── migrations/                # Schema migrations
│       └── 001_initial.sql
│
├── skills/                        # Existing + MCP extension
│   ├── __init__.py
│   ├── loader.py                  # Existing skill loader
│   ├── schema.py                  # Skill definitions
│   ├── mcp.py                     # NEW: MCP adapter
│   └── builtin/                   # Built-in skills
│       ├── chat.py
│       ├── search.py
│       └── ...
│
├── cli/                           # NEW: CLI Module
│   ├── __init__.py
│   ├── main.py                    # CLI entry point
│   └── commands/
│       ├── serve.py               # nimbus serve
│       ├── session.py             # nimbus session list/create
│       └── config.py              # nimbus config
│
├── config/                        # NEW: Configuration
│   ├── __init__.py
│   ├── settings.py                # Pydantic settings
│   └── default.toml               # Default config
│
└── docs/
    ├── openwork-opencode-analysis.md
    └── openwork-integration-design.md  # This document
```

---

## Decisions

### Decision 1: FastAPI + aiosqlite 技术栈

- **Decision**: 使用 FastAPI 作为 HTTP Server，aiosqlite 作为存储
- **Rationale**:
  - FastAPI 原生支持 async/await，与 Nimbus 的 AsyncRuntime 契合
  - 自动 OpenAPI 文档生成
  - aiosqlite 轻量级，无需额外依赖
- **Alternatives**:
  - Flask + SQLAlchemy: 同步模型不适合
  - aiohttp: 功能较少
- **Risks**: FastAPI SSE 支持需要额外处理 (sse-starlette)

### Decision 2: 扩展 API + OpenCode 兼容层

- **Decision**: 核心 API 暴露 Nimbus 能力，提供可选的 OpenCode 兼容适配器
- **Rationale**:
  - 保留 DAG/Memory 优势
  - 可渐进迁移到 OpenWork
  - 灵活性最高
- **Alternatives**:
  - 完全兼容 OpenCode API: 会丢失 Nimbus 特色
  - 完全独立 API: 无法复用 OpenWork
- **Risks**: 维护两套 API 增加工作量

### Decision 3: SSE 事件扩展

- **Decision**: 在标准事件基础上增加 `dag_created`, `dag_complete` 等扩展事件
- **Rationale**:
  - 支持 DAG 可视化
  - 提供更细粒度的进度反馈
  - 向后兼容标准事件
- **Alternatives**: 只使用标准事件
- **Risks**: 自定义事件可能与 OpenWork 不兼容

### Decision 4: Permission 系统设计

- **Decision**: 采用 ask/allow_once/allow_always/deny 四级权限模型
- **Rationale**:
  - 与 OpenCode 兼容
  - 平衡安全性和便利性
  - 支持持久化规则
- **Alternatives**: 简单的 allow/deny
- **Risks**: 复杂工具参数匹配可能困难

---

## Tradeoffs

1. **复杂度 vs 兼容性**: 选择扩展 API + 兼容层，增加了代码复杂度但获得了更好的兼容性
2. **实时性 vs 可靠性**: SSE 提供实时推送，但连接断开需要重连机制
3. **SQLite vs 分布式存储**: 选择 SQLite 简化部署，但限制了水平扩展能力
4. **MCP 协议 vs 原生 Skill**: 采用 MCP 获得生态，但增加了协议转换开销

---

## Constraints

- **技术约束**:
  - Python 3.10+ (async/await, match 语句)
  - FastAPI 0.100+
  - aiosqlite 0.19+
  - 兼容现有 NotebookAgent 接口

- **业务约束**:
  - 单 Server 部署场景优先
  - 支持 CLI 和 GUI 两种客户端

- **安全约束**:
  - 工具执行需要权限控制
  - 敏感操作 (bash, file write) 默认需要确认

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| SSE 连接不稳定 | Medium | Medium | 实现心跳机制和自动重连 |
| SQLite 并发限制 | Low | Medium | 使用 WAL 模式，考虑连接池 |
| MCP Server 通信失败 | Medium | High | 超时重试，优雅降级 |
| 内存序列化开销 | Low | Low | 增量 checkpoint，延迟加载 |
| OpenCode API 变更 | Low | Medium | 抽象兼容层，版本锁定 |

---

## Evidence

- Sources:
  - `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/docs/openwork-opencode-analysis.md` - OpenCode 架构分析
  - `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/core/agent.py:21-931` - NotebookAgent 实现
  - `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/core/runtime.py:1-430` - AsyncRuntime 实现
  - `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/core/memory.py:1-708` - TieredMemoryManager 实现
  - `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/skills/loader.py:1-330` - SkillLoader 实现
  - `/Users/wangqing/sourcecode/agent/agent-framework/nimbus/skills/schema.py:1-375` - Skill Schema 定义

- Assumptions:
  - OpenWork 可接受扩展事件类型
  - MCP 协议稳定，短期内不会大改
  - 单机部署足够满足初期需求

---

## Implementation Priority

### Phase 1: Core Server (Week 1-2)

1. **P0**: `nimbus/server/app.py` - FastAPI 应用框架
2. **P0**: `nimbus/server/api.py` - Session CRUD API
3. **P0**: `nimbus/server/sse.py` - SSE 事件推送
4. **P0**: `nimbus/storage/sqlite.py` - 基础存储层

### Phase 2: Integration (Week 3)

5. **P1**: `nimbus/server/session.py` - Session Manager 与 Agent 集成
6. **P1**: `nimbus/server/permission.py` - Permission 系统
7. **P1**: `nimbus/cli/main.py` - `nimbus serve` 命令

### Phase 3: MCP & Polish (Week 4)

8. **P2**: `nimbus/skills/mcp.py` - MCP 工具适配
9. **P2**: `nimbus/server/models.py` - 完整 Pydantic 模型
10. **P2**: OpenCode 兼容适配器 (可选)

---

## Next Steps

1. **评审本设计文档** - 确认架构方向
2. **实现 Phase 1 核心 Server** - 建立 API 基础
3. **编写集成测试** - 验证与 NotebookAgent 的集成
4. **与 OpenWork 联调** - 验证 SSE 事件兼容性
5. **性能测试** - 验证 SQLite 和 SSE 性能

---

*Document End*
