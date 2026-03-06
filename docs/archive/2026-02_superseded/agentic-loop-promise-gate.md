# Nimbus Agentic Loop 优化：两阶段回复 + Gemini Hallucination 问题

## 问题概览

Nimbus vCPU 的 Think-Act-Observe loop 存在两个相关但不同的 agent 架构问题：

| 问题 | 触发模型 | 现象 | 根因 |
|------|---------|------|------|
| **Issue 1: 承诺型文本** | GPT-5.3, 部分 Gemini | AI 说"我这就去搜"但不调工具，loop 停止 | `max_consecutive_thoughts=1` 把承诺误判为 final answer |
| **Issue 2: Hallucinated Tool Calls** | Gemini (高频) | AI 把 tool call 写成文本而非 API 调用，连续失败后放弃 | Gemini 倾向用文本模拟工具调用，纠正 prompt 无法打破循环 |

---

## Issue 1: 承诺型两阶段回复

### 问题描述

当用户要求调用工具时（如"帮我搜一下 xxx"），AI 会先输出一条"承诺型文本"（如"当然可以，我这就去搜"），但不附带任何 tool_call。由于 `max_consecutive_thoughts=1`（纯文本响应视为 final answer），loop 立即停止。用户必须再发一条消息才能触发实际的工具调用。

两个需求的冲突：
- **需求A**: 纯文本响应应立即停止 loop（避免 AI 空转 3 轮）— 已修复
- **需求B**: "承诺型文本"不应被视为 final answer（AI 还没执行工具）

### 典型时间线

```
用户: "帮我搜一下 xxx"

Step 1: LLM 返回 → content: "当然可以，我这就去搜" | tool_calls: []
         ↓ Decoder: THOUGHT action
         ↓ _handle_thought: consecutive_thoughts=1 >= 1 → is_final=True
         ↓ Loop 停止 ❌ （AI 还没调工具！）

用户: "好" ← 用户被迫再发一条

Step 2: LLM 返回 → tool_calls: [WebSearch(...)] → 执行 → 返回结果
```

### 两种 THOUGHT 的语义差异

| 类型 | 例子 | 是否 Final |
|------|------|-----------|
| **Final Answer** | "我已经创建了 hello.py" | ✅ 是 |
| **Promise/Intent** | "当然可以，我这就去搜" | ❌ 不是 |

---

## Issue 2: Gemini Sequential Tool Call Hallucination

### 问题描述

当要求 Gemini 按顺序执行多个工具调用（非并行）时，Gemini 倾向于把 tool call **写成文本**，而不是通过 function calling API 发起。

### 实际日志证据

```
13:30:00  LLM 返回:
  content: "[Historical context: a different model called tool "Bash" 
            with arguments: { "command": "python3 -c \"import math; print(math.pi * 5**2)\"" }.
            Do not mimic this format - use proper function calling.]"
  tool_calls: []   ← 空！没有实际 tool call

  → HallucinationSanitizer 检测到 "[Historical context:" 模式
  → 注入纠正: "Use the function calling API. Do NOT write tool calls as text."
  → 重试...

13:30:03  LLM 再次返回同样模式（hallucination #2）
13:30:06  LLM 再次返回同样模式（hallucination #3）
  → max_hallucinations=3 达到上限
  → 放弃: "(Task could not be completed - model produced invalid responses)"
```

### 现有防御链路分析

```
LLM Response
  ↓
Pipeline: HallucinationSanitizer (pipeline.py)
  → 检测 "[Historical context:" → 用 regex 剥离该文本块
  → 如果剥离后为空 → content=None
  ↓
Decoder: _check_hallucination (decoder.py)
  → 如果 content 仍含 HALLUCINATION_PATTERNS → raise Fault(ILL_INSTRUCTION)
  ↓
VCPU: step() 的 except Fault 分支 (vcpu.py L705)
  → hallucination_count += 1
  → 注入纠正 prompt (ephemeral)
  → 如果 hallucination_count >= 3 → 放弃任务
```

### 为什么现有纠正无效

1. **纠正 prompt 太弱**: `"Use the function calling API. Do NOT write tool calls as text."` 对 Gemini 效果有限
2. **上下文污染**: 即使 ephemeral 标记了错误的 assistant message，Gemini 可能已经从自己的"模式"中学到了继续 hallucinate
3. **没有 positive example**: 只告诉模型"不要这样做"，没给出"应该怎么做"的示例
4. **`max_hallucinations=3` 太激进**: 3 次后直接放弃，无恢复手段
5. **Sequential 触发更频繁**: 并行工具调用 Gemini 能处理，但"做完一个再做下一个"时容易退化为文本模拟

---

## 提议的分层方案

### Layer 1: Prompt Policy（P0 优先级 — 立刻见效，零代码改动）

**位置**: `src/nimbus/orchestration/prompts.py` → `BASE_RULES`

**注入规则** (同时解决 Issue 1 和 Issue 2):

```python
BASE_RULES += """
5. **No Pre-announcement**: When you decide to call a tool, call it IMMEDIATELY in the same response.
   Do NOT first say "Let me search for that" or "I'll look into this" without an accompanying tool call.
   Text-only responses are treated as your FINAL answer. If you still need to act, you MUST include tool calls.
   对"好/继续/开始"等确认词，若上轮已形成可执行计划，本轮应直接执行工具，不要再次解释将要执行。
6. **Sequential Tool Calls**: When you need to call multiple tools in sequence, call the FIRST tool now.
   After receiving its result, call the NEXT tool. Never describe tool calls as text.
"""
```

**Gemini-specific 额外规则** (在 `TRAIT_GEMINI` 中):

```python
TRAIT_GEMINI += """
- **CRITICAL**: You MUST use the function calling API. 
  NEVER output tool calls as text like "[Called tool...]" or "<function_call>".
  If you need to call Bash, use the Bash function. If you need to Read, use the Read function.
  Text output = final answer to user. Function call = tool execution.
"""
```

**覆盖率**: ~80% (Issue 1), ~60% (Issue 2, Gemini 遵从率较低)

### Layer 2: Runtime Promise Gate（P1 — 解决 Issue 1）

**位置**: `src/nimbus/core/runtime/vcpu.py` → `_handle_thought`

**核心逻辑**: 当 AI 输出纯文本（THOUGHT action, tool_calls=[]）时，检测是否为"承诺型"回复：

```python
PROMISE_PATTERNS_ZH = ["我这就", "我来", "让我", "我现在去", "我去", "马上"]
PROMISE_PATTERNS_EN = ["I'll ", "I will ", "Let me ", "I'm going to "]
```

**如果检测到承诺**:
1. 标记当前 assistant message 为 `ephemeral`
2. 注入纠正指令: `"[System] Do not describe what you will do. Call the tool directly."`
3. 返回 `is_final=False`（loop 继续）

**内存时序**:
```
step() L789: mmu.add_assistant_message(response.content)  ← 已写入
step() L820: _execute_action() → _handle_thought()        ← 回溯标记 ephemeral
```

### Layer 3: Hallucination Recovery 增强（P1 — 解决 Issue 2）

**位置**: `src/nimbus/core/runtime/vcpu.py` → step() 的 hallucination 处理分支

**改进点**:

#### 3a. 更强的纠正 prompt（带 positive example）

```python
# 当前（弱）:
"Use the function calling API. Do NOT write tool calls as text."

# 改进（强，带示例）:
"""[System] INVALID RESPONSE. You output a text description of a tool call instead of 
actually calling the tool. This is WRONG.

WRONG: Writing "I'll call Bash with command xyz" as text
RIGHT: Actually invoking the Bash function via the API

Do NOT reply with text. Call the tool function NOW. 
Your next response MUST contain a tool_call, not text."""
```

#### 3b. Context Purge — 清除 hallucination 上下文

```python
# 当前：只标记 ephemeral，但 hallucinated content 可能影响后续生成
# 改进：检测到 hallucination 时，主动清除最近的 hallucinated messages
if self._state.hallucination_count >= 2:
    # 清除所有 ephemeral + 最近一条 assistant message
    self.mmu.cleanup_ephemeral_messages()
    # 移除最后一条被污染的 assistant message（如果是 hallucination）
    if self.mmu.current_frame.messages:
        last = self.mmu.current_frame.messages[-1]
        if last.role == "assistant":
            self.mmu.current_frame.messages.pop()
```

#### 3c. 提高 max_hallucinations 并分级处理

```python
# 当前: max_hallucinations=3, 全部用同一策略
# 改进: 分级递进
HALLUCINATION_STRATEGIES = {
    1: "gentle_correction",   # 温和纠正
    2: "strong_correction",   # 强力纠正 + context purge
    3: "context_reset",       # 清除近期对话，只保留原始任务
    4: "model_fallback",      # (可选) 切换到备用模型
    5: "give_up",             # 最终放弃
}
```

#### 3d. ModelFeatures 控制

```python
# manifest.py - 新增 feature flag
@dataclass
class ModelFeatures:
    ...
    # Does the model tend to hallucinate sequential tool calls?
    sequential_tool_hallucination_prone: bool = False
    max_hallucination_retries: int = 3  # Default

GEMINI_FEATURES = ModelFeatures(
    ...
    sequential_tool_hallucination_prone=True,
    max_hallucination_retries=5,  # Gemini 需要更多重试机会
)
```

### Layer 4: 前端 UI 处理（P2 可选）

- 承诺型文本 → 灰色"计划中"气泡
- Hallucination 重试 → 只在 debug 面板显示，用户不可见
- 最终 tool_done 后才展示正式回答

### 不建议的方案

| 方案 | 原因 |
|------|------|
| 改回 `max_consecutive_thoughts=3` | 重新引入"AI 空转 3 轮"的原始 bug |
| 完整状态机重构 | ROI 太低，与现有 step-based 架构冲突 |
| 对 Gemini 完全禁用 sequential tool calls | 限制太大，很多场景需要按序执行 |

---

## 关键代码路径

```
Issue 1 (承诺型文本):
  vcpu.py step()
    → Decoder: content only, no tool_calls → THOUGHT
    → _handle_thought() → [Promise Gate 插入点]
    → is_final=True (当前) / is_final=False (修复后, 如检测到承诺)

Issue 2 (Gemini hallucination):
  vcpu.py step()
    → Pipeline: HallucinationSanitizer 剥离文本
    → Decoder: _check_hallucination → raise Fault(ILL_INSTRUCTION)
    → step() except 分支: hallucination_count++ → [纠正策略插入点]
    → 3 次后放弃 (当前) / 分级恢复 (修复后)

共享代码路径:
  src/nimbus/core/runtime/vcpu.py          # vCPU 主循环
  src/nimbus/core/runtime/pipeline.py      # Response Pipeline
  src/nimbus/core/runtime/decoder.py       # Hallucination 检测
  src/nimbus/core/models/manifest.py       # ModelFeatures
  src/nimbus/orchestration/prompts.py      # System Prompt
```

## 预期效果

| 方案 | 解决问题 | 改动量 | 覆盖率 | 风险 |
|------|---------|--------|--------|------|
| L1 Prompt Policy | 1+2 | ~5 行 | ~70% | 极低 |
| L2 Promise Gate | 1 | ~20 行 | ~95% (Issue 1) | 中（误判） |
| L3 Hallucination Recovery | 2 | ~40 行 | ~85% (Issue 2) | 低 |
| L4 前端 UI | 体验 | 中等 | 锦上添花 | 低 |

## 待评审问题

1. 分层方案是否合理？优先级排序是否正确？
2. Layer 2 的模式匹配检测有什么风险？（误判/漏判）有没有更好的检测方法？
3. Layer 3 的分级 hallucination 恢复是否过度设计？ 
4. Context Purge（清除 hallucinated messages）会不会导致信息丢失？
5. 是否应该在 Pipeline 层（process_response 阶段）做 Promise 检测，而非在 _handle_thought 中回溯？
6. 是否存在遗漏的边界情况？
