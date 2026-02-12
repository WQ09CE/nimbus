# AI Council Report:  帮忙我评审一下  docs/design/agentic-loop-promise-gate.md

2026/2/12 14:07:36

### Opinion from Claude Opus 4.5 (Deep Thinker)

好的，我已经仔细阅读了设计文档和所有相关源码。以下是我的全面评审意见：

---

## 📋 设计文档评审：`agentic-loop-promise-gate.md`

### 一、总体评价

**文档质量：优秀 (8.5/10)**

问题分析清晰、根因定位准确、分层方案结构合理。文档展现了对 vCPU 代码路径的深入理解。以下逐项给出具体意见。

---

### 二、问题定义评审 ✅ 准确

两个问题的划分非常合理，根因分析也与代码实际一致：

- **Issue 1**：代码确认 `max_consecutive_thoughts=1`（vcpu.py L156），`_handle_thought()` 在 L958 直接调用 `_handle_return()` 终止 loop——这确实会把承诺型文本误判为 final answer。
- **Issue 2**：`HallucinationSanitizer` → `Decoder._check_hallucination()` → `step() except Fault` 三段式链路描述准确。日志证据具体可信。

---

### 三、分层方案逐层评审

#### Layer 1: Prompt Policy — ✅ 赞同优先级最高

**优点**：零代码改动、立刻上线、风险极低。

**问题与建议**：

1. **规则编号冲突**：文档写 `BASE_RULES += "5. ... 6. ..."` 但当前 `BASE_RULES` 只有 4 条规则（1-4），所以编号是对的。但**追加方式有隐患**——如果未来有人在中间插入规则，编号会错乱。建议改为无序列表或自动编号。

2. **中英混杂**：`BASE_RULES` 的规则 5 里突然切换到中文（"对'好/继续/开始'等确认词..."），与前面全英文风格不统一。建议：
   - 要么全用英文（因为 `BASE_RULES` 是通用规则，面向所有模型）
   - 要么把中文场景挪到一个独立的 `TRAIT_ZH` 块

3. **覆盖率估计偏乐观**：文档说 Issue 1 ~80%，但 prompt 对模型是"软约束"。GPT-5.3 在高温度下仍可能先说后做。建议注明这是**最佳情况估计**，并说明为何仍需 Layer 2 兜底。

#### Layer 2: Runtime Promise Gate — ⚠️ 核心方案，有几个重要风险

**优点**：定位精准、改动量小（~20行）。

**问题与建议**：

1. **🔴 误判风险（最大隐患）**：`PROMISE_PATTERNS_ZH = ["我来", "我去", "让我", "马上"]` 这些模式**过于宽泛**。例如：
   - `"我来总结一下这个文件的内容：..."` ← 这是 final answer，不是承诺！
   - `"让我解释一下为什么这行代码有问题"` ← 也是 final answer
   - `"我去，这个 bug 好严重"` ← 感叹词，不是承诺
   
   **建议**：
   - 模式匹配不够，需要**组合条件**：`contains_promise_pattern AND len(text) < THRESHOLD AND no_substantive_content`
   - 或者用更精确的正则：`r"^(好的[，,]?)?(我(这就|来|现在就)|让我|马上).{0,20}$"` — 承诺型回复通常**很短**且**不包含实质信息**
   - 考虑加入 `negative patterns`：如果文本包含代码块、列表、超过 N 个字符的分析内容，则排除

2. **🟡 回溯标记 ephemeral 的时序问题**：文档提到"先 `mmu.add_assistant_message` 再回溯标记 ephemeral"。但看代码 vcpu.py L789 的 `add_assistant_message` 发生在 step() 主逻辑中，而 `_handle_thought` 是在 `_execute_action` 内部调用的。时序是：

   ```
   L789: mmu.add_assistant_message(content)  ← 写入
   L820: _execute_action() → _handle_thought() ← 此时要回溯修改上面写入的消息
   ```
   
   这意味着 `_handle_thought` 需要**反向查找**刚写入的 message 并标记 ephemeral。这是可行的，但：
   - 如果 `add_assistant_message` 和 `_handle_thought` 之间有其他消息插入（tool result 等），回溯就会标记错误的消息
   - **建议**：验证一下 THOUGHT action 路径是否保证这两步之间没有其他 message 插入。从代码看，对于纯 THOUGHT（无 tool_call），L789 到 L808 的 `_handle_thought` 之间只有 decode 和 action 遍历，不会有 tool result 插入，所以**时序是安全的**。但建议在代码注释中明确说明这个依赖关系。

3. **🟡 Pipeline 层 vs _handle_thought 层的架构选择**（文档待评审问题 #5）：
   
   我倾向于**在 Pipeline 层做检测**，理由：
   - Pipeline 的设计意图就是"在 decode 之前拦截/修改 response"
   - Promise 检测本质上是 response 分类，与 hallucination 检测是同一层次
   - 在 `_handle_thought` 中做需要回溯修改 memory，增加了 vcpu.py 的复杂度
   
   具体做法：在 `ResponsePipeline` 中增加 `PromiseDetector` middleware，在 `process_response` 阶段将承诺型文本转换为一个带 `meta={"is_promise": True}` 标记的 ActionIR，然后 `_handle_thought` 只需检查这个标记即可。这样**检测逻辑和执行逻辑解耦**。

#### Layer 3: Hallucination Recovery 增强 — ⚠️ 方向正确，部分过度设计

**优点**：分级恢复比"3 次就放弃"合理得多。

**问题与建议**：

1. **🟡 3b Context Purge 风险**（文档待评审问题 #4）：
   
   ```python
   if self.mmu.current_frame.messages:
       last = self.mmu.current_frame.messages[-1]
       if last.role == "assistant":
           self.mmu.current_frame.messages.pop()
   ```
   
   直接 `pop()` messages 列表的尾部元素有风险：
   - 如果 tool_call_id 已经发出但被 pop 掉，后续 tool result 的 `tool_call_id` 会找不到对应的 assistant message，部分 API（OpenAI）会报错
   - 但对于 hallucination 场景，assistant message 是纯文本（无 tool_call），所以**不存在 tool_call_id 孤儿问题**——此处是安全的
   - **建议**：增加断言 `assert not last.tool_calls`，防止误删带 tool_call 的消息

2. **🟡 3c 分级策略的 "context_reset" (Level 3) 实现复杂度被低估**：
   
   "清除近期对话，只保留原始任务"——这涉及到：
   - 如何定义"近期"？
   - 如何在 MMU 的 frame 结构中精准保留"原始任务"？
   - 已完成的 tool result 是否保留？
   
   建议将 Level 3 简化为：**清除所有 ephemeral messages + 注入一条包含原始用户请求的 summary**。这比"选择性保留"更安全。

3. **🔴 3c 的 "model_fallback" (Level 4) 不建议在此版本实现**：
   
   切换模型涉及：API key 切换、token 计费变更、system prompt 差异、tool schema 差异。这是一个独立的大特性，不应塞在 hallucination recovery 里。
   
   **建议**：Level 4 标记为 `# Future: model fallback (out of scope for this PR)`

4. **🟢 3d ModelFeatures 扩展**：
   
   `sequential_tool_hallucination_prone` 和 `max_hallucination_retries` 是合理的扩展。但命名建议简化：
   - `sequential_tool_hallucination_prone` → `hallucination_prone` （已经有 `firewall_hallucinations` flag 了，语义重复）
   - 或者直接复用现有 `firewall_hallucinations: bool` + 新增 `max_hallucination_retries: int`

#### Layer 4: 前端 UI — ✅ P2 合理

同意标记为 P2。承诺型文本灰色气泡是好的 UX 改进，但不是当前阻塞项。

---

### 四、遗漏的边界情况（文档待评审问题 #6）

1. **🔴 多语言承诺模式**：当前只覆盖了中文和英文。如果用户用日语/韩语/法语对话，AI 的承诺型回复也会是对应语言。建议：
   - 短期：增加检测**回复长度** + **是否包含实质内容**的通用规则，减少对语言特定模式的依赖
   - 长期：让 LLM 自分类（但这引入额外的 API 调用，成本过高，不推荐）

2. **🟡 Streaming 场景**：文档没有讨论 streaming 模式下的 Promise 检测。当 `_handle_thought` 被调用时，content 已经完整了（因为 stream 会先收集完再 decode），所以**没有问题**——但建议在文档中明确说明。

3. **🟡 并发/多 step 边界**：如果 `MixedResponseSplitter` 拆分出 `[THOUGHT, TOOL_CALL]`，THOUGHT 的 `non_blocking=True` 会跳过 Promise Gate。这是正确行为（因为已经有 tool_call 了），但值得在文档中点明。

4. **🟡 `max_consecutive_thoughts` > 1 的回退**：文档假设 `max_consecutive_thoughts=1`，但 `VCPUConfig` 允许外部配置。如果有人设为 2，Promise Gate 在第 1 次 THOUGHT 时会触发（因为 `consecutive_thoughts < max`），但 `_handle_thought` 不会走到 `_handle_return`——此时 Promise Gate 的位置在哪？需要确认逻辑在两种配置下都正确。

---

### 五、对待评审问题的直接回答

| # | 问题 | 我的判断 |
|---|------|---------|
| 1 | 分层方案是否合理？优先级？ | **合理**。L1→L2→L3 优先级正确。L4 可 defer。 |
| 2 | Promise 模式匹配的风险？ | **高**。误判率是主要风险。必须加组合条件（短文本 + 无实质内容）。见上文详细建议。 |
| 3 | 分级 hallucination 恢复是否过度设计？ | **Level 1-3 合理，Level 4 (model fallback) 过度**。建议砍掉 Level 4。 |
| 4 | Context Purge 会不会信息丢失？ | **对纯文本 hallucination 安全**。需断言防止误删带 tool_call 的消息。 |
| 5 | Pipeline 层还是 _handle_thought？ | **建议 Pipeline 层**。检测逻辑属于 response 分类，不应回溯修改 memory。 |
| 6 | 遗漏的边界情况？ | 多语言、streaming、`max_consecutive_thoughts>1` 配置。见上文第四节。 |

---

### 六、实施建议总结

| 优先级 | 行动项 | 预计改动 |
|--------|--------|---------|
| **P0** | L1 Prompt 注入（统一英文风格，中文场景移入 TRAIT） | ~10 行 prompts.py |
| **P0** | 将 Promise 检测做成 Pipeline middleware，而非 _handle_thought 回溯 | ~30 行 pipeline.py, ~5 行 vcpu.py |
| **P0** | Promise 模式匹配加**组合条件**（短文本 + 无代码块 + 无列表） | 检测函数内 |
| **P1** | L3 分级恢复（Level 1-3），砍掉 Level 4 | ~40 行 vcpu.py |
| **P1** | `ModelFeatures` 增加 `max_hallucination_retries` | ~5 行 manifest.py |
| **P2** | L4 前端 UI | 独立 PR |

**总结**：这是一份高质量的设计文档，问题分析和方案结构都很扎实。主要的改进方向是 **Promise 检测的精度**（避免误判）和 **检测层次的架构选择**（Pipeline 优于 _handle_thought 回溯）。建议在实现前先写 5-10 个 test case 覆盖误判/漏判场景，用测试驱动检测规则的调优。


### Opinion from GPT-5.2 (Generalist)

好的，我已经仔细阅读了设计文档和所有相关源码。以下是我作为 AI 顾问的全面评审意见。

---

## 📋 设计文档评审：`agentic-loop-promise-gate.md`

### 一、总体评价：**良好，值得实施，但有若干设计隐患需要修正**

文档的问题定义清晰，两个 Issue 的描述精准，日志证据充分，分层方案的思路也是正确的。整体架构意识很强 — 用分层防御（Prompt → Runtime → Recovery → UI）来应对 LLM 的不确定性，这是正确的工程策略。

但我在以下几个关键点上有不同意见或补充建议：

---

### 二、逐层详细评审

#### 🟢 Layer 1 (Prompt Policy) — **赞同，但措辞需要调整**

**优点**：零代码改动，风险极低，应该最先上线。

**问题 1：中英混写规则可能被忽略**

```python
BASE_RULES += """
5. ...
   对"好/继续/开始"等确认词，若上轮已形成可执行计划，本轮应直接执行工具，不要再次解释将要执行。
"""
```

这条中文规则嵌入在英文 `BASE_RULES` 中，LLM 对混合语言指令的遵从率不稳定。建议：
- `BASE_RULES` 保持纯英文（这是系统级指令）
- 中文行为引导放到 `TRAIT_*` 或单独的 locale 层

**问题 2：TRAIT_GEMINI 的"CRITICAL"标记**

当前 `TRAIT_GEMINI` 已经有 `"Do NOT emit XML tags like <tool_code>..."` 但你的日志显示 Gemini 仍然无视。追加更多文本的边际效果有限。建议结合 Layer 3 的 positive example 一起注入，而不是单独依赖 prompt。

**覆盖率估算偏高**：文档写 Issue 1 ~80%，Issue 2 ~60%。根据实际经验，Gemini 对 system prompt 的遵从率在复杂上下文中会显著下降。建议下调为 Issue 1 ~60%，Issue 2 ~30-40%，这样也更能说明 Layer 2/3 的必要性。

---

#### 🟡 Layer 2 (Promise Gate) — **核心方向正确，但检测机制有重大风险**

这是整个方案的**关键创新点**，也是风险最高的一层。

**🔴 风险 1：正则/关键词匹配的误判率**

```python
PROMISE_PATTERNS_ZH = ["我这就", "我来", "让我", "我现在去", "我去", "马上"]
PROMISE_PATTERNS_EN = ["I'll ", "I will ", "Let me ", "I'm going to "]
```

这些模式的**假阳性 (False Positive)** 非常高：

| 文本 | 实际语义 | 匹配结果 |
|------|---------|---------|
| "我来解释一下这个概念" | ✅ Final Answer | ❌ 误判为 Promise |
| "Let me explain why this approach works" | ✅ Final Answer | ❌ 误判为 Promise |
| "I'll summarize the key findings" | ✅ Final Answer（总结） | ❌ 误判为 Promise |
| "让我告诉你为什么这行不通" | ✅ Final Answer | ❌ 误判为 Promise |

**如果误判为 Promise，后果是**：注入纠正指令 → LLM 被迫调工具 → 执行无意义的操作 → 用户体验下降甚至出错。

**建议的改进方案**：

**方案 A（推荐）—— 语义分类替代关键词匹配**

不要用正则匹配，改为用一个轻量级的**启发式评分**：

```python
def _is_promise(self, text: str, has_actionable_context: bool) -> bool:
    """
    判断是否为承诺型文本。
    需要同时满足：
    1. 文本短（< 80 字符） — 真正的 final answer 通常更长
    2. 包含 promise 关键词
    3. 不包含实质性内容（解释、代码、列表等）
    4. 上下文中有可执行的工具（用户要求了搜索/创建等操作）
    """
    if len(text) > 120:
        return False  # 长文本几乎不可能是纯 promise
    
    has_promise_word = any(p in text for p in PROMISE_PATTERNS)
    has_substance = any(marker in text for marker in ['```', '1.', '- ', '：', ':'])
    
    return has_promise_word and not has_substance and has_actionable_context
```

**方案 B —— 用 LLM 自分类（成本略高但精确）**

在 decoder 层添加一个 `role` 字段要求：在 system prompt 中要求 LLM 对自己的 response 打标签：
```
If your response is a final answer, prefix with [FINAL].
If you intend to call tools next, include the tool call in this same response.
```

这样 decoder 层有更可靠的信号源。但这依赖 LLM 遵从，也不是 100%。

**🟡 风险 2：内存时序的回溯标记**

文档提到：
```
step() L789: mmu.add_assistant_message(response.content)  ← 已写入
step() L820: _execute_action() → _handle_thought()        ← 回溯标记 ephemeral
```

我看了代码，实际时序是：

```python
# vcpu.py L790-800 范围
elif response.content:
    self.mmu.add_assistant_message(response.content)  # ① 写入内存

# ... later ...
results = await asyncio.gather(
    *(self._execute_action(action) for action in actions),  # ② 执行
)
```

问题在于：**`_handle_thought` 内部需要回溯修改已经写入 MMU 的消息**。这意味着你需要在 `_handle_thought` 里拿到 `self.mmu.current_frame.messages[-1]` 并标记 ephemeral。但如果有并发 actions（虽然 THOUGHT 通常是唯一 action），这个索引可能不安全。

**建议**：Promise Gate 检测应该提前到**内存写入之前**（即在 `process_response` pipeline 阶段或写入 MMU 之前），这也回答了文档中的"待评审问题 5"。具体来说：

```python
# 在 MEMORY UPDATE 段之前插入
if actions and len(actions) == 1 and actions[0].kind == "THOUGHT":
    if self._is_promise(actions[0].args.get("text", "")):
        # 不写入 MMU，直接注入纠正
        self.mmu.add_user_message("[System] Call the tool directly.")
        self.mmu.current_frame.messages[-1].meta["ephemeral"] = True
        continue  # 跳过后续执行，进入下一个 step
```

这样避免了回溯修改的复杂性。

---

#### 🟡 Layer 3 (Hallucination Recovery) — **方向正确，但分级策略过度设计**

**🟢 3a. 更强的纠正 prompt — 完全赞同**

当前的纠正太弱了。带 positive example 的纠正是必要的改进。但建议纠正内容**不要太长**，避免挤占 context window：

```python
correction = (
    "[System] WRONG: You wrote tool calls as text. "
    "RIGHT: Use the function calling API. "
    "Your next response MUST contain an actual tool_call."
)
```

**🟢 3b. Context Purge — 赞同，但需要更精确**

当前代码：
```python
self.mmu.add_assistant_message("I need to use proper function calls.")
if self.mmu.current_frame.messages:
    self.mmu.current_frame.messages[-1].meta["ephemeral"] = True
```

这里 `ephemeral` 标记的作用取决于 MMU 的 compaction 逻辑是否会清除它们。我没在代码中看到 `cleanup_ephemeral_messages()` 的实现 — **这是一个需要新增的方法**。文档假设它存在但实际上需要开发。

**🔴 3c. 分级策略 — 过度设计，建议简化**

```python
HALLUCINATION_STRATEGIES = {
    1: "gentle_correction",
    2: "strong_correction",
    3: "context_reset",
    4: "model_fallback",
    5: "give_up",
}
```

**问题**：
- **5 级策略增加了大量分支复杂度**，每一级都需要独立测试
- `"context_reset"` (清除近期对话只保留原始任务) — 这是一个很重的操作，可能丢失有价值的中间状态
- `"model_fallback"` — 运行时切换模型涉及 API key 管理、manifest 切换、可能不同的 tool schema 格式，复杂度极高，不适合在此方案中引入

**建议简化为 3 级**：

```python
HALLUCINATION_STRATEGIES = {
    1: "correction_with_example",   # 纠正 + positive example
    2: "purge_and_retry",           # 清除最近的 hallucinated msg + 更强纠正
    3: "give_up_gracefully",        # 放弃但给用户有意义的提示
}
```

这已经覆盖了 95% 的场景。Model fallback 可以作为 P3 的独立方案后续考虑。

**🟡 3d. ModelFeatures 控制 — 赞同，但命名建议调整**

```python
sequential_tool_hallucination_prone: bool = False
```

建议改为更通用的命名：

```python
hallucination_retry_budget: int = 3  # 默认 3，Gemini 设为 5
```

这样不需要 `bool + int` 两个字段，一个字段就搞定。

---

#### 🟢 Layer 4 (前端 UI) — **赞同标记为 P2，暂不实施**

无需过多评论，这确实是锦上添花。

---

### 三、文档中遗漏的重要边界情况

**🔴 遗漏 1：Promise + Hallucination 同时发生**

文档将 Issue 1 和 Issue 2 视为独立问题，但它们可能**同时发生**：

```
LLM 返回: "当然，我这就执行 Bash 命令 python3 -c 'print(42)'"
           ↑ Promise 型文本        ↑ 文本模拟的 tool call
```

这种情况下，Promise Gate (L2) 和 Hallucination Sanitizer (L3) 的处理顺序和优先级需要明确。建议：**Hallucination 检测优先于 Promise 检测**（因为 hallucination 是更严重的问题）。

**🟡 遗漏 2：`hallucination_count` 未在 `ExecutionState` 中正式管理**

当前代码用了 `getattr(self._state, 'hallucination_count', 0)`：

```python
self._state.hallucination_count = getattr(self._state, 'hallucination_count', 0) + 1
```

这是个明显的 **tech debt** — `hallucination_count` 应该正式加入 `ExecutionState` dataclass，并在 `reset()` 和 `create_snapshot()`/`restore_from_snapshot()` 中处理。否则 checkpoint 恢复后 hallucination 计数会丢失。

**🟡 遗漏 3：Promise Gate 的计数器重置时机**

如果 Promise Gate 检测到承诺并注入纠正，下一轮 LLM 成功调用了工具，此时 `consecutive_thoughts` 计数器需要重置。但文档中没有提到 Promise Gate 如何与 `ExecutionState.on_action()` 配合。需要确保 `on_action()` 能正确重置相关状态。

**🟡 遗漏 4：流式输出 (Streaming) 场景**

Pipeline 的 `HallucinationSanitizer` 有 `process_chunk()` 方法处理流式。但 Promise Gate 如果也放在 Pipeline 层，需要考虑：流式场景下，"让我搜一下" 这样的文本可能分散在多个 chunk 中，单个 chunk 无法判断。这意味着 Promise 检测**只能在 `process_response` 阶段**做，不能在 `process_chunk` 阶段做。文档未提及这一点。

---

### 四、对"待评审问题"的逐一回答

> **Q1: 分层方案是否合理？优先级排序是否正确？**

**合理**。L1 → L2 → L3 的优先级排序正确。建议 L1 和 L2 可以在同一个 PR 中实施（L1 改 prompt，L2 改 vcpu.py，互不冲突）。L3 作为独立 PR。

> **Q2: Layer 2 的模式匹配检测有什么风险？**

**主要风险是假阳性**，如上文详细分析。"让我解释一下" "I'll summarize" 等都会误触发。建议增加长度门槛 + 实质内容检测，或改用语义评分。

> **Q3: Layer 3 的分级 hallucination 恢复是否过度设计？**

**是的，5 级过度**。建议简化为 3 级。Model fallback 作为独立的后续方案。

> **Q4: Context Purge 会不会导致信息丢失？**

**会**。但在 hallucination 循环中，这些信息本身就是有毒的（LLM 会从自己的 hallucinated output 中学习并重复）。所以**信息丢失是可接受的代价**。但要注意只清除 hallucinated messages，不要误删用户的原始输入。

> **Q5: 是否应该在 Pipeline 层做 Promise 检测，而非在 _handle_thought 中回溯？**

**是的，强烈建议放在 Pipeline 层或内存写入之前**，避免回溯修改。理由见上文分析。

> **Q6: 是否存在遗漏的边界情况？**

见上文"遗漏的重要边界情况"4 条。

---

### 五、改动量和风险评估的修正

| 方案 | 文档估算 | 我的评估 | 调整理由 |
|------|---------|---------|---------|
| L1 Prompt | ~5 行，极低风险 | ~10 行，极低风险 | 需要分中英文两处改 |
| L2 Promise Gate | ~20 行，中风险 | **~40-50 行，中高风险** | 需要增加误判防护逻辑、与 Pipeline 集成 |
| L3 Recovery | ~40 行，低风险 | **~60 行（简化后），中风险** | 需要新增 `cleanup_ephemeral_messages()`、修复 `ExecutionState` |
| L4 UI | 中等，低风险 | 同意 | — |

---

### 六、总结建议

1. **立即实施** L1 (Prompt) — 零风险，马上有收益
2. **L2 Promise Gate** 是关键但要重新设计检测逻辑：
   - 放弃纯关键词匹配 → 改用**短文本 + 关键词 + 无实质内容**的组合启发式
   - 检测位置从 `_handle_thought` 移到**内存写入之前**
3. **L3 Hallucination Recovery** 简化为 3 级，先落地再迭代
4. 将 `hallucination_count` 正式纳入 `ExecutionState` — 这是必要的前置重构
5. 补充 Promise + Hallucination 同时发生的处理优先级说明

**整体评分：7.5/10** — 问题分析和分层思路优秀，但 Promise Gate 的检测机制需要重新打磨，Layer 3 的分级策略需要简化。


### Opinion from Gemini 3 Pro High (Reasoning)

好的，我已经仔细阅读了设计文档和所有相关源码。以下是我的全面评审意见。

---

# 🔍 设计文档评审：`agentic-loop-promise-gate.md`

## 一、总体评价

**文档质量：优秀（8/10）**。问题定义清晰，根因分析到位，分层方案有明确的优先级和 ROI 考量。以下逐层给出具体意见。

---

## 二、问题分析的评审

### ✅ 优点
1. **两个 Issue 分离得当**。承诺型文本（Issue 1）和 Gemini Hallucination（Issue 2）本质上是不同的问题，但共享了 `max_consecutive_thoughts=1` 这条路径，文档正确地识别了这一点。
2. **日志证据充分**。Issue 2 附带了实际运行日志，可追溯性好。
3. **现有防御链路分析**完整，从 Pipeline → Decoder → vCPU 的流转描述准确，与源码吻合。

### ⚠️ 需要补充
1. **缺少量化数据**。文档声称 Gemini "高频" 触发 hallucination，但没有给出统计数据（如：100 次 sequential tool call 场景中触发率是多少？）。建议补充采样数据来论证方案的优先级。
2. **Issue 1 的触发频率也不明确**。"GPT-5.3, 部分 Gemini" 是模糊描述。哪些具体 prompt 会触发？是中文场景更多还是英文也有？这直接影响 Layer 2 的模式匹配设计。

---

## 三、分层方案逐层评审

### Layer 1: Prompt Policy — ✅ 同意 P0

**评价：正确的第一步，风险极低，值得立刻做。**

**具体意见：**

1. **`BASE_RULES` 中的规则 5 写得好**，但有一处措辞风险：
   > "Text-only responses are treated as your FINAL answer."
   
   这句话对模型来说是**强声明**，可能导致模型在真正需要分步思考时也强行调工具。建议改为：
   > "If you intend to use a tool, you MUST include the tool call in the same response. A response without tool calls is treated as your final answer."

2. **`TRAIT_GEMINI` 的追加规则**中提到 `"[Called tool...]"`——这是 Nimbus 自身的 `[Historical context:` 前缀模式，不是 Gemini 会自然生成的文本。建议把示例改为 Gemini 实际 hallucinate 的模式，如 `<tool_code>` 或 `<function_calls>`，与 `decoder.py` 的 `HALLUCINATION_PATTERNS` 保持一致。

3. **覆盖率估算（Issue 1: ~80%, Issue 2: ~60%）合理**但偏乐观。Prompt 对 Gemini 的实际控制力往往低于预期，建议 Issue 2 的覆盖率估算降到 ~40%。

### Layer 2: Promise Gate — ⚠️ 有重要设计风险

**评价：核心方向正确，但检测机制和内存操作的设计需要修改。**

#### 风险 1：模式匹配的误判率（False Positive）

```python
PROMISE_PATTERNS_ZH = ["我这就", "我来", "让我", "我现在去", "我去", "马上"]
PROMISE_PATTERNS_EN = ["I'll ", "I will ", "Let me ", "I'm going to "]
```

这些模式**过于宽泛**，会产生大量误判：

| 模型实际输出 | 是否为承诺 | 是否匹配 |
|---|---|---|
| "让我**总结一下**：你的代码问题在于..." | ❌ 是 Final Answer | ✅ 会误判 |
| "我来**解释一下**这个错误的原因" | ❌ 是 Final Answer | ✅ 会误判 |
| "I'll explain the issue..." | ❌ 是 Final Answer | ✅ 会误判 |
| "I'll search for that now" | ✅ 是承诺 | ✅ 正确 |

**建议改进：** 不要只做前缀/子串匹配，而是结合**上下文意图**来判断：

```python
def _is_promise(self, text: str, last_user_msg: str) -> bool:
    """
    判断是否为"承诺型"回复。
    要求同时满足：
    1. 文本包含承诺模式
    2. 文本较短（< 100 chars），排除包含完整解释的回复
    3. 上一条用户消息包含工具意图（搜/查/执行/创建...）
    """
    if len(text) > 150:  # 长文本大概率是完整回答
        return False
    # ... pattern matching
```

这样可以把误判率大幅降低。**核心原则：宁可漏判也不要误判**——误判的代价是用户收不到 Final Answer（无限 loop），漏判的代价只是需要用户多发一句话。

#### 风险 2：内存时序的回溯操作

文档描述的时序：
```
step() L789: mmu.add_assistant_message(response.content)  ← 已写入
step() L820: _execute_action() → _handle_thought()        ← 回溯标记 ephemeral
```

但查看实际代码（vcpu.py L793-L810），内存写入发生在 `process_response` 之后、`_execute_action` 之前。这意味着 `_handle_thought` 需要去修改**已经写入 MMU 的消息**。当前代码确实有类似操作（hallucination 分支也这样做），但这种 **"先写入再回溯"** 的模式在并发场景下是有风险的。

**建议：** 在 `_handle_thought` 中检测到 promise 后，不要回溯修改已有消息，而是：
1. 把当前消息直接标记 ephemeral（可以，因为它是刚刚添加的最后一条）
2. 同时注入纠正消息
3. 返回 `is_final=False`

这与文档描述的基本一致，但要**明确保证只修改最后一条消息**，并添加防御性检查：

```python
# 在 _handle_thought 中
msgs = self.mmu.current_frame.messages
if msgs and msgs[-1].role == "assistant" and msgs[-1].content == action.args.get("text"):
    msgs[-1].meta["ephemeral"] = True
```

#### 风险 3：无限 Loop 保护

如果 Promise Gate 判定为非 Final（`is_final=False`），但模型下一轮继续输出承诺型文本怎么办？文档没有描述**递归承诺**的退出条件。

**建议：** 增加 `max_promise_retries` 限制（建议 = 2）：
```python
if self._state.promise_retry_count >= 2:
    # 两次承诺后仍无工具调用，视为 final answer
    return await self._handle_return(action)
```

### Layer 3: Hallucination Recovery 增强 — ⚠️ 部分过度设计

#### 3a. 更强的纠正 prompt — ✅ 同意

当前的纠正文本确实太弱。建议的强化版本合理。但有一个关键遗漏：**纠正 prompt 应该把用户的原始请求重新注入**，避免模型在 hallucination 纠正后"忘了自己要做什么"：

```python
correction = f"""[System] INVALID RESPONSE. Use the function calling API, not text.
Original user request: {original_user_message}
Call the appropriate tool NOW."""
```

#### 3b. Context Purge — ⚠️ 需要谨慎

文档提议：
```python
if self._state.hallucination_count >= 2:
    self.mmu.cleanup_ephemeral_messages()
    # 移除最后一条被污染的 assistant message
```

这里的 `cleanup_ephemeral_messages()` 会清除**所有** ephemeral 消息，包括之前正常的错误恢复提示。这可能会导致：
- 之前工具错误恢复的上下文丢失
- 模型失去"为什么之前失败了"的记忆

**建议：** 不要调用全局的 `cleanup_ephemeral_messages()`，而是只删除最近 N 条被标记为 hallucination 的消息。可以在 meta 中加一个 `hallucination=True` 标记来区分：

```python
# 只清除 hallucination 相关的 ephemeral 消息
msgs = self.mmu.current_frame.messages
self.mmu.current_frame.messages = [
    m for m in msgs 
    if not m.meta.get("hallucination_correction")
]
```

#### 3c. 分级策略 — ⚠️ 过度设计

```python
HALLUCINATION_STRATEGIES = {
    1: "gentle_correction",
    2: "strong_correction",
    3: "context_reset",
    4: "model_fallback",    # ← 这个
    5: "give_up",
}
```

**`model_fallback`（策略 4）不建议在此版本实现**，原因：
1. 模型切换涉及不同的 `ModelManifest`、`PromptManager`、API key 路由，复杂度远超一个 hallucination handler
2. 切换模型后上下文格式可能不兼容（如 tool_call_id 格式不同）
3. 这应该是一个独立的 feature（"模型降级/fallback 策略"），不应耦合在 hallucination 处理中

**建议：** 保留 1-3 + 5，去掉 4。把 `max_hallucination_retries` 设为 4（而不是 5），快速收敛。

#### 3d. ModelFeatures 扩展 — ✅ 方向正确

```python
sequential_tool_hallucination_prone: bool = False
max_hallucination_retries: int = 3
```

同意将这些参数提升到 `ModelFeatures` 中。但要注意 **`hallucination_count` 目前是通过 `getattr` 动态添加到 `ExecutionState` 上的**（见 vcpu.py L710），而不是在 `ExecutionState` 的 `__init__` 中定义。这是一个 **tech debt**——应该在实现 Layer 3 时一并修复，将 `hallucination_count` 正式加入 `ExecutionState` dataclass。

---

## 四、架构层面的关键建议

### 建议 1：Promise 检测应该在 Pipeline 层，而不是 `_handle_thought`

文档的"待评审问题 5"问到了这一点。我的答案是：**是的，应该在 Pipeline 层**。

理由：
1. **职责分离**：Pipeline 的职责就是"在 LLM 输出到达 vCPU 前做预处理"。Promise 检测属于"语义分类"，与 `HallucinationSanitizer` 同层。
2. **避免回溯操作**：如果在 Pipeline 中检测到 promise，可以直接修改 `response` 对象（如注入 `meta` 标记），vCPU 不需要再回溯修改已写入的消息。
3. **可测试性**：Pipeline middleware 可以单独单测，而 `_handle_thought` 深埋在 vCPU 中，测试需要 mock 大量依赖。

**建议实现**：
```python
class PromiseDetector:
    """Pipeline middleware: 检测承诺型文本"""
    
    def process_response(self, response, decoder):
        if response.content and not response.tool_calls:
            if self._is_promise(response.content):
                # 标记，让 vCPU 知道这不是 final answer
                response._promise_detected = True
        return None  # 继续走 decoder
```

然后在 vCPU 的 `_handle_thought` 中检查这个标记即可，逻辑更干净。

### 建议 2：补充边界情况（文档待评审问题 6）

以下是文档遗漏的边界情况：

| 边界情况 | 风险 | 建议 |
|---|---|---|
| **Streaming 场景下的 Promise 检测** | 文本分块到达，模式可能跨 chunk 被截断 | Promise 检测只在 `process_response`（完整文本）中做，不做 streaming 检测 |
| **Tool Call + 承诺型文本同时出现** | `MixedResponseSplitter` 已分离，但 Promise Gate 可能误触发 | 如果 `response.tool_calls` 非空，跳过 Promise 检测 |
| **多语言混合**（如 "OK let me 搜一下"） | 中英文模式都只单独匹配 | 合并为一个列表，或做全文扫描 |
| **`cleanup_ephemeral_messages` 在 step 开头被调用** | vcpu.py L617-621 在每个 step 开头清理 ephemeral。如果 Promise Gate 注入的纠正消息被标为 ephemeral，下一个 step 开头它就被删了 | ✅ 这其实是正确的——ephemeral 的纠正消息在模型看到并回复后就应该被清理。但要确认时序：注入在 step N 末尾，模型在 step N+1 开头看到它，然后 step N+1 的 cleanup 在模型回复**之后**才执行。**查看代码确认：L617 的 cleanup 在 LLM 调用之前（L633），这意味着纠正消息会在模型看到之前就被删除！** 这是一个 **严重 bug**。 |

**⚠️ 关键发现：Ephemeral 消息的清理时序问题**

查看 vcpu.py 的 step() 方法：
```
L617: cleaned_count = self.mmu.cleanup_ephemeral_messages()  # ← 先清理
...
L633: response = await self._think(...)  # ← 再调 LLM
```

这意味着在 step N 末尾注入的 ephemeral 纠正消息，会在 step N+1 的 `_think()` 调用**之前**被清理掉。模型永远看不到纠正消息！

但仔细想一下——**这取决于 `_think()` 构建 prompt 的时间点**。如果 `_think()` 是从 `mmu.current_frame.messages` 读取的，而 cleanup 已经先执行了，那纠正消息确实会被删。

**这个问题不仅影响 Promise Gate 的设计，也影响现有的 hallucination 纠正（Layer 3）**。建议在实施任何改动前先确认这个时序是否正确。如果有 bug，需要把 `cleanup_ephemeral_messages()` 移到 step 末尾（LLM 回复之后）而非 step 开头。

---

## 五、预期效果表的评审

| 方案 | 文档覆盖率 | 我的修正估算 | 原因 |
|---|---|---|---|
| L1 Prompt Policy (Issue 1) | ~80% | ~70% | Gemini/GPT-5.3 对 prompt 的遵从不够稳定 |
| L1 Prompt Policy (Issue 2) | ~60% | ~35-40% | Gemini 对 "不要用文本模拟" 的指令遵从率很低 |
| L2 Promise Gate | ~95% | ~85% | 误判/漏判会吃掉 10% |
| L3 Hallucination Recovery | ~85% | ~75% | Context purge 有信息丢失风险 |

---

## 六、总结建议

### 实施优先级建议

| 优先级 | 任务 | 预估工作量 |
|---|---|---|
| **P0** | Layer 1: Prompt 优化（立即做） | 0.5h |
| **P0** | **修复 ephemeral cleanup 时序问题**（阻塞后续所有层） | 1h |
| **P1** | Layer 2: Promise Gate（在 Pipeline 层实现，而非 `_handle_thought`） | 3h |
| **P1** | 将 `hallucination_count` 正式加入 `ExecutionState` | 0.5h |
| **P1** | Layer 3a+3b: 增强纠正 prompt + 精确 context purge | 2h |
| **P2** | Layer 3c: 分级策略（去掉 model_fallback） | 2h |
| **P2** | Layer 3d: ModelFeatures 扩展 | 1h |
| **P3** | Layer 4: 前端 UI | 另行评估 |

### 核心改动清单

1. ✅ **Prompt 优化**：修改 `prompts.py`，措辞微调
2. 🔴 **确认/修复 ephemeral cleanup 时序**：`vcpu.py` L617 vs L633
3. ✅ **新增 `PromiseDetector` middleware**：`pipeline.py`，附带长度+意图双重检测
4. ✅ **增强 hallucination 纠正**：`vcpu.py` 的 except Fault 分支
5. ✅ **`ExecutionState` 补充 `hallucination_count` 和 `promise_retry_count`**
6. ✅ **补充单测**：针对 Promise 检测的各种边界情况

---

总体来说，这是一份高质量的设计文档。主要风险在于 **Promise 模式匹配的误判率** 和 **ephemeral 消息的清理时序**。建议在实施前先验证时序问题，然后按上述优先级推进。
