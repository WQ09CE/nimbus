# Nimbus Next — 极简重构架构设计

> 核心原则：如无必要，勿增实体。
> 灵感来源：pi coding agent 的极简哲学 + nimbus 的分层严谨性。

## 1. 设计哲学

### 从 pi 学到的核心教训

| pi 的原则 | nimbus next 的应用 |
|---|---|
| < 1000 token 系统提示 | 精简 system_rules，移除冗余指令 |
| 只有 4 个工具 (read/write/edit/bash) | 核心工具集精简为 5 个：Read/Write/Edit/Bash/Grep |
| 无 Plan Mode | 移除 specialist_tools 中的 Design/Plan |
| 无内置 TODO | 移除 context_tools |
| 无 MCP | 保持不变（nimbus 本就没有） |
| 无后台 Bash | Bash 默认同步，复杂场景用户自行 tmux |
| 无子代理 | 移除 IPC/Mailbox/SpawnSubAgent |
| YOLO 模式 | Gate 保留 doom loop 检测但移除 write_filter |
| 上下文工程 > 功能堆积 | MMU Anchor & Stream 是核心优势，保留并精简 |
| 可观测性 > 抽象 | 移除 Heart daemon，保留 EventStream |

### nimbus 自身的坚持

pi 是一个"扁平"架构，nimbus 的价值在于**严格分层**。Nimbus Next 保留分层但压缩层数：

```
现有 nimbus：7 层
Nimbus Next：4 层
```

---

## 2. 架构对比

### 现有 nimbus（Before）

```
Layer 7: UI (CLI / Web / Specialist Tools)
Layer 6: AgentOS (Facade + Heart + SkillManager + NimFSGC)
Layer 5: Session & Persistence (SessionManager + Coordinator + Checkpoint)
Layer 4: Process Management (ProcessManager + Factory + RuntimeLoop + Compaction)
Layer 3: System Interfaces (Gate + ToolExecutor + DirectAdapter)
Layer 2: Execution Engine (VCPU + FSM States + Decoder + Tracer)
Layer 1: Memory & Context (MMU + StackFrame + PinnedContext + ContextAssembler)
Layer 0: Protocol (ActionIR + ToolResult + Fault + Event + IPC)
```

### Nimbus Next（After）

```
Layer 3: Interface    │ CLI / Web API (薄壳)
Layer 2: Runtime      │ AgentOS (精简 Facade) + RuntimeLoop
Layer 1: Engine       │ VCPU (FSM) + MMU (Anchor & Stream) + Gate + Decoder
Layer 0: Protocol     │ ActionIR + ToolResult + Fault + Event
```

---

## 3. 组件去留清单

### ✅ 保留（核心骨架）

| 组件 | 文件 | 理由 | 精简方向 |
|---|---|---|---|
| **Protocol** | `core/protocol.py` | ISA/ABI 是根基 | 删除 IPCMessage、NimFS helpers |
| **VCPU** | `core/runtime/vcpu.py` | FSM 执行引擎，383 行，已很精简 | 删除 checkpoint、tracer |
| **FSM States** | `core/runtime/states.py` | Think-Act-Observe 状态机 | 保持不变 |
| **Decoder** | `core/runtime/decoder.py` | 幻觉防火墙是关键差异化能力 | 保持不变 |
| **MMU** | `core/memory/mmu.py` | Anchor & Stream 是核心创新 | 删除 NimFS offload、scroll、clipboard |
| **Gate** | `os/gate.py` | 统一执行入口 + 超时 + doom loop | 删除 write_filter、meta tool timeouts |
| **DirectAdapter** | `adapters/direct_adapter.py` | 三通道 LLM 适配 | 重构精简（73KB 太大） |
| **RuntimeLoop** | `core/process/loop.py` | 统一执行循环 | 删除 Heart 报告、NimFS GC |
| **EventStream** | `os/gate.py` | 可观测性 | 保持不变 |

### 🔧 精简合并

| 组件 | 动作 | 原因 |
|---|---|---|
| **AgentOS** | 精简为 ~150 行 | 611 行中大量 proxy 方法、SkillManager、Heart 启动 |
| **ProcessFactory** | 合并入 AgentOS | 独立 factory 是过度抽象 |
| **ProcessManager** | 合并入 AgentOS | 755 行太重，nimbus next 单进程即可 |
| **CompactionService** | 内联到 RuntimeLoop | 独立 service 是过度抽象 |
| **ToolRegistry** | 精简为注册 + 查找 | 移除 CompositeToolRegistry 双层结构 |
| **ModelManifest** | 精简为轻量配置 | 保留 model_id + features，移除 role/tier 体系 |

### ❌ 删除

| 组件 | 理由 |
|---|---|
| **Heart daemon** (heart.py + 3 个 modules) | 后台监控增加复杂性，pi 的理由正确：可观测性应该在前台 |
| **SkillManager** (skills/) | 插件系统是过度工程，工具应该硬编码注册 |
| **NimFS** (nimfs/) | 大输出直接截断即可（Gate 已有 `_truncate_output`） |
| **IPC** (ipc/mailbox, ipc/tools, ipc/subagent) | 多进程通信不需要，pi 证明了直接 bash 启动子任务就够 |
| **Specialist Tools** (orchestration/specialist_tools.py) | Explore/Implement/Design/Test/Dispatch 全部移除 |
| **Context Tools** (tools/context_tools.py) | ScrollHistory、Clipboard 等 |
| **Memo Tools** (tools/memo_tools.py) | 文件系统即记忆 |
| **NimFS Tools** (tools/nimfs_tools.py) | 随 NimFS 一起移除 |
| **Sandbox** (tools/sandbox.py) | 如无必要 |
| **Review Tool** (orchestration/review_tool.py) | 过度工程 |
| **Workspace Diff** (orchestration/workspace_diff.py) | 过度工程 |
| **Context Protocol** (orchestration/context_protocol.py) | 过度工程 |
| **AgentProfile** (core/profile.py) | 角色配置系统过重 |
| **CheckpointManager** | Session 持久化先不做 |
| **StateManager** (memory/state_manager.py) | 确定性状态追踪可以延后 |
| **Tracer** (runtime/tracer.py) | 调试追踪延后 |

---

## 4. Nimbus Next 核心工具集

遵循 pi 的 "4 工具" 哲学，Nimbus Next 的核心工具集：

| 工具 | 对应 pi | 说明 |
|---|---|---|
| **Read** | read | 读文件/图片，支持 offset/limit |
| **Write** | write | 创建/覆写文件，自动创建父目录 |
| **Edit** | edit | 精确文本替换（old_text 必须精确匹配） |
| **Bash** | bash | 同步命令执行，有超时 |
| **Grep** | (bash 的子集) | 内容搜索，比 bash grep 更安全高效 |

额外考虑（可选）：
- **Glob**: 文件模式搜索（可用 Bash `find` 替代，但更安全）

所有其他功能（搜索、浏览、规划、记忆）都通过这 5 个基础工具的组合实现。

---

## 5. Nimbus Next 代码结构

```
src/nimbus_next/
├── __init__.py
├── protocol.py          # ActionIR, ToolResult, Fault, Event (~100 行)
├── mmu.py               # MMU: Anchor & Stream (~400 行)
├── vcpu.py              # VCPU: FSM 执行引擎 (~300 行)
├── decoder.py           # InstructionDecoder: 幻觉防火墙 (~250 行)
├── gate.py              # KernelGate: 工具执行 + 超时 + doom loop (~200 行)
├── adapter.py           # DirectAdapter: LLM 适配 (重构精简到 ~500 行)
├── loop.py              # RuntimeLoop: 执行循环 (~200 行)
├── agent.py             # AgentOS: 精简 Facade (~150 行)
├── tools/
│   ├── __init__.py
│   ├── registry.py      # ToolRegistry: 注册 + 查找 (~100 行)
│   ├── read.py
│   ├── write.py
│   ├── edit.py
│   ├── bash.py
│   └── grep.py
└── cli.py               # CLI 入口 (~100 行)
```

**预估总代码量：~2300 行**（当前 nimbus 核心代码 ~5000+ 行）

---

## 6. 关键接口设计

### 6.1 AgentOS（精简版）

```python
class AgentOS:
    """Nimbus Next - 极简 Agent OS"""

    def __init__(self, llm_client, tools=None, config=None):
        self._llm = llm_client
        self._tools = ToolRegistry()
        self._events = EventStream()
        self._register_core_tools()

    def run(self, goal: str) -> ToolResult:
        """同步执行任务"""

    async def run_stream(self, goal: str) -> AsyncIterator[dict]:
        """流式执行任务"""

    async def chat(self, message: str) -> str:
        """交互式对话"""
```

没有 ProcessManager、没有 SessionCoordinator、没有 Heart、没有 SkillManager。
**一个类，三个方法，完事。**

### 6.2 VCPU（保持现有设计）

```python
class VCPU:
    def __init__(self, alu, decoder, gate, mmu, config, tools):
        ...

    async def step(self) -> StepResult:
        """驱动 FSM 前进一步：Think → Act → Observe"""

    async def run(self, goal: str) -> ToolResult:
        """便捷封装：循环执行直到完成"""
```

### 6.3 MMU（精简版）

保留核心：
- `PinnedContext`（Anchor）
- `StackFrame`（Stream）- 仅保留单层，移除 push/pop
- `assemble_context()`
- `archive_and_reset()`（compaction）
- `add_user_message()` / `add_assistant_message()` / `add_tool_result()`

移除：
- NimFS offload
- Clipboard
- Scroll/Viewport
- Memory context injection
- Milestone tracking
- StateManager

### 6.4 Gate（精简版）

保留核心：
- `syscall_tool()` with timeout
- Doom loop detection
- Arg normalization
- Output truncation
- Event emission

移除：
- Write filter
- Meta tool timeouts
- Local tools (IPC)

---

## 7. 实施计划：按概念深度排序的学习路径

> 核心原则：每个 Step 只聚焦一个概念，做完后有可运行的验证，
> 不是赶工期，而是通过亲手重构来彻底内化 agent 框架的每一块骨头。

### Step 1: Protocol — "Agent 的语言"

**你在学什么**：一个 agent 框架的所有组件之间靠什么通信？答案是一套极小的数据结构。
这是整个系统的"脊柱"——搞懂 ActionIR 和 ToolResult，就搞懂了 agent 的指令集。

**做什么**：
1. 创建 `src/nimbus_next/__init__.py` + `src/nimbus_next/protocol.py`
2. 从现有 `core/protocol.py` 提取：ActionIR、ToolResult、Fault、Event
3. 删除：IPCMessage、NimFS helpers、NIMFS_OFFLOAD_THRESHOLD
4. 写单元测试：ActionIR 创建/序列化、Fault 分类路由

**验证**：`pytest tests/nimbus_next/test_protocol.py` 全绿

**行数预算**：~100 行

---

### Step 2: Tools + Registry — "Agent 的手脚"

**你在学什么**：工具是 agent 与外界交互的唯一方式。工具的定义、注册、查找机制
是所有 agent 框架的基础设施。pi 用 4 个工具就能做 90% 的事，这是极简设计的精髓。

**做什么**：
1. 创建 `src/nimbus_next/tools/registry.py` — 精简的 ToolRegistry（注册+查找，~100行）
2. 创建 `src/nimbus_next/tools/` 下 5 个核心工具：read/write/edit/bash/grep
3. 每个工具用 `@tool` 装饰器自注册
4. 写测试：工具注册、查找、schema 导出

**验证**：能从 registry 导出 OpenAI function calling 格式的 tool schema

**行数预算**：~400 行（registry 100 + 工具 300）

---

### Step 3: Decoder — "Agent 的防火墙"

**你在学什么**：LLM 的输出是不可信的。Decoder 是 LLM 和执行引擎之间的翻译层+过滤层。
这是 nimbus 相比 pi 的核心差异化能力——幻觉检测、控制流映射、参数验证。

**做什么**：
1. 创建 `src/nimbus_next/decoder.py`
2. 从现有 decoder.py 提取：幻觉检测模式、tool_call → ActionIR 转换、done-pattern 检测
3. 移除：与 IPC/SubAgent 相关的控制流映射
4. 写测试：正常 tool_call 解码、幻觉文本检测、done 判定

**验证**：喂入模拟的 LLM 响应，验证能正确输出 ActionIR 或检测到幻觉

**行数预算**：~250 行

---

### Step 4: Gate — "Agent 的系统调用"

**你在学什么**：工具执行不是直接调函数——需要超时控制、参数容错、doom loop 检测、
输出截断。Gate 就是 agent 的 syscall 层，所有副作用都经过这个瓶颈点。

**做什么**：
1. 创建 `src/nimbus_next/gate.py`
2. 从现有 gate.py 提取：syscall_tool()、超时机制、doom loop 检测、arg normalization、output truncation
3. 移除：write_filter、meta tool timeouts、IPC local tools
4. 写测试：正常执行、超时中断、doom loop 触发、参数修正

**验证**：能通过 Gate 执行一个真实的 Bash 工具调用并拿到 ToolResult

**行数预算**：~200 行

**里程碑 A**：到这里，你有了完整的"工具执行通路"——Tool 定义 → Gate 执行 → ToolResult 返回。
可以单独跑一个 `gate.syscall_tool("Bash", {"command": "echo hello"})` 验证整条链路。

---

### Step 5: MMU — "Agent 的记忆"

**你在学什么**：context window 管理是 agent 和普通 chatbot 的根本区别。
Anchor（不变的系统上下文）+ Stream（会被压缩的动态历史）是 nimbus 最核心的创新。
理解了这个，就理解了为什么 Claude Code 在长任务中不会迷失方向。

**做什么**：
1. 创建 `src/nimbus_next/mmu.py`
2. 从现有 MMU 提取：PinnedContext（Anchor）、StackFrame（Stream，单层）、assemble_context()、archive_and_reset()（compaction）
3. 移除：NimFS offload、scroll/viewport、clipboard、milestone tracking、StateManager
4. 写测试：消息添加、context 组装、token 统计、压缩触发

**验证**：能组装出一个完整的 messages 数组（system + history），并在超限时自动压缩

**行数预算**：~400 行

---

### Step 6: VCPU + FSM — "Agent 的大脑"

**你在学什么**：Think-Act-Observe 循环是所有 agent 的核心模式。
把它实现为 FSM 而不是简单 while 循环，带来的好处是：可中断、可恢复、状态可观测。
这一步把前面所有组件串起来。

**做什么**：
1. 创建 `src/nimbus_next/vcpu.py` + `src/nimbus_next/fsm.py`（或合并）
2. 从现有代码提取：FSM 状态定义、VCPUConfig、step() 方法
3. 移除：checkpoint、tracer、与 ProcessManager 的耦合
4. VCPU 接收：ALU（LLM client）、Decoder、Gate、MMU、Tools
5. 写测试：单步执行（mock LLM）、状态转换验证、中断测试

**验证**：用 mock LLM 驱动一个完整的 Think→Act→Observe→Complete 循环

**行数预算**：~350 行

---

### Step 7: Adapter — "Agent 的嘴和耳"

**你在学什么**：LLM API 调用看似简单，实际上要处理：流式/非流式、多 provider 适配、
tool_choice 控制、token 统计、重试。Adapter 是 vCPU 的 ALU（算术逻辑单元）。

**做什么**：
1. 创建 `src/nimbus_next/adapter.py`
2. 从现有 73KB 的 DirectAdapter 中只提取核心路径：
   - `generate()`：非流式调用
   - `stream()`：流式调用
   - token 统计
3. 先只支持 Anthropic（或 OpenAI 二选一），不做多 provider
4. 写测试：mock API 调用，验证响应解析

**验证**：能用真实 API key 发一次请求，拿到正确解析的响应

**行数预算**：~500 行

---

### Step 8: RuntimeLoop + AgentOS — "Agent 的心跳"

**你在学什么**：RuntimeLoop 是驱动 VCPU 持续运转的外层循环——处理迭代限制、
context overflow（触发 compaction）、中断信号。AgentOS 是最终的组装点。

**做什么**：
1. 创建 `src/nimbus_next/loop.py` — 精简的 RuntimeLoop
2. 创建 `src/nimbus_next/agent.py` — AgentOS facade（~150 行）
3. RuntimeLoop: step 循环 + compaction 触发 + 迭代限制 + 中断
4. AgentOS: 组装所有组件 + 暴露 run()/stream()/chat()
5. 写集成测试

**验证**：`AgentOS(llm).run("列出当前目录的文件")` 端到端执行成功

**行数预算**：~350 行

**里程碑 B**：一个完整可运行的 agent，能接收任务、调用工具、返回结果。

---

### Step 9: CLI — "Agent 的皮肤"

**你在学什么**：交互式 CLI 是 agent 与人类的界面。流式输出、工具调用展示、
中断处理（Ctrl+C）是 UX 的核心。

**做什么**：
1. 创建 `src/nimbus_next/cli.py`
2. 最简 CLI：读取用户输入 → 调用 AgentOS.chat() → 流式输出
3. 支持：流式 token 输出、工具调用显示、Ctrl+C 中断

**验证**：在终端里跑起来，能对话、能执行工具、能中断

**行数预算**：~100 行

**最终里程碑**：一个 ~2500 行的完整 agent 框架，你理解每一行代码的存在理由。

---

### 每步完成后的 Checklist

- [ ] 代码行数是否在预算内？超了说明没删干净
- [ ] 能否用一句话解释这个组件为什么存在？
- [ ] 它的上游（谁调用它）和下游（它调用谁）是否清晰？
- [ ] 测试是否覆盖了核心路径？
- [ ] 与 pi 的对应关系是否清楚？（pi 没有的部分，nimbus 为什么需要？）

---

## 8. 与 pi 的关键差异

Nimbus Next 不是 pi 的 Python 翻版。关键差异：

| 方面 | pi | nimbus next |
|---|---|---|
| **语言** | TypeScript | Python |
| **架构** | 扁平（4 个包） | 分层（4 层，严格依赖方向） |
| **执行模型** | 简单 while 循环 | FSM 状态机（可中断、可恢复） |
| **内存管理** | 无显式管理 | Anchor & Stream + Smart Drop |
| **幻觉防御** | 无 | Decoder 防火墙（pi 缺少的能力） |
| **Doom Loop** | 无 | Gate 级检测 + 终止 |
| **Compaction** | 无 | LLM 摘要 + 滑动窗口 |
| **参数容错** | 无 | Gate 参数归一化（`path` → `file_path`） |

这些是 nimbus 真正有价值的差异化能力，必须保留。

---

## 9. 代码行数预算

| 组件 | 目标行数 | 备注 |
|---|---|---|
| protocol.py | 100 | 删除 IPC, NimFS |
| mmu.py | 400 | 删除 NimFS, scroll, clipboard |
| vcpu.py | 300 | 删除 checkpoint, tracer |
| decoder.py | 250 | 保持不变 |
| gate.py | 200 | 删除 write_filter, meta timeouts |
| adapter.py | 500 | 从 73KB 重构精简 |
| loop.py | 200 | 删除 Heart, NimFS GC |
| agent.py | 150 | 精简 Facade |
| tools/ | 300 | 5 个核心工具 |
| cli.py | 100 | 最简 CLI |
| **总计** | **~2500** | 现有 ~5000+ 行的一半 |

---

## 10. 一句话总结

> **Nimbus Next = pi 的极简哲学 + nimbus 的分层纪律 + 差异化能力（MMU/Decoder/Gate）**

保留真正有价值的创新（Anchor & Stream、幻觉防火墙、Doom Loop 检测、参数容错），
删除一切"可能有用但现在不需要"的功能。
