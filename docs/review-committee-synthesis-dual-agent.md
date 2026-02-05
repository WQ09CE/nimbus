# 评审委员会综合报告：Dual-Agent 编排架构提案

**报告类型**：主席综合裁定  
**评审对象**：`nimbus/docs/proposal-dual-agent-orchestration.md`  
**参与专家**：Claude Opus 4.5（深度思考者）、GPT-5.2（通才型）、Gemini 3 Pro High（推理型）  
**报告日期**：2026-02-05  
**最终裁定**：**有条件通过（Conditional Approve）— 需修订 4 项后可进入实施**

---

## 一、评审流程说明

本报告基于三位独立专家对提案的全面评审意见，由主席交叉验证其代码级断言后综合而成。主席已完整阅读了：

- 提案全文（`proposal-dual-agent-orchestration.md`）
- AgentOS 核心源码（`agentos.py`，626 行）
- 工具注册系统（`tools/base.py` 的 `ToolRegistry`，`tools/__init__.py` 的 `register_default_tools`）
- 系统调用门（`os/gate.py` 的 `KernelGate`）
- Harbor 适配层（`nimbus_harbor/nimbus_agent.py`）

---

## 二、专家共识（三方一致）

以下 7 点为三位专家**完全一致**的判断，主席确认均成立：

### 共识 A：问题诊断精准 ✅

三位专家一致认为提案正确识别了"自验证闭环（confirmation bias）"是架构层面问题而非 prompt engineering 问题。从 benchmark 数据出发的论证方式——特别是 `value` vs `val` 的具体案例——有说服力。

### 共识 B：Core + Executor 双角色方向正确 ✅

"对抗性验证需要独立上下文"的核心洞察正确。没有专家建议放弃 dual-agent 方向。

### 共识 C："不改内核"的约束设得好 ✅

三位专家均确认提案"在 AgentOS 之上的薄编排层"策略比侵入 VCPU/MMU 的方案更安全。主席验证了 `AgentOS.spawn()`/`wait()`/`run()` 接口确实能支撑这个方案。

### 共识 D：Bash Daemon 是前置依赖 ⚠️

三位专家**一致**指出：没有 Bash Daemon 解决后台进程问题，Executor 在 gRPC/server 类任务上同样会遇到 60s 超时问题。提案中 kv-store-grpc 的"高信心 ✅"预期**无法在不解决 Bash Daemon 的情况下兑现**。

### 共识 E：swe-bench-langcodes 的预期需要下调 ⚠️

该任务失败原因是 Python 3.9 不兼容（`requires-python >= 3.10`），属于基础设施问题，与 Dual-Agent 架构无关。不应计入 Dual-Agent 的预期收益。

### 共识 F：成本估算偏乐观 ⚠️

三位专家均认为"40-60% token 增加"低估了实际开销。Claude Opus 和 Gemini 认为实际可能在 80-150%。但一致认为：**即使成本翻倍，如果通过率能从 22% 提到 50%+，ROI 仍然合理**。

### 共识 G：暂不需要第三角色 ✅

对提案决策问题 1，三位专家均建议"先跑通双角色，验证收益后再考虑扩展"。

---

## 三、专家分歧及主席裁定

### 分歧 1：AgentOS 实例化策略（两个实例 vs 单实例多 Process）

| 专家 | 立场 |
|------|------|
| Claude Opus 4.5 | 建议用"单 AgentOS + 多 Process"，更轻量 |
| GPT-5.2 | 认为需要两个独立实例，但担心 overhead |
| Gemini 3 Pro | 未明确表态，但指出工具隔离是关键 |

**主席交叉验证结果**：

经代码审计，**Claude Opus 的建议在现有架构下不可行**。原因如下：

1. `AgentOS.spawn()` 创建的所有 Process 共享同一个 `self._tools`（ToolRegistry 实例）
2. `KernelGate` 的工具分发逻辑是：先查 `local_tools`（进程专属），再查 `self.executor`（全局 ToolRegistry）

```python
# gate.py 关键逻辑
if tool_name in self.local_tools:
    func = self.local_tools[tool_name]  # 进程专属
else:
    output = await self.executor.execute(tool_name, action.args)  # 全局 Registry
```

这意味着 `local_tools` 只能**新增**工具，不能**限制**全局工具。如果 Core 和 Executor 在同一个 AgentOS 中，Core 仍然能调用 Write/Edit（因为它们在全局 Registry 中）。

**裁定**：提案的"两个 AgentOS 实例"方案是**当前架构下唯一正确的选择**。但建议：
- Core 的 AgentOS 用 `kernel_tools=False`，手动注册 Read + Bash
- Executor 的 AgentOS 用 `kernel_tools=True`（默认全部工具）
- Executor 的 AgentOS 关闭 session 持久化和 compaction，降低 overhead

> **备注**：未来如果要做"单 AgentOS + 多 Process + 工具隔离"，需要修改 `KernelGate` 增加 `tool_whitelist` 参数——但这属于内核改动，不在本提案范围内。

---

### 分歧 2：Verify 工具的去留

| 专家 | 立场 |
|------|------|
| Claude Opus 4.5 | 保留 Verify，但在 prompt 中明确优先级 |
| GPT-5.2 | 保留 Verify，建议增加 `command_output_contains` 等 check type |
| Gemini 3 Pro | **删除 Verify**，Core 直接用 Bash + Read 验证 |

**主席分析**：

Gemini 的论据有力——当前 Verify 的 6 种 check type（file_exists, file_contains, command_succeeds 等）本质上是 `os.path.exists()` + `grep` + `socket.connect()` 的包装，Core 用 Bash 一行命令都能做。引入功能重叠的新概念增加认知负担。

但 Verify 有两个 Bash 做不到的优势：
1. **结构化输出**：`✅`/`❌` 比 Bash 原始输出更易让 LLM 判断
2. **原子批量执行**：多个检查一次调用返回，减少 LLM 迭代次数

**裁定**：**保留 Verify，但升级为有差异化价值的版本**。具体做法：
- 保留当前 6 种 check type（简单且 robust）
- 增加 `command_output_contains`（运行命令并断言输出包含特定字符串）
- 增加 `process_running`（检查特定进程是否存活）
- 在 Core 的 system prompt 中明确："批量验证用 Verify，单次探索用 Bash"

---

### 分歧 3：Core 的 Bash 限制方案

| 专家 | 立场 |
|------|------|
| Claude Opus 4.5 | **选项 A**（prompt 约束），足够用 |
| GPT-5.2 | **选项 C**（白名单），最安全 |
| Gemini 3 Pro | **选项 C**（白名单），黑名单不完备 |

**主席分析**：

三个方案的对比：

| 方案 | 安全性 | 实现量 | 灵活性 | 风险 |
|------|--------|--------|--------|------|
| A（prompt） | 低 | 0 行 | 最高 | LLM 在压力下可能违规 |
| B（黑名单） | 中低 | ~15 行 | 中 | 不完备（`python -c` 绕过） |
| C（白名单） | 中高 | ~20 行 | 低 | 可能遗漏合法命令 |

Claude Opus 认为"benchmark 场景下 LLM 遵守率足够"——但这在 Core 接近 iteration 上限、急需完成任务时并不可靠。GPT-5.2 和 Gemini 共同指出的白名单方案更稳健。

但白名单也有缺陷：`python3 -c` 在白名单中（Core 验证需要它），而 `python3 -c "open('x','w').write('...')"` 可以写文件。这不是白名单能解决的。

**裁定**：**采用白名单方案（选项 C），接受其不完美**。具体白名单：

```python
CORE_BASH_WHITELIST = [
    "grep", "egrep", "rg",          # 搜索
    "find",                           # 文件查找
    "ls", "tree",                     # 目录浏览
    "cat", "head", "tail", "less",    # 文件查看
    "wc", "stat", "file",            # 文件信息
    "diff",                           # 比较
    "echo", "printf",                # 输出
    "python3 -c", "python -c",       # 验证脚本
    "which", "type",                  # 命令查找
    "env", "printenv",               # 环境变量
    "curl", "wget",                   # 网络检查（只读）
    "nc -z",                          # 端口检查
    "pgrep", "ps",                    # 进程检查
    "git status", "git log", "git diff",  # Git 只读
]
```

实现量 ~20 行，通过前缀匹配。接受 `python3 -c` 的"逃逸"风险——这是设计上的有意取舍，不是疏忽。

---

### 分歧 4：Executor 的 context 隔离策略

| 专家 | 立场 |
|------|------|
| Claude Opus 4.5 | 全新 context 没问题，但 Core 的 Dispatch 指令要完备 |
| GPT-5.2 | **硬伤**——需要 `reuse_executor` 或自动 context 注入 |
| Gemini 3 Pro | **硬伤**——Dispatch 应自动附加上轮修改文件的内容 |

**主席分析**：

GPT-5.2 和 Gemini 提出的问题是真实的：第二次 Dispatch 时 Executor 不知道文件当前状态，需要重新 Read 才能修改。这浪费迭代。

但 GPT-5.2 提出的 `reuse_executor` 方案有隐患——如果复用 Executor 的 MMU 状态，就失去了"fresh context 打破 confirmation bias"的核心设计意图。

**裁定**：**保持 fresh context 设计，但在 Dispatch 中自动注入上下文**。具体做法：

```python
async def _dispatch(self, task: str, context: str = "") -> str:
    # 如果存在上一次 dispatch 的结果，自动附加关键文件内容
    if self._last_dispatch_diff:
        auto_context = "## 上次 Dispatch 修改的文件当前内容\n"
        for filepath in self._last_dispatch_diff.modified + self._last_dispatch_diff.created:
            if _is_text_file(filepath) and _file_size(filepath) < 10000:
                auto_context += f"\n### {filepath}\n```\n{Path(filepath).read_text()[:5000]}\n```\n"
        context = auto_context + "\n" + context
    
    # ... 继续执行 dispatch
```

这样 Executor 拿到的是 fresh context + 文件系统的当前状态，既不继承上一个 Executor 的推理历史（防 bias），又不需要浪费迭代去 Read 已知文件。

---

## 四、代码级交叉审计

主席对三位专家的代码级断言进行了逐一验证：

### 审计 1：GPT-5.2 声称"AgentOS 没有 `register_tool` 方法"

**❌ GPT-5.2 判断错误。**

`agentos.py` 第 1056-1084 行明确定义了 `register_tool` 方法，且支持 upsert 语义（如果已存在则先 unregister 再 register）：

```python
def register_tool(self, name: str, func: Callable, description: str = "", 
                  parameters: Optional[Dict[str, Any]] = None) -> None:
    if name in self._tools:
        self._tools.unregister(name)
    # ... register
```

提案伪代码 `os.register_tool("Dispatch", self._dispatch, ...)` **是合法的调用**。GPT-5.2 基于此错误判断衍生的"硬伤 3"（伪代码与 API 不匹配）**不成立**。

### 审计 2：`register_default_tools` 是否支持选择性注册

**✅ 三位专家均正确。**

`tools/__init__.py` 第 206 行的 `register_default_tools` 函数签名：

```python
def register_default_tools(os, workspace=None, tools: List[str] | None = None) -> List[str]:
```

`tools=["Read", "Bash"]` 可以只注册指定工具。提案的方案完全可行。

### 审计 3：KernelGate 的工具分发是否支持进程级隔离

**✅ Claude Opus 的担忧成立，但建议的解法错误。**

`gate.py` 第 146-156 行显示：`local_tools` 优先于全局 Registry，但**不能阻止全局工具被调用**。因此"单 AgentOS + 多 Process"方案**无法实现工具隔离**。两个 AgentOS 实例是正确的。

### 审计 4：`kernel_tools=False` 是否真的跳过工具注册

**✅ 确认。**

`agentos.py` 第 164 行：

```python
if self.config.kernel_tools:
    from nimbus.tools import BASH_TOOL, EDIT_TOOL, READ_TOOL, WRITE_TOOL
    kernel_tools_list = [READ_TOOL, WRITE_TOOL, EDIT_TOOL, BASH_TOOL]
```

当 `kernel_tools=False` 时，整个 `if` 块被跳过，不注册任何 kernel tool。配合 `register_default_tools(os, tools=["Read", "Bash"])`，可以精确控制 Core 的工具集。

### 审计 5：两个 AgentOS 实例的实际 overhead

**部分成立。**

每个 AgentOS 创建以下组件：
- `ToolRegistry`（轻量，字典）
- `Scheduler` + `EventStream`（轻量）
- `SessionManager`（可选，可关闭）
- `CompactionEngine`（含 `DefaultCompactionLLM` 引用，轻量）

总内存开销 < 1MB。在 benchmark 场景下（单任务、串行执行）**不构成瓶颈**。Executor 的 AgentOS 可用 `enable_session=False` 进一步精简。

### 审计结论汇总

| 审计项 | Claude Opus | GPT-5.2 | Gemini 3 Pro |
|--------|:-----------:|:-------:|:------------:|
| `register_tool` 是否存在 | ✅ 正确（存在） | ❌ **错误**（声称不存在） | ✅ 正确（存在） |
| 选择性工具注册 | ✅ | ✅ | ✅ |
| 单 AgentOS 能否做工具隔离 | ❌ 建议错误 | ✅ 判断正确 | ✅ 判断正确 |
| `kernel_tools=False` 有效性 | ✅ | ✅ | ✅ |
| 两实例 overhead 可接受 | ✅ | ✅ | ✅ |

---

## 五、修正后的预期通过率

综合三位专家意见和主席的独立判断，对提案第五节的对照分析做修正：

| 任务 | 当前 | 提案预期 | 修正预期 | 依赖条件 | 说明 |
|------|:----:|:-------:|:-------:|----------|------|
| fibonacci-server | ✅ | ✅ | ✅ | 无 | 已通过，保持 |
| kv-store-grpc | ❌ | ✅ 高 | ⚠️ 中 | **Bash Daemon** | 无 Daemon 则 Executor 同样卡在服务启动超时 |
| pypi-server | ✅ | ✅ | ✅ | 无 | 已通过，保持 |
| polyglot-c-py | ❌ | ✅ 高 | ✅ 高 | 无 | workspace diff 能发现编译产物，Dispatch 修复 |
| pcap-to-netflow | ❌ | ❌/✅ 低 | ❌ | — | 领域知识问题，双智能体不解决 |
| add-benchmark-lm-eval | ❌ | ❌ 低 | ❌ | — | 框架知识问题，双智能体不解决 |
| swe-bench-langcodes | ❌ | ✅ 高 | ❌ | **Python 版本兼容** | 与双智能体架构无关，是基础设施问题 |
| build-cython-ext | ❌ | ✅ 高 | ✅ 高 | 无 | Core 全局 grep 能发现所有需修改文件 |
| *(第9个任务)* | — | — | — | — | 按提案数据 |

**修正后预期**：
- **仅 Dual-Agent**：4/9（44%）— polyglot + build-cython + 2 个已通过
- **Dual-Agent + Bash Daemon**：5/9（56%）— 加上 kv-store-grpc
- **Dual-Agent + Bash Daemon + Python 兼容修复**：6/9（67%）— 加上 swe-bench-langcodes

**结论**：提案预期的 56-67% 是**可达到的**，但前提是 Bash Daemon 和 Python 兼容同步推进。仅靠 Dual-Agent 单独实施，预期通过率为 44%——仍然是从 22% 的 2 倍提升，有意义。

---

## 六、最终裁定：需修订的 4 项

### 修订 1（P0）：明确 AgentOS 实例化策略及代码骨架

将提案伪代码升级为基于实际 API 的可编译代码骨架：

```python
class DualAgentOrchestrator:
    def __init__(self, llm_client, workspace: Path, config=None):
        self.workspace = workspace
        
        # Core: 只读工具 + Dispatch + Verify
        core_config = AgentOSConfig(
            kernel_tools=False,  # 不自动注册 Read/Write/Edit/Bash
            system_rules=CORE_SYSTEM_PROMPT,
            vcpu_config=VCPUConfig(max_iterations=20),
            enable_session=False,
        )
        self._core_os = AgentOS(llm_client=llm_client, config=core_config)
        register_default_tools(self._core_os, workspace=workspace, tools=["Read", "Bash"])
        self._core_os.register_tool("Dispatch", self._dispatch, description="...")
        self._core_os.register_tool("Verify", self._verify, description="...")
        
        # Executor: 全权限工具
        executor_config = AgentOSConfig(
            kernel_tools=True,  # 自动注册全部工具
            system_rules=EXECUTOR_SYSTEM_PROMPT,
            vcpu_config=VCPUConfig(max_iterations=25),
            enable_session=False,
        )
        self._executor_os = AgentOS(llm_client=llm_client, config=executor_config)
```

### 修订 2（P0）：补充 Dispatch 的上下文自动注入

多轮 Dispatch 时自动附加上一轮修改文件的当前内容到 `context` 参数，避免 Executor 浪费迭代重新 Read：

```python
async def _dispatch(self, task: str, context: str = "") -> str:
    # 自动注入上次修改的文件内容
    if self._last_dispatch_diff:
        injected = self._build_file_context(self._last_dispatch_diff)
        context = injected + "\n" + context
    
    snapshot_before = self._snapshot_workspace()
    result = await self._executor_os.run(task + "\n\n## Context\n" + context)
    snapshot_after = self._snapshot_workspace()
    
    self._last_dispatch_diff = self._diff_snapshots(snapshot_before, snapshot_after)
    return self._format_result(result, self._last_dispatch_diff)
```

### 修订 3（P1）：补充与 Bash Daemon 的依赖关系和实施顺序

在提案中新增一节，明确：

```
实施顺序（修订）：
├── Phase 0 (Day 1-2): Bash Daemon 模式 — 解决后台服务启动问题
├── Phase 1 (Day 2-4): DualAgentOrchestrator 核心实现
├── Phase 2 (Day 4-5): Core/Executor prompt 调优 + Verify 工具
├── Phase 3 (Day 5-6): Python 版本兼容修复 + Docker 多版本
└── Phase 4 (Day 6-7): terminal-bench 回归测试
总工时：7 天
```

不将 Bash Daemon 纳入本提案 scope，但明确其为**并行前置依赖**，并在预期通过率表格中标注依赖关系。

### 修订 4（P1）：增加时间预算管理和 Dispatch 次数限制

```python
# Dispatch 调用时注入时间预算
async def _dispatch(self, task, context="", max_dispatch_count=3):
    if self._dispatch_count >= max_dispatch_count:
        return "[Error] 已达最大 Dispatch 次数，请基于当前结果做最终判断"
    
    elapsed = time.time() - self._start_time
    remaining = self._total_budget - elapsed
    time_hint = f"\n⏱ 剩余时间预算：{int(remaining)}s，请高效完成。"
    
    self._dispatch_count += 1
    # ...
```

---

## 七、对四个决策问题的委员会最终回复

| # | 决策问题 | 委员会建议 | 投票 |
|---|---------|-----------|------|
| 1 | Core + Executor 双角色是否合理？需要第三角色吗？ | **双角色合理，暂不需要第三角色**。先验证收益，v0.4 再考虑 Explorer 角色。 | 3:0 一致 |
| 2 | Core 的 Bash 限制选项？ | **选项 C（白名单）**。~20 行代码，安全性显著优于 prompt 约束。 | 2:1（Claude Opus 选 A） |
| 3 | 先做 dual-agent 还是先做基础改进？ | **并行推进**。Bash Daemon 是 Dual-Agent 兑现承诺的前置依赖，两者应同步开发。 | 3:0 一致 |
| 4 | 额外 token 成本可接受？ | **可接受**。修正估算为 80-150%（非原始的 40-60%），但 2x 成本换 2-3x 通过率提升，ROI 合理。建议增加简单任务识别机制，自动 fallback 到 single-agent。 | 3:0 一致 |

---

## 八、专家可信度评估

基于本次交叉审计，主席对三位专家的代码级判断准确度做如下评估：

| 专家 | 代码准确度 | 架构判断 | 说明 |
|------|:---------:|:-------:|------|
| Claude Opus 4.5 | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 唯一的误判是建议"单 AgentOS + 多 Process"（不可行），但其他代码引用准确，架构视野最全面 |
| GPT-5.2 | ⭐⭐⭐ | ⭐⭐⭐⭐ | `register_tool` 不存在的断言**错误**，由此衍生的"硬伤 3"不成立；但 Executor context 丢失问题的分析深入 |
| Gemini 3 Pro | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 代码引用准确，`kernel_tools=False` 方案和 Verify 工具分析到位；但对实施顺序的建议过于保守 |

---

## 九、总结

### 提案优势
1. 问题诊断从数据出发，根因分析准确
2. 架构选择务实——编排层而非内核改动
3. kv-store-grpc 的完整 trace 示例有很强的说服力
4. 风险分析覆盖了主要场景

### 提案不足
1. 预期通过率乐观，未充分考虑 Bash Daemon 依赖
2. 多轮 Dispatch 的 context 丢失问题未处理
3. 缺少时间预算管理机制
4. 成本估算偏低

### 最终一句话

> 方案方向正确、架构设计务实，是从"能用"到"好用"的关键一步。完成上述 4 项修订后即可开工——但请与 Bash Daemon 并行推进，否则承诺的收益只能兑现一半。

---

**主席签署**：AI 评审委员会  
**日期**：2026-02-05
