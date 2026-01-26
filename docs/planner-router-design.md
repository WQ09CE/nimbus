# Planner Router Design (ADR-010)

> 解决 LLM Prompt 过长导致模型"失焦"的问题

## 状态

**Proposed** - 2026-01-25

## 问题背景

### 现状分析

当前 Nimbus 的规划流程：

```
User Goal
    |
    v
+-------------------+
| try_rule_match()  |---- 快速路径 (28+ 规则)
+-------------------+
    | (miss)
    v
+-------------------+
| ContextAnalyzer   |---- 检测上下文依赖
+-------------------+
    |
    v
+-------------------+
| RulePlanner       |---- 正则匹配
+-------------------+
    |
    v
+-------------------+
| LLMEnhancer       |---- 5429+ 字符的 prompt!
+-------------------+
    |
    v
  TaskDAG
```

### 问题描述

1. **Prompt 过长**: `LLM_PLANNING_PROMPT` 包含 5429+ 字符
   - 12 个示例
   - 6 个工具定义
   - 大量规则说明
   - 角色澄清、代词转换规则等

2. **模型失焦**: 某些模型（如 Gemini）在处理长 prompt 时：
   - 只生成部分任务（如只有 Read，遗漏 Edit）
   - 忽视示例中的多步骤模式
   - 对复杂的 old_string/new_string 参数生成错误

3. **职责耦合**: 单个 LLM 调用承担太多责任
   - 判断任务复杂度
   - 选择执行模式 (direct/dag)
   - 选择工具/技能
   - 填充参数
   - 处理依赖关系

### 证据

- `src/nimbus/core/planner/llm_enhancer.py:29-197` - LLM_PLANNING_PROMPT 定义
- `src/nimbus/core/planner/rule_planner.py:259-370` - 代码编辑任务已委托给 Subagent
- `src/nimbus/tools/subagent.py:51-56` - Subagent 类型和权限定义

## 设计方案

### 新架构概览

```
User Goal
    |
    v
+-------------------+
| try_rule_match()  |---- 快速路径 (保留)
+-------------------+
    | (miss)
    v
+-------------------+
|   TaskRouter      |---- 新增! 轻量级路由 (<500 字符 prompt)
+-------------------+
    |
    +---------+---------+
    |         |         |
    v         v         v
 SIMPLE    MODERATE   COMPLEX
    |         |         |
    v         v         v
+-------+ +--------+ +----------+
| Direct| |ToolDAG | | Subagent |
| Reply | |Planner | | Delegate |
+-------+ +--------+ +----------+
    |         |         |
    v         v         v
  TaskDAG  TaskDAG   TaskDAG
    |         |         |
    +----+----+---------+
         |
         v
     Executor
```

### 核心组件

#### 1. TaskRouter (新增)

**职责**: 用极短的 prompt 判断任务复杂度

**复杂度分类**:

| 级别 | 描述 | 典型场景 | 处理方式 |
|------|------|----------|----------|
| SIMPLE | 可直接回答 | 问候、感谢、基于上下文的问答 | Direct Reply |
| MODERATE | 需要 1-3 个工具 | 读文件、搜索、列出目录 | Tool DAG Planner |
| COMPLEX | 需要多步迭代或专业能力 | 代码编辑、重构、测试生成 | Subagent Delegate |

**路由器 Prompt** (< 400 字符):

```
判断任务复杂度。只输出 JSON。

规则:
- SIMPLE: 问候/感谢/基于对话能回答的问题
- MODERATE: 需要1-3个工具(读/搜/列)的简单任务
- COMPLEX: 代码编辑/重构/多文件修改/需要迭代

示例:
"你好" -> {"level": "SIMPLE"}
"读取 main.py" -> {"level": "MODERATE", "tools": ["Read"]}
"给 test.py 添加错误处理" -> {"level": "COMPLEX", "type": "coder"}

任务: {goal}
JSON:
```

#### 2. DirectReplyHandler

**职责**: 处理 SIMPLE 级别任务

**实现**: 复用现有 RulePlanner 的 direct 模式，或使用简化的 Synthesize 任务

#### 3. ToolDAGPlanner (精简版 LLMEnhancer)

**职责**: 处理 MODERATE 级别任务

**Prompt 优化策略**:
- 只包含 3 个核心工具 (Read/Glob/Grep)
- 删除 Edit/Write/Bash 相关示例
- 简化规则说明

**精简后 Prompt** (< 1500 字符):

```
生成工具调用 DAG。只输出 JSON。

工具:
- Read(file_path): 读文件
- Glob(pattern, path?): 查找文件
- Grep(pattern, path?, type?): 搜索代码

规则:
1. 可并行的任务 depends_on=[]
2. 需要前置结果的任务填写依赖

示例:
"读取 main.py 并搜索 error" ->
{"tasks": [
  {"id": "t1", "skill": "Read", "params": {"file_path": "main.py"}},
  {"id": "t2", "skill": "Grep", "params": {"pattern": "error"}}
]}

上下文: {context}
任务: {goal}
JSON:
```

#### 4. SubagentDelegator

**职责**: 处理 COMPLEX 级别任务

**实现**: 直接生成 Subagent 工具调用 DAG

**Subagent 类型映射**:

| 任务特征 | Subagent Type | 权限 |
|----------|---------------|------|
| 代码编辑/添加功能 | coder | Read, Write, Edit, Bash, Glob, Grep |
| 代码探索/分析 | explorer | Read, Glob, Grep |
| 技术调研 | researcher | Read, Glob, Grep, WebSearch, WebFetch |
| 代码审查 | reviewer | Read, Glob, Grep |

### 数据流

```
┌──────────────────────────────────────────────────────────────────┐
│                        User Goal                                  │
└──────────────────────────────────────────────────────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────────┐
│  Step 1: try_rule_match()                                        │
│  - 28+ 预定义正则规则                                              │
│  - 匹配成功 -> 直接返回 DAG                                        │
└──────────────────────────────────────────────────────────────────┘
                               │ (miss)
                               v
┌──────────────────────────────────────────────────────────────────┐
│  Step 2: TaskRouter (NEW)                                        │
│  - Input: goal (仅目标文本，不含完整对话)                           │
│  - Prompt: ~400 字符                                              │
│  - Output: {level: SIMPLE|MODERATE|COMPLEX, tools?: [], type?: } │
│  - Latency: ~200ms                                                │
└──────────────────────────────────────────────────────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
           v                   v                   v
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │   SIMPLE    │    │  MODERATE   │    │   COMPLEX   │
    │             │    │             │    │             │
    │ DirectReply │    │ ToolDAG     │    │  Subagent   │
    │ Handler     │    │ Planner     │    │  Delegator  │
    │             │    │             │    │             │
    │ ~0ms        │    │ ~300ms      │    │ ~100ms      │
    └─────────────┘    └─────────────┘    └─────────────┘
           │                   │                   │
           v                   v                   v
    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
    │  TaskDAG    │    │  TaskDAG    │    │  TaskDAG    │
    │ (synthesize)│    │ (Read/Glob/ │    │ (Subagent)  │
    │             │    │  Grep)      │    │             │
    └─────────────┘    └─────────────┘    └─────────────┘
           │                   │                   │
           └───────────────────┼───────────────────┘
                               │
                               v
┌──────────────────────────────────────────────────────────────────┐
│  Step 4: Executor                                                │
│  - AsyncRuntime 并行执行                                          │
│  - Subagent 内部有自己的 Planner (可迭代)                          │
└──────────────────────────────────────────────────────────────────┘
```

### 与现有代码的集成点

| 文件 | 修改点 |
|------|--------|
| `src/nimbus/core/planner/pipeline.py` | 在 RulePlanner 之后插入 TaskRouter 阶段 |
| `src/nimbus/core/planner/router.py` | **新增** - TaskRouter 实现 |
| `src/nimbus/core/planner/llm_enhancer.py` | 精简 prompt，重命名为 ToolDAGPlanner |
| `src/nimbus/core/planner/delegator.py` | **新增** - SubagentDelegator 实现 |
| `src/nimbus/core/planner/__init__.py` | 导出新组件 |
| `src/nimbus/core/planner/protocol.py` | 添加 TaskComplexity 枚举 |

### 新增类型定义

```python
# protocol.py 新增

class TaskComplexity(str, Enum):
    """任务复杂度级别"""
    SIMPLE = "simple"      # 直接回复
    MODERATE = "moderate"  # 工具 DAG
    COMPLEX = "complex"    # 委托 Subagent


@dataclass
class RoutingResult:
    """路由决策结果"""
    complexity: TaskComplexity
    suggested_tools: List[str] = field(default_factory=list)
    subagent_type: Optional[str] = None
    confidence: float = 1.0
    reasoning: Optional[str] = None
```

## 决策记录

### Decision 1: 新增 TaskRouter 阶段

- **决策**: 在 RulePlanner 和 LLMEnhancer 之间新增 TaskRouter 阶段
- **理由**:
  1. 将复杂度判断与 DAG 生成分离，每个 LLM 调用只做一件事
  2. 路由决策 prompt 短 (~400 字符)，不会失焦
  3. 为不同复杂度选择最优处理路径
- **备选方案**: 在 RulePlanner 中增加更多规则覆盖 -> 规则爆炸，难以维护
- **风险**: 增加一次 LLM 调用延迟 (~200ms)

### Decision 2: 复杂任务委托 Subagent

- **决策**: COMPLEX 级别任务（代码编辑/重构）直接委托给 Coder Subagent
- **理由**:
  1. Subagent 可以迭代执行（Read -> 理解 -> Edit），不需要预知文件内容
  2. 解决 Edit 需要 old_string 但 Planner 不知道内容的问题
  3. 复用已有的 Subagent 系统 (`src/nimbus/tools/subagent.py`)
- **备选方案**: 让 LLMEnhancer 先生成 Read，再触发重规划 -> 延迟高，逻辑复杂
- **风险**: Subagent 执行时间较长

### Decision 3: 精简 ToolDAGPlanner Prompt

- **决策**: 将 LLMEnhancer prompt 从 5429 字符精简到 ~1500 字符
- **理由**:
  1. 只保留 Read/Glob/Grep 三个只读工具
  2. Edit/Write/Bash 任务已经路由到 Subagent，无需在此 prompt 中
  3. 减少示例数量从 12 个到 3 个
- **备选方案**: 保持原 prompt，但加入更多示例强调 Edit -> 适得其反，更容易失焦
- **风险**: 可能漏掉一些边缘情况

## 权衡取舍

### 1. 延迟 vs 准确性

- **选择准确性**: 增加一次路由 LLM 调用 (~200ms)，换取更准确的任务处理
- **理由**: 失焦导致的任务失败更浪费时间（需要用户重试）

### 2. 灵活性 vs 简单性

- **选择简单性**: 三级复杂度分类而非更细粒度
- **理由**:
  - SIMPLE/MODERATE/COMPLEX 足够覆盖 90% 场景
  - 细粒度分类会增加路由 prompt 复杂度

### 3. 集中 vs 分散

- **选择分散**: 将 DAG 生成能力分散到三个处理器
- **理由**:
  - 每个处理器专注一类任务，prompt 短且针对性强
  - 比单一 LLMEnhancer 处理所有情况更可靠

## 风险分析

| 风险 | 可能性 | 影响 | 缓解措施 |
|------|--------|------|----------|
| 路由错误（应该 COMPLEX 判为 MODERATE） | 中 | 高 | 路由失败后 fallback 到 Subagent |
| 路由延迟过高 | 低 | 中 | 使用更快的模型做路由（如 Claude Haiku） |
| Subagent 执行时间过长 | 中 | 中 | 设置 max_turns 限制，提供进度反馈 |
| 精简 prompt 后遗漏边缘情况 | 中 | 中 | 保留详细版本作为 fallback |

## 迁移策略

### Phase 1: 路由器原型 (1-2 天)

1. 实现 TaskRouter 类
2. 添加到 PipelineConfig 作为可选阶段
3. 通过 feature flag 控制启用

### Phase 2: 处理器实现 (2-3 天)

1. 实现 DirectReplyHandler
2. 精简 LLMEnhancer 为 ToolDAGPlanner
3. 实现 SubagentDelegator

### Phase 3: 测试和调优 (2-3 天)

1. 添加 router 单元测试
2. 端到端测试覆盖三类任务
3. 基于测试结果调优路由规则

### Phase 4: 全面启用 (1 天)

1. 将 router 设为默认启用
2. 监控指标：路由准确率、执行成功率、延迟
3. 收集失败案例，持续改进

## 监控指标

| 指标 | 描述 | 目标 |
|------|------|------|
| router_accuracy | 路由决策正确率 | > 90% |
| simple_latency | SIMPLE 任务端到端延迟 | < 500ms |
| moderate_latency | MODERATE 任务端到端延迟 | < 2s |
| complex_success_rate | COMPLEX 任务成功率 | > 80% |
| prompt_tokens | 平均 prompt token 数 | < 800 (原 ~1500) |

## 附录

### A. 完整路由 Prompt

```
判断任务复杂度。只输出一行 JSON。

分类规则:
- SIMPLE: 直接回复即可（问候、感谢、基于对话历史能回答的问题）
- MODERATE: 需要使用1-3个只读工具（读取文件、搜索代码、列出目录）
- COMPLEX: 需要修改代码、创建文件、运行命令、或需要多步迭代完成

示例:
"你好" -> {"level":"SIMPLE"}
"读取 main.py" -> {"level":"MODERATE","tools":["Read"]}
"搜索所有 TODO" -> {"level":"MODERATE","tools":["Grep"]}
"给 test.py 添加错误处理" -> {"level":"COMPLEX","type":"coder"}
"分析项目架构" -> {"level":"MODERATE","tools":["Glob","Read"]}

任务: {goal}
```

(共 372 字符，满足 < 500 字符要求)

### B. ToolDAGPlanner 精简 Prompt

```
为以下任务生成工具调用计划。只输出 JSON。

可用工具:
- Read: 读取文件 {file_path}
- Glob: 查找文件 {pattern, path?}
- Grep: 搜索代码 {pattern, path?, type?}

输出格式:
{"tasks": [{"id": "t1", "skill": "工具名", "params": {...}, "depends_on": []}]}

规则:
1. 可以并行执行的任务 depends_on 为空数组
2. 需要前一个结果的任务填写依赖的 id

上下文:
{context}

任务: {goal}
JSON:
```

(共 298 字符，加上上下文和目标后约 1000-1500 字符)

### C. 与 Claude Code 的设计对比

| 方面 | Claude Code | Nimbus 新设计 |
|------|-------------|---------------|
| 路由机制 | 本体铁律 (系统 prompt) | TaskRouter (LLM 调用) |
| 复杂任务 | Task tool 召唤分身 | Subagent tool |
| 工具权限 | allowed_tools 参数 | SubagentRegistry |
| 快速路径 | 无 (本体不执行) | RulePlanner + try_rule_match |

本设计借鉴了 Claude Code 的分身/本体理念，但适配了 Nimbus 的 DAG 规划架构。

---

## 审批记录

| 日期 | 审批人 | 状态 | 备注 |
|------|--------|------|------|
| 2026-01-25 | @意分身 | Proposed | 初稿 |

## 相关文档

- `src/nimbus/core/planner/llm_enhancer.py` - 现有 LLM 规划实现
- `src/nimbus/core/planner/rule_planner.py` - 规则规划器
- `src/nimbus/tools/subagent.py` - Subagent 系统
- `docs/architecture.md` - 整体架构文档
