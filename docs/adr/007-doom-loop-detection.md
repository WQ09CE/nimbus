# ADR-007: Doom Loop Detection Mechanism

## Status
Accepted

## Context
在 Agent 执行过程中，LLM 可能会陷入无限循环，不断尝试相同的工具调用。这会导致：
1. 资源浪费（tokens、时间、API 调用）
2. 任务永远无法完成
3. 用户体验差

## Decision
从 opencode 项目学习并实现 Doom Loop 检测机制。

### 核心机制
1. **DOOM_LOOP_THRESHOLD = 3**：如果连续 3 次调用相同工具且参数完全相同，判定为无限循环
2. **参数签名比较**：使用 `json.dumps(args, sort_keys=True)` 生成唯一签名
3. **追踪器**：维护最近 N 次工具调用的 `(tool_name, args_signature)` 元组列表

### 实现位置
- `src/nimbus/v2/core/runtime/vcpu.py`
  - `DOOM_LOOP_THRESHOLD = 3` 常量
  - `self._recent_tool_calls: List[Tuple[str, str]]` 追踪器
  - `_handle_tool_call()` 中的检测逻辑
  - `_get_doom_loop_guidance()` 工具特定恢复指导

### 检测算法
```python
# 创建当前调用的签名
call_signature = (action.name, json.dumps(action.args, sort_keys=True))

# 添加到追踪器
self._recent_tool_calls.append(call_signature)

# 只保留最近 DOOM_LOOP_THRESHOLD 次调用
if len(self._recent_tool_calls) > DOOM_LOOP_THRESHOLD:
    self._recent_tool_calls = self._recent_tool_calls[-DOOM_LOOP_THRESHOLD:]

# 检查是否所有最近调用都相同
if len(self._recent_tool_calls) == DOOM_LOOP_THRESHOLD:
    if all(call == call_signature for call in self._recent_tool_calls):
        # 触发 Doom Loop 终止，提供工具特定指导
        guidance = self._get_doom_loop_guidance(action.name)
```

### 工具特定恢复指导
```python
def _get_doom_loop_guidance(self, tool_name: str) -> str:
    """为不同工具提供特定的恢复指导"""
    guidance_map = {
        "Edit": "使用 Read 先查看当前文件内容，确保 old_string 精确匹配",
        "Write": "检查文件路径和目录是否存在",
        "Bash": "检查命令语法和依赖",
        "Read": "使用 Glob 搜索正确的文件路径",
        "Glob": "尝试更宽泛的搜索模式",
        "Grep": "简化搜索模式或检查路径",
    }
    return guidance_map.get(tool_name, "检查错误信息并尝试不同方法")
```

### 触发后的行为
- 返回 `is_final=True` 强制终止执行
- 返回明确的错误消息 + 工具特定恢复指导
- 记录 `Fault(domain="RUNTIME", code="DOOM_LOOP")`

## Consequences

### Positive
- 避免无限循环导致的资源浪费
- 快速失败，提高用户体验
- 提供清晰的错误诊断信息
- 工具特定指导帮助 LLM 理解如何修复

### Negative
- 可能误判合法的重复操作（如批量处理相同操作）
- 阈值 3 可能过于保守或激进，需要根据实际使用调整

## References
- opencode 源码: `packages/opencode/src/session/processor.ts:20,144-168`
- 相关 PR: N/A (内部实现)

---

# 第二次取经成果 (2026-01-29)

## 学习内容
从 opencode 最新代码学习工具调用优化机制。

## 关键发现

### 1. 重试机制 (retry.ts)
- **指数退避**: `RETRY_INITIAL_DELAY (2s) * BACKOFF_FACTOR (2) ^ attempt`
- **Header 解析**: 支持 `retry-after-ms`、`retry-after`（秒或 HTTP 日期）
- **最大延迟**: 30s (无 header)，尊重 provider 指定的延迟
- **错误分类**: Overloaded、Too Many Requests、Rate Limited、Server Error 可重试

```typescript
export function delay(attempt: number, error?: MessageV2.APIError) {
  if (error?.data.responseHeaders) {
    // 优先使用 provider 指定的重试时间
    if (retryAfterMs) return parsedMs
    if (retryAfter) return parsedSeconds * 1000
  }
  // 回退到指数退避
  return Math.min(
    RETRY_INITIAL_DELAY * Math.pow(RETRY_BACKOFF_FACTOR, attempt - 1),
    RETRY_MAX_DELAY_NO_HEADERS
  )
}
```

### 2. Edit 工具 - 9 阶段模糊匹配级联 (edit.ts)
1. `SimpleReplacer` - 精确字符串匹配
2. `LineTrimmedReplacer` - 去空格行比较
3. `BlockAnchorReplacer` - 首尾行锚点 + Levenshtein (阈值: 0.0/0.3)
4. `WhitespaceNormalizedReplacer` - 空白不敏感匹配
5. `IndentationFlexibleReplacer` - 缩进规范化匹配
6. `EscapeNormalizedReplacer` - 转义序列规范化
7. `TrimmedBoundaryReplacer` - 边界空白修剪
8. `ContextAwareReplacer` - 上下文锚点匹配 (50% 行相似度)
9. `MultiOccurrenceReplacer` - 多次精确匹配 (配合 replaceAll)

### 3. 工具名自动修复 (llm.ts)
```typescript
experimental_repairToolCall(failed) {
  const lower = failed.toolCall.toolName.toLowerCase()
  // 自动修复大小写错误
  if (lower !== failed.toolCall.toolName && tools[lower]) {
    return { ...failed.toolCall, toolName: lower }
  }
  // 路由未知工具到 "invalid" 工具
  return {
    ...failed.toolCall,
    toolName: "invalid",
    input: JSON.stringify({ tool: failed.toolCall.toolName, error: failed.error.message })
  }
}
```

### 4. 分层指令加载 (instruction.ts)
- **文件优先级**: `AGENTS.md` > `CLAUDE.md` > `CONTEXT.md`
- **搜索路径**: 全局 (~/.claude/CLAUDE.md) → 项目根 → 子目录
- **声明系统**: 防止重复加载 `messageID → Set<filepath>`

### 5. LSP 集成 (edit.ts:133-143)
```typescript
await LSP.touchFile(filePath, true)
const diagnostics = await LSP.diagnostics()
const errors = diagnostics[filePath]?.filter(item => item.severity === 1)
if (errors.length > 0) {
  output += `\n\nLSP errors detected:\n${errors.map(LSP.Diagnostic.pretty).join("\n")}`
}
```

## 已实施优化

### Phase 1: DOOM_LOOP 检测
- ✅ `DOOM_LOOP_THRESHOLD = 3` 常量
- ✅ 工具调用追踪器
- ✅ 工具特定恢复指导

### Phase 2: Terminal Tools Hint
- ✅ Edit/Write/Bash 成功后注入 "call return_result" 提示

## 测试结果改进
| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| 通过测试 | 7/12 | 8/12 |
| scenario_bug_fix 耗时 | 超时 (>60s) | 6.9s (doom loop 检测) |
| scenario_document_api | FAIL | PASS |

## 待实施优化
1. **重试机制**: 添加 provider header 解析
2. **Edit 工具**: 移植更多模糊匹配阶段
3. **工具修复**: 实现大小写自动修复
4. **LSP 集成**: 将诊断信息集成到 Edit 结果中
