# Context Stack Architecture Design

> ADR-010: Context Stack for Focused LLM Calls

## Summary

设计 Context Stack 架构，类似 CPU 调用栈概念，解决 LLM 调用时 prompt 过长导致模型失焦的问题。通过栈式上下文管理，不同阶段（Planner、Tool 执行、Subagent）可以获得精简聚焦的上下文视图。

## Problem Statement

### 现状问题

1. **Prompt 过长**: 当前 LLM 调用 prompt 达到 5429 字符，包含过多无关信息
2. **上下文扁平**: TieredMemory 提供扁平的全量上下文，无法按阶段裁剪
3. **职责混淆**: Planner 不需要 workspace 信息，Tool 不需要对话历史

### 各阶段上下文需求

| 阶段 | 必需信息 | 不需要的信息 |
|------|----------|--------------|
| **Planner** | goal, available_skills, 简要历史 | workspace, 文件内容, 权限 |
| **Tool Execution** | workspace, 权限, 当前任务参数 | 对话历史, 其他任务 |
| **Subagent** | 父上下文子集, 受限权限, 任务描述 | 父级完整历史, 父级工作内存 |
| **Synthesize** | 上游结果, 用户问题, 对话历史 | 规划元数据, 权限信息 |

## Design

### 架构概述

```
┌─────────────────────────────────────────────────────────────┐
│                        CodeAgent                             │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │ TieredMemory    │  │  ContextStack   │                   │
│  │ (历史存储)       │←→│  (当前视图)      │                   │
│  │                 │  │                 │                   │
│  │ - Pinned        │  │  ┌───────────┐  │                   │
│  │ - Working       │  │  │ Frame 3   │ ← Stack Top         │
│  │ - Episodic      │  │  ├───────────┤  │                   │
│  │ - Semantic      │  │  │ Frame 2   │  │                   │
│  └─────────────────┘  │  ├───────────┤  │                   │
│                       │  │ Frame 1   │ ← Root Frame        │
│                       │  └───────────┘  │                   │
│                       └─────────────────┘                   │
└─────────────────────────────────────────────────────────────┘
```

### 核心组件

#### 1. ContextFrame (栈帧)

```python
@dataclass
class ContextFrame:
    """单个上下文栈帧。

    类似 CPU 调用栈帧，包含特定调用阶段的上下文信息。

    Attributes:
        id: 唯一帧标识
        name: 帧名称 (e.g., "planner", "tool:Read", "subagent:eye")
        purpose: 帧用途描述
        system_prompt: 该阶段的系统提示词
        tools: 该阶段可用的工具列表
        max_tokens: 上下文最大 token 数
        data: 自定义数据字典
        parent_id: 父帧 ID (用于继承链)
        inherit_from: 从父帧继承的字段列表
        created_at: 创建时间戳
    """
    id: str
    name: str
    purpose: str = ""
    system_prompt: str = ""
    tools: List[str] = field(default_factory=list)
    max_tokens: int = 2000
    data: Dict[str, Any] = field(default_factory=dict)
    parent_id: Optional[str] = None
    inherit_from: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def get(self, key: str, default: Any = None) -> Any:
        """获取帧数据。"""
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置帧数据。"""
        self.data[key] = value

    def derive(
        self,
        name: str,
        override: Optional[Dict[str, Any]] = None,
        inherit: Optional[List[str]] = None,
    ) -> "ContextFrame":
        """派生子帧，可覆盖部分字段并继承其他字段。

        Args:
            name: 子帧名称
            override: 要覆盖的字段
            inherit: 要继承的字段列表 (从 data 中)

        Returns:
            新的 ContextFrame 实例
        """
        override = override or {}
        inherit = inherit or []

        # 继承指定的 data 字段
        inherited_data = {k: v for k, v in self.data.items() if k in inherit}

        return ContextFrame(
            id=f"{self.id}:{uuid.uuid4().hex[:6]}",
            name=name,
            purpose=override.get("purpose", ""),
            system_prompt=override.get("system_prompt", self.system_prompt),
            tools=override.get("tools", list(self.tools)),
            max_tokens=override.get("max_tokens", self.max_tokens),
            data={**inherited_data, **override.get("data", {})},
            parent_id=self.id,
            inherit_from=inherit,
        )
```

#### 2. ContextStack (上下文栈)

```python
class ContextStack:
    """上下文栈管理器。

    管理 ContextFrame 的栈式结构，提供 push/pop 操作
    和 with 语法支持。

    Features:
    - 栈式帧管理 (push/pop)
    - 上下文管理器支持 (with 语法)
    - 与 TieredMemory 协作
    - 帧继承和派生
    - 视图生成 (当前帧的上下文字符串)
    """

    def __init__(
        self,
        memory: Optional[Union[SimpleMemory, TieredMemoryManager]] = None,
        max_depth: int = 10,
    ):
        """初始化上下文栈。

        Args:
            memory: 关联的内存管理器 (提供历史上下文)
            max_depth: 最大栈深度
        """
        self._stack: List[ContextFrame] = []
        self._memory = memory
        self._max_depth = max_depth

        # 创建 root frame
        self._root = self._create_root_frame()
        self._stack.append(self._root)

    def _create_root_frame(self) -> ContextFrame:
        """创建根帧。"""
        return ContextFrame(
            id="root",
            name="agent",
            purpose="Agent main execution context",
            max_tokens=8000,  # 根帧较大
        )

    @property
    def current(self) -> ContextFrame:
        """获取当前栈顶帧。"""
        return self._stack[-1]

    @property
    def depth(self) -> int:
        """当前栈深度。"""
        return len(self._stack)

    def push(self, frame: ContextFrame) -> None:
        """入栈新帧。

        Args:
            frame: 要入栈的帧

        Raises:
            ContextStackOverflow: 超过最大深度
        """
        if self.depth >= self._max_depth:
            raise ContextStackOverflow(
                f"Stack overflow: max depth {self._max_depth} exceeded"
            )

        # 设置父帧关系
        if frame.parent_id is None:
            frame.parent_id = self.current.id

        self._stack.append(frame)

    def pop(self) -> ContextFrame:
        """弹出栈顶帧。

        Returns:
            弹出的帧

        Raises:
            ContextStackUnderflow: 尝试弹出根帧
        """
        if self.depth <= 1:
            raise ContextStackUnderflow("Cannot pop root frame")

        return self._stack.pop()

    @contextmanager
    def frame(self, frame: ContextFrame) -> Iterator[ContextFrame]:
        """使用 with 语法管理帧生命周期。

        Example:
            async with context.frame(planner_frame):
                result = await llm.complete(...)
        """
        self.push(frame)
        try:
            yield frame
        finally:
            self.pop()

    def get_view(self, include_memory: bool = True) -> str:
        """生成当前帧的上下文视图。

        组装当前帧的上下文字符串，用于 LLM 调用。

        Args:
            include_memory: 是否包含来自 Memory 的上下文

        Returns:
            格式化的上下文字符串
        """
        parts = []
        frame = self.current

        # 1. 系统提示词
        if frame.system_prompt:
            parts.append(frame.system_prompt)

        # 2. 帧特定数据
        if frame.data:
            frame_data = "\n".join(
                f"- {k}: {v}" for k, v in frame.data.items()
                if not k.startswith("_")  # 跳过私有数据
            )
            if frame_data:
                parts.append(f"## Context\n{frame_data}")

        # 3. 可选: 来自 Memory 的上下文
        if include_memory and self._memory:
            memory_context = self._get_memory_context(frame)
            if memory_context:
                parts.append(memory_context)

        # 4. 可用工具
        if frame.tools:
            tools_list = ", ".join(frame.tools)
            parts.append(f"## Available Tools\n{tools_list}")

        # 组装并截断
        full_context = "\n\n".join(parts)
        return self._truncate_to_tokens(full_context, frame.max_tokens)

    def _get_memory_context(self, frame: ContextFrame) -> str:
        """从 Memory 获取适配当前帧的上下文。

        根据帧类型过滤和格式化 Memory 内容。
        """
        if self._memory is None:
            return ""

        # 根据帧名称决定包含哪些 Memory 内容
        frame_type = frame.name.split(":")[0]

        if frame_type == "planner":
            # Planner 只需要简要历史
            if isinstance(self._memory, TieredMemoryManager):
                summaries = self._memory.episodic_summaries[-2:]
                if summaries:
                    return "## Recent Context\n" + "\n".join(summaries)
            return ""

        elif frame_type == "tool":
            # Tool 需要 workspace
            if isinstance(self._memory, TieredMemoryManager):
                workspace_item = self._memory.memory_get("workspace")
                if workspace_item:
                    return f"## Workspace\n{workspace_item}"
            return ""

        elif frame_type == "synthesize":
            # Synthesize 需要完整对话历史
            return self._memory.get_context()

        else:
            # 默认: 获取常规上下文
            return self._memory.get_context()

    def _truncate_to_tokens(self, text: str, max_tokens: int) -> str:
        """截断文本到指定 token 数。"""
        from ..utils.tokens import estimate_tokens, truncate_to_tokens

        current_tokens = estimate_tokens(text)
        if current_tokens <= max_tokens:
            return text

        return truncate_to_tokens(text, max_tokens)

    def create_subagent_frame(
        self,
        subagent_type: str,
        task_prompt: str,
        allowed_tools: List[str],
    ) -> ContextFrame:
        """为 Subagent 创建隔离的栈帧。

        从当前帧派生一个受限的子帧，用于 Subagent 执行。

        Args:
            subagent_type: Subagent 类型 (eye, body, mind, etc.)
            task_prompt: 任务描述
            allowed_tools: 允许的工具列表

        Returns:
            为 Subagent 定制的 ContextFrame
        """
        # 继承的数据字段
        inherit_keys = ["workspace", "session_id"]

        # Subagent 类型特定配置
        subagent_configs = {
            "eye": {
                "purpose": "Code exploration and discovery",
                "max_tokens": 1500,
                "system_prompt": "You are an exploration agent. Focus on reading and understanding code.",
            },
            "body": {
                "purpose": "Code implementation",
                "max_tokens": 2000,
                "system_prompt": "You are a coding agent. Implement the requested changes.",
            },
            "mind": {
                "purpose": "Architecture design",
                "max_tokens": 2000,
                "system_prompt": "You are a design agent. Think through architecture decisions.",
            },
            "tongue": {
                "purpose": "Testing and verification",
                "max_tokens": 1500,
                "system_prompt": "You are a testing agent. Verify code correctness.",
            },
            "nose": {
                "purpose": "Code review",
                "max_tokens": 1500,
                "system_prompt": "You are a review agent. Analyze code quality and issues.",
            },
            "ear": {
                "purpose": "Requirements analysis",
                "max_tokens": 1000,
                "system_prompt": "You are a requirements agent. Clarify user needs.",
            },
        }

        config = subagent_configs.get(subagent_type, subagent_configs["eye"])

        return self.current.derive(
            name=f"subagent:{subagent_type}",
            override={
                "purpose": config["purpose"],
                "system_prompt": config["system_prompt"],
                "tools": allowed_tools,
                "max_tokens": config["max_tokens"],
                "data": {"task": task_prompt},
            },
            inherit=inherit_keys,
        )

    def get_stack_trace(self) -> List[Dict[str, Any]]:
        """获取当前栈的追踪信息 (用于调试)。"""
        return [
            {
                "id": frame.id,
                "name": frame.name,
                "depth": i,
                "max_tokens": frame.max_tokens,
                "tools_count": len(frame.tools),
                "data_keys": list(frame.data.keys()),
            }
            for i, frame in enumerate(self._stack)
        ]


class ContextStackOverflow(Exception):
    """栈溢出异常。"""
    pass


class ContextStackUnderflow(Exception):
    """栈下溢异常。"""
    pass
```

#### 3. 预定义帧工厂

```python
class FrameFactory:
    """预定义帧的工厂类。

    提供常用阶段的标准帧配置。
    """

    @staticmethod
    def planner(
        goal: str,
        available_skills: Set[str],
    ) -> ContextFrame:
        """创建 Planner 阶段帧。

        精简上下文，仅包含规划所需信息。
        """
        return ContextFrame(
            id=f"planner:{uuid.uuid4().hex[:6]}",
            name="planner",
            purpose="Task planning and DAG generation",
            system_prompt="""你是一个任务规划器。分析用户目标，生成执行计划。
只关注任务分解，不执行具体操作。""",
            tools=list(available_skills),
            max_tokens=500,  # Planner 帧精简
            data={"goal": goal},
        )

    @staticmethod
    def tool_execution(
        tool_name: str,
        params: Dict[str, Any],
        workspace: Path,
    ) -> ContextFrame:
        """创建 Tool 执行帧。"""
        return ContextFrame(
            id=f"tool:{tool_name}:{uuid.uuid4().hex[:6]}",
            name=f"tool:{tool_name}",
            purpose=f"Execute {tool_name} tool",
            tools=[tool_name],
            max_tokens=1000,
            data={
                "workspace": str(workspace),
                "params": params,
            },
        )

    @staticmethod
    def synthesize(
        message: str,
        upstream_results: Dict[str, Any],
    ) -> ContextFrame:
        """创建 Synthesize 阶段帧。

        包含较多上下文，用于生成最终响应。
        """
        return ContextFrame(
            id=f"synthesize:{uuid.uuid4().hex[:6]}",
            name="synthesize",
            purpose="Generate final response to user",
            system_prompt="根据收集到的信息，生成对用户问题的完整回答。",
            tools=[],  # Synthesize 不需要工具
            max_tokens=4000,  # Synthesize 需要较多上下文
            data={
                "message": message,
                "results": upstream_results,
            },
        )

    @staticmethod
    def context_analyzer() -> ContextFrame:
        """创建上下文分析帧。"""
        return ContextFrame(
            id=f"analyzer:{uuid.uuid4().hex[:6]}",
            name="analyzer",
            purpose="Analyze context dependencies",
            max_tokens=300,  # 分析器帧最精简
            data={},
        )
```

### 数据流

```
User Request
    │
    ▼
┌───────────────────────────────────────────────────────────────┐
│  Root Frame (agent)                                           │
│  - Full context budget: 8000 tokens                           │
│  - All tools available                                        │
└───────────────────────────────────────────────────────────────┘
    │
    │ context.push(FrameFactory.planner(...))
    ▼
┌───────────────────────────────────────────────────────────────┐
│  Planner Frame                                                │
│  - Reduced budget: 500 tokens                                 │
│  - Only: goal, skills list, brief history                     │
│  - No: workspace, file contents, permissions                  │
└───────────────────────────────────────────────────────────────┘
    │
    │ context.pop() → context.push(FrameFactory.tool_execution(...))
    ▼
┌───────────────────────────────────────────────────────────────┐
│  Tool Frame (Read)                                            │
│  - Budget: 1000 tokens                                        │
│  - Only: workspace, params, current task                      │
│  - No: conversation history, other tasks                      │
└───────────────────────────────────────────────────────────────┘
    │
    │ context.pop() → context.push(FrameFactory.synthesize(...))
    ▼
┌───────────────────────────────────────────────────────────────┐
│  Synthesize Frame                                             │
│  - Budget: 4000 tokens                                        │
│  - Includes: upstream results, user question, history         │
│  - No: planning metadata, permission info                     │
└───────────────────────────────────────────────────────────────┘
    │
    │ context.pop() → back to Root Frame
    ▼
Response to User
```

## Decisions

### Decision 1: 独立 ContextStack 类

- **决策**: 创建独立的 ContextStack 类，与 TieredMemory 并行协作
- **理由**:
  - 低耦合: 不修改现有 Memory 实现
  - 易测试: 可独立单元测试
  - 渐进式: 可逐步集成到各阶段
- **备选方案**:
  - 方案 B: 在 TieredMemory 内部嵌入栈
  - 方案 C: 组合模式包装 Memory
- **风险**: 需要在 Agent 中协调两个状态管理器

### Decision 2: 每帧独立 Token 预算

- **决策**: 每个 ContextFrame 有独立的 max_tokens 配置
- **理由**:
  - Planner 只需 500 tokens (精简聚焦)
  - Synthesize 需要 4000 tokens (完整响应)
  - 按需分配，避免统一开销
- **备选方案**: 统一预算动态分配
- **风险**: 需要调优各阶段的预算值

### Decision 3: 帧继承机制

- **决策**: 支持 derive() 方法从父帧派生子帧
- **理由**:
  - Subagent 需要继承 workspace、session_id
  - 避免重复传递公共信息
  - 支持选择性继承
- **备选方案**: 完全隔离，每次重新构造
- **风险**: 继承链过长可能导致复杂度增加

### Decision 4: with 语法自动管理

- **决策**: 使用 contextmanager 实现 with 语法
- **理由**:
  - 自动 push/pop，避免手动遗漏
  - 异常安全，确保帧正确弹出
  - Pythonic 风格
- **备选方案**: 显式 push/pop 调用
- **风险**: 需要确保 async with 支持

## Integration Points

### 1. 与 PlannerPipeline 集成

```python
# In pipeline.py
class PlannerPipeline:
    async def plan(
        self,
        goal: str,
        context_stack: ContextStack,  # 新增参数
        available_skills: Set[str],
    ) -> TaskDAG:
        # 创建 Planner 帧
        planner_frame = FrameFactory.planner(goal, available_skills)

        async with context_stack.frame(planner_frame):
            # 获取精简的上下文视图
            focused_context = context_stack.get_view()

            # Planner 只看到 500 tokens 的精简上下文
            for stage in self.stages:
                ctx = await stage.process(ctx)

        return ctx.final_dag
```

### 2. 与 AsyncRuntime 集成

```python
# In executor.py
class AsyncRuntime:
    async def _execute_task(
        self,
        node: TaskNode,
        context_stack: ContextStack,
    ) -> Any:
        # 创建 Tool 执行帧
        tool_frame = FrameFactory.tool_execution(
            tool_name=node.skill,
            params=node.params,
            workspace=self.workspace,
        )

        async with context_stack.frame(tool_frame):
            # Tool 只看到 workspace 和参数
            return await self._run_tool(node)
```

### 3. 与 Subagent 集成

```python
# In subagent.py
class SubagentExecutor:
    async def spawn(
        self,
        prompt: str,
        subagent_type: str,
        parent_context_stack: ContextStack,
    ) -> Dict[str, Any]:
        # 创建 Subagent 隔离帧
        subagent_frame = parent_context_stack.create_subagent_frame(
            subagent_type=subagent_type,
            task_prompt=prompt,
            allowed_tools=self._get_allowed_tools(subagent_type),
        )

        # 创建新的栈给 Subagent
        child_stack = ContextStack(memory=None, max_depth=3)
        child_stack.push(subagent_frame)

        # Subagent 使用隔离的栈
        result = await self._execute_with_stack(child_stack)
        return result
```

### 4. 与 TieredMemory 协作

```python
# In agent.py
class CodeAgent:
    def __init__(self, ...):
        # Memory 存储历史
        self.memory = TieredMemoryManager(...)

        # Stack 管理当前视图
        self.context_stack = ContextStack(memory=self.memory)

    async def run(self, user_input: str) -> AgentResponse:
        # Stack 通过 memory 参数访问历史
        # 但只在需要时才拉取，按帧类型过滤

        # 例如 Planner 帧只获取 episodic_summaries
        # 而 Synthesize 帧获取完整 get_context()
        pass
```

## Token Budget Guidelines

| 帧类型 | max_tokens | 包含内容 | 排除内容 |
|--------|------------|----------|----------|
| Root (agent) | 8000 | 所有 | - |
| Planner | 500 | goal, skills, brief history | workspace, files, permissions |
| Context Analyzer | 300 | goal only | 几乎所有 |
| Tool Execution | 1000 | workspace, params, task context | history, other tasks |
| Synthesize | 4000 | results, question, history | planning metadata |
| Subagent (eye) | 1500 | task, parent context subset | parent history |
| Subagent (body) | 2000 | task, workspace, permissions | parent history |

## Tradeoffs

1. **精简 vs 完整**: 选择精简帧（500 tokens for Planner），牺牲一些上下文换取聚焦性
2. **独立 vs 耦合**: 选择独立 ContextStack，增加协调复杂度换取低耦合
3. **预设 vs 动态**: 选择预设帧工厂，牺牲灵活性换取一致性和可预测性
4. **继承 vs 隔离**: 选择选择性继承，在便利性和隔离性之间平衡

## Constraints

- **技术约束**:
  - 必须与现有 TieredMemory 兼容
  - 必须支持 async/await 模式
  - 最大栈深度限制为 10
- **性能约束**:
  - get_view() 调用应 < 1ms
  - Token 估算使用现有 utils/tokens.py
- **安全约束**:
  - Subagent 帧必须严格限制工具权限

## Risks

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| Token 预算过于激进 | 中 | 高 | 可配置预算，提供调优指南 |
| 栈状态与 Memory 不一致 | 低 | 中 | 明确职责边界，单元测试 |
| 帧继承链过长 | 低 | 低 | 限制最大深度，警告日志 |
| 性能开销 | 低 | 低 | get_view() 懒计算，结果缓存 |

## Evidence

- **Sources**:
  - `src/nimbus/core/memory.py:90-107` - TieredMemoryManager 架构
  - `src/nimbus/core/planner/pipeline.py:89-163` - PlannerPipeline.plan() 方法
  - `src/nimbus/core/agent.py:462-494` - 当前上下文构造逻辑
  - `src/nimbus/tools/subagent.py:103-165` - SubagentContext 实现
  - `src/nimbus/core/planner/protocol.py:46-94` - PlanningContext 结构

- **Assumptions**:
  - 当前 prompt 5429 字符导致失焦（需要验证具体失焦表现）
  - 500 tokens 足够 Planner 阶段（需要实际测试调优）
  - Subagent 继承 workspace/session_id 足够（可能需要扩展）

## Next Steps

1. **Phase 1: 核心实现**
   - 实现 ContextFrame 和 ContextStack 类
   - 实现 FrameFactory 预定义帧
   - 添加单元测试

2. **Phase 2: Planner 集成**
   - 修改 PlannerPipeline.plan() 使用 ContextStack
   - 验证 Planner 帧 500 tokens 是否足够
   - 对比集成前后的 LLM 响应质量

3. **Phase 3: Tool/Subagent 集成**
   - 修改 AsyncRuntime._execute_task() 使用帧
   - 修改 SubagentExecutor.spawn() 使用隔离帧
   - E2E 测试验证

4. **Phase 4: 调优与监控**
   - 添加帧使用统计
   - 调优各阶段 token 预算
   - 添加 get_stack_trace() 调试支持

## Appendix: Example Usage

```python
# 完整使用示例
async def example_agent_run():
    # 初始化
    memory = TieredMemoryManager()
    context = ContextStack(memory=memory)

    # 1. Planner 阶段 - 精简上下文
    planner_frame = FrameFactory.planner(
        goal="读取 src/main.py 并分析结构",
        available_skills={"Read", "Grep", "synthesize"},
    )

    async with context.frame(planner_frame):
        view = context.get_view()  # ~500 tokens
        dag = await planner.plan_with_view(view)

    # 2. Tool 执行阶段 - 只有工作区和参数
    for node in dag.get_ready_tasks():
        tool_frame = FrameFactory.tool_execution(
            tool_name=node.skill,
            params=node.params,
            workspace=Path("/workspace"),
        )

        async with context.frame(tool_frame):
            view = context.get_view()  # ~1000 tokens
            result = await execute_tool(node, view)

    # 3. Synthesize 阶段 - 包含历史和结果
    synth_frame = FrameFactory.synthesize(
        message="读取 src/main.py 并分析结构",
        upstream_results=dag.get_results(),
    )

    async with context.frame(synth_frame):
        view = context.get_view()  # ~4000 tokens
        response = await llm.complete(view)

    return response
```
