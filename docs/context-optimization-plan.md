# Context 膨胀问题研究：Nimbus vs Pi 深度对比

## 问题

Nimbus 框架 token 消耗过快，context 很快超过 200K，而 Pi coding agent 在同类任务下稳步增长。

---

## 核心差异对比（经二次核实）

| 维度 | Pi (成熟方案) | Nimbus (当前) | 影响 |
|------|-------------|-------------|------|
| **bash 截断** | **50KB / 2000行**，超过后写临时文件 | 100KB / 2000行，不写临时文件 | 🔴 2x |
| **read 截断** | **50KB / 2000行** | 100KB / 2000行 | 🔴 2x |
| **grep 截断** | 50KB + 每行截断 **500字符** | **200条匹配，无字节限制，无行截断** | 🔴 可爆炸 |
| **Keep recent** | **token-based** `20000 tokens` | **count-based** `20条消息` | 🔴 20条大消息=100K+ |
| **Compaction 触发** | usage + trailing 估算 | ✅ hybrid 估算（已对齐） | ✅ |
| **Compaction 效果** | `replaceMessages` 立即收缩 | ✅ `assemble_context` 下次生效（等价） | ✅ |
| **大输出临时文件** | bash 超 50KB 自动写 `/tmp`，context 中只放截断+路径 | ❌ 无此机制 | 🟡 |
| **Overflow recovery** | 自动 compact → retry（最多1次） | ❌ 直接报错 | 🟡 |

---

## 根因详析（按影响排序）

### 1. 🔴 grep 无字节限制 + 无单行截断（最危险）

**Pi grep**: `truncateHead(rawOutput, { maxLines: Infinity })` 仍受 **50KB 字节限制**。每行还截断到 **500 字符**：
```typescript
// Pi: 每行最多500字符
export const GREP_MAX_LINE_LENGTH = 500;
```

**Nimbus grep**: `MAX_MATCHES = 200` 按条数限制，但每条匹配行**无长度限制**。一个 JSON 文件里一行可能上万字符，200 条匹配 = **数 MB context**。

### 2. 🔴 Tool 截断阈值 2x（bash + read）

| Tool | Pi | Nimbus | 差距 |
|------|-----|--------|------|
| bash `MAX_OUTPUT_BYTES` | 50KB | 100KB | 2x |
| read `MAX_BYTES` | 50KB | 100KB | 2x |

并行执行时放大：Nimbus 5 个并行 Read = 5 × 100KB = **500KB**；Pi = 5 × 50KB = 250KB。

Pi 还有关键机制：**bash 超过 50KB 自动写临时文件** `/tmp/pi-bash-*.log`，context 中只放截断的尾部 + 文件路径。这样 LLM 需要看全文时可以 `Read` 临时文件而非占用 context。

### 3. 🔴 `keep_recent` 用条数而非 token 数

**Pi**: `keepRecentTokens: 20000` — 保留最近 ~20K tokens，不管有几条消息。

**Nimbus**: `keep_recent_messages: 20` — 保留最近 20 条。
- 3 个大 Read 结果（各 100KB）= 3 条 tool result 占 ~75K tokens
- 加上 assistant + user 消息 = compaction 后还保留了 80-100K tokens
- **Compaction 效果大打折扣**

### 4. 🟡 Overflow recovery

Pi 有两级保护：
1. **threshold compaction**：`contextTokens > contextWindow - 16384` 时主动压缩
2. **overflow recovery**：LLM 返回 `context_length_exceeded` → 删错误消息 → compact → 自动 retry

Nimbus 只有 threshold compaction，overflow 直接报错。

---

## 修复方案

### P0: 统一工具截断到 50KB（最大投入产出比）

#### [MODIFY] [bash.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/tools/bash.py)
- `MAX_OUTPUT_BYTES`: 100KB → **50KB**
- 大输出时**写临时文件**（对齐 Pi），context 内只放截断版 + 路径

#### [MODIFY] [read.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/tools/read.py)
- `MAX_BYTES`: 100KB → **50KB**

#### [MODIFY] [grep.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/tools/grep.py)
- 添加 **50KB 字节硬限**
- 每行截断到 **500 字符**（对齐 Pi 的 `GREP_MAX_LINE_LENGTH`）

### P1: keep_recent 改为 token-based

#### [MODIFY] [mmu.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/mmu.py)
- `keep_recent_messages: int = 20` → `keep_recent_tokens: int = 20000`
- `_smart_drop` 和 `_find_cut_point` 统一按 token 数保留最近消息
- 确保 compaction 后 context 显著缩减

### P2: Overflow auto-recovery

#### [MODIFY] [vcpu.py](file:///Users/wangqing/sourcecode/agent/agent-framework/nimbus/src/nimbus/core/vcpu.py)
- 捕获 `context_length_exceeded` / `ContextWindowExceeded` 错误
- 删除错误消息 → forced compact → retry（最多 1 次）

---

## 验证计划

### 定量对比
1. 用同一对话（10+ 轮、包含 Read/Bash/Grep 操作）对比：
   - 修改前 token 增长速率
   - 修改后 token 增长速率
2. 验证 compaction 后 context 缩减到 ~20K recent + summary

### 手动验证
1. TokenFooter 观察：长对话下 token 应保持在合理范围
2. grep 大文件后验证 context 不爆炸
3. bash 大输出后验证临时文件生成 + 截断显示
