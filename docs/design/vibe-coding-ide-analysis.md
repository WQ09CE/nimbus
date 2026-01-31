# Vibe Coding IDE 分析

> 目标：让 nimbus serve 兼容这个前端

## 项目结构

```
vibe-coding-ide/
├── backend/           # Python FastAPI 后端
│   ├── server.py      # FastAPI 应用入口
│   └── src/
│       ├── api/
│       │   ├── agent.py    # Agent API (核心)
│       │   ├── sandbox.py  # Sandbox API
│       │   └── models.py   # Models API
│       ├── agent/
│       │   ├── agent.py    # Agent 实现 (用 OpenAI Agents SDK)
│       │   └── tools/      # 工具定义
│       └── sse.py          # SSE 格式化
└── frontend/          # Next.js 前端
    └── src/
        ├── hooks/
        │   └── useAgentStream.tsx  # SSE 连接
        ├── context/
        │   └── RunContext.tsx      # 运行状态管理
        └── types/
            └── run.ts              # 类型定义
```

## API 端点

### 1. 创建运行
```
POST /api/runs/
Request:
{
    "user_id": "xxx",
    "project_id": "xxx",
    "message_history": [{"role": "user", "content": "..."}],
    "query": "用户问题",
    "project": {"path/to/file.py": "file content"},
    "model": "anthropic/claude-sonnet-4"
}

Response:
{
    "task_id": "task_1234567890_abc12345",
    "stream_token": "jwt_token_here"
}
```

### 2. 事件流 (SSE)
```
GET /api/runs/{task_id}/events?token={stream_token}

SSE 事件格式:
data: {"event_type": "xxx", "task_id": "xxx", "timestamp": "ISO8601", "data": {...}, "error": null}
```

### 3. 恢复运行 (代码执行后)
```
GET /api/runs/{task_id}/resume?token={resume_token}&result={exec_result}
```

## SSE 事件类型

| event_type | 说明 | data 结构 |
|------------|------|-----------|
| `run_log` | 运行日志 | `string` |
| `progress_update_tool_action_started` | 工具开始 | `{args: [{id, function: {name, arguments}}]}` |
| `progress_update_tool_action_completed` | 工具完成 | `{result: {tool_call: {id, function}, output_data}}` |
| `progress_update_tool_action_log` | 工具日志 | `{id, name, data}` |
| `agent_output` | 最终输出 | `string` (agent 回复) |
| `run_failed` | 运行失败 | `error: string` |
| `run_cancelled` | 运行取消 | - |

## 前端期望的 Action 类型

```typescript
type Action =
  | UserMessageAction      // 用户消息
  | AssistantThoughtAction // AI 思考
  | ToolStartedAction      // 工具开始 (对应 progress_update_tool_action_started)
  | ToolCompletedAction    // 工具完成 (对应 progress_update_tool_action_completed)
  | ToolFailedAction       // 工具失败
  | ExecRequestAction      // 请求执行代码
  | ExecResultAction       // 执行结果
  | SystemNoticeAction     // 系统通知
  | FinalAnswerAction      // 最终回答 (对应 agent_output)
```

## Nimbus Serve 需要实现的适配层

### 1. API 路由映射

```python
# nimbus/server/api_vibe.py

@router.post("/api/runs/")
async def create_run(request: RunRequest):
    """创建新的 agent 运行"""
    task_id = generate_task_id()
    stream_token = create_jwt({"run_id": task_id})
    
    # 存储 payload 供后续使用
    await store_run_payload(task_id, request)
    
    return {"task_id": task_id, "stream_token": stream_token}

@router.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, token: str):
    """SSE 事件流"""
    verify_token(token, run_id)
    payload = await get_run_payload(run_id)
    
    async def event_generator():
        # 创建 nimbus agent
        agent = AgentOS(...)
        
        # 转换事件格式
        async for event in agent.run_stream(payload["query"]):
            yield format_as_vibe_sse(run_id, event)
    
    return StreamingResponse(event_generator(), headers=SSE_HEADERS)
```

### 2. 事件格式转换

```python
def format_as_vibe_sse(task_id: str, nimbus_event: dict) -> str:
    """将 nimbus 事件转换为 vibe-coding-ide 格式"""
    
    if nimbus_event["type"] == "tool_call":
        return sse_format({
            "event_type": "progress_update_tool_action_started",
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "args": [{
                    "id": nimbus_event["tool_id"],
                    "function": {
                        "name": nimbus_event["tool_name"],
                        "arguments": nimbus_event["arguments"]
                    }
                }]
            }
        })
    
    elif nimbus_event["type"] == "tool_result":
        return sse_format({
            "event_type": "progress_update_tool_action_completed",
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "data": {
                "result": {
                    "tool_call": {...},
                    "output_data": nimbus_event["output"]
                }
            }
        })
    
    elif nimbus_event["type"] == "done":
        return sse_format({
            "event_type": "agent_output",
            "task_id": task_id,
            "timestamp": datetime.now().isoformat(),
            "data": nimbus_event["result"]
        })
```

### 3. 文件操作工具映射

vibe-coding-ide 使用的工具:
- `edit_code` → nimbus `Edit`
- `create_file` → nimbus `Write`
- `delete_file` → nimbus `Bash("rm ...")`
- `sandbox_run` → nimbus `Bash`

## 实现计划

1. **Phase 1: 基础 API 兼容** (1-2 小时)
   - 实现 `/api/runs/` 端点
   - 实现 `/api/runs/{id}/events` SSE 端点
   - 事件格式转换

2. **Phase 2: 工具映射** (1 小时)
   - 映射 nimbus 工具到 vibe-coding-ide 期望的工具名
   - 或者前端修改工具显示名

3. **Phase 3: Sandbox 集成** (可选)
   - 代码执行 sandbox 集成
   - resume 端点实现

## 快速验证

1. 启动 nimbus serve (需要新增 vibe 兼容模式)
2. 修改前端 `API_BASE` 指向 nimbus
3. 测试基本聊天功能

## 参考

- 前端 SSE 处理: `frontend/src/hooks/useAgentStream.tsx`
- 后端 SSE 格式: `backend/src/sse.py`
- Agent 实现: `backend/src/agent/agent.py`
