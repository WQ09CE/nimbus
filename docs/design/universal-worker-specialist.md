# 通用 Worker Specialist 设计文档

> **Author**: Architect Agent  
> **Date**: 2025-02-22  
> **Status**: Proposal  

---

## 1. 现有 Specialist 能力矩阵

| Specialist | Read | Write | Edit | Bash | write_filter | TRACK_DIFF | 定位 |
|:-----------|:----:|:-----:|:----:|:----:|:------------|:----------:|:-----|
| **Explorer** | ✅ | ❌ | ❌ | ✅ | — | ❌ | 只读调查员：搜代码、找模式、读结构 |
| **Implementer** | ✅ | ✅ | ✅ | ✅ | 无限制 | ✅ | 全能工程师：写代码、改文件、跑命令 |
| **Architect** | ✅ | ✅ | ❌ | ❌ | `.md` only | ✅ | 设计师：只能写 markdown |
| **Tester** | ✅ | ❌ | ❌ | ✅ | — | ❌ | 测试员：只读+跑命令，只报告不修复 |
| **Orchestrator** (自身) | ✅ | ❌ | ❌ | ✅ | — | — | 协调者：有能力但不应亲自干活 |

**源码引用**：
- Specialist 定义：`src/nimbus/orchestration/specialist_tools.py` (L1-L158)
- Profile 工厂：`src/nimbus/core/profile.py` (L83-L146)
- 工具声明：`src/nimbus/orchestration/tools.py` (L252-L388)

---

## 2. 能力缺口分析

### 2.1 缺口总览

| 场景 | Explorer | Implementer | 问题 |
|:-----|:--------:|:-----------:|:-----|
| 跑个脚本看结果 | ✅ 能做但语义不对 | ✅ 杀鸡用牛刀 | Explorer 定位是"读代码"，不是跑杂活 |
| git 操作（commit/branch/stash） | ❌ 只读 | ✅ 太重 | 需要 Bash+有时写文件，但不是"实现" |
| 文件系统管理（mv/rm/cp/rename） | ❌ 只读 | ✅ 太重 | Implementer 有完整 system prompt，overhead 大 |
| 数据处理/格式转换 | ❌ 不能写 | ✅ 太重 | 写个 JSON、CSV 转换不需要"工程师" |
| 环境检查+简单修复 | ❌ 不能写 | ✅ 太重 | 检查 python 版本、装个包、改个 config |
| 生成配置文件 | ❌ 不能写 | ✅ 太重 | 生成 `.gitignore`、`.env`、`Makefile` |
| 跑 benchmark 并收集结果 | ✅ 能做 | ✅ 太重 | 跑命令+写结果文件，两个都不完美匹配 |

### 2.2 核心问题

**Implementer 的"重"体现在三个层面：**

1. **语义过重**：Implementer 的 system prompt 强调"写代码、精确编辑、不偏离指令"，对于"跑个脚本看看输出"这种任务来说指令噪音太大
2. **心理成本**：Orchestrator 调用 Implement 意味着"我要做一个正式的实现"，对于杂活任务这个语义门槛太高，导致 Orchestrator 倾向于自己用 Bash 干（违反了"不应自己做太多"的原则）
3. **Diff 追踪开销**：Implementer 开启了 `TRACK_DIFF=True`，每次调用都会做 workspace snapshot 对比，对于不改代码的操作是纯浪费

**Explorer 的"窄"体现在：**

- 没有 Write/Edit 权限，一旦任务需要写任何文件（哪怕是把命令输出存到文件），就无法使用
- 但很多"探索+记录"型任务确实需要写个结果文件

**结果**：Orchestrator 遇到"杂活"时陷入两难——用 Explorer 权限不够，用 Implementer 太重。最终要么自己用 Bash 硬干，要么勉强用 Implementer 浪费 token。

---

## 3. Worker Specialist 设计方案

### 3.1 定位

**Worker** = 轻量级通用执行者。"什么都能干一点，但不是专家。"

对标现实：Explorer 是侦察兵，Implementer 是工程师，Architect 是建筑师，Tester 是 QA。**Worker 是勤杂工**——搬东西、跑腿、收拾场地。

### 3.2 能力配置

```python
# profile.py 新增

@classmethod
def create_worker(cls, model_id: str = "default") -> "AgentProfile":
    """Create a Worker Agent profile (general-purpose utility)."""
    from nimbus.orchestration.prompts import PromptManager
    return cls(
        name="worker",
        role="worker",
        allowed_tools=["Read", "Write", "Bash", "SubmitResult"] + _NIMFS_SPECIALIST,
        system_prompt=PromptManager.get_system_prompt("worker", model_id),
        max_iterations=30,          # 比 Implementer 少，杂活不应太复杂
        max_consecutive_thoughts=1, # 干活为主，少废话
        write_filter=[],            # 不限制文件类型
    )
```

**关键设计决策：**

| 决策 | 选择 | 理由 |
|:-----|:-----|:-----|
| 包含 Write？ | ✅ | 必须能写文件，否则和 Explorer 重叠 |
| 包含 Edit？ | ❌ | Edit 是精确编辑工具，暗示"改代码"。Worker 不应做精确代码编辑 |
| write_filter | 无限制 | Worker 需要写各种类型：config、json、txt、sh 等 |
| max_iterations | 30 | 低于 Implementer(50)，杂活不应太长 |
| TRACK_DIFF | ✅ | Worker 能写文件，Orchestrator 需要知道改了什么 |

### 3.3 System Prompt

```python
# prompts.py 新增

WORKER_INSTRUCTIONS = """\
You are the **Worker Agent** — a general-purpose utility runner.

## Your Mission
- Execute miscellaneous tasks that don't fit neatly into exploration, implementation, or testing.
- Run scripts, manage files, process data, perform git operations, set up environments.
- You are the "get it done" agent — practical and efficient.

## Your Toolkit
- **Read**: Read file contents
- **Write**: Create or overwrite files (any type)
- **Bash**: Run shell commands

## Rules
- You do NOT have Edit. If a task requires precise code editing (surgical text replacement), tell the orchestrator to use Implement instead.
- Keep it simple. You're for quick jobs, not complex multi-file refactoring.
- Report what you did and what the results are.
- **Task Completion**: When done, call `SubmitResult(result="your summary")` to deliver results.
"""
```

### 3.4 Specialist Tool 类

```python
# specialist_tools.py 新增

class WorkerTool(SpecialistTool):
    """General-purpose utility execution."""
    ROLE = "worker"
    DEFAULT_TIMEOUT = 300.0  # 5 min — 杂活应该更快
    TRACK_DIFF = True

    def _create_profile(self, model_id: str = "default") -> AgentProfile:
        return AgentProfile.create_worker(model_id)
```

### 3.5 Tool Definition

```python
# tools.py 新增

WORKER_TOOL_DEF = {
    "name": "Worker",
    "description": (
        "Delegate a miscellaneous utility task to the Worker agent. "
        "The Worker can Read files, Write files (any type), and run Bash commands. "
        "Use for: running scripts, file management (move/copy/delete/rename), "
        "git operations, data processing, environment setup, config generation. "
        "Lighter than Implement -- no Edit tool, shorter timeout, simpler prompt. "
        "Do NOT use for precise code editing or complex refactoring."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "What to do. Be specific: what commands to run, what files to manage, "
                    "what data to process."
                ),
            },
            **_COMMON_OPTIONAL_PARAMS,
        },
        "required": ["task"],
    },
}
```

### 3.6 Orchestrator Profile 更新

```python
# profile.py — create_orchestrator 修改

allowed_tools=["Read", "Bash", 
               "Explore", "Implement", "Design", "Test", "Worker",  # ← 新增 Worker
               "Verify", "ReviewCommittee", "Memo"] + _NIMFS_ALL,
```

### 3.7 Orchestrator Prompt 更新

```markdown
# prompts.py — ORCHESTRATOR_INSTRUCTIONS 中添加

**Specialist Tools** (delegate to specialist agents):
- **Explore(task, ...)**: 只读代码探索
- **Implement(task, ...)**: 代码实现（全权限，重量级）
- **Design(task, ...)**: 架构设计（只写 .md）
- **Test(task, ...)**: 测试执行（只读+Bash）
- **Worker(task, ...)**: 通用杂活（Read+Write+Bash，轻量级）  ← 新增

## When to Delegate vs Do It Yourself
- **Worker**: File management, git ops, running scripts, data processing, config generation
- **Implement**: Code writing, precise editing, multi-file refactoring (heavier than Worker)
```

---

## 4. Worker vs Implementer 选择指南

Orchestrator 需要清晰的决策边界：

| 任务 | 用 Worker | 用 Implementer |
|:-----|:---------:|:--------------:|
| `git commit -m "..."` | ✅ | |
| 跑 `python script.py` 看输出 | ✅ | |
| 删除/移动/重命名文件 | ✅ | |
| 生成 `.gitignore` / `Makefile` | ✅ | |
| 把 JSON 转成 CSV | ✅ | |
| 装 pip 包、配环境 | ✅ | |
| 跑 benchmark 收集数据 | ✅ | |
| 精确修改某函数某行代码 | | ✅ |
| 多文件重构 | | ✅ |
| 新建一个完整模块 | | ✅ |
| 需要 Edit 工具的精确替换 | | ✅ |

**判断口诀**：需要 Edit 或需要"理解代码上下文做精确修改"→ Implementer。其他 → Worker。

---

## 5. 实现清单

按依赖顺序：

| # | 文件 | 改动 |
|:--|:-----|:-----|
| 1 | `src/nimbus/orchestration/prompts.py` | 新增 `WORKER_INSTRUCTIONS`，在 `ROLE_INSTRUCTIONS` dict 中注册 |
| 2 | `src/nimbus/core/profile.py` | 新增 `create_worker()` 工厂方法 |
| 3 | `src/nimbus/orchestration/specialist_tools.py` | 新增 `WorkerTool` 类 |
| 4 | `src/nimbus/orchestration/tools.py` | 新增 `WORKER_TOOL_DEF` |
| 5 | `src/nimbus/core/profile.py` | `create_orchestrator()` 的 `allowed_tools` 加入 `"Worker"` |
| 6 | `src/nimbus/orchestration/prompts.py` | `ORCHESTRATOR_INSTRUCTIONS` 加入 Worker 说明 |
| 7 | `src/nimbus/agentos.py` | 工具注册逻辑中加入 Worker（跟随 Explore/Implement/Design/Test 的注册模式） |
| 8 | 测试 | 新增 Worker specialist 的单元测试 |

**预计代码量**：约 80 行新增，20 行修改。

---

## 6. 风险与缓解

| 风险 | 缓解 |
|:-----|:-----|
| Orchestrator 把所有任务都丢给 Worker（滥用） | prompt 中明确边界："需要 Edit 的用 Implement" |
| Worker 做了复杂代码修改（Write 覆盖整个文件） | 接受此风险。Worker 没有 Edit，只能 Write 全文件覆盖，对于大文件修改自然不如 Implementer 精确 |
| 工具太多导致 Orchestrator 选择困难 | 5 个 specialist（Explore/Implement/Design/Test/Worker）仍在合理范围。prompt 中给出清晰决策树 |

---

## 7. 未来扩展

- **Worker 可考虑的 model 降级**：Worker 任务通常简单，可以默认用更便宜的 model（如 gemini-flash），在 `WorkerTool` 中设置 `DEFAULT_MODEL = "gemini-flash"`
- **Worker 超时自动升级**：如果 Worker 超时，Orchestrator 可以自动用 Implementer 重试（在 prompt 中指导）
- **Parallel Worker**：杂活任务通常互不依赖，适合并行调用多个 Worker（与 Explorer 类似，在 prompt 中标注 "可并行"）
