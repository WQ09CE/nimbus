# AI Council 评审报告：Agentic Loop 优化方案

> 评审日期: 2025-02-12
> 评审专家: Claude Opus 4.5, GPT-5.2, Gemini 3 Pro High
> 综合人: Claude Sonnet (Chairman)

---

## 一、共识（三方一致）

### 1.1 方案整体结构
三位专家**一致认同**分层方案 (L1→L2→L3→L4) 的结构和优先级排序。

### 1.2 Layer 1 Prompt Policy — P0 ✅
全体赞同立刻实施。但有两个一致修改意见：
- `BASE_RULES` 中不应中英混写，中文场景应移入独立块
- 措辞应解释"**为什么**"（text-only = final answer），而非只给命令

### 1.3 Promise Gate 关键词匹配 — 🔴 高风险
**最强烈的共识**：纯关键词匹配的误判率不可接受。

三方共同举例：
| 文本 | 实际 | 匹配结果 |
|------|------|---------|
| "让我**解释**一下..." | Final Answer | ❌ 误判为 Promise |
| "I'll **summarize** the findings" | Final Answer | ❌ 误判为 Promise |
| "我来**总结**一下" | Final Answer | ❌ 误判为 Promise |

三方**共识替代方案**：**短文本 + 关键词 + 无实质内容** 的组合启发式：
```python
def _is_promise(text, user_context):
    if len(text) > 120~150:  return False   # 长文本 = 完整回答
    if has_substance(text):  return False   # 含代码块/列表/分析 = 完整回答
    if not has_promise_word(text): return False
    return True  # 短 + 承诺词 + 无实质 = 承诺型
```

### 1.4 检测位置：Pipeline 层 > `_handle_thought` 回溯
三方一致建议：**Promise 检测应放在 Pipeline 层**（作为 `ResponseMiddleware`），而非在 `_handle_thought` 中回溯标记已写入的 MMU 消息。

理由：
- Pipeline 的设计意图就是 response 预处理
- 避免"先写入再回溯删除"的时序耦合
- 检测逻辑可独立单测

### 1.5 Hallucination 分级策略 — 去掉 model_fallback
三方一致：Level 4 `model_fallback` **不应在本版本实施**。模型切换涉及 API key、manifest、tool schema 差异，复杂度远超 hallucination handler 的范畴。

### 1.6 `hallucination_count` 需正式化
三方都指出 `getattr(self._state, 'hallucination_count', 0)` 是 tech debt，应正式加入 `ExecutionState` dataclass，并在 `reset()`、`create_snapshot()`、`restore_from_snapshot()` 中处理。

---

## 二、分歧

### 2.1 覆盖率估算

| 方案 | 文档 | Opus | GPT-5.2 | Gemini |
|------|------|------|---------|--------|
| L1 (Issue 1) | ~80% | ~80% | ~60% | ~70% |
| L1 (Issue 2) | ~60% | ~60% | ~30-40% | ~35-40% |
| L2 Promise Gate | ~95% | ~95% | ~85% | ~85% |

**Chairman 裁定**：采用保守估计。Prompt 对 Gemini 的控制力确实弱于 GPT/Claude，Issue 2 的 L1 覆盖率按 ~40% 计。

### 2.2 Promise Gate 具体实现位置

| 专家 | 建议位置 | 具体做法 |
|------|---------|---------|
| **Opus** | Pipeline middleware (`PromiseDetector`) | 返回带 `meta={"is_promise": True}` 的 ActionIR，vcpu 检查标记 |
| **GPT-5.2** | 内存写入之前（step 主逻辑中） | 在 MEMORY UPDATE 段前插入检查，直接 `continue` 跳过 |
| **Gemini** | Pipeline middleware | 在 response 上设 `_promise_detected` flag，vcpu 检查 |

**Chairman 裁定**：**采用 Opus 方案**（Pipeline middleware 返回带标记的 ActionIR）。理由：
- 与现有 `MixedResponseSplitter` 架构一致
- `_handle_thought` 只需检查 `action.meta.get("is_promise")`，干净解耦
- GPT-5.2 的 `continue` 方案会增加 step() 主循环的条件分支

### 2.3 Hallucination 分级数量

| 专家 | 建议级数 | 策略 |
|------|---------|------|
| **Opus** | 保留文档的 5 级，去掉 Level 4 | gentle → strong → context_reset → give_up |
| **GPT-5.2** | 简化为 3 级 | correction_with_example → purge_and_retry → give_up |
| **Gemini** | 去掉 Level 4，保留其余 | 同 Opus |

**Chairman 裁定**：**采用 GPT-5.2 的 3 级方案**。理由：
- `context_reset`（清除近期对话只保留原始任务）实现复杂度被低估（Opus 也指出了这一点）
- 3 级已覆盖 95% 场景，额外复杂度不值得

### 2.4 Ephemeral 消息清理时序

**Gemini 3 Pro 提出了一个"严重 bug"**：认为 `cleanup_ephemeral_messages()` 在 `alu.chat()` 之前执行，导致 ephemeral 纠正消息在模型看到之前就被删。

**Chairman 交叉验证**：**Gemini 的结论是错误的。** 实际代码时序：

```python
# vcpu.py step() 实际执行顺序：
L567: messages = mmu.assemble_context()       # ① 构建 prompt（含 ephemeral）
L614: response = await alu.chat(messages, ...) # ② LLM 看到 ephemeral 消息 ✅
L619: cleanup_ephemeral_messages()             # ③ LLM 回复后才清理 ✅
```

`assemble_context()` 在 L567 已经把 ephemeral 消息打包到了 `messages` 列表中。LLM 通过 `messages` 参数接收上下文，`cleanup` 在 chat 返回后才执行。**时序正确，不存在 bug。**

但 Gemini 提出的**思考方向**是有价值的——如果未来重构将 `assemble_context()` 改为惰性求值，这个时序假设就会被打破。建议在代码注释中明确这个依赖。

---

## 三、新发现的遗漏

### 3.1 Promise + Hallucination 同时发生（GPT-5.2 提出）
AI 可能输出 `"当然，我这就执行 Bash 命令 python3 -c 'print(42)'"` — 既是承诺又是 hallucinated tool call。

**裁定**：Hallucination 检测优先于 Promise 检测。Pipeline 执行顺序应为：
```
HallucinationSanitizer → PromiseDetector → MixedResponseSplitter → Decoder
```

### 3.2 多语言承诺模式（Opus 提出）
日语/韩语/法语用户的承诺型回复不在检测范围内。

**裁定**：采用 Opus 建议的**通用规则**（短文本 + 无实质内容），减少对语言特定模式的依赖。关键词匹配作为辅助信号，不作为唯一判据。

### 3.3 `max_consecutive_thoughts > 1` 的配置兼容（Opus 提出）
如果外部配置 `max_consecutive_thoughts=2`，Promise Gate 的行为需要在两种配置下都正确。

**裁定**：Promise 检测逻辑独立于 `max_consecutive_thoughts`。Promise 在 Pipeline 层标记，`_handle_thought` 中优先检查 promise 标记，然后才检查 consecutive_thoughts 计数。

### 3.4 需要 `max_promise_retries` 保护（Opus + Gemini 共同提出）
如果 Promise Gate 返回 `is_final=False`，但模型持续输出承诺，需要退出条件。

**裁定**：增加 `promise_retry_count` 到 `ExecutionState`，上限 = 2。超过后强制视为 final answer。

---

## 四、最终方案

### Phase 1（P0 — 立刻做）

#### 1a. Prompt 优化
```python
# prompts.py BASE_RULES 追加（纯英文）
"""
5. **No Pre-announcement**: If you intend to use a tool, you MUST include the tool call 
   in the same response. A response without tool calls is treated as your final answer.
   Do NOT say "Let me search" or "I'll look into this" without an accompanying tool call.
6. **Sequential Tool Calls**: When multiple tools are needed, call the FIRST tool now.
   After receiving its result, call the NEXT tool. Never describe tool calls as text.
"""

# prompts.py TRAIT_GEMINI 追加
"""
- **CRITICAL**: You MUST use the function calling API.
  NEVER output tool calls as text like "<function_call>" or "<tool_code>".
  A response without a function call = your final answer to the user.
"""
```

#### 1b. `ExecutionState` 正式化
```python
# execution_state.py 新增字段
hallucination_count: int = 0
promise_retry_count: int = 0

# 在 reset() 中重置
# 在 create_snapshot()/restore_from_snapshot() 中序列化
```

### Phase 2（P1 — 核心修复）

#### 2a. `PromiseDetector` Pipeline Middleware
```python
# pipeline.py 新增
class PromiseDetector:
    """检测承诺型文本（短文本 + 承诺关键词 + 无实质内容）"""
    
    PROMISE_ZH = ["我这就", "我来", "让我", "马上", "我现在"]
    PROMISE_EN = ["I'll ", "I will ", "Let me ", "I'm going to "]
    SUBSTANCE_MARKERS = ["```", "1.", "- ", "* ", "：\n", ":\n", "\n\n"]
    MAX_PROMISE_LENGTH = 120
    
    def process_response(self, response, decoder):
        if response.tool_calls:
            return None  # 有 tool call，不是承诺
        if not response.content:
            return None
        
        text = response.content.strip()
        if len(text) > self.MAX_PROMISE_LENGTH:
            return None  # 长文本 = 完整回答
        if any(m in text for m in self.SUBSTANCE_MARKERS):
            return None  # 包含实质内容 = 完整回答
        if not any(p in text for p in self.PROMISE_ZH + self.PROMISE_EN):
            return None  # 不含承诺词
        
        # 标记为 promise，返回带标记的 ActionIR
        return [ActionIR(
            kind="THOUGHT", name="thought",
            args={"text": text},
            meta={"is_promise": True}
        )]
```

Pipeline 注册顺序：
```python
# ResponsePipeline.__init__
self.middleware = []
if features.firewall_hallucinations:
    self.middleware.append(HallucinationSanitizer(...))
if features.detect_promises:          # 新 feature flag
    self.middleware.append(PromiseDetector())
if features.split_mixed_responses:
    self.middleware.append(MixedResponseSplitter())
```

#### 2b. `_handle_thought` 适配
```python
async def _handle_thought(self, action: ActionIR) -> ToolResult:
    # 1. Non-blocking (from MixedResponseSplitter)
    if action.meta and action.meta.get("non_blocking"):
        return ToolResult(status="OK", output=action.args.get("text"), is_final=False)

    # 2. Promise Gate（from PromiseDetector pipeline）
    if action.meta and action.meta.get("is_promise"):
        self._state.promise_retry_count += 1
        if self._state.promise_retry_count <= 2:
            # 标记已写入的 assistant message 为 ephemeral
            msgs = self.mmu.current_frame.messages
            if msgs and msgs[-1].role == "assistant":
                msgs[-1].meta["ephemeral"] = True
            # 注入纠正
            self.mmu.add_user_message(
                "[System] Do not describe what you will do. Call the tool function directly."
            )
            if self.mmu.current_frame.messages:
                self.mmu.current_frame.messages[-1].meta["ephemeral"] = True
            return ToolResult(status="OK", output="", is_final=False)
        # 超过重试上限，视为 final answer
    
    # 3. Standard thought = Final answer
    self._state.on_thought()
    if self._state.consecutive_thoughts >= self.config.max_consecutive_thoughts:
        return await self._handle_return(action)
    return ToolResult(status="OK", output=action.args.get("text"), is_final=False)
```

#### 2c. Hallucination Recovery 增强（3 级）
```python
# vcpu.py step() 的 except Fault(ILL_INSTRUCTION) 分支
if self._state.hallucination_count == 1:
    # Level 1: 温和纠正 + positive example
    correction = (
        "[System] INVALID: You wrote tool calls as text. "
        "Use the function calling API. "
        "Your next response MUST contain an actual function call, not text."
    )
elif self._state.hallucination_count == 2:
    # Level 2: 清除 hallucinated messages + 强纠正
    self._purge_hallucination_messages()
    correction = (
        f"[System] You have repeatedly failed to use the function calling API. "
        f"Original user request: {original_goal}\n"
        f"Call the appropriate tool NOW using the API. Do NOT write text."
    )
else:  # >= 3
    # Level 3: 放弃，但给用户有意义的提示
    # (保持现有的 graceful termination 逻辑)
```

### Phase 3（P2 — 后续优化）

- `ModelFeatures` 新增 `detect_promises: bool` 和 `max_hallucination_retries: int`
- Gemini 默认 `detect_promises=True`, `max_hallucination_retries=5`
- 前端 UI 灰色气泡（独立 PR）

---

## 五、风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Promise Gate 误判（"让我解释" 被拦截） | 中 | 高（用户收不到回复） | 长度阈值 + 实质内容排除 + max_retry=2 |
| Promise Gate 漏判（新的承诺表达方式） | 中 | 低（用户多说一句话） | Prompt L1 覆盖大部分 |
| Hallucination 纠正仍然无效 | 中 (Gemini) | 中 | 分级恢复 + context purge |
| 长度阈值 120 太大/太小 | 低 | 低 | 可配置，根据运行数据调整 |

## 六、推荐实施顺序

| 步骤 | 内容 | 工时 | 阻塞关系 |
|------|------|------|---------|
| 1 | Prompt 优化 (1a) | 0.5h | 无 |
| 2 | ExecutionState 补字段 (1b) | 0.5h | 无 |
| 3 | PromiseDetector middleware (2a) | 2h | 依赖 1b |
| 4 | _handle_thought 适配 (2b) | 1h | 依赖 2a |
| 5 | Hallucination Recovery 3 级 (2c) | 2h | 依赖 1b |
| 6 | 单元测试（Promise 误判/漏判场景） | 2h | 依赖 3,4 |
| 7 | ModelFeatures + Gemini 配置 (Phase 3) | 1h | 依赖 3,5 |
