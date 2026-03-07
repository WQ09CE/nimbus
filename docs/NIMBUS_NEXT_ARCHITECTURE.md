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

## 7. 迁移策略

### Phase 1: 创建 nimbus_next 包（不破坏现有代码）

1. 在 `src/nimbus_next/` 下创建新包
2. 从现有代码中提取核心组件
3. 精简每个组件，删除不需要的功能
4. 编写独立测试

### Phase 2: 验证

1. 使用 Terminal-Bench 风格的任务验证能力
2. 对比 nimbus v2 的表现
3. 确保分层边界清晰

### Phase 3: 逐步替换

1. CLI 切换到 nimbus_next
2. Web UI 适配
3. 旧代码标记为 legacy

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
