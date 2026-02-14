# Nimbus vCPU 技术内幕

> 本文档基于 vcpu.py v0.2.0 (1,673 行) 及其 11 个子组件 (合计 4,195 行) 撰写。
> 面向需要理解、维护或扩展 vCPU 的工程师。

---

## 1. 概述

vCPU 是 Nimbus Agent 框架的执行引擎核心。它实现了 **Think-Act-Observe** 循环——类似 CPU 的取指-解码-执行-写回流水线，只不过"指令"来自 LLM，"执行"是调用工具。

一句话总结 vCPU 的职责：**把一个用户目标，通过反复调用 LLM 和工具，推进到完成。**

| 属性 | 值 |
|------|-----|
| 主文件 | `src/nimbus/core/runtime/vcpu.py` (1,673 行) |
| 子组件 | 11 个，分布在 `core/runtime/` 目录 |
| 总代码量 | 4,195 行 |
| 入口方法 | `execute(goal)` (高层) / `step()` (单步) |
| 调用者 | `AgentOS._run_process()` |

---

## 2. 架构总览

### 2.1 vCPU 内部流水线

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              vCPU.step()                                │
│                                                                         │
│   ┌──────────┐    ┌──────────────┐    ┌──────────┐    ┌──────────┐     │
│   │  1.THINK │    │  2.DECODE    │    │ 3.MEMORY │    │4.EXECUTE │     │
│   │          │ →  │              │ →  │  UPDATE  │ →  │          │     │
│   │ ALU调用  │    │ Pipeline     │    │ 写入MMU  │    │ Gate执行 │     │
│   │ (LLM)   │    │ + Decoder    │    │          │    │ (工具)   │     │
│   └──────────┘    └──────────────┘    └──────────┘    └──────────┘     │
│        ↑                                                    │           │
│        └────────────────────────────────────────────────────┘           │
│                         下一轮迭代                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 vCPU 与外部组件的关系

```
                    ┌─────────────┐
                    │   AgentOS   │  拥有并驱动 vCPU
                    │  (协调器)   │  处理 CONTEXT_OVERFLOW / 用户消息注入
                    └──────┬──────┘
                           │ vcpu.step() / vcpu.execute()
                           ▼
┌──────────────────────────────────────────────────────┐
│                        vCPU                           │
│                                                       │
│  ┌─────┐  ┌─────────┐  ┌─────────┐  ┌────────────┐  │
│  │ ALU │  │Pipeline  │  │ Decoder │  │ExecutionSt.│  │
│  │(LLM)│  │(中间件)  │  │(解析器) │  │  (状态)    │  │
│  └──┬──┘  └────┬────┘  └────┬────┘  └────────────┘  │
│     │          │            │                         │
│     │    ┌─────┴────────────┴──────┐                  │
│     │    │    防御子系统             │                  │
│     │    │ DoomLoop · ErrorHandler │                  │
│     │    │ Recovery · EmptyResult  │                  │
│     │    └─────────────────────────┘                  │
└─────┼────────────────────────────────────────────────┘
      │                    │                    │
      ▼                    ▼                    ▼
  ┌───────┐          ┌──────────┐         ┌─────────┐
  │  MMU  │          │   Gate   │         │ Tracer  │
  │(内存) │          │(权限执行)│         │(可观测) │
  └───────┘          └──────────┘         └─────────┘
```

### 2.3 协议接口

vCPU 通过两个 Protocol 定义与外部解耦：

```python
class LLMClient(Protocol):        # ALU 接口
    async def chat(messages, tools, on_chunk) -> LLMResponse

class LLMResponse(Protocol):      # ALU 返回值
    content: Optional[str]         # 文本内容
    tool_calls: Optional[List]     # 工具调用
```

任何实现了 `LLMClient` 的对象都可以作为 ALU 插入 vCPU，当前实现是 `PiLLMAdapter`。

---

## 3. 核心数据结构

### 3.1 VCPUConfig

```python
@dataclass
class VCPUConfig:
    max_iterations: int = 50           # 单轮最大 Think-Act-Observe 次数
    default_timeout: float = 60.0      # 工具执行超时（秒）
    max_consecutive_thoughts: int = 1  # ★ 关键：纯文本响应=最终答案
    max_sub_call_depth: int = 10       # SUB_CALL 最大递归深度
    compact_on_limit: bool = True      # 达到迭代上限时压缩而非停止
    max_compactions: int = 100         # 最大压缩次数（无限上下文的安全阀）
    pin_goal: bool = True              # 将用户目标置顶，压缩后不丢失
    goal_max_length: int = 500         # 目标超过此长度时用 LLM 摘要
    enable_tracing: bool = True        # 结构化执行追踪
```

**`max_consecutive_thoughts = 1` 是整个执行模型的关键设计决策**：LLM 一旦不调用工具而是直接输出文本，就被视为最终答案，循环立即终止。这防止了 Agent 退化为聊天机器人。

### 3.2 StepResult

每次 `step()` 调用返回一个 `StepResult`，它是单步执行的完整快照：

```python
@dataclass
class StepResult:
    actions: List[ActionIR] = []           # 本步解码出的指令
    results: List[ToolResult] = []         # 工具执行结果
    is_final: bool = False                 # 是否产生了最终结果
    final_result: Optional[ToolResult]     # 最终结果（如果 is_final）
    fault: Optional[Fault] = None          # 异常信息
    timing_ms: Dict[str, int] = {}         # 各阶段耗时 (think/decode/execute/total)
```

### 3.3 ExecutionState（集中式状态管理）

从最初散落在 vCPU 中的 15+ 个实例变量，重构为单一 dataclass：

```python
@dataclass
class ExecutionState:
    # 循环控制
    iteration: int = 0                     # 当前迭代次数
    is_running: bool = False               # 是否正在执行
    is_done: bool = False                  # 是否已完成

    # 连续计数器（用于异常检测）
    consecutive_thoughts: int = 0          # 连续纯文本响应次数
    consecutive_errors: int = 0            # 连续错误次数
    consecutive_empty_responses: int = 0   # 连续空响应次数

    # 资源计数
    compaction_count: int = 0              # 已执行的压缩次数
    tool_failure_counts: Dict[str, int]    # 每个工具的失败次数

    # 控制信号
    interruption_requested: bool = False   # 外部中断请求
```

提供 `on_thought()`, `on_action()`, `on_tool_success()`, `on_tool_failure()` 等状态转移方法，封装了计数器的更新逻辑。

---

## 4. 主循环：execute()

`execute(goal)` 是 vCPU 的高层入口。它包装了 `step()` 并处理所有循环控制逻辑。

### 完整流程

```
execute(goal)
│
├── 1. _reset()                           # 重置所有状态
├── 2. pin_goal(goal)                     # 目标置顶（长目标用 LLM 摘要）
├── 3. mmu.add_user_message(goal)         # 将目标作为首条用户消息
│
└── 4. while not is_done:                 # ← 主循环
        │
        ├── 检查 interruption_requested   → return CANCELLED
        │
        ├── 检查 iteration ≥ max_iterations
        │   ├── compaction_count ≥ max_compactions → return BUDGET_EXCEEDED
        │   ├── compact_on_limit=True → _do_compaction(), iteration=0, continue
        │   └── 否则 → return BUDGET_EXCEEDED
        │
        ├── step_result = await step()    # ← 执行一步
        │
        ├── 处理 fault:
        │   ├── 优雅终止条件（见下文）→ LLM 生成失败报告，return OK
        │   ├── 非可重试 fault → return ERROR
        │   └── 可重试 fault → 注入 ephemeral 错误消息，continue
        │
        └── step_result.is_final?
            └── Yes → return final_result
```

### 优雅终止条件

以下任一条件成立时，不返回硬错误，而是让 LLM 生成一份用户友好的失败报告：

| 条件 | 说明 |
|------|------|
| `consecutive_errors ≥ 5` | 连续 5 次出错，模型可能已经困惑 |
| `doom_loop_count ≥ 1` | 第一次 doom loop 就触发（不给第二次机会） |
| `fault.code == "DOOM_LOOP"` | 明确的死循环 |
| `fault.code == "EMPTY_RESPONSE_LOOP"` | LLM 反复返回空响应，已经卡死 |

优雅终止调用 `FailureReporter.generate_report()`，由 LLM 根据当前上下文生成自然语言的失败解释，返回 `status="OK"`（而非 ERROR），用户体验更好。

---

## 5. 单步执行：step()

`step()` 是 vCPU 的核心方法，实现一次完整的 Think-Act-Observe 循环。以下按 4 个阶段展开。

### 阶段 1: THINK（ALU 调用）

```python
# 前置检查
if interruption_requested:  return INTERRUPTED
if mmu.needs_compression(): return CONTEXT_OVERFLOW  # 交给 AgentOS 处理

# 准备上下文
pipeline.reset()
messages = mmu.assemble_context()       # 组装完整上下文

# 调用 LLM
response = await alu.chat(
    messages,
    tools=self.tools,
    on_chunk=on_think_chunk              # 流式回调 → SSE 推送
)

# 后处理
mmu.cleanup_ephemeral_messages()         # 清理上一轮的临时提示
```

**流式回调 `on_think_chunk`**：每个 chunk 经过 `pipeline.process_chunk()` 过滤后，以 `THINKING` 事件推送给前端。Pipeline 可能在流式阶段就拦截幻觉内容。

**空响应处理**（THINK 阶段末尾）：

```
LLM 返回空（无 content、无 tool_calls）
│
├── consecutive_empty_responses += 1
├── 注入 poke 消息: "[System] Your last response was empty. Please continue."
│   └── 标记为 ephemeral（下一轮成功后自动清理）
│
└── 连续 ≥ 5 次?
    └── Yes → 返回 EMPTY_RESPONSE_LOOP fault，step 结束
```

### 阶段 2: DECODE（Pipeline + 解码）

```python
actions = pipeline.process_response(response, decoder)
```

Pipeline 是一个中间件链，按顺序执行：

```
LLM Response
    │
    ▼
┌───────────────────────┐
│ HallucinationSanitizer│  ← 仅当 ModelFeatures.firewall_hallucinations=True
│ 过滤文本中的幻觉模式   │     (Gemini 需要，Claude 不需要)
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ MixedResponseSplitter │  ← 仅当 ModelFeatures.split_mixed_responses=True
│ 拆分 text+tools 混合  │     (Gemini 需要，Claude 不需要)
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│  InstructionDecoder   │  ← 始终执行
│  原始响应 → ActionIR  │
└───────────────────────┘
            │
            ▼
      List[ActionIR]
```

**幻觉处理的三级防御**：

| 级别 | 时机 | 处理 |
|------|------|------|
| 流式拦截 | `process_chunk()` | 实时过滤含幻觉模式的 chunk，不推送给前端 |
| 响应过滤 | `HallucinationSanitizer.process_response()` | 清除响应中的幻觉文本 |
| 解码拦截 | `InstructionDecoder.decode()` | 检测文本模拟的工具调用，抛出 `ILL_INSTRUCTION` |

**`ILL_INSTRUCTION` 异常处理**：

```
hallucination_count += 1

≥ 3 次?
├── Yes → 强制结束，返回错误提示（建议用户换模型或简化任务）
└── No  → 注入纠正消息（ephemeral）:
          "[System] INVALID RESPONSE — you wrote a tool call as text
           instead of using the function calling API..."
          → 返回，让下一轮 step 重试
```

### 阶段 3: MEMORY UPDATE（写回 MMU）

根据响应的类型，分三种情况写入内存：

| 情况 | 条件 | 写入方式 |
|------|------|----------|
| Split 响应 | `split_mixed_responses=True` 且有 thought + tools | 先写 thought 消息，再写 tool_calls 消息 |
| 纯工具调用 | `response.tool_calls` 存在 | `mmu.add_assistant_with_tool_calls(content, tool_calls)` |
| 纯文本 | 仅 `response.content` | `mmu.add_assistant_message(content)` |

### 阶段 4: EXECUTE（并行执行）

```python
results = await asyncio.gather(
    *(self._execute_action(action) for action in actions),
    return_exceptions=True
)
```

所有 actions **并行执行**，遵循 OpenAI parallel tool calls 范式。执行通过统一的 `_execute_action()` 路由到各个 handler：

```python
handlers = {
    "TOOL_CALL":       _handle_tool_call,      # 主要路径
    "RETURN":          _handle_return,          # 显式返回
    "THOUGHT":         _handle_thought,         # 纯文本 → 隐式返回
    "SUB_CALL":        _handle_sub_call,        # 子进程（模拟）
    "POST_IPC":        _handle_post_ipc,        # IPC（no-op）
    "REQUEST_REPLAN":  _handle_request_replan,  # 重规划（no-op）
    "CANCEL":          _handle_cancel,          # 取消执行
}
```

---

## 6. Action Handlers 详解

### 6.1 _handle_tool_call（最复杂的 handler）

这是 vCPU 中逻辑最密集的方法。完整流程：

```
_handle_tool_call(action)
│
├── 1. 工具名修正 (Tool Name Repair)
│   └── TOOL_NAME_CANONICAL: read→Read, bash→Bash, edit→Edit ...
│       仅修正内建工具，自定义工具透传
│
├── 2. Gate 执行
│   └── result = gate.syscall_tool(action)
│       Gate 负责权限检查、参数校验、超时控制
│
├── 3. Doom Loop 检测（执行后检查，非执行前）
│   └── doom_detector.check(name, args)
│       同一工具+同一参数连续 ≥3 次 → DOOM_LOOP fault
│       ★ 设计决策：执行后检测，避免 gate 的参数校验错误污染检测
│
├── 4. 错误恢复（在写入 MMU 之前）
│   ├── 有 fault → _handle_tool_error()
│   │   └── ErrorHandlerRegistry 决策恢复策略
│   │       → RecoveryExecutor 执行恢复动作
│   │
│   └── 成功但空结果 → _handle_empty_result()
│       └── 如 Grep 无匹配 → 自动 ls 辅助定位
│       ★ 设计决策：恢复在写入内存之前，失败尝试不污染上下文
│
├── 5. 写入 MMU
│   └── mmu.add_tool_result(tool_call_id, name, output)
│
└── 6. 重置 thought 计数器
    └── state.on_action()
```

### 6.2 _handle_thought（纯文本响应处理）

```
_handle_thought(action)
│
├── action.meta.non_blocking = True?    (来自 MixedResponseSplitter)
│   └── Yes → 返回 is_final=False      (这只是伴随工具调用的思考片段)
│
└── 标准 thought:
    ├── state.on_thought()              (consecutive_thoughts += 1)
    └── ≥ max_consecutive_thoughts?     (默认 1)
        └── Yes → _handle_return()      ★ 纯文本 = 最终答案
```

**这意味着**：在默认配置下，LLM 第一次不调用工具而直接输出文本，就被视为完成任务。这是 Agent 编程的正确范式——Agent 应该用工具获取信息，而不是向用户提问。

### 6.3 _handle_return（返回结果）

```python
# 兼容多种参数名
result = args.get("result")      # 显式 RETURN 工具调用
      or args.get("output")      
      or args.get("content")     # THOUGHT 隐式返回
      or args.get("text")
```

发射 `PROC_FINISHED` 事件，返回 `ToolResult(is_final=True)`。

### 6.4 其他 Handlers

| Handler | 状态 | 说明 |
|---------|------|------|
| `_handle_sub_call` | 模拟 | 返回 "Subroutine called (simulated)" |
| `_handle_post_ipc` | no-op | IPC 功能已移除 (YAGNI)，保留接口兼容 |
| `_handle_request_replan` | no-op | Replan 功能已移除 (YAGNI)，保留接口兼容 |
| `_handle_cancel` | 活跃 | 设置 `is_done=True`，返回 CANCELLED |

---

## 7. 四层防御机制

vCPU 实现了 4 层递进的防御体系，处理 LLM 的各种不可靠行为。

### 7.1 第一层：Doom Loop 检测器

**组件**: `DoomLoopDetector` (212 行)

**问题**：LLM 反复用相同参数调用同一工具（如不断 Read 同一个不存在的文件）。

**机制**：
- 记录最近的 tool+args 调用序列
- 同一工具+同一参数连续出现 ≥ `DOOM_LOOP_THRESHOLD`(3) 次 → 判定为死循环
- 返回 `DOOM_LOOP` fault + 针对该工具的指导信息

**特殊设计**：第一次 doom loop 就触发优雅终止。不给 LLM 第二次陷入死循环的机会。

### 7.2 第二层：幻觉防火墙

**组件**: `ResponsePipeline` (215 行) + `HallucinationSanitizer` + `InstructionDecoder` (219 行)

**问题**：某些模型（尤其 Gemini）会在文本中模拟工具调用，而不是使用 function calling API。

**检测模式**：
```python
HALLUCINATION_PATTERNS = [
    "[Called", "[Calling", "[Tool:", "[Execute:",
    "```tool", "<tool_call>", "<function_call>",
    "[Historical context:",       # GPT-5.3/Gemini 特有
    "Do not mimic this format",
]
```

**三级拦截**：
1. **流式拦截** — `process_chunk()` 实时过滤，不推给前端
2. **响应清洗** — `HallucinationSanitizer` 从最终响应中剥离幻觉文本
3. **解码拦截** — `InstructionDecoder` 检测文本模拟的工具调用，抛 `ILL_INSTRUCTION`

**由 `ModelManifest` 的 feature flags 控制**：Claude 不需要幻觉防火墙，Gemini 需要。避免了 `if model == "gemini"` 式的硬编码。

### 7.3 第三层：错误恢复系统

**组件**: `ErrorHandlerRegistry` (627 行) + `RecoveryExecutor` (219 行)

**问题**：工具执行失败后，LLM 可能不知道如何修正。

**4 种恢复策略**：

| 策略 | 说明 | 示例 |
|------|------|------|
| `skip` | 不干预，让 LLM 自行处理 | 超时等无法自动恢复的情况 |
| `inject_hint` | 注入提示消息 | "文件不存在，请检查路径" |
| `auto_tool` | 自动执行恢复工具 | 文件找不到 → 自动 `ls` 列目录 |
| `modify_args` | 修改参数后重试 | 路径修正 |

**渐进式恢复**：同一工具的第 1、2、3 次失败采用不同强度的恢复策略。

**关键设计**：错误恢复在 **写入 MMU 之前** 执行。如果恢复成功，失败的原始结果不会进入上下文，避免污染后续推理。

### 7.4 第四层：空响应处理

**组件**: `EmptyResultHandler` (122 行) + vCPU 内建逻辑

**两个层面**：

| 类型 | 说明 | 处理 |
|------|------|------|
| LLM 空响应 | `response.content=None, tool_calls=None` | Poke 消息 → 连续 ≥5 次停止 |
| 工具空结果 | Grep/Glob 返回 OK 但无匹配 | 注入提示或自动 ls 辅助 |

---

## 8. Compaction（无限上下文支持）

### 触发时机

```
iteration ≥ max_iterations (50)
    AND compact_on_limit = True
    AND compaction_count < max_compactions (100)
```

这意味着理论上 vCPU 支持 **50 × 100 = 5,000 次迭代**。

### 压缩流程

```
_do_compaction()
│
├── 优先: _compaction_callback()     ← AgentOS 设置的外部回调
│   └── 使用 CompactionEngine 进行 LLM 摘要 + 归档
│
└── 兜底: _compact_mmu()
    └── mmu.archive_and_reset(session_id)
        ├── 将当前上下文序列化到磁盘
        │   └── ~/.nimbus/sessions/<id>/archive/part_*.md
        ├── 重置内存帧
        └── 返回归档文件路径
```

压缩成功后，`iteration` 重置为 0，主循环继续执行。用户目标因为 `pin_goal=True` 被置顶在 Anchor 中，不会被压缩掉。

### 目标摘要

当用户目标超过 `goal_max_length`(500 字符) 时，vCPU 会调用 LLM 将其摘要为一句话：

```python
# 自动检测语言
has_chinese = any("\u4e00" <= c <= "\u9fff" for c in goal)
# 中文 → "请用一句话总结..."
# 英文 → "Summarize in one sentence..."
```

摘要失败时 fallback 到截断。

---

## 9. 可观测性

### 9.1 事件系统

vCPU 通过 `gate.events.emit()` 发射生命周期事件，用于 SSE 推送和调试：

| 事件类型 | 触发时机 | 数据 |
|----------|----------|------|
| `STEP_STARTED` | step() 开始 | `{iteration}` |
| `THINKING` | LLM 流式输出每个 chunk | `{content}` |
| `ACTION_EMITTED` | 解码出一个 ActionIR | `{action_id, kind, name}` |
| `TOOL_NAME_REPAIRED` | 工具名被自动修正 | `{original, repaired}` |
| `DOOM_LOOP_DETECTED` | 检测到死循环 | `{tool, args, count}` |
| `COMPACTION_START` | 开始压缩 | `{iteration, compaction_count}` |
| `COMPACTION_END` | 压缩完成 | `{success, compaction_count}` |
| `PROC_FINISHED` | 执行完成 | `{result, is_final}` |
| `INTERRUPTION_HANDLED` | 处理中断请求 | `{iteration}` |

### 9.2 TraceManager (198 行)

每步记录完整的执行追踪：

```python
@dataclass
class ExecutionTrace:
    iteration: int
    timestamp: str
    context: ContextSnapshot        # LLM 看到了什么（token 统计、消息列表）
    llm_raw_content: str            # LLM 原始输出
    llm_tool_calls: List[...]       # LLM 工具调用
    actions: List[ActionIR]         # 解码后的指令
    results: List[ToolResult]       # 执行结果
    fault: Optional[Fault]          # 异常
    timing_ms: Dict[str, int]       # 各阶段耗时
```

存储路径：`.nimbus/traces/<session_id>/`

### 9.3 Context Dump

设置环境变量 `NIMBUS_DUMP_CONTEXT=1` 可将每步的完整上下文写入 JSON 文件：

```
.logs/context/context_20260212_225900_iter003.json
```

包含完整的 messages 数组和 vCPU 状态快照，用于离线调试。

---

## 10. Checkpoint（状态持久化）

**组件**: `CheckpointManager` (87 行)

```python
# 创建检查点
checkpoint = vcpu.create_checkpoint(session_id, reason="periodic")

# 从检查点恢复
vcpu.restore_from_checkpoint(checkpoint)
```

检查点捕获 vCPU 的 `ExecutionState` + MMU 的内存状态，序列化为 `SessionCheckpointModel` (Pydantic)。用于会话的暂停/恢复。

---

## 11. 子组件清单

| 组件 | 文件 | 行数 | 职责 | 与 vCPU 的交互 |
|------|------|------|------|----------------|
| **vCPU 主体** | `vcpu.py` | 1,673 | 执行循环、协调所有子组件 | — |
| **ErrorHandlerRegistry** | `error_handler.py` | 627 | 错误分类，决策恢复策略 | `_handle_tool_error()` 调用 |
| **ExecutionState** | `execution_state.py` | 263 | 集中式状态管理 | `self._state` |
| **FailureReporter** | `failure_reporter.py` | 240 | 生成用户友好的失败报告 | 优雅终止时调用 |
| **RecoveryExecutor** | `recovery_executor.py` | 219 | 执行恢复动作（auto_tool 等） | 与 ErrorHandler 配合 |
| **InstructionDecoder** | `decoder.py` | 219 | LLM 原始响应 → ActionIR | `pipeline.process_response()` 内调用 |
| **ResponsePipeline** | `pipeline.py` | 215 | 中间件链（幻觉过滤、响应拆分） | DECODE 阶段的入口 |
| **DoomLoopDetector** | `doom_loop.py` | 212 | 检测工具调用死循环 | `_handle_tool_call()` 中调用 |
| **TraceManager** | `tracer.py` | 198 | 每步执行追踪 | `step()` 各阶段记录 |
| **EmptyResultHandler** | `empty_result_handler.py` | 122 | 处理 Glob/Grep 无结果 | `_handle_empty_result()` 调用 |
| **ActionContext** | `action_context.py` | 120 | 动作执行上下文封装 | 传递给 handlers |
| **CheckpointManager** | `checkpoint_manager.py` | 87 | 状态快照持久化 | `create/restore_checkpoint()` |

---

## 12. 隐式状态机分析

vCPU 的 `step()` 方法虽然没有显式的 FSM（状态枚举 + 转移函数），但通过代码的顺序流和条件分支，构成了一个**隐式状态机**：

```
                    ┌─────────────────┐
                    │      IDLE       │  (step 入口)
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │    THINKING     │  alu.chat()
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
              ┌─────│    DECODING     │─────────┐
              │     └────────┬────────┘         │
              │              │                  │
     ┌────────▼───┐  ┌──────▼──────┐  ┌────────▼────────┐
     │ RECOVERING │  │   ACTING    │  │   RETURNING     │
     │ (幻觉/错误)│  │ (工具执行)  │  │ (纯文本=最终)   │
     └────────┬───┘  └──────┬──────┘  └─────────────────┘
              │              │
              │     ┌────────▼────────┐
              │     │   OBSERVING     │  写入 MMU
              │     └────────┬────────┘
              │              │
              └──────────────┘
                     │
                     ▼
                   IDLE (下一轮 step)
```

### 当前方案 vs 显式 FSM 的权衡

| 维度 | 当前隐式状态机 | 显式 FSM (State Pattern) |
|------|---------------|------------------------|
| **可读性** | 线性流程，自上而下阅读 | 需要在多个 State 类间跳转 |
| **状态传递** | 通过局部变量和 StepResult 自然传递 | 需要 Context God Object 或参数透传 |
| **扩展新状态** | 在 step() 中加 if/else 分支 | 新建 State 子类 |
| **子组件集成** | 11 个子组件通过组合模式嵌入 | 需要将子组件注入每个 State |
| **测试** | 测试 step() 的输入输出 | 测试每个 State 的转移 |
| **复杂度** | O(1) 个文件理解主流程 | O(N) 个 State 类文件 |

**当前方案的核心优势**：step() 是一个**完整的事务**——从 THINK 到 OBSERVE 的 4 个阶段在一个方法中顺序完成，中间状态不暴露，也不需要持久化。这比 FSM 的状态跳转更容易推理正确性。

**适合引入 FSM 的时机**：当 DECODING 阶段的分支逻辑复杂到需要独立测试每个分支时（目前还不到这个程度）。

---

## 13. 与 AgentOS 的协作

vCPU **不是**独立运行的，它被 AgentOS 拥有和驱动：

| 职责 | AgentOS | vCPU |
|------|---------|------|
| 创建/销毁 vCPU 实例 | ✅ | — |
| 驱动执行循环 | 调用 `vcpu.step()` 或 `vcpu.execute()` | 执行单步 |
| 处理 CONTEXT_OVERFLOW | 接收 fault → 调用 CompactionEngine | 检测并返回 fault |
| 设置压缩回调 | `vcpu.set_compaction_callback(fn)` | 在迭代上限时调用 |
| 用户消息注入 | `AgentOS.inject_message()` | `vcpu.inject_message()` 已废弃 (no-op) |
| 中断执行 | 调用 `vcpu.request_pause()` | 在下一个 step 开头检查 |

**关键架构约束**：vCPU 自己不处理 CONTEXT_OVERFLOW，而是将其作为 fault 返回给 AgentOS。这保持了 vCPU 的单一职责（执行循环），将资源管理的决策权留在上层。

---

## 14. 设计决策备忘录

以下是代码中明确标注或隐含的关键设计决策，每一条都有其背后的失败教训：

### D1: 纯文本 = 最终答案

```python
max_consecutive_thoughts: int = 1
```

LLM 一旦不调用工具而直接输出文本，立即视为任务完成。这防止 Agent 退化为聊天机器人，也避免了 "好的，我来帮你..." 式的废话循环。

### D2: 执行后检测 Doom Loop

Doom loop 检查在 `gate.syscall_tool()` **之后**，而非之前。原因：gate 的参数校验失败（如权限拒绝）不应算作 doom loop 的一次计数。只有实际执行过的调用才纳入检测。

### D3: 错误恢复在写入内存之前

```python
result = await gate.syscall_tool(action)
if result.fault:
    recovered = await _handle_tool_error(action, result)  # 先恢复
    if recovered: result = recovered
mmu.add_tool_result(...)  # 后写入
```

如果恢复成功，写入 MMU 的是恢复后的结果。失败的原始尝试不会污染上下文，LLM 在下一轮看不到它。

### D4: 不注入 continuation hints

早期版本在 Edit/Write 成功后会注入 "consider testing with Bash before finishing"，结果 LLM 即使任务已完成也会多做一步无用的验证。现在完全移除，让 LLM 根据原始请求自行判断是否需要验证。

### D5: Ephemeral 消息

错误提示、poke 消息、幻觉纠正等临时消息都标记为 `meta["ephemeral"] = True`。LLM 成功响应后，这些消息由 `mmu.cleanup_ephemeral_messages()` 自动清理，不会永久占用上下文空间。

### D6: Pipeline 中间件 + Feature Flags

模型差异通过 `ModelManifest.features` 控制，不在代码中硬编码 `if model == "gemini"` 之类的判断：

```python
@dataclass
class ModelFeatures:
    split_mixed_responses: bool = False      # Gemini: True
    firewall_hallucinations: bool = False    # Gemini: True
    force_tool_name_repair: bool = True      # 所有模型
```

Pipeline 根据 features 自动组装中间件链。新增模型支持只需要定义一个新的 `ModelManifest`。

### D7: 优雅终止而非硬错误

Doom loop / 连续错误时，不返回冷冰冰的 `status="ERROR"`，而是调用 `FailureReporter` 让 LLM 生成自然语言的失败报告。用户看到的是 "我尝试了 3 次读取文件但都失败了，可能是路径不对..." 而不是 `[RUNTIME:DOOM_LOOP] Operation failed`。返回 `status="OK"` 让前端正常渲染，而非弹错误框。

---

## 附录 A: 工具名修正映射表

```python
TOOL_NAME_CANONICAL = {
    "read": "Read",   "Read": "Read",
    "glob": "Glob",   "Glob": "Glob",
    "grep": "Grep",   "Grep": "Grep",
    "bash": "Bash",   "Bash": "Bash",
    "kill": "Kill",   "Kill": "Kill",
    "write": "Write", "Write": "Write",
    "edit": "Edit",   "Edit": "Edit",
    "return_result": "return_result",
}
```

仅覆盖内建工具。自定义工具（如 Skill 定义的 `WebSearch`）不在此表中，直接透传给 Gate。

## 附录 B: ActionIR 指令集

```python
ActionKind = Literal[
    "TOOL_CALL",         # 调用工具 → _handle_tool_call
    "SUB_CALL",          # 派生子进程 → _handle_sub_call (模拟)
    "RETURN",            # 返回结果 → _handle_return
    "THOUGHT",           # 思考/文本 → _handle_thought → 隐式 RETURN
    "POST_IPC",          # IPC 发布 → no-op
    "REQUEST_REPLAN",    # 请求重规划 → no-op
    "CANCEL",            # 取消执行 → _handle_cancel
]
```

## 附录 C: Fault 域分类

| Domain | 说明 | 示例 Code |
|--------|------|-----------|
| `RUNTIME` | vCPU 运行时错误 | `DOOM_LOOP`, `EMPTY_RESPONSE_LOOP`, `INTERRUPTED` |
| `MEMORY` | 内存/上下文错误 | `CONTEXT_OVERFLOW` |
| `RESOURCE` | 资源限制 | `BUDGET_EXCEEDED`, `TIMEOUT` |
| `KERNEL` | 系统级错误 | `SYSTEM_ERROR`, `ILL_INSTRUCTION`, `HANDLER_ERROR` |
| `TOOL` | 工具执行错误 | `TOOL_FAILURE` |
