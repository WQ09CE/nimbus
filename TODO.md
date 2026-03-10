# TODO

## 🔧 Tool Result 超长截断治理

**涉及**: `src/nimbus/core/gate.py`, `web-ui/src/components/chat/tools/`

### 问题描述

Tool result（Read/Grep/Bash 等）可能返回非常长的输出（Read 最大 100KB，Bash 无硬上限）。
当前直接把完整 output 塞进 LLM context，浪费 token 且可能触发 context overflow。

### 预期方案

**LLM 侧（gate.py）**：
- 对超长 tool result 做强制截断（保留头部 + 尾部关键内容）
- 截断后在 tool result 中**显式告知 LLM**："[输出已截断，共 N 行，显示前 100 行和后 20 行]"
- 让 LLM 知道数据不完整，可以决定是否需要分段读取

**Web-UI 侧（SSE event + 前端组件）**：
- SSE `tool_result` 事件继续传输完整（或接近完整）的 output，供 UI 展示
- 前端组件（FileRead/Bash/DefaultTool）自行实现 UI 层截断：
  - 折叠 + "Show all (N lines)" 展开按钮
  - 语法高亮只渲染可见部分
- 实现 LLM context 省 token 与 UI 展示完整性的解耦

### 关键设计点

- `gate.py:_truncate_output()` 已有 200K 硬截断，需改为更精细的分层策略
- `gate.py:_finish()` 的 `"output"` 字段（给 SSE/UI）和 `result.output`（给 LLM context）应分离
- 截断阈值可配置（默认建议 LLM 侧 ~4000 chars，UI 侧 ~50000 chars）
