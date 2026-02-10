# VCPU 混合响应处理修改总结

## 修改目标
支持 GPT-5.3-codex 的 "边说边做" 模式，将同时包含 `content` 和 `tool_calls` 的混合响应拆分为两阶段处理。

## 修改内容

### 1. 添加混合响应检测与分裂逻辑（第 655-679 行）

在 `step()` 方法的 decode 之前添加：

```python
# 2. SPLIT MIXED RESPONSE: Handle models that return both content and tool_calls
# (e.g., GPT-5.3-codex tends to "talk while calling tools")
# We split this into two phases to match Claude/Gemini behavior:
#   Phase 1: Output the text content (THOUGHT)
#   Phase 2: Execute the tool calls
split_response = False
if response.content and response.content.strip() and response.tool_calls:
    logger.info(
        "🔀 Detected mixed response (content + tool_calls). Splitting into thought → action."
    )
    split_response = True
    
    # First, emit the content as a THOUGHT action
    thought_action = ActionIR(
        kind="THOUGHT", 
        name="thought", 
        args={"text": response.content.strip()}
    )
    
    # Execute the thought immediately (just yields to stream)
    thought_result = await self._execute_action(thought_action)
    step_result.results.append(thought_result)
    
    # Add assistant message with only content to MMU (for context continuity)
    self.mmu.add_assistant_message(response.content)
```

### 2. 修改 decode 调用逻辑（第 681-688 行）

```python
# 2. DECODE: Parse into ActionIR
decode_start = time.time_ns()
try:
    # If we split the response, decode only tool_calls (content already handled)
    decode_content = None if split_response else response.content
    actions = self.decoder.decode(
        content=decode_content, tool_calls=response.tool_calls
    )
```

### 3. 调整 MMU 写入逻辑（第 735-749 行）

```python
# Only add assistant message if we didn't already add it in split phase
if not split_response:
    self.mmu.add_assistant_with_tool_calls(
        content=response.content, tool_calls=tool_calls_for_storage
    )
else:
    # Split case: add assistant message with only tool_calls (content already added)
    self.mmu.add_assistant_with_tool_calls(
        content=None, tool_calls=tool_calls_for_storage
    )
elif response.content:
    # Add text-only assistant message (Implicit Return / Thought)
    # Skip if we already added it in split phase
    if not split_response:
        self.mmu.add_assistant_message(response.content)
```

## 处理流程

### 场景 1: 纯文本响应（Claude/Gemini 风格）
- **检测**: `content="文本"`, `tool_calls=None`
- **split_response**: `False`
- **decode**: 传递 `content="文本"`, `tool_calls=None`
- **MMU**: 添加文本消息 `mmu.add_assistant_message("文本")`

### 场景 2: 纯工具调用响应
- **检测**: `content=None`, `tool_calls=[...]`
- **split_response**: `False`
- **decode**: 传递 `content=None`, `tool_calls=[...]`
- **MMU**: 添加工具调用消息 `mmu.add_assistant_with_tool_calls(content=None, tool_calls=[...])`

### 场景 3: 混合响应（GPT-5.3-codex 风格）✨
- **检测**: `content="我来执行这三个测试命令："`, `tool_calls=[call1, call2, call3]`
- **split_response**: `True`
- **阶段 1 - 处理文本**:
  - 创建 THOUGHT 动作并立即执行
  - 添加文本到 MMU: `mmu.add_assistant_message("我来执行这三个测试命令：")`
- **阶段 2 - 处理工具调用**:
  - decode: 传递 `content=None`, `tool_calls=[...]`
  - MMU: 添加工具调用消息 `mmu.add_assistant_with_tool_calls(content=None, tool_calls=[...])`

### 场景 4: 空白内容 + 工具调用
- **检测**: `content="   "`, `tool_calls=[...]`
- **split_response**: `False`（因为 `content.strip()` 为空）
- **处理**: 与场景 2 相同

## 期望效果

1. ✅ **立即显示文本**: GPT-5.3-codex 说"我来执行这三个测试命令："时，WebUI 立即显示这段文字
2. ✅ **立即执行工具**: 紧接着立即执行 3 个 Bash 工具，不需要等待下一轮迭代
3. ✅ **消除延迟**: 日志中不再出现 "Thinking... (Iteration 2)" 的延迟
4. ✅ **兼容性**: 不影响 Claude/Gemini 的纯文本或纯工具调用模式
5. ✅ **MMU 正确性**: 消息历史保持正确的 OpenAI 格式

## 验证

- ✅ 语法检查通过
- ✅ 逻辑测试通过（4 个测试场景）
- ✅ decode_content 处理正确
- ✅ MMU 写入逻辑正确

## 修改的文件

- `src/nimbus/core/runtime/vcpu.py`
  - 第 655-679 行: 添加混合响应分裂逻辑
  - 第 681-688 行: 修改 decode 调用
  - 第 735-749 行: 调整 MMU 写入逻辑

## 影响范围

- **向后兼容**: ✅ 完全兼容现有的 Claude/Gemini 响应模式
- **性能影响**: ✅ 几乎无影响（仅增加一次条件判断）
- **用户体验**: ✅ 显著改善 GPT-5.3-codex 的响应速度

---

修改完成时间: 2024-XX-XX
修改人: Executor Agent
