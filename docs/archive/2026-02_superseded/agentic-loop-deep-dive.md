# Nimbus Agentic Loop 深水区：终止策略重设计

> 状态: RFC (Review Committee 评审后综合方案)
> 日期: 2026-02-12
> 前置文档: docs/design/agentic-loop-promise-gate.md

## 1. 问题全景

Nimbus 的 vCPU 运行一个 Think-Act-Observe 循环。循环的**终止条件**是核心设计点——什么时候停下来？

当前实现用 `max_consecutive_thoughts` 计数器控制：LLM 连续返回纯文字（无 tool_call）达到阈值就停止。这个单一开关同时控制了两个互相矛盾的需求：

```
防碎碎念 ←————— max_consecutive_thoughts ————————→ 防过早终止
(AI 空转多轮)           =1 时偏左                    (AI 承诺了但没执行)
                        =5 时偏右
```

### 1.1 为什么走到这一步

**阶段 1：`max_consecutive_thoughts=1`（初始设计）**
- 纯文字回复 = AI 的最终答案，立即停止循环
- ✅ 解决了碎碎念问题（AI 不会空转）
- ❌ 但引入了"承诺型断裂"——AI 说"好的我去搜"但没调工具，循环就停了

**阶段 2：Gemini 改为 `max_consecutive_thoughts=3`（profile 层覆盖）**
- 给 AI 多几轮机会去调用工具
- ✅ 解决了承诺型断裂
- ❌ 但引入了 Anthropic API 兼容性问题（context 以 assistant 消息结尾）
- ❌ 还引入了碎碎念回归（AI 可能空转 3 轮）

**阶段 3：各种 patch 尝试**
- ephemeral user message `"..."` → 泄漏到 UI
- vCPU 层 `[Continue]` → 用户和系统消息混淆
- adapter 层 `"go on"` → 语义不清晰
- 最终改回 `max_consecutive_thoughts=1` + adapter 兜底 → 回到阶段 1 的问题

**现在的状态：** 我们在阶段 1 和阶段 2 之间来回摆，说明这个单一计数器本身就不是正确的抽象。

### 1.2 两个独立的问题

| 问题 | 触发模型 | 现象 | 本质 |
|------|---------|------|------|
| **Issue 1: 承诺型断裂** | GPT-5.3, 部分 Gemini | AI 说"我这就去搜"但 tool_calls=[] → 循环停止 | 终止条件无法区分"最终回答"和"中间承诺" |
| **Issue 2: Gemini Hallucinated Tool Calls** | Gemini (高频) | AI 把 tool call 写成文本而非 API 调用，连续失败后放弃 | Gemini 在 context 中看到自己的错误模式会继续模仿 |

## 2. 已排除的方案

| 方案 | 排除原因 |
|------|----------|
| **正则匹配 Promise 模式** (`"让我"`, `"I'll "`) | 误判率极高——"让我总结一下: 1.xxx" 是最终回答但会匹配 |
| **往 MMU 注入 ephemeral 假消息** | 泄漏到 UI/DB，用户和系统消息混淆 |
| **调大 `max_consecutive_thoughts`** | 重新引入碎碎念，且 Anthropic API 不允许 assistant 结尾 |
| **adapter 层注入 `"go on"`** | LLM 无法区分系统注入和用户输入 |

以上方案在实际测试中都被验证有问题。

## 3. Review Committee 共识

三个 AI 模型（Claude Opus、GPT-5.3、Gemini Pro）独立评审后达成以下共识：

### 3.1 根因共识

> **`max_consecutive_thoughts` 是一个粗暴的单维度开关，它不应该是循环终止的主要机制。**
>
> 真正的问题是：系统无法区分两种 THOUGHT 的语义——
> - **Final Answer**: "文件已创建好了" → 应该停止
> - **Promise/Intent**: "好的，我来搜一下" → 不应该停止
>
> 用计数器无法区分这两者。计数器应该降级为安全上限（兜底），主要终止逻辑应该基于语义判断。

### 3.2 方案共识

| 共识点 | 三方意见 |
|--------|---------|
| **Prompt 规则先行** | 零风险，直接告诉 LLM "纯文字=最终回答，要行动必须带 tool_call" |
| **Context Purge 是 Issue 2 的核心修复** | Gemini hallucination 是自我强化的，清除被污染的 assistant 消息能打破循环 |
| **用轻量启发式替代硬编码计数器** | 不需要复杂分类器，简单的 length + keyword 就够了 |
| **检测逻辑放在 Pipeline/Decoder 层** | 在 MMU 写入之前拦截，不是写入后回溯标记 |
| **正则匹配不可行** | 语义问题不能用语法规则解决 |

### 3.3 分歧点

| 分歧点 | 各方意见 |
|--------|---------|
| `max_consecutive_thoughts` 设多少 | Opus: 3-5, GPT: 语义驱动, Gemini: 2 |
| 是否需要统一的 ResponseValidator | Opus: 需要, GPT: 需要, Gemini: 先简单做 |
| Context Purge 硬删除 vs tombstone | Opus: 硬删除, GPT: tombstone, Gemini: 硬删除 |

## 4. 提议方案

### 4.1 Phase 1: Prompt 规则 (P0, 零代码改动)

在 `BASE_RULES` 中加入：

```
Text-only responses (without tool calls) are treated as your FINAL answer to the user.
If you need to perform an action (search, read, write, etc.), you MUST include the 
tool call in the SAME response. Do NOT first announce what you will do — just do it.
```

Gemini 额外规则：
```
You MUST use the function calling API for tool invocations. NEVER write tool calls 
as text. Text output = final answer. Function call = tool execution.
```

### 4.2 Phase 2: Context Purge (P0, ~15 行)

当 Decoder 检测到 hallucinated tool call 时，**在重试之前清除被污染的 assistant 消息**：

```python
# vcpu.py - hallucination 处理分支
if f.code == "ILL_INSTRUCTION":
    self._state.hallucination_count += 1
    
    # 核心改动：清除被污染的 assistant 消息
    # 防止 Gemini 从自己的错误模式中"学习"
    frame = self.mmu.current_frame
    if frame.messages and frame.messages[-1].role == "assistant":
        frame.messages.pop()
    
    # 分级纠正策略
    if self._state.hallucination_count == 1:
        correction = "Use the function calling API. Do NOT write tool calls as text."
    elif self._state.hallucination_count == 2:
        correction = (
            "[CRITICAL] Your last response was INVALID. You wrote a tool call as text.\n"
            "WRONG: Writing '[Called Bash...]' as text\n" 
            "RIGHT: Actually calling the Bash function via API\n"
            "Your next response MUST contain a real tool_call."
        )
    else:  # 3+
        correction = "Call the tool function NOW. No text, only function call."
```

### 4.3 Phase 3: 轻量终止启发式 (P1, ~30 行)

替代硬编码 `max_consecutive_thoughts=1`，在 `_handle_thought` 中引入简单的语义判断：

```python
def _is_final_answer(self, text: str) -> bool:
    """
    判断一段纯文字回复是否为最终答案。
    
    设计原则：
    - 短文本 + 承诺动词 = 可能不是 final（给一次机会重试）
    - 长文本 / 有实质内容 = 是 final
    - 计数器作为安全上限（max_consecutive_thoughts=3）
    """
    if not text or not text.strip():
        return False
    
    text = text.strip()
    
    # 长文本（>100字）基本可以确定是实质性回答
    if len(text) > 100:
        return True
    
    # 短文本：检查是否包含承诺/意图表达
    promise_signals = [
        # 中文
        "我来", "让我", "我去", "马上", "这就", "我现在",
        "我先", "我帮你", "稍等", "我搜", "我查", "我看看",
        # English  
        "let me", "i'll ", "i will ", "i'm going to ",
        "searching", "looking into", "checking",
    ]
    text_lower = text.lower()
    for signal in promise_signals:
        if signal in text_lower:
            return False  # 短文本 + 承诺词 = 不是 final
    
    # 默认：短文本无承诺词 = 是 final（保守策略）
    return True
```

修改 `_handle_thought`：

```python
async def _handle_thought(self, action: ActionIR) -> ToolResult:
    # ... non-blocking 分支不变 ...
    
    self._state.on_thought()
    
    text = action.args.get("text", "")
    
    # 安全上限：不管语义判断结果，超过阈值直接停止
    if self._state.consecutive_thoughts >= self.config.max_consecutive_thoughts:
        return await self._handle_return(action)
    
    # 语义判断：这是最终回答还是中间承诺？
    if self._is_final_answer(text):
        return await self._handle_return(action)
    
    # 不是 final —— 循环继续，但需要处理 Anthropic API 兼容性
    # （由 adapter 层兜底，不在这里注入假消息）
    return ToolResult(
        status="OK",
        output=text,
        is_final=False
    )
```

### 4.4 各 Phase 关系

```
Phase 1 (Prompt)     ─── 从源头减少问题发生（预防）
       ↓
Phase 2 (Purge)      ─── 发生后快速恢复（Issue 2 治疗）
       ↓
Phase 3 (启发式)     ─── 替代粗暴计数器（Issue 1 根因修复）
```

## 5. 待讨论的开放问题

### Q1: Phase 3 的启发式方案仍然是模式匹配

虽然加了 length 维度（>100字 = final），但核心还是在匹配 "让我"、"I'll " 等关键词。Review Committee 一致认为"语义问题不能用语法解决"，但同时也没有提出不用模式匹配的实用方案。

可能的替代方向：
- **LLM 自分类**: 让 LLM 在回复末尾自带一个 `<final>` 或 `<continuing>` 标记？（需要改 prompt + decoder）
- **Tool-first 约束**: 要求 LLM 如果要调工具，必须把 tool_call 放在文本前面？（MixedResponseSplitter 已支持）
- **完全信任 LLM**: 如果 Prompt 规则说了 "text=final"，是否可以信任模型遵守？不需要运行时检测？

### Q2: Anthropic API 兼容性如何处理

如果 Phase 3 让 `is_final=False`（循环继续），context 末尾仍然是 assistant 消息。目前 adapter 层 `"go on"` 兜底方案有效但不优雅。

可能的方向：
- adapter 层兜底是否可以接受？（它在 API payload 层，不进 MMU，不存 DB，不显示 UI）
- 还是应该在 assemble_context() 层处理？
- 或者，让 `_handle_thought` 在返回 `is_final=False` 时，同时通知 vCPU 在下一轮 step 开头自动加 user 提示？

### Q3: Executor 和 Chat 是否应该有不同的终止策略

- **Chat 模式**: 面对用户，纯文字回复通常就是最终答案
- **Executor 模式**: 执行任务，纯文字回复可能确实是中间思考

是否应该：
- Chat: `max_consecutive_thoughts=1` + 不做启发式（text=final，句号）
- Executor: `max_consecutive_thoughts=3` + 启发式检测

但目前 vCPU 不知道自己的角色（chat vs executor），需要传入或从 config 推断。

### Q4: 投入产出比

- Phase 1 (Prompt) 和 Phase 2 (Purge) 的 ROI 很高，共 20 行代码
- Phase 3 (启发式) 的 ROI 不确定——如果 Phase 1 已经有效（LLM 遵守 "text=final" 规则），Phase 3 可能根本不需要
- 是否应该先做 Phase 1+2，观察效果，再决定是否做 Phase 3？

## 6. 当前代码状态

```
已完成的改动（已 commit）:
  - max_consecutive_thoughts=1 (VCPUConfig default)
  - profile 层: core=5, executor=3, standard=3 (会覆盖 default)
  - adapter 层 "go on" 兜底
  - THOUGHT = Final Answer (当 consecutive_thoughts >= limit)
  
关键代码路径:
  src/nimbus/core/runtime/vcpu.py          # _handle_thought, step()
  src/nimbus/core/runtime/pipeline.py      # HallucinationSanitizer
  src/nimbus/core/runtime/decoder.py       # _check_hallucination
  src/nimbus/core/models/manifest.py       # ModelFeatures
  src/nimbus/orchestration/prompts.py      # System Prompt / BASE_RULES
  src/nimbus/core/profile.py               # AgentProfile (per-role config)
  src/nimbus/adapters/pi_adapter.py        # Anthropic API 兼容性
```
