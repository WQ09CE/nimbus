# AgentOS 重构设计方案 (v6)

## 1. 核心目标
将 `AgentOS` (~1900行 God Class (经多轮修复后有所缩减)) 瘦身为 **Thin Coordinator (<300行)**，仅保留核心协调逻辑，将具体职责委托给单一职责的子系统。

**v5 更新重点**：解决 Runtime Loop 分裂问题 (`run_stream` vs `_run_process`)，统一迭代控制权，消除 FSM 与 OS 层的逻辑冲突。

**v6 更新重点**：整合 FSM 重构后的实战经验与 bug 修复总结（v3-beta-2/beta-3），更新现状分析。

## 2. 现状分析
当前 AgentOS 存在 5 大职责耦合及 **3 个严重的一致性问题**：

### 2.1 职责耦合
1. **Process Lifecycle**: `spawn`, `wait`, `run`, `_run_process`
2. **Session Management**: `new_session`, `load_session`
3. **Compaction Engine**: `compact`, `_check_compaction`
4. **Tool/Skill Registry**: `register_tool`, `reload_skills`
5. **Event/Signal System**: `interrupt`, `inject_message`

### 2.2 关键缺陷 (New Findings)
1. **Runtime Loop 分裂危机**：
    - `_run_process` (313行) 是全功能循环（含 IPC、中断、压缩、心跳）。
    - `run_stream` (104行) 是残缺的独立循环，**严重缺失** Iteration Limit、Interrupt、Inbox 处理、Auto Compaction。
    - **后果**：Web 端流式调用时，Agent 无法被中断，且不会触发自动压缩，长期运行必崩。
    > **v6 状态**: 仍然存在，但实际影响可控。生产路径（web-ui SSE）全走 `_run_process`（session_v2 → chat() → _run_process），`run_stream` 仅在 demo 脚本中使用。建议在下一轮重构中统一，但不是紧迫问题。
2. **Iteration Limit 权责冲突**：
    - **FSM 层** (`StateObservation`): 检测 `iteration >= max` → 直接返回 `BUDGET_EXCEEDED` + `StateCompleted`。
    - **OS 层** (`_run_process`): 检测 `iteration >= max` → 尝试 `compact` 重置计数或 `inject` 强制总结。
    - **后果**：FSM 先触发终止，OS 层的补救策略（自动压缩续命）永远无法执行。
    > **v6 状态**: 已解决。当前实际行为：OS 层（`_run_process`）先检查 iteration limit → 尝试 compact 续命或强制总结 → FSM 层（`StateObservation`）作为 fail-safe 兜底。两层共存，OS 优先，符合 Section 4.2 建议。
3. **代码冗余**：
    - `chat()` 方法 (94行) 重复了 `spawn()` 80% 的组件创建逻辑 (MMU, Gate, VCPU)。
    > **v6 状态**: 仍然存在。chat() 和 spawn() 各自组装 MMU/Gate/VCPU，有约 80% 重复。ProcessFactory 提取仍有价值。

### 2.3 方法调用热度分析
| 方法 | 热度 | 备注 |
| :--- | :--- | :--- |
| `run`, `chat`, `get_session` | 🔥 High | 核心入口 |
| `run_stream` | ⚠️ Mid | Web 接口依赖，功能残缺 |
| `wait_all`, `spawn_batch` | ⚠️ Mid | 并行/Review 场景 |
| `compact` | ❄️ Low | 几乎无外部调用，应转为内部策略 |

### 2.4 FSM 重构后新发现的问题 (v3-beta-2/beta-3 修复)

Gemini 将 vCPU while 循环重构为 FSM 架构后，引入了 8 个 bug，已全部修复。按根因分类：

#### 映射不完整
| Bug | 修复 | 文件 |
| :--- | :--- | :--- |
| SubmitResult 未映射到 RETURN | 添加 CONTROL_FLOW_TOOLS 条目 | decoder.py |
| DoomLoopDetector 断联 (TODO 未完成) | 集成到 StateObservation | states.py |
| Process.signals 字段被误删 (重命名不彻底) | 恢复 signals 字段 | agentos.py |

#### 约束未迁移
| Bug | 修复 | 文件 |
| :--- | :--- | :--- |
| ErrorRecovery 无限重试 | MAX_CONSECUTIVE_ERRORS=5 | states.py |
| Process.inbox = None 崩溃 | default_factory=list | agentos.py |

#### 语义不兼容（最难发现）
| Bug | 修复 | 根因 |
| :--- | :--- | :--- |
| THOUGHT 不终止 → Explorer 返回空 | strict 模式移除，纯文本统一为 REPLY | 旧循环 THOUGHT 有隐式终止路径，FSM 没有 |
| Anthropic 400 tool_use/tool_result 不配对 | THOUGHT 嵌入 assistant message content | FSM 分步写 MMU 打破了紧邻约束 |
| inbox 阻塞子进程终止 | inbox 延续检查限定 chat 进程 | 旧代码 inbox 检查位置不同，FSM 统一后没区分进程类型 |

**核心教训**: FSM 重构改变了控制流语义。旧 while 循环有大量隐式终止条件和副作用顺序，结构重构必须同步迁移这些隐式约定。特别是 chat 进程 vs sub-agent 进程的行为差异，需要在架构层面显式区分。

## 3. 架构设计：5 个子系统
我们将拆分为以下 5 个核心子系统，AgentOS 仅作为 Facade。

### 3.1. ProcessManager (核心运行时)
**职责**: 统一管理 Process 生命周期与 Runtime Loop。
- **Location**: `src/nimbus/core/process/manager.py`
- **Key Components**:
    - `RuntimeLoop`: **新增组件**，统一 `_run_process` 和 `run_stream` 的逻辑。
    - `ProcessContainer`: 持有 `VCPU`, `MMU`, `Gate`。
- **关键决策**:
    - **Unified Loop**: 提取 `RuntimeLoop` 类，支持 `run()` (同步阻塞) 和 `stream()` (异步生成器) 两种消费模式，底层共用一套 step/interrupt/compaction 逻辑。
    - **Factory Pattern**: 提取 `ProcessFactory` 统一 `spawn` 和 `chat` 的组件组装过程。

### 3.2. SessionController (会话管理)
**职责**: 会话持久化、恢复、历史记录。
- **Location**: `src/nimbus/core/session/controller.py`

### 3.3. CompactionService (内存压缩服务)
**职责**: 自动/手动 Context 压缩策略。
- **Location**: `src/nimbus/core/compaction/service.py`
- **策略变更**: 压缩触发逻辑从 `_run_process` 剥离，注册为 `RuntimeLoop` 的钩子 (Hook)。

### 3.4. ToolSystem (工具/技能系统)
**职责**: 工具注册与查找。
- **Location**: `src/nimbus/core/tools/system.py`

### 3.5. EventBus (事件总线)
**职责**: 进程间通信与事件分发。
- **Location**: `src/nimbus/core/event/bus.py`

## 4. 核心重构方案：Unified Runtime Loop

针对 `run_stream` 和 `_run_process` 分裂问题，设计统一的 **RuntimeLoop**。

### 4.1 Loop 逻辑流 (Standard Flow)
所有执行模式（Sync/Stream）必须遵循统一流程：
1. **Prolog**: Load Context -> Init Components -> Check Resume
2. **Cycle**:
   - Check Interrupts / Mailbox (IPC)
   > **注意**: Mailbox/Inbox 延续检查仅对 Chat 进程生效。Sub-agent 进程（Explorer/Implementer 等）完成即终止，不检查 inbox。
   - **Check Iteration Limit** (OS Level First) -> Trigger Compaction/Summary if needed
   - `vcpu.step()`
   - Emit Heartbeat
   - Check Context Overflow -> Trigger Compaction
   - Check Faults
   - Yield Result (for Stream) or Accumulate (for Sync)
3. **Epilog**: GC -> Save State -> Close

### 4.2 解决 Iteration Limit 冲突
- **方案**: **OS 层优先拦截**。
- 在调用 `vcpu.step()` 之前，OS 检查 `iteration_count`。
- 如果达到阈值且策略允许（如 auto-compact），则执行压缩并重置计数器，**不调用** `vcpu.step()`。
- 只有当 OS 决定不再续命时，才让 `vcpu.step()` 执行，此时 FSM 内部的检查作为最后一道防线（Fail-safe）。
- **实际采用方案 (v3-beta-2)**: 保留 FSM `StateObservation` 的 `BUDGET_EXCEEDED` 检查作为 fail-safe，OS 层在调用 `vcpu.step()` 前先拦截。两层共存，OS 层可执行 compact 续命，FSM 层防止失控。

### 4.3 统一接口设计
```python
class RuntimeLoop:
    def __init__(self, process: Process, strategy: LoopStrategy):
        ...

    async def run_generator(self):
        """核心生成器，驱动所有逻辑"""
        while not self.is_terminated:
            # 1. System Checks (Interrupt, IPC)
            await self._handle_system_events()
            
            # 2. Resource Checks (Compaction, Iteration)
            if self._check_resources():
                continue # 可能触发了压缩，跳过本次 step
            
            # 3. Execution
            step_result = await self.vcpu.step()
            yield step_result
            
            # 4. Post-step (Heartbeat, Overflow)
            await self._post_step_maintenance()

    async def run_until_complete(self):
        """同步模式封装"""
        async for result in self.run_generator():
            if result.is_final:
                return result
```

## 5. 迁移路径 (Updated)

### Phase 1: 基础设施 (EventBus, ToolSystem, Compaction)
- 提取 `EventBus` 和 `ToolSystem`。
- 将 `_check_compaction` 和 `_compaction_for_process` 移入 `CompactionService`。

### Phase 2: Runtime Loop 统一 (Critical)
- **Step 2.1**: 创建 `core.process.factory.py`，统一 `spawn` 和 `chat` 的创建逻辑。
- **Step 2.2**: 创建 `core.process.loop.py`，实现 `RuntimeLoop`。
  - 移植 `_run_process` 的 313 行逻辑到 `RuntimeLoop`。
  - 补充 `run_stream` 缺失的 IPC/Interrupt/Compaction 逻辑。
- **Step 2.3**: 修改 FSM `StateObservation`，放宽迭代限制检查。

### Phase 3: AgentOS 瘦身
- 将 `AgentOS.run`, `chat`, `run_stream` 重写为调用 `RuntimeLoop`。
- 删除旧的 `_run_process` 和 `run_stream` 实现。

## 6. 目录结构变更
```text
src/nimbus/
├── agentos.py            # Facade
├── core/
│   ├── process/
│   │   ├── manager.py    # Process Registry
│   │   ├── loop.py       # [New] Unified RuntimeLoop
│   │   ├── factory.py    # [New] Process/VCPU Factory
│   │   └── state.py
│   ├── session/ ...
│   ├── compaction/ ...
│   ├── tools/ ...
│   └── event/ ...
```
