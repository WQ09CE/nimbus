# Nimbus 无限上下文 (Infinite Context) 深度洞察报告

**作者**: Antigravity (Current Pair Programmer)  
**日期**: 2026-02-04  
**状态**: 待评审

---

## 1. 现状解剖：我们现在到底在怎么做？

经过对 `src/nimbus/core/memory/mmu.py` 和 `src/nimbus/agentos.py` 的深度代码审计，目前的“无限上下文”机制实际上是 **基于 Rolling Summary 的有损压缩流**，而非真正的“分层存储”。

### 1.1 核心流程 (The Reality)

当 Token 溢出时 (`CONTEXT_OVERFLOW`)，系统执行以下操作：

1.  **切分 (Slice)**: 
    *   计算保留窗口（例如最近 20%）。
    *   将消息历史切分为 `[Old Messages]` 和 `[Recent Messages]`。
    *   *Bug Fix*: 我们最近修复了切分逻辑，确保不会切断 `Tool Call` 和 `Tool Result` 的联系，避免 API 报错。

2.  **总结 (Summarize)**:
    *   调用 LLM 生成 `Summary`。
    *   *Bug Fix*: 我们引入了“智能预算”，根据 Pinned Budget 动态计算 Summary 长度，并支持 LLM 二次压缩，防止 Summary 无限膨胀。
    *   *Bug Fix*: 我们增强了 `MMU`，强制从 Pinned Context 提取原始 Goal 并置顶到 Summary 中 (`# 🎯 PRIMARY GOAL`)，以抵抗 Recency Bias。

3.  **重置 (Reset & Drop)**:
    *   内存中的 Context 被重置为 `[Pinned] + [Summary] + [Recent Messages]`。
    *   **关键点**：`[Old Messages]` 被 **直接丢弃**。虽然方法名叫 `archive_and_reset`，但代码中**并没有**将旧消息写入磁盘文件的逻辑。

### 1.2 架构本质

目前的架构是 **"Memory-Only Rolling Context"**。

*   **持久性**: 无。旧历史一旦被压缩，除了 Summary 里留下的痕迹，原始细节（代码片段、具体报错、中间推理）全部永久丢失。
*   **回溯能力**: 零。Agent 无法通过工具（如 `ReadArchive`）查阅过去，因为它根本不存在。

---

## 2. 核心缺陷：为何这是一个险境？

目前的机制就像是在走钢丝，全靠 **Summary 的质量** 和 **Pinned Goal 的强力引导** 来维持任务。

### 2.1 "任务漂移" 的根源
我们之前观察到的“任务漂移”（Agent 开始总结最近读的 Tool 代码，而不是最初要求的 VCPU 架构），其根本原因不仅是 Recency Bias，更是 **信息熵的不可逆丢失**。

*   当 AI 读完 `vcpu.py`，触发压缩。`vcpu.py` 的具体内容被压缩成几句话。
*   当 AI 读完 `agentos.py`，触发压缩。之前的 `vcpu` 摘要与新摘要合并，信息进一步稀释。
*   如果 Summary 遗漏了“我要总结 VCPU 架构”这个意图，或者遗漏了 `vcpu.py` 的关键设计细节，那么这些信息就 **永远消失了**。
*   AI 无法“回头看”，只能基于越来越模糊的 Summary 和越来越近期的历史（Tool 代码）来生成回答。

### 2.2 容错率极低
*   **Summary 质量即生命线**：一旦某次 Summary 生成得不好（例如太简略、重点跑偏），任务链就会断裂。
*   **无法纠错**：如果用户问“你刚才在第二步提到的那个变量叫什么？”，Agent 无法回答，因为它真的忘了。

---

## 3. 洞见与建议：迈向"真·无限"

如果要实现真正的 Long-Horizon Agent，我们需要从“有损压缩”进化为“分层存储”。

### 3.1 短期修补 (Current State is OK for now)
我们通过 **Pinned Goal 置顶** 和 **智能 Summary Prompt**，暂时缓解了任务漂移问题。对于“总结代码”这类线性任务，这已经足够好了（如 Trace Log 所示，Agent 成功完成了任务）。

### 3.2 长期演进 (Phase 3 Proposal)

建议在下一阶段恢复 **磁盘归档 (Disk Archival)** 机制：

#### A. 真实的 Archive
在 `archive_and_reset` 中，将 `messages_to_archive` 序列化并写入 `.nimbus/sessions/<id>/archive/part_N.md`。

#### B. 显式指针 (Explicit Pointers)
在 Context 中不仅保留 Summary，还要保留 **指针**：
```text
[System: Memory compacted.]
[Summary: ...]
[Archive Reference: 详细历史已归档至 part_001.md (包含 vcpu.py 读取记录)]
```

#### C. 主动回忆机制 (Active Recall)
教育 LLM（通过 System Prompt）：
> "如果你发现 Summary 中的信息不足以回答问题，或者你需要查阅之前的具体代码/报错，请使用 `ReadArchive` 工具读取历史档案。"

这将把 Nimbus 从一个“健忘但努力”的助手，升级为一个“拥有无限笔记本”的专家。

---

## 4. 结论

目前的 **Simplified MMU v2** 是一个精简、高效的纯内存实现。它通过严格的 Token 预算控制和强力的 Goal 锚定，成功实现了在极低 Context 下的持续运行。

但它不是“无限记忆”，而是“无限续命”。真正的无限记忆需要磁盘存储的回归。

**建议委员会评审重点：**
1.  目前的“纯内存流”是否满足当前阶段的需求？
2.  是否批准在下一阶段恢复磁盘归档机制？
