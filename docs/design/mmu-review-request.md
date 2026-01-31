# MMU（Memory Management Unit）评审请求

## 📊 文件概览

| 指标 | 数值 |
|------|------|
| **主文件** | `src/nimbus/core/memory/mmu.py` |
| **行数** | 914 行 |
| **辅助文件** | `src/nimbus/core/memory/context.py` (276 行) |
| **总代码量** | ~1,190 行 |

---

## 🏗️ 核心架构

### 内存布局

```
┌─────────────────────────────────┐
│        Pinned Context           │  ← 永远在顶部，永不压缩
│  - System Rules                 │
│  - Workspace Info               │
│  - Capabilities                 │
│  - User Goal (pinned)           │
├─────────────────────────────────┤
│        Root Frame               │  ← 主对话
│  - messages[...]                │
├─────────────────────────────────┤
│        Sub Frame 1              │  ← 第一个 SUB_CALL
│  - goal: "explore codebase"     │
│  - messages[...]                │
├─────────────────────────────────┤
│        Sub Frame 2              │  ← 嵌套 SUB_CALL
│  - goal: "find auth module"     │
│  - messages[...]                │
└─────────────────────────────────┘
         ↑ 当前帧 (栈顶)
```

### 核心数据结构

```python
@dataclass
class PinnedContext:
    """不可压缩的系统锚点"""
    system_rules: str       # 行为规则
    workspace_info: str     # 工作区信息
    capabilities: str       # 工具能力描述
    custom_anchors: List[str]  # 自定义锚点

@dataclass
class StackFrame:
    """调用栈帧"""
    frame_id: str
    goal: str               # 子任务目标
    state: FrameState       # ACTIVE/SUSPENDED/COMPLETED/FAILED
    messages: List[Message] # 隔离的消息历史
    result: Optional[Any]   # 完成结果

@dataclass
class Message:
    """LLM 消息格式"""
    role: MessageRole       # system/user/assistant/tool
    content: Any
    tool_call_id: Optional[str]
    tool_calls: Optional[List[Dict]]
```

---

## 🎯 MMU 的核心职责

### 1. 上下文组装 (`assemble_context`)

```python
def assemble_context(self, max_tokens=None, filter_discardable=True):
    """
    组装完整的 LLM 上下文
    
    步骤:
    1. 添加 Pinned Context (system message)
    2. 合并所有 Stack Frames 的消息
    3. 过滤被标记为无价值的 tool calls
    4. 处理 token 预算
    """
```

### 2. Context Stack 提炼 (`pop_frame`)

```python
def pop_frame(self, result=None, extract_valuable=True):
    """
    弹出栈帧并提炼有价值内容
    
    Context Stack 提炼逻辑:
    1. 自动检测失败的 tool calls
    2. 过滤 failed/exploratory 的调用
    3. 只有结论和成功的操作被保留到父 frame
    """
```

这是 Nimbus 的**核心创新点**：当子任务完成时，不是简单地丢弃或保留全部，而是智能提炼。

### 3. Tool Call 标记系统

```python
ToolCallValue = Literal["valuable", "failed", "exploratory", "intermediate"]

def mark_tool_call(self, tool_call_id, value, reason=None):
    """标记 tool call 的价值"""
    
def mark_recent_tool_calls(self, value, reason=None, count=1):
    """批量标记最近的 tool calls"""
```

### 4. Token 预算管理

```python
@dataclass
class MMUConfig:
    max_context_tokens: int = 16000    # 总预算
    pinned_budget: int = 2000          # Pinned 预算
    frame_budget: int = 8000           # 单帧预算
    compress_threshold: float = 0.9    # 90% 时触发压缩
    keep_recent_messages: int = 10     # 压缩时保留最近 N 条
    auto_compact: bool = False         # 是否自动压缩（默认关闭）
    remove_failed_tool_calls: bool = True  # 移除失败的调用
```

---

## 🔍 当前实现分析

### 方法分布

| 分类 | 方法数 | 行数 | 说明 |
|------|--------|------|------|
| **Pinned 管理** | 5 | ~50 | set_pinned, update_* |
| **栈操作** | 5 | ~100 | push_frame, pop_frame |
| **消息添加** | 6 | ~80 | add_*_message |
| **Tool 标记** | 5 | ~150 | mark_*, clear_markers |
| **上下文组装** | 4 | ~200 | assemble_context, _filter_*, _compress_* |
| **Token 估算** | 3 | ~50 | estimate_tokens, needs_compression |
| **辅助方法** | 8 | ~100 | get_state, clear, rollback_* |
| **内容提炼** | 3 | ~100 | _extract_valuable_*, _detect_* |

### 状态变量

```python
class MMU:
    config: MMUConfig
    process_id: str
    _pinned: Optional[PinnedContext]
    _stack: List[StackFrame]           # 调用栈
    _tool_markers: Dict[str, ToolCallMarker]  # tool call 标记
    _frame_discardable: Dict[str, Set[str]]   # 每帧可丢弃的 IDs
```

---

## ⚠️ 潜在问题

### 1. 复杂的失败检测逻辑

```python
def _auto_detect_tool_failure(self, message: Message) -> bool:
    """自动检测 tool call 是否失败"""
    failure_indicators = [
        "not found", "error", "failed", "permission denied",
        "does not exist", "no such file", "cannot", "unable to",
        # ... 更多关键词
    ]
```

问题：基于关键词的检测可能不够准确，可能误判。

### 2. Token 估算不精确

```python
def token_estimate(self) -> int:
    """Rough token estimate (4 chars ≈ 1 token)."""
    return len(self.content) // 4
```

问题：4 字符/token 是粗略估计，中文和代码的 token 比例不同。

### 3. Context Stack 提炼可能丢失信息

```python
def _extract_valuable_content(self, frame: StackFrame) -> str:
    # 只保留最后一个 assistant 结论
    # 只保留前 3 个成功的 tool results
```

问题：可能丢失重要的中间信息。

### 4. 压缩策略较简单

```python
def _compress_frames(self, budget: int):
    # 只保留最近 N 条消息
    # 总结旧消息
```

问题：可能丢失早期的重要上下文。

---

## 💡 评审问题

### 1. Context Stack 提炼的价值

- 这个功能是 Nimbus 的核心创新之一
- 但是否真的需要这么复杂的提炼逻辑？
- 有没有更简单的方案？

### 2. Token 管理策略

- 当前的 token 预算管理是否合理？
- `auto_compact: bool = False` 的默认值是否正确？
- 是否需要更精确的 token 计算？

### 3. 架构是否过度设计？

- StackFrame 隔离是否必要？实际使用 SUB_CALL 的场景多吗？
- Tool Call 标记系统是否太复杂？
- 是否可以简化为更扁平的消息列表？

### 4. 与 Compaction（外部压缩）的关系

- MMU 有内部压缩逻辑 (`_compress_frames`)
- 外部也有 CompactionEngine
- 两者的职责边界是否清晰？

### 5. 遗漏的功能？

- 是否需要消息优先级？
- 是否需要更细粒度的 token 控制？
- 是否需要持久化支持？

---

## 📊 与其他 Agent 框架的对比

| 特性 | Nimbus MMU | LangChain Memory | AutoGPT Memory |
|------|------------|-----------------|----------------|
| 栈帧隔离 | ✅ | ❌ | ❌ |
| 失败提炼 | ✅ 自动 | ❌ | 部分 |
| Token 预算 | ✅ 分层 | ✅ 简单 | ✅ |
| 压缩策略 | 总结式 | 窗口式 | 向量式 |
| 持久化 | ❌ 外部 | ✅ 内置 | ✅ 内置 |

---

## 🎯 请专家评估

1. **MMU 是否是正确的抽象层次？**
   - 操作系统的 MMU 隐喻是否恰当？
   - 还是应该更简单地叫 ContextManager？

2. **Context Stack 提炼是否值得？**
   - 复杂度 vs 收益的权衡
   - 是否有更简单的替代方案？

3. **哪些功能可以简化或删除？**
   - Tool Call 标记系统是否必要？
   - StackFrame 隔离是否必要？

4. **哪些功能应该增强？**
   - Token 估算精度？
   - 压缩策略？
   - 持久化？

5. **整体评价**
   - 作为 Agent 的核心组件，MMU 的设计质量如何？
   - 有哪些可以借鉴的设计？有哪些可以改进的地方？

---

*请专家从架构设计、实用性、复杂度三个角度进行评审*
