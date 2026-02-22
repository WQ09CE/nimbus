# NimFS ToolResult Offload 优化方案

> **Status**: Draft  
> **Author**: Architect Agent  
> **Date**: 2025-02-22  
> **Scope**: MMU offload pipeline, vCPU truncation,工具层智能截断

---

## 1. 问题诊断

### 1.1 "无效搬运"问题

当前 offload 流程在多数场景下**不但不省 token，反而增加开销**：

```
Read("mmu.py") → 返回 20KB
  → vCPU 截断至 4K (head 2K + tail 2K)          ← 如果未触发：继续
  → MMU offload (>8K) → NimFS → 替换为 ~300 char ref
  → Agent 看到 ref → 调 NimFSReadArtifact 读回全量 20KB
  → NimFSReadArtifact 在白名单，不会再被 offload
  → 全量 20KB 直接进 stream
  ─────────────────────────────────────────
  结果：token 净增 = 300 (ref) + 20K (读回) + 工具调用 overhead ≈ 21K
  对比直接保留：20K
  多花了 ~1K token + 一轮 LLM 调用延迟
```

### 1.2 数据流中的三层截断（现状）

| 层 | 位置 | 阈值 | 行为 | 文件位置 |
|---|---|---|---|---|
| L1 | vCPU | 4000 chars | head 2K + tail 2K, 非 final | `vcpu.py:858-866` |
| L2 | MMU add_tool_result | 8000 chars | NimFS offload → ref + 300 char preview | `mmu.py:315-405` |
| L3 | MMU _optimize_context | hot 10K / history 1K | 渲染时截断 view | `mmu.py:521-580` |

**矛盾**：L1 截到 4K 后，不会触发 L2 的 8K 阈值。但 L1 只对非 final 生效，specialist 的 SubmitResult 跳过 L1 → 大输出直接到 L2 触发 offload → orchestrator 读回 → 无效搬运。

### 1.3 场景分类

| 场景 | offload 有用？ | 为什么 |
|---|---|---|
| Agent Read 文件 | ❌ 无用 | Agent 必然需要看内容才能完成任务 |
| Agent grep/搜索 | ❌ 大概率无用 | Agent 需要看搜索结果来做判断 |
| Specialist 返回结果给 Orchestrator | ⚠️ 部分有用 | GoalDocument 自动展开可以传给下游 specialist，但 orchestrator 本身也需要看结果 |
| Agent ls 目录列表 | ✅ 有用 | Agent 可能只需要前几个文件名 |
| Agent cat 大日志 | ✅ 有用 | Agent 可能只需要关键错误行 |

**结论**：当前 offload 在大多数场景下是反效果。只有「结果很大但 agent 不需要全量」这一小类场景真正受益。

---

## 2. 方案分析

### 方案 A：智能 Preview（改良 offload 的 preview 质量）

**思路**：offload 时不用简单的 `content[:300]`，而是根据工具类型生成结构化摘要。

```python
# 例如 Read 文件时生成：
preview = """
File: mmu.py (580 lines, 20KB)
Classes: MMU, MMUConfig, PinnedContext
Key Methods:
  - add_tool_result (L315): NimFS auto-offload logic
  - _optimize_context (L521): View truncation
  - assemble_context (L630): Full context assembly
"""
```

| Pros | Cons |
|---|---|
| preview 质量高，agent 可能不需要读回 | 生成摘要需要额外计算/LLM 调用 |
| 不改变现有架构 | 对 grep/搜索结果的摘要效果有限 |
| | agent 如果确实需要全量，还是会读回 |

**评价：治标不治本。** 摘要不能替代实际内容，大多数情况 agent 仍需读回。且要写大量工具特定的摘要逻辑。

---

### 方案 B：分段读取 Artifact

**思路**：NimFSReadArtifact 支持 offset/limit，agent 按需读取片段。

| Pros | Cons |
|---|---|
| 理论上可以只读需要的部分 | agent 不知道要读哪部分 → 还是会读全量 |
| 与现有 Read 工具的 offset/limit 一致 | 增加了工具调用轮数 |

**评价：治标不治本。** Agent 的任务模式决定了它大概率需要全量内容。

---

### 方案 C：Lazy Expansion（渲染时智能展开）⭐

**思路**：offload 照常做，但在 `_optimize_context()` 渲染时，如果 token 预算允许，自动将 NimFS ref 展开回原始内容。Agent 永远不需要手动调 NimFSReadArtifact。

```
Tool 返回 20KB → offload 到 NimFS → stream 存 ref
                                         ↓
_optimize_context() 渲染时：
  if token_budget_remaining > content_size:
      展开 ref → 内联完整内容
  else:
      保留 ref + 增强 preview (2K)
```

| Pros | Cons |
|---|---|
| **根治**：消除 agent 手动读回的需求 | 需要改 _optimize_context 逻辑 |
| offload 仍然有效：token 压力大时不展开 | 展开需要读 NimFS，增加少量 I/O |
| 压缩/归档时 ref 仍然节省空间 | 需要区分「已被展开的 ref」和「普通内容」 |
| 向后兼容：不改变 offload 本身的行为 | |

**评价：最优方案。** 既保留了 offload 在压缩时的价值，又消除了 agent 的无效搬运。

---

### 方案 D：统一截断层（去掉 vCPU 截断，全部交给 MMU）

**思路**：去掉 vCPU 的 4000 chars 截断，统一由 MMU 的 offload + _optimize_context 处理。

| Pros | Cons |
|---|---|
| 简化架构，减少截断层 | vCPU 截断是有效的安全网（防止极大输出） |
| 一个地方管所有截断逻辑 | MMU 的 offload 如果没有方案 C 配合，仍是无效搬运 |

**评价：可以作为方案 C 的配套优化。** 单独做价值不大。

---

### 方案 E：工具层智能截断

**思路**：让 Read 工具本身根据文件大小智能截断，而不是在 MMU 层处理。

| Pros | Cons |
|---|---|
| 源头控制，最高效 | Read 工具已经有了 2000 行/100KB 限制 |
| 不需要改 MMU | 无法覆盖 Bash 等其他工具 |
| | 工具不知道 context 预算剩余多少 |

**评价：Read 已经有了基础截断。** 问题出在 8K~20K 这个「中间地带」—— 太大不能全部保留，太小不值得 offload。应该由 MMU 统一处理。

---

### 方案 F：增强 Preview（offload 时保留更长的 preview）

**思路**：offload 后保留 2K 而非 300 char 的 preview。

| Pros | Cons |
|---|---|
| 简单改动 | preview 从 300→2K 只是减少了一部分读回需求 |
| 2K preview 对小文件可能已经够了 | 大文件/搜索结果仍不够 |
| | 不彻底 |

**评价：可作为方案 C 的降级方案。** 但不如 C 彻底。

---

## 3. 推荐方案：C + D + F 组合

### 3.1 核心策略："Offload-Store, Inline-View"

**核心思想**：存储和视图分离。

- **存储层**（Stream）：大内容照常 offload 到 NimFS，stream 中只存 ref + 元信息。这保证了压缩/归档的效率。
- **视图层**（_optimize_context 渲染时）：根据 token 预算动态决定是否展开 ref。预算够就展开，不够就保留增强 preview。

```
                    Storage (节省长期空间)
                    ┌─────────────────┐
                    │ stream: ref     │ ← 只存引用，300 chars
                    │ NimFS: 全量内容  │ ← 全量保留
                    └─────────────────┘
                           │
                    View (按需展开)
                    ┌─────────────────┐
                    │ 预算够 → 内联展开 │ ← Agent 直接看到完整内容
                    │ 预算紧 → 2K预览   │ ← Agent 看到有用的预览
                    └─────────────────┘
```

### 3.2 实现设计

#### Phase 1: Lazy Expansion in `_optimize_context()` [核心]

**文件**: `core/memory/mmu.py` (`_optimize_context` 方法，L521-580)

```python
# 新增常量
NIMFS_REF_PATTERN = re.compile(r"nimfs://artifact/([\w\-]+)")
INLINE_EXPAND_MAX_CHARS = 15_000  # 单个 artifact 最大展开大小

def _optimize_context(self, messages: List[Dict], hot_count: int = 0) -> List[Dict]:
    """
    Phase 1: Inline-expand NimFS refs if budget allows.
    Phase 2: Image downgrade (existing).
    Phase 3: Tool output truncation (existing).
    """
    # --- Phase 1: NimFS Lazy Expansion ---
    # 计算当前总 token，确定可用预算
    total_chars = sum(
        len(m.get("content", "") if isinstance(m.get("content"), str) else "")
        for m in messages
    )
    remaining_budget_chars = (self.config.max_context_tokens * 4) - total_chars  # 粗估 1 token ≈ 4 chars
    
    total = len(messages)
    hot_boundary = 0 if hot_count == 0 else total - hot_count

    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        
        # 检测 NimFS offload ref
        if "[NimFS Auto-Offload]" not in content:
            continue
        
        ref_match = NIMFS_REF_PATTERN.search(content)
        if not ref_match:
            continue
        
        ref = f"nimfs://artifact/{ref_match.group(1)}"
        is_hot = (i >= hot_boundary)
        
        # 决策：是否展开
        try:
            from nimbus.core.nimfs.manager import NimFSManager
            manager = NimFSManager(self.nimfs_workspace)
            manifest = manager.get_artifact_manifest(ref)
            artifact_size = manifest.size_bytes
            
            if (is_hot 
                and artifact_size <= INLINE_EXPAND_MAX_CHARS
                and artifact_size <= remaining_budget_chars * 0.5):
                # 展开：预算充足
                full_content = manager.read_artifact(ref)
                new_msg = dict(msg)
                new_msg["content"] = full_content
                messages[i] = new_msg
                remaining_budget_chars -= artifact_size
            else:
                # 不展开：增强 preview
                full_content = manager.read_artifact(ref)
                preview_size = min(2000, len(full_content))
                enhanced_preview = (
                    f"[NimFS Offloaded] {manifest.size_bytes:,} chars stored at {ref}\n"
                    f"Preview ({preview_size} chars):\n"
                    f"{full_content[:preview_size]}\n"
                    f"... [{manifest.size_bytes - preview_size:,} chars remaining]\n"
                    f"Use NimFSReadArtifact(ref='{ref}') for full content."
                )
                new_msg = dict(msg)
                new_msg["content"] = enhanced_preview
                messages[i] = new_msg
        except Exception:
            pass  # 展开失败，保持原样
    
    # --- 继续原有的 image downgrade + truncation 逻辑 ---
    # ... (existing code)
```

**关键设计决策**：

1. **只展开 hot context 中的 ref**：history 中的 ref 不展开，保持 1K 截断即可
2. **预算卡口**：单个 artifact 不能消耗超过剩余预算的 50%，防止一个大 artifact 挤掉其他内容
3. **大小上限 15K**：超过 15K 的 artifact 不展开（太大了说明 agent 应该分段处理）
4. **展开顺序**：从最近的消息开始展开（最可能是 agent 当前需要的）

#### Phase 2: 增强 Preview（作为展开失败的降级）

**文件**: `core/memory/mmu.py` (`_offload_tool_result_to_nimfs` 方法，L369-405)

将 offload 时的 preview 从 300 chars 增加到 2000 chars：

```python
def _offload_tool_result_to_nimfs(self, tool_name: str, content: str) -> str:
    # ... existing NimFS write logic ...
    
    preview_size = min(2000, len(content))  # 从 300 → 2000
    return (
        f"[NimFS Auto-Offload] Tool '{tool_name}' returned {len(content):,} chars "
        f"(exceeded {self.config.nimfs_offload_threshold:,} threshold).\n"
        f"Full output stored at: {ref}\n\n"
        f"Preview ({preview_size} chars):\n{content[:preview_size]}\n"
        f"{'...' if len(content) > preview_size else ''}\n"
        f"Use NimFSReadArtifact(ref='{ref}') to retrieve the complete content."
    )
```

**目的**：即使 Lazy Expansion 因预算不足无法展开，2K preview 也能让 agent 在大多数情况下无需手动读回。

#### Phase 3: 统一截断层（简化 vCPU 截断）

**文件**: `core/runtime/vcpu.py` (L854-866)

将 vCPU 截断阈值从 4000 提高到与 MMU offload 阈值一致（8000），或直接去掉，让 MMU 统一管理：

```python
# 方案 a：提高阈值（保守）
VCPU_TRUNCATION_THRESHOLD = 8000  # 与 MMU offload 阈值对齐

# 方案 b：去掉 vCPU 截断（激进，推荐）
# 理由：MMU 的 offload + _optimize_context 已经覆盖了所有场景
# 只保留一个极端安全网（如 100K）防止 OOM
VCPU_SAFETY_THRESHOLD = 100_000  # 只防 OOM，不做常规截断
```

**推荐方案 b**。理由：
- vCPU 截断是「head 2K + tail 2K」这种简单拼接，丢失了中间内容
- MMU 的 offload 保留了全量内容在 NimFS
- Lazy Expansion 可以在渲染时智能决定保留多少
- vCPU 截断在 MMU offload 之前执行，截断后的内容不再触发 offload，失去了 NimFS 存档的机会

#### Phase 4: Offload 引用增加元信息（辅助展开决策）

在 offload ref 消息中增加结构化元信息，方便 `_optimize_context` 解析：

```python
# offload ref 消息格式（结构化，方便解析）
OFFLOAD_MARKER = "[NimFS Auto-Offload]"
ref_message = (
    f"{OFFLOAD_MARKER}\n"
    f"ref: {ref}\n"
    f"tool: {tool_name}\n"
    f"size: {len(content)}\n"
    f"---\n"
    f"{content[:2000]}"
)
```

### 3.3 offload 阈值调整

| 参数 | 当前值 | 新值 | 理由 |
|---|---|---|---|
| vCPU 截断阈值 | 4000 | 100000 (safety only) | 去掉常规截断，让 MMU 统一处理 |
| MMU offload 阈值 | 8000 | 8000 (不变) | offload 本身仍有价值（存储压缩） |
| offload preview | 300 chars | 2000 chars | 更有用的预览 |
| _optimize_context hot 截断 | 10K | 保留但 ref 先展开 | 展开后再截断 |
| Lazy expand 上限 | - | 15K per artifact | 单个 artifact 最大展开大小 |

---

## 4. 对现有场景的影响分析

### 4.1 Agent Read 文件 (20KB)

```
Before:  Read → offload → agent 手动 ReadArtifact → 21K tokens + 1 轮调用
After:   Read → offload → _optimize_context 展开 → 20K tokens, 0 轮额外调用
节省:    ~1K tokens + 1 轮 LLM 调用 (数秒延迟 + ~100 tokens overhead)
```

### 4.2 Agent grep 大量结果 (15KB)

```
Before:  Bash grep → vCPU 截断到 4K → 丢失中间内容
After:   Bash grep → 不截断 → offload → 展开(如果预算够) / 2K preview(如果不够)
改善:    不再丢失内容；预算紧时有 2K 预览替代 300 char
```

### 4.3 Specialist 返回大结果 (30KB)

```
Before:  SubmitResult → specialist_tools offload → orchestrator 调 ReadArtifact → 31K
After:   SubmitResult → specialist_tools offload → _optimize_context:
         - 如果预算够：展开（orchestrator 直接看到结果）
         - 如果预算紧：2K preview + ref（orchestrator 看 preview 就够了）
         - 如果需要传给下游 specialist：GoalDocument 自动展开（不受影响）
```

### 4.4 Agent ls 1000 个文件 (50KB)

```
Before:  Bash ls → vCPU 截断到 4K（还算合理）
After:   Bash ls → offload → _optimize_context 不展开(超过 15K 上限) → 2K preview
效果:    preview 比截断更有用（保留前 2000 chars ≈ 前 ~200 个文件名）
```

### 4.5 Context 紧张时 (接近 180K token limit)

```
Before:  offload 的 ref 是 300 chars，节省有效
After:   _optimize_context 检测到预算紧 → 不展开 → 保持 2K preview
效果:    比 300 char 多用 1.7K，但信息量大幅提升；
         如果连 2K 都不行，可以 fallback 到 300 char preview
```

---

## 5. 实现优先级

| Priority | 改动 | 文件 | 复杂度 | 收益 |
|---|---|---|---|---|
| **P0** | Lazy Expansion in _optimize_context | `mmu.py` +40~60 lines | 中 | **高**: 根治无效搬运 |
| **P1** | 增强 offload preview 到 2K | `mmu.py` 改 1 行 | 低 | **中**: 降级方案 |
| **P2** | 去掉 vCPU 常规截断 | `vcpu.py` 改 1 行 | 低 | **中**: 统一管理 |
| **P3** | offload ref 结构化元信息 | `mmu.py` 改 5 行 | 低 | **低**: 辅助 P0 |

### P0 详细改动范围

```
core/memory/mmu.py:
  - _optimize_context() 方法：新增 ~50 行 NimFS ref 检测+展开逻辑
  - 新增 import: re, NimFSManager
  - 新增常量: NIMFS_REF_PATTERN, INLINE_EXPAND_MAX_CHARS

测试:
  - 单元测试: mock NimFSManager，验证展开/不展开决策
  - 集成测试: 验证端到端 Read → offload → 展开 → agent 不再调 ReadArtifact
```

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|---|---|---|
| Lazy Expansion 读 NimFS I/O 开销 | 渲染延迟增加 | NimFS 是本地磁盘读，<1ms，可忽略 |
| 展开后总 token 超过 max_context_tokens | context overflow | 预算卡口逻辑：只展开时检查剩余预算 |
| 老的 offload ref 格式不含足够信息 | 无法解析 ref | 用正则兜底，解析失败保持原样 |
| 去掉 vCPU 截断后极端大输出 | OOM | 保留 100K safety threshold |

---

## 7. 未来演进

1. **Token-aware 工具**：让工具知道当前 context 剩余预算，从源头控制输出大小
2. **Streaming offload**：超大输出（>100K）直接 stream 写入 NimFS，不经过内存
3. **Smart preview by tool type**：针对 Read/Bash/grep 等工具生成结构化摘要（方案 A 的精简版），作为不展开时的增强 preview
4. **Agent 自省**：让 agent 在 system prompt 中知道 offload 内容会被自动展开，不需要手动调 ReadArtifact（减少白名单工具的使用频率）

---

## 8. 总结

**核心洞察**：offload 的问题不在于 offload 本身，而在于 offload 后 agent 被迫手动读回。解决方案是「存储层 offload，视图层展开」——Offload-Store, Inline-View。

**推荐方案**：
1. **P0**: `_optimize_context()` 中实现 Lazy Expansion — 自动展开 NimFS ref（根治问题）
2. **P1**: 增强 offload preview 到 2K chars（降级方案）
3. **P2**: 去掉 vCPU 的 4K 常规截断（统一管理）

**预期效果**：
- 消除 90%+ 的 NimFSReadArtifact 手动调用
- 每次无效搬运节省 ~1K tokens + 1 轮 LLM 调用（3-8 秒延迟）
- 不影响 context 紧张时的压缩效率
