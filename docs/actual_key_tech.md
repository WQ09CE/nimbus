# 实际 Nimbus 关键技术与多 Agent 协作机制

## 1. 核心协作原语：`spawn_agent`

在复杂的业务场景中（如扫描巨大代码库进行重构，或跨文件排查深层 BUG），单节点 Agent 极易受限于上下文窗口、注意力衰减以及陷入无限重试死循环。Nimbus 的解决方案是在 Toolchain 层面原生提供了 `spawn_agent` 能力。

### 1.1 隔离与分发 (Isolation & Dispatch)

- **独立内存空间 (Isolated MMU)**：当 Orchestrator 调用 `spawn_agent` 时，系统会为其拉起一个全新的子 Agent 实例。这个子 Agent 拥有自己**完全独立的 MMU (内存管理单元)** 和状态机。它不会继承父节点的庞大历史记录（History Stream），保证了其上下文极度纯净，100% 聚焦于当前分配的具体 `goal`。
- **并发执行**：Orchestrator 可以在一个认知周期内（通过一个 function call block）并发拉起多个独立的子任务。这些子 Agent 异步运行，互不干扰，极大提升了长任务的执行效率。

### 1.2 角色化与权限管控 (Role-based Registry)

`spawn_agent` 强制要求指定子 Agent 的角色（例如 `reader` 或 `worker`）。
- **Reader Agent**: 它的 Tool Registry（工具白名单）仅包含 `Read`, `Grep` 等只读探查工具。这种物理层的阻断保证了在复杂扫描任务中，即便模型发生幻觉也无法破坏系统。
- **Worker Agent**: 拥有 `Write`, `Edit`, `Bash` 等修改权限，负责精准执行。
- **无嵌套约束 (No Nesting)**：当前框架规则强制子 Agent 必须扁平化运行，禁止在其内部再次调用 `spawn_agent`，有效防止了调度失控。

### 1.3 状态流转与结果聚合

子节点在执行过程中，会将其“思考”与“行动”记录在专属的 Scratchpad 或工作日志中。当其任务结束（或超时/失败）后，父节点 (Orchestrator) 处于 `OBSERVING` 状态，接收到执行结束的信号。随后父节点进入新的 `THINKING` 周期，通过 `Read` 或 `Grep` 工具主动回收并验证子节点的工作成果，从而完成 **"分发 -> 独立执行 -> 结果汇聚"** 的 Map-Reduce 闭环。

---

## 2. AgentOS 内存防爆机制 (MMU 压缩)

虽然子 Agent 解决了横向扩展和隔离的问题，但在长程的单体任务（或者父节点的调度过程中），上下文爆炸依然是致命威胁。Nimbus 的 MMU 在代码底层实现了多种防护策略。

### 2.1 主被动双重监控 (VCPU COMPRESSING 状态)

VCPU 状态机在流转时，始终对 Token 消耗保持警惕：
- **主动防御 (Pre-emptive)**：每轮 `THINKING` 循环前，MMU 会检测当前使用率（如 `token 使用率 > 85%`）。若超过阈值，立刻挂起当前任务流，强制状态切换至 `COMPRESSING`。
- **被动防御 (Reactive)**：如果因为大段输出导致底层 Adapter (LLM API) 抛出 `CTX_OVERFLOW` 等超长错误，VCPU 捕获后同样进入 `COMPRESSING` 状态。

### 2.2 墓碑机制与摘要 (Tombstoning & Hot-Cold Zone)

进入 `COMPRESSING` 状态后，MMU 会执行上下文清理：
1. **保留锚点 (Anchor Context)**：用户的原始 Goal 和 System Rules 永远在 Prompt 头部作为 Pinned Context，不参与任何压缩。
2. **冷热区划分 (Hot-Cold Zone)**：保留最近 N 个消息对构成的“热区”，保证 Agent 能回忆起刚刚的上下文；而将更早的“冷区”历史对话，交由 LLM 生成“全局摘要”后替换原有的详细记录。
3. **工具执行的墓碑化 (Tombstoning)**：对于极长且不再影响当前决策的工具 Request/Response，MMU 会将其折叠，替换为一句简短的“墓碑标记”（如：“*[压缩] 执行了文件读取并完成分析*”），彻底释放宝贵的 Token 空间。

---

## 3. KernelGate 与底层执行安全

在最底层的执行引擎 `KernelGate` 处，框架设置了最后一道防线：
- **输出硬截断**：对于 `Bash` 等可能产生无尽输出（如编译日志、无限死循环输出）的工具，实施强制硬截断（例如截断至最后 2000 行 / 50KB）。被截断的完整日志写入本地临时文件，仅返回文件路径和截断提示给 LLM，迫使其使用分片读取。
- **超时强杀 (Timeout Kills)**：单次工具调用均受硬超时控制。一旦超时，抛出标准异常，VCPU 捕获后交由 Agent 在下一轮 `THINKING` 中决策是否重试，防止由于单个子任务阻塞导致整个系统陷入瘫痪假死。
