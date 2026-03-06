# DAG Conditional Branching and Retry Loop Design

> ADR-007: Support conditional execution and retry loops in TaskDAG

## Status

Proposed

## Summary

设计 Nimbus DAG 执行引擎的条件分支和循环重试机制，支持"自动化测试编写员"等需要验证-修复-重试循环的场景。

## Context

### 目标场景: 自动化测试编写员

```
Step 1: Analysis (读取源代码)
Step 2: Test Gen (生成测试文件)
Step 3: Verification (运行 pytest)
如果 Step 3 失败 -> Step 4: Fix -> 回到 Step 3
最大重试 3 次
```

### 现有架构

| 组件 | 位置 | 现有能力 |
|------|------|----------|
| TaskNode | `types.py:228-386` | id, skill, params, depends_on, is_checkpoint, constraints |
| TaskDAG | `types.py:389-600` | 节点管理, get_ready_tasks, mark_downstream_skipped |
| AsyncRuntime | `runtime/executor.py` | 并行执行, 超时/重试, ReplanCoordinator 集成 |
| RulePlanner | `planner/rule_planner.py` | 正则匹配, DAG 生成, 参数模板 |
| ReplanCoordinator | `runtime/coordinator.py` | 运行时计划调整, 任务取消, 结果合并 |

### 关键发现

1. **已有 ReplanningStrategy.ON_FAILURE** (`legacy.py:638`): 系统已支持失败时触发重规划
2. **已有 ReplanRequest** (`legacy.py:644-690`): 包含 failed_task_id, failed_error 等字段
3. **已有 is_checkpoint 机制**: 在检查点触发重规划评估
4. **已有 constraints 字段**: 可扩展用于重试配置

## Design

### 架构概述

```
                    ┌──────────────────────────────────────────────┐
                    │              TaskDAG                         │
                    │  ┌─────┐    ┌─────┐    ┌─────────────────┐  │
                    │  │ t1  │───>│ t2  │───>│ t3 (verify)     │  │
                    │  │read │    │ gen │    │ on_failure: t4  │  │
                    │  └─────┘    └─────┘    │ max_retries: 3  │  │
                    │                        └────────┬────────┘  │
                    │                                 │ (failed)  │
                    │                        ┌────────▼────────┐  │
                    │                        │ t4 (fix)        │  │
                    │                        │ retry_target:t3 │  │
                    │                        └─────────────────┘  │
                    └──────────────────────────────────────────────┘
                                        │
                                        ▼
                    ┌──────────────────────────────────────────────┐
                    │            AsyncRuntime                      │
                    │  ┌────────────────────────────────────────┐  │
                    │  │          RetryController               │  │
                    │  │  - track retry counts per task         │  │
                    │  │  - inject fix task on failure          │  │
                    │  │  - reset target task for retry         │  │
                    │  └────────────────────────────────────────┘  │
                    └──────────────────────────────────────────────┘
```

### 数据结构设计

#### 1. TaskNode 扩展

```python
# src/nimbus/core/types.py - TaskNode 新增字段

@dataclass
class TaskNode:
    # ... 现有字段 ...

    # NEW: Failure handling
    on_failure: Optional[str] = None
    """
    Task ID to execute when this task fails.
    If set, instead of marking downstream as SKIPPED,
    execute the failure handler task first.

    Example: on_failure="t4_fix"
    """

    retry_target: Optional[str] = None
    """
    Task ID to retry after this (fix) task completes successfully.
    Used for fix-and-retry patterns.

    Example: retry_target="t3_verify"
    """

    max_retries: int = 0
    """
    Maximum number of retry attempts for this task.
    0 = no retry (default behavior).
    Works with on_failure to implement retry loops.
    """

    retry_count: int = 0
    """
    Current retry attempt counter (runtime state).
    Incremented each time the task is retried.
    """
```

#### 2. RetryLoopConfig (新增)

```python
# src/nimbus/core/types.py

@dataclass
class RetryLoopConfig:
    """Configuration for a retry loop in the DAG.

    Defines a verify-fix-retry loop pattern commonly used
    in test automation and self-healing workflows.

    Attributes:
        verify_task: Task ID of the verification step.
        fix_task: Task ID of the fix/repair step.
        max_attempts: Maximum total attempts (including initial).
        backoff_seconds: Delay between retries.
    """
    verify_task: str
    fix_task: str
    max_attempts: int = 3
    backoff_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verify_task": self.verify_task,
            "fix_task": self.fix_task,
            "max_attempts": self.max_attempts,
            "backoff_seconds": self.backoff_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetryLoopConfig":
        return cls(
            verify_task=data["verify_task"],
            fix_task=data["fix_task"],
            max_attempts=data.get("max_attempts", 3),
            backoff_seconds=data.get("backoff_seconds", 0.0),
        )
```

#### 3. TaskDAG 扩展

```python
# src/nimbus/core/types.py - TaskDAG 新增字段

@dataclass
class TaskDAG:
    # ... 现有字段 ...

    # NEW: Retry loop definitions
    retry_loops: List[RetryLoopConfig] = field(default_factory=list)
    """
    Retry loop configurations in this DAG.
    Each loop defines a verify-fix-retry pattern.
    """
```

### 执行流程设计

#### AsyncRuntime 修改

```python
# src/nimbus/core/runtime/executor.py

class AsyncRuntime:
    def __init__(self, ...):
        # ... 现有初始化 ...
        self._retry_counts: Dict[str, int] = {}  # NEW: 跟踪重试次数

    async def _execute_task(self, task: TaskNode, dag: TaskDAG) -> None:
        """Execute a single task with retry and failure handling."""
        # ... 现有代码到任务失败处 ...

        if task.status == TaskStatus.FAILED:
            # NEW: Check for on_failure handler
            if task.on_failure and self._can_retry(task, dag):
                await self._handle_task_failure(task, dag)
            else:
                # 原有行为：标记下游为 SKIPPED
                dag.mark_downstream_skipped(task.id)

    def _can_retry(self, task: TaskNode, dag: TaskDAG) -> bool:
        """Check if task can be retried."""
        current_count = self._retry_counts.get(task.id, 0)
        return current_count < task.max_retries

    async def _handle_task_failure(self, task: TaskNode, dag: TaskDAG) -> None:
        """Handle task failure by executing failure handler and retrying."""
        log = get_agent_logger("runtime", task_id=task.id)

        # Get failure handler task
        fix_task_id = task.on_failure
        fix_task = dag.nodes.get(fix_task_id)

        if not fix_task:
            log.warning(f"on_failure handler {fix_task_id} not found")
            dag.mark_downstream_skipped(task.id)
            return

        # Record retry attempt
        self._retry_counts[task.id] = self._retry_counts.get(task.id, 0) + 1
        task.retry_count = self._retry_counts[task.id]

        log.info(
            f"Task failed, executing handler: {fix_task_id} "
            f"(attempt {task.retry_count}/{task.max_retries})"
        )

        # Reset fix task status if it was executed before
        fix_task.status = TaskStatus.PENDING
        fix_task.result = None
        fix_task.error = None

        # Execute fix task
        await self._execute_task(fix_task, dag)

        if fix_task.status == TaskStatus.COMPLETED:
            # Fix succeeded, retry original task
            if fix_task.retry_target:
                target_id = fix_task.retry_target
            else:
                target_id = task.id

            target_task = dag.nodes.get(target_id)
            if target_task:
                log.info(f"Retrying task: {target_id}")
                # Reset target task for retry
                target_task.status = TaskStatus.PENDING
                target_task.result = None
                target_task.error = None
                # Will be picked up by get_ready_tasks in next iteration
        else:
            # Fix also failed, give up
            log.error(f"Fix task {fix_task_id} failed, giving up retry")
            dag.mark_downstream_skipped(task.id)
```

### 规则语法设计

#### PLANNING_RULES 扩展

```python
# src/nimbus/core/planner/rule_planner.py

PLANNING_RULES: List[Dict[str, Any]] = [
    # ... 现有规则 ...

    # ==========================================================================
    # Auto Test Writer Pattern
    # ==========================================================================
    {
        "name": "auto_test_writer",
        "pattern": r"^(?:为|给|对)\s*(.+?)\s*(?:写|编写|生成|创建)\s*(?:单元)?测试.*$",
        "mode": "dag",
        "tasks": [
            {
                "id": "t1_analyze",
                "skill": "Read",
                "params_template": {"file_path": "$1"},
            },
            {
                "id": "t2_generate",
                "skill": "write_test",
                "params_template": {"source": "$t1_analyze", "target_file": "$1"},
                "depends_on": ["$t1_analyze"],
            },
            {
                "id": "t3_verify",
                "skill": "Bash",
                "params_template": {"command": "pytest $1 --tb=short"},
                "depends_on": ["$t2_generate"],
                # NEW: Failure handling
                "on_failure": "t4_fix",
                "max_retries": 3,
            },
            {
                "id": "t4_fix",
                "skill": "fix_test",
                "params_template": {
                    "test_file": "$1",
                    "error": "$t3_verify.error",
                },
                # NEW: Retry target
                "retry_target": "t3_verify",
                # Initially inactive, only executed on failure
                "inactive": True,
            },
        ],
        # NEW: Retry loop metadata
        "retry_loops": [
            {
                "verify_task": "t3_verify",
                "fix_task": "t4_fix",
                "max_attempts": 3,
            }
        ],
    },

    # ==========================================================================
    # Code Fix Pattern (with retry)
    # ==========================================================================
    {
        "name": "code_fix_with_retry",
        "pattern": r"^(?:修复|fix)\s+(.+?)\s+(?:并|and)\s*(?:验证|verify|测试|test).*$",
        "mode": "dag",
        "tasks": [
            {
                "id": "t1_fix",
                "skill": "code_fix",
                "params_template": {"file_path": "$1"},
            },
            {
                "id": "t2_verify",
                "skill": "Bash",
                "params_template": {"command": "python -m pytest tests/ -x"},
                "depends_on": ["$t1_fix"],
                "on_failure": "t3_refix",
                "max_retries": 2,
            },
            {
                "id": "t3_refix",
                "skill": "code_fix",
                "params_template": {
                    "file_path": "$1",
                    "previous_error": "$t2_verify.error",
                },
                "retry_target": "t2_verify",
                "inactive": True,
            },
        ],
    },
]
```

#### 规则处理扩展

```python
# src/nimbus/core/planner/rule_planner.py - _create_dag_from_rule 修改

def _create_dag_from_rule(
    self,
    rule: Dict[str, Any],
    match: re.Match,
    ctx: PlanningContext,
) -> Optional[TaskDAG]:
    """Create a DAG from a matched rule."""
    # ... 现有代码 ...

    for i, task_template in enumerate(tasks_template):
        # ... 现有参数处理 ...

        # NEW: Process failure handling fields
        on_failure = task_template.get("on_failure")
        if on_failure and on_failure in task_id_map:
            on_failure = task_id_map[on_failure]

        retry_target = task_template.get("retry_target")
        if retry_target and retry_target in task_id_map:
            retry_target = task_id_map[retry_target]

        task = {
            "id": task_id,
            "skill": skill,
            "params": params,
            "depends_on": depends_on,
            "source": TaskSource.RULE.value,
            "confidence": 1.0,
            # NEW fields
            "on_failure": on_failure,
            "retry_target": retry_target,
            "max_retries": task_template.get("max_retries", 0),
            "inactive": task_template.get("inactive", False),
        }
        tasks.append(task)

    # NEW: Process retry loops
    retry_loops = []
    for loop_config in rule.get("retry_loops", []):
        verify_id = loop_config.get("verify_task")
        fix_id = loop_config.get("fix_task")
        if verify_id in task_id_map:
            verify_id = task_id_map[verify_id]
        if fix_id in task_id_map:
            fix_id = task_id_map[fix_id]

        retry_loops.append(RetryLoopConfig(
            verify_task=verify_id,
            fix_task=fix_id,
            max_attempts=loop_config.get("max_attempts", 3),
            backoff_seconds=loop_config.get("backoff_seconds", 0.0),
        ))

    dag = TaskDAG.create(ctx.goal, tasks)
    dag.retry_loops = retry_loops
    return dag
```

## Decisions

### Decision 1: 使用 TaskNode 扩展而非条件边

- **决策**: 在 TaskNode 中添加 `on_failure`、`retry_target`、`max_retries` 字段
- **理由**:
  1. 最小改动原则 - 扩展现有结构而非引入新类型
  2. 与现有 `depends_on` 语义一致 - 都是节点间关系
  3. 易于序列化/反序列化 - 直接在 to_dict/from_dict 中处理
  4. 声明式定义 - 在规则中易于表达
- **备选方案**:
  - ConditionalEdge 类型 - 更灵活但复杂度高
  - 状态机模式 - 过于重量级
- **风险**: `on_failure` 指向的任务可能不存在，需要验证

### Decision 2: 在 AsyncRuntime 中实现重试逻辑

- **决策**: 在 `_execute_task` 失败路径中处理 `on_failure` 和重试
- **理由**:
  1. 保持 DAG 不可变 - 运行时状态与计划分离
  2. 利用现有执行循环 - 复用 get_ready_tasks 机制
  3. 跟踪重试计数 - 运行时状态
- **备选方案**:
  - 在 ReplanCoordinator 中实现 - 但那是计划级别的
  - 新建 RetryController 类 - 可能过度设计

### Decision 3: inactive 标记用于条件激活任务

- **决策**: 添加 `inactive` 标记，表示任务初始不参与调度
- **理由**:
  1. 修复任务只在失败时激活
  2. 避免在 get_ready_tasks 中返回不应执行的任务
  3. 语义清晰
- **备选方案**:
  - 使用 SKIPPED 状态 - 但语义不同
  - 使用假依赖 - 会破坏依赖图

### Decision 4: 错误信息注入

- **决策**: 支持 `$t3_verify.error` 语法将错误信息注入修复任务参数
- **理由**:
  1. 修复任务需要知道失败原因
  2. 与现有 `$t1` 语法一致
  3. 运行时解析
- **备选方案**:
  - 通过上下文传递 - 需要更多基础设施

## Tradeoffs

| 权衡 | 选择 | 理由 |
|------|------|------|
| 灵活性 vs 简单性 | 简单性 | 只支持失败处理，不支持任意条件分支，覆盖 90% 场景 |
| 声明式 vs 命令式 | 声明式 | 在规则中声明重试配置，运行时自动处理 |
| DAG 不变性 vs 动态修改 | 不变性 | 通过任务状态重置实现重试，不修改 DAG 结构 |
| 集成深度 vs 解耦 | 适度集成 | 在 AsyncRuntime 中实现，不引入新组件 |

## Constraints

### 技术约束
- 必须与现有 TaskDAG.create() 兼容
- 必须与 ReplanCoordinator 共存
- 必须支持 DAG 序列化/反序列化
- 不能破坏现有测试

### 业务约束
- 最大重试次数必须有上限（防止无限循环）
- 重试必须有明确的终止条件
- 错误信息必须可追溯

### 性能约束
- 重试不应显著增加执行开销
- 重试计数应在 O(1) 时间访问

## Risks

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 无限循环 | 低 | 高 | max_retries 硬限制 + 全局安全阀 |
| on_failure 指向不存在任务 | 中 | 中 | DAGValidator 增加验证规则 |
| 重试与 Replan 冲突 | 中 | 中 | 重试发生在 Replan 决策之前 |
| 嵌套重试导致复杂状态 | 低 | 中 | 禁止嵌套：fix 任务不能有 on_failure |
| 并发执行中的状态竞争 | 低 | 高 | 使用锁保护 _retry_counts |

## Evidence

- TaskNode 现有字段: `types.py:228-386`
- AsyncRuntime 执行流程: `runtime/executor.py:286-393`
- ReplanningStrategy.ON_FAILURE: `legacy.py:638`
- RulePlanner 规则处理: `rule_planner.py:348-426`
- ReplanCoordinator: `runtime/coordinator.py:67-600`

## Assumptions

1. **假设**: 修复任务通常只依赖于失败任务的结果/错误
2. **假设**: 重试次数通常不超过 5 次
3. **假设**: 不需要支持任意条件分支（只需失败处理）
4. **假设**: 不需要支持嵌套重试循环

## Implementation Plan

### 文件改动清单

| 文件 | 改动类型 | 工作量 |
|------|----------|--------|
| `src/nimbus/core/types.py` | TaskNode 扩展 | 中 |
| `src/nimbus/core/types.py` | TaskDAG 扩展 | 小 |
| `src/nimbus/core/runtime/executor.py` | 重试逻辑 | 大 |
| `src/nimbus/core/planner/rule_planner.py` | 规则扩展 | 中 |
| `src/nimbus/core/planner/validator.py` | 验证规则 | 小 |
| `tests/test_dag.py` | 测试用例 | 中 |

### Phase 1: 数据结构 (1-2h)

1. 在 TaskNode 中添加:
   - `on_failure: Optional[str]`
   - `retry_target: Optional[str]`
   - `max_retries: int`
   - `retry_count: int`
   - `inactive: bool`

2. 更新 to_dict/from_dict

3. 添加 RetryLoopConfig 数据类

### Phase 2: 执行引擎 (2-3h)

1. AsyncRuntime 添加 `_retry_counts` 字典

2. 修改 `_execute_task` 失败处理逻辑

3. 实现 `_can_retry` 和 `_handle_task_failure`

4. 修改 `get_ready_tasks` 跳过 inactive 任务

### Phase 3: 规则支持 (1-2h)

1. 扩展规则语法支持新字段

2. 添加 auto_test_writer 规则

3. 添加 code_fix_with_retry 规则

### Phase 4: 验证器 (0.5-1h)

1. 验证 on_failure 指向存在的任务

2. 验证无嵌套重试

3. 验证 max_retries > 0 当 on_failure 存在

### Phase 5: 测试 (2-3h)

1. TaskNode 新字段测试

2. 重试循环执行测试

3. 边界条件测试（max_retries 达到、fix 任务失败等）

4. 规则匹配测试

## Example: auto_test_writer Rule

```python
# 完整规则定义示例

{
    "name": "auto_test_writer",
    "pattern": r"^(?:为|给|对)\s*(.+\.py)\s*(?:写|编写|生成|创建)\s*(?:单元)?测试.*$",
    "mode": "dag",
    "tasks": [
        {
            "id": "t1_read",
            "skill": "Read",
            "params_template": {"file_path": "$1"},
            "description": "读取源代码",
        },
        {
            "id": "t2_analyze",
            "skill": "analyze_code",
            "params_template": {"code": "$t1_read"},
            "depends_on": ["$t1_read"],
            "description": "分析代码结构",
        },
        {
            "id": "t3_generate",
            "skill": "generate_test",
            "params_template": {
                "code": "$t1_read",
                "analysis": "$t2_analyze",
                "output_file": "test_$1",
            },
            "depends_on": ["$t2_analyze"],
            "description": "生成测试代码",
        },
        {
            "id": "t4_verify",
            "skill": "Bash",
            "params_template": {
                "command": "python -m pytest test_$1 -v --tb=short"
            },
            "depends_on": ["$t3_generate"],
            "on_failure": "t5_fix",
            "max_retries": 3,
            "is_checkpoint": True,
            "description": "运行测试验证",
        },
        {
            "id": "t5_fix",
            "skill": "fix_test",
            "params_template": {
                "test_file": "test_$1",
                "source_file": "$1",
                "error": "$t4_verify.error",
                "previous_code": "$t3_generate",
            },
            "retry_target": "t4_verify",
            "inactive": True,
            "description": "修复测试错误",
        },
        {
            "id": "t6_report",
            "skill": "synthesize",
            "params_template": {
                "message": "测试编写完成",
                "test_file": "test_$1",
                "result": "$t4_verify",
            },
            "depends_on": ["$t4_verify"],
            "description": "生成报告",
        },
    ],
    "retry_loops": [
        {
            "verify_task": "t4_verify",
            "fix_task": "t5_fix",
            "max_attempts": 3,
            "backoff_seconds": 1.0,
        }
    ],
}
```

## Execution Flow Diagram

```
                            ┌─────────────────┐
                            │  Start DAG      │
                            └────────┬────────┘
                                     │
                            ┌────────▼────────┐
                            │  t1_read        │
                            │  (Read)         │
                            └────────┬────────┘
                                     │
                            ┌────────▼────────┐
                            │  t2_analyze     │
                            │  (analyze)      │
                            └────────┬────────┘
                                     │
                            ┌────────▼────────┐
                            │  t3_generate    │
                            │  (generate)     │
                            └────────┬────────┘
                                     │
                  ┌──────────────────┼──────────────────┐
                  │                  │                  │
         ┌────────▼────────┐        │                  │
         │  t4_verify      │◄───────┘                  │
         │  (Bash pytest)  │                           │
         │  max_retries: 3 │                           │
         └────────┬────────┘                           │
                  │                                    │
         ┌───────▼───────┐                            │
         │   Success?    │                            │
         └───────┬───────┘                            │
                 │                                    │
    ┌────────────┴────────────┐                      │
    │ Yes                     │ No                   │
    │                         │                      │
    │           ┌─────────────▼────────────┐        │
    │           │  retry_count < 3?        │        │
    │           └─────────────┬────────────┘        │
    │                         │                      │
    │            ┌────────────┴────────────┐        │
    │            │ Yes                     │ No     │
    │            │                         │        │
    │  ┌─────────▼─────────┐     ┌────────▼────────┐
    │  │  t5_fix           │     │  Mark Failed    │
    │  │  (fix_test)       │     │  Skip t6        │
    │  │  retry_target: t4 │     └─────────────────┘
    │  └─────────┬─────────┘
    │            │
    │            │ Success? ──► Reset t4, goto t4
    │            │
    │            │ Failed?  ──► Mark all failed
    │
    ▼
┌─────────────────┐
│  t6_report      │
│  (synthesize)   │
└────────┬────────┘
         │
         ▼
    [Complete]
```

## Next Steps

1. **@身 (body)**: 实现 TaskNode 扩展字段
2. **@身 (body)**: 实现 AsyncRuntime 重试逻辑
3. **@身 (body)**: 扩展 RulePlanner 支持新语法
4. **@舌 (tongue)**: 编写单元测试和集成测试
5. **@鼻 (nose)**: 代码审查

---

*Generated by 意分身 (Architect) - 2026-01-25*
