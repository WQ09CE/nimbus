# Pi-Coding-Agent 鲁棒性机制研究 -- Nimbus 对标分析

> 基于 `@mariozechner/pi-coding-agent@0.55.3` 和 `@mariozechner/pi-ai@0.55.3` 源码分析
> 对标 Nimbus `nimbus-next` 架构（vcpu.py / mmu.py / gate.py）
> 研究日期: 2026-03-10

---

## 目录

1. [迭代上限: Pi 没有 maxIterations](#1-迭代上限-pi-没有-maxiterations)
2. [Compaction 双路径触发](#2-compaction-双路径触发)
3. [两级重试机制](#3-两级重试机制)
4. [Per-Tool 错误隔离](#4-per-tool-错误隔离)
5. [Goal 跟踪策略差异](#5-goal-跟踪策略差异)
6. [Steering vs Follow-up 双队列](#6-steering-vs-follow-up-双队列)
7. [Session 持久化](#7-session-持久化)
8. [其他值得学习的细节](#8-其他值得学习的细节)
9. [Nimbus 行动计划](#nimbus-行动计划)

---

## 1. 迭代上限: Pi 没有 maxIterations

### Pi 设计

Pi 的核心循环是纯 `while(true)` -- 没有任何迭代计数器：

```javascript
// pi-agent-core/dist/agent-loop.js:64-126
async function runLoop(currentContext, newMessages, config, signal, stream, streamFn) {
    let firstTurn = true;
    let pendingMessages = (await config.getSteeringMessages?.()) || [];
    // Outer loop: continues when queued follow-up messages arrive after agent would stop
    while (true) {
        let hasMoreToolCalls = true;
        let steeringAfterTools = null;
        // Inner loop: process tool calls and steering messages
        while (hasMoreToolCalls || pendingMessages.length > 0) {
            // ... stream assistant response, execute tools, check steering
        }
        // Agent would stop here. Check for follow-up messages.
        const followUpMessages = (await config.getFollowUpMessages?.()) || [];
        if (followUpMessages.length > 0) {
            pendingMessages = followUpMessages;
            continue;
        }
        break; // No more messages, exit
    }
}
```

退出条件仅有三个：
1. LLM 返回 `stopReason === "error"` 或 `"aborted"` (line 88)
2. LLM 不返回 tool_calls -- 自然完成 (line 96, `hasMoreToolCalls = toolCalls.length > 0`)
3. steering 和 follow-up 队列都为空 (line 118-125)

**核心哲学**: Pi 信任 context window 作为天然资源天花板 -- context 满了就 compaction，compaction 后继续。允许无限长的任务运行，真正让 agent 自己决定何时完成。

### Nimbus 现状

```python
# nimbus/core/vcpu.py:37-41
@dataclass
class VCPUConfig:
    max_iterations: int = 50          # <-- 硬限制
    max_consecutive_thoughts: int = 8
    max_consecutive_errors: int = 3
    llm_call_timeout: float = 300.0
```

```python
# nimbus/core/vcpu.py:152-163
self._exec.iteration += 1
if self._exec.iteration > self.config.max_iterations:
    result.is_final = True
    result.final_result = ToolResult(
        status="ERROR",
        output=f"Max iterations ({self.config.max_iterations}) reached.",
        fault=Fault(domain="RESOURCE", code="BUDGET_EXCEEDED",
                    message="Max iterations", retryable=False),
        is_final=True,
    )
```

**问题**: `max_iterations: 50` 对复杂任务来说太低。刚修了 iteration 跨 turn 累计不重置的 bug，但根本问题是 Pi 压根不用这个机制。

### 建议

- **移除 `max_iterations` 硬限制**，或提升到 200+ 作为极端兜底
- 用 compaction 失败 + `max_compactions` 计数器作为间接限制（compaction 也无法释放空间时才停）
- 保留 `max_consecutive_thoughts`（防纯思考死循环）和 `max_consecutive_errors`（防 LLM API 持续报错）作为异常检测手段
- 保留 `iteration` 计数器仅用于可观测性（日志、metrics），不作为终止条件

---

## 2. Compaction 双路径触发

### Pi 设计

Pi 有两条独立的 compaction 路径：

**路径 A -- Overflow（硬错误恢复）**

```javascript
// pi-coding-agent/dist/core/agent-session.js:1247-1256
// Case 1: Overflow - LLM returned context overflow error
if (sameModel && !errorIsFromBeforeCompaction && isContextOverflow(assistantMessage, contextWindow)) {
    // Remove the error message from agent state (it IS saved to session for history,
    // but we don't want it in context for the retry)
    const messages = this.agent.state.messages;
    if (messages.length > 0 && messages[messages.length - 1].role === "assistant") {
        this.agent.replaceMessages(messages.slice(0, -1));
    }
    await this._runAutoCompaction("overflow", true);  // willRetry=true
    return;
}
```

触发后：compact + **自动重试**。关键是先移除错误的 assistant message，再 compact，再 `agent.continue()`。

Pi 还有非常完善的 overflow 检测正则，覆盖了十余个 LLM provider：

```javascript
// pi-ai/dist/utils/overflow.js:25-41
const OVERFLOW_PATTERNS = [
    /prompt is too long/i,                    // Anthropic
    /input is too long for requested model/i, // Amazon Bedrock
    /exceeds the context window/i,            // OpenAI
    /input token count.*exceeds the maximum/i,// Google (Gemini)
    /maximum prompt length is \d+/i,          // xAI (Grok)
    /reduce the length of the messages/i,     // Groq
    /maximum context length is \d+ tokens/i,  // OpenRouter
    /exceeds the limit of \d+/i,              // GitHub Copilot
    /exceeds the available context size/i,    // llama.cpp
    /greater than the context length/i,       // LM Studio
    /context window exceeds limit/i,          // MiniMax
    /exceeded model token limit/i,            // Kimi
    /context[_ ]length[_ ]exceeded/i,         // Generic
    /too many tokens/i,                       // Generic
    /token limit exceeded/i,                  // Generic
];
```

还处理了两个特殊情况：
- Cerebras/Mistral 返回 `400/413 (no body)` 的裸状态码 (line 96)
- z.ai 的"静默溢出" -- 不报错但 `usage.input > contextWindow` (line 101-106)

**路径 B -- Threshold（软阈值预防）**

```javascript
// pi-coding-agent/dist/core/agent-session.js:1258-1265
// Case 2: Threshold - turn succeeded but context is getting large
if (assistantMessage.stopReason === "error") return;
const contextTokens = calculateContextTokens(assistantMessage.usage);
if (shouldCompact(contextTokens, contextWindow, settings)) {
    await this._runAutoCompaction("threshold", false);  // willRetry=false
}
```

使用 `contextWindow - reserveTokens`（默认保留 16384 tokens）作为阈值。Compact 后**不自动重试**，等下一次自然触发。

还有一个精妙的防重复 compaction 逻辑：

```javascript
// agent-session.js:1239-1246
// Skip overflow check if the message came from a different model.
const sameModel = this.model && assistantMessage.provider === this.model.provider
    && assistantMessage.model === this.model.id;
// Skip overflow check if the error is from before a compaction in the current path.
const compactionEntry = getLatestCompactionEntry(this.sessionManager.getBranch());
const errorIsFromBeforeCompaction = compactionEntry !== null
    && assistantMessage.timestamp < new Date(compactionEntry.timestamp).getTime();
```

### Nimbus 现状

```python
# nimbus/core/mmu.py:584-586
def needs_compaction(self) -> bool:
    threshold = int(self.config.max_context_tokens * self.config.compress_threshold)
    return self.estimate_tokens() >= threshold
```

只有 Threshold 路径。Overflow 在 VCPU 层作为 `Fault` 处理，但没有 Pi 那样精细的 overflow 正则识别，也没有"移除错误 message 后重试"的流程。

### 建议

- **增加 overflow 检测函数**，移植 Pi 的 `OVERFLOW_PATTERNS` 正则集（至少覆盖 Anthropic/OpenAI/Google 三大 provider）
- Overflow 路径：检测到 context overflow → 移除错误 assistant message → compact → 自动重试一次
- 加防重复标志（类似 `_overflowRecoveryAttempted`），overflow compaction 失败后不再重试
- Threshold 路径保持现有逻辑，但考虑用 LLM API 返回的真实 `usage.input` 替代本地估算

---

## 3. 两级重试机制

Pi 实现了分层的错误恢复策略，这是整个鲁棒性设计中最值得学习的部分。

### Level 1 -- Provider 级重试（单个 API 请求内）

```javascript
// pi-ai/dist/providers/google-gemini-cli.js:46-47
const MAX_RETRIES = 3;
const BASE_DELAY_MS = 1000;
```

```javascript
// google-gemini-cli.js:246-297
for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    // ... fetch request
    if (response.ok) break; // Success

    const errorText = await response.text();
    if (attempt < MAX_RETRIES && isRetryableError(response.status, errorText)) {
        // Use server-provided delay or exponential backoff
        const serverDelay = extractRetryDelay(errorText, response);
        const delayMs = serverDelay ?? BASE_DELAY_MS * 2 ** attempt;
        // Check if server delay exceeds max allowed (default: 60s)
        const maxDelayMs = options?.maxRetryDelayMs ?? 60000;
        if (maxDelayMs > 0 && serverDelay && serverDelay > maxDelayMs) {
            throw new Error(`Server requested ${delaySeconds}s retry delay`);
        }
        await sleep(delayMs, options?.signal);
        continue;
    }
    throw new Error(`API error (${response.status})`);
}
```

**精华**: `extractRetryDelay()` 函数解析多种 header 和 body 格式：
- `Retry-After` header（秒数或 HTTP 日期）
- `x-ratelimit-reset` / `x-ratelimit-reset-after` header
- Body 中的 `"Your quota will reset after 39s"` 文本
- Body 中的 `"retryDelay": "34.074824224s"` JSON 字段

### Level 2 -- Session 级重试（agent 循环层面）

```javascript
// pi-coding-agent/dist/core/agent-session.js:1660-1672
_isRetryableError(message) {
    if (message.stopReason !== "error" || !message.errorMessage) return false;
    // Context overflow is handled by compaction, not retry
    if (isContextOverflow(message, contextWindow)) return false;
    const err = message.errorMessage;
    return /overloaded|rate.?limit|too many requests|429|500|502|503|504|
        service.?unavailable|server error|internal error|connection.?error|
        connection.?refused|other side closed|fetch failed|upstream.?connect|
        reset before headers|terminated|retry delay/i.test(err);
}
```

```javascript
// agent-session.js:1678-1741
async _handleRetryableError(message) {
    const settings = this.settingsManager.getRetrySettings();
    // settings = { maxRetries: 3, baseDelayMs: 2000, maxDelayMs: 60000 }
    this._retryAttempt++;
    if (this._retryAttempt > settings.maxRetries) {
        // Max retries exceeded
        return false;
    }
    const delayMs = settings.baseDelayMs * 2 ** (this._retryAttempt - 1);
    // --> 2s, 4s, 8s, 16s

    // 关键: Remove error message from agent state (keep in session for history)
    const messages = this.agent.state.messages;
    if (messages.length > 0 && messages[messages.length - 1].role === "assistant") {
        this.agent.replaceMessages(messages.slice(0, -1));
    }

    await sleep(delayMs, this._retryAbortController.signal);
    setTimeout(() => { this.agent.continue().catch(() => {}); }, 0);
    return true;
}
```

**关键设计**: 重试前从 agent state 中**移除错误 assistant message**。这样 LLM 重新从最后的 `user/tool` message 开始，不会被之前的错误误导。同时错误消息仍保留在 session 持久化层用于调试。

还有一个重要的分流：**context overflow 不走重试，走 compaction**。两条路径互斥。

### Nimbus 现状

```python
# nimbus/core/vcpu.py:208-213
except Fault as f:
    self.mmu.add_system_message(f"[LLM Error] {f.message}")
    errs = self._exec.on_error()
    if errs >= self.config.max_consecutive_errors:
        return self._error_step(result, f"Too many LLM stream errors: {f.message}")
    return result  # non-final, will retry
```

问题：
1. **没有指数退避** -- 连续错误之间没有等待，可能导致快速耗尽重试次数
2. **错误 message 被保留在 context** -- `add_system_message("[LLM Error]...")` 会留在 MMU 中，可能误导 LLM
3. **没有区分 retryable vs non-retryable** -- 所有 LLM Fault 都走相同逻辑
4. LiteLLM 内部有一些 provider 级重试，但不受 Nimbus 控制，也没有 `Retry-After` 解析

### 建议

1. **在 Session/RuntimeLoop 层加指数退避**:
   ```python
   delay_ms = base_delay_ms * (2 ** (attempt - 1))  # 2s, 4s, 8s
   await asyncio.sleep(delay_ms / 1000)
   ```
2. **重试前清除错误 assistant message**，而不是注入 `[System]` 消息
3. **区分 retryable 和 non-retryable**，移植 Pi 的正则：
   ```python
   RETRYABLE_PATTERN = re.compile(
       r'overloaded|rate.?limit|too many requests|429|500|502|503|504|'
       r'service.?unavailable|server error|internal error|connection.?error|'
       r'connection.?refused|fetch failed|terminated|retry delay',
       re.IGNORECASE
   )
   ```
4. **Context overflow 排除出 retryable**，走 compaction 路径

---

## 4. Per-Tool 错误隔离

### Pi 设计

```javascript
// pi-agent-core/dist/agent-loop.js:222-244
let result;
let isError = false;
try {
    if (!tool) throw new Error(`Tool ${toolCall.name} not found`);
    const validatedArgs = validateToolArguments(tool, toolCall);
    result = await tool.execute(toolCall.id, validatedArgs, signal, (partialResult) => {
        stream.push({ type: "tool_execution_update", ... });
    });
} catch (e) {
    result = {
        content: [{ type: "text", text: e instanceof Error ? e.message : String(e) }],
        details: {},
    };
    isError = true;
}
```

每个 tool 单独 `try/catch`。失败的 tool 返回 `isError: true` 的 ToolResult，agent 看到错误后自行决定重试或换策略。**单个 tool 失败永远不会 crash 整个循环**。

### Nimbus 现状

```python
# nimbus/core/gate.py:173-205
try:
    raw_output = await asyncio.wait_for(
        self._executor(tool_name, exec_args),
        timeout=effective_timeout,
    )
    # ... process output
except asyncio.TimeoutError:
    result = ToolResult(status="TIMEOUT", output=f"Tool '{tool_name}' timed out", ...)
except Exception as e:
    result = ToolResult(status="ERROR", output=f"Tool '{tool_name}' failed: {e}", ...)
```

Nimbus 的 KernelGate 已经有了类似的隔离设计 -- tool 异常被捕获为 `ToolResult(status="ERROR")`，不会传播到外层。这一点和 Pi 设计一致。

但在 VCPU 层还有一个区别：

```python
# nimbus/core/vcpu.py:268-285 -- tool 执行循环
for idx, action in enumerate(tool_actions):
    if self._interrupted:
        for remaining in tool_actions[idx:]:
            skip = ToolResult(status="CANCELLED", output="Execution interrupted.")
            # ...
        break
    tool_result = await self.gate.syscall_tool(action)
    result.results.append(tool_result)
    self.mmu.add_tool_result(action.id, action.name, str(tool_result.output))
```

**已做得不错**: gate 层隔离 + vcpu 层中断检查，结构清晰。无需大改。

---

## 5. Goal 跟踪策略差异

### Pi 设计

Pi 的 goal 不是独立字段，而是嵌入在 compaction summary 的 `## Goal` 部分：

```javascript
// Pi 的 summarization prompt (内嵌在 compaction 逻辑中)
// "Preserve existing goals, add new ones if the task expanded."
```

Goal 随 LLM 的理解演化 -- 初始目标是 "修 bug"，后来 LLM 发现需要重构，goal 会变成 "修 bug + 重构相关模块"。

### Nimbus 设计

```python
# nimbus/core/mmu.py:505-507
def set_goal(self, goal: str) -> None:
    """Pin the user's original goal (resists recency bias)."""
    self._goal = goal
```

```python
# nimbus/core/mmu.py:551-555 -- 在 assemble_context() 中注入
if self._goal:
    messages.append({
        "role": "user",
        "content": f"### CURRENT GOAL\n{self._goal}\n\n---\n"
    })
```

Nimbus 的 `_goal` 是确定性字段，直接取最新 user message。

同时 Nimbus 的 summarization prompt 也已经包含了 Goal section：

```python
# nimbus/core/mmu.py:59-86
SUMMARIZATION_PROMPT = """...
## Goal
[What is the user trying to accomplish?]
...
"""

UPDATE_SUMMARIZATION_PROMPT = """...
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
..."""
```

### 分析

| 维度 | Pi | Nimbus |
|------|-----|--------|
| Goal 来源 | LLM 在 summary 中生成 | 用户原始消息 |
| 确定性 | 低（依赖 LLM 正确提取） | 高（确定性赋值） |
| 演化能力 | 强（LLM 可更新 goal） | 弱（固定为最新 user msg） |
| 抗遗忘 | 中（summary 可能丢信息） | 强（独立字段，永不被压缩） |

### 建议

**混合策略**:
- 保留 `_goal` 字段作为确定性锚点（保证不丢失）
- 在 `UPDATE_SUMMARIZATION_PROMPT` 中增加明确指令：要求 LLM 在 `## Goal` section 中不仅复述原始 goal，还要补充任务演化信息
- 两者共存：`assemble_context()` 先注入 `_goal`（确定性），summary 中的 `## Goal` 提供演化上下文

---

## 6. Steering vs Follow-up 双队列

### Pi 设计

```javascript
// pi-agent-core/dist/agent-loop.js:64-126
while (true) {
    // Inner loop: process tool calls + steering
    while (hasMoreToolCalls || pendingMessages.length > 0) {
        // ... execute tools
        // After each tool, check steering:
        if (getSteeringMessages) {
            const steering = await getSteeringMessages();
            if (steering.length > 0) {
                steeringMessages = steering;
                // Skip remaining tools
                for (const skipped of remainingCalls) {
                    results.push(skipToolCall(skipped, stream));
                }
                break;
            }
        }
    }
    // After inner loop exits, check follow-up:
    const followUpMessages = (await config.getFollowUpMessages?.()) || [];
    if (followUpMessages.length > 0) {
        pendingMessages = followUpMessages;
        continue;  // Re-enter inner loop
    }
    break;  // No more messages, exit
}
```

清晰区分：
- **Steering**: 运行中插入，每个 tool 执行后检查。触发时**跳过剩余 tool calls**
- **Follow-up**: 仅在 agent 自然停止后检查。重新进入循环

### Nimbus 现状

```python
# nimbus/core/vcpu.py:287-301 -- Steering 检查
if self._get_steering and idx < len(tool_actions) - 1:
    steering = self._get_steering()
    if steering:
        # Skip remaining tool calls (pi-style)
        for remaining in tool_actions[idx + 1:]:
            skip = ToolResult(status="SKIPPED", output="Skipped due to queued user message.")
            result.results.append(skip)
            self.mmu.add_tool_result(remaining.id, remaining.name, skip.output)
        result.steering_messages = steering
        break
```

Nimbus 已经实现了和 Pi 一致的 steering 机制（每个 tool 后检查，跳过剩余 tools）。Follow-up 队列也在 RuntimeLoop 层实现。

**状态**: 设计已对齐 Pi，无需大改。

---

## 7. Session 持久化

### Pi 设计

```javascript
// pi-coding-agent/dist/core/session-manager.js:79-93
export function parseSessionEntries(content) {
    const entries = [];
    const lines = content.trim().split("\n");
    for (const line of lines) {
        if (!line.trim()) continue;
        try {
            const entry = JSON.parse(line);
            entries.push(entry);
        } catch {
            // Skip malformed lines  <-- crash-safe
        }
    }
    return entries;
}
```

特点：
- **JSONL 追加式写入**: 每条消息一行 JSON，追加到文件末尾。最小化数据丢失风险
- **树结构**: 每条 entry 有 `id/parentId`，支持 branching（用户回退到某个点重新对话）
- **Crash 安全**: 部分写入的行会被 `try/catch` 跳过
- **Session 文件延迟创建**: 第一个 assistant 响应到达后才创建文件（避免空 session）
- **版本迁移**: v1 -> v2 -> v3 原地幂等升级

```javascript
// session-manager.js:18-73
function migrateV1ToV2(entries) { ... }  // 添加 id/parentId
function migrateV2ToV3(entries) { ... }  // hookMessage -> custom
function migrateToCurrentVersion(entries) {
    if (version < 2) migrateV1ToV2(entries);
    if (version < 3) migrateV2ToV3(entries);
}
```

### Nimbus 现状

```python
# nimbus/core/storage.py:29-64
def save_session(self, session_id, status, messages, vcpu_state, ...):
    dump = {
        "session_id": session_id,
        "status": status,
        "updated_at": datetime.now().isoformat(),
        "messages": messages,
        "vcpu_state": vcpu_state,
        ...
    }
    temp_path = path.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(dump, f, indent=2, ensure_ascii=False)
    temp_path.replace(path)  # atomic rename
```

特点：
- **整体重写**: 每次 `save_session()` 重写整个 JSON 文件
- **原子写**: 使用 tmp + rename，保证写入完整性
- **不支持 branching**: 线性消息列表
- **无版本迁移**: 单一格式

### 对比

| 维度 | Pi (JSONL 树) | Nimbus (JSON 整体) |
|------|--------------|-------------------|
| 写入性能 | O(1) 追加 | O(n) 全量重写 |
| Crash 安全 | 最多丢一行 | tmp+rename 保证完整 |
| Branching | 支持（id/parentId） | 不支持 |
| 读取复杂度 | 需要全量解析 | 直接加载 |
| 存储大小 | 较大（无 indent） | 较大（indent=2） |
| 版本升级 | 原地迁移 | 无版本概念 |

### 建议

当前 Nimbus 的 JSON 整体写方案对小中规模 session 足够用。如果未来需要支持 branching 或超长 session，再考虑迁移到 JSONL。短期内**优先级低**。

但应该添加 **session 版本号**，为未来的格式迁移预留空间：
```python
dump = {
    "version": 1,  # <-- 加这个
    "session_id": session_id,
    ...
}
```

---

## 8. 其他值得学习的细节

### 8.1 Provider API Key 动态解析

```javascript
// pi-agent-core/dist/agent-loop.js:150
const resolvedApiKey = (config.getApiKey
    ? await config.getApiKey(config.model.provider)
    : undefined) || config.apiKey;
```

每次 LLM 调用前重新获取 API key。支持短时 OAuth token（如 GitHub Copilot token 每小时过期）。

**Nimbus 影响**: 当前是在 agent 初始化时传入 API key，整个 session 生命周期固定。如果要支持 OAuth-based provider（如 GitHub Copilot），需要改为按需获取。

### 8.2 Bash 大输出处理

```javascript
// pi-coding-agent/dist/core/bash-executor.js:44-86
const maxOutputBytes = DEFAULT_MAX_BYTES * 2;  // 100KB 内存缓冲
// ...
if (totalBytes > DEFAULT_MAX_BYTES && !tempFilePath) {
    // 超过 50KB 写临时文件 /tmp/pi-bash-*.log
    tempFilePath = join(tmpdir(), `pi-bash-${id}.log`);
    tempFileStream = createWriteStream(tempFilePath);
    // 把已缓冲的内容也写入
    for (const chunk of outputChunks) {
        tempFileStream.write(chunk);
    }
}
```

```javascript
// pi-coding-agent/dist/core/tools/truncate.js
export const DEFAULT_MAX_LINES = 2000;
export const DEFAULT_MAX_BYTES = 50 * 1024; // 50KB
```

Pi 的 bash 大输出处理：
1. 内存中维护滚动缓冲（最多 100KB），实时流式输出
2. 超过 50KB 开始同步写 `/tmp/pi-bash-*.log` 临时文件
3. 最终输出 tail-truncated（保留最后 2000 行或 50KB）
4. `fullOutputPath` 字段让 agent 知道完整输出在哪

**Nimbus 对比**:

```python
# nimbus/core/tools/bash.py:17-18
MAX_OUTPUT_BYTES = 100 * 1024   # 100KB
MAX_OUTPUT_LINES = 2000

# bash.py:214-228
if original_bytes > MAX_OUTPUT_BYTES:
    output = output[-(MAX_OUTPUT_BYTES):]  # 保留尾部
    output = "[...truncated...]\n" + output
```

Nimbus 的 100KB / 2000 行限制和 Pi 的 50KB / 2000 行接近，但 Nimbus 没有写临时文件的机制。当前方案对 agent 使用场景够用。

### 8.3 Session Migration

```javascript
// pi-coding-agent/dist/core/session-manager.js:19-73
function migrateV1ToV2(entries) { ... }  // id/parentId
function migrateV2ToV3(entries) { ... }  // hookMessage → custom
function migrateToCurrentVersion(entries) {
    const version = header?.version ?? 1;
    if (version >= CURRENT_SESSION_VERSION) return false;
    if (version < 2) migrateV1ToV2(entries);
    if (version < 3) migrateV2ToV3(entries);
    return true;
}
```

幂等、逐版本递进的迁移链。Nimbus 目前没有这个机制，但当 session 格式发生变化时会需要。

---

## Nimbus 行动计划

按优先级排序，从高到低：

### P0 -- 核心鲁棒性（影响任务成功率）

| # | 改进项 | 涉及文件 | 工作量 | 预期收益 |
|---|--------|----------|--------|----------|
| 1 | **移除/放宽 max_iterations** | `vcpu.py:38,152-163` | S | 解除复杂任务的人为上限 |
| 2 | **Session 级指数退避重试** | 新增 RuntimeLoop 层逻辑 | M | 网络抖动/rate limit 自动恢复 |
| 3 | **重试前清除错误 assistant message** | `vcpu.py:208-213`, RuntimeLoop | S | 防止错误消息误导 LLM |
| 4 | **区分 retryable vs non-retryable 错误** | `vcpu.py`, 新增正则 | S | 精确重试，避免无效重试 |

### P1 -- Compaction 增强（影响长任务表现）

| # | 改进项 | 涉及文件 | 工作量 | 预期收益 |
|---|--------|----------|--------|----------|
| 5 | **Overflow 检测正则（multi-provider）** | 新增 `overflow.py` | S | 覆盖 Anthropic/OpenAI/Google 等 provider |
| 6 | **Overflow compaction + 自动重试路径** | MMU/VCPU/RuntimeLoop | M | Context overflow 自动恢复，不终止 agent |
| 7 | **Compaction 后清除 overflow 错误消息** | MMU | S | 防重复 compaction 循环 |

### P2 -- 可靠性加固（防御性编程）

| # | 改进项 | 涉及文件 | 工作量 | 预期收益 |
|---|--------|----------|--------|----------|
| 8 | **Goal 混合策略** | `mmu.py` summarization prompt | S | 保留确定性锚点 + 演化信息 |
| 9 | **Session 版本号** | `storage.py` | XS | 为未来格式迁移预留 |
| 10 | **Iteration 计数器改为纯可观测** | `vcpu.py` | XS | 日志/metrics 可用，不作为终止条件 |

### P3 -- 未来储备（按需实施）

| # | 改进项 | 涉及文件 | 工作量 | 预期收益 |
|---|--------|----------|--------|----------|
| 11 | **API key 动态获取** | ALU/Adapter 层 | M | 支持 OAuth token 类 provider |
| 12 | **Bash 临时文件写出** | `bash.py` | S | 超大输出可回查 |
| 13 | **JSONL 持久化** | `storage.py` 重写 | L | Crash-safe + branching |

### 实施顺序建议

```
Phase 1 (本周): #1 + #4 + #10 -- 最小改动最大收益
  - 放宽 max_iterations（改一行 config）
  - 加 retryable 正则分流
  - iteration 计数器改为纯 metrics

Phase 2 (下周): #2 + #3 + #5 + #9 -- 重试机制完整化
  - RuntimeLoop 指数退避
  - 重试前清除错误 message
  - Overflow 检测正则
  - Session 版本号

Phase 3 (后续): #6 + #7 + #8 -- Compaction 增强
  - Overflow 自动恢复路径
  - Goal 混合策略
```

---

## 附录: 源码引用索引

| 引用 | 路径 |
|------|------|
| Pi agent-loop | `openclaw/node_modules/.pnpm/@mariozechner+pi-agent-core@0.55.3_.../dist/agent-loop.js` |
| Pi agent-session | `openclaw/node_modules/.pnpm/@mariozechner+pi-coding-agent@0.55.3_.../dist/core/agent-session.js` |
| Pi overflow | `openclaw/node_modules/.pnpm/@mariozechner+pi-ai@0.55.3_.../dist/utils/overflow.js` |
| Pi google-gemini-cli | `openclaw/node_modules/.pnpm/@mariozechner+pi-ai@0.55.3_.../dist/providers/google-gemini-cli.js` |
| Pi session-manager | `openclaw/node_modules/.pnpm/@mariozechner+pi-coding-agent@0.55.3_.../dist/core/session-manager.js` |
| Pi bash-executor | `openclaw/node_modules/.pnpm/@mariozechner+pi-coding-agent@0.55.3_.../dist/core/bash-executor.js` |
| Pi settings-manager | `openclaw/node_modules/.pnpm/@mariozechner+pi-coding-agent@0.55.3_.../dist/core/settings-manager.js` |
| Nimbus VCPU | `nimbus/src/nimbus/core/vcpu.py` |
| Nimbus MMU | `nimbus/src/nimbus/core/mmu.py` |
| Nimbus Gate | `nimbus/src/nimbus/core/gate.py` |
| Nimbus Storage | `nimbus/src/nimbus/core/storage.py` |
| Nimbus Bash | `nimbus/src/nimbus/core/tools/bash.py` |
