# Nimbus 快速入门

## 简介

Nimbus 是一个轻量级的 Agent 框架,支持并行任务执行、智能规划和多层级记忆管理。

## 安装

```bash
pip install -r requirements.txt
```

### 依赖项

- Python 3.11+
- anthropic (可选,用于 Claude LLM)
- pytest (开发依赖)
- pyyaml (配置文件)

## 基本使用

### 1. 创建简单 Agent

```python
import asyncio
from nimbus.core import NotebookAgent

class SimpleLLM:
    async def complete(self, prompt: str) -> str:
        # 实现您的 LLM 调用
        return "响应内容"

async def main():
    llm = SimpleLLM()
    agent = NotebookAgent(
        llm_client=llm,
        system_prompt="你是一个智能助手。"
    )

    # 运行对话
    response = await agent.run("你好!")
    print(response.text)

asyncio.run(main())
```

### 2. 使用配置文件创建 Agent

```python
from nimbus.core import AgentFactory

# 从 YAML 配置创建
agent = AgentFactory.create("agents/default.yaml")

# 或从字典创建
config = {
    "name": "My Agent",
    "llm": {"model": "claude-3-5-sonnet"},
    "skills": [{"name": "chat", "type": "builtin"}]
}
agent = AgentFactory.create_from_dict(config)
```

### 3. 基本对话示例

```python
# 简单问答
response = await agent.run("什么是 Python?")
print(response.text)

# 带上下文的多轮对话
response = await agent.run("介绍一下机器学习")
response = await agent.run("它有哪些应用场景?")  # 自动使用上下文
```

### 4. 文件上传与管理

```python
# 上传文件(文件内容会被固定在上下文中)
agent.on_file_upload(
    filename="data.csv",
    file_type="csv",
    summary="包含 1000 行销售数据"
)

# 现在可以询问关于文件的问题
response = await agent.run("分析一下 data.csv 的内容")

# 移除文件
agent.on_file_remove("data.csv")
```

### 5. 注册自定义 Skill

```python
# 定义自定义技能
async def custom_skill(query: str, context: str = "") -> str:
    # 实现您的技能逻辑
    return f"处理查询: {query}"

# 注册技能
agent.register_skill("my_skill", custom_skill)

# Agent 会自动在需要时调用该技能
response = await agent.run("使用自定义技能处理数据")
```

## 配置说明

### Memory 类型

**Simple Memory** (默认,向后兼容):
```python
agent = NotebookAgent(
    llm_client=llm,
    memory_type="simple"  # 简单的对话历史记录
)
```

**Tiered Memory** (高级,支持压缩和检查点):
```python
from nimbus.core import MemoryConfig

config = MemoryConfig(
    pinned_budget=1000,       # 固定内容的 token 预算
    working_budget=4000,      # 工作记忆预算
    episodic_budget=8000,     # 对话历史预算
    compression_threshold=6   # 6 轮对话后触发压缩
)

agent = NotebookAgent(
    llm_client=llm,
    memory_type="tiered",
    memory_config=config
)
```

### Planner 类型

**Simple Planner** (串行执行):
```python
agent = NotebookAgent(
    llm_client=llm,
    planner_type="simple"  # 任务按顺序执行
)
```

**DAG Planner** (并行执行,推荐):
```python
from nimbus.core import RuntimeConfig

runtime_config = RuntimeConfig(
    default_timeout=30,   # 单个任务超时(秒)
    max_retries=2,        # 失败重试次数
    max_concurrent=10     # 最大并行任务数
)

agent = NotebookAgent(
    llm_client=llm,
    planner_type="dag",
    runtime_config=runtime_config
)
```

## 流式响应

使用 `run_stream()` 获取实时状态更新:

```python
async for event in agent.run_stream("搜索并总结 AI 趋势"):
    if event["type"] == "task_start":
        print(f"开始任务: {event['skill']}")
    elif event["type"] == "task_done":
        print(f"完成任务: {event['task_id']}")
    elif event["type"] == "complete":
        print(f"最终结果: {event['content']}")
```

## 内置 Skills

OpenNotebook 默认提供以下内置技能:

- **chat**: 基本对话
- **search**: Web 搜索(需要实现)
- **summarize**: 文本摘要
- **keywords**: 关键词提取

## 日志与追踪

```python
# 启用日志(默认启用)
agent = NotebookAgent(
    llm_client=llm,
    enable_logging=True
)

# 查看 Memory 统计
stats = agent.get_memory_stats()
print(f"总 token 数: {stats['total_tokens']}")

# 查看追踪信息
trace = agent.get_trace_summary()
```

## 检查点与恢复

```python
# 使用 Tiered Memory 时支持检查点
agent = NotebookAgent(
    llm_client=llm,
    memory_type="tiered",
    session_id="my_session"
)

# 手动保存检查点
checkpoint_path = await agent.checkpoint()

# 恢复检查点
restored = await agent.restore_checkpoint()
if restored:
    print("成功恢复上次会话")
```

## 下一步

- 查看 [架构说明](./architecture.md) 了解系统设计
- 查看 [API 参考](./api-reference.md) 了解详细接口
- 查看 [Skill 开发指南](./skills-development.md) 创建自定义技能
- 查看 [高级用法](./advanced-usage.md) 学习 DAG 模式和 Re-planning
