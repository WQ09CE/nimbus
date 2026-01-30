# Pi Integration Strategy

> **状态**: 已实施方案 A（pi-ai HTTP 服务）

## 快速开始

```bash
# 1. 启动 pi-ai HTTP 服务
./scripts/start-pi-ai.sh &

# 2. 启动 nimbus server
uv run nimbus serve

# 3. 测试
curl http://localhost:3031/health
curl http://localhost:4096/health
```

## 现状分析

### 当前架构

```
┌─────────────┐     stdin/stdout      ┌─────────────┐      HTTP       ┌─────────┐
│   Nimbus    │◄────────────────────►│  pi-bridge  │◄───────────────►│  pi-ai  │
│   Server    │      JSON-RPC         │   (自己写)   │                 │  Cloud  │
└─────────────┘                       └─────────────┘                 └─────────┘
     Python                               TypeScript                    Anthropic
```

**问题**：
- pi-bridge 是我们自己写的，需要维护
- 只用了 pi-ai 的基础 LLM 调用能力
- 错过了 pi 的很多高级功能（compaction, retry, extensions, skills）

### Pi 提供的能力（我们没用上的）

| 能力 | 描述 | Nimbus 现状 |
|------|------|-------------|
| **Auto Compaction** | 自动压缩上下文，避免超出 token 限制 | 自己实现，较简单 |
| **Auto Retry** | 自动重试 429/5xx 错误，指数退避 | 无 |
| **Extensions** | 插件系统，可扩展工具和命令 | 无 |
| **Skills** | 领域知识注入 | 无 |
| **Session Management** | 会话持久化、分支、fork | 自己实现 |
| **Thinking Support** | 多级 thinking 支持 | 无 |
| **Model Cycling** | 模型切换和降级 | 无 |
| **OAuth** | 统一的认证管理 | 依赖 pi |

## 改进方案

### 方案 A: 使用 Pi RPC Mode（推荐 - 最小改动）

**核心思路**：直接使用 `pi --mode rpc` 替代 pi-bridge

```
┌─────────────┐     stdin/stdout      ┌─────────────┐
│   Nimbus    │◄────────────────────►│  pi --mode  │
│   Server    │      JSON Protocol    │    rpc      │
└─────────────┘                       └─────────────┘
     Python                              Pi (完整功能)
```

**优势**：
- ✅ 删除 pi-bridge.ts，不再需要维护
- ✅ 获得 Pi 的全部能力（compaction, retry, extensions）
- ✅ 协议已经稳定，有完整文档
- ✅ 改动最小

**实现示例**：

```python
# nimbus/v2/bridge/pi_rpc_client.py

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator, Any

@dataclass
class PiRpcClient:
    """使用 pi --mode rpc 的客户端"""
    
    def __init__(self):
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
    
    async def start(self):
        """启动 pi rpc 进程"""
        self._process = await asyncio.create_subprocess_exec(
            "pi", "--mode", "rpc", "--no-session",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 等待初始化
        await asyncio.sleep(0.5)
    
    async def prompt(self, message: str) -> AsyncIterator[dict]:
        """发送 prompt 并流式返回事件"""
        await self._send({"type": "prompt", "message": message})
        
        async for event in self._read_events():
            yield event
            if event.get("type") == "agent_end":
                break
    
    async def _send(self, cmd: dict):
        """发送命令"""
        self._request_id += 1
        cmd["id"] = self._request_id
        line = json.dumps(cmd) + "\n"
        self._process.stdin.write(line.encode())
        await self._process.stdin.drain()
    
    async def _read_events(self) -> AsyncIterator[dict]:
        """读取事件流"""
        while True:
            line = await self._process.stdout.readline()
            if not line:
                break
            yield json.loads(line.decode())
```

**改动清单**：
1. 新建 `pi_rpc_client.py`，使用 Pi RPC 协议
2. 修改 `PiLLMAdapter` 使用新客户端
3. 删除 `bridge/pi-bridge.ts`
4. 更新文档

### 方案 B: 深度集成 - Nimbus 作为 Pi Extension

**核心思路**：把 Nimbus 的 DAG 规划能力做成 Pi Extension

```
┌─────────────────────────────────────────┐
│                  Pi                      │
│  ┌─────────────────────────────────┐    │
│  │       Nimbus Extension           │    │
│  │  - DAG Planner                   │    │
│  │  - Multi-agent Runtime           │    │
│  │  - Custom Tools                  │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
```

**优势**：
- ✅ 复用 Pi 的所有基础设施
- ✅ 可以被 Pi 用户直接使用
- ✅ 统一的用户体验

**Extension 示例**：

```typescript
// nimbus-extension.ts
import { ExtensionFactory } from "@mariozechner/pi-coding-agent";

export const nimbusExtension: ExtensionFactory = (pi) => {
  // 注册 DAG 规划工具
  pi.registerTool({
    name: "plan_dag",
    label: "DAG Planner",
    description: "Create a DAG plan for complex tasks",
    parameters: Type.Object({
      goal: Type.String({ description: "Task goal" }),
    }),
    execute: async (toolCallId, params, onUpdate, ctx, signal) => {
      // 调用 Nimbus DAG 规划器
      const dag = await nimbusPlanner.plan(params.goal);
      return {
        content: [{ type: "text", text: JSON.stringify(dag) }],
        details: {},
      };
    },
  });

  // 注册 sub-agent 工具
  pi.registerTool({
    name: "spawn_agent",
    label: "Spawn Agent",
    description: "Spawn a sub-agent for parallel execution",
    parameters: Type.Object({
      role: Type.String({ description: "Agent role" }),
      task: Type.String({ description: "Task description" }),
    }),
    execute: async (toolCallId, params, onUpdate, ctx, signal) => {
      // 使用 Nimbus 的 sub-agent 系统
      const result = await nimbusRuntime.spawn(params.role, params.task);
      return {
        content: [{ type: "text", text: result }],
        details: {},
      };
    },
  });

  // 订阅 Pi 事件
  pi.on("agent_start", () => {
    console.log("[Nimbus] Agent started, ready for DAG planning");
  });
};
```

### 方案 C: 混合模式 - Nimbus 调用 Pi Agent

**核心思路**：Nimbus 保持独立，但使用 Pi Agent 作为 LLM 后端

```
┌─────────────────────────────────────────────────────────┐
│                      Nimbus Server                       │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │ Nimbus DAG  │───►│  Nimbus     │───►│  Pi Agent   │  │
│  │  Planner    │    │  Runtime    │    │  (子进程)   │  │
│  └─────────────┘    └─────────────┘    └─────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**优势**：
- ✅ Nimbus 保持独立架构
- ✅ 可以使用 Pi 的完整 agent 能力（不只是 LLM 调用）
- ✅ 可以并行运行多个 Pi Agent

**实现示例**：

```python
# nimbus/v2/agents/pi_agent.py

class PiAgentRunner:
    """运行 Pi Agent 作为子任务执行器"""
    
    def __init__(self):
        self.sessions: Dict[str, PiRpcClient] = {}
    
    async def run_task(
        self, 
        task: str, 
        tools: List[str] = None,
        cwd: str = None,
    ) -> str:
        """运行一个任务，返回结果"""
        client = PiRpcClient()
        await client.start(
            args=["--no-session"] + 
                 (["--cwd", cwd] if cwd else []) +
                 (["--tools", ",".join(tools)] if tools else [])
        )
        
        result = []
        async for event in client.prompt(task):
            if event["type"] == "message_update":
                delta = event.get("assistantMessageEvent", {})
                if delta.get("type") == "text_delta":
                    result.append(delta["delta"])
        
        await client.stop()
        return "".join(result)
    
    async def run_parallel(
        self, 
        tasks: List[str],
    ) -> List[str]:
        """并行运行多个任务"""
        return await asyncio.gather(*[
            self.run_task(task) for task in tasks
        ])
```

## 推荐实施路径

### 阶段 1: 切换到 Pi RPC Mode（1-2 天）

**目标**：删除 pi-bridge，使用 Pi 原生 RPC

1. 新建 `pi_rpc_client.py`
2. 修改 `PiLLMAdapter` 使用新客户端
3. 测试所有功能
4. 删除 `bridge/pi-bridge.ts`

**收益**：
- 减少维护成本
- 获得 auto-retry 和 auto-compaction

### 阶段 2: 利用 Pi 高级功能（1 周）

**目标**：使用 Pi 的 compaction、retry、thinking 等功能

1. 配置 auto-compaction（替代 Nimbus 的 TieredMemory）
2. 配置 auto-retry（处理 rate limit）
3. 添加 thinking level 支持
4. 添加 model cycling 支持

**收益**：
- 更稳定的 LLM 调用
- 更好的 token 管理

### 阶段 3: 混合模式探索（2 周）

**目标**：探索 Nimbus + Pi 的最佳集成方式

选项 1: **Nimbus 作为 Pi Extension**
- 把 DAG 规划做成 Pi 工具
- 可以被 Pi 用户直接使用

选项 2: **Pi Agent 作为 Nimbus 子执行器**
- Nimbus DAG 中的节点可以是完整的 Pi Agent
- 获得 Pi 的完整工具能力

## RPC 协议对比

### 当前 pi-bridge 协议（我们自己定义的）

```json
// 请求
{"jsonrpc": "2.0", "id": 1, "method": "ai.complete", "params": {...}}

// 响应
{"jsonrpc": "2.0", "id": 1, "result": {...}}
```

### Pi RPC 协议（官方的，更丰富）

```json
// 请求
{"type": "prompt", "message": "Hello", "id": "req-1"}

// 事件流
{"type": "agent_start"}
{"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "Hi"}}
{"type": "tool_execution_start", "toolName": "bash", "args": {...}}
{"type": "tool_execution_end", "result": {...}}
{"type": "agent_end", "messages": [...]}

// 响应
{"type": "response", "command": "prompt", "success": true, "id": "req-1"}
```

**Pi RPC 的优势**：
- 更丰富的事件类型
- 支持 abort、steer、follow_up
- 支持 compaction 和 retry 事件
- 支持 thinking 事件

## 配置对比

### 当前配置

```yaml
# llm.yaml
default_provider: anthropic
providers:
  anthropic:
    api_key: ${ANTHROPIC_API_KEY}
    model: claude-sonnet-4-20250514
```

### Pi 配置（更强大）

```json
// ~/.pi/agent/settings.json
{
  "compaction": {
    "enabled": true,
    "threshold": 0.8
  },
  "retry": {
    "enabled": true,
    "maxRetries": 3,
    "baseDelay": 1000
  },
  "model": {
    "provider": "anthropic",
    "id": "claude-sonnet-4-20250514"
  },
  "thinking": {
    "level": "medium"
  }
}
```

## 总结

| 方案 | 改动量 | 收益 | 推荐场景 |
|------|--------|------|----------|
| **A: Pi RPC Mode** | 小 | ⭐⭐⭐ | 立即实施，替换 pi-bridge |
| **B: Pi Extension** | 大 | ⭐⭐⭐⭐⭐ | Nimbus 融入 Pi 生态 |
| **C: 混合模式** | 中 | ⭐⭐⭐⭐ | 保持独立同时复用 Pi |

**建议**：
1. **立即**：实施方案 A，删除 pi-bridge，切换到 Pi RPC
2. **短期**：利用 Pi 的 auto-retry 和 auto-compaction
3. **长期**：探索方案 B 或 C，更深度集成
