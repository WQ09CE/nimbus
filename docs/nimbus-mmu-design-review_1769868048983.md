# AI Council Report: 请评审 Nimbus MMU（Memory Management Unit）的设计。这是 Agent 最核心的组件。 ## 文件信息 - mmu.py: 914 行 - context.py: 276 行 - 总计: ~1,190 行 ## 核心功能 1. **内存布局**: Pinned Context (永不压缩) + Stack Frames (调用栈隔离) 2. **Context Stack 提炼**: pop_frame 时自动提取有价值内容，丢弃失败的探索 3. **Tool Call 标记**: valuable/failed/exploratory/intermediate 四级标记 4. **Token 预算管理**: 分层预算 (pinned/frame/total) ## 核心创新 ```python def pop_frame(self, result=None, extract_valuable=True): """ 弹出栈帧时: 1. 自动检测失败的 tool calls 2. 过滤 failed/exploratory 调用 3. 只保留结论和成功操作到父 frame """ ``` ## 潜在问题 1. 失败检测基于关键词，可能误判 2. Token 估算不精确 (4 chars/token) 3. 提炼可能丢失重要中间信息 4. 与外部 CompactionEngine 职责边界模糊 ## 评审问题 1. MMU 作为核心组件，设计质量如何？ 2. Context Stack 提炼是否值得这么复杂？ 3. StackFrame 隔离是否必要？实际 SUB_CALL 使用多吗？ 4. Tool Call 标记系统是否过度设计？ 5. 哪些可以简化？哪些应该增强？ 请从架构设计、实用性、复杂度三个角度评审。

1/31/2026, 10:00:48 PM

### Opinion from Claude Opus 4.5 (Deep Thinker)

现在我已经完整阅读了代码，让我进行系统性评审。

---

# Nimbus MMU 设计评审报告

## 📊 总体评价

| 维度 | 评分 | 评价 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐ (4/5) | 概念清晰，分层合理，但有职责重叠 |
| 实用性 | ⭐⭐⭐ (3/5) | 核心功能实用，高级功能使用率存疑 |
| 复杂度 | ⭐⭐⭐ (3/5) | 部分过度设计，可简化 |

---

## 一、架构设计评审

### ✅ 优点

**1. 内存布局设计精良**
```
┌─────────────────────────────────┐
│        Pinned Context           │  ← 永不压缩，绝佳设计
├─────────────────────────────────┤
│        Root Frame               │
├─────────────────────────────────┤
│        Sub Frame 1              │  ← 调用栈隔离
└─────────────────────────────────┘
```
- `PinnedContext` 保证系统规则不丢失 — **这是关键创新**
- `pin_user_goal()` 确保用户目标始终可见 — 解决了 compaction 丢失任务的痛点

**2. 数据结构简洁**
```python
@dataclass
class Message:  # 276行 context.py，非常精简
    role: MessageRole
    content: Any
    tool_calls: Optional[List[Dict]]
    # ...
```

**3. 关注点分离**
- `context.py`: 数据结构定义
- `mmu.py`: 状态管理 + 操作逻辑
- `compaction.py`: 压缩策略（外部模块）

### ⚠️ 问题

**1. 职责边界模糊（MMU vs CompactionEngine）**

| 功能 | MMU | CompactionEngine |
|------|-----|------------------|
| `mark_tool_call()` | ✅ 有 | ✅ 也有 |
| `_auto_detect_tool_failure()` | ✅ 有 | ✅ 也有类似逻辑 |
| `_filter_discardable_messages()` | ✅ 有 | ✅ 也在做过滤 |

**这是设计缺陷** — 两个模块有重复逻辑。

**2. Token 估算过于粗糙**
```python
def token_estimate(self) -> int:
    return len(self.content) // 4  # 4 chars/token
```
- 英文约 4 chars/token ✅
- 中文约 1.5 chars/token ❌（严重低估）
- 这会导致中文场景 token 预算失控

---

## 二、Context Stack 提炼评审

### 核心逻辑分析

```python
def pop_frame(self, result=None, extract_valuable=True):
    if result is None and extract_valuable:
        result = self._extract_valuable_content(frame)  # 自动提取
    # ...
```

### ⚠️ 问题

**1. `_extract_valuable_content()` 启发式过于简单**
```python
# 只保留最后一个 assistant 结论
for msg in reversed(frame.messages):
    if msg.role == "assistant" and not msg.tool_calls:
        last_assistant_content = content
        break
```
- 只取最后一条文本回复
- 可能丢失重要的中间推理
- **没有使用 LLM 做智能摘要**（与 CompactionEngine 的做法不一致）

**2. 失败检测基于关键词 — 可能误判**
```python
failure_indicators = [
    "not found",      # grep 没找到也触发
    "no matches",     # 正常空结果也触发
    "permission denied",
]
```
例如：`grep "pattern" file.txt` 返回 `no matches found` — 这可能是**正常的空结果**，不应标记为 failed。

### 💡 建议

```python
# 改进：区分 "工具失败" vs "结果为空"
def _auto_detect_tool_failure(self, tool_call_id, tool_name, content):
    # 只有明确的错误才标记为 failed
    if content.startswith("[Error]") or "Exception" in content:
        self.mark_tool_call(tool_call_id, "failed", ...)
    # "not found" 类的结果标记为 "empty_result"，不过滤
```

---

## 三、StackFrame 隔离必要性分析

### 当前使用情况

```bash
# SUB_CALL 在测试中出现 25 次
grep -r "push_frame\|SUB_CALL" tests/ | wc -l  # 25
```

### vcpu.py 中的使用
```python
async def _handle_sub_call(self, action):
    frame_id = self.mmu.push_frame(goal, meta={"action_id": action.id})
    # ...
```

### 评估

| 场景 | 是否需要 StackFrame |
|------|---------------------|
| 单轮任务 | ❌ 不需要 |
| 多步任务（无子调用）| ❌ 不需要 |
| Agent 递归调用 Agent | ✅ 需要 |
| Tool 内嵌 Agent | ✅ 需要 |

**结论**：StackFrame 是**未来证明型设计**（future-proof），当前可能用不上，但架构正确。

### 💡 建议
- **保留设计**，但可以考虑延迟实现
- 如果 90% 场景只用 root frame，可以简化默认路径

---

## 四、Tool Call 标记系统评审

### 四级标记
```python
ToolCallValue = Literal["valuable", "failed", "exploratory", "intermediate"]
```

### 使用情况分析

| 标记 | vcpu.py 自动使用 | 手动使用场景 |
|------|------------------|--------------|
| `valuable` | ❌ 没有自动标记 | 需要手动调用 |
| `failed` | ✅ 自动检测 | 自动 |
| `exploratory` | ❌ 没有自动标记 | 需要手动调用 |
| `intermediate` | ❌ 没有自动标记 | 需要手动调用 |

**问题**：4 个标记只自动使用了 1 个！

### 💡 评估

这是**过度设计**的典型案例：
- 设计了精细的分类系统
- 但自动化程度不够
- 依赖手动标记 → 实际不会用

**建议简化为两级**：
```python
ToolCallValue = Literal["keep", "discard"]  # 二元决策更实用
```

---

## 五、具体问题回答

### Q1: MMU 作为核心组件，设计质量如何？

**评分：B+**

优点：
- 概念模型清晰（Pinned + Stack）
- API 设计合理
- 状态管理完整

不足：
- 与 CompactionEngine 职责重叠
- Token 估算不准确
- 部分功能过度设计

### Q2: Context Stack 提炼是否值得这么复杂？

**不太值得**

当前复杂度：~200 行代码用于提炼逻辑

问题：
- 提炼质量依赖简单启发式
- 可能丢失重要信息
- 没有 LLM 辅助摘要（CompactionEngine 有）

**建议**：要么简化（只保留 pop_frame 结果），要么增强（用 LLM 摘要）

### Q3: StackFrame 隔离是否必要？

**架构上必要，当前不紧迫**

- 为递归 Agent 调用预留了正确抽象
- 但当前 90%+ 场景只用 root frame
- 可以保留设计，但简化实现

### Q4: Tool Call 标记系统是否过度设计？

**是的，过度设计**

证据：
- 4 级标记只自动使用 1 级
- 手动标记 API 测试中才有使用
- 实际运行时几乎不会手动调用

**建议**：简化为 `keep/discard` 二元分类

### Q5: 哪些可以简化？哪些应该增强？

#### 应该简化 ✂️

1. **Tool Call 标记**：4 级 → 2 级
2. **失败检测**：去掉模糊关键词匹配
3. **Token 估算**：移除或标记为 deprecated（应该用真实 tokenizer）

#### 应该增强 💪

1. **Token 估算**：接入真实 tokenizer（tiktoken）
2. **职责边界**：明确 MMU vs CompactionEngine 的分工
3. **语言检测**：`_detect_user_language()` 放错位置，应该在更高层

---

## 六、重构建议

### 短期（Quick Win）

```python
# 1. 简化 Token 估算，加警告
def token_estimate(self) -> int:
    """Rough estimate. Use real tokenizer for accuracy."""
    # TODO: 接入 tiktoken
    return len(self.content) // 3  # 更保守的估算

# 2. 简化 Tool Call 标记
ToolCallValue = Literal["keep", "discard"]

# 3. 移除模糊失败检测
def _auto_detect_tool_failure(self, ...):
    # 只检测明确错误
    if content.startswith("[Error]") or content.startswith("Error:"):
        self.mark_tool_call(tool_call_id, "discard", ...)
```

### 长期（Refactor）

```
                  ┌─────────────────┐
                  │ MemoryManager   │  ← 统一门面
                  └────────┬────────┘
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
   ┌──────────┐     ┌──────────┐     ┌──────────────┐
   │   MMU    │     │ Compactor│     │ TokenCounter │
   │ (Stack)  │     │ (Summary)│     │  (Accurate)  │
   └──────────┘     └──────────┘     └──────────────┘
```

---

## 总结

MMU 是一个**设计合理但部分过度工程化**的组件：

| 维度 | 评价 |
|------|------|
| **Pinned Context** | ⭐⭐⭐⭐⭐ 优秀设计 |
| **StackFrame** | ⭐⭐⭐⭐ 正确但当前不必要 |
| **Context 提炼** | ⭐⭐⭐ 想法好，实现粗糙 |
| **Tool Call 标记** | ⭐⭐ 过度设计 |
| **Token 估算** | ⭐⭐ 需要改进 |

**核心建议**：保持 Pinned + Stack 的核心架构，简化提炼和标记系统，明确与 CompactionEngine 的职责边界。


### Opinion from GPT-5.2 (Generalist)

现在我对 MMU 的设计有了全面的了解。以下是我的专业评审：

---

# Nimbus MMU 设计评审报告

## 📊 总体评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐ | 概念清晰，但职责边界模糊 |
| 实用性 | ⭐⭐⭐ | 核心功能实用，高级功能可能过度 |
| 复杂度 | ⭐⭐ | 明显过度工程化 |

---

## 1️⃣ 架构设计评审

### ✅ 优点

**清晰的内存布局模型**
```
Pinned Context (永不压缩) + Stack Frames (调用栈隔离)
```
这个概念模型非常直观，类似操作系统的内存管理：
- Pinned = 常驻内存的内核代码
- Stack = 函数调用栈

**Goal Pinning 是真正的创新**
```python
def pin_user_goal(self, goal: str) -> None:
    """确保用户目标在压缩后不丢失"""
```
这解决了 Agent 长任务中"忘记目标"的关键问题。

**语言一致性考虑周到**
```python
def _detect_user_language(self, messages) -> str:
    # 保持用户语言上下文
```

### ❌ 问题

**1. MMU 与 CompactionEngine 职责重叠严重**

| 功能 | MMU | CompactionEngine |
|------|-----|------------------|
| 失败检测 | `_auto_detect_tool_failure()` | `auto_detect_failed_tools()` |
| 消息过滤 | `_filter_discardable_messages()` | `filter_messages()` |
| 标记系统 | `mark_tool_call()` | `mark_tool_call()` |

**建议**: 将 CompactionEngine 合并进 MMU，或明确分工：
- MMU: 仅负责内存布局和组装
- CompactionEngine: 负责所有压缩相关逻辑

**2. 过度抽象的分层**

`context.py` 中的 `Message`, `StackFrame`, `PinnedContext` 完全可以内联到 `mmu.py`，276 行的独立文件增加了认知负担。

---

## 2️⃣ Context Stack 提炼评审

### 核心问题：复杂度与收益不成正比

```python
def pop_frame(self, result=None, extract_valuable=True):
    """弹出栈帧时自动提炼"""
    if result is None and extract_valuable:
        result = self._extract_valuable_content(frame)  # 复杂逻辑
```

**实际提炼逻辑过于简单**：
```python
def _extract_valuable_content(self, frame: StackFrame) -> str:
    # 只是截断前 200 字符，加上最后的 assistant 消息前 300 字符
    # 这不是"智能提炼"，只是简单截断
```

### 评估

| 场景 | 提炼是否有效 |
|------|-------------|
| 文件搜索失败后成功 | ❌ 可能丢失成功路径的上下文 |
| 多步调试过程 | ❌ 中间步骤可能包含重要线索 |
| 代码重构 | ⚠️ 可能丢失重构原因 |

**建议**：简化为两种模式：
1. **保留所有**：默认模式，不做提炼
2. **仅保留结果**：用户明确请求时，只保留最终结论

---

## 3️⃣ StackFrame 隔离评审

### 实际使用情况

从代码搜索来看：
- `push_frame` / `pop_frame` 仅在 `vcpu.py` 中被调用
- 用于 `SUB_CALL` 动作（spawn subprocess）

### 关键问题：SUB_CALL 是否真正有用？

```python
# vcpu.py 中的使用
"SUB_CALL": self._handle_sub_call,  # 处理子任务
```

**实际观察**：
1. LLM（如 Claude）原生不会输出 SUB_CALL，需要特殊提示
2. 子任务通常可以通过普通 tool calls 完成
3. 增加了 10+ 层栈深限制的维护成本

### 评估

| 方面 | 评价 |
|------|------|
| 概念优雅 | ✅ 类似函数调用栈，直观 |
| 实际价值 | ⚠️ 大多数任务不需要深层嵌套 |
| 维护成本 | ❌ 增加了显著的代码复杂度 |

**建议**：考虑简化为单层 context，或将 SUB_CALL 作为可选扩展。

---

## 4️⃣ Tool Call 标记系统评审

### 四级标记分析

```python
ToolCallValue = Literal["valuable", "failed", "exploratory", "intermediate"]
```

| 级别 | 定义 | 实际使用 |
|------|------|----------|
| `valuable` | 有价值 | ⚠️ 代码中几乎没有主动标记 |
| `failed` | 失败 | ✅ 自动检测使用 |
| `exploratory` | 探索性 | ❌ 未见使用 |
| `intermediate` | 中间步骤 | ❌ 未见使用 |

### 自动失败检测问题

```python
failure_indicators = [
    "not found",      # 问题：grep 没找到可能是正常结果
    "no such file",   # 问题：检查文件存在时的正常返回
    "permission denied",
    ...
]
```

**误判示例**：
```bash
# 正常场景：检查文件是否存在
$ [ -f /path/to/file ] && echo "exists" || echo "not found"
# 被误判为失败
```

### 建议

**简化为两级**：
```python
ToolCallValue = Literal["keep", "discard"]
```

**改进失败检测**：
- 基于 exit code 而非文本内容
- 对于 Bash 工具，检查实际返回码
- 白名单而非黑名单策略

---

## 5️⃣ 简化建议

### 可以删除/简化的部分

| 功能 | 建议 | 节省行数 |
|------|------|----------|
| `exploratory`, `intermediate` 标记 | 删除 | ~50 |
| `context.py` 独立文件 | 合并到 mmu.py | 减少文件数 |
| `_extract_valuable_content` | 简化为可选的完整保留/摘要 | ~50 |
| 与 CompactionEngine 重复代码 | 合并 | ~100 |

### 应该增强的部分

| 功能 | 建议 |
|------|------|
| Token 估算 | 使用 tiktoken 或 provider 的 tokenizer |
| 失败检测 | 基于结构化返回（exit code, error field） |
| 目标追踪 | 增加 goal 的版本历史，支持 goal 演进 |

---

## 📋 最终建议

### 短期（高优先级）

1. **合并 MMU 和 CompactionEngine 的重复逻辑**
2. **简化标记系统为 keep/discard 两级**
3. **改进失败检测机制**，避免文本关键词误判

### 中期

4. **重新评估 StackFrame 的必要性**
   - 如果 SUB_CALL 使用率 < 10%，考虑简化
5. **引入精确的 token 计算**

### 长期

6. **考虑使用 LLM 做真正的智能提炼**（当前只是截断）
7. **建立 A/B 测试**，验证各功能对任务成功率的影响

---

## 🎯 结论

MMU 的**核心概念是好的**（Pinned Context + Memory Layout），但**实现上存在过度工程化**。建议：

> **"Keep what's truly useful, remove what's speculative"**

- ✅ 保留：Pinned Context, Goal Pinning, 基本的 Token 管理
- ⚠️ 简化：标记系统, Context Stack 提炼
- ❓ 重新评估：StackFrame 隔离, SUB_CALL 支持

作为 Agent 的核心组件，**简单可靠比功能丰富更重要**。


### Opinion from Gemini 3 Pro High (Reasoning)

现在我已经完整阅读了 MMU 的设计。让我进行全面评审：

---

# Nimbus MMU 设计评审报告

## 一、整体评估

### 1.1 设计质量：⭐⭐⭐⭐ (4/5)

**优点：**
- 📐 **内存模型清晰** - Pinned + Stack 的分层设计直观
- 📝 **文档优秀** - ASCII 图和注释非常清晰
- ⚙️ **配置化设计** - `MMUConfig` 支持灵活调整
- 🔤 **语言保留** - `_detect_user_language()` 在压缩时保留语言上下文
- 🎯 **Goal Pinning** - `pin_user_goal()` 确保目标在压缩后不丢失

**问题：**
- ⚠️ Token 估算过于粗糙 (4 chars/token)
- ⚠️ 与 CompactionEngine 职责边界模糊
- ⚠️ 部分功能设计但未实际使用

---

## 二、核心问题分析

### 2.1 Context Stack 提炼：**过度设计** ⚠️

```
代码行数：~200 行
实际使用：几乎仅限测试
```

从代码搜索结果看：

| 使用场景 | 文件 | 用途 |
|---------|------|------|
| `pop_frame` | test_v2_memory.py | 测试 |
| `mark_tool_call` | test_v2_new_features.py | 测试 |
| `push_frame` | test_v2_vcpu.py | 测试 |

**问题**：提炼逻辑设计复杂，但 SUB_CALL 在实际 Agent 运行中很少使用。现代 LLM 的 tool calling 是扁平的，不需要嵌套调用栈。

**建议**：保留基本栈功能用于未来扩展，但移除复杂的自动提炼逻辑。

---

### 2.2 StackFrame 隔离：**价值有限**

**理论价值：**
```python
# 子任务失败时可以完全丢弃
mmu.push_frame("explore codebase")
# ... 探索失败 ...
mmu.pop_frame(extract_valuable=False)  # 丢弃所有内容
```

**实际情况：**
- 从 VCPU 代码看，SUB_CALL 处理后立即返回 `ToolResult(status="OK")`
- LLM 没有被训练主动发起 SUB_CALL
- 实际 Agent 运行几乎全在 root frame 中

**建议**：简化为单层消息列表 + 可选的摘要点，而非完整的调用栈。

---

### 2.3 Tool Call 标记系统：**重复实现**

**MMU 中的实现：**
```python
class ToolCallMarker:
    value: Literal["valuable", "failed", "exploratory", "intermediate"]
```

**CompactionEngine 中的实现：**
```python
class ContextStackAwareCompaction:
    def mark_tool_call(self, tool_call_id, valuable: bool, ...):
```

这是**职责重叠**。两个组件都在做工具调用价值判断，但没有明确的分工。

**自动失败检测问题：**
```python
failure_indicators = [
    "not found",      # "Config not found" 可能是正常信息
    "no matches",     # Grep 空结果不一定是失败
    "cannot find",    # "Cannot find better solution" 是正常文本
]
```

关键词检测会产生**大量误判**。

---

### 2.4 Token 估算：**精度不足**

```python
def token_estimate(self) -> int:
    """Rough token estimate (4 chars ≈ 1 token)."""
    return len(self.content) // 4
```

**问题：**
- 中文平均 1.5-2 tokens/字符（不是 0.25）
- 代码有特殊 tokenization
- 预算管理失去精确性

**影响：**
- 中文对话会过早触发压缩
- 代码重的场景预算计算偏差大

---

## 三、架构评审

### 3.1 MMU vs CompactionEngine 职责混淆

```
┌─────────────────────────────────────────────────────────────────┐
│                          当前架构                                │
├─────────────────────────────────────────────────────────────────┤
│  MMU                         │  CompactionEngine                │
│  ├─ mark_tool_call()         │  ├─ mark_tool_call()            │
│  ├─ _filter_discardable()    │  ├─ filter_messages()           │
│  ├─ _compress_frames()       │  ├─ compact()                   │
│  └─ _summarize_messages()    │  └─ summarize() (via LLM)       │
└─────────────────────────────────────────────────────────────────┘
                    ↓ 重复功能 ↓
```

**建议的职责分离：**

```
┌─────────────────────────────────────────────────────────────────┐
│                          建议架构                                │
├─────────────────────────────────────────────────────────────────┤
│  MMU (纯内存管理)              │  CompactionEngine (智能压缩)    │
│  ├─ 存储消息                   │  ├─ 失败检测                   │
│  ├─ 栈帧管理                   │  ├─ LLM 摘要                   │
│  ├─ 上下文组装                 │  ├─ Token 管理                 │
│  └─ 基础 token 统计            │  └─ 压缩策略                   │
└─────────────────────────────────────────────────────────────────┘
          ↑ 单一职责 ↑               ↑ 单一职责 ↑
```

---

## 四、具体建议

### 4.1 应该简化的功能

| 功能 | 当前行数 | 建议 |
|------|---------|------|
| Tool call 四级标记 | ~100 行 | → 简化为 boolean (keep/discard) |
| StackFrame 提炼 | ~150 行 | → 移除，仅保留基本 pop/push |
| MMU 内置压缩 | ~80 行 | → 删除，全部交给 CompactionEngine |

### 4.2 应该增强的功能

```python
# 1. Token 估算增强
def token_estimate(self, model: str = "gpt-4") -> int:
    """使用 tiktoken 或模型特定估算"""
    try:
        import tiktoken
        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(self.content))
    except:
        # 回退：考虑语言
        if self._detect_language() == "zh":
            return len(self.content) * 2
        return len(self.content) // 4

# 2. 更健壮的失败检测
def is_tool_call_failed(self, content: str, tool_name: str) -> bool:
    """基于工具类型的失败检测"""
    if content.startswith("[Error]"):
        return True
    
    # 工具特定规则
    if tool_name == "Grep":
        return "error" in content.lower() and len(content) < 100
    if tool_name == "Read":
        return "ENOENT" in content or "not found" in content
    
    return False  # 默认不是失败
```

### 4.3 建议的简化版 MMU

```python
class SimplifiedMMU:
    """精简版 MMU - 聚焦核心功能"""
    
    def __init__(self, config: MMUConfig):
        self._pinned: Optional[PinnedContext] = None
        self._messages: List[Message] = []
        self._checkpoints: List[int] = []  # 摘要点索引
        self.config = config
    
    # 核心功能
    def add_message(self, msg: Message) -> None: ...
    def assemble_context(self, max_tokens: int) -> List[Dict]: ...
    
    # Pinned 管理
    def set_pinned(self, pinned: PinnedContext) -> None: ...
    def pin_user_goal(self, goal: str) -> None: ...
    
    # 检查点（替代复杂栈）
    def create_checkpoint(self) -> int: ...
    def rollback_to_checkpoint(self, idx: int) -> None: ...
    
    # 压缩接口（实现交给 CompactionEngine）
    def get_messages_for_compaction(self) -> List[Message]: ...
    def apply_compaction_result(self, summary: str, keep_from: int) -> None: ...
```

---

## 五、总结

### 5.1 回答评审问题

| 问题 | 答案 |
|------|------|
| 1. MMU 设计质量 | **良好** - 核心概念清晰，但有冗余 |
| 2. Context Stack 提炼值得吗 | **不值得** - 实际使用率极低 |
| 3. StackFrame 隔离必要吗 | **不必要** - SUB_CALL 几乎没用 |
| 4. Tool Call 标记过度设计 | **是** - 四级简化为两级足够 |
| 5. 简化/增强建议 | 见上文 4.1/4.2 |

### 5.2 优先级建议

```
🔴 高优先级：
   - 明确 MMU 与 CompactionEngine 职责边界
   - 改进 Token 估算（至少区分中英文）

🟡 中优先级：
   - 简化 Tool Call 标记系统
   - 移除未使用的 StackFrame 提炼

🟢 低优先级：
   - 移除/简化 SUB_CALL 支持
   - 考虑使用 tiktoken 精确计算
```

### 5.3 核心结论

> **MMU 的核心价值在于 Pinned Context + Goal Pinning + 消息管理。** 
> 
> **Context Stack 提炼和 Tool Call 标记系统是"提前优化"的产物** - 理论上有价值，但实际 Agent 运行模式不需要这些功能。建议简化这些复杂机制，将精力放在：
> 
> 1. 更精确的 Token 管理
> 2. 与 CompactionEngine 的清晰集成
> 3. 更健壮的 Goal 保留机制
