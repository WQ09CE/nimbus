# Termination Inversion — 终止权还给模型，harness 只做退出闸门

Status: proposal (2026-08-16) · 前置：终止证据化两刀（concludes_turn c66a45ab、claim guard 27b28d59）、narrate guard 收窄（a21074e2）

## 证据

### eval 实锤（meta-question 任务，2026-08-15）

run 模式（`text_is_final=False`）下纯文本任务的真实成本：

- gpt-5.6-sol：同一答案**完整生成 8 遍**（48.9s，8 步 0 工具）才被 stall 检测强制收尾
- qwen3.8：6 步打转后 `_final_summary` 空产出 → `run()` 兜底 "Loop ended without result" → ERROR
- 同样的问题走 chat 模式（`text_is_final=True`）：**1 步完成**

根因链：`_is_done()` 词法启发式（≤300 字符 + done 词 / ≤120 字符无 planning 词）对长答案永远判 THOUGHT → consecutive_thoughts 计数 → poke/stall 循环。**长的、完整的、正确的最终答案是被系统性惩罚的**。

### dsh（deepseek-harness）

终止逻辑全文（agent.ts:394-399）：

```ts
if (toolCalls.length === 0) return { kind: 'completed' }
const { concluded } = await executeToolCalls(...)
return concluded ? { kind: 'completed' } : null
```

纯文本 = 无条件终止；工具可声明 `concludesTurn` 提前收轮。无 nudge、无启发式、无 THOUGHT 概念。终止是模型的决定。

### 业界共识（2026）

- stop_reason 是 agent loop 唯一可靠的环控制（`tool_use` → 继续，`end_turn` → 结束）
- hard iteration limit 作为主终止机制是反模式
- premature-stop 的解法是 **Stop Hook**：终止前检查完成判据，不满足才拦——不是 harness 主动赶着模型继续
- pi：无 maxIterations，text 即终止，靠 context+compaction 自然限制

## 判断

nimbus 现行的"文本≠终止 + `_is_done` 启发式 + continuation poke"是 ReAct 时代（弱模型需要鞭子）的设计。2026 年的模型（包括本地 27B）绝大多数时候知道自己什么时候完成；猜错的少数情况，我们**已经有**证据化的拦截器（narrate guard / claim guard）——它们正是业界说的 Stop Hook，只是现在被埋在一条走不到的路径下（run 模式长文本连 REPLY 都不是，guards 根本不运行）。

**反转方向：从"证明你完成了才放行"（默认怀疑）改为"有未完成证据才拦"（默认放行）。**

## 手术方案（四刀）

1. **`text_is_final=True` 成为唯一语义**，删 `_is_done()` 启发式与 run/chat 模式分叉。纯文本一律 REPLY。THOUGHT 仅保留两处：工具调用伴随的文本、contract 模式（子 agent 必须 `submit_result` 退出——那是结构化契约，不是猜测，保留）。
2. **退出闸门统一为三个 evidence-based stop hooks**（全部已存在、全部有界）：narrate guard（收窄版：末两句宣告未行动）、claim guard（声称改文件无证据）、contract-mode submit 强制。删 `consecutive_thoughts` 整套计数（THOUGHT 主路径消失后成为死代码）。
3. **stall 检测降级为纯安全网**（text-final 后应几乎不触发）；`_final_summary` 修空产出兜底：summarizer 无输出时回退最后一条 assistant 文本（修 qwen ERROR 路径）。
4. **eval 验证**：
   - meta-question：8 步 → 1 步（主要收益的直接量化）
   - 新任务 premature-stop-bait：多步任务里诱导模型中途宣告"接下来我会…"就停——验证 guards 接得住弱模型（这是保留 nimbus 弱模型价值主张的关键测试）
   - 全任务双 rail 回归（sol judgment / qwen smoke），动刀前先存 baseline

## 风险与对冲

弱模型（gemma4 12B 级）真实存在中途停。对冲：它们停的时候几乎总在宣告下一步——恰是 narrate guard 的匹配面；premature-stop-bait 任务量化验证。若 smoke rail 显示不可接受的回归，为弱模型加 **opt-in** 的 goal-checklist poke（配置项，不是全局默认）。

## 预期删除

`decoder._is_done` + `_DONE_PATTERNS`/`_PLANNING_WORDS`、`ExecContext.consecutive_thoughts`/`on_thought`、`VCPUConfig.max_consecutive_thoughts`、vcpu 纯 THOUGHT 分支、`AgentConfig.text_is_final` 分叉（含 `_build_loop` 传参）。终止相关代码量预计净减 ~150 行，语义从 5 层收敛到 2 层（模型决定 + 证据闸门）。
