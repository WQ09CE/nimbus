# Nimbus 2026 多智能体编排升级设计（可执行方案）

> 目标：在**不改现有代码**前提下，给出面向 2026 最佳实践的升级蓝图，覆盖 WorkflowType 拓扑、Dispatch 契约化、Pipeline 硬验证、统一 Trace Schema、分阶段迁移落地。本文以当前代码为基线，输出可直接进入实施的设计文档与映射计划。

---

## 0. 基线解读（当前架构能力与缺口）

### 0.1 关键事实（来自现有代码）

| 模块 | 现状能力 | 证据文件 |
|---|---|---|
| 顶层集成 | `AgentOS` 已整合 Scheduler/VCPU/MMU/Gate/Session/Skill 等，具备多进程 spawn/wait 编排能力 | `src/nimbus/agentos.py`（文件头与 `AgentOS.__init__` 区域） |
| 调度内核 | `Scheduler` 是 DAG 任务调度器，具备 task state machine、依赖解析、并发执行、事件流 | `src/nimbus/core/scheduler.py`（`TaskState/TaskSpec/Task/DAG/Scheduler`） |
| 编排工具 | 已有 `Dispatch` 和 `Verify` 工具定义，Verify 支持 deterministic checks | `src/nimbus/orchestration/tools.py`（`DISPATCH_TOOL_DEF/VERIFY_TOOL_DEF/run_verify_checks`） |
| 专家化工具 | 已有 typed specialist：Explore/Implement/Design/Test，基于 `GoalDocument` 和 `AgentProfile` 运行 | `src/nimbus/orchestration/specialist_tools.py`（`SpecialistTool` 及子类） |
| 运行时 pipeline | 已有 response middleware pipeline（sanitize/split）机制 | `src/nimbus/core/runtime/pipeline.py`（`ResponsePipeline/ResponseMiddleware`） |
| Trace | 已有 step 级 trace，含 context/llm/actions/results/fault，写入 NimFS JSON + MD | `src/nimbus/core/runtime/tracer.py`（`ExecutionTrace/TraceManager`） |
| Session | JSONL 树形会话，支持恢复/分支/entry 类型扩展 | `src/nimbus/core/session.py`（`SessionEntry/SessionManager`） |

### 0.2 核心缺口（2026 编排视角）

1. **缺统一 Workflow 拓扑语义层**：DAG 能表达依赖，但缺面向编排策略的一级概念（single/hierarchical/pipeline/swarm）。
2. **Dispatch 缺“契约优先”模型**：当前参数是 `task/context/model`，缺 input/output schema、success criteria、retry policy 的标准字段。
3. **Pipeline 缺“硬验证节点”标准**：Verify 存在但仍偏工具调用，未形成 pipeline 中“不可跳过 gate”的统一步骤模型。
4. **Trace schema 分散**：已有 trace 结构偏 VCPU-step，不是跨 agent/workflow/toolcall 的统一 span/event 模型。
5. **迁移路径未产品化**：缺兼容现有结构的分阶段路线（快赢/中期/长期）与文件级落点。

---

## 1. 目标架构（2026 编排控制面）

### 1.1 设计原则

- **兼容优先**：在 `AgentOS + Scheduler + SpecialistTools` 之上增量演进，不推翻现有执行面。
- **契约优先**：所有跨 agent 任务交互先定义 Contract，再调度执行。
- **验证内建**：Verify 不是“可选工具”，而是 pipeline 的标准化 gate。
- **可观测优先**：统一 Trace Schema，支持跨层归因（workflow → task → toolcall）。
- **分层演进**：先文档+协议、再软集成、后强约束与治理平台化。

### 1.2 新增逻辑分层（不改代码前提下的设计目标）

```text
Orchestration Control Plane (新增抽象层)
├─ WorkflowType Resolver      # 拓扑选择 single/hierarchical/pipeline/swarm
├─ Dispatch Contract Engine   # 任务契约校验、重试与验收策略
├─ Verify Gate Standard       # Pipeline 的硬验证规范
├─ Unified Trace Adapter      # span/event/toolcall/agent 统一记录
└─ Migration Compatibility    # 与现有 AgentOS/Scheduler/Specialist 的映射

Execution Plane (现有)
├─ AgentOS
├─ Scheduler (DAG)
├─ Specialist Tools
├─ Runtime Pipeline
└─ TraceManager + SessionManager
```

---

## 2. WorkflowType 拓扑引入（single/hierarchical/pipeline/swarm）

### 2.1 拓扑定义

| WorkflowType | 场景 | 编排特征 | 与现有能力映射 |
|---|---|---|---|
| `single` | 单 agent 快速任务 | 单进程/低协调成本 | `AgentOS.spawn + wait` |
| `hierarchical` | 主管-专家分解 | 上层 planner + 下层 specialist | `Dispatch` / `specialist_tools` |
| `pipeline` | 阶段化产物流 | 阶段串联 + 必经 Verify Gate | `Scheduler DAG` + `Verify` |
| `swarm` | 并行探索/投票融合 | 多 agent 并发 + 聚合策略 | `Scheduler` 并发 + 聚合任务 |

### 2.2 数据结构草图（建议）

```python
# logical draft (for 2026 spec)
class WorkflowType(str, Enum):
    SINGLE = "single"
    HIERARCHICAL = "hierarchical"
    PIPELINE = "pipeline"
    SWARM = "swarm"

@dataclass
class WorkflowPlan:
    workflow_id: str
    type: WorkflowType
    goal: str
    stages: list["StageSpec"] = field(default_factory=list)
    global_retry_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 2.3 与现有文件映射位置

- 拓扑语义入口：`src/nimbus/agentos.py`（新增“workflow plan 解析/路由”职责，保持 spawn/scheduler 不变）。
- 拓扑执行承载：`src/nimbus/core/scheduler.py`（继续作为底层 DAG 执行器）。
- 拓扑与 specialist 对接：`src/nimbus/orchestration/specialist_tools.py`（按 type 选择角色组合）。

---

## 3. Dispatch 任务契约化（Contract-first）

### 3.1 新契约模型（建议字段）

```json
{
  "task_id": "T-2026-001",
  "title": "实现 Pipeline Verify Gate",
  "input_schema": {
    "type": "object",
    "required": ["target_files", "checks"],
    "properties": {
      "target_files": {"type": "array", "items": {"type": "string"}},
      "checks": {"type": "array", "items": {"type": "object"}}
    }
  },
  "output_schema": {
    "type": "object",
    "required": ["changed_files", "verification_report"],
    "properties": {
      "changed_files": {"type": "array", "items": {"type": "string"}},
      "verification_report": {"type": "string"}
    }
  },
  "success_criteria": [
    "所有 required checks 通过",
    "输出满足 output_schema"
  ],
  "retry_policy": {
    "max_attempts": 2,
    "backoff": "exponential",
    "retry_on": ["timeout", "schema_validation_failed", "verification_failed"]
  }
}
```

### 3.2 接口示例（面向编排器）

```python
Dispatch(
  contract={...},
  payload={...},
  model="gpt-5.3-codex"
)
```

### 3.3 与现有结构对齐

| 现有 | 升级后语义 | 文件映射 |
|---|---|---|
| `Dispatch(task, context, model)` | `Dispatch(contract, payload, model)`（向后兼容旧参数） | `src/nimbus/orchestration/tools.py` |
| Specialist `task/context/instructions` | Contract 可降级渲染为 GoalDocument | `src/nimbus/orchestration/specialist_tools.py` |
| Scheduler `TaskSpec(input,budget)` | `input` 承载 payload，budget 叠加 retry/timeout | `src/nimbus/core/scheduler.py` |

### 3.4 契约校验执行时序

1. 编排器生成/接收 Contract。
2. pre-dispatch：校验 `input_schema`。
3. 执行 specialist / task。
4. post-dispatch：校验 `output_schema`。
5. 校验 `success_criteria`（含 Verify 结果）。
6. 失败按 `retry_policy` 进行重试或失败上抛。

### 3.5 Contract Builder（降低接入成本，新增）

> 建议提供 Contract Builder，将“手写 JSON schema”升级为“类型定义自动生成 schema”，减少接入方心智负担与格式错误。

#### 3.5.1 推荐形态

1. **Pydantic 模型生成**：通过 `BaseModel.model_json_schema()` 自动生成 `input_schema/output_schema`。
2. **装饰器声明式定义**：在任务函数上声明 `success_criteria/retry_policy`，自动编译为完整 contract。
3. **向后兼容**：仍允许直接传原始 JSON schema；Builder 仅作为推荐入口。

#### 3.5.2 示例（草案）

```python
from pydantic import BaseModel

class ImplementInput(BaseModel):
    target_files: list[str]
    checks: list[dict]

class ImplementOutput(BaseModel):
    changed_files: list[str]
    verification_report: str

@contract_builder(
    task_id="T-2026-001",
    title="实现 Pipeline Verify Gate",
    success_criteria=["所有 required checks 通过", "输出满足 output schema"],
    retry_policy={"max_attempts": 2, "backoff": "exponential"},
)
def build_contract():
    return Contract.from_models(ImplementInput, ImplementOutput)
```

#### 3.5.3 接入收益

- 降低 schema 手写错误率，减少“字段漏填/类型不一致”。
- 统一 contract 产出格式，便于 Dispatch 与 Verify 自动化联动。
- 降低新 agent/新团队接入门槛，提升契约化推广速度。

---

## 4. Pipeline 硬验证节点（Verify Step 标准化）

### 4.1 标准阶段模板

```text
Plan -> Implement -> Verify(hard gate) -> Integrate -> Finalize
```

### 4.2 Verify Step 规范

| 项 | 要求 |
|---|---|
| 触发时机 | 每个关键产物阶段后必须执行 |
| 输入 | 标准 `checks[]`（复用 `tools.py` 既有结构） |
| 判定 | 任一 required check fail => 阶段失败 |
| 输出 | 结构化 report + failed checks + remediation hint |
| 重试 | 仅允许按 retry_policy 重试 implement/verify 子链路 |

#### 4.2.1 自愈反馈闭环（新增）

> 目标：将 Verify 失败信息从“日志”升级为“可执行反馈”，自动注入下一轮 Implement 上下文，形成 Plan/Implement/Verify 的闭环自愈。

**机制说明（增量，不改现有主干）：**
1. Verify 失败时，生成标准化 `remediation_hint`（失败检查项、根因线索、建议改动范围、禁回归约束）。
2. 编排器将 `remediation_hint` 写入当前任务的 retry metadata，并追加到下一轮 Implement 的 context（高优先级系统提示段）。
3. Implement 产物需显式回填 `applied_hints`（已采纳的 hint id 列表）与 `unresolved_risks`。
4. 下一轮 Verify 对 `applied_hints` 做一致性检查，避免“重复失败但无修正动作”。
5. 达到 `retry_policy.max_attempts` 后上抛，并保留完整闭环链路用于 trace 回放。

**伪代码（编排侧）：**

```python
def run_stage_with_verify(contract, payload):
    attempts = 0
    remediation_hints = []

    while attempts < contract.retry_policy.max_attempts:
        impl_context = payload.context + render_hints(remediation_hints)
        impl_output = run_implement(payload.with_context(impl_context))

        verify_report = run_verify_checks(contract.checks, impl_output)
        if verify_report.passed:
            return {
                "status": "ok",
                "impl_output": impl_output,
                "verify_report": verify_report,
            }

        hint = build_remediation_hint(verify_report, impl_output)
        remediation_hints.append(hint)
        attempts += 1

    raise VerificationFailed(
        message="verify hard gate failed after retries",
        remediation_hints=remediation_hints,
    )
```

**流程图（文本版）：**

```text
Implement -> Verify
             | pass ----------------------> Integrate
             | fail
             v
      build remediation_hint
             v
   inject to next Implement context
             v
          re-Implement (retry)
```

### 4.3 与现有代码映射

- 验证能力复用：`src/nimbus/orchestration/tools.py::run_verify_checks`
- Pipeline 编排承载：`src/nimbus/core/scheduler.py`（以 DAG task 建模 gate）
- Runtime middleware 保持中立：`src/nimbus/core/runtime/pipeline.py`（不承载业务验证逻辑）

### 4.4 Verify 节点任务草图

```python
TaskSpec(
  goal="Verify stage output",
  process_role="tester",
  input={
    "contract_id": "T-2026-001",
    "checks": [...],
    "required": True
  },
  budget={"timeout": 120}
)
```

---

## 5. Trace Schema 统一（span/event/toolcall/agent id）

### 5.1 统一事件模型（建议）

```json
{
  "trace_id": "tr_...",
  "span_id": "sp_...",
  "parent_span_id": "sp_parent",
  "workflow_id": "wf_...",
  "task_id": "T-2026-001",
  "agent_id": "agent_architect_01",
  "event_type": "toolcall",
  "name": "Dispatch",
  "timestamp": "2026-03-01T10:00:00Z",
  "status": "ok",
  "input": {},
  "output": {},
  "metrics": {"latency_ms": 3180, "tokens": 1200},
  "error": null,
  "tags": ["workflow:pipeline", "role:architect"]
}
```

### 5.2 事件类型标准

| event_type | 说明 |
|---|---|
| `span_start` / `span_end` | 生命周期边界 |
| `agent_step` | 单 agent 的 think-act-observe step |
| `toolcall` | 工具调用（含 Dispatch/Verify） |
| `verification` | Verify Gate 判定事件 |
| `retry` | 重试触发/结果 |
| `handoff` | agent 间任务交接 |
| `fault` | 失败事件 |

### 5.3 与现有 TraceManager 的衔接

- 当前 `ExecutionTrace` 可作为 `agent_step` 子集来源：`src/nimbus/core/runtime/tracer.py`。
- 通过“Trace Adapter”在写入 NimFS 前做 schema 归一：保留现有 JSON/MD artifact，新增统一字段（trace_id/span_id/agent_id/workflow_id）。
- Session 关联：`src/nimbus/core/session.py` 的 entry 可附 trace 引用（entry_id ↔ span_id）。

### 5.4 Trace 轻量化 + NimFS 引用化（新增）

> 目标：控制上下文体积与存储成本，避免在 trace 事件中内联大对象；默认轻量事件 + 按需展开。

#### 5.4.1 轻量化策略

1. `input/output/context` 采用“摘要优先”：保留结构化摘要、大小、hash、关键片段（preview），大字段不直接内联。
2. 单事件超阈值（如 >16KB）触发 offload，将完整内容写入 NimFS artifact。
3. Trace 主体仅保留引用与元数据，读取端按需 `ReadArtifact` 展开。

#### 5.4.2 Schema 增补字段（建议）

```json
{
  "context_preview": "...",
  "context_size": 84231,
  "context_hash": "sha256:...",
  "context_refs": [
    {
      "ref": "nimfs://artifact/abc123",
      "kind": "context_full",
      "encoding": "json",
      "size": 84231,
      "offloaded": true
    }
  ],
  "offload": {
    "enabled": true,
    "threshold_bytes": 16384,
    "policy": "inline-preview+nimfs-ref"
  }
}
```

#### 5.4.3 存储与回放约定

- 写入约定：trace writer 先写轻量事件，再异步/同步写入 NimFS artifact，最终回填 `context_refs`。
- 读取约定：默认只展示 preview；调试/审计场景按 `context_refs.ref` 拉取全量。
- 兼容约定：若无 `context_refs`，视为旧事件，沿用现有内联解析路径。

---

## 6. 模块职责（升级后）

| 模块 | 2026 职责定位 | 不做什么 |
|---|---|---|
| AgentOS | Workflow 入口、拓扑路由、生命周期管理 | 不承担底层 task 并发细节 |
| Scheduler | DAG 执行内核、状态机、重试执行器 | 不关心业务语义（contract含义） |
| orchestration/tools | Dispatch/Verify 契约接口与标准检查定义 | 不做复杂拓扑决策 |
| specialist_tools | 角色化执行与结果归集 | 不定义全局 workflow policy |
| runtime/pipeline | LLM 响应中间件（sanitize/split等） | 不承载工程验收逻辑 |
| runtime/tracer | 统一 trace 采集与输出 | 不做业务决策 |
| session | 会话与可回溯上下文 | 不做实时调度 |

---

## 7. 分阶段迁移计划（兼容现有结构）

### 7.1 Phase 0：快赢（1~2 周，低风险）

**目标**：先把“规范”落地，不动主干执行逻辑。

1. 文档化 `WorkflowType` 与 Dispatch Contract v1（规范先行）。
2. 在编排提示词/任务模板中强制包含：`success_criteria` + `verify checks`。
3. 统一 Trace 字段最小集：`trace_id/span_id/agent_id/task_id/event_type`（先附加，不替换）。
4. 对 pipeline 类任务制定“必经 Verify”操作规程。

**收益**：立刻提升可控性与可观测性。

### 7.2 Phase 1：中期（1~2 月，增量开发）

1. 为 `Dispatch` 增加 contract/payload 新参数并保留旧参数兼容。
2. 在 Scheduler task metadata 中加入 retry policy 映射（不改核心状态机语义）。
3. 建立 Verify Gate 标准 task 模板库（file/command/process/port checks 组合）。
4. Trace Adapter 落地：把现有 `ExecutionTrace` 自动映射到统一 schema。

**收益**：契约驱动开始成为默认路径。

### 7.3 Phase 2：长期（季度级）

1. Workflow Planner：按目标自动选择 single/hierarchical/pipeline/swarm。
2. 引入 swarm 聚合器（投票/置信度加权/冲突消解）。
3. 建立编排治理看板：成功率、重试率、verify fail root cause、agent 成本分布。
4. 将统一 trace 接入外部观测系统（OTel/数据仓库）。

**收益**：从“可用编排”升级到“可治理编排平台”。

---

## 8. 参考实现与借鉴点（增量引入）

基于 wukong 探索结果，建议在不改变现有执行内核前提下，引入以下 5 项“已被验证有效”的编排机制，并与 Nimbus 现有模块形成一一对应的落地点。

### 8.1 事件溯源 EventBus（JSONL）

**借鉴点**：以事件流作为系统事实来源，所有关键动作（dispatch/start/verify/fail/retry）都落为可追加 JSONL 事件，支持回放、审计与因果追踪。

**Nimbus 可落点**：
- 以 `runtime/tracer.py` 为核心，补齐统一 **Trace schema**（workflow/task/toolcall/span/event）。
- 与 `core/session.py` 的 JSONL entry 对齐，形成“会话事件 + 执行事件”双轨可追溯。
- 在不替换现有 trace 的情况下先做 adapter：旧 `ExecutionTrace` 自动映射到统一事件结构。

### 8.2 模板驱动编排（templates）

**借鉴点**：把编排经验固化为模板（角色分工、输入输出、验证节点、重试策略），减少临场拼装导致的不稳定性。

**Nimbus 可落点**：
- 以 **WorkflowType 模板**作为入口（single/hierarchical/pipeline/swarm）。
- 在 `orchestration/specialist_tools.py` 中将模板渲染到 `GoalDocument/AgentProfile`。
- 在 `orchestration/tools.py` 中统一 Dispatch 模板字段（包含 success_criteria / verify checks / retry policy）。

### 8.3 Agent 强约束（Do/Don't + Tool Allowlist + Output Contract）

**借鉴点**：通过明确行为边界与输出契约，将“能力”转化为“可控执行”，降低偏航与格式漂移。

**Nimbus 可落点**：
- 建立 **Contract Builder**：统一输入 schema、输出 schema、成功标准、失败处理。
- 在 specialist 执行提示中强制 Do/Don't 规范，并绑定工具白名单（Tool Allowlist）。
- 输出必须满足 Contract（结构、必填字段、可验证断言），失败则进入 retry 或 verify gate。

### 8.4 Anchor System（长期锚点）

**借鉴点**：沉淀稳定的长期锚点（规则、偏好、关键实体、项目事实），让多轮/跨会话编排保持一致性。

**Nimbus 可落点**：
- 纳入现有编排**治理体系**：把高价值约束（规范、决策、禁用策略）升级为可复用锚点。
- 在 workflow 启动阶段注入锚点上下文，减少 agent 重复探索成本。
- 锚点变更纳入审计链路（谁改、何时改、影响哪些 workflow）。

### 8.5 横切验证层（AOP / Manas 思路）

**借鉴点**：将验证从“工具调用”提升为横切层能力，在关键节点自动触发，避免被业务流程绕过。

**Nimbus 可落点**：
- 建立 **Verify Gate 审计器**：在 pipeline/handover/finalize 等节点执行标准检查。
- 复用 `run_verify_checks`，并在 Scheduler 层定义“不可跳过 gate”的任务模板。
- 验证结果直接写入 Trace schema，支持失败归因、重试策略评估与治理看板统计。

---

## 9. 现有文件映射清单（实施定位）

| 目标能力 | 主要落点文件 | 备注 |
|---|---|---|
| WorkflowType 拓扑入口 | `src/nimbus/agentos.py` | 新增路由抽象层（设计） |
| DAG 承载 pipeline/swarm | `src/nimbus/core/scheduler.py` | 保持执行内核稳定 |
| Dispatch 契约参数 | `src/nimbus/orchestration/tools.py` | 向后兼容 `task/context/model` |
| Specialist 契约渲染 | `src/nimbus/orchestration/specialist_tools.py` | Contract -> GoalDocument |
| Verify Gate 标准节点 | `src/nimbus/orchestration/tools.py` + `core/scheduler.py` | run_verify_checks 复用 |
| Trace 统一适配 | `src/nimbus/core/runtime/tracer.py` | ExecutionTrace -> UnifiedTraceEvent |
| Session 关联 trace | `src/nimbus/core/session.py` | entry 与 span 关联字段 |

---

## 10. 风险与缓解

| 风险 | 影响 | 缓解策略 |
|---|---|---|
| 契约字段引入导致调用复杂度上升 | 接入门槛变高 | 提供旧参数兼容 + 自动补全模板 |
| Verify 变 hard gate 导致失败率短期上升 | 发布节奏受影响 | 先灰度到 pipeline 场景，保留人工 override |
| Trace 字段扩展带来存储增长 | 成本上升 | 分层采样：关键 workflow 全量、常规采样 |
| swarm 并发增加成本与不确定性 | token/时延上升 | 仅在高价值任务启用，设置预算上限 |

---

## 11. 执行检查清单（落地即用）

### 快赢检查（本周可执行）
- [ ] 定义 `WorkflowType` 与使用准则（文档级）
- [ ] Dispatch 模板统一包含 `success_criteria`
- [ ] pipeline 任务模板强制 Verify checks
- [ ] Trace 输出附加 `trace_id/span_id/agent_id`

### 中期检查（版本里程碑）
- [ ] Dispatch contract/payload API 设计评审通过
- [ ] Verify Gate task 模板覆盖 80% 常见验证场景
- [ ] Trace Adapter 与 Session 关联可查询
- [ ] 重试策略与失败分类报表上线

### 长期检查（平台化）
- [ ] Workflow 自动选型准确率达标
- [ ] swarm 聚合策略可解释、可回放
- [ ] 编排治理看板支持周度运营

---

## 12. 结论

Nimbus 现有架构已经具备升级到 2026 多智能体编排的“执行基础”（AgentOS + Scheduler + Specialist + Verify + Trace + Session）。建议采用**契约优先 + 验证内建 + 统一可观测 + 分阶段迁移**路径：

- 短期先把规范变成默认操作（快赢）；
- 中期把契约与验证做成系统能力（增量开发）；
- 长期实现自动化拓扑选型与治理平台（体系化）。

在该路线下，可在保持当前系统稳定性的同时，逐步获得更高的成功率、可解释性与工程可控性。