# 高级用法

## DAG 模式详解

### DAG 的优势

**传统串行执行**:
```
搜索 Python (10s) → 搜索 Rust (10s) → 总结 (5s)
总耗时: 25s
```

**DAG 并行执行**:
```
搜索 Python (10s) ─┐
                   ├─→ 总结 (5s)
搜索 Rust (10s)   ─┘
总耗时: 15s
```

### DAG 执行流程

```python
# Agent 使用 DAG Planner
agent = NotebookAgent(
    llm_client=llm,
    planner_type="dag"  # 启用 DAG 模式
)

# 用户输入
response = await agent.run("同时搜索 Python 和 Rust 教程,然后总结")

# Planner 生成 DAG
# TaskDAG:
#   t1: search(query="Python 教程")
#   t2: search(query="Rust 教程")
#   t3: summarize(source=[t1, t2], depends_on=[t1, t2])

# Runtime 并行执行
# 1. t1 和 t2 同时开始 (无依赖)
# 2. t1, t2 完成后,t3 开始
# 3. 返回最终结果
```

### DAG 可视化

通过 `response.dag` 查看任务图:

```python
response = await agent.run("...")

dag = response.dag
for task_id, node in dag.nodes.items():
    print(f"{task_id}: {node.skill}")
    print(f"  依赖: {node.depends_on}")
    print(f"  状态: {node.status}")
    print(f"  耗时: {node.duration_ms}ms")
```

### 并行效率

```python
stats = response.dag.stats
print(f"并行效率: {stats.parallel_efficiency:.2f}x")
# parallel_efficiency = 串行总时长 / 实际耗时
# 3.0x 表示 3 倍加速
```

---

## Re-planning 机制

Re-planning 允许 Agent 在执行过程中动态调整计划。

### 启用 Re-planning

```python
from nimbus.core import AdaptivePlanner, ReplanningStrategy

# 创建 Adaptive Planner
planner = AdaptivePlanner(
    llm_client=llm,
    strategy=ReplanningStrategy.ON_CHECKPOINT  # 在检查点重新规划
)

# 注意: 当前版本需要手动集成,未来会支持配置启用
```

### Re-planning 策略

```python
class ReplanningStrategy(Enum):
    NONE = "none"                    # 不重新规划
    ON_FAILURE = "on_failure"        # 失败时重新规划
    ON_CHECKPOINT = "on_checkpoint"  # 检查点时重新规划
    ALWAYS = "always"                # 每个任务后重新规划
```

### 检查点任务

检查点任务在完成后会触发 Re-planning 评估:

```python
# 搜索任务自动标记为检查点
tasks = [
    {"id": "t1", "skill": "search", ...},  # 自动标记为检查点
    {"id": "t2", "skill": "summarize", "depends_on": ["t1"]}
]

# 执行流程:
# 1. t1 完成
# 2. 触发 Re-planning 评估
# 3. Planner 决定是否调整计划
#    - 继续执行 t2
#    - 或生成新计划 (如搜索结果建议不同方向)
```

### 自定义检查点

```python
tasks = [
    {
        "id": "t1",
        "skill": "analyze",
        "is_checkpoint": True  # 手动标记为检查点
    }
]
```

---

## Artifact 使用

### 生成 Artifact

Skill 返回包含 `artifact_type` 的字典:

```python
async def generate_chart(data: List[dict]) -> dict:
    return {
        "artifact_type": "chart",
        "id": "sales_chart",
        "title": "销售趋势图",
        "data": {
            "type": "line",
            "options": {"responsive": True},
            "series": [{"name": "Sales", "data": data}]
        },
        "mime_type": "application/json",
        "metadata": {"generated_at": "2025-01-21"}
    }
```

### 访问 Artifact

```python
response = await agent.run("生成销售报表")

# 获取所有 Artifact
for artifact in response.artifacts:
    print(f"ID: {artifact.id}")
    print(f"类型: {artifact.type}")
    print(f"标题: {artifact.title}")

# 按类型过滤
charts = response.get_artifacts_by_type(ArtifactType.CHART)
tables = response.get_artifacts_by_type(ArtifactType.TABLE)
```

### Artifact 类型

```python
class ArtifactType(str, Enum):
    FILE = "file"           # 文件 (PPT, PDF, Word)
    CHART = "chart"         # 图表配置
    CODE = "code"           # 代码块
    TABLE = "table"         # 表格数据
    IMAGE = "image"         # 图像
    MARKDOWN = "markdown"   # Markdown 文档
```

---

## Memory 高级管理

### Tiered Memory 详解

#### 四层架构

```python
from nimbus.core import MemoryConfig, TieredMemoryManager

config = MemoryConfig(
    pinned_budget=1000,      # Tier 1: 固定层
    working_budget=4000,     # Tier 2: 工作层
    episodic_budget=8000,    # Tier 3: 对话层
    semantic_budget=4000     # Tier 4: 语义层
)

agent = NotebookAgent(
    llm_client=llm,
    memory_type="tiered",
    memory_config=config
)
```

#### 1. Pinned Tier (固定层)

永不压缩的关键信息:

```python
from nimbus.core import PinnedItem

# 固定重要信息
item = PinnedItem(
    id="file:data.csv",
    type="file_meta",
    content="包含 1000 行销售数据",
    priority=10  # 高优先级
)

agent.memory.pin(item)

# 查看固定项
pinned = agent.memory.get_pinned()
```

#### 2. Working Tier (工作层)

当前任务状态:

```python
# 设置工作状态
agent.set_working_context("current_task", "分析销售数据")
agent.set_working_context("progress", 0.5)

# 获取工作状态
task = agent.get_working_context("current_task")

# 清空工作层
agent.memory.clear_working()
```

#### 3. Episodic Tier (对话层)

自动压缩的对话历史:

```python
# 对话自动添加
await agent.run("你好")  # 自动添加到 episodic

# 每 6 轮对话自动压缩
# 旧对话 → 摘要 → episodic_summaries

# 查看统计
stats = agent.get_memory_stats()
print(f"压缩次数: {stats['compression_count']}")
print(f"对话轮数: {stats['turn_count']}")
```

#### 4. Semantic Tier (语义层)

RAG 查询缓存:

```python
# 缓存 RAG 结果
agent.memory.cache_semantic(
    query="什么是机器学习?",
    results=["机器学习是...", "主要应用..."]
)

# 获取缓存
cached = agent.memory.get_semantic("什么是机器学习?")
```

### 检查点与恢复

```python
# 自动检查点 (每 5 轮)
agent = NotebookAgent(
    llm_client=llm,
    memory_type="tiered",
    memory_config=MemoryConfig(checkpoint_interval=5),
    session_id="user_123"
)

# 手动触发检查点
checkpoint_path = await agent.checkpoint()
print(f"已保存到: {checkpoint_path}")

# 恢复会话
agent = NotebookAgent(
    llm_client=llm,
    memory_type="tiered",
    session_id="user_123"
)
restored = await agent.restore_checkpoint()
if restored:
    print("会话已恢复")
```

### Memory 统计与监控

```python
stats = agent.get_memory_stats()

print(f"总 tokens: {stats['total_tokens']}")
print(f"固定层: {stats['pinned_tokens']} tokens")
print(f"工作层: {stats['working_tokens']} tokens")
print(f"对话层: {stats['episodic_tokens']} tokens")
print(f"语义层: {stats['semantic_tokens']} tokens")

# 检查是否超预算
if stats['total_tokens'] > 16000:
    print("⚠️ 超出 token 预算")
```

---

## 日志与追踪

### 结构化日志

```python
# 启用日志 (默认启用)
agent = NotebookAgent(
    llm_client=llm,
    enable_logging=True
)

# 日志自动记录:
# - agent_run_start
# - planner.create_plan
# - runtime.execute
# - task_start / task_done / task_failed
# - agent_run_complete
```

### 追踪信息

```python
# 获取追踪摘要
trace = agent.get_trace_summary()

print(f"总耗时: {trace['total_duration_ms']}ms")
print(f"规划耗时: {trace['planning_ms']}ms")
print(f"执行耗时: {trace['execution_ms']}ms")
```

---

## 流式响应高级用法

### 实时状态更新

```python
async for event in agent.run_stream("复杂任务"):
    event_type = event["type"]

    if event_type == "dag_start":
        print(f"开始执行 DAG: {event['goal']}")
        print(f"总任务数: {event['total_tasks']}")

    elif event_type == "task_start":
        print(f"▶ 开始任务 {event['task_id']}: {event['skill']}")

    elif event_type == "task_done":
        print(f"✓ 完成任务 {event['task_id']} ({event['duration_ms']}ms)")

    elif event_type == "task_failed":
        print(f"✗ 失败任务 {event['task_id']}: {event['error']}")

    elif event_type == "dag_complete":
        print(f"DAG 完成: {event['completed']} 成功, {event['failed']} 失败")

    elif event_type == "complete":
        print(f"最终结果: {event['content']}")
```

### 进度条集成

```python
from tqdm import tqdm

async def run_with_progress(user_input: str):
    total_tasks = 0
    completed = 0

    async for event in agent.run_stream(user_input):
        if event["type"] == "dag_start":
            total_tasks = event["total_tasks"]
            pbar = tqdm(total=total_tasks, desc="执行任务")

        elif event["type"] in ("task_done", "task_failed", "task_skipped"):
            completed += 1
            pbar.update(1)

        elif event["type"] == "complete":
            pbar.close()
            return event["content"]

result = await run_with_progress("复杂任务")
```

---

## 性能优化

### 1. 调整并发限制

```python
from nimbus.core import RuntimeConfig

config = RuntimeConfig(
    max_concurrent=20,  # 增加并发数 (默认 10)
    default_timeout=60  # 增加超时时间 (默认 30s)
)

agent = NotebookAgent(
    llm_client=llm,
    planner_type="dag",
    runtime_config=config
)
```

### 2. 优化 Memory 预算

```python
from nimbus.core import MemoryConfig

# 减少 token 使用
config = MemoryConfig(
    pinned_budget=500,        # 减少固定层
    episodic_budget=4000,     # 减少对话层
    compression_threshold=4   # 更早触发压缩
)

agent = NotebookAgent(
    llm_client=llm,
    memory_type="tiered",
    memory_config=config
)
```

### 3. Skill 缓存

```python
from functools import lru_cache

@lru_cache(maxsize=256)
def expensive_preprocessing(text: str) -> str:
    # 昂贵预处理
    return processed

async def optimized_skill(text: str) -> str:
    processed = expensive_preprocessing(text)
    return await llm.complete(processed)
```

### 4. 批量操作

```python
async def batch_skill(items: List[str]) -> List[str]:
    """批量处理多个项目"""
    async with httpx.AsyncClient() as client:
        tasks = [client.post(url, json={"text": item}) for item in items]
        responses = await asyncio.gather(*tasks)
        return [r.json()["result"] for r in responses]
```

---

## 错误处理与容错

### 任务失败处理

```python
response = await agent.run("可能失败的任务")

if response.is_error():
    print(f"错误: {response.error}")
else:
    print(f"成功: {response.text}")

# 检查 DAG 状态
if response.dag:
    stats = response.dag.stats
    if stats.failed > 0:
        print(f"失败任务: {stats.failed}")
        errors = response.dag.get_errors()
        for task_id, error in errors.items():
            print(f"  {task_id}: {error}")
```

### 优雅降级

```python
# DAG 模式自动降级
# - 部分任务失败时,返回部分结果
# - 完全失败时,返回错误提示

response = await agent.run("复杂任务")

if response.dag:
    if response.dag.stats.status == "partial":
        print("部分成功,部分失败")
        print(f"成功: {response.dag.stats.completed}")
        print(f"失败: {response.dag.stats.failed}")
```

### Retry 配置

```python
from nimbus.core import RuntimeConfig

config = RuntimeConfig(
    max_retries=3,      # 失败重试 3 次
    retry_delay=2.0     # 每次重试间隔 2 秒
)

agent = NotebookAgent(
    llm_client=llm,
    planner_type="dag",
    runtime_config=config
)
```

---

## 扩展示例

### 示例 1: 研究助手

```python
# 并行搜索多个来源,然后生成综合报告
agent = NotebookAgent(
    llm_client=llm,
    planner_type="dag",
    system_prompt="你是一个研究助手,擅长信息收集和综合分析。"
)

# 注册自定义 Skill
agent.register_skill("academic_search", academic_search_skill)
agent.register_skill("news_search", news_search_skill)
agent.register_skill("synthesize", synthesize_skill)

# 执行
response = await agent.run("研究 AI 在医疗领域的应用")

# DAG 自动并行搜索学术资源和新闻,然后综合
# t1: academic_search ─┐
# t2: news_search     ─┼─→ t3: synthesize
```

### 示例 2: 数据分析流水线

```python
agent = NotebookAgent(llm_client=llm, planner_type="dag")

agent.register_skill("load_csv", load_csv_skill)
agent.register_skill("clean_data", clean_data_skill)
agent.register_skill("analyze", analyze_skill)
agent.register_skill("visualize", visualize_skill)

response = await agent.run("分析 sales.csv 并生成可视化报告")

# DAG 执行流程:
# t1: load_csv → t2: clean_data → t3: analyze → t4: visualize

# 获取图表 Artifact
charts = response.get_artifacts_by_type(ArtifactType.CHART)
for chart in charts:
    render_chart(chart.data)
```

### 示例 3: 多语言翻译

```python
agent = NotebookAgent(llm_client=llm, planner_type="dag")

agent.register_skill("translate", translate_skill)

response = await agent.run("将这段文字翻译成英语、日语和法语")

# DAG 自动并行翻译
# t1: translate(target="en") ─┐
# t2: translate(target="ja") ─┼─→ t4: format_results
# t3: translate(target="fr") ─┘
```

---

## 最佳实践总结

### 1. 选择合适的模式

- **Simple 模式**: 简单对话、快速原型
- **DAG 模式**: 复杂任务、需要并行执行

### 2. Memory 管理

- 使用 Tiered Memory 处理长对话
- 定期检查 token 使用量
- 固定关键信息到 Pinned Tier

### 3. Skill 设计

- 保持 Skill 功能单一
- 返回结构化数据
- 处理错误并提供友好提示

### 4. 性能优化

- 增加并发限制 (max_concurrent)
- 使用缓存减少重复计算
- 批量处理相似任务

### 5. 监控与调试

- 启用日志记录
- 使用流式响应实时监控
- 检查 DAG 执行统计

---

## 相关文档

- [快速入门](./getting-started.md)
- [架构说明](./architecture.md)
- [API 参考](./api-reference.md)
- [Skill 开发指南](./skills-development.md)
