# OpenCode 子智能体与编排架构研究报告

> 研究日期: 2026-01-24
> 研究目的: 参考 OpenCode 框架的 subagent 和智能体编排实现，为 Nimbus 改进提供参考

## 1. 概述

OpenCode 是一个基于 TypeScript 的 AI 编程助手框架，实现了完整的子智能体(Subagent)机制和智能体编排系统。本报告深入分析其核心架构，为 Nimbus 框架的改进提供参考。

## 2. 核心架构

### 2.1 三层智能体架构

```
┌─────────────────────────────────────────┐
│           Primary Agent (build/plan)    │  ← 用户直接交互
├─────────────────────────────────────────┤
│    ↓ Task Tool 调用                      │
├─────────────────────────────────────────┤
│  Subagent (explore/general/自定义)       │  ← 独立 Session 隔离
└─────────────────────────────────────────┘
```

OpenCode 定义了三种智能体模式：

| 模式 | 说明 | 示例 |
|------|------|------|
| `primary` | 主智能体，与用户直接交互 | build, plan |
| `subagent` | 子智能体，通过 Task Tool 调用 | explore, general |
| `all` | 两者皆可 | 自定义 agent |

### 2.2 内置智能体

```typescript
// 来源: src/agent/agent.ts:72-197
{
  build: {
    mode: "primary",
    description: "主代码编写 agent",
    permission: { question: "allow", plan_enter: "allow" }
  },
  plan: {
    mode: "primary",
    description: "计划模式 agent",
    permission: { plan_exit: "allow", edit: { "*": "deny", "*.md": "allow" } }
  },
  general: {
    mode: "subagent",
    description: "通用研究 agent，用于执行多步骤任务"
  },
  explore: {
    mode: "subagent",
    description: "代码搜索专家，支持 quick/medium/very thorough 三种搜索深度",
    permission: { "*": "deny", grep: "allow", glob: "allow", read: "allow" }
  },
  compaction: { mode: "primary", hidden: true },  // 上下文压缩
  title: { mode: "primary", hidden: true },       // 标题生成
  summary: { mode: "primary", hidden: true }      // 摘要生成
}
```

## 3. Task Tool - 子智能体调度核心

### 3.1 接口定义

```typescript
// 来源: src/tool/task.ts:15-21
const parameters = z.object({
  description: z.string().describe("任务简述 (3-5 词)"),
  prompt: z.string().describe("详细任务描述"),
  subagent_type: z.string().describe("子智能体类型"),
  session_id: z.string().optional().describe("继续之前的会话"),
  command: z.string().optional().describe("触发此任务的命令")
})
```

### 3.2 核心执行流程

```typescript
// 来源: src/tool/task.ts:41-188 (简化版)
async execute(params, ctx) {
  // 1. 权限检查
  await ctx.ask({ permission: "task", patterns: [params.subagent_type] })

  // 2. 获取 agent 配置
  const agent = await Agent.get(params.subagent_type)

  // 3. 创建或恢复子会话
  const session = params.session_id
    ? await Session.get(params.session_id)
    : await Session.create({
        parentID: ctx.sessionID,
        title: `${params.description} (@${agent.name} subagent)`,
        permission: [
          // 禁止 subagent 嵌套调用 Task
          { permission: "task", pattern: "*", action: "deny" },
          // 禁止 Todo 操作
          { permission: "todowrite", pattern: "*", action: "deny" },
        ]
      })

  // 4. 执行子智能体
  const result = await SessionPrompt.prompt({
    sessionID: session.id,
    model: agent.model ?? ctx.model,
    agent: agent.name,
    parts: await SessionPrompt.resolvePromptParts(params.prompt)
  })

  // 5. 返回结果
  return {
    title: params.description,
    metadata: { sessionId: session.id, model },
    output: result.text + `\n\n<task_metadata>\nsession_id: ${session.id}\n</task_metadata>`
  }
}
```

### 3.3 关键特性

1. **独立会话隔离**: 每个 subagent 运行在独立 session 中
2. **权限继承与限制**: 子会话继承父会话权限，可额外限制
3. **防递归**: 自动禁止 subagent 嵌套调用 Task Tool
4. **状态恢复**: 支持 `session_id` 继续之前的执行
5. **结果追踪**: 返回 `session_id` 供后续交互使用

## 4. 并行任务执行

### 4.1 Batch Tool

```typescript
// 来源: src/tool/batch.ts:33-175
const parameters = z.object({
  tool_calls: z.array(z.object({
    tool: z.string(),
    parameters: z.record(z.string(), z.any())
  }))
})

async execute(params, ctx) {
  // 限制最多 25 个并发调用
  const toolCalls = params.tool_calls.slice(0, 25)

  // 并行执行所有工具
  const results = await Promise.all(toolCalls.map((call) => executeCall(call)))

  return {
    title: `Batch execution (${successfulCalls}/${results.length} successful)`,
    output: `All ${successfulCalls} tools executed successfully.`
  }
}
```

**限制**:
- 最大 25 个并发调用
- 禁止嵌套 batch、task 等危险工具
- 不支持 MCP 外部工具

### 4.2 LLM 多 Tool Call 并发

```
// 来源: src/tool/task.txt:18-19
Usage notes:
1. Launch multiple agents concurrently whenever possible, to maximize performance;
   to do that, use a single message with multiple tool uses
```

LLM 可在单条消息中输出多个 tool calls，框架自动并发执行。

## 5. 权限系统 (PermissionNext)

### 5.1 规则结构

```typescript
// 来源: src/permission/next.ts:13-20
const Rule = z.object({
  permission: z.string(),           // 权限类型: read, edit, bash, task...
  pattern: z.string(),              // 匹配模式: *, *.env, src/**/*.ts
  action: z.enum(["allow", "deny", "ask"])
})

type Ruleset = Rule[]
```

### 5.2 评估逻辑

```typescript
// 来源: src/permission/next.ts:231-238
export function evaluate(permission: string, pattern: string, ruleset: Ruleset) {
  // 从后向前匹配，最后一条规则生效
  const rule = ruleset.findLast(r =>
    r.permission === permission && match(pattern, r.pattern)
  )
  return rule?.action ?? "ask"
}
```

### 5.3 权限合并

```typescript
// 来源: src/permission/next.ts:240-260
export function merge(...rulesets: Ruleset[]): Ruleset {
  return rulesets.flat()  // 简单拼接，后来者优先
}
```

## 6. 会话循环 (Session Loop)

### 6.1 核心循环

```typescript
// 来源: src/session/prompt.ts:258-500 (简化版)
export async function loop(sessionID: string) {
  const abort = start(sessionID)
  let step = 0

  while (true) {
    // 1. 获取消息流
    let msgs = await MessageV2.filterCompacted(MessageV2.stream(sessionID))

    // 2. 查找待处理的子任务
    let tasks = msgs.flatMap(m => m.parts.filter(p =>
      p.type === "compaction" || p.type === "subtask"
    ))

    // 3. 处理子任务
    const task = tasks.pop()
    if (task?.type === "subtask") {
      await taskTool.execute({
        prompt: task.prompt,
        subagent_type: task.agent
      }, ctx)
      continue
    }

    // 4. 处理上下文压缩
    if (task?.type === "compaction") {
      await SessionCompaction.process({ messages: msgs, sessionID })
      continue
    }

    // 5. 调用 LLM
    const result = await llm.call(...)

    // 6. 检查完成条件
    if (result.finish !== "tool-calls") break

    step++
  }
}
```

### 6.2 关键特性

1. **持续执行**: 循环直到 `finish` 不是 `tool-calls`
2. **子任务队列**: 通过 `SubtaskPart` 跟踪嵌套任务
3. **自动压缩**: 检测 `CompactionPart` 触发上下文压缩
4. **中断控制**: 使用 `AbortController` 支持取消

## 7. 工具系统

### 7.1 工具定义接口

```typescript
// 来源: src/tool/tool.ts:7-88
export namespace Tool {
  export interface Info {
    id: string
    init: (ctx?: InitContext) => Promise<{
      description: string
      parameters: z.ZodObject<any>
      execute: (params: any, ctx: Context) => Promise<Result>
    }>
  }

  export interface Context {
    sessionID: string
    messageID: string
    agent: string
    abort: AbortSignal
    callID: string
    ask: (req: PermissionRequest) => Promise<void>
    metadata: (input: MetadataInput) => Promise<void>
  }

  export interface Result {
    title: string
    output: string
    metadata?: Record<string, any>
    attachments?: Attachment[]
  }
}

// 工厂方法
export function define(id: string, init: InitFn): Info {
  return { id, init }
}
```

### 7.2 内置工具列表

```typescript
// 来源: src/tool/registry.ts:96-117
const tools = [
  // 基础
  ReadTool, WriteTool, EditTool, BashTool, GlobTool, GrepTool,
  // 高级
  TaskTool, BatchTool, WebFetchTool, WebSearchTool, CodeSearchTool,
  // 特殊
  TodoWriteTool, TodoReadTool, QuestionTool, SkillTool,
  // 实验性
  LspTool, PlanEnterTool, PlanExitTool, ApplyPatchTool
]
```

## 8. 与 Nimbus 对比

| 特性 | OpenCode | Nimbus | 差距分析 |
|------|----------|--------|----------|
| Subagent | Task Tool + 独立 Session | 无 | 缺失核心能力 |
| 并行执行 | Batch Tool + LLM 多 tool | DAG Executor | 已有基础 |
| 权限控制 | PermissionNext (细粒度) | 基于技能 | 需要升级 |
| Agent 配置 | 动态配置文件 | 硬编码 | 需要配置化 |
| 上下文管理 | Compaction 自动压缩 | 无 | 缺失 |
| 模型切换 | 每个 agent 独立配置 | 全局配置 | 需要支持 |
| 工具注册 | 动态加载自定义工具 | 硬编码 | 需要改进 |

## 9. 对 Nimbus 的改进建议

### 9.1 优先级 P0 (核心能力)

#### 1. 实现 SubagentTool

```python
# 参考 task.ts 实现
class SubagentTool(BaseTool):
    name = "subagent"

    async def execute(self, params: SubagentParams, ctx: Context) -> Result:
        # 1. 创建子会话
        sub_session = await Session.create(parent_id=ctx.session_id)

        # 2. 获取 agent 配置
        agent = await Agent.get(params.agent_type)

        # 3. 执行子智能体
        result = await agent.run(
            prompt=params.prompt,
            session=sub_session,
            model=agent.model or ctx.model
        )

        return Result(
            output=result.text,
            metadata={"session_id": sub_session.id}
        )
```

#### 2. Agent 配置化

```yaml
# ~/.nimbus/agents/researcher.yaml
name: researcher
mode: subagent
description: "专业研究 agent，用于深度调研任务"
model:
  provider: gemini
  model: gemini-2.5-flash
permission:
  read: allow
  write: deny
  bash: deny
  websearch: allow
prompt: |
  你是一个专业的研究员，擅长深度调研和信息整合。
  请专注于收集准确的信息，不要进行任何修改操作。
```

### 9.2 优先级 P1 (效率提升)

#### 3. 实现 BatchTool

```python
class BatchTool(BaseTool):
    name = "batch"

    async def execute(self, params: BatchParams, ctx: Context) -> Result:
        # 限制最大并发数
        calls = params.tool_calls[:25]

        # 并行执行
        results = await asyncio.gather(
            *[self._execute_single(call, ctx) for call in calls],
            return_exceptions=True
        )

        return self._aggregate_results(results)
```

#### 4. 权限系统升级

```python
@dataclass
class PermissionRule:
    permission: str  # read, write, bash, subagent...
    pattern: str     # *, *.env, src/**/*.ts
    action: Literal["allow", "deny", "ask"]

def evaluate(permission: str, pattern: str, rules: list[PermissionRule]) -> str:
    # 从后向前匹配
    for rule in reversed(rules):
        if rule.permission == permission and fnmatch(pattern, rule.pattern):
            return rule.action
    return "ask"
```

### 9.3 优先级 P2 (体验优化)

#### 5. 上下文压缩

```python
class ContextCompactor:
    async def compact(self, messages: list[Message], max_tokens: int) -> list[Message]:
        if self._count_tokens(messages) <= max_tokens:
            return messages

        # 保留最近消息，压缩历史
        recent = messages[-5:]
        history = messages[:-5]

        summary = await self.llm.summarize(history)

        return [Message(role="system", content=f"历史摘要: {summary}")] + recent
```

#### 6. 每 Agent 独立模型

```python
@dataclass
class AgentConfig:
    name: str
    mode: str
    model: Optional[ModelConfig] = None  # 可覆盖全局配置

    def get_model(self, default: ModelConfig) -> ModelConfig:
        return self.model or default
```

## 10. 关键文件参考

| 功能 | 文件路径 | 关键行号 |
|------|----------|----------|
| Task Tool | `src/tool/task.ts` | 23-191 |
| Agent 定义 | `src/agent/agent.ts` | 72-197 |
| Batch Tool | `src/tool/batch.ts` | 33-175 |
| 权限系统 | `src/permission/next.ts` | 13-280 |
| 工具接口 | `src/tool/tool.ts` | 7-88 |
| 会话循环 | `src/session/prompt.ts` | 258-500 |
| 工具注册 | `src/tool/registry.ts` | 96-117 |
| Explore Prompt | `src/agent/prompt/explore.txt` | 1-19 |

## 11. 总结

OpenCode 的子智能体架构核心思想：

1. **Session 隔离**: 每个 subagent 独立会话，权限自动继承
2. **Task Tool 调度**: 统一的 subagent 调用接口
3. **防递归保护**: 自动禁止 subagent 嵌套调用
4. **并行优化**: Batch Tool + LLM 多 tool call
5. **配置化 Agent**: 支持自定义 agent 配置

这些模式可以直接应用于 Nimbus 框架的改进，实现更强大的多智能体协作能力。
