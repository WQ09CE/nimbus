# Nimbus Terminal-Bench 评审委员会主席总结报告

**评审日期**: 2026-02-05  
**评审对象**: nimbus 0.2.0 在 terminal-bench 9 项有效任务上的表现（通过率 22%）  
**参评专家**: Claude Opus 4.5 (Deep Thinker), GPT-5.2 (Generalist), Gemini 3 Pro High (Reasoning)  
**交叉验证**: 主席已逐一核实专家引用的源码，确认引用准确性

---

## 第一部分：共识与分歧

### 1.1 完全共识（三位专家一致同意）

#### 共识 A：核心能力没有问题，瓶颈在框架层面

三位专家对此看法高度一致：

> - Opus："核心能力没有问题，问题集中在框架层面缺少的'最后一公里'机制"
> - GPT-5.2："真正的问题不在'能不能做'，而在'做完之后能不能确认做对了'"
> - Gemini："问题不在'智商'而在'工作习惯'"

**主席验证**：数据支持此判断。5/7、10/11、2/3 的子项通过率证明 LLM 推理 + 工具调用链路是健康的。

#### 共识 B：验证机制是最核心的缺陷

三位专家均指出当前验证只是一个可忽略的文本 hint：

```python
# vcpu.py line 1079-1083 — 已核实
if action.name in ("Edit", "Write") and result.status == "OK":
    output_str += (
        "\n\n[Hint] File modified successfully. "
        "If the task involves code or configuration, "
        "consider testing it with Bash before finishing."
    )
```

三位专家均提议引入 **Verification Gate**——在 `_handle_return` 路径上拦截，执行确定性检查后再决定是否真正返回。此为评审中最强共识。

#### 共识 C：Bash 工具需要 Daemon 模式

三位专家均指出 `bash.py` 的 `asyncio.create_subprocess_shell` + `PIPE` 模式无法支持 `command &` 后台化，且 60s 超时导致 kv-store-grpc 浪费 17 轮迭代。均建议增加专门的后台进程支持。

#### 共识 D：需要修复后全局扫描机制

三位专家均指出 build-cython-ext 的"修了 2 个漏 1 个"是工作流缺陷，均建议在 Edit 成功后自动 grep 同类 pattern。

#### 共识 E：Docker 安装兼容性是工程 bug

`pyproject.toml` 声明 `requires-python = ">=3.10"`，但 swe-bench-langcodes 容器使用 Python 3.9。三位专家均认为这是纯工程问题，需要 CI 覆盖多版本。

---

### 1.2 方向一致但方案有差异

#### 差异 1：Verification Gate 的实现深度

| 专家 | 方案 | 层级 |
|------|------|------|
| Opus | 分 L0（确定性检查）→ L1（需求关键词匹配）→ L2（LLM 交叉验证），建议 VerificationEngine 注册多个 Checker | 最激进 |
| GPT-5.2 | 同样分层（确定性 → 需求比对 → LLM 交叉），但额外提出 Reviewer Agent 应该有独立上下文不复用 Executor 的执行历史 | 中间 |
| Gemini | 建议用 MMU 的执行历史提取 agent 做了什么，做确定性验证。不建议 LLM 交叉验证（认为复杂度太高） | 最保守 |

**主席评估**：Gemini 的保守方案最务实。L0/L1 的确定性检查（文件系统 diff、需求关键词 grep）是投入产出比最高的。Opus 和 GPT-5.2 提出的 L2 层 LLM 交叉验证确实有价值，但实现复杂度显著增加（需要独立 context、额外 API 调用成本），建议作为后续迭代。

#### 差异 2：需求约束追踪的方法

| 专家 | 方案 | 方法 |
|------|------|------|
| Opus | ConstraintExtractor，正则提取关键标识符 | 纯规则 |
| GPT-5.2 | RequirementAnchor，用一次 LLM 调用提取结构化约束 | LLM 辅助 |
| Gemini | RequirementTracker，用正则 + 启发式提取 | 纯规则 |

**主席评估**：GPT-5.2 用 LLM 做一次性约束提取虽然成本极低，但引入了不确定性——LLM 提取的约束本身可能有误。Opus/Gemini 的纯规则方案更可靠。但实际上，自然语言中提取精确字段名的规则写起来很脆弱（比如要区分"a value (int)"里的 `value` 是字段名还是普通英文单词），纯正则难以完美处理。

**折中建议**：先用简单规则提取反引号/引号内的标识符、路径、端口号等高置信度约束。对于需要语义理解的复杂约束，允许任务提交者通过结构化 metadata 显式声明（这也更符合实际工程场景——任务需求本就应该有结构化部分）。

#### 差异 3：环境 Snapshot 的粒度

| 专家 | 方案 |
|------|------|
| Opus | 任务开始时 snapshot 整个 `/app`，RETURN 时 diff |
| GPT-5.2 | 同上，但额外包含关键文件的 hash |
| Gemini | 每次 Bash 执行前后都做 snapshot |

**主席评估**：Gemini 的逐次 Bash snapshot 粒度最细但开销最大。考虑到 terminal-bench 容器中 `/app` 目录通常不大（数十到数百文件），**Opus/GPT-5.2 的"任务首尾 diff"方案开销最小且已能覆盖 polyglot 场景**。Gemini 的逐次 snapshot 可以作为 debug 模式保留。

---

### 1.3 独到观点（仅一位专家提出）

| 观点 | 提出者 | 评估 |
|------|--------|------|
| **注意力预算管理**（Attention Budget）：在最后 5 轮强制进入验证 | Opus | ⭐ 有价值。可以防止 agent 在最后时刻仍在"做"而不是"验"。但"强制进入验证"的实现需要谨慎——不应打断真正在推进的工作。 |
| **Dual-Agent 验证**（Executor + Reviewer 独立 context） | GPT-5.2 | ⭐⭐ 长期最有价值。但当前 nimbus 的 `AgentOS.spawn` 机制虽存在，IPC 已被标记为 YAGNI 移除。要落地需要重新设计 agent 间通信，工作量大。建议作为 v0.4 规划。 |
| **确认偏差的本质论述**（"单程执行架构"的根本局限） | GPT-5.2 | 精准。这是整份评审中最有洞察力的观点——当前架构的核心问题不是某个模块缺失，而是**缺少对抗性反馈回路**。所有改进方案本质上都是在为 single-agent 架构打补丁。 |
| **Execution Budget 可视化** | Opus | 中性。注入 step/budget 信息接近 prompt engineering 的边界。但如果与 Verification Gate 结合——当预算不足时强制触发验证而非只是显示——则有工程价值。 |

---

## 第二部分：代码方案交叉审计

### 2.1 Bash Daemon 模式（三人均提出，方案相似）

**Opus 方案**：独立的 `StartDaemon` 工具  
**GPT-5.2 方案**：独立的 `BackgroundRun` 工具  
**Gemini 方案**：在现有 `Bash` 工具上增加 `daemon` 参数

**审计意见**：

Gemini 的"在现有工具上加参数"方案最符合 nimbus 当前的工具注册架构（`gate.py` 的 `register_tool`），不需要新增工具定义。但三个方案的核心实现逻辑一致：

```python
# 核心逻辑（三人共识）
full_cmd = f"nohup {command} > {log_file} 2>&1 & echo $!"
# 然后 poll log_file 或 port 等待就绪
```

**潜在问题**：三位专家都用了 `nohup ... &` 的 shell 语法，但在 Docker 容器的 `/bin/sh` 中，`nohup` 可能不可用（kv-store-grpc 日志中就有 `ps: not found`、`pgrep: not found` 的问题）。更健壮的实现应使用 Python 的 `subprocess.Popen` + `start_new_session=True`：

```python
import subprocess
proc = subprocess.Popen(
    command, shell=True,
    stdout=open(log_file, 'w'),
    stderr=subprocess.STDOUT,
    start_new_session=True,  # 关键：detach from parent process group
)
return proc.pid
```

**结论**：采用 Gemini 的工具层方案（在 Bash 工具上加 `daemon` 参数），但实现上用 `subprocess.Popen(start_new_session=True)` 替代 `nohup`。

---

### 2.2 Verification Gate（三人均提出，深度不同）

**审计重点**：三位专家都建议在 `_handle_return` 路径拦截。我核实了当前代码：

```python
# vcpu.py line 1094-1119 — 当前实现
async def _handle_return(self, action: ActionIR) -> ToolResult:
    result = action.args.get("result", ...)
    # 直接返回 is_final=True，没有任何验证
    return ToolResult(status="OK", output=result, is_final=True, ...)
```

确实如三位所述——直接返回，没有拦截点。插入 Verification Gate 的位置应该在 `is_final=True` 之前。

**Opus 的 Checker 注册表模式**比较优雅（类似现有的 `ErrorHandlerRegistry`），便于扩展：

```python
class VerificationEngine:
    def __init__(self):
        self._checkers: List[VerificationChecker] = []
    def register(self, checker): ...
    async def verify(self, goal, workspace): ...
```

**GPT-5.2 强调的"验证者不共享执行者上下文"**是关键设计约束。如果验证阶段复用同一个 MMU context，LLM 的 confirmation bias 会使验证形同虚设。但如果只做确定性检查（L0/L1），则不存在此问题——不需要 LLM 参与。

**结论**：采用 Opus 的 Checker 注册表架构，先实现 L0（文件系统 diff）和 L1（需求关键词 grep）两个确定性 Checker。不在 v0.2.x 阶段引入 LLM 交叉验证。

---

### 2.3 Post-Fix Sweep（三人均提出）

**Opus 方案**：在 `_handle_tool_call` 的 Edit 成功分支中自动触发 grep  
**GPT-5.2 方案**：同上，命名为 ReflexionScanner  
**Gemini 方案**：新增 `Sweep` 工具 + `PostFixSweepHandler`

**审计意见**：

Gemini 新增独立 `Sweep` 工具的方案有一个好处——agent 也可以主动调用它。但核心价值在于**框架自动触发**，不应依赖 agent 的自觉性。

三位专家都遇到了一个共同的难题：**如何从 `old_text` 中提取有意义的搜索 pattern**。例如：

```python
# old_text = "cdef np.int[:] order"
# new_text = "cdef np.int64[:] order"
# 应该提取 "np.int" 但不应该匹配 "np.int64"
```

这需要精确的 diff 分析——取 old/new 的差异部分作为 pattern。三位专家都含糊地说"提取核心 pattern"但没有给出可靠的通用算法。

**实际可行的简化方案**：不做通用 pattern 提取，而是在 Edit 成功后，直接用 `old_text`（被替换掉的原文）作为搜索 pattern，在项目中 `grep -F` 搜索。如果在其他文件中找到相同的旧文本，说明可能需要同样的修复。

```python
# 简化实现
if old_text and len(old_text) < 200:  # 太长的文本不适合做 pattern
    grep_result = await bash_command(
        f"grep -rnF '{old_text}' . --include='*.py' --include='*.pyx' | grep -v '{file_path}'"
    )
```

**结论**：采用"框架自动触发"（Opus/GPT-5.2 方案），实现上用 `grep -F old_text` 的简化策略。同时可以提供 Gemini 提议的独立 `Sweep` 工具供 agent 主动调用。

---

### 2.4 需求约束提取

三位专家的正则方案我做了交叉验证。以 kv-store-grpc 的需求为例：

```
SetVal takes a message named SetValRequest that includes a key (string) 
and a value (int) as parameters and returns a SetValResponse with a val (int) field
```

- Opus/Gemini 的 `r'(\w+)\s*\((\w+)\)'` 会匹配：`key(string)`, `value(int)`, `val(int)` ✅
- 但也会匹配 `message named SetValRequest that includes a key (string)` 中的各种噪音

**实际测试**：这条正则对 kv-store-grpc 有效（确实能提取出 `value` 和 `val` 是两个不同标识符），但在 pcap-to-netflow 任务（"NetFlow v5 format"）或 polyglot 任务中价值有限。

**结论**：约束提取器的实用性高度依赖任务类型。建议作为 P2 优先级实现，且初始版本只提取最可靠的约束（文件路径、端口号、反引号内代码）。

---

## 第三部分：最终结论与实施方案

### 3.1 核心判断

三位专家的共识可以归纳为一句话：

> **Nimbus 的 Think-Act-Observe 循环缺少一个 Verify 阶段。当前架构是 open-loop（开环）的——agent 执行完即返回，没有确定性的反馈回路来校验产出物是否满足需求。**

这不是 prompt engineering 能解决的问题。需要在框架层面引入 **closed-loop（闭环）验证机制**。

### 3.2 推荐实施路线

#### Phase 1：低成本高收益（1-2 天）

**1a. Bash Daemon 模式**

在 `bash.py` 中增加 `daemon: bool` 参数。当 `daemon=True` 时，使用 `subprocess.Popen(start_new_session=True)` 启动，将 stdout/stderr 重定向到临时文件，轮询端口或 log pattern 等待就绪。

预期收益：消除 kv-store-grpc 类任务中 10+ 轮的无效迭代。

**1b. Docker 多版本兼容性修复**

- `pyproject.toml` 的 `requires-python` 是否应降至 `>=3.9`？如果不降，则需要在安装脚本中做版本检查并给出清晰错误。
- 添加 CI matrix 覆盖 Python 3.9-3.13。

预期收益：直接修复 swe-bench-langcodes 的安装失败。

#### Phase 2：核心架构改进（3-5 天）

**2a. Verification Gate (L0 + L1)**

架构设计：

```
_handle_return(action)
    ├── VerificationEngine.verify(goal, workspace)
    │     ├── WorkspaceDiffChecker    # L0: 对比任务首尾的 /app 目录状态
    │     └── RequirementGrepChecker  # L1: 从 goal 提取关键标识符，在产出物中 grep
    ├── 如果全部通过 → return ToolResult(is_final=True)
    └── 如果有 issue → 注入反馈到 MMU，return ToolResult(is_final=False)
```

关键约束：
- L0/L1 层只做**确定性检查**，不调用 LLM
- Verification Gate 最多触发 **1 次**（避免无限验证循环）
- Gate 有自己的超时限制（比如 10 秒）

**2b. Post-Fix Sweep**

在 `_handle_tool_call` 中 Edit 成功后自动触发：
- 取 `old_text` 作为搜索 pattern
- `grep -rnF` 搜索同项目其他文件
- 如果有匹配，以 `[Post-Fix Sweep]` 系统消息注入 MMU

#### Phase 3：深度增强（后续版本规划）

| 项目 | 描述 | 目标版本 |
|------|------|----------|
| Verification Gate L2 | 独立 context 的 LLM 交叉验证 | v0.3 |
| 需求结构化约束提取 | 从 goal 中提取精确字段名/路径/端口 | v0.3 |
| Dual-Agent 验证 | Executor + Reviewer 分离 | v0.4 |
| Attention Budget | 尾部迭代强制触发验证 | v0.3 |

### 3.3 预期效果

| 任务 | 当前结果 | Phase 1 后 | Phase 2 后 |
|------|----------|-----------|-----------|
| fibonacci-server | ✅ | ✅ | ✅ |
| kv-store-grpc (5/7) | ❌ | ❌（仍有字段名问题，但省下时间可能间接改善） | ✅（RequirementGrepChecker 可检出 `value` 缺失） |
| pypi-server | ✅ | ✅ | ✅ |
| polyglot-c-py (0/1) | ❌ | ❌ | ✅（WorkspaceDiffChecker 检出 `cmain`） |
| pcap-to-netflow (1/4) | ❌ | ❌ | ❌（时间戳语义错误需要 L2 层或领域知识） |
| add-benchmark-lm-eval (2/3) | ❌ | ❌ | ❌（metric 配置需要框架知识） |
| swe-bench-langcodes | ❌ | ✅（安装修复） | ✅ |
| build-cython-ext (10/11) | ❌ | ❌ | ✅（Post-Fix Sweep 扫出 ccomplexity.pyx） |

**预期通过率：Phase 1 后 33% → Phase 2 后 56-67%**

### 3.4 对专家的反馈

对三位专家的工作质量给予高度认可。具体反馈：

- **Claude Opus 4.5**：分析最为全面深入，VerificationEngine 的 Checker 注册表设计和 Attention Budget 概念有原创价值。但部分方案（如 ConstraintExtractor）的伪代码过于乐观——实际的自然语言约束提取远比示例复杂。
- **GPT-5.2**：对 single-agent 架构局限性的论述（confirmation bias、开环 vs 闭环）最具洞察力，"验证者不应共享执行者上下文"是关键设计原则。Dual-Agent 建议虽暂不落地但方向正确。
- **Gemini 3 Pro High**：最务实，优先级排序最合理。"先做 Bash Daemon（投入产出比最高）"的建议正确。但 RequirementTracker 的正则方案略显粗糙，需要更多边界情况处理。

---

*评审主席签发*  
*2026-02-05*
