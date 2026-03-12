# Nimbus Subagent 治理方案 (V3 — 极简版)

> Owner: WQ · Date: 2026-03-12 · Status: **V3 (遵循"非必要不加"原则)**
> 
> 设计哲学：只解决**现在真正在痛的问题**。不预设未来的 async/PAUSED/Semaphore 等复杂机制。
> 等到真正需要时再加，代价不会更高。

## 1. 现状评估：什么已经在工作？

当前 `spawn_agent.py` 已有的治理能力（不需要重做）：

- ✅ **上下文隔离**：子 Agent 独立 MMU，不污染父级
- ✅ **输出截断**：返回值 > 4000 字符自动截断，指向 Scratchpad
- ✅ **超时控制**：`asyncio.wait_for` + 可配置 `timeout_seconds`
- ✅ **Partial Recovery**：超时/异常时 `_collect_partial()` 收集已有成果
- ✅ **Abort 传播**：父级中断时级联取消子 Agent
- ✅ **Scratchpad 增量写入**：System Prompt 已要求子 Agent 边做边写

**结论**：基础设施已经 80% 到位。剩下的 20% 是四个精确的手术刀修复。

## 2. 四个必须修复的问题

### 2.1 修复 Decoder `_is_done()` 误杀子 Agent

**痛点**：子 Agent 设置了 `text_is_final=False`，但 Decoder 的 `_is_done()` 对短文本（≤120 字符）过于激进——只要不含"接下来"等规划词就判定为 RETURN。导致子 Agent 说一句"好的，我来看看这个文件"就被强制结束。

**修复方案**：为子 Agent 引入 `contract_mode` 标志。在此模式下，纯文本**永远只产出 THOUGHT**，绝不触发 RETURN。子 Agent 唯一的退出路径是：
1. 所有工具都用完自然结束（`max_iterations`）
2. 被 `max_consecutive_thoughts` 限制兜底

**改动范围**：
- `decoder.py`：`decode()` 方法增加 `contract_mode: bool = False` 参数
- `vcpu.py`：构造函数透传 `contract_mode`
- `spawn_agent.py`：创建子 Agent 时设置 `contract_mode=True`

**代码量**：约 15 行。

### 2.2 迭代倒计时 Steering（耗尽前强制收尾）

**痛点**：子 Agent 有时在 `max_iterations` 限制内一直探索，耗尽步数时 Scratchpad 里只有半成品，没有总结。

**修复方案**：在 `vcpu.py` 的 `step()` 中，当 `iteration >= max_iterations * 0.85` 时，自动注入一条 System Message：

```
⚠️ 你只剩 N 步可用。请立即将当前所有发现写入 Scratchpad 并总结。
```

**改动范围**：
- `vcpu.py`：`step()` 方法中加一个 `if` 判断，约 5 行

**代码量**：约 5 行。

### 2.3 结构化返回数据（`submit_result` 工具）

**痛点**：子 Agent 完成任务后，父 Agent 拿到的是一坨自然语言文本。父 Agent 必须做语义解析才能理解结果，极易产生幻觉或遗漏关键信息。

**当前的返回值长这样**：
```
Sub-agent [reader] completed successfully.

**Result:**
好的，我已经阅读了 vcpu.py、decoder.py 和 loop.py 这三个文件。
vcpu.py 大约有 270 行，主要包含一个 VCPU 类，它实现了 Think-Act-Observe
状态机...（后面还有几千字自由发挥）
```

父 Agent 要从这段话里提取"到底有哪些文件、每个文件多大、关键结构是什么"——完全靠猜。

**修复方案**：给子 Agent 注入一个通用的 `submit_result` 工具。子 Agent 在完成任务时**必须调用它**来交付结构化 JSON，而非用自然语言汇报。

```python
# submit_result 工具定义
@tool(name="submit_result")
async def submit_result(summary: str, findings: list[str], artifacts: list[str]):
    """提交结构化的任务结果。必须在任务完成时调用。"""
    # 写入固定路径
    path = f".nimbus/sessions/{sub_session_id}/deliverable.json"
    json.dump({"summary": summary, "findings": findings, "artifacts": artifacts}, ...)
    return {"status": "DELIVERED"}
```

`spawn_agent` 完成时的消费链路：
1. 优先读取 `deliverable.json` → 如果存在，将其 JSON 作为 `output` 返回给父 Agent
2. 如果不存在（子 Agent 超时或未调用 submit_result）→ 降级到现有的文本 + Scratchpad 方案

**父 Agent 拿到的结构化返回值**：
```json
{
  "summary": "分析了 3 个核心文件，VCPU 状态机有 7 个状态",
  "findings": [
    "vcpu.py: 270 行，核心类 VCPU，FSM 引擎",
    "decoder.py: 180 行，LLM 输出防火墙，含幻觉检测",
    "loop.py: 680 行，RuntimeLoop 驱动 VCPU"
  ],
  "artifacts": ["scratchpad.md"]
}
```

**关键约束**：
- `summary` ≤ 500 字符，`findings` 每条 ≤ 200 字符（解析后截断，不破坏 JSON）
- `submit_result` 调用后立即触发 VCPU 中断（`request_interruption()`），防止继续执行
- 与 2.1 的 `contract_mode` 配合：纯文本不能结束，必须调用 `submit_result` 才能退出

**改动范围**：
- 新增 `tools/submit_result.py`：工具实现，写 `deliverable.json`
- `spawn_agent.py`：注册工具到子 Agent 工具集；完成时优先读 `deliverable.json`
- `spawn_agent.py`：System Prompt 补充"必须调用 submit_result 交付结果"指令

**代码量**：约 50 行。

### 2.4 SSE 进展事件（最小化方案）

**痛点**：父 Agent 执行 `spawn_agent` 期间，WebUI 只显示 loading 动画。

**修复方案（不引入 SSEHub 合流）**：
现有的 `on_update` callback 已经能输出文字。我们只需让它输出**结构化的进度文本**，而非自由格式字符串。前端渲染时识别这些固定格式即可做简单展示。

在 `_drain_loop()` 中，每次子 Agent 完成一个工具调用时，通过 `on_update` 发出格式化进展：

```
[spawn:reader] 🔧 Read src/nimbus/core/vcpu.py (2.1KB)
[spawn:reader] 🔧 Grep "def step" in src/ (3 matches)
[spawn:reader] 💭 Analyzing VCPU state machine...
```

前端只需对 `[spawn:XXX]` 前缀做简单的折叠/展开渲染。

**改动范围**：
- `spawn_agent.py`：在 `_drain_loop()` 中监听 tool_call/tool_result 事件，格式化输出
- 前端（可选，后续）：识别 `[spawn:*]` 前缀做折叠 UI

**代码量**：约 20 行。

## 3. 明确不做的事（及理由）

| 方案 | 不做的理由 |
|------|-----------|
| 按 Role 动态注册不同交付工具 | 一个通用 `submit_result` 足够，不需要 `submit_findings` / `submit_analysis` 等按角色拆分 |
| PAUSED / resume_agent | 极其复杂（MMU 脏状态、Core Dump schema 扩展、协程恢复）。如果子 Agent 失败，父 Agent 可以直接带着旧 Scratchpad 重新 spawn 一个新的 |
| Process Registry | 只有 async mode 才需要。当前 sync-only 模式下没有意义 |
| LLM Semaphore | 目前并发子 Agent 场景极少（sync 模式下根本不存在并发）。等真正遇到 429 惊群时再加，只需在 Adapter 层包一层 Semaphore 即可 |
| SSEHub 合流改造 | 工程量大（需改 SSEHub、Session 层、前端状态管理），ROI 不如文本格式化方案 |
| 新增 `subagent_event` 事件类型 | 破坏现有事件体系。文本格式化足够当前需求 |

## 4. 实施计划

**总工时：1 天**

| 步骤 | 内容 | 预计 |
|------|------|------|
| 1 | 写测试 `test_decoder_contract_mode.py` | 30 min |
| 2 | 实现 Decoder `contract_mode` | 15 min |
| 3 | 实现 VCPU 迭代倒计时 Steering | 15 min |
| 4 | 实现 `submit_result` 工具 + spawn_agent 消费链路 | 45 min |
| 5 | 实现 `_drain_loop` 结构化 `on_update` | 30 min |
| 6 | 集成测试 | 30 min |

## 5. 未来演进路径（等痛了再做）

当以下条件满足时，再考虑引入对应机制：

- **当 sync 子 Agent 频繁超时影响体验** → 引入 `mode="async"` + `poll_agent`
- **当 async 并发 > 3 且频繁 429** → 引入 LLM Semaphore（放在 Adapter 层）
- **当通用 `submit_result` 无法区分不同 Role 的交付物** → 引入按 Role 动态注册的专用交付工具
- **当文本格式化的 `on_update` 无法满足 UX 需求** → 引入 SSEHub 事件合流
- **当需要跨重启恢复子 Agent** → 引入 Core Dump 恢复 + Process Registry
