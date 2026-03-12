# 实际 Nimbus AgentOS 架构与设计

## 1. 核心理念：受操作系统启发的 Agent 运行时

Nimbus AgentOS (v0.2.0) 是一个建立在大型语言模型 (LLM) 之上的系统级框架。与简单的链式或图式编排工具不同，它将 LLM 抽象为 CPU，围绕其构建了完整的操作系统级组件。其核心目标是解决单体 Agent 在复杂、长程任务中常见的上下文漂移、状态管理失效和循环幻觉问题。

## 2. 系统核心组件映射

| 组件名称 | 代码实现模块 | 操作系统概念 | 核心职责 |
| :--- | :--- | :--- | :--- |
| **Facade** | `AgentOS` | 操作系统内核总线 | 组装所有子组件，对外提供统一的运行入口（`run`, `stream`），处理全局生命周期与并发。 |
| **VCPU** | `VCPU` | 虚拟中央处理器 | 实现核心的 Think-Act-Observe 有限状态机 (FSM)，驱动 Agent 认知与行动的循环。 |
| **MMU** | `MMU` | 内存管理单元 | 负责长程上下文管理，通过 Anchor (规则/目标) 与 Stream (动态历史) 机制平衡 Token 消耗。 |
| **Gate** | `KernelGate` | 内核态门卫 / Syscall | 作为所有工具执行的单一出口，处理权限隔离、硬截断、超时强杀以及结果封装。 |
| **ALU** | `Adapter` | 算术逻辑单元 | 抽象大模型底层的网络请求，支持多种 Provider（如 Anthropic, OpenAI）。 |

---

## 3. 核心机制解析

### 3.1 VCPU: 基于状态机的认知循环 (FSM)

区别于无尽的 `while True:` 循环，VCPU 是一个严格的有限状态机。它确保 Agent 的每一步转换（思考、执行、观察、错误处理）都是**显式且可观测的**。

**关键状态流转 (`IDLE` -> `THINKING` -> `ACTING` -> `OBSERVING`)：**
1. **思考 (THINKING)**：通过 ALU (LLM) 推理下一步动作。如果 Token 正常，进入 `ACTING`。
2. **执行 (ACTING)**：解析出工具调用（ActionIR），交由 Gate 执行。
3. **观察 (OBSERVING)**：接收 Gate 返回的结果，更新 MMU 上下文，然后回到 `THINKING` 评估结果。

**防爆状态 (`COMPRESSING` / `DEAD`)：**
如果循环中触发 Token 超限（主动检测 > 85% 或被动接收到 `CTX_OVERFLOW`），VCPU 会挂起当前流程，强制进入 `COMPRESSING` 状态。如果压缩成功，恢复 `THINKING`；如果超过最大重试次数，则标记为 `DEAD`。

### 3.2 MMU: Anchor & Stream 上下文管理

长程任务的最大敌人是上下文污染和指令遗忘。MMU 采用分层架构应对：
- **Anchor (Pinned Context)**：系统的绝对规则（System Rules）、用户的核心目标（Goal）作为“锚点”永远钉在 Prompt 头部，**绝不参与任何压缩**，保证 Agent 不会“失忆”。
- **Stream (History Stream)**：动态的对话和工具执行流。
  - 当触发 `COMPRESSING` 时，MMU 会保留最近的 N 个消息（Hot Zone），而将更早的对话（Cold Zone）交给 LLM 生成“全局摘要”。
  - **墓碑化 (Tombstoning)**：对极长且不再影响当前决策的工具输出，MMU 会将其替换为“墓碑标记”（例如：“*执行了复杂的 Bash 构建，已成功，细节折叠*”），极大地释放了 Token 窗口，却不破坏逻辑链条。

### 3.3 KernelGate: 安全执行的最后防线

大模型的输出存在不确定性，`KernelGate` (内核门) 负责在执行层面进行硬防护：
- **权限隔离**：根据 Agent 角色（如 `reader` 还是 `worker`）校验工具是否在白名单内。
- **输出硬截断**：对于 `Bash` 等可能导致灾难性大输出的命令，实施严格的行数/大小限制（如最后 2000 行）。被截断的完整日志会被写入本地临时文件，只给 LLM 返回文件的路径，迫使其使用 `Read` 或 `Grep` 进行分片分析。
- **超时与异常隔离**：单次工具调用如果超时或崩溃，会转换为标准的 `Fault` 消息返回给 VCPU 的 `OBSERVING` 状态，而不是直接阻断整个系统。Agent 可以在下一轮 `THINKING` 中决定是重试还是改变策略。
