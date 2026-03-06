# OpenNotebook 架构说明

## 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      NotebookAgent                          │
│  (主入口 - 协调 Memory, Planner, Runtime/Executor)          │
└────────────┬────────────────────────────┬───────────────────┘
             │                            │
    ┌────────▼────────┐          ┌────────▼────────┐
    │  Memory Layer   │          │  Planning Layer │
    │  (上下文管理)     │          │  (任务规划)      │
    └────────┬────────┘          └────────┬────────┘
             │                            │
    ┌────────▼────────────────────────────▼────────┐
    │          Execution Layer                     │
    │  (任务执行 - Executor/Runtime)                │
    └────────┬──────────────────────────────────────┘
             │
    ┌────────▼────────┐
    │  Skills Layer   │
    │  (具体能力实现)   │
    └─────────────────┘
```

## 核心组件

### 1. NotebookAgent (核心协调器)

**职责**:
- 接收用户输入
- 协调 Memory、Planner、Executor/Runtime
- 返回结构化响应 (NotebookResponse)

**两种运行模式**:
- `run()`: 同步返回完整结果
- `run_stream()`: 流式返回中间状态

**关键方法**:
```python
async def run(user_input: str) -> NotebookResponse
async def run_stream(user_input: str) -> AsyncIterator[Dict]
def register_skill(name: str, func: SkillFunc)
def on_file_upload(filename, file_type, summary)
```

### 2. Memory (记忆管理)

#### SimpleMemory (基础版)

```
┌──────────────────────┐
│  Pinned Context      │  固定上下文 (文件元信息)
├──────────────────────┤
│  Conversation        │  最近 N 轮对话
│  History (FIFO)      │
└──────────────────────┘
```

#### TieredMemoryManager (高级版)

```
┌───────────────────────────────────────────────────────┐
│  Context Window (16K tokens)                          │
├───────────────────────────────────────────────────────┤
│  Pinned Tier      │ 1K  │ 永不压缩 (关键信息)         │
├───────────────────┼─────┼─────────────────────────────┤
│  Working Tier     │ 4K  │ 当前任务状态                 │
├───────────────────┼─────┼─────────────────────────────┤
│  Episodic Tier    │ 8K  │ 对话历史 + 自动压缩摘要      │
├───────────────────┼─────┼─────────────────────────────┤
│  Semantic Tier    │ 4K  │ RAG 缓存                    │
└───────────────────┴─────┴─────────────────────────────┘
```

**特性**:
- **自动压缩**: 每 N 轮对话自动压缩旧对话为摘要
- **检查点**: 支持保存/恢复会话状态
- **Token 预算管理**: 每层独立 token 限制

### 3. Planner (任务规划器)

#### SimplePlanner (串行模式)

```
用户输入 → LLM 规划 → Plan:
  - mode: "direct" → 直接回复
  - mode: "multi_step" → Task[] (串行执行)
```

**输出格式**:
```python
Plan(
    mode="multi_step",
    tasks=[
        Task(skill="search", params={"query": "..."}),
        Task(skill="summarize", params={"text": "..."})
    ]
)
```

#### DAGPlanner (并行模式)

```
用户输入 → LLM 规划 → TaskDAG:
  - nodes: {task_id: TaskNode}
  - dependencies: 每个 Node 记录 depends_on[]

示例 DAG:
    t1(search)  ──┐
                  ├─→ t3(summarize)
    t2(search)  ──┘
```

**优势**:
- 自动识别可并行任务
- 支持任务依赖关系
- 失败时自动跳过下游任务

**输出格式**:
```python
TaskDAG(
    goal="用户目标",
    nodes={
        "t1": TaskNode(skill="search", depends_on=[]),
        "t2": TaskNode(skill="search", depends_on=[]),
        "t3": TaskNode(skill="summarize", depends_on=["t1", "t2"])
    }
)
```

### 4. Runtime/Executor (执行引擎)

#### SimpleExecutor (串行执行)

```
for task in plan.tasks:
    result = await skill_func(**task.params)
```

#### AsyncRuntime (并行执行)

```
while not dag.is_completed():
    ready_tasks = dag.get_ready_tasks()  # 依赖已满足的任务
    await asyncio.TaskGroup:
        for task in ready_tasks:
            run task in parallel
```

**特性**:
- **并发控制**: Semaphore 限制最大并发数
- **超时处理**: 单任务超时自动重试
- **失败传播**: 失败任务自动标记下游为 SKIPPED
- **统计信息**: 并行效率、执行时长等

### 5. Skills (能力层)

**内置 Skills**:
- `chat`: 基于 LLM 的对话
- `search`: Web 搜索
- `summarize`: 文本摘要
- `keywords`: 关键词提取

**Skill 接口**:
```python
async def my_skill(**kwargs) -> Any:
    # 实现技能逻辑
    return result
```

## 数据流

### 标准对话流程

```
1. 用户输入 → NotebookAgent.run(user_input)
                ↓
2. 添加到 Memory.add_turn("user", user_input)
                ↓
3. 获取上下文 → context = Memory.get_context()
                ↓
4. 规划任务 → plan = Planner.create_plan(goal, context, skills)
                ↓
5. 执行任务 → result = Runtime.execute_dag(plan)
                ↓
6. 提取响应 → response_text = extract_response(result)
                ↓
7. 添加到 Memory.add_turn("assistant", response_text)
                ↓
8. 返回 NotebookResponse(text, artifacts, suggestions)
```

### DAG 并行执行流程

```
TaskDAG:
  t1 (search "Python")    ─┐
  t2 (search "Rust")      ─┼─→ t3 (summarize)

执行过程:
1. 初始: 所有节点 status = PENDING
2. 获取就绪任务: [t1, t2]  (depends_on = [])
3. 并行执行 t1, t2
4. t1, t2 完成 → status = COMPLETED
5. 获取就绪任务: [t3]  (depends_on=[t1, t2] 都已完成)
6. 执行 t3
7. t3 完成 → DAG 完成
```

### 失败处理

```
TaskDAG:
  t1 (search) ─→ t2 (summarize) ─→ t3 (export)

如果 t1 失败:
1. t1.status = FAILED, error = "..."
2. 调用 dag.mark_downstream_skipped(t1.id)
3. t2.status = SKIPPED, error = "upstream failed"
4. t3.status = SKIPPED, error = "upstream failed"
5. 返回 ExecutionResult(status="failed", errors={...})
```

## 设计决策

### 为什么使用 DAG 而非简单任务列表?

1. **并行性**: DAG 自然表达任务依赖,最大化并行执行
2. **灵活性**: 支持复杂的任务依赖关系 (扇入/扇出)
3. **容错性**: 失败时自动跳过下游任务,部分成功优于全失败
4. **可追踪**: 每个节点有独立状态,便于调试和监控

### 为什么分层 Memory?

1. **固定上下文**: 关键信息(文件、用户指令)永不丢失
2. **压缩历史**: 节省 token,支持长对话
3. **工作记忆**: 任务中间状态独立管理
4. **语义缓存**: RAG 查询结果复用

### 为什么支持两种模式 (Simple vs DAG)?

1. **向后兼容**: 简单场景不需要 DAG 开销
2. **渐进式**: 用户可从 Simple 模式平滑迁移到 DAG
3. **开发友好**: 调试时 Simple 模式更直观

## 扩展点

### 1. 自定义 Skill

```python
async def my_skill(param1: str, param2: int) -> dict:
    return {"result": "..."}

agent.register_skill("my_skill", my_skill)
```

### 2. 自定义 LLM Client

```python
class MyLLM:
    async def complete(self, prompt: str) -> str:
        # 调用您的 LLM API
        return response

agent = NotebookAgent(llm_client=MyLLM())
```

### 3. 自定义 Skill Loader

```python
def load_custom_skill(config: SkillConfig) -> SkillFunc:
    # 从配置加载技能
    return skill_func

AgentFactory.register_skill_loader("custom", load_custom_skill)
```

### 4. 自定义 Memory 压缩策略

继承 `TieredMemoryManager` 并重写 `_compress_episodic()`.

## 性能优化

### 并行效率

```python
# ExecutionStats 中的 parallel_efficiency
efficiency = serial_time / actual_time

示例:
  - 3 个任务,每个 10s
  - 串行: 30s
  - 并行: 10s
  - efficiency = 30 / 10 = 3.0 (理想并行)
```

### Token 管理

- Pinned: 1K (关键信息)
- Working: 4K (当前任务)
- Episodic: 8K (自动压缩)
- Semantic: 4K (RAG 缓存)

**总预算**: 16K tokens (适配 Claude 3.5 Sonnet)

## 相关文档

- [API 参考](./api-reference.md)
- [Skill 开发指南](./skills-development.md)
- [高级用法](./advanced-usage.md)
