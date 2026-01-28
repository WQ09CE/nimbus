# Nimbus v2.0 最小接口草案 (MVP 版)

> **Purpose**: 把最容易迷糊的几块（context / 队列&IPC / DAG / replanning / 异步 / permission）"钉死"
>
> **Date**: 2026-01-27
> **Status**: Draft (待讨论)
> **Updated**: 2026-01-27 - Thread 改为 Process（强调独立上下文语义）

---

## 设计原则

遵循现有分层：
- **Scheduler** 管 Task
- **vCPU** 跑 Step
- **MMU** 管 Process Context
- **Permission** 管 syscall

---

## 1. 三个执行单位：Process / Task / Step

### 1.1 Process（= vCPU 实例）

- 长生命周期
- 绑定一个 LLM core
- 拥有私有 MMU（memory tiers / context stack）
- **独立上下文**：每个 Process 有独立的对话历史/memory，不与其他 Process 共享

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ProcessState = Literal["READY", "RUNNING", "BLOCKED", "STOPPED", "FAILED"]

@dataclass(frozen=True)
class ProcessId:
    value: str

@dataclass
class ProcessConfig:
    role: str
    llm_core_id: str                 # 绑定哪个 LLM Core
    allowed_tools: list[str]         # tool allowlist（逻辑层）
    capability_id: str               # 绑定 capability token（系统调用权限）

@dataclass
class ProcessInfo:
    pid: ProcessId
    config: ProcessConfig
    state: ProcessState = "READY"
    current_task_id: Optional[str] = None
```

### 1.2 Task（Scheduler 调度的单元）

- 可取消、可重试、可 checkpoint
- 被分配到某个 process 上执行
- 对 DAG 而言，节点就是 Task

```python
TaskState = Literal["PENDING", "READY", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]

@dataclass(frozen=True)
class TaskId:
    value: str

@dataclass
class TaskSpec:
    process_role: str                # 期望跑在哪个 role（或留空让 scheduler 选）
    goal: str                        # 目标（Compute）
    input: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)  # token/time/tool_calls 等

@dataclass
class TaskRecord:
    task_id: TaskId
    spec: TaskSpec
    state: TaskState = "PENDING"
    assigned_process: Optional[ProcessId] = None
    depends_on: list[TaskId] = field(default_factory=list)
    result_ref: Optional[str] = None  # artifact id / checkpoint id / result store key
    error: Optional[str] = None
```

### 1.3 Step（vCPU 的单步）

- Think → Act → Observe 的原子循环
- 只产出结构化事件和 ActionIR

```python
@dataclass(frozen=True)
class StepId:
    value: str

@dataclass
class StepSnapshot:
    task_id: TaskId
    pid: ProcessId
    step_id: StepId
    registers_hash: str         # 当前 prompt 的 hash（避免日志泄密）
    memory_cursor: str          # MMU 的一个轻量指针/版本号
    budget_remaining: dict[str, Any]
```

---

## 2. ActionIR v1：统一"指令"中间表示

避免 "工具 / spawn / ipc / plan" 各写一套格式，MVP 把一切都当 Action。

```python
ActionKind = Literal["TOOL_CALL", "SPAWN_PROCESS", "POST_IPC", "REQUEST_REPLAN", "NOOP"]

@dataclass
class ActionIRv1:
    version: str                 # 固定 "1"
    kind: ActionKind
    name: str                    # tool 名 / syscall 名
    args: dict[str, Any]         # 参数（必须可 JSON 序列化）
    meta: dict[str, Any] = field(default_factory=dict)
```

### 关键约束

- **ControlUnit 只能产 ActionIR**
- **执行 ActionIR 是 OS 层（Kernel/vCPU）的责任**
- **结果必须回到 ToolResult ABI**

---

## 3. ToolResult ABI v1：统一返回

给 DAG/IPC/回放/Debug 统一的返回格式。

```python
ResultStatus = Literal["OK", "ERROR", "CANCELLED", "TIMEOUT"]

@dataclass
class ArtifactRef:
    kind: Literal["FILE", "BLOB", "JSON", "DIFF"]
    uri: str                 # e.g. artifacts://hash..., workspace://path...
    summary: str = ""

@dataclass
class ToolResultV1:
    version: str            # "1"
    status: ResultStatus
    tool_name: str
    output: dict[str, Any] = field(default_factory=dict)     # 结构化输出
    artifacts: list[ArtifactRef] = field(default_factory=list)
    timing_ms: dict[str, int] = field(default_factory=dict)  # exec/queue/total
    cost: dict[str, Any] = field(default_factory=dict)       # tokens/$
    fault: Optional["FaultV1"] = None
```

---

## 4. Fault 分类学 v1

把"异常"变成结构化 Fault，InterruptHandler 依据 Fault 做策略。

```python
FaultDomain = Literal["LLM", "TOOL", "KERNEL", "PERMISSION", "RESOURCE"]
FaultCode = str

@dataclass
class FaultV1:
    version: str           # "1"
    domain: FaultDomain
    code: FaultCode
    message: str
    retryable: bool
    context: dict[str, Any] = field(default_factory=dict)
```

### 常见 Fault 示例

| Domain | Code | 说明 |
|--------|------|------|
| LLM | RATE_LIMIT | API 限流 |
| LLM | CONTEXT_TOO_LONG | 上下文超长 |
| LLM | BAD_FORMAT | 输出格式错误 |
| TOOL | NOT_FOUND | 工具不存在 |
| TOOL | EXEC_FAILED | 执行失败 |
| TOOL | INVALID_ARGS | 参数无效 |
| PERMISSION | DENIED | 权限拒绝 |
| RESOURCE | BUDGET_EXCEEDED | 预算超限 |
| RESOURCE | TIMEOUT | 超时 |

---

## 5. Replanning 机制

**核心原则**: vCPU 只"请求"，Kernel 才"决定"

### 5.1 vCPU 发请求（ActionIR）

ControlUnit 触发条件：工具失败次数、预算不足、依赖缺失、用户更新目标等。

```python
# ControlUnit 产出：
ActionIRv1(
    version="1",
    kind="REQUEST_REPLAN",
    name="need_replan",
    args={"reason": "tool_failed", "hint": "try alternative tool or spawn architect"},
)
```

### 5.2 Kernel 的 PlannerPipeline 输出"计划补丁"

MVP 不需要复杂 diff，先用"替换 DAG / 追加节点"两种就够。

```python
PlanPatchKind = Literal["APPEND_TASKS", "REPLACE_DAG", "CANCEL_TASKS"]

@dataclass
class PlanPatchV1:
    version: str          # "1"
    kind: PlanPatchKind
    payload: dict[str, Any]   # 例如新增 tasks 列表 / 新 dag / cancel ids
```

---

## 6. IPC 与消息队列

拆成 **EventStream**（观测）+ **IPCBus**（数据注入）

### 6.1 EventStream（只给观测，不参与决策）

最小事件集（UI/TUI 直接消费）：

```python
EventType = Literal[
    "TASK_CREATED", "TASK_ASSIGNED", "STEP_STARTED",
    "ACTION_EMITTED", "TOOL_STARTED", "TOOL_FINISHED",
    "FAULT_RAISED", "REPLAN_REQUESTED", "TASK_FINISHED"
]

@dataclass
class EventV1:
    version: str          # "1"
    type: EventType
    ts_ms: int
    task_id: Optional[str] = None
    process_id: Optional[str] = None
    step_id: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
```

### 6.2 IPCBus（用于 DAG 依赖注入）

DAG 依赖的"结果注入"统一走 IPCBus（或者写 artifact 再引用）。

```python
@dataclass
class IPCMessageV1:
    version: str          # "1"
    channel: str          # e.g. "task_result"
    key: str              # e.g. "t1.output"
    value_ref: str        # artifact id / result store key
    meta: dict[str, Any] = field(default_factory=dict)
```

### 关键规则

**跨 process 不共享 memory，只共享 ref（artifact/result key）**

---

## 7. Permission：Capability Token

把权限从"工具白名单"升级成"系统调用能力"。

```python
@dataclass(frozen=True)
class CapabilityId:
    value: str

@dataclass
class CapabilityTokenV1:
    version: str               # "1"
    cap_id: CapabilityId
    fs: dict[str, Any]         # {"mode":"ro/rw", "allow_paths":[...], "deny_paths":[...]}
    exec: dict[str, Any]       # {"mode":"deny/allow", "allow_cmds":[...]}
    net: dict[str, Any]        # {"mode":"deny/allow", "allow_domains":[...]}
    tools: dict[str, Any]      # {"allow":[...], "deny":[...], "arg_rules":{...}}
    spawn: dict[str, Any]      # {"mode":"deny/allow", "max_processes":3}
```

### Syscall 校验点

Kernel 统一入口：
- 所有 Tool 执行/文件写/命令执行/spawn 都走 Kernel API
- Kernel 根据 `ProcessHandle.capability_id` 做校验
- **权限只能收缩不能放大**

---

## 8. Kernel / vCPU 最小协议

### 8.1 vCPU（Process Runtime）

vCPU 只负责执行 Task，内部跑 Step；需要 replan 就发事件/Action。

```python
class VCPU:
    def __init__(self, alu, isa, mmu, interrupt_handler, event_sink):
        self.alu = alu
        self.isa = isa
        self.mmu = mmu
        self.ih = interrupt_handler
        self.events = event_sink

    async def run_task(self, task: TaskRecord) -> ToolResultV1:
        """
        while not done:
            registers = mmu.assemble_context(task)
            action = await control_unit.think(...)
            result = await execute_action(action)  # kernel/vcpu
            observe + mmu.update
        """
        ...
```

### 8.2 Scheduler（DAG / 并发 / 取消 / IPC）

Scheduler 不关心 prompt；它关心 task 状态机和依赖。

```python
class Scheduler:
    async def submit_dag(self, dag) -> str: ...
    async def cancel_task(self, task_id: TaskId) -> bool: ...
    def on_task_finished(self, task: TaskRecord, result: ToolResultV1) -> None: ...
```

---

## 9. 落地路线（最小改动）

不用重写一切，按优先级做 4 步：

### Step 1: ActionIR + ToolResult + Fault（中枢）

- `AgenticRunner` 输出 ActionIR
- Tool 执行包装成 ToolResultV1
- Exception 全部映射成 FaultV1

### Step 2: Replanning 变成 REQUEST_REPLAN + PlannerPatch

- vCPU 只发 `need_replan`
- PlannerPipeline 生成 patch，Scheduler 应用 patch

### Step 3: 消息队列拆成 EventStream + IPCBus

- 先实现 EventStream（给 UI/日志）
- IPC 先只做 "task result ref 注入"

### Step 4: Permission 上升为 capability token

- `Kernel.exec_tool` / `fs_write` / `spawn` 统一 gate

---

## 10. 建议目录结构

```
nimbus/v2/
├── ir/
│   ├── __init__.py
│   ├── action_v1.py      # ActionIR
│   ├── result_v1.py      # ToolResultV1, ArtifactRef
│   └── fault_v1.py       # FaultV1
├── runtime/
│   ├── __init__.py
│   ├── types.py          # Process/Task/Step 类型
│   ├── vcpu.py           # vCPU 实现
│   └── scheduler.py      # Scheduler 实现
├── os/
│   ├── __init__.py
│   ├── events.py         # EventStream
│   ├── ipc.py            # IPCBus
│   └── capability.py     # CapabilityToken
└── planner/
    ├── __init__.py
    └── patch_v1.py       # PlanPatchV1
```

---

## 已决定的问题

### Q1: Process vs Thread 命名 ✅

**决定**: 使用 **Process**

**理由**:
- 语义准确：每个执行单位有**独立上下文**（对话历史/memory），这是 Process 语义
- 与现有代码一致：`kernel/proc.py` 已经用 `AgentProcess`
- spawn 开销不是问题：Agent OS 的 spawn 是 O(1)，没有传统 OS 的 fork 开销

---

## 待讨论问题

### Q2: Step 的 checkpoint 粒度

`StepSnapshot` 是为了 replay/debug 还是 save/restore？如果是后者可能需要更多信息。

### Q3: v1/v2 调用边界

`nimbus/v2/*` 并行落地时，CodeAgent (v1) 如何逐步切换到 v2 接口？

---

## 参考资料

- [The-Swarm-Corporation/AgentOS](https://github.com/The-Swarm-Corporation/AgentOS) - 类似项目参考（注：实际只是 Swarms 包装器，非真正 OS 实现）
- Nimbus 现有架构分析: `docs/architecture/VCPU_ARCHITECTURE_ANALYSIS.md`
