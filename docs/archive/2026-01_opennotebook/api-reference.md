# API 参考手册

## NotebookAgent

主 Agent 类,协调记忆、规划和执行。

### 构造函数

```python
NotebookAgent(
    llm_client: LLMClient,
    system_prompt: str = "",
    memory_type: str = "simple",
    memory_config: Optional[MemoryConfig] = None,
    planner_type: str = "simple",
    runtime_config: Optional[RuntimeConfig] = None,
    enable_logging: bool = True,
    session_id: Optional[str] = None,
)
```

**参数**:
- `llm_client`: 实现 `async complete(prompt: str) -> str` 接口的 LLM 客户端
- `system_prompt`: 系统提示词
- `memory_type`: Memory 类型 (`"simple"` 或 `"tiered"`)
- `memory_config`: TieredMemoryManager 配置 (仅 `memory_type="tiered"` 时生效)
- `planner_type`: Planner 类型 (`"simple"` 或 `"dag"`)
- `runtime_config`: AsyncRuntime 配置 (仅 `planner_type="dag"` 时生效)
- `enable_logging`: 是否启用结构化日志
- `session_id`: 会话 ID (用于检查点)

### 核心方法

#### run()

```python
async def run(user_input: str) -> NotebookResponse
```

处理用户输入并返回响应。

**参数**:
- `user_input`: 用户消息或命令

**返回**: `NotebookResponse` 对象,包含:
- `text`: 主响应文本
- `artifacts`: 结构化产物列表
- `suggestions`: 后续操作建议
- `dag`: TaskDAG (仅 DAG 模式)
- `memory_stats`: Memory 统计信息

**示例**:
```python
response = await agent.run("搜索并总结 AI 趋势")
print(response.text)
for artifact in response.artifacts:
    print(f"产物: {artifact.title}")
```

#### run_stream()

```python
async def run_stream(user_input: str) -> AsyncIterator[Dict[str, Any]]
```

流式处理用户输入,实时返回状态更新。

**Yields**: 状态字典,包含以下类型:
- `{"type": "status", "content": "..."}`
- `{"type": "planning", "content": "..."}`
- `{"type": "task_start", "task_id": "...", "skill": "..."}`
- `{"type": "task_done", "task_id": "...", "result": "..."}`
- `{"type": "task_failed", "task_id": "...", "error": "..."}`
- `{"type": "dag_start", "dag_id": "...", "total_tasks": N}`
- `{"type": "dag_complete", "dag_id": "...", "completed": N, ...}`
- `{"type": "complete", "content": "..."}`

**示例**:
```python
async for event in agent.run_stream("分析数据"):
    if event["type"] == "task_start":
        print(f"开始: {event['skill']}")
    elif event["type"] == "complete":
        print(f"完成: {event['content']}")
```

#### register_skill()

```python
def register_skill(name: str, func: SkillFunc) -> None
```

注册自定义技能。

**参数**:
- `name`: 技能名称 (用于路由)
- `func`: 异步函数 `async def(**kwargs) -> Any`

**示例**:
```python
async def custom_skill(query: str) -> str:
    return f"处理: {query}"

agent.register_skill("custom", custom_skill)
```

#### on_file_upload()

```python
def on_file_upload(filename: str, file_type: str, summary: str) -> None
```

处理文件上传事件,将文件元信息固定到上下文。

**参数**:
- `filename`: 文件名
- `file_type`: 文件类型 (如 "pdf", "csv")
- `summary`: 文件内容摘要

#### on_file_remove()

```python
def on_file_remove(filename: str) -> None
```

处理文件移除事件。

**参数**:
- `filename`: 要移除的文件名

### 记忆管理方法

#### clear_memory()

```python
def clear_memory() -> None
```

清空对话历史 (保留固定内容和工作记忆)。

#### reset()

```python
def reset() -> None
```

完全重置 Agent 状态 (清空所有记忆)。

#### checkpoint()

```python
async def checkpoint() -> Optional[str]
```

手动触发检查点 (仅 Tiered Memory)。

**返回**: 检查点文件路径,如果不支持则返回 `None`。

#### restore_checkpoint()

```python
async def restore_checkpoint() -> bool
```

从最新检查点恢复 (仅 Tiered Memory)。

**返回**: 恢复成功返回 `True`,否则 `False`。

#### get_memory_stats()

```python
def get_memory_stats() -> Dict[str, Any]
```

获取 Memory 使用统计。

**返回**:
```python
{
    "type": "simple" | "tiered",
    "turn_count": int,
    "pinned_count": int,
    # Tiered Memory 额外字段:
    "pinned_tokens": int,
    "working_tokens": int,
    "episodic_tokens": int,
    "total_tokens": int,
    "compression_count": int
}
```

---

## AgentFactory

用于从配置创建 Agent 实例的工厂类。

### 类方法

#### create()

```python
@classmethod
def create(
    config_path: Union[str, Path],
    llm_client: Optional[Any] = None,
) -> NotebookAgent
```

从 YAML 配置文件创建 Agent。

**参数**:
- `config_path`: YAML 配置文件路径
- `llm_client`: (可选) 预配置的 LLM 客户端

**返回**: 配置好的 `NotebookAgent` 实例

**示例**:
```python
agent = AgentFactory.create("agents/default.yaml")
```

#### create_from_dict()

```python
@classmethod
def create_from_dict(
    config: Dict[str, Any],
    llm_client: Optional[Any] = None,
) -> NotebookAgent
```

从字典配置创建 Agent。

**参数**:
- `config`: 配置字典
- `llm_client`: (可选) 预配置的 LLM 客户端

**返回**: 配置好的 `NotebookAgent` 实例

**示例**:
```python
config = {
    "name": "My Agent",
    "llm": {"model": "claude-3-5-sonnet"},
    "planner_type": "dag",
    "skills": [{"name": "chat", "type": "builtin"}]
}
agent = AgentFactory.create_from_dict(config)
```

#### register_llm_factory()

```python
@classmethod
def register_llm_factory(
    name: str,
    factory: Callable[[LLMConfig], Any]
) -> None
```

注册自定义 LLM 客户端工厂。

**参数**:
- `name`: 工厂名称 (通常是模型前缀,如 "gpt")
- `factory`: 工厂函数 `(LLMConfig) -> LLMClient`

**示例**:
```python
def create_openai_client(config: LLMConfig):
    import openai
    return openai.AsyncOpenAI(api_key=os.getenv(config.api_key_env))

AgentFactory.register_llm_factory("gpt", create_openai_client)
```

#### register_skill_loader()

```python
@classmethod
def register_skill_loader(
    skill_type: str,
    loader: Callable[[SkillConfig], SkillFunc]
) -> None
```

注册自定义 Skill 加载器。

**参数**:
- `skill_type`: Skill 类型 (如 "wukong", "langchain")
- `loader`: 加载器函数 `(SkillConfig) -> SkillFunc`

**示例**:
```python
def load_custom_skill(config: SkillConfig) -> SkillFunc:
    # 从配置加载技能
    async def skill(**kwargs):
        return "结果"
    return skill

AgentFactory.register_skill_loader("custom", load_custom_skill)
```

---

## 配置类

### AgentConfig

完整的 Agent 配置。

**字段**:
```python
@dataclass
class AgentConfig:
    name: str                           # Agent 名称
    version: str = "1.0.0"              # 版本号
    llm: LLMConfig                      # LLM 配置
    memory: MemoryConfigSpec            # Memory 配置
    runtime: RuntimeConfigSpec          # Runtime 配置
    skills: List[SkillConfig]           # Skills 列表
    system_prompt: str = ""             # 系统提示词
    planner_type: str = "dag"           # Planner 类型
    enable_logging: bool = True         # 是否启用日志
```

**类方法**:
```python
@classmethod
def from_yaml(path: Union[str, Path]) -> AgentConfig

@classmethod
def from_dict(data: Dict[str, Any]) -> AgentConfig

def to_yaml(path: Union[str, Path]) -> None
```

### LLMConfig

LLM 客户端配置。

```python
@dataclass
class LLMConfig:
    model: str = "claude-3-5-sonnet"        # 模型标识
    temperature: float = 0.7                 # 采样温度
    max_tokens: int = 4096                   # 最大 tokens
    api_key_env: str = "ANTHROPIC_API_KEY"  # API Key 环境变量名
    base_url: Optional[str] = None           # 自定义 API 端点
```

### MemoryConfigSpec

Memory 管理器配置。

```python
@dataclass
class MemoryConfigSpec:
    type: str = "simple"                    # Memory 类型
    pinned_budget: int = 1000               # 固定层预算
    working_budget: int = 4000              # 工作层预算
    episodic_budget: int = 8000             # 对话层预算
    semantic_budget: int = 4000             # 语义层预算
    compression_threshold: int = 6          # 压缩阈值(轮数)
    checkpoint_interval: int = 5            # 检查点间隔(轮数)
    checkpoint_path: str = "./.checkpoints" # 检查点路径
```

### RuntimeConfigSpec

Runtime 执行器配置。

```python
@dataclass
class RuntimeConfigSpec:
    default_timeout: float = 30.0   # 单任务超时(秒)
    max_retries: int = 2             # 最大重试次数
    retry_delay: float = 1.0         # 重试延迟(秒)
    max_concurrent: int = 10         # 最大并发任务数
```

### SkillConfig

单个 Skill 配置。

```python
@dataclass
class SkillConfig:
    name: str                              # Skill 名称
    type: str = "builtin"                  # Skill 类型
    path: Optional[str] = None             # Skill 文件路径
    params: Dict[str, Any] = {}            # 默认参数
    enabled: bool = True                   # 是否启用
```

---

## 类型定义

### NotebookResponse

Agent 响应对象。

```python
@dataclass
class NotebookResponse:
    text: str                               # 主响应文本
    error: Optional[str] = None             # 错误信息
    artifacts: List[Artifact] = []          # 产物列表
    suggestions: List[str] = []             # 后续建议
    dag: Optional[TaskDAG] = None           # TaskDAG (仅 DAG 模式)
    memory_stats: Optional[Dict] = None     # Memory 统计

    def is_error() -> bool                  # 是否包含错误
    def has_artifacts() -> bool             # 是否包含产物
    def get_artifacts_by_type(type: ArtifactType) -> List[Artifact]
```

### Artifact

结构化产物。

```python
@dataclass
class Artifact:
    id: str                        # 唯一标识
    type: ArtifactType             # 产物类型
    title: str                     # 标题
    data: Any                      # 数据内容
    mime_type: Optional[str]       # MIME 类型
    url: Optional[str]             # 下载/访问 URL
    metadata: Dict[str, Any]       # 元数据
```

**ArtifactType**:
```python
class ArtifactType(str, Enum):
    FILE = "file"           # 文件
    CHART = "chart"         # 图表配置
    CODE = "code"           # 代码
    TABLE = "table"         # 表格
    IMAGE = "image"         # 图像
    MARKDOWN = "markdown"   # Markdown 文档
```

### TaskDAG

任务有向无环图。

```python
@dataclass
class TaskDAG:
    id: str                         # DAG ID
    goal: str                       # 用户目标
    nodes: Dict[str, TaskNode]      # 任务节点
    created_at: datetime            # 创建时间

    def get_ready_tasks() -> List[TaskNode]     # 获取就绪任务
    def is_completed() -> bool                   # 是否完成
    def get_results() -> Dict[str, Any]          # 获取所有结果
    def get_errors() -> Dict[str, str]           # 获取所有错误
```

### TaskNode

DAG 中的任务节点。

```python
@dataclass
class TaskNode:
    id: str                         # 任务 ID
    skill: str                      # 技能名称
    params: Dict[str, Any]          # 参数
    depends_on: List[str]           # 依赖的任务 ID
    status: TaskStatus              # 状态
    result: Optional[Any]           # 结果
    error: Optional[str]            # 错误信息
    started_at: Optional[datetime]  # 开始时间
    finished_at: Optional[datetime] # 结束时间
```

**TaskStatus**:
```python
class TaskStatus(Enum):
    PENDING = "pending"       # 等待执行
    RUNNING = "running"       # 执行中
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 失败
    SKIPPED = "skipped"       # 跳过(上游失败)
```

### ExecutionResult

DAG 执行结果。

```python
@dataclass
class ExecutionResult:
    dag_id: str                     # DAG ID
    status: str                     # "success" | "partial" | "failed"
    results: Dict[str, Any]         # 任务 ID -> 结果
    errors: Dict[str, str]          # 任务 ID -> 错误
    duration_ms: int                # 总执行时长(毫秒)
    stats: ExecutionStats           # 统计信息
```

### ExecutionStats

执行统计信息。

```python
@dataclass
class ExecutionStats:
    total_tasks: int                # 总任务数
    completed: int                  # 完成数
    failed: int                     # 失败数
    skipped: int                    # 跳过数
    total_duration_ms: int          # 总耗时
    parallel_efficiency: float      # 并行效率 (串行时间/实际时间)
```

---

## 内置 Skills

### chat

基本对话技能。

```python
async def chat(message: str, context: str = "") -> str
```

**参数**:
- `message`: 用户消息
- `context`: 上下文信息

**返回**: 对话响应

### search

Web 搜索 (需实现)。

```python
async def web_search(query: str) -> str
```

**参数**:
- `query`: 搜索查询

**返回**: 搜索结果摘要

### summarize

文本摘要。

```python
async def summarize_text(text: str, max_length: int = 200) -> str
```

**参数**:
- `text`: 待摘要文本
- `max_length`: 最大长度

**返回**: 摘要文本

### keywords

关键词提取。

```python
async def extract_keywords(text: str, count: int = 5) -> List[str]
```

**参数**:
- `text`: 待提取文本
- `count`: 关键词数量

**返回**: 关键词列表

---

## 便捷函数

### create_agent()

快速创建 Agent 的便捷函数。

```python
def create_agent(
    config: Union[str, Path, Dict[str, Any]],
    llm_client: Optional[Any] = None,
) -> NotebookAgent
```

**参数**:
- `config`: YAML 文件路径或配置字典
- `llm_client`: (可选) LLM 客户端

**返回**: `NotebookAgent` 实例

**示例**:
```python
from nimbus.core import create_agent

# 从 YAML
agent = create_agent("agents/default.yaml")

# 从字典
agent = create_agent({"name": "My Agent", "llm": {"model": "gpt-4"}})
```

---

## 完整示例

```python
import asyncio
from nimbus.core import NotebookAgent, AgentFactory, MemoryConfig, RuntimeConfig

# 方式 1: 直接创建
async def example1():
    agent = NotebookAgent(
        llm_client=MyLLM(),
        memory_type="tiered",
        memory_config=MemoryConfig(working_budget=4000),
        planner_type="dag",
        runtime_config=RuntimeConfig(max_concurrent=10)
    )

    response = await agent.run("搜索并总结 AI 趋势")
    print(response.text)

# 方式 2: 从配置创建
async def example2():
    agent = AgentFactory.create("agents/default.yaml")

    async for event in agent.run_stream("分析数据"):
        if event["type"] == "complete":
            print(event["content"])

asyncio.run(example1())
```
