# AI Council Report:  帮忙评审一下~/sourcecode/agent/agent-framework/nimbus/docs/subagent-orchestration-design.md

2026/3/12 13:26:22

### Opinion from Claude Opus 4.6 (Deep Thinker)

好的，我已经仔细阅读了设计文档以及相关的现有代码（`spawn_agent.py`、`vcpu.py`、`loop.py`、`sse.py`、`ARCHITECTURE.md`），下面给出专业评审意见。

---

## 📋 Subagent 编排架构设计文档评审报告

**文档版本**: V2 | **评审人**: AI 架构顾问 | **评审日期**: 2026-03-12

---

### 一、整体评价

**评分: B+ (良好，有若干关键问题需要修正)**

这份设计文档对 Nimbus 当前 `spawn_agent` 的四大痛点（黑盒化、非结构化输出、无中间态干预、并发失控）诊断**准确且深刻**。解决方案的思路整体上是合理的，特别是：

- ✅ SSE 合流方案比父进程转发更优（正确识别了阻塞问题）
- ✅ Core Dump 持久化 + 内存实例销毁的设计（避免了长生命周期内存泄漏）
- ✅ PAUSED 态先闭合脏 `tool_result` 再持久化（解决了 MMU 一致性问题）
- ✅ 全局 LLM Semaphore 的思路正确

但在与**实际代码对照**后，发现了**几个架构级别的断裂和缺失**。

---

### 二、关键问题 (Must Fix 🔴)

#### 🔴 问题 1: 架构文档与代码严重不一致 — 组件命名和结构已漂移

设计文档基于 `CLAUDE.md` 的旧架构描述（`RuntimeLoop` 在 `loop.py`、`AgentOS` 在 `agent.py`），但 `ARCHITECTURE.md`（Context Hub 版本，更新至 03-06）显示架构已演进到包含 **Scheduler（DAG 调度器）**、**NimFS（虚拟文件系统）**、**Heart（后台守护）** 等新组件，且 `vCPU` 代码量已膨胀到 ~4200 行（含 12 个子组件），`AgentOS` 为 2324 行。

**影响**: 设计文档中说"修改 `vcpu.py` 的 `step()` 逻辑"——但实际 `vcpu.py` 当前只有 ~270 行且结构清晰，而 `ARCHITECTURE.md` 描述的 vCPU 是 4200 行的巨型模块。到底改哪个？新旧架构之间是否有迁移中间态？

**建议**: 在文档第 1 节明确声明**基于哪个代码版本和架构分支**。如果 `ARCHITECTURE.md` 中的 Scheduler/NimFS 已存在，多 Agent 编排应考虑与 **Scheduler（DAG 并行调度）** 的关系，而不是单独再造一套并发控制。

#### 🔴 问题 2: SSE 合流方案缺乏对 SSEHub 现有架构的适配细节

设计文档说"Subagent 的 `RuntimeLoop` 注入 `SSEHub` 队列引用，通过独立协程推送"。但查看现有 `sse.py`，`SSEHub` 的 `publish()` 方法签名是：

```python
async def publish(self, session_id: str, event_type: str, data: Dict) -> int
```

它基于 **session_id** 做 fan-out。而设计文档引入了 `sub_session_id` + `parent_action_id` 的二维路由，这意味着：

1. **前端必须按 `parent_action_id` 做嵌套卡片挂载**——但现有 SSE 事件类型（`tool_call`、`tool_result`、`message` 等）没有 `parent_action_id` 字段，需要新增 `subagent_event` 事件类型或扩展现有类型。
2. **`subscribe()` 是按 `session_id` 订阅的**——Subagent 事件应该推到**父 Session** 的 `session_id` 下（否则前端需要同时订阅多个 SSE 流），但文档没有明确这一点。

**建议**: 补充以下内容：
- Subagent 事件推送到 **父 session_id** 的 SSE 流（而非 sub_session_id），前端按 `parent_action_id` 做 UI 嵌套。
- 新增 `SSEHub.EVENT_SUBAGENT = "subagent_event"` 事件类型。
- 补充 `SSEEventBuilder.subagent_event()` 工厂方法。

#### 🔴 问题 3: `resume_agent` 基于 Core Dump 恢复的可行性存疑

设计说"读取 `core_dump.json` 重建 `AgentOS` 和 MMU"。但查看 `loop.py` 中现有的 `_save_core_dump()`，它保存的内容是：

```python
messages, vcpu_state, vcpu_config, llm_config, mmu_state(global_summary + goal)
```

**缺失项**:
- **Tool Registry 状态**: 不同 Role 的工具集不同，Core Dump 没有保存 role 信息。
- **LLM Adapter**: 需要根据 model 重新创建，但 Core Dump 里的 `llm_config` 是否包含完整的 model routing 信息？
- **Scratchpad 内容**: 虽然在磁盘上，但恢复后 System Prompt 里引用的 scratchpad 路径是否一致？
- **Steering/FollowUp Queue 状态**: 未持久化。

**建议**: 在 Core Dump schema 中补充 `role`、`model`、`scratchpad_path` 字段。或者更务实地——在 `resume_agent` 的工具参数中让父 Agent 显式传入 role + hints，而非完全依赖 Core Dump 自动恢复。

---

### 三、重要问题 (Should Fix 🟡)

#### 🟡 问题 4: 强类型交付契约 — "剥夺 RETURN 权限" 的副作用

设计说"剥夺原生 `RETURN` 随意结束权限"，只允许通过 `submit_findings` 等工具交付。但查看 `vcpu.py` 的 `step()`:

```python
for action in actions:
    if action.kind in ("RETURN", "REPLY"):
        result.is_final = True
        ...
```

如果禁止 RETURN，那当 LLM 没有 tool_call 也没有 submit 调用、只产生纯文本时，VCPU 会走到 `THOUGHT` 分支并触发 `max_consecutive_thoughts` 限制后强制结束——此时**没有结构化结果**。

**建议**: 不要完全剥夺 RETURN，而是采用**降级策略**：
1. 优先：检测到 `submit_*` 工具调用 → 结构化交付。
2. 降级：超过 N 轮无 `submit_*` → 自动注入 Steering 提醒。
3. 兜底：达到 max_iterations → 自动调用 `_collect_partial()` 作为伪结构化结果。

#### 🟡 问题 5: LLM Semaphore 的粒度需要细化

设计说"针对同 Provider/API_KEY 共享信号量"。但实际场景中：
- Anthropic 的限流是 **per-model** 的（Claude 3.5 Sonnet 和 Claude 3 Haiku 限流独立）。
- OpenAI 的限流是 **per-organization + per-model** 的。
- 429 的 `Retry-After` header 值各不相同。

**建议**: Semaphore key 应该是 `(provider, api_key, model_family)` 的三元组，而非仅 `(provider, api_key)`。且退避策略应尊重 429 响应中的 `Retry-After` header（如有）。

#### 🟡 问题 6: 异步 `mode="async"` 的生命周期管理缺乏设计

文档列出了 `poll_agent(pid)` / `kill_agent(pid)` / `resume_agent(pid)` 工具，但：
- 异步 Subagent 的结果**何时、如何**通知父 Agent？靠 polling？事件驱动？
- 父 Agent 自身如果被 compact 或中断，orphan Subagent 如何处理？
- 多个异步 Subagent 之间如果有隐式依赖（比如一个读取另一个的写入结果），如何协调？

**建议**: 补充异步 Subagent 的完整生命周期状态机图，包括 orphan 检测和 GC 策略。考虑是否可以复用 `ARCHITECTURE.md` 中已有的 **Scheduler（DAG 调度器）** 来管理异步任务依赖。

---

### 四、建议改进 (Nice to Have 🟢)

#### 🟢 问题 7: 缺少容量规划和性能估算

- 每个 Subagent 独立 MMU + 独立 RuntimeLoop 的内存开销是多少？
- 同时运行 N 个 Subagent 时的 peak memory 和 API 成本上界？
- `LLMSemaphore` 的 max concurrency 默认值建议是多少？

**建议**: 补充一个简单的容量模型表（如 "3 个并发 Subagent ≈ 额外 50MB 内存 + 3x token 消耗"）。

#### 🟢 问题 8: Milestone 0 的测试场景不够

5 个测试场景覆盖了核心路径，但缺少：
- **嵌套 Subagent**: Subagent 内部再 spawn_agent 的递归场景（应禁止还是限制深度？）
- **SSE 断线重连**: 前端在 Subagent 执行中途断开 SSE 后重连，event log replay 是否包含 subagent_event？
- **并发 resume**: 同一个 PAUSED agent 被 resume 两次（幂等性）。

#### 🟢 问题 9: 实施时间估算偏乐观

Phase 1-3 总计 5.5 天，但考虑到：
- `vcpu.py` 的 RETURN 路由修改涉及核心执行路径，需要极高测试覆盖。
- Core Dump schema 扩展需要向后兼容旧 session 数据。
- 前端 Next.js 的嵌套卡片渲染是一个独立工作量（未计入）。

**建议**: 实际工时建议 x1.5，约 8-9 天，且前端工作应单列为 Phase 4。

---

### 五、总结与建议优先级

| 优先级 | 问题 | 行动项 |
|--------|------|--------|
| 🔴 P0 | 架构版本不一致 | 明确代码基线，与 Scheduler/NimFS 对齐 |
| 🔴 P0 | SSE 合流缺适配细节 | 补充 session_id 路由策略和事件类型 |
| 🔴 P0 | Core Dump 恢复信息不全 | 扩展 schema，补充 role/model 等字段 |
| 🟡 P1 | RETURN 剥夺策略过激 | 改为降级策略 |
| 🟡 P1 | Semaphore 粒度 | 细化到 model_family 级别 |
| 🟡 P1 | 异步生命周期缺设计 | 补充状态机和 orphan GC |
| 🟢 P2 | 容量规划 | 补充内存/成本估算 |
| 🟢 P2 | 测试场景补充 | 增加递归/重连/幂等测试 |
| 🟢 P2 | 时间估算 | 调整为 8-9 天，单列前端工作 |

**结论**: 方向正确，核心设计思路（SSE 合流、Core Dump 恢复、Semaphore）都是对的。主要风险在于**设计文档与实际代码/架构之间的 gap** 需要先弥合，否则实施阶段会频繁返工。建议在动手编码之前，先花半天时间更新设计文档以对齐上述 P0 问题。


### Opinion from GPT-5.4 (Generalist)

下面是我基于**文档本身**以及 **Nimbus 当前实现/上下文**做的评审。

---

# 总体结论

这份 `docs/subagent-orchestration-design.md` **方向是对的**，而且抓住了当前 `spawn_agent` 的几个真实痛点：  
- 黑盒执行  
- 父 Agent 解析子 Agent 自然语言输出不可靠  
- 超时/中断后无法恢复  
- 并发子任务下缺少统一限流

从产品体验和系统演进角度看，这是一份**有明显推进价值**的设计稿。  
但如果按当前 V2 文档直接落地，我认为会遇到 5 个关键问题：

1. **有些设计点与当前代码行为不一致**，尤其是 `VCPU/Decoder` 部分。  
2. **“强类型交付”目前还没有真正打通到父 Agent 消费链路**。  
3. **SSE 合流方案耦合点选得不够好**，不建议直接注入 queue 引用。  
4. **异步生命周期缺少持久化 process registry**，仅靠 core dump 不够。  
5. **LLM Semaphore 的作用域放在 Session 级不太合适**，更适合 Adapter/Provider 级。

所以我的结论是：

> **这份设计可以作为下一阶段子 Agent 编排升级的主方案，但建议先修订为 V2.1，再进入实现。**

---

# 我认为写得好的部分

## 1) 问题定义很准确
文档对现状判断基本符合代码与上下文：

- 当前 `spawn_agent` 确实是**阻塞式**  
  文件：`src/nimbus/core/tools/spawn_agent.py`
- 当前子 Agent 结果主要还是靠 `output` 文本回传，父 Agent 消费时仍然是**文本语义解析**
- 当前确实已有 core dump 机制，但**没有完整 pause/resume 生命周期**
  文件：`src/nimbus/core/loop.py`
- Context Hub 里也明确提到：
  - `async spawn_agent background polling is missing`
  - `Specialist timeout recovery` 还没落地
  - `Process/Session` 仍有纠缠

这些判断是成立的。

## 2) “可观测性 + 可恢复性 + 资源治理”三件事放在一起考虑，是成熟的
很多设计文档只盯着 UI 或只盯着恢复，但你这版把三块串起来了，这点很好。

## 3) TDD 先行是对的
Milestone 0 先定义边界测试，这很符合 Nimbus 现在的工程状态，也符合仓库里的开发习惯。

---

# 关键问题与修改建议

---

## 一、`VCPU 路由拦截` 这段，和当前实现并不完全对齐

你在 3.2 里写：

> 当且仅当响应中只有文本 REPLY 没有 Tool Call 时，才允许因为 `text_is_final` 触发结束。如果存在 `submit_*` 工具调用，优先执行工具，忽略附带自然语言。

### 但当前代码其实已经部分满足这个目标
文件：`src/nimbus/core/decoder.py`

当前逻辑是：

- **只要有原生 `tool_calls`**，Decoder 就会：
  - 把文本变成 `THOUGHT`
  - 把工具调用变成 `TOOL_CALL`
  - **不会产出 `REPLY/RETURN`**
- `VCPU.step()` 在路由时，才会对 `RETURN/REPLY` 结束

也就是说，**“text + tool_call 混合时，优先执行 tool”这个行为当前已经成立**。  
所以你文档里把主要改动点放在 `vcpu.py step()`，我觉得**不够准确**。

### 真正需要改的是哪里？
真正的风险点在于 **Decoder 的 pure text 终止判定**：

文件：`src/nimbus/core/decoder.py`

当 `text_is_final=False` 时，Decoder 仍可能基于 `_is_done()` 把短文本判成 `RETURN`。  
这意味着你想“剥夺自由发言权”，**仅改 VCPU 不够**，应该增加一个更明确的模式，比如：

- `subagent_contract_mode=True`
- 在这个模式下：
  - 纯文本默认只算 `THOUGHT`
  - 只有调用 `submit_*` 工具才允许终结
  - 或者仅允许显式 `RETURN` 且必须携带结构化 payload 引用

### 建议
把文档中的这一段改成：

> 对 Subagent 的“终止权限”约束，应主要落在 `InstructionDecoder` 的 contract mode，而非仅在 `VCPU.step()` 做路由优先级修正。

这是一个很重要的实现边界修正。

---

## 二、“强类型交付契约”现在还没有真正打通到父 Agent

这是这份设计里我最想提醒的一点。

### 当前 `spawn_agent` 返回链路是什么？
文件：`src/nimbus/core/tools/spawn_agent.py`  
文件：`src/nimbus/core/gate.py`

当前工具返回大致是：

- 工具层返回 `{output, ui_detail}`
- Gate 把它包装成 `ToolResult`
- `output` 会写进父 Agent 的 MMU 作为 tool result
- `ui_detail` 主要给 UI 事件消费

### 这意味着什么？
即便你设计了 `submit_findings`，如果最后仍然是：

- 子 Agent 提交 JSON
- 然后 `spawn_agent` 再把它转成一段字符串 `output`

那**父 Agent 实际消费的仍然是文本**。  
也就是说：

> 你解决了“子 Agent 随便说话”的问题，  
> 但还没有真正解决“父 Agent 以结构化方式可靠拿到结果”的问题。

### 建议你把“Typed Contracts”补完整
我建议明确两层交付：

#### 方案 A：结构化结果走 `ui_detail.deliverable`
例如 `spawn_agent` 返回：

```json
{
  "output": "Sub-agent completed. See structured deliverable.",
  "ui_detail": {
    "deliverable": {
      "kind": "findings",
      "summary": "...",
      "findings": [...],
      "artifacts": [...]
    }
  }
}
```

然后**编排层**而不是父 LLM，直接消费 `ui_detail.deliverable`。

#### 方案 B：结构化结果落盘为 artifact，再返回引用
更稳妥：

- `submit_findings` 把 JSON 写到
  `.nimbus/sessions/<sub_session_id>/deliverable.json`
- `spawn_agent` 只返回：
  - 简短文本摘要
  - deliverable 路径
  - ui_detail 中附 manifest

这样：
- 父 Agent 如需读详细内容，可显式 `Read`
- UI/编排器也能直接拿结构化 JSON
- 避免 JSON 巨量注入父上下文

### 我的建议
在 Nimbus 当前架构下，**B 比 A 更稳**。  
因为当前体系本来就偏向“状态外置 / artifact 引用”。

---

## 三、SSE 合流的接入点建议改一下：不要直接注入 queue 引用

文档 3.1 写的是：

> Subagent RuntimeLoop 在实例化时注入当前 SSEHub 队列引用，后台协程直接向 SSEHub 推送包装后的事件。

这个思路能工作，但我不建议这么做。

### 原因
当前 SSE 实现里，`SSEHub` 的职责不只是“一个 queue”：

文件：`src/nimbus/server/sse.py`

它还负责：
- session 级 publish
- pending buffer
- event log replay
- multi-tab reconnect
- heartbeat

如果直接绕过这些抽象，把底层 queue 暴露给 subagent，会带来两个问题：

1. **破坏 SSEHub 封装**
2. **事件回放、重连、多连接行为更难保证一致**

### 更适合当前代码的方式
当前 AgentOS / Gate / Loop 已经有 event callback 机制：

- `KernelGate._emit(...)`
- `RuntimeLoop._emit(...)`
- Session streaming 层再 publish 到 SSEHub

所以更合理的方式是：

> 让 subagent 沿用现有事件回调机制，  
> 然后由 server/session 层把事件转发到 **父 session** 的 SSEHub，  
> 并补充 `sub_session_id`、`parent_action_id` 元数据。

### 我建议的事件模型
你不一定要新增一个大包裹的 `subagent_event`，更建议：

- 保持已有事件类型：
  - `tool_call`
  - `tool_result`
  - `message`
  - `done`
- 只是额外加字段：
  - `sub_session_id`
  - `parent_action_id`
  - `agent_role`
  - `is_subagent: true`

这样前端改造成本会更低，也更贴近现有事件体系。

### 结论
**目标正确，接入点建议调整。**

---

## 四、异步生命周期设计里，缺一个“持久化 process table”

文档 3.3 里提出：

- `spawn_agent(mode="async")`
- `poll_agent(pid)`
- `kill_agent(pid)`
- `resume_agent(pid)`

这是正确方向。  
但当前文档里只有：

- core dump
- 销毁内存实例
- 恢复时读 `core_dump.json`

这还不够。

### 为什么不够？
因为 `poll/kill/resume` 需要的不只是“能恢复上下文”，还需要**有一个稳定的 process registry** 去回答：

- 这个 pid 是否存在？
- 当前状态是 `RUNNING / PAUSED / COMPLETED / ERROR / TIMEOUT`？
- 对应哪个 `sub_session_id`？
- 属于哪个 `parent_session_id` / `parent_action_id`？
- 结果文件/日志文件/core dump 在哪里？
- 是否还能 resume？
- 是否已经被 kill？

### 仅靠内存单例不行，文档也意识到了
这点文档判断是对的。  
但**仅靠 `core_dump.json` 也不行**，因为它不是 process index。

### 建议
增加一个最小持久化 manifest，例如：

`.nimbus/processes/<pid>.json`

内容至少包括：

```json
{
  "pid": "sub_xxx",
  "sub_session_id": "sub_xxx",
  "parent_session_id": "sess_xxx",
  "parent_action_id": "call_xxx",
  "role": "reader",
  "status": "PAUSED",
  "created_at": "...",
  "updated_at": "...",
  "scratchpad": "...",
  "deliverable": "...",
  "core_dump": "...",
  "last_error": null,
  "resumable": true
}
```

这样：
- `poll_agent(pid)` 查 manifest
- `resume_agent(pid)` 读 manifest + core dump
- `kill_agent(pid)` 更新 manifest 状态
- UI 也可直接轮询或订阅

### 这点非常关键
否则 async API 会变成“接口看起来完整，实际上没有可靠状态源”。

---

## 五、`PAUSED` 状态设计是好的，但“回合闭合”建议用现有消息模型表达，不要用伪 XML

文档里说：

> Subagent 必须先向 MMU 写入一个 `<tool_result status="PAUSED" />` 闭合当前回合。

这个表达是对意图的描述，但不建议实现成这种伪协议文本。

### 原因
Nimbus 当前消息合法性是通过 MMU 的真实消息结构维持的。  
Context Hub 里也提到已有“消息排序安全网”。

### 建议实现方式
应该是：

- 如果当前有未闭合 tool_call
- 用 `mmu.add_tool_result(call_id, tool_name, paused_payload)` 正式补齐
- `paused_payload` 可以是 JSON 字符串或标准短文本
- 然后保存 core dump

例如：

```json
{
  "status": "PAUSED",
  "reason": "dependency_missing",
  "resume_hint": "Please provide package name or credentials"
}
```

这样更符合当前 MMU / provider 兼容逻辑。

---

## 六、`LLM Semaphore` 方向是对的，但作用域不该是 Session 级

文档写：

> 父智能体及其派生的所有子智能体，共享同一个 Session 级别的并发与退避控制锁。

我认为这里**作用域偏小了**。

### 为什么？
429 / rate limit 通常是按这些维度发生的：

- provider
- account / API key
- model family
- region / deployment

如果只做 Session 级：
- 能缓解“单个会话内”的惊群
- 但无法缓解“多个会话共享同一 API key”的全局风暴

### 更合理的放置层级
建议放在 **Adapter 层 / Provider Client 层**：

- key = `(provider, credential, maybe model)`
- 由所有 parent/subagent/session 共用
- 429 后统一 backoff

这也更贴近当前代码位置：  
`src/nimbus/adapters/direct_adapter.py` 已经有 429/重试逻辑。

### 我建议文档改成
> 引入 provider/api-key 维度的 `LLMConcurrencyCoordinator`，由 Session/Parent/Subagent 共享，而不是仅 Session 级 semaphore。

---

# 与现有架构的一致性评价

## 一致的部分
这份设计与 Context Hub 里的这些方向是对齐的：

- `Specialist timeout recovery` 需要补
- `Process/Session` 需要解耦
- 需要更强 observability
- 需要治理 sub-agent massive outputs/context pollution

## 需要补充对齐的部分
### 1) 要和 D-008 三级委托模型联动
`DECISIONS.md` 里已有 Pending 决策：

- Inline Call
- Light Process
- Full Process

你这份设计现在基本是在给 **Full Process Subagent** 做增强。  
建议文档里明确一句：

> 本设计先服务于 Full Process / spawn_agent，不试图覆盖 Inline Call / Light Process。

否则后面容易重复设计。

### 2) `spawn_agent` 与 `Process` 概念最好统一命名
现在文档里有时说 pid，有时说 session_id。  
考虑到 Context Hub 已明确提示 process/session 纠缠，建议术语收紧：

- `process_id`
- `sub_session_id`
- `parent_session_id`
- `parent_action_id`

四者分开定义。

---

# 我建议你修改文档的几个重点句子

## 1. 把“VCPU 路由拦截”改成“Decoder Contract Mode”
原文不够准。  
建议改成：

> 在 subagent contract mode 下，Decoder 对 pure-text 默认只产出 THOUGHT，不因 heuristics 产出 RETURN；只有 submit_* 工具或显式受控终止动作才允许结束。

## 2. 把“SSEHub 注入 queue 引用”改成“复用 event callback + SSEHub publish”
建议写成：

> Subagent 沿用现有 RuntimeLoop/Gate 事件回调链路，由父 session 的 server/session 层转发到 SSEHub，并带上 `sub_session_id` / `parent_action_id`。

## 3. 给 async 生命周期补一个 manifest/process registry
建议单列一个小节：

> `core_dump` 用于恢复上下文，`process manifest` 用于查询生命周期状态；两者职责不同，不可互相替代。

## 4. 把 LLM Semaphore 从 Session 级改成 Provider/API Key 级
这会让设计更稳，也更容易长期扩展。

---

# 测试建议：Milestone 0 还应再加 4 个

你列的 5 个测试不错，但我建议再补：

1. **SSE 重连回放**  
   子 Agent 事件在父 session reconnect 后仍能正确回放，不重复、不乱序。

2. **父 MMU 零污染验证**  
   subagent 的中间 thought / tool output 不进入 parent MMU。

3. **kill → manifest 状态一致**  
   `kill_agent(pid)` 后，poll 立即可见状态变化，且不可再 resume。

4. **submit 工具缺失时的兜底恢复**  
   子 Agent 超时/报错时，即使没调用 `submit_findings`，仍能通过 scratchpad/core dump 恢复部分成果。

---

# 最终评价

如果给这份设计一个综合评价，我会给：

**7.8 / 10**

## 优点
- 选题准
- 痛点抓得真
- 路线有工程感
- 比当前实现明显前进一步

## 缺点
- 个别实现边界没贴紧当前代码
- “typed contract” 还差最后一公里
- async lifecycle 缺少 process registry
- semaphore 作用域定义不理想

---

# 我的建议结论

**建议采纳，但先修成 V2.1 再实现。**

如果你愿意，我下一步可以继续帮你做两件事之一：

1. **直接给这份文档出一版“修订建议稿（V2.1）”**  
2. **基于当前代码结构，拆成“最小可落地 implementation plan”**（按文件路径列修改点）


### Opinion from Gemini 3.1 Pro Preview (Reasoning)

你好！作为你的 AI 架构顾问，我仔细审阅了这份《Nimbus Subagent 编排架构设计文档 (V2)》。

结合 Nimbus 当前的系统上下文（AgentOS 架构、Sisyphus 协议以及现有的技术债），这份设计文档**质量极高，切中要害**。它不仅精准识别了多智能体协同中的核心痛点（黑盒化、上下文污染、并发灾难），而且给出的解决方案（SSE 旁路推送、强类型契约、Core Dump 断点续传、全局信号量）非常契合 Unix 哲学的进程模型设计。

以下是我的深度评审意见，包含**亮点肯定**以及几个需要警惕的**架构风险与优化建议**：

### 🌟 架构亮点 (Strengths)

1. **SSEHub 流合流设计非常巧妙 (3.1)**：
   跳过父进程被阻塞的生成器，直接让 Subagent 携带 `parent_action_id` 向前端推送事件。这不仅完美解决了长时间 "Loading" 的 UX 噩梦，还严格保证了父智能体的 MMU 不被这些中间过程污染。
2. **状态机安全闭合与持久化 (3.3)**：
   将异步任务从内存单例 (`SubagentManager`) 迁移到无状态的 `core_dump.json` 是极其正确的设计。这避免了长生命周期 Agent 导致的内存泄漏，且 `status="PAUSED"` 的强制闭合动作有效消除了“有 tool_call 无 tool_result”的脏状态报错风险。
3. **LLM Semaphore 全局限流 (3.4)**：
   精准命中了多智能体并发请求 LLM API 时最容易出现的“惊群效应（Thundering Herd）”。集中式锁和退避机制将大幅提升系统的稳定性。

---

### ⚠️ 风险预警与优化建议 (Risks & Recommendations)

#### 1. 前端状态水合 (UI Hydration) 的盲区
* **风险**：文档中提到“SSE 推送仅用于 UI 渲染，绝对不写入父智能体的 MMU”。如果用户在 Subagent 运行期间**刷新了浏览器**，此时瞬时的 SSE 事件已丢失，而父智能体的 `_messages` 里又没有这些记录，前端将无法重建 Subagent 的嵌套卡片和执行进度。
* **建议**：Subagent 的事件虽然不写入父级 MMU，但必须写入一个**只读的审计日志或前端渲染数据库 (UI Event Store)**。前端拉取历史会话时，能根据 `parent_action_id` 查询到对应的子级执行轨迹进行回放渲染。

#### 2. JSON 字段“硬截断”的致命陷阱 (3.2)
* **风险**：文档提议“工具层必须对字段强行截断（如 `findings` ≤ 1000 字符）”。如果你直接对 LLM 生成的 JSON 字符串进行物理截断，大概率会**破坏 JSON 的闭合括号**，导致 JSON 解析直接崩溃（JSONDecodeError）。
* **建议**：
  * **方案 A（优雅截断）**：在工具层成功解析 JSON 后，对字典中的 String Value 进行截断（如 `args["findings"] = args["findings"][:1000] + "...(truncated)"`）。
  * **方案 B（结合上下文设计）**：根据 `CLAUDE.md` 中的既有设计，建议继续复用 **Scratchpad（草稿本）机制**——将完整超长报告写入硬盘文件（如 `scratchpad.md`），而在父智能体的工具返回值中仅包含：“文件已保存至 xxx，内容摘要：[前1000字]”。

#### 3. VCPU 路由拦截时的“思维链 (CoT) 截断”问题 (3.2)
* **风险**：文档中写道“如果存在 `submit_*` 工具调用，优先执行工具，忽略附带的自然语言”。在父智能体视角忽略是可以的，但 **Subagent 自身的 MMU 必须保留这部分文本**。Claude 等大模型经常在调用工具前输出大量 `<thinking>`（思维链），如果这段文本在 Subagent 自身的历史记忆中被抹除，模型在下一轮（或 resume 时）将失去连贯的推理上下文。
* **建议**：明确区分 **Delivery (交付)** 和 **Memory (记忆)**。自然语言不作为交付结果返回给 Parent，但必须忠实记录在 Subagent 自己的 `.nimbus/sessions/{sub_session_id}/core_dump.json` 的 Message 历史中。

#### 4. 垃圾回收 (Garbage Collection) 与资源孤儿
* **风险**：通过 `mode="async"` 启动并触发 PAUSED 的 Subagent，如果父智能体最终忘记调用 `resume_agent` 或 `kill_agent`，硬盘上会遗留大量 `core_dump.json` 成为“孤儿会话”。
* **建议**：需要引入一个后台 TTL 清理机制，或者在父智能体 Session 终结 / DEAD 时，级联销毁所有关联的 `sub_session_id` 磁盘数据。

#### 5. 对齐 Next.js 前端 rAF 渲染池
* **提醒**：由于 `CLAUDE.md` 提到前端 (chat-store.ts) 刚刚修复了基于 `requestAnimationFrame` 的缓冲更新逻辑。当你引入极其高频的 Subagent SSE 流时，务必在前端验证新的 `subagent_event` 类型是否能被当前的 rAF 队列正确批量消费，避免出现嵌套状态更新的竞态条件。

### 总结
整体而言，这是一份**具备工业级成熟度**的架构设计方案。你可以直接进入 **Milestone 0 (TDD)** 阶段。建议在 Phase 2 实现交付契约时，重点采纳上述第 2 点和第 3 点的建议，以确保 Agent 的逻辑连贯性和系统的健壮性。
