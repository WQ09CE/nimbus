# Sub-agent Result Contract 实施方案 (方案 2：代码级改造清单)

## 1. 问题背景
目前 Sub-agent 的执行结果主要依赖于 `scratchpad.md` 文件。虽然 scratchpad 记录了详细的思维过程，但作为 Parent Agent 或 UI 的稳定消费接口存在以下问题：
- **非结构化**：Markdown 格式难以被代码可靠解析，Parent Agent 难以提取关键产出。
- **耦合严重**：UI 直接展示 Working Memory (scratchpad) 导致噪音过多，用户难以快速获取核心结论。
- **状态不透明**：当 Sub-agent 超时、崩溃或被中断时，缺乏统一的状态报告机制来告知已完成的部分和遗留的问题。
- **稳定性差**：Scratchpad 的格式随模型习惯变化，不可作为契约（Contract）使用。

## 2. 目标与非目标

> **当前状态**：本方案的最小可用实现（MVP）已完成。当前重点已从“是否做”转为“已完成能力的验收、文档同步与后续增强项梳理”。

### 目标
- [x] 定义一套结构化的 `SubAgentResult` 契约。
- [x] 改造核心组件（`spawn_agent` / `submit_result`）以支持该契约。
- [x] 提升 UI 层对子任务结果的展示可读性。
- [x] 确保在异常流程（超时/报错）下仍能产出结构化快照。
- [ ] 继续增强自动补全与进度汇报能力（作为 follow-up）。

### 非目标
- 彻底取代 Scratchpad（它仍作为 Agent 的 Working Memory 存在）。
- 改变现有的多 Agent 编排逻辑。

## 3. Sub-agent Result Contract 设计
所有 Sub-agent 在结束或汇报进度时，应遵循以下数据结构：

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `status` | `enum` | `success` \| `failed` \| `timeout` \| `aborted` |
| `summary` | `string` | 任务执行结果的高度概括（50-100字） |
| `key_findings` | `list[str]` | 核心发现或结论清单 |
| `artifacts` | `list[dict]` | 产出的重要文件、URL 或数据引用 |
| `files_touched` | `list[path]` | 修改或创建的文件列表 |
| `todos_completed` | `list[str]` | 已完成的任务点 |
| `todos_remaining` | `list[str]` | 未完成或遗留的任务点 |
| `errors` | `list[str]` | 遇到的错误或阻塞点（如有） |
| `scratchpad_path` | `path` | 关联的原始 scratchpad 文件路径，供追溯 |

## 4. 代码级改造清单

### 4.1 `submit_result` 工具改造
- [x] **Schema 强制化**：已更新 `submit_result` 的参数定义，可接收并校验 Contract 中的核心字段。
- [x] **结构化落盘**：已将结果落盘到 `.nimbus/sessions/<sub_session>/deliverable.json`，作为 Parent / UI 的稳定读取入口。
- [ ] **自动收集**：若模型未提供某些字段（如 `files_touched`），工具层仍可继续增强为从执行上下文（如 File Event Monitor）自动补充。

### 4.2 `spawn_agent` 核心逻辑改造
- [x] **优先读取 deliverable**：Sub-agent 正常完成时，`spawn_agent` 已优先读取 `deliverable.json` 返回结构化结果。
- [x] **`_collect_partial` 函数**：已从早期 raw scratchpad 拼接升级为结构化降级结果，能在 failure / timeout / abort 场景下提供 partial result。
- [ ] **同步/异步支持增强**：当前已支持完成态结果回收；若要支持更强的实时查询/增量汇报，可作为 follow-up 继续完善。

### 4.3 异常处理收尾 (Timeout/Abort/Failure)
- [x] **结构化收尾**：timeout / abort / failure 路径已能返回结构化结果或其降级版本。
- [ ] **Graceful Termination**：当触发超时或用户手动终止时，系统尚未实现“向模型发出最后 10s 总结指令”的收尾逻辑。
- [ ] **自动 files_touched 补全**：若模型无响应，由底层 Monitor 根据文件变更记录自动填充 `files_touched` 的能力仍属 follow-up。

### 4.4 UI Card 展示适配
- [x] **视图分离**：UI 已支持优先展示 `SubAgentResult`（Summary / Key Findings / Files / Todos / Errors / Scratchpad）。
- [x] **兼容接入**：已兼容 `tool.result`、`tool.result.deliverable`、`tool.result.ui_detail.deliverable` 等常见形态，并在缺字段时回退到 raw Markdown/JSON。
- [ ] **详情下钻增强**：若要提供显式“查看原始日志”按钮并按需加载完整 scratchpad / stderr，可继续作为 UI follow-up。

### 4.5 未来扩展：Progress Delta
- [ ] 引入 `report_progress` 工具，允许 Sub-agent 在不结束任务的情况下，推送符合 Contract 格式的增量更新。

## 5. 与现有 Scratchpad 的关系
- **Scratchpad (The Brain)**：依然是 Sub-agent 的“草稿本”和“工作记忆”，模型可以自由地在此书写原始想法。
- **Result Contract (The Report)**：是 Sub-agent 对外的“正式报告”。
- **映射关系**：底层框架负责引导模型将 Scratchpad 中的阶段性结论提炼到 Result Contract 中。

## 6. Phased Rollout
1. **Phase 1 (Infrastructure)**：定义 Schema，重构 `submit_result` 工具。**[x] 已完成**
2. **Phase 2 (Instrumentation)**：在 `agent-core` 中接入 deliverable 读取、`_collect_partial()` 结构化降级与 timeout/abort/failure 收尾。**[x] 最小可用实现已完成**
3. **Phase 3 (UI)**：上线新的任务卡片 UI，优先展示结构化结果，并兼容 raw fallback。**[x] 已完成兼容接入**
4. **Phase 4 (Follow-up Enhancements)**：自动文件收集、graceful termination summarization、progress delta / `report_progress`、更系统的多层嵌套验证。**[ ] 后续项**

## 7. 测试与验收清单
- [x] **正常链路**：Sub-agent 完成任务后，`submit_result` / `spawn_agent` 已能传回和消费结构化结果。
- [x] **异常链路基础覆盖**：timeout / abort / failure 已能生成结构化结果或其降级版本。
- [x] **UI 一致性**：UI 已支持基于结构化结果进行卡片展示，并在缺字段时回退到 raw Markdown/JSON。
- [ ] **超时链路增强验收**：人为设置极短超时，进一步验证 `status: timeout` 与 `files_touched` 自动补全质量。
- [ ] **多层嵌套**：A 唤起 B，B 唤起 C，验证 C 的结构化结果能被 A 正确解析并引用。
- [ ] **Progress Delta**：若后续引入 `report_progress`，需补增量进度场景的测试。
