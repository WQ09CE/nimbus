# AI Council Report:  方案已写入：

 ```
   nimbus/docs/proposal-dual-agent-orchestration.md
 ```

 文档结构：

 1. 问题陈述 — 从评审报告数据出发
 2. 设计目标 — 4 条可衡量的目标
 3. 架构设计 — Core/Executor 角色定义、工具集、Dispatch/Verify 工具的完整接口和伪实现
 4. 交互流程 — 以 kv-store-grpc 为例的完整 trace
 5. 实现方案 — 基于现有 AgentOS 的 DualAgentOrchestrator，不改内核
 6. 对照分析 — 逐个分析 5 个失败任务在新架构下的预期表现
 7. 成本分析 — API 调用量、延迟、工作量估算
 8. 风险与缓解 — 5 个主要风险
 9. 决策请求 — 4 个需要委员会拍板的问题

 拿去评审吧。 帮我评审一下这个放哪

2026/2/5 23:48:16

### Opinion from Claude Opus 4.5 (Deep Thinker)

好的，我已经完整阅读了提案全文、评审委员会报告、terminal-bench 测试报告、现有多智能体架构设计文档，以及 AgentOS 核心代码。下面给出专业评审意见。

---

## 📋 评审报告：Dual-Agent 编排架构提案

**评审人**：AI 架构顾问  
**评审日期**：2026-02-05  
**评审对象**：`nimbus/docs/proposal-dual-agent-orchestration.md`  
**总体评价**：**有条件通过（Conditional Approve）**

---

### 一、总体判断

这是一份**质量上乘的提案**。问题诊断精准、方案务实、成本估算合理、风险分析完整。特别是以下几点值得肯定：

1. **正确识别了核心矛盾**：不是 prompt engineering 的问题，而是架构层面缺少对抗性验证
2. **正确选择了实现策略**："在 AgentOS 之上的薄编排层"比评审委员会报告中提到的 Verification Gate（侵入 VCPU `_handle_return`）**更干净、更安全**
3. **与现有代码的对齐度高**：利用已有的 `spawn/wait/Process` 机制，不改内核，这比 `docs/design/multi-agent-architecture.md` 中的 vCPU Pool + Coordination Subsystem 方案务实 10 倍

但存在 **5 个需要讨论的设计问题** 和 **3 个实现风险**。

---

### 二、设计问题（需要回答才能落地）

#### 问题 1：Executor 的 Context 隔离是否真的"全新"？

提案 3.3 节说：

> Executor 每次用 **全新的 context**（fresh MMU），不继承 Core 的执行历史

但看 `agentos.py` 的 `spawn()` 实现（line 281-329），每次 spawn 确实创建新的 MMU 实例——这没问题。**但问题在于 LLM client 是共享的**：

```python
# DualAgentOrchestrator.__init__ 中
self._core_os = self._create_core_os(llm_client)     # 同一个 llm_client
self._executor_os = self._create_executor_os(llm_client)  # 同一个 llm_client
```

虽然 Dispatch 是串行的（Core wait Executor），不会并发调用 LLM，但你在提案中实际创建了**两个 AgentOS 实例**。每个 AgentOS 实例都会创建自己的 Scheduler、EventStream 等。这意味着：

- **两个 AgentOS 的进程表是隔离的**——Core 看不到 Executor 的进程，反之亦然
- **Session 持久化是否冲突？** 如果两个 AgentOS 都写同一个 `session_dir`，会出问题

**建议**：明确 `DualAgentOrchestrator` 是创建两个独立的 AgentOS 实例，还是在同一个 AgentOS 中 spawn 两个不同 role 的 Process。后者更符合 AgentOS 已有的设计意图，也更简单——你只需要在 `spawn()` 时传不同的 tools 和 system_rules。

#### 问题 2：Dispatch 的 "workspace diff" 在大项目中的性能

提案中 `_snapshot_workspace()` 做 "file list + mtime" 快照。在 terminal-bench 的 `/app` 目录下这不是问题（通常 < 100 文件）。但如果 nimbus 的目标场景不仅仅是 terminal-bench：

- **node_modules** 等目录会让 snapshot 变得极慢
- **mtime 精度问题**：某些文件系统的 mtime 精度只到秒，如果 Executor 在同一秒内修改多个文件可能漏报

**建议**：在 `_snapshot_workspace` 中增加 `.gitignore`-aware 的过滤逻辑（或显式的 exclude 列表）。优先使用 `git status --porcelain` 如果工作区是 git repo。

#### 问题 3：Verify 工具的定位模糊

Verify 工具定义了 6 种 check type，全部是确定性检查。但 **Core 本身已经有只读的 Bash**——Core 完全可以用 `Bash("test -f /app/xxx && echo ok")` 做同样的事。

Verify 工具的真正价值应该是：
1. **结构化输出**：✅/❌ 而不是 Bash 的原始输出——这让 LLM 更容易判断
2. **原子性**：多个检查同时执行，一次返回——减少 LLM 迭代次数
3. **安全性**：比 Bash 更受控

但目前提案里 Core 同时拥有 Bash 和 Verify，LLM 可能会"选择困难"，或者在 Verify 不够用时退化为 Bash。

**建议**：
- 方案 A（推荐）：保留 Verify，但在 Core system prompt 中明确说明优先级——"验证时优先使用 Verify，只有 Verify 不能覆盖的场景才用 Bash"
- 方案 B：直接去掉 Verify，让 Core 用 Bash 验证。更简单，但失去了结构化输出的优势

#### 问题 4：Core 的 max_iterations 预算分配

提案给 Core 和 Executor 各 30 iterations。但实际上：

- Core 的每次 Dispatch 会"消耗"Executor 的 20+ iterations 时间
- 如果 Core 做 3 次 Dispatch（初始 + 2 次修正），实际总迭代可能超过 90
- terminal-bench 有总时间限制（通常 10-20 分钟），过多的 Dispatch 轮次会超时

**建议**：
- 给 Core 设 `max_iterations=20`（Core 的工作主要是读、分发、验证，不需要 30 轮）
- 给 Executor 设 `max_iterations=25`
- 在 Dispatch 中传入 `remaining_time` 参数，让 Executor 感知时间预算
- 限制 Dispatch 最大次数（如 3 次），超过则 Core 必须接受当前结果

#### 问题 5：与评审委员会 Phase 1 建议的优先级关系

评审委员会建议的路线是：

> Phase 1（1-2天）: Bash Daemon + Docker 兼容 → Phase 2（3-5天）: Verification Gate + Post-Fix Sweep

本提案跳过了 Phase 1，直接做 Dual-Agent（相当于评审委员会说的 Phase 3 的 v0.4 方向）。这存在一个问题：

- **Bash Daemon 问题在 Dual-Agent 下仍然存在**：Executor 启动后台服务时，同样会遇到 60s 超时和 `command &` 无法真正后台化的问题
- kv-store-grpc 任务中 17 轮浪费的迭代，在 Dual-Agent 下变成了 Executor 浪费的迭代——问题没变，只是从 Core 转移到了 Executor

**建议**：Dual-Agent 和 Bash Daemon 应该**并行推进**，或者 Bash Daemon 优先。否则你的预期通过率会偏乐观——kv-store-grpc 的"高信心"假设了 Executor 不会在 Bash 超时问题上浪费时间。

---

### 三、实现风险

#### 风险 1：两个 AgentOS 实例的资源开销

当前 `AgentOS.__init__` 会创建 Scheduler、EventStream、SessionManager、CompactionEngine 等。两个实例意味着双倍的初始化和内存占用。虽然在 terminal-bench 场景下不是瓶颈，但如果未来扩展到并行 Dispatch（提案 v0.4 规划），可能需要重新考虑是否用"单 AgentOS + 多 Process"的方式。

**建议**：在 `DualAgentOrchestrator` 的实现中，给 Executor 的 AgentOS 用轻量配置（关闭 session 持久化、关闭 compaction），减少开销。

#### 风险 2：Core 的"只读 Bash"约束不够硬

提案推荐选项 A（prompt 约束）。我同意这在 benchmark 场景下够用。但有一个边界 case：

如果 Core 用 Bash 运行验证脚本（如 `python3 -c "import kv_store_pb2; ..."`），而这个 import 触发了 `.pyc` 文件的生成——这算"写"操作吗？技术上是，但影响为零。

**结论**：选项 A 确实够用，不需要在这里多花精力。

#### 风险 3：预期通过率的乐观偏差

提案预期 56-67%。我逐个审核：

| 任务 | 提案预期 | 我的评估 | 理由 |
|------|---------|---------|------|
| fibonacci-server | ✅ 确定 | ✅ | 同意 |
| kv-store-grpc | ✅ 高 | ⚠️ 中高 | 如果不修 Bash Daemon，Executor 仍会浪费大量迭代在启动服务上，可能导致时间不足 |
| pypi-server | ✅ 确定 | ✅ | 同意 |
| polyglot-c-py | ✅ 高 | ✅ | 同意，workspace diff 能发现 cmain |
| pcap-to-netflow | ❌/✅ 低 | ❌ | 同意不乐观 |
| add-benchmark-lm-eval | ❌ 低 | ❌ | 同意 |
| swe-bench-langcodes | ✅ 高 | ⚠️ 中 | 这是 Docker 安装兼容性问题，Dual-Agent 不能解决。需要配合 Phase 1 的 Docker 修复 |
| build-cython-ext | ✅ 高 | ✅ | Core 全局扫描确实能解决 |

**修正后预期**：4-5/9（44-56%），仍然是从 22% 的显著提升，但可能达不到 56-67%。**加上 Bash Daemon + Docker 修复后**，才有可能到 56-67%。

---

### 四、文档质量评价

| 维度 | 评分 | 说明 |
|------|------|------|
| 问题陈述 | ⭐⭐⭐⭐⭐ | 从数据出发，论证清晰 |
| 架构设计 | ⭐⭐⭐⭐ | 角色定义明确，但两个 AgentOS 实例的方式需要讨论 |
| 交互流程 | ⭐⭐⭐⭐⭐ | kv-store-grpc 示例非常具体，有说服力 |
| 伪代码 | ⭐⭐⭐⭐ | 清晰可读，但 Dispatch 的错误处理路径缺失 |
| 对照分析 | ⭐⭐⭐⭐⭐ | 逐任务分析，有表格有论据 |
| 成本分析 | ⭐⭐⭐⭐ | 合理但略粗糙，"40-60% token 增加"的依据不够 |
| 风险分析 | ⭐⭐⭐⭐ | 覆盖了主要风险，但缺少"与 Phase 1 的依赖关系"这个风险 |

---

### 五、最终建议

#### 通过条件

1. **补充与 Phase 1 的关系**：明确 Dual-Agent 和 Bash Daemon/Docker 修复的先后关系或并行策略
2. **确认实例模式**：是两个独立 AgentOS 实例还是单 AgentOS 多 Process——我建议用后者，更轻量
3. **增加 Dispatch 的时间预算管理**：限制最大 Dispatch 次数，传入 remaining_time

#### 建议的实施顺序

```
Week 1, Day 1-2:  Bash Daemon 模式（解决所有启动服务类任务的工具层瓶颈）
Week 1, Day 2-3:  Docker 多版本兼容修复（解锁 swe-bench-langcodes）
Week 1, Day 3-5:  DualAgentOrchestrator 核心实现（Dispatch + 串行执行）
Week 2, Day 1-2:  Core/Executor system prompt 调优 + Verify 工具
Week 2, Day 3:    terminal-bench 回归测试
```

总工时约 7-8 天（比提案估算的 4-5 天多，因为加入了 Phase 1 的前置工作）。

#### 对决策请求的回应

| 问题 | 我的建议 |
|------|---------|
| 1. 是否需要第三个角色？ | **不需要**。先跑通 Core+Executor，验证收益后再说 |
| 2. Bash 限制选项 A 还是 B？ | **选项 A**。但在 Core prompt 中加一句 "绝对不要用 Bash 执行 rm/mv/write 类命令" |
| 3. 先做 dual-agent 还是先做基础改进？ | **并行做**。Bash Daemon 是前置依赖，必须先完成或同步推进 |
| 4. 40-60% 额外 token 成本可接受？ | **可接受**。benchmark 场景下正确率的收益远大于成本。建议加个 fallback：如果简单任务被识别出来，直接走 single-agent |

---

**总结一句话**：方案方向正确、设计务实，但需要补上 Bash Daemon 的前置依赖，并确认 AgentOS 实例化策略后再开工。


### Opinion from GPT-5.2 (Generalist)

好了，我已经完整阅读了提案文档、评审委员会报告、terminal-bench 测试报告，以及 AgentOS/VCPU/KernelGate 的实际代码。以下是我的专业评审意见。

---

# 📋 提案评审：Dual-Agent 编排架构

**评审人**：AI 架构顾问  
**评审对象**：`nimbus/docs/proposal-dual-agent-orchestration.md`  
**评审日期**：2026-02-05  
**总体评价**：**方向正确，但存在 3 个架构级硬伤和若干工程细节问题需要修正后方可实施。**

---

## 一、肯定的部分（做得好的）

### 1.1 问题诊断精准
提案从评审委员会报告的数据出发，准确识别了"自验证闭环"是根因。用 `value` vs `val` 这种具体案例说明 confirmation bias，比抽象论证有说服力。

### 1.2 "不改内核"的约束非常好
提案明确定义了边界——`DualAgentOrchestrator` 是 AgentOS 之上的编排层，不修改 VCPU/MMU/Gate。我看了 `agentos.py` 的 `spawn()`/`wait()` 接口，确实可以支撑这个方案。这个约束降低了风险。

### 1.3 Verify 工具设计合理
确定性检查（file_exists, file_contains, command_succeeds, port_listening）是投入产出比最高的验证手段。不依赖 LLM 判断，结果确定。这和评审委员会"L0 确定性检查先行"的建议一致。

### 1.4 交互流程 trace 很清晰
第 3.5 节以 kv-store-grpc 为例的完整 trace 让人一目了然。这种"show, don't tell"的写法比纯文字描述好很多。

---

## 二、架构级硬伤（必须解决）

### 🔴 硬伤 1：Executor 的 context 隔离导致"历史丢失"问题

提案声称：

> Executor 每次用**全新的 context**（fresh MMU），不继承 Core 的执行历史

这在理论上很美——打破 confirmation bias。但实际上会导致严重问题：

**场景**：Core 第一次 Dispatch 让 Executor 安装 grpcio、写 proto、生成代码、写 server.py。Core 验证发现字段名错误，第二次 Dispatch 让 Executor 修复 proto 并重新生成。

问题来了——第二个 Executor **没有第一个 Executor 的任何记忆**。它不知道：
- grpcio 已经装了（可能重复安装浪费时间）
- proto 文件在哪里、当前内容是什么（需要重新 Read）
- server.py 的结构和依赖关系

这意味着 Core 的 Dispatch 指令必须**极其完备**——把所有上下文都塞进 `task` 和 `context` 参数。但这本质上把"context 管理"的负担从 MMU 转移到了 Core 的 prompt，而 Core 是 LLM，它的 context 组织能力并不比 Executor 更强。

**建议**：
- **方案 A（推荐）**：同一个任务的多轮 Dispatch 复用同一个 Executor 实例（保留 MMU 状态），只在"全新子任务"时创建新 Executor。这需要在 Dispatch 中加一个 `reuse_executor: bool` 参数。
- **方案 B**：保持 fresh context，但 Dispatch 自动注入上一轮 Executor 的"文件变更摘要"和"关键文件当前内容"到 context 中——相当于自动化地做 context 准备。

### 🔴 硬伤 2：提案完全没有处理评审委员会共识 C（Bash Daemon 模式）

评审委员会报告中，三位专家**一致**指出 Bash 的 60s 超时 + 无后台进程支持是 kv-store-grpc 浪费 17 轮迭代的直接原因。这是"完全共识 C"。

但这个提案的 Executor 仍然使用现有的 Bash 工具——同样的 `asyncio.create_subprocess_shell` + PIPE + 60s 超时。**即使有了 Dual-Agent 架构，Executor 启动 gRPC server 时仍然会遇到完全相同的 Bash 超时问题。**

提案第八节"后续演进路径"里把 Bash Daemon 放到了 v0.3.x，但现实是：**没有 Bash Daemon，kv-store-grpc 的 7/7 预期就无法兑现**。你不能一边在第五节声称"预期 7/7"，一边把实现这个预期的前置依赖放到"后续"。

**建议**：要么把 Bash Daemon 纳入本提案的 scope，要么把 kv-store-grpc 的预期降到"5-6/7"并注明依赖 Bash Daemon。诚实比乐观更重要。

### 🔴 硬伤 3：DualAgentOrchestrator 的伪代码与实际 AgentOS API 不匹配

我看了 `agentos.py` 的实际代码：

```python
class AgentOS:
    def __init__(self, llm_client, tools=None, config=None):
        # tools 在 __init__ 时注册，不支持后续动态注册
```

提案伪代码写的是：

```python
os.register_tool("Dispatch", self._dispatch, ...)
```

但 `AgentOS` 没有 `register_tool` 方法。工具是通过构造函数的 `tools` 参数或 `config.kernel_tools` 注册的。你需要：

1. 要么通过 `tools={"Dispatch": self._dispatch, "Verify": self._verify}` 在构造时传入
2. 要么在 `AgentOS` 上新增 `register_tool` 方法（但你承诺了"不改内核"）

另外，`AgentOS` 的 `spawn()` 方法创建的 Process 共用同一个 `self._tools` registry。如果 Core 和 Executor 是同一个 AgentOS 实例下的两个 Process，它们会共享工具集——这和提案设计的"Core 没有 Write/Edit，Executor 没有 Dispatch/Verify"矛盾。

提案中用"分别创建两个 AgentOS 实例"来解决，但两个 AgentOS 实例意味着两套独立的 session 管理、event stream、scheduler——overhead 比提案描述的"薄编排层"要大。

**建议**：提案需要给出**可编译的**实现方案，而不是伪代码。具体来说：
- Core 的 AgentOS 初始化时 `kernel_tools=False`，只通过 `tools=` 传入 Read + Bash + Memo + Dispatch + Verify
- Executor 的 AgentOS 初始化时 `kernel_tools=True` 使用默认全部工具
- 明确说明两个 AgentOS 实例的生命周期管理

---

## 三、设计层面的中等问题

### 🟡 问题 4：Core 的 Bash "只读"策略太模糊

提案推荐选项 A（prompt 约束），理由是"LLM 遵守率足够"。但在 benchmark 场景下，**一次违规就可能导致任务失败**。例如 Core 如果意外执行了 `rm -rf /app/generated/*` 来"清理验证环境"，Executor 的产出就被破坏了。

更重要的是，选项 B 的黑名单方案也确实不完备。真正安全的方案是：

**建议**：**选项 C——白名单模式**。只允许 `grep`, `find`, `ls`, `cat`, `head`, `tail`, `wc`, `file`, `python3 -c`, `diff`, `stat`, `which`, `echo` 等确定性只读命令。用正则匹配命令前缀，不在白名单内的返回错误提示。这比黑名单安全得多，实现量也就 ~20 行。

### 🟡 问题 5：Dispatch 是串行阻塞的，但未考虑超时后的恢复

提案提到了 Dispatch 有 `timeout=300` 秒，但没有说明：
- Executor 超时被 kill 后，工作目录可能处于中间状态（文件写了一半、server 启动了一半）
- Core 收到超时错误后应该怎么做？重新 Dispatch？还是 abort 整个任务？

terminal-bench 的总时间限制通常是 300-600 秒。如果第一次 Dispatch 耗了 300 秒超时，再来一次就可能超出总时间。

**建议**：
1. Dispatch 超时后自动做 workspace snapshot diff，告诉 Core "Executor 超时了，但它已经完成了这些文件变更"
2. Core 根据 diff 判断是"继续完成剩余工作"还是"回滚重来"
3. 加入全局时间预算管理（time_budget_remaining 作为 Core 的 context）

### 🟡 问题 6：成本估算过于乐观

提案说"总 token 成本增加约 40-60%"，理由是"Executor 的 context 更小"。但实际上：

1. **Core 的 context 不小**：Core 需要读需求、grep 项目结构、读 Dispatch 返回的结果、读文件验证——这些都累积在 Core 的 MMU 中
2. **每次 Dispatch 等于启动一次完整的 agent run**：Executor 的 15-20 轮迭代各自有 system prompt + 逐轮累积的 context
3. **反馈循环意味着多次 Dispatch**：kv-store-grpc 的例子中就有 2 次 Dispatch

更合理的估算：**token 成本增加 80-150%**，调用次数增加 75% 只是下限。

这不是说不应该做——如果能把通过率从 22% 提到 55%+，2 倍成本完全可以接受。但应该如实估算。

---

## 四、细节问题

### 🟢 问题 7：Verify 工具缺少几个实用 check type

当前 6 种 check type 不够覆盖 terminal-bench 的验证场景。建议增加：
- `command_output_contains`：命令输出包含特定字符串（比 command_succeeds 更精细）
- `process_running`：检查特定进程是否在运行（`pgrep -f "python server.py"`）
- `file_matches_regex`：正则匹配（比 file_contains 的纯字符串匹配更强）

### 🟢 问题 8：第五节对照分析中 swe-bench-langcodes 的"高信心"缺乏依据

提案说 swe-bench-langcodes "预期 ✅，信心高（配合安装修复）"。但这个任务失败的原因是 Python 3.9 不兼容 nimbus 的 `requires-python >= 3.10`。这是**基础设施问题**，和 Dual-Agent 架构无关。即使有 Core + Executor，nimbus 还是装不上。

**建议**：把 swe-bench-langcodes 标记为"依赖 Python 版本兼容修复，与本提案无关"，不计入预期提升。修正后预期通过率：**4-5/9（44-56%）**，仍然是显著提升。

### 🟢 问题 9：文档结构建议

- 第三节和第四节有重叠（3.5 交互流程 vs 四、Core 的 Bash 限制策略）——第四节的编号应该是 3.6 还是独立一节？目前的 Markdown 结构里第四节标题是"Core Agent 的 Bash 限制策略"但实际是提案的第四大节，和"四、交互流程"冲突
- 建议加一节 **"与评审委员会建议的对照表"**——逐条列出委员会的共识/建议，标注本提案覆盖了哪些、没覆盖哪些

---

## 五、评审结论

| 维度 | 评分 | 说明 |
|------|------|------|
| 问题诊断 | ⭐⭐⭐⭐⭐ | 从数据出发，根因分析准确 |
| 架构方向 | ⭐⭐⭐⭐ | Core/Executor 分离是正确思路 |
| 实现可行性 | ⭐⭐⭐ | 伪代码与实际 API 不匹配，需重写实现方案 |
| 预期收益 | ⭐⭐⭐ | 过于乐观，需修正 swe-bench 和 kv-store 的预期 |
| 成本分析 | ⭐⭐ | 显著低估，需上调到 80-150% |
| 风险覆盖 | ⭐⭐⭐ | 缺少 Executor 超时恢复、context 丢失等关键风险 |
| 文档质量 | ⭐⭐⭐⭐ | 结构清晰，trace 示例优秀，但章节编号有混乱 |

### 最终建议：**有条件通过，修订后实施**

需要修订的 3 个硬伤：
1. ✏️ 解决多轮 Dispatch 的 Executor context 丢失问题（引入 `reuse_executor` 或自动 context 注入）
2. ✏️ 要么纳入 Bash Daemon，要么诚实调低 kv-store-grpc 预期
3. ✏️ 基于实际 AgentOS API 重写 DualAgentOrchestrator 的实现方案（不是伪代码，是可以直接 PR 的代码骨架）

**对委员会四个决策问题的建议回复**：
1. **架构方案**：Core + Executor 双角色合理，暂不需要第三角色。但需增加 "Executor 复用" 机制。
2. **Core 的 Bash 限制**：选项 C（白名单），既非信任 prompt 也非不完备的黑名单。
3. **实现优先级**：Bash Daemon 和 Dual-Agent 应该**并行推进**，因为 Dual-Agent 依赖 Bash Daemon 来兑现其承诺的收益。
4. **成本接受度**：80-150% 的额外成本换 2-3 倍通过率提升，ROI 合理，可以接受。


### Opinion from Gemini 3 Pro High (Reasoning)

好，我已经完整读完了提案文档、评审委员会报告、benchmark 测试报告以及 AgentOS 源码。以下是我的专业评审意见。

---

## 🔍 评审报告：Dual-Agent 编排架构提案

**评审人**：AI 技术顾问  
**评审对象**：`nimbus/docs/proposal-dual-agent-orchestration.md`  
**评审结论**：**有条件通过** — 方向正确，但有 3 个架构硬伤需要修改后才能进入实施

---

### 一、做得好的部分 ✅

**1. 问题诊断精准**

从 benchmark 数据出发，准确识别了"confirmation bias"是架构层面问题而非 prompt engineering 问题。"自验证闭环"这个判断与评审委员会三位专家的共识完全一致。

**2. "不改内核"的约束设得好**

提案明确声明不修改 `VCPU/MMU/Gate`，作为编排层叠加在 `AgentOS` 之上。我验证了现有代码——`AgentOS.spawn()` 和 `AgentOS.wait()` 确实已经具备了这种能力，提案没有臆造不存在的接口。

**3. kv-store-grpc 的 trace 示例说服力强**

第 3.5 节用完整的 trace 展示了 `value` vs `val` 问题如何被 Core 的独立验证捕获，这比抽象论述有力得多。

**4. 成本分析诚实**

没有回避"40-60% 额外 token 成本"和"75% API 调用增加"，这是好的。

---

### 二、架构硬伤 🚨（需修改）

#### 硬伤 1：两个 AgentOS 实例共享 `ToolRegistry` 的隐患

提案伪代码中：

```python
self._core_os = self._create_core_os(llm_client)
self._executor_os = self._create_executor_os(llm_client)
```

但回到 `agentos.py` 源码（第 164-170 行），`AgentOS.__init__` 会自动注册 **全部** kernel tools（Read/Write/Edit/Bash）：

```python
if self.config.kernel_tools:
    kernel_tools_list = [READ_TOOL, WRITE_TOOL, EDIT_TOOL, BASH_TOOL]
```

你的 `_create_core_os` 里写了 `tools=["Read", "Bash"]`，但 **AgentOS 构造函数没有这个 selective registration 接口**。你需要：

- 要么给 `AgentOSConfig` 加一个 `kernel_tool_whitelist` 参数
- 要么在 Core OS 创建后手动 `unregister("Write")` 和 `unregister("Edit")`
- 要么用 `kernel_tools=False` 然后手动注册需要的工具

这不是小问题——如果 Core Agent 在 tool list 里看到 `Write` 和 `Edit`，LLM **会用它们**，prompt 约束挡不住。

**建议**：选第三种方案，`kernel_tools=False` + 手动注册。代码量最小，语义最清晰。

#### 硬伤 2：Executor 的 "fresh context" 与 workspace 状态不 fresh 的矛盾

提案反复强调 Executor 每次用"全新的 context"，这是对的。但有一个被忽略的问题：**workspace 的文件系统状态是跨 Dispatch 累积的**。

考虑这个场景：
1. Dispatch #1: "创建 proto 文件" → Executor 成功
2. Core 验证发现字段名错误
3. Dispatch #2: "修复 proto 字段" → Executor 拿到 fresh context，但它需要先 Read 已有的 proto 文件才能修改

问题在于：Dispatch #2 的 task 描述必须足够详细，让 Executor 知道文件的当前状态。但 **Core 看到的 Executor 产出只是文本总结 + 文件变更列表**（见 3.3 节设计），Core 不一定知道 proto 文件的完整内容。

**建议**：在 Dispatch 工具的 `context` 参数中，**自动附加上一次 Dispatch 修改过的文件的当前内容**（至少是关键文件的 head/tail）。或者让 Core 在验证阶段 `Read` 完文件后，主动将相关内容传入下一次 Dispatch 的 context。提案应该明确这个最佳实践。

#### 硬伤 3：Verify 工具过于原始，真正需要的验证它做不了

看看 benchmark 里的实际失败模式：

| 失败 | Verify 能抓到吗？ |
|------|----------------|
| `value` vs `val` 字段名 | ✅ `file_contains` 可以 |
| `cmain` 编译产物未删除 | ✅ `file_not_exists` 可以 |
| 时间戳是 2026 而非 2011 | ❌ 需要运行程序 + 解析输出 |
| build-cython-ext 漏改一个文件 | ❌ 需要 `command_succeeds("grep -rn 'np.int[^0-9e]' ...")` 但 Core 自己用 Bash 也能做 |

**核心问题**：Verify 工具提供的 check types 与 Core 已有的 `Bash`（只读）+ `Read` 能力**高度重叠**。真正有价值的是 Verify 做不了的事——运行程序、比对输出、语义检查。

**建议**：
- 要么删掉 Verify 工具，让 Core 直接用 Bash + Read 做验证（减少一个概念）
- 要么把 Verify 升级为**结构化断言工具**，支持 `output_matches`（运行命令并断言输出匹配模式）、`json_field_equals`（解析 JSON 输出并检查字段值）等更高阶的 check type

我倾向前者。Verify 当前的实现本质上是 `os.path.exists()` + `grep` + `socket.connect()` 的包装，Core 用 Bash 一行命令就能做。引入一个功能重叠的新概念会增加认知负担。

---

### 三、设计疑问 🤔（需要澄清）

#### 疑问 1：Core 的 Bash "只读限制" 选项 A 太弱，选项 B 太脆

提案自己也意识到了选项 B 的黑名单不完备。但选项 A 的"信任 LLM"在压力下会失效——当 Core 验证失败、反复重试、接近 iteration 上限时，LLM 有动机直接用 Bash 修文件而不是再走一次 Dispatch。

**建议**：采用**白名单**而非黑名单。只允许 Core 的 Bash 执行以 `grep`、`find`、`ls`、`cat`、`head`、`tail`、`wc`、`python3 -c`、`file`、`stat` 开头的命令。白名单虽然也不完美（`python3 -c` 可以做任何事），但覆盖率远高于黑名单。

#### 疑问 2：Dispatch 的超时策略太简单

提案设 300 秒超时。但 terminal-bench 的总时间限制是多少？如果总限制是 600 秒，而 Core 做了 2 次 Dispatch（每次 300 秒），Core 自己的验证时间就没了。

**建议**：Dispatch 的 timeout 应该是**动态的**——基于剩余时间预算分配。Core 应该感知全局时间预算。

#### 疑问 3：成本预估可能低估了

提案说"总 token 成本增加 40-60%"，理由是 Executor 的 context 更小。但 Core 的 context 也不小——它需要存储多次 Dispatch 的结果、文件内容的 Read 结果、Verify 结果。而且如果 Core 进行 2-3 次 Dispatch-Verify 循环，Core 的 context 会膨胀得很快。

**建议**：做一个更精细的 token 估算，按 kv-store-grpc 的 trace 示例逐轮计算 Core 和 Executor 的 context window 占用。

---

### 四、遗漏问题 ⚠️

#### 遗漏 1：没有讨论错误传播

Executor 失败了怎么办？是返回错误让 Core 重试，还是 Core 换一种方式 Dispatch？Executor 如果陷入 doom loop（你们有 `DoomLoopDetector`），Core 拿到的是什么？

#### 遗漏 2：没有讨论与 Harbor 适配层的具体改动

提案提到要修改 `nimbus_agent.py`，但没有给出接口定义。Harbor 当前期望的是 single-agent 的 `run()` 接口，换成 DualAgentOrchestrator 后，Harbor 那边需要改什么？

#### 遗漏 3：没有 fallback 策略的触发条件

提案提到"保留 single-agent 模式作为 fallback"，但没有定义什么情况下触发 fallback。是配置开关？还是运行时自动判断？如果是自动判断，判据是什么？

#### 遗漏 4：与评审委员会报告中其他建议的关系

评审委员会还提出了 **Bash Daemon 模式**（共识 C）和 **Docker 多版本兼容**（共识 E）。这两个问题在 dual-agent 架构下依然存在。提案第八节的演进路径里提到了 Bash Daemon，但没有讨论优先级关系——如果 Bash Daemon 不做，kv-store-grpc 的 Executor 启动 gRPC server 时同样会遇到后台进程问题，dual-agent 解决不了这个。

---

### 五、评审结论与建议

#### 总体判断：**方向正确，细节需要打磨**

Dual-agent 的核心洞察——**对抗性验证需要独立上下文**——是对的。评审委员会中 GPT-5.2 的"Dual-Agent 验证"独到观点被你们提升为完整方案，逻辑自洽。

#### 修改后可通过的条件：

| # | 修改项 | 优先级 |
|---|--------|--------|
| 1 | 明确 Core OS 的工具注册方式（`kernel_tools=False` + 手动注册），给出可编译的代码 | **P0** |
| 2 | 简化工具设计：删除 Verify，让 Core 用 Bash+Read 做验证；或将 Verify 升级为有差异化价值的断言工具 | **P0** |
| 3 | 补充 Executor 失败时的错误传播与重试策略 | **P1** |
| 4 | 补充 Dispatch 的动态超时策略 | **P1** |
| 5 | 补充 Harbor 适配层的接口变更说明 | **P1** |
| 6 | 补充 fallback 触发条件 | **P2** |
| 7 | 讨论与 Bash Daemon 的依赖关系和优先级 | **P2** |

#### 对九、决策请求的回应：

1. **Core + Executor 双角色是否合理？** → 合理，暂不需要第三个角色。但要注意 Core 不应该成为瓶颈——如果 Core 自身推理出错，整个流程就错了。
2. **Core Bash 限制选项？** → 都不好。建议白名单方案（见疑问 1）。
3. **优先级？** → **先做 Bash Daemon**。不解决后台进程问题，Executor 在 gRPC/server 类任务上同样会失败，dual-agent 不解决这个基础问题。顺序应该是：Bash Daemon → Docker 兼容 → Dual-Agent。
4. **40-60% 额外成本？** → 如果能将通过率从 22% 提到 55%+，这个 ROI 是值得的。但建议做更精细的 token 估算验证这个数字。
