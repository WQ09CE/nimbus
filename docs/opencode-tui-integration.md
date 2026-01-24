# OpenCode TUI 集成指南

> **Status**: Active
> **Version**: 1.0
> **Date**: 2025-01-24

本文档描述如何将 OpenCode TUI (Go 实现的终端界面) 连接到 Nimbus Agent 后端。

---

## 1. 概述

Nimbus 实现了 OpenCode 兼容 API 层，使得 OpenCode TUI 可以无缝连接到 Nimbus 后端。这种架构带来以下优势：

- **保留 Nimbus 核心能力**: DAG 并行执行、分层内存压缩、自适应重规划
- **复用 OpenCode TUI**: 无需重新开发终端界面
- **标准化接口**: 基于 HTTP + SSE 的通用通信协议

### 核心组件

| 组件 | 位置 | 职责 |
|------|------|------|
| OpenCode 兼容层 | `server/compat/opencode.py` | API 格式转换 |
| SSE Hub | `server/sse.py` | 实时事件推送 |
| Session Manager | `server/session.py` | 会话生命周期管理 |
| Permission Manager | `server/permission.py` | 工具执行权限控制 |

---

## 2. 架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          OpenCode TUI (Go)                               │
│                      Terminal User Interface                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │   Session   │  │   Chat      │  │  Permission │  │   Status    │    │
│  │   Picker    │  │   View      │  │   Dialog    │  │   Bar       │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
└────────────────────────────┬────────────────────────────────────────────┘
                             │
                             │ HTTP + SSE
                             │ (localhost:8080)
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Nimbus Server                                    │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                  OpenCode Compat Layer                           │   │
│  │                 (server/compat/opencode.py)                      │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐    │   │
│  │  │  Session  │  │  Message  │  │    SSE    │  │Permission │    │   │
│  │  │   APIs    │  │   APIs    │  │  Stream   │  │   APIs    │    │   │
│  │  └───────────┘  └───────────┘  └───────────┘  └───────────┘    │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │                                           │
│  ┌──────────────────────────▼──────────────────────────────────────┐   │
│  │                    Session Manager                               │   │
│  │              (Agent Instance Pool + State Sync)                  │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │                                           │
│  ┌──────────────────────────▼──────────────────────────────────────┐   │
│  │                      Agent Core                                  │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐    │   │
│  │  │   DAG     │  │   Async   │  │  Tiered   │  │   Skill   │    │   │
│  │  │  Planner  │  │  Runtime  │  │  Memory   │  │  Loader   │    │   │
│  │  └───────────┘  └───────────┘  └───────────┘  └───────────┘    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                             │                                           │
│  ┌──────────────────────────▼──────────────────────────────────────┐   │
│  │                    SQLite Storage                                │   │
│  │           (Sessions, Messages, DAGs, Checkpoints)                │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 快速开始

### 3.1 启动 Nimbus Server

```bash
# 方式 1: 使用 uvicorn 直接启动
cd /path/to/nimbus
uvicorn server.app:create_app --factory --host 0.0.0.0 --port 8080

# 方式 2: 使用 nimbus CLI (如果已安装)
nimbus serve --port 8080

# 方式 3: 使用环境变量配置
NIMBUS_DB=".nimbus/data.db" \
NIMBUS_LLM_MODEL="qwen3:8b" \
NIMBUS_LLM_URL="http://localhost:11434" \
uvicorn server.app:create_app --factory --port 8080
```

### 3.2 配置 OpenCode TUI 连接 Nimbus

在 OpenCode TUI 的配置文件中 (通常是 `~/.opencode/config.json` 或环境变量):

```json
{
  "server": {
    "host": "http://localhost:8080",
    "timeout": 30000
  }
}
```

或使用环境变量:

```bash
export OPENCODE_SERVER_URL="http://localhost:8080"
opencode
```

### 3.3 连接测试

```bash
# 测试 Root 端点
curl http://localhost:8080/
# 预期: {"status":"ok","server":"nimbus"}

# 测试 Health 端点
curl http://localhost:8080/health
# 预期: {"healthy":true}

# 测试 Session 创建
curl -X POST http://localhost:8080/session \
  -H "Content-Type: application/json" \
  -d '{"title":"test","directory":"."}'
# 预期: {"id":"sess_xxx","title":"test",...}

# 测试 SSE 连接
curl -N http://localhost:8080/event
# 预期: 持续输出 SSE 事件流
```

---

## 4. API 端点清单

### 4.1 Session 管理

| 端点 | 方法 | 描述 | 状态 |
|------|------|------|------|
| `/session` | GET | 列出所有会话 | Implemented |
| `/session` | POST | 创建新会话 | Implemented |
| `/session/{id}` | GET | 获取会话详情 | Implemented |
| `/session/{id}` | DELETE | 删除会话 | Implemented |
| `/session/{id}/abort` | POST | 中止当前操作 | Stub |
| `/session/{id}/summarize` | POST | 总结会话 | Not Implemented |

### 4.2 消息/聊天

| 端点 | 方法 | 描述 | 状态 |
|------|------|------|------|
| `/session/{id}/message` | GET | 获取会话消息 | Implemented |
| `/session/{id}/message` | POST | 发送消息 (SSE 响应) | Implemented |

### 4.3 SSE 事件流

| 端点 | 方法 | 描述 | 状态 |
|------|------|------|------|
| `/event` | GET | 全局事件流 | Implemented |
| `/global/event` | GET | 全局事件流别名 | Implemented |

### 4.4 权限系统

| 端点 | 方法 | 描述 | 状态 |
|------|------|------|------|
| `/permission/{id}` | POST | 响应权限请求 | Implemented |

### 4.5 系统端点

| 端点 | 方法 | 描述 | 状态 |
|------|------|------|------|
| `/` | GET | Root 连接检查 | Implemented |
| `/health` | GET | 健康检查 | Implemented |
| `/global/health` | GET | 全局健康检查 (含版本) | Implemented |
| `/config` | GET | 获取配置 | Stub |
| `/config/providers` | GET | 获取 Provider 配置 | Stub |
| `/provider` | GET | 列出 Provider | Stub |
| `/agent` | GET | 列出 Agent | Stub |
| `/path` | GET | 获取路径信息 | Implemented |
| `/vcs` | GET | 获取 VCS 信息 | Stub |
| `/lsp` | GET | 获取 LSP 信息 | Stub |
| `/project` | GET | 列出项目 | Stub |
| `/project/current` | GET | 获取当前项目 | Implemented |

### 4.6 Todo 系统 (Not Implemented)

OpenCode 原生的 Todo 系统在 Nimbus 中尚未实现。Nimbus 使用 DAG 任务系统替代。

### 4.7 文件操作 (Not Implemented)

文件操作通过 Agent 的 Skill 系统处理，不直接暴露 API。

---

## 5. SSE 事件格式

### 5.1 事件流格式

```
event: {event_type}
data: {json_payload}

```

每个事件由 `event:` 行和 `data:` 行组成，以两个换行符结束。

### 5.2 消息处理事件

#### event.start
消息处理开始

```json
{
  "event": "event.start",
  "data": {
    "messageID": "msg_abc123",
    "sessionID": "sess_xyz789"
  }
}
```

#### content.delta
内容增量 (流式输出)

```json
{
  "event": "content.delta",
  "data": {
    "text": "I'll help you with..."
  }
}
```

#### content.done
内容输出完成

```json
{
  "event": "content.done",
  "data": {}
}
```

#### event.done
消息处理完成

```json
{
  "event": "event.done",
  "data": {
    "messageID": "msg_abc123",
    "sessionID": "sess_xyz789"
  }
}
```

### 5.3 工具执行事件

#### event.status
执行状态变更

```json
{
  "event": "event.status",
  "data": {
    "status": "planning|executing",
    "message": "Creating plan...",
    "dagID": "dag_abc",
    "totalTasks": 5
  }
}
```

#### tool.start
工具开始执行

```json
{
  "event": "tool.start",
  "data": {
    "taskID": "task_001",
    "name": "read_file",
    "input": {"path": "/path/to/file"}
  }
}
```

#### tool.done
工具执行完成

```json
{
  "event": "tool.done",
  "data": {
    "taskID": "task_001",
    "result": "file content...",
    "durationMs": 150
  }
}
```

#### tool.error
工具执行失败

```json
{
  "event": "tool.error",
  "data": {
    "taskID": "task_001",
    "error": "File not found"
  }
}
```

### 5.4 权限请求事件

#### permission.request (Nimbus 扩展)
需要用户授权

```json
{
  "event": "permission_request",
  "data": {
    "request_id": "perm_abc123",
    "tool": "bash",
    "args": {"command": "rm -rf /tmp/test"}
  }
}
```

### 5.5 错误事件

#### event.error
执行错误

```json
{
  "event": "event.error",
  "data": {
    "code": "execution_error|server_error",
    "message": "Error description"
  }
}
```

### 5.6 连接管理事件

#### connected
连接建立 (全局事件流)

```json
{
  "event": "connected",
  "data": {
    "timestamp": "2025-01-24T10:00:00.000Z"
  }
}
```

#### heartbeat
心跳保活 (每 30 秒)

```json
{
  "event": "heartbeat",
  "data": {
    "timestamp": "2025-01-24T10:00:30.000Z"
  }
}
```

---

## 6. 与原生 OpenCode 的差异

### 6.1 完整实现的功能

| 功能 | OpenCode | Nimbus | 说明 |
|------|----------|--------|------|
| Session CRUD | Yes | Yes | 完全兼容 |
| Message 收发 | Yes | Yes | 完全兼容 |
| SSE 事件流 | Yes | Yes | 扩展了 DAG 相关事件 |
| Permission 系统 | Yes | Yes | 完全兼容 |

### 6.2 Stub/Mock 实现

以下功能返回静态或默认值，不影响基本使用：

| 功能 | 说明 |
|------|------|
| `/config` | 返回默认配置 |
| `/config/providers` | 返回空列表 |
| `/provider` | 返回单一 "nimbus" provider |
| `/agent` | 返回单一 "nimbus" agent |
| `/vcs` | 返回默认 git 状态 |
| `/lsp` | 返回空列表 |
| `/project` | 返回空列表 |
| `/session/{id}/abort` | 返回成功但实际不中止 |

### 6.3 Nimbus 扩展功能

Nimbus 在兼容 OpenCode 的基础上提供额外能力：

| 扩展 | 说明 |
|------|------|
| DAG 并行执行 | 任务可并行执行，通过 `event.status` 反馈进度 |
| 分层内存 | 支持 `tiered` 和 `simple` 内存类型 |
| DAG Planner | 支持 `dag` 和 `simple` 规划类型 |
| 扩展事件 | `event.status` 包含 DAG 相关字段 |

---

## 7. 已知限制

### 7.1 当前版本限制

1. **Abort 未实现**: `/session/{id}/abort` 返回成功但不实际中止执行
2. **Summarize 未实现**: 会话总结功能尚未开发
3. **Todo 系统缺失**: OpenCode 的 Todo API 未实现，使用 DAG 任务替代
4. **文件操作**: 不提供直接文件 API，通过 Skill 处理
5. **LSP 集成**: 不支持语言服务器协议
6. **多 Provider**: 仅支持配置的单一 LLM Provider

### 7.2 兼容性说明

- **OpenCode TUI 版本**: 测试于 OpenCode TUI v0.x，其他版本可能存在差异
- **SSE 重连**: 客户端需自行实现重连逻辑
- **并发会话**: 支持最多 10 个并发活跃会话 (可配置)

### 7.3 性能限制

- **SQLite 并发**: 单 Writer，适合单机部署
- **内存使用**: 每个活跃 Session 约占用 50-100MB 内存
- **响应延迟**: LLM 调用为主要瓶颈，通常 2-30 秒

---

## 8. 配置参考

### 8.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NIMBUS_DB` | `.nimbus/nimbus.db` | SQLite 数据库路径 |
| `NIMBUS_LLM_MODEL` | `qwen3:8b` | LLM 模型名称 |
| `NIMBUS_LLM_URL` | `http://localhost:11434` | LLM API 地址 (Ollama) |

### 8.2 服务器配置

```python
# server/app.py 默认配置
{
    "host": "0.0.0.0",
    "port": 8080,
    "cors_origins": ["*"],
    "max_concurrent_sessions": 10,
    "heartbeat_interval": 30.0,
}
```

---

## 9. 故障排除

### 9.1 连接问题

**问题**: TUI 无法连接到 Server

```bash
# 检查 Server 是否运行
curl http://localhost:8080/health

# 检查端口占用
lsof -i :8080

# 查看 Server 日志
uvicorn server.app:create_app --factory --log-level debug
```

### 9.2 SSE 连接断开

**问题**: 事件流频繁断开

- 检查网络稳定性
- 确认 heartbeat 正常 (30 秒一次)
- TUI 端实现自动重连

### 9.3 权限请求无响应

**问题**: 工具执行卡在权限请求

```bash
# 检查待处理的权限请求
# (需要通过 API 或日志查看)

# 默认超时 300 秒后自动拒绝
```

### 9.4 LLM 调用失败

**问题**: 消息发送后无响应

```bash
# 检查 Ollama 服务
curl http://localhost:11434/api/tags

# 检查模型是否存在
ollama list

# 测试模型调用
ollama run qwen3:8b "hello"
```

---

## 10. 相关文档

- [OpenWork/OpenCode 架构分析](./openwork-opencode-analysis.md)
- [Nimbus-OpenWork 集成设计](./openwork-integration-design.md)
- [API 参考手册](./api-reference.md)
- [Nimbus 架构文档](./architecture.md)

---

## 附录 A: 完整 API 示例

### A.1 创建会话并发送消息

```bash
# 1. 创建会话
SESSION=$(curl -s -X POST http://localhost:8080/session \
  -H "Content-Type: application/json" \
  -d '{"title":"Test Session","directory":"."}' | jq -r '.id')

echo "Created session: $SESSION"

# 2. 发送消息 (SSE 响应)
curl -N -X POST "http://localhost:8080/session/$SESSION/message" \
  -H "Content-Type: application/json" \
  -d '{"content":"List files in current directory"}'

# 3. 获取消息历史
curl "http://localhost:8080/session/$SESSION/message" | jq

# 4. 删除会话
curl -X DELETE "http://localhost:8080/session/$SESSION"
```

### A.2 响应权限请求

```bash
# 当收到 permission_request 事件时
PERM_ID="perm_abc123"

# 允许执行
curl -X POST "http://localhost:8080/permission/$PERM_ID" \
  -H "Content-Type: application/json" \
  -d '{"allow":true}'

# 或拒绝执行
curl -X POST "http://localhost:8080/permission/$PERM_ID" \
  -H "Content-Type: application/json" \
  -d '{"allow":false}'
```

### A.3 订阅全局事件流

```bash
# 使用 curl 订阅 (持续输出)
curl -N http://localhost:8080/event

# 使用 Python 处理事件
python3 << 'EOF'
import requests

with requests.get('http://localhost:8080/event', stream=True) as r:
    for line in r.iter_lines():
        if line:
            print(line.decode('utf-8'))
EOF
```

---

*Document End*
