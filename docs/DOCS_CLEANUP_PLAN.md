# 📋 Nimbus 文档清洗计划

> **日期**: 2026-02-27  
> **作者**: Architect Agent  
> **范围**: 110 个 .md 文件全量分类  
> **目标**: 将文档从 110 个精简到 ~30 个活跃文档 + 结构化归档

---

## 执行摘要

| 分类 | 数量 | 占比 | 动作 |
|:-----|:----:|:----:|:-----|
| 🟢 保留（核心/最新） | 22 | 20% | 保持原位，确认为权威文档 |
| 🔵 需更新 | 8 | 7% | 内容有价值但过时，安排刷新 |
| 🟡 归档 | 32 | 29% | 移至 `docs/archive/` |
| 🔴 删除 | 48 | 44% | 直接删除，无保留价值 |

**清洗后目录结构**:
```
docs/
├── DOCS_CLEANUP_PLAN.md        # 本文档（清洗后可删）
├── ARCHITECTURE_REVIEW_2026_02.md  # 架构复盘
├── architecture/               # 架构参考（~5 篇）
├── design/                     # 活跃设计文档（~10 篇）
├── adr/                        # 架构决策记录
├── research/                   # 研究文档
└── archive/                    # 归档区（只读，按日期组织）
    ├── 2026-01_opennotebook/   # OpenNotebook 时期
    ├── 2026-01_agent-os-v1/    # AgentOS v1 草案
    ├── 2026-02_superseded/     # 被取代的设计
    └── 2026-02_reviews/        # 自动生成评审报告
```

---

## 1. 🟢 保留 — 核心/最新文档 (22 篇)

这些文档反映当前实际架构，是新贡献者和日常开发的权威参考。

### docs/ 根目录 (12 篇)

| 文件 | 最后更新 | 保留理由 |
|:-----|:--------:|:---------|
| `nimbus-architecture-overview.md` | 2/20 | **首要架构文档**，Explorer 自动生成的全景报告 |
| `nimfs-design-v2.md` | 2/20 | NimFS 技术规格书 v2，当前实现的设计基准 |
| `nimfs-as-agent-ipc.md` | 2/20 | NimFS 作为 Agent IPC 的预研，仍在实施中 |
| `nimfs-implementation-plan.md` | 2/20 | NimFS 落地计划，配合 v2 spec |
| `vcpu-internals.md` | 2/15 | vCPU 技术内幕，4195 行代码的权威说明 |
| `project-status-2026-02.md` | 2/15 | 项目现状快照（472 tests, 29500 行） |
| `deployment-public-access.md` | 2/15 | 部署指南，实操文档 |
| `harbor-integration-guide.md` | 2/15 | Harbor 评测集成，实操文档 |
| `copilotkit-retrospective.md` | 2/25 | CopilotKit POC 回顾，记录了架构决策（弃用 CopilotKit） |
| `frontend-rendering-logic.md` | 2/24 | Web UI 渲染逻辑说明 |
| `specialist-display-redesign.md` | 2/24 | Specialist 展示重设计 |
| `infinite-context-insight.md` | 2/4 | 上下文管理深度分析，仍有参考价值 |

### docs/design/ (8 篇)

| 文件 | 最后更新 | 保留理由 |
|:-----|:--------:|:---------|
| `multi-agent-orchestration.md` | 2/19 | **多 Agent 编排 v2**，当前实现基准 |
| `universal-worker-specialist.md` | 2/23 | Worker Specialist 提案，活跃设计 |
| `nimfs-offload-optimization.md` | 2/22 | NimFS Offload 优化，已实施 |
| `nimfs-search-enhancement.md` | 2/22 | NimFS 搜索增强，已实施 |
| `edit-tool-failure-improvement.md` | 2/26 | Edit 工具容错改进，最新 |
| `edit-tool-recovery-redesign.md` | 2/26 | Edit 恢复机制重设计，最新 |
| `model-system-cleanup.md` | 2/26 | 模型系统清理，最新 |
| `web-ui-architecture.md` | 2/26 | Web UI 架构说明，最新 |
| `orchestrator-conversation-nimfs.md` | 2/26 | Orchestrator 对话 NimFS，最新 |

### docs/architecture/ (1 篇)

| 文件 | 最后更新 | 保留理由 |
|:-----|:--------:|:---------|
| `backend-overview.md` | 2/26 | 后端三通道流式架构 + ParallelDispatch 说明 |

### docs/adr/ (1 篇)

| 文件 | 最后更新 | 保留理由 |
|:-----|:--------:|:---------|
| `007-doom-loop-detection.md` | 1/31 | ADR 格式，架构决策永久保留 |

### docs/research/ (1 篇)

| 文件 | 最后更新 | 保留理由 |
|:-----|:--------:|:---------|
| `vcpu_loop_analysis.md` | 2/20 | vCPU 循环分析研究 |

---

## 2. 🔵 需更新 — 有价值但内容过时 (8 篇)

这些文档概念仍有效，但引用的代码路径、API 或架构层次已变化，需要刷新。

| 文件 | 更新优先级 | 过时内容 | 建议动作 |
|:-----|:----------:|:---------|:---------|
| `docs/memory-upgrade-plan-v2.md` | **P0** | MMU 已大幅改造（NimFS 集成、offload），v2 方案部分已实施部分已废弃 | 对照当前 `mmu.py` 标注已完成/已废弃/仍计划项 |
| `docs/agent-os-architecture.md` | **P1** | 1/26 的三层架构图已部分落地，但组件名和路径已变 | 合并进 `nimbus-architecture-overview.md` 或单独更新 |
| `docs/agent-os-von-neumann-architecture.md` | **P1** | 冯·诺依曼隐喻核心思想不变，但 Gate→KernelGate, Scheduler 等细节已变 | 与上文合并或更新 |
| `docs/agent-os-process-mechanism.md` | **P1** | 进程机制已通过 Specialist 系统落地，spawn/wait API 已稳定 | 更新代码引用 |
| `docs/design/vibe-coding-ide-analysis.md` | **P2** | 2/1 的竞品分析，数据还算新 | 标注分析日期，保持原样或追加更新 |
| `docs/architecture/vcpu_refactoring_plan.md` | **P2** | 2/20 重构计划，部分已完成 | 标注完成状态 |
| `docs/architecture/vcpu_refactoring_review.md` | **P2** | 配套评审 | 同上 |
| `docs/design/tools-skills-system.md` | **P2** | Skills 系统描述可能与当前实现有差异 | 对照 `src/nimbus/skills/` 验证 |

---

## 3. 🟡 归档 — 移至 `docs/archive/` (32 篇)

有历史参考价值（记录演进轨迹、已弃用方案的决策原因），但不应混在活跃文档中误导读者。

### 3.1 OpenNotebook 时期 → `docs/archive/2026-01_opennotebook/`

| 文件 | 归档理由 |
|:-----|:---------|
| `docs/architecture.md` | 旧名 OpenNotebook，NotebookAgent 架构已完全被 AgentOS 取代 |
| `docs/api-reference.md` | 引用 `NotebookAgent` API，已不存在 |
| `docs/getting-started.md` | 旧 import 路径 `from nimbus import NotebookAgent` |
| `docs/advanced-usage.md` | 旧 DAG API，`notebook.create_plan()` 已废弃 |

### 3.2 AgentOS v1 草案 → `docs/archive/2026-01_agent-os-v1/`

| 文件 | 归档理由 |
|:-----|:---------|
| `docs/agent-framework-design-v0.2.md` | 早期草稿，被多份后续文档取代 |
| `docs/context-stack-design.md` | 1/25 的上下文栈设计，已被 MMU 实现取代 |
| `docs/code-agent-research.md` | 1/24 竞品调研，结论已融入设计 |
| `docs/code-agent-capability-tests.md` | 1/24 能力测试，一次性产出 |
| `docs/multi-agent-benchmark-report.md` | 1/25 benchmark 快照 |
| `docs/architecture/NIMBUS_V2_MVP_DRAFT.md` | 1/31 MVP 草案，已被实际实现超越 |
| `docs/architecture/VCPU_ARCHITECTURE_ANALYSIS.md` | 1/31 分析报告，已被 `vcpu-internals.md` 取代 |

### 3.3 被取代的设计 → `docs/archive/2026-02_superseded/`

| 文件 | 归档理由 |
|:-----|:---------|
| `docs/nimfs-design.md` | 被 `nimfs-design-v2.md` 明确取代 |
| `docs/multi-agent-integration-design.md` | v1 被 `design/multi-agent-orchestration.md` v2 取代 |
| `docs/design/multi-agent-architecture.md` | 同上，v1 已标注 superseded |
| `docs/planner-router-design.md` | Planner 路由方案已被 Specialist 系统取代 |
| `docs/subagent-dag-design.md` | DAG 子 Agent 设计已被简化的 Specialist 编排取代 |
| `docs/proposal-dual-agent-orchestration.md` | 双 Agent 提案，已被多 Specialist 方案取代 |
| `docs/dag-conditional-design.md` | DAG 条件分支设计，未实施 |
| `docs/session-management-roadmap.md` | Session 管理路线图，部分已实施 |
| `docs/memory-comparison-report.md` | 初稿，已被 `memory-upgrade-plan-v2.md` 吸收 |
| `docs/design/error-handling-refactor-notes.md` | 已修 bug 的记录，归档 |

### 3.4 有参考价值的历史调研 → `docs/archive/2026-02_superseded/`

| 文件 | 归档理由 |
|:-----|:---------|
| `docs/opencode-subagent-research.md` | 竞品调研，一次性参考 |
| `docs/openwork-integration-design.md` | OpenWork 集成方案，未执行 |
| `docs/openwork-opencode-analysis.md` | 配套分析 |
| `docs/toad-integration-design.md` | Toad 集成方案，未执行 |
| `docs/acp-web-client-design.md` | ACP Web Client 设计，未执行 |
| `docs/ai-sdk-v6-stream-protocol.md` | AI SDK v6 协议分析，参考性质 |
| `docs/tool-failover-design.md` | 工具故障转移设计 |
| `docs/skills-development.md` | Skills 开发指南，待与实际对齐后可能升级 |

### 3.5 设计目录中的过时文档 → `docs/archive/2026-02_superseded/`

| 文件 | 归档理由 |
|:-----|:---------|
| `docs/design/agentic-loop-*.md` (3 篇) | Agentic Loop 三件套，概念已融入 vCPU |
| `docs/design/nimbus_context_sidecar_architecture.md` | Sidecar 架构，未实施 |
| `docs/design/tui-dashboard-design.md` | TUI Dashboard，未实施 |
| `docs/design/v2-integration.md` | v2 集成方案，已完成 |

---

## 4. 🔴 删除 — 无保留价值 (48 篇)

### 4.1 自动生成的评审报告 (29 篇) — `docs/reviews/` 全部删除

**理由**: ReviewCommittee 自动生成的评审报告是一次性 CI 产物。评审意见已被吸收到后续设计文档中（如 `nimfs-design-v2.md` 明确引用了 ReviewCommittee 的 C-1/C-2/C-3 问题）。保留这些文件只会造成噪音。

```
docs/reviews/
├── 20260210_*.md    # 全部删除
├── 20260213_*.md    # 全部删除
├── 20260214_*.md    # 全部删除
├── 20260215_*.md    # 全部删除
├── 20260216_*.md    # 全部删除
├── 20260219_*.md    # 全部删除
├── 20260220_*.md    # 全部删除
├── 20260222_*.md    # 全部删除
├── 20260224_*.md    # 全部删除
└── 20260225_*.md    # 全部删除
```

> **替代方案**: 如果希望保留评审历史，可 `git log` 追溯。这些文件全部在 git 历史中。

### 4.2 根目录中的低价值文档 (11 篇)

| 文件 | 删除理由 |
|:-----|:---------|
| `docs/TODO.md` | 应使用 GitHub Issues 或 NimFS memory 管理 TODO |
| `docs/troubleshooting-guide.md` | 内容空洞或严重过时 |
| `docs/web-ui-streaming-performance.md` | 一次性性能分析，数据已过时 |

### 4.3 设计目录中的低价值文档 (8 篇)

| 文件 | 删除理由 |
|:-----|:---------|
| `docs/design/ascii-rendering-review.md` | 一次性代码审查笔记 |
| `docs/design/mmu-review-request.md` | Review 请求（非设计文档） |
| `docs/design/vcpu-review-request.md` | Review 请求（非设计文档） |
| `docs/design/error-handler-design.md` | 已修 bug 的临时设计，代码已变 |
| `docs/design/chat-file-upload.md` | 功能调研笔记 |
| `docs/design/file-viewer-proposal.md` | 小功能提案，已过时 |
| `docs/design/image-support-investigation.md` | 调研笔记 |
| `docs/design/mmu-image-token-optimization.md` | MMU 图片优化调研 |
| `docs/design/tools-category-proposal.md` | 工具分类提案，未采纳 |
| `docs/design/multi-model-architecture.md` | 已被 `model-system-cleanup.md` 取代 |

---

## 5. 执行计划

### Phase 1: 立即执行（~30 分钟）

```bash
# 1. 创建归档目录
mkdir -p docs/archive/{2026-01_opennotebook,2026-01_agent-os-v1,2026-02_superseded,2026-02_reviews}

# 2. 删除 reviews（29 篇）
rm -rf docs/reviews/

# 3. 移动归档文件（示例）
mv docs/architecture.md docs/archive/2026-01_opennotebook/
mv docs/api-reference.md docs/archive/2026-01_opennotebook/
mv docs/getting-started.md docs/archive/2026-01_opennotebook/
mv docs/advanced-usage.md docs/archive/2026-01_opennotebook/

mv docs/agent-framework-design-v0.2.md docs/archive/2026-01_agent-os-v1/
mv docs/context-stack-design.md docs/archive/2026-01_agent-os-v1/
mv docs/code-agent-research.md docs/archive/2026-01_agent-os-v1/
mv docs/code-agent-capability-tests.md docs/archive/2026-01_agent-os-v1/
mv docs/multi-agent-benchmark-report.md docs/archive/2026-01_agent-os-v1/
mv docs/architecture/NIMBUS_V2_MVP_DRAFT.md docs/archive/2026-01_agent-os-v1/
mv docs/architecture/VCPU_ARCHITECTURE_ANALYSIS.md docs/archive/2026-01_agent-os-v1/

mv docs/nimfs-design.md docs/archive/2026-02_superseded/
mv docs/multi-agent-integration-design.md docs/archive/2026-02_superseded/
mv docs/design/multi-agent-architecture.md docs/archive/2026-02_superseded/
mv docs/planner-router-design.md docs/archive/2026-02_superseded/
mv docs/subagent-dag-design.md docs/archive/2026-02_superseded/
mv docs/proposal-dual-agent-orchestration.md docs/archive/2026-02_superseded/
mv docs/dag-conditional-design.md docs/archive/2026-02_superseded/
# ... 其余归档文件同理

# 4. 删除低价值文件
rm docs/TODO.md docs/troubleshooting-guide.md docs/web-ui-streaming-performance.md
rm docs/design/ascii-rendering-review.md docs/design/mmu-review-request.md
rm docs/design/vcpu-review-request.md docs/design/error-handler-design.md
# ... 其余删除文件同理

# 5. 提交
git add -A && git commit -m "docs: cleanup 110→30 active docs, archive 32, delete 48"
```

### Phase 2: 文档更新（~2-4 小时）

按优先级更新 🔵 标记的 8 篇文档，P0 优先。

### Phase 3: 建立文档治理规则

1. **新文档必须明确 Status**: `Draft | Active | Superseded | Archived`
2. **设计文档用 ADR 编号**: 扩展 `docs/adr/` 目录
3. **自动生成的评审报告**: 改为输出到 `docs/reviews/` 但加入 `.gitignore`，或输出到 NimFS
4. **季度文档审查**: 每季度执行一次类似本文档的清洗

---

## 附录：完整文件清单

<details>
<summary>展开查看全部 110 个文件的分类</summary>

| # | 文件路径 | 分类 | 动作 |
|:-:|:---------|:----:|:-----|
| 1 | `docs/nimbus-architecture-overview.md` | 🟢 | 保留 |
| 2 | `docs/nimfs-design-v2.md` | 🟢 | 保留 |
| 3 | `docs/nimfs-as-agent-ipc.md` | 🟢 | 保留 |
| 4 | `docs/nimfs-implementation-plan.md` | 🟢 | 保留 |
| 5 | `docs/vcpu-internals.md` | 🟢 | 保留 |
| 6 | `docs/project-status-2026-02.md` | 🟢 | 保留 |
| 7 | `docs/deployment-public-access.md` | 🟢 | 保留 |
| 8 | `docs/harbor-integration-guide.md` | 🟢 | 保留 |
| 9 | `docs/copilotkit-retrospective.md` | 🟢 | 保留 |
| 10 | `docs/frontend-rendering-logic.md` | 🟢 | 保留 |
| 11 | `docs/specialist-display-redesign.md` | 🟢 | 保留 |
| 12 | `docs/infinite-context-insight.md` | 🟢 | 保留 |
| 13 | `docs/memory-upgrade-plan-v2.md` | 🔵 | 更新 P0 |
| 14 | `docs/agent-os-architecture.md` | 🔵 | 更新 P1 |
| 15 | `docs/agent-os-von-neumann-architecture.md` | 🔵 | 更新 P1 |
| 16 | `docs/agent-os-process-mechanism.md` | 🔵 | 更新 P1 |
| 17 | `docs/architecture.md` | 🟡 | 归档 → opennotebook/ |
| 18 | `docs/api-reference.md` | 🟡 | 归档 → opennotebook/ |
| 19 | `docs/getting-started.md` | 🟡 | 归档 → opennotebook/ |
| 20 | `docs/advanced-usage.md` | 🟡 | 归档 → opennotebook/ |
| 21 | `docs/agent-framework-design-v0.2.md` | 🟡 | 归档 → agent-os-v1/ |
| 22 | `docs/context-stack-design.md` | 🟡 | 归档 → agent-os-v1/ |
| 23 | `docs/code-agent-research.md` | 🟡 | 归档 → agent-os-v1/ |
| 24 | `docs/code-agent-capability-tests.md` | 🟡 | 归档 → agent-os-v1/ |
| 25 | `docs/multi-agent-benchmark-report.md` | 🟡 | 归档 → agent-os-v1/ |
| 26 | `docs/nimfs-design.md` | 🟡 | 归档 → superseded/ |
| 27 | `docs/multi-agent-integration-design.md` | 🟡 | 归档 → superseded/ |
| 28 | `docs/planner-router-design.md` | 🟡 | 归档 → superseded/ |
| 29 | `docs/subagent-dag-design.md` | 🟡 | 归档 → superseded/ |
| 30 | `docs/proposal-dual-agent-orchestration.md` | 🟡 | 归档 → superseded/ |
| 31 | `docs/dag-conditional-design.md` | 🟡 | 归档 → superseded/ |
| 32 | `docs/session-management-roadmap.md` | 🟡 | 归档 → superseded/ |
| 33 | `docs/memory-comparison-report.md` | 🟡 | 归档 → superseded/ |
| 34 | `docs/opencode-subagent-research.md` | 🟡 | 归档 → superseded/ |
| 35 | `docs/openwork-integration-design.md` | 🟡 | 归档 → superseded/ |
| 36 | `docs/openwork-opencode-analysis.md` | 🟡 | 归档 → superseded/ |
| 37 | `docs/toad-integration-design.md` | 🟡 | 归档 → superseded/ |
| 38 | `docs/acp-web-client-design.md` | 🟡 | 归档 → superseded/ |
| 39 | `docs/ai-sdk-v6-stream-protocol.md` | 🟡 | 归档 → superseded/ |
| 40 | `docs/tool-failover-design.md` | 🟡 | 归档 → superseded/ |
| 41 | `docs/skills-development.md` | 🟡 | 归档 → superseded/ |
| 42-44 | `docs/design/agentic-loop-*.md` (×3) | 🟡 | 归档 → superseded/ |
| 45 | `docs/design/multi-agent-architecture.md` | 🟡 | 归档 → superseded/ |
| 46 | `docs/design/error-handling-refactor-notes.md` | 🟡 | 归档 → superseded/ |
| 47 | `docs/design/nimbus_context_sidecar_architecture.md` | 🟡 | 归档 → superseded/ |
| 48 | `docs/design/tui-dashboard-design.md` | 🟡 | 归档 → superseded/ |
| 49 | `docs/design/v2-integration.md` | 🟡 | 归档 → superseded/ |
| 50 | `docs/architecture/NIMBUS_V2_MVP_DRAFT.md` | 🟡 | 归档 → agent-os-v1/ |
| 51 | `docs/architecture/VCPU_ARCHITECTURE_ANALYSIS.md` | 🟡 | 归档 → agent-os-v1/ |
| 52-53 | `docs/architecture/vcpu_refactoring_*.md` (×2) | 🔵 | 更新 P2 |
| 54 | `docs/design/vibe-coding-ide-analysis.md` | 🔵 | 更新 P2 |
| 55 | `docs/design/tools-skills-system.md` | 🔵 | 更新 P2 |
| 56 | `docs/adr/007-doom-loop-detection.md` | 🟢 | 保留 |
| 57 | `docs/research/vcpu_loop_analysis.md` | 🟢 | 保留 |
| 58-60 | `docs/design/{multi-agent-orchestration,universal-worker-specialist,nimfs-offload-optimization}.md` | 🟢 | 保留 |
| 61-63 | `docs/design/{nimfs-search-enhancement,edit-tool-*}.md` | 🟢 | 保留 |
| 64-66 | `docs/design/{model-system-cleanup,web-ui-architecture,orchestrator-conversation-nimfs}.md` | 🟢 | 保留 |
| 67 | `docs/architecture/backend-overview.md` | 🟢 | 保留 |
| 68 | `docs/TODO.md` | 🔴 | 删除 |
| 69 | `docs/troubleshooting-guide.md` | 🔴 | 删除 |
| 70 | `docs/web-ui-streaming-performance.md` | 🔴 | 删除 |
| 71-79 | `docs/design/{ascii-rendering-review,mmu-review-request,vcpu-review-request,error-handler-design,chat-file-upload,file-viewer-proposal,image-support-investigation,mmu-image-token-optimization,tools-category-proposal,multi-model-architecture}.md` | 🔴 | 删除 |
| 80-108 | `docs/reviews/*.md` (×29) | 🔴 | 删除 |
| 109-110 | 剩余未列出文件 | 🔴/🟡 | 按同类原则处理 |

</details>
