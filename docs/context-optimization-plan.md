# Context 管理整改 Design Doc

> Owner: WQ · Date: 2026-03-12 · Status: **已实施并验证**

## 1. 问题背景

Nimbus agent 的 context 消耗过快——简单 10 轮对话 context 就逼近 200K。核心原因是框架缺乏 Pi coding agent 中已成熟的 **assembly-time token 预算** 机制。

## 2. 根因分析

通过与 Pi 源码（`sourcecode/pi-mono`）的深度对比，定位到 4 项关键差异：

| 维度 | Pi | Nimbus (改前) | 影响 |
|------|-----|-------------|------|
| **Assembly-time 限制** | `keepRecentTokens: 20K` — 每次 LLM 调用只发送 budget 内的消息 | ❌ `assemble_context` 发送**全部** messages | 🔴 致命 |
| **Tool 截断** | 50KB (bash/read/grep) | 100KB, grep 无字节限制 | 🔴 2x+ |
| **keep_recent** | token-based `20000` | count-based `20条` | 🔴 |
| **Overflow recovery** | compact → retry | ✅ 已有（`CTX_OVERFLOW` → `_try_compaction`） | ✅ |

## 3. 架构设计

### 3.1 Assembly-Time Token Budget（核心）

```
assemble_context()                      ← 每次 LLM 调用前
├── 1. anchor: system prompt + goal + summary
├── 2. 计算 available_budget = max_context - anchor - reserve(4K)
├── 3. 从末尾回溯 _messages，累积 tokens 直到超 budget
│   └── cut_index 不切分 tool_call ↔ tool_result 对
└── 4. 只发送 messages[cut_index:] 给 LLM
```

**与 compaction 的关系**：assembly-time 限制是 **即时生效的防线**（每次 LLM 调用都限制），compaction 是 **存储层清理**（真正减少 `_messages` 数组大小）。两者互补。

### 3.2 工具截断对齐

```
bash.py:  100KB → 50KB + 超限写 /tmp/nimbus-bash-*.log
read.py:  100KB → 50KB
grep.py:  无限制 → 50KB + 500 char/line
```

### 3.3 Token-Based Keep Recent

```
MMUConfig.keep_recent_messages: 20  →  keep_recent_tokens: 20000
_smart_drop: 按 token 回溯计算 hot boundary（而非固定条数）
```

## 4. 改动清单

| 文件 | 改动 | Commit |
|------|------|--------|
| [mmu.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/mmu.py) | `assemble_context` 加入 token budget 限制 | `5c52511` |
| [mmu.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/mmu.py) | `keep_recent_messages` → `keep_recent_tokens` | `145df0c` |
| [bash.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/tools/bash.py) | 50KB + 临时文件 | `145df0c` |
| [read.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/tools/read.py) | 50KB | `145df0c` |
| [grep.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/tools/grep.py) | 50KB + 500 char/line | `145df0c` |
| [test_mmu.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/tests/core/test_mmu.py) | 孤儿检测测试 + 参数迁移 | `26a5b9d` |

## 5. 验证结果

### 5.1 单元测试

65/65 全绿，含 3 个新增孤儿检测测试（token boundary mid-turn / emergency hot zone / multi-result）。

### 5.2 实景测试 (10 场景)

per-call `step_input` 对比：

```
场景                       v1 (无 budget)   v2 (有 budget)     Δ
───────────────────────────────────────────────────────────────
1_simple_question                 726            727      +0%
3_read_large_file (30KB)       10,183         10,684      +5%
4_grep_codebase                26,123          2,185    -92% ✅
5_grep_broad                   17,178          2,462    -86% ✅
9_grep_json                    21,053          8,100    -62% ✅
10_complex_task                 7,291          2,570    -65% ✅
```

核心改善：场景 4 从 **26K→2K**（-92%），因为之前 3 轮的大 tool output 不再无脑带入 context。

## 6. 数据流示意

```
User msg → MMU._messages.append()
         → VCPU.step()
           → mmu.assemble_context()     ← ★ 这里限制发送量
             → LLM API call (bounded)
           → tool execution
           → mmu.add_tool_result()      ← tool output 截断到 50KB
         → 检查是否触发 compaction (85%)
           → archive_and_reset()        ← 存储层清理
```

## 7. 后续可选优化

- **Lazy Offload**: 在 assembly 时对旧 turn 的 tool result 二次截断，只保留摘要
- **Compaction 触发阈值下调**: 85% → 70%，更早清理存储层
- **Tool result 内容摘要**: 用小模型对大 tool output 生成摘要替代原文
