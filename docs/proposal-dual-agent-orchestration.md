# 提案：Nimbus Dual-Agent 编排架构

**作者**: 技术团队  
**日期**: 2026-02-05  
**状态**: 待评审  
**目标版本**: nimbus 0.3.0

---

## 一、问题陈述

Nimbus 0.2.0 在 terminal-bench 上通过率 22%（9 有效任务通过 2 个），但子项通过率普遍很高（5/7, 10/11, 2/3）。评审委员会一致认为核心瓶颈是 **single-agent 的开环架构**——agent 执行完即返回，没有独立的验证反馈回路。

具体表现为：
- **自验证闭环**：agent 用自己的理解验证自己的实现（kv-store-grpc）
- **环境副作用无感知**：验证行为产生的产物未清理（polyglot-c-py）
- **同类问题遗漏**：修了 2/3 文件就认为完成（build-cython-ext）
- **需求精度衰减**：`value` 和 `val` 混淆（kv-store-grpc）

**核心判断**：这些问题不是 prompt engineering 能解决的。需要在架构层面引入 **对抗性验证**——让验证者与执行者拥有独立的上下文。

---

## 二、设计目标

1. **引入 dual-agent 模式**：Core（调度+验证）+ Executor（实现），打破 confirmation bias
2. **最小化架构改动**：复用 AgentOS 现有的 `spawn/wait/Process` 和 `KernelGate` 机制
3. **不影响现有 single-agent 模式**：dual-agent 作为可选的编排层，不修改 VCPU/MMU 内核
4. **可衡量收益**：目标将 terminal-bench 通过率从 22% 提升至 55%+

---

## 三、架构设计

### 3.1 整体结构

```
用户任务
   │
   ▼
┌──────────────────────────────────────────────────────┐
│                   Orchestrator                        │
│         (新增编排层，不修改 AgentOS 内核)               │
│                                                       │
│  ┌─────────────────────┐  ┌────────────────────────┐  │
│  │     Core Agent       │  │    Executor Agent      │  │
│  │                     │  │                        │  │
│  │  AgentOS Process    │  │  AgentOS Process       │  │
│  │  role="core"        │  │  role="executor"       │  │
│  │                     │  │                        │  │
│  │  Tools:             │  │  Tools:                │  │
│  │  - Read             │  │  - Read                │  │
│  │  - Bash (只读)      │  │  - Write               │  │
│  │  - Memo             │  │  - Edit                │  │
│  │                     │  │  - Bash (全权限)        │  │
│  │  能力:              │  │  - Memo                │  │
│  │  - 任务拆分         │  │                        │  │
│  │  - 验证产出物       │  │  能力:                  │  │
│  │  - 全局扫描         │  │  - 代码实现             │  │
│  │  - 反馈循环         │  │  - 测试运行             │  │
│  └─────────┬───────────┘  └────────────┬───────────┘  │
│            │    dispatch / collect      │              │
│            └───────────┬───────────────┘              │
│                        │                              │
└────────────────────────┼──────────────────────────────┘
                         │
                    最终结果
```

### 3.2 两个角色的定义

#### Core Agent

**职责**：理解需求 → 拆分任务 → 分发执行 → 验证结果 → 反馈修正

**工具集**（只读 + 验证）：
- `Read`：读取文件
- `Bash`：限制为只读命令（grep, find, ls, cat, python -c 验证脚本等）
- `Memo`：记录任务状态
- `Dispatch`：新增工具，向 Executor 分发子任务（见 3.3）
- `Verify`：新增工具，对工作目录执行确定性检查（见 3.4）

**不拥有的工具**：Write, Edit。Core 不能修改文件——保持"审查者"角色的纯净性。

**系统提示词要点**：
- 你是任务调度者和质量审查者
- 你不直接写代码，你通过 Dispatch 工具将实现任务分发给 Executor
- 在分发前，先 grep/find 全面了解项目结构
- Executor 返回后，你必须独立验证产出物
- 验证不通过时，带具体反馈重新分发

#### Executor Agent

**职责**：接收具体的实现任务 → 读代码 → 写代码 → 运行验证 → 返回结果

**工具集**（全权限）：
- `Read`：读取文件
- `Write`：创建文件
- `Edit`：编辑文件
- `Bash`：全权限执行命令
- `Memo`：记录进度

**不拥有的工具**：Dispatch, Verify。Executor 不参与任务编排。

**系统提示词要点**：
- 你是代码实现者
- 你会收到明确的实现任务，直接执行
- 完成后简要报告做了什么、修改了哪些文件
- 不要自己判断"整个任务是否完成"，那是 Core 的职责

### 3.3 Dispatch 工具（Core 专用）

```python
DISPATCH_TOOL = {
    "name": "Dispatch",
    "description": (
        "Dispatch a sub-task to the Executor agent for implementation. "
        "The Executor has full Read/Write/Edit/Bash permissions. "
        "Returns the Executor's result and a list of files it modified."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Clear, specific implementation task for the Executor. "
                    "Include: what to do, which files to modify, "
                    "exact names/values to use, and success criteria."
                ),
            },
            "context": {
                "type": "string",
                "description": (
                    "Optional context: relevant code snippets, file contents, "
                    "or constraints the Executor needs to know."
                ),
            },
        },
        "required": ["task"],
    },
}
```

**实现逻辑**：

```python
async def dispatch(task: str, context: str = "") -> str:
    """
    1. 拍摄工作目录快照（file list + mtime）
    2. spawn Executor process，传入 task + context
    3. wait Executor 完成
    4. 对比快照，提取 Executor 修改/创建了哪些文件
    5. 返回：Executor 的回答 + 文件变更列表
    """
    # 快照
    snapshot_before = _snapshot_workspace(workspace)
    
    # 构造 Executor 的完整 goal
    executor_goal = task
    if context:
        executor_goal = f"{task}\n\n## Context\n{context}"
    
    # 通过 AgentOS 启动 Executor
    pid = agent_os.spawn(executor_goal, role="executor")
    result = await agent_os.wait(pid, timeout=300)
    
    # 文件系统 diff
    snapshot_after = _snapshot_workspace(workspace)
    diff = _diff_snapshots(snapshot_before, snapshot_after)
    
    # 组装返回
    output = f"## Executor Result\n{result.output}\n\n"
    output += f"## Files Changed\n"
    for f in diff.created:
        output += f"  + {f} (created)\n"
    for f in diff.modified:
        output += f"  ~ {f} (modified)\n"
    for f in diff.deleted:
        output += f"  - {f} (deleted)\n"
    
    return output
```

**关键设计点**：
- Executor 每次用 **全新的 context**（fresh MMU），不继承 Core 的执行历史
- Dispatch 返回的是 Executor 的文本总结 + 文件变更列表，**不是 Executor 的完整推理过程**
- Core 拿到 diff 后可以 `Read` 具体文件来独立审查

### 3.4 Verify 工具（Core 专用）

```python
VERIFY_TOOL = {
    "name": "Verify",
    "description": (
        "Run deterministic verification checks on the workspace. "
        "Checks include: file existence, pattern matching in files, "
        "port availability, and command exit codes. "
        "Use this after Dispatch to verify the Executor's work."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "description": "List of checks to run",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "file_exists",
                                "file_not_exists",
                                "file_contains",
                                "file_not_contains",
                                "command_succeeds",
                                "port_listening",
                            ],
                        },
                        "target": {
                            "type": "string",
                            "description": "File path, pattern, command, or port number",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "For file_contains/file_not_contains: search pattern",
                        },
                    },
                    "required": ["type", "target"],
                },
            },
        },
        "required": ["checks"],
    },
}
```

**实现逻辑**：

```python
async def verify(checks: list) -> str:
    """执行确定性验证检查，返回通过/失败报告"""
    results = []
    for check in checks:
        check_type = check["type"]
        target = check["target"]
        
        if check_type == "file_exists":
            passed = Path(target).exists()
            results.append(f"{'✅' if passed else '❌'} file_exists: {target}")
            
        elif check_type == "file_not_exists":
            passed = not Path(target).exists()
            results.append(f"{'✅' if passed else '❌'} file_not_exists: {target}")
            
        elif check_type == "file_contains":
            pattern = check.get("pattern", "")
            content = Path(target).read_text() if Path(target).exists() else ""
            passed = pattern in content
            results.append(f"{'✅' if passed else '❌'} file_contains: '{pattern}' in {target}")
            
        elif check_type == "file_not_contains":
            pattern = check.get("pattern", "")
            content = Path(target).read_text() if Path(target).exists() else ""
            passed = pattern not in content
            results.append(f"{'✅' if passed else '❌'} file_not_contains: '{pattern}' in {target}")
            
        elif check_type == "command_succeeds":
            proc = await asyncio.create_subprocess_shell(
                target, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()
            passed = proc.returncode == 0
            results.append(f"{'✅' if passed else '❌'} command_succeeds: {target[:80]}")
            
        elif check_type == "port_listening":
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            passed = sock.connect_ex(('localhost', int(target))) == 0
            sock.close()
            results.append(f"{'✅' if passed else '❌'} port_listening: {target}")
    
    all_passed = all("✅" in r for r in results)
    header = "## Verification: ALL PASSED ✅" if all_passed else "## Verification: ISSUES FOUND ❌"
    return header + "\n" + "\n".join(results)
```

### 3.5 交互流程

以 kv-store-grpc 任务为例，展示完整的交互流程：

```
用户: "Build a gRPC KV store server..."

Core Agent (iteration 1-3):
  │ Read 需求，提取关键约束：
  │   - SetValRequest: key(string), value(int)    ← 注意是 "value"
  │   - SetValResponse: val(int)                   ← 这里是 "val"
  │   - port 5328
  │   - /app/kv-store.proto, /app/server.py
  │
  │ Dispatch(task="""
  │   1. pip install grpcio==1.73.0 grpcio-tools==1.73.0
  │   2. Create /app/kv-store.proto with:
  │      - SetValRequest: key(string), value(int)  ← 精确传达
  │      - SetValResponse: val(int)
  │      - GetValRequest: key(string)
  │      - GetValResponse: val(int)
  │   3. Generate Python files from proto
  │   4. Create /app/server.py with class Server, port 5328
  │   5. Start server in background
  │ """)
  ▼

Executor Agent (独立 context, ~10 iterations):
  │ 安装依赖 → 写 proto → 生成代码 → 写 server → 启动
  │ → 返回: "完成，修改了 kv-store.proto, server.py, 生成了 pb2 文件"
  ▼

Core Agent (iteration 4-6):
  │ 收到 Executor 结果 + 文件变更列表
  │
  │ 独立验证：
  │ Read("/app/kv-store.proto")
  │   → 检查 SetValRequest 是否有 "value" 字段（不是 "val"）
  │   → ❌ 发现字段名是 "val" 而非 "value"！
  │
  │ Dispatch(task="""
  │   修复 /app/kv-store.proto 中 SetValRequest 的字段名：
  │   将 'val' 改为 'value'（注意 SetValResponse 的字段保持为 'val'）
  │   然后重新生成 Python 代码，重启 server
  │ """)
  ▼

Executor Agent (新的独立 context):
  │ 修改 proto → 重新生成 → 重启
  │ → 返回: "已修复"
  ▼

Core Agent (iteration 7-8):
  │ Verify([
  │   {"type": "file_contains", "target": "/app/kv-store.proto", "pattern": "value"},
  │   {"type": "port_listening", "target": "5328"},
  │   {"type": "command_succeeds", "target": "python3 -c 'import kv_store_pb2; print(kv_store_pb2.SetValRequest.DESCRIPTOR.fields_by_name.keys())'"},
  │ ])
  │ → ✅ ALL PASSED
  │
  │ → 返回最终结果给用户
```

### 3.6 基于 AgentOS 的实现方案

**核心改动**：新增一个 `Orchestrator` 类，封装 dual-agent 编排逻辑。不修改 `AgentOS`、`VCPU`、`MMU`、`Gate` 的任何代码。

```python
# 新增文件: nimbus/orchestration/dual_agent.py

class DualAgentOrchestrator:
    """
    Dual-Agent 编排器
    
    在 AgentOS 之上的薄编排层，管理 Core + Executor 两个角色。
    """
    
    def __init__(self, llm_client: LLMClient, workspace: Path, config: OrchestratorConfig = None):
        self.workspace = workspace
        self.config = config or OrchestratorConfig()
        
        # 为 Core 和 Executor 分别创建 AgentOS 实例
        # 它们共享同一个 LLM client，但有独立的 tools 和 system prompt
        self._core_os = self._create_core_os(llm_client)
        self._executor_os = self._create_executor_os(llm_client)
    
    def _create_core_os(self, llm) -> AgentOS:
        """创建 Core Agent 的 AgentOS（只读工具 + Dispatch + Verify）"""
        config = AgentOSConfig(
            system_rules=CORE_SYSTEM_PROMPT,
            vcpu_config=VCPUConfig(max_iterations=30),
            workspace_info=f"Workspace: {self.workspace}",
        )
        os = AgentOS(llm_client=llm, config=config)
        
        # 只注册只读工具
        register_default_tools(os, workspace=self.workspace, tools=["Read", "Bash"])
        
        # 注册 Dispatch 和 Verify（Core 专用）
        os.register_tool("Dispatch", self._dispatch, ...)
        os.register_tool("Verify", self._verify, ...)
        
        return os
    
    def _create_executor_os(self, llm) -> AgentOS:
        """创建 Executor Agent 的 AgentOS（全权限工具）"""
        config = AgentOSConfig(
            system_rules=EXECUTOR_SYSTEM_PROMPT,
            vcpu_config=VCPUConfig(max_iterations=30),
            workspace_info=f"Workspace: {self.workspace}",
        )
        os = AgentOS(llm_client=llm, config=config)
        register_default_tools(os, workspace=self.workspace)  # 全部工具
        return os
    
    async def run(self, goal: str) -> ToolResult:
        """执行入口：启动 Core Agent"""
        return await self._core_os.run(goal, role="core")
    
    async def _dispatch(self, task: str, context: str = "") -> str:
        """Dispatch 工具实现：启动 Executor 执行子任务"""
        snapshot_before = self._snapshot_workspace()
        
        executor_goal = task
        if context:
            executor_goal = f"{task}\n\n## Context\n{context}"
        
        result = await self._executor_os.run(executor_goal, role="executor")
        
        snapshot_after = self._snapshot_workspace()
        diff = self._diff_snapshots(snapshot_before, snapshot_after)
        
        return self._format_dispatch_result(result, diff)
    
    async def _verify(self, checks: list) -> str:
        """Verify 工具实现：确定性检查"""
        # ... 如 3.4 节所述
```

**文件结构变更**：

```
nimbus/
├── src/nimbus/
│   ├── orchestration/          # 新增目录
│   │   ├── __init__.py
│   │   ├── dual_agent.py       # DualAgentOrchestrator
│   │   ├── prompts.py          # Core / Executor 系统提示词
│   │   ├── tools.py            # Dispatch / Verify 工具定义
│   │   └── workspace_diff.py   # 文件系统快照 & diff
│   ├── agentos.py              # 不修改
│   ├── core/                   # 不修改
│   └── tools/                  # 不修改
├── nimbus_harbor/
│   └── nimbus_agent.py         # 修改：支持 dual-agent 模式
```

---

## 四、Core Agent 的 Bash 限制策略

Core Agent 拥有 Bash 工具，但限制为只读用途。实现方式有两个选项：

### 选项 A：信任 LLM + System Prompt 约束（推荐）

在 Core 的 system prompt 中明确声明：

> 你的 Bash 工具仅用于只读操作（grep, find, ls, cat, python -c 验证）。如需修改文件或运行长时间命令，使用 Dispatch 分发给 Executor。

**优点**：零额外代码，LLM 在明确规则下遵守率很高。  
**风险**：LLM 偶尔可能违规。但对于验证场景，Core 修改文件不会比不修改更差。

### 选项 B：Bash Wrapper 做命令过滤

```python
WRITE_COMMANDS = {"rm", "mv", "cp", "mkdir", "touch", "chmod", ">", ">>", "tee", "sed -i", "dd"}

async def core_bash(command: str, **kwargs) -> str:
    # 简单的黑名单检查
    cmd_lower = command.lower().strip()
    for wc in WRITE_COMMANDS:
        if wc in cmd_lower:
            return f"[Error] Core Agent cannot execute write commands. Use Dispatch to delegate."
    return await bash_command(command, **kwargs)
```

**优点**：硬约束。  
**缺点**：黑名单不完备（`python -c "open('x','w').write('...')"` 绕过），给人安全感但不真安全。

**推荐**：选项 A。在 benchmark 场景下，LLM 的遵守率足够。如果后续发现问题再升级为选项 B。

---

## 五、与 terminal-bench 失败任务的对照分析

### 5.1 kv-store-grpc（当前 5/7 → 预期 7/7）

| 阶段 | 行为 | 解决的问题 |
|------|------|-----------|
| Core 分发前 | 从需求中提取 `value(int)` 和 `val(int)` 是不同字段，写入 Dispatch 指令 | 需求精度降级 |
| Core 验证时 | `Read("/app/kv-store.proto")` 确认字段名 | 自验证盲区 |
| Core 二次分发 | 带具体反馈让 Executor 修正 | 反馈循环 |

### 5.2 polyglot-c-py（当前 0/1 → 预期 1/1）

| 阶段 | 行为 | 解决的问题 |
|------|------|-----------|
| Dispatch 返回时 | 自动 diff 显示 `+ cmain (created)` | 环境副作用感知 |
| Core 验证时 | `Verify([{"type": "file_not_exists", "target": "/app/polyglot/cmain"}])` | 清洁度检查 |
| Core 二次分发 | "删除 /app/polyglot/cmain" | 反馈修正 |

### 5.3 build-cython-ext（当前 10/11 → 预期 11/11）

| 阶段 | 行为 | 解决的问题 |
|------|------|-----------|
| Core 分发前 | `Bash("grep -rn 'np\.int[^0-9e]' /app/pyknotid --include='*.pyx'")` 找到所有 3 个文件 | 全局扫描 |
| Core 分发 | 拆成 3 个子任务，每个文件一个 | 不遗漏 |
| Core 验证时 | `Bash("grep -rn 'np\.int[^0-9e]' /app/pyknotid --include='*.pyx'")` 确认零匹配 | 确认完整性 |

### 5.4 pcap-to-netflow（当前 1/4 → 预期：不确定）

时间戳语义错误属于领域知识问题。Dual-agent 不能直接解决，但 Core 验证时运行 `python3 /app/pcap_to_netflow.py test.pcap /tmp/test_out && python3 -c "..."` 做输出抽样检查，**有可能**发现时间戳异常（2026 vs 2011 差距过大）。

### 5.5 预期通过率

| 任务 | 当前 | 预期 | 信心 |
|------|------|------|------|
| fibonacci-server | ✅ | ✅ | 确定 |
| kv-store-grpc | ❌ | ✅ | 高 |
| pypi-server | ✅ | ✅ | 确定 |
| polyglot-c-py | ❌ | ✅ | 高 |
| pcap-to-netflow | ❌ | ❌/✅ | 低 |
| add-benchmark-lm-eval | ❌ | ❌ | 低（框架知识问题） |
| swe-bench-langcodes | ❌ | ✅ | 高（配合安装修复） |
| build-cython-ext | ❌ | ✅ | 高 |

**预期通过率：5-6/9（56-67%）**，从 22% 提升约 3 倍。

---

## 六、成本分析

### 6.1 API 调用开销

| 模式 | 平均 LLM 调用次数 | 说明 |
|------|-------------------|------|
| Single-agent | ~20 次/任务 | 当前模式 |
| Dual-agent | ~35 次/任务 | Core ~15 次 + Executor ~20 次 |

增加约 **75%** 的 LLM 调用量。但 Executor 的每次调用 context 更小（fresh context，没有历史包袱），单次调用的 token 消耗更低。综合看 **总 token 成本增加约 40-60%**。

### 6.2 延迟影响

Dispatch 是同步阻塞的——Core 等 Executor 完成。每次 Dispatch 增加 30-120 秒延迟。但 Core 的验证 + 反馈循环可能避免了"做完才发现错"的更大延迟。

### 6.3 实现工作量

| 组件 | 预估工作量 | 说明 |
|------|-----------|------|
| `DualAgentOrchestrator` | 1-2 天 | 核心编排类 |
| `Dispatch` 工具 | 0.5 天 | 含 workspace diff |
| `Verify` 工具 | 0.5 天 | 确定性检查 |
| Core / Executor 系统提示词 | 0.5 天 | 需要调优 |
| Harbor adapter 适配 | 0.5 天 | nimbus_agent.py 支持 dual-agent 模式 |
| 测试 | 1 天 | 单元测试 + terminal-bench 回归 |

**总计：4-5 天**

---

## 七、风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Core 的任务拆分质量差，导致 Executor 拿到模糊指令 | Executor 做错事 | Core 的 system prompt 中强调：Dispatch 指令必须包含精确的文件路径、字段名、约束条件 |
| Core 过度拆分，简单任务也拆成多个 Dispatch | 浪费 API 调用 | system prompt 中说明：简单任务可以一个 Dispatch 搞定，不需要拆 |
| Executor 超时（terminal-bench 有总时间限制） | 任务未完成 | Dispatch 加 timeout 参数，默认 300 秒；Core 监控剩余时间 |
| 两个 AgentOS 实例的 LLM client 并发问题 | 请求冲突 | Dispatch 是串行的（Core wait Executor），不会并发调用 LLM |
| single-agent 在简单任务上本来就能通过，dual-agent 反而增加失败概率 | 通过率反降 | 保留 single-agent 模式作为 fallback；或根据任务复杂度自动选择模式 |

---

## 八、后续演进路径

```
v0.3.0 (本提案)
  └── Core + Executor dual-agent
       └── Dispatch (串行) + Verify (确定性)

v0.3.x (优化)
  ├── Bash Daemon 模式（解决后台服务启动问题）
  ├── Docker 多版本兼容性修复
  └── Core 的 Verify 工具增强（更多 check types）

v0.4.0 (扩展)
  ├── 并行 Dispatch（多个 Executor 同时工作）
  ├── Explorer Agent（只读探索，适合大型代码库）
  └── Core 的 LLM 交叉验证（独立 context 审查 Executor 代码）
```

---

## 九、决策请求

请评审委员会审议以下问题：

1. **架构方案**：Core + Executor 双角色是否合理？是否需要第三个角色？
2. **Core 的 Bash 限制**：选项 A（prompt 约束）还是选项 B（命令过滤）？
3. **实现优先级**：是先做 dual-agent 编排（本提案），还是先做 Phase 1 的基础改进（Bash Daemon + Docker 兼容）？
4. **成本接受度**：40-60% 的额外 token 成本是否可接受？
