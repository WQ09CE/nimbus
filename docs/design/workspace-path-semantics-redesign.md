# Workspace & Path Semantics 设计文档 (最终版)

## 1. 问题背景与深度误判分析

### 1.1 背景
在 Nimbus 的多 Agent 协作体系中，路径语义的模糊是导致 Agent 执行失败、代码覆盖、或因无法找到文件而产生幻觉的核心原因。随着从单仓任务向多仓（Multi-repo）、多层级子任务（Sub-agent）演进，简单的 CWD 概念已无法覆盖复杂的执行需求。

### 1.2 核心痛点
- **路径漂移**：Sub-agent 基于父级 CWD 的相对路径在自身上下文下失效。
- **权限逃逸**：Agent 通过 `../../` 访问或修改了不属于该任务授权范围的文件。
- **环境隔离缺失**：Bash 执行时缺乏对执行路径（Execution CWD）的显式追踪。
- **多仓歧义**：在多仓库任务中，Agent 无法区分“我的工作区”与“外部参考仓库”。

## 2. 设计目标

1.  **AgentPathContext 模型化**：引入强类型上下文，定义 Agent 的物理与逻辑边界。
2.  **确定性解析 (Deterministic Resolution)**：建立统一的 PathResolver，消除 `.` 与 `/` 的解析歧义。
3.  **多级收敛 (Target Narrowing)**：支持 Sub-agent 继承父级上下文并安全地缩小工作范围。
4.  **工具链一致性**：确保 Read/Write/Edit/Grep/Bash/spawn_agent 遵循同一套语义规则。
5.  **核心守卫 (KernelGate)**：通过 KernelGate 统一拦截并注入路径上下文，实现“默认安全”。

## 3. AgentPathContext 核心模型

每个 Agent 实例在初始化时必须关联一个 `AgentPathContext`：

```python
class AgentPathContext:
    # 物理根路径：Agent 的逻辑 "/"。任何操作禁止超越此范围（Strict 模式）
    workspace_root: str
    
    # 任务目标根路径：Agent 当前专注的核心子目录
    target_root: str
    
    # 当前执行目录：Agent 执行 Bash 或查找相对路径的起始点
    execution_cwd: str
    
    # 只读参考路径列表：例如引用的第三方库、其他仓库或配置文件
    reference_roots: List[str]
    
    # 可写路径白名单：通常等于 [target_root]
    writable_roots: List[str]
    
    # 模式：'strict' (隔离) | 'relaxed' (允许访问但记录)
    scope_mode: str = "strict"
```

**关键语义说明**：
- `workspace_root` 是物理硬边界。
- `target_root` 是逻辑专注点，Agent 视角下的“我的项目”。
- `execution_cwd` 随 `bash` 工具的 `cd` 操作动态更新。

## 4. PathResolver 增强解析规则

`PathResolver` 作为单例服务，为所有工具提供服务。

### 4.1 解析算法
1.  **输入归一化**：处理 `~`, `./`, `../` 及多余的 `/`。
2.  **绝对路径处理**：若输入以 `/` 开头，视其为真实系统绝对路径（不重定向到 `workspace_root`）。边界控制由 `validate_read/write` 负责。
3.  **相对路径处理（文件工具）**：Read/Write/Edit/Grep 基于 `target_root` 进行拼接，不受 Bash `cd` 影响。
4.  **相对路径处理（Bash）**：Bash 基于 `execution_cwd` 拼接，`cd` 后自动更新 `execution_cwd`。
5.  **跨仓库处理**：检查路径是否命中 `reference_roots`。

### 4.2 校验逻辑
- **Read 校验**：路径必须在 `workspace_root` 或 `reference_roots` 内。
- **Write/Edit 校验**：路径必须在 `writable_roots`（通常是 `target_root`）内。
- **越界响应（Read）**：`strict` 模式抛出 `PathOutOfScopeError`；`relaxed` 模式仅 Log 记录。
- **越界响应（Write/Edit）**：无论 `scope_mode`，写操作越界始终抛出 `PathOutOfScopeError`。

## 5. 工具级执行规则 (Tool-level Semantics)

| 工具 | 路径语义与约束 |
| :--- | :--- |
| **Read / Grep** | 基于 `PathResolver` 解析。允许访问 `workspace_root` + `reference_roots`。 |
| **Write / Edit** | **强制约束**在 `writable_roots`。自动 `mkdir -p` 缺失的父目录。 |
| **Bash** | 1. 在 `execution_cwd` 下执行；2. 若命令包含 `cd`，成功后需更新 `AgentPathContext.execution_cwd`。 |
| **spawn_agent** | 见下文“子 Agent 继承”部分。 |

### 5.1 Bash 深度追踪
Bash 工具在返回结果时，必须包含 `executed_in` 字段，明确告诉 Agent 命令是在哪个物理路径下完成的。
- 示例：`bash(command="ls")` -> `result: { "output": "...", "executed_in": "/abs/path/to/target" }`

## 6. Sub-agent 继承与 Target Narrowing

当父 Agent 启动子 Agent 时，路径上下文按以下逻辑演进：

1.  **完整继承 (Default)**：
    - `Sub.workspace_root = Parent.workspace_root`
    - `Sub.reference_roots = Parent.reference_roots`
2.  **目标收敛 (Target Narrowing)**：
    - 若父 Agent 指定 `target_sub_path="src/utils"`：
    - `Sub.target_root = Parent.target_root + "/src/utils"`
    - `Sub.execution_cwd = Sub.target_root`
    - `Sub.writable_roots = [Sub.target_root]`
3.  **多仓任务派发**：
    - 父 Agent 可以将某个 `reference_root` 提升为子 Agent 的 `target_root`，实现跨仓协作。

## 7. KernelGate 统一接入点

`KernelGate` (位于 `src/nimbus/core/gate.py`) 是路径安全的首道防线：
- **注入**：在 `call_tool` 时，根据当前 `AgentID` 查找其 `AgentPathContext` 并注入工具实例。
- **拦截**：在底层 IO 执行前，调用 `PathResolver.validate(path, context)`。
- **反馈**：将路径相关的 Error 转化为 Agent 可理解的提示（例如：“你试图访问受限区域，请在 target_root 内操作”）。

## 8. 落地计划与分阶段实施

### 第一阶段：模型定义与 Gate 改造 (Week 1)
- 在 `src/nimbus/core/agent.py` 中引入 `AgentPathContext`。
- 实现 `PathResolver` 并集成到 `KernelGate`。

### 第二阶段：工具链重构 (Week 2)
- 改造 `Write/Edit/Read` 等文件操作工具，移除零散的路径拼接逻辑。
- 增强 `Bash` 工具，实现 `execution_cwd` 状态同步。

### 第三阶段：Orchestration 增强 (Week 3)
- 修改 `Agent.spawn` 逻辑，支持显式的 `Context` 派生。
- 在 CLI 和日志中增加逻辑路径与物理路径的映射显示。

## 9. 测试矩阵 (Test Matrix)

| 测试分类 | 测试场景 | 预期行为 |
| :--- | :--- | :--- |
| **安全性** | 尝试写入 `../../.ssh/id_rsa` | 被 PathResolver 拦截，抛出越界异常 |
| **确定性** | 绝对路径 `/src/main.py` | 解析为系统绝对路径 `/src/main.py`，由 `validate_read/write` 做边界拦截 |
| **连贯性** | `bash(cd folder)` 后执行 `Write(file)` | 文件应出现在 `folder/file` |
| **继承性** | Sub-agent 接收 `target_sub_path` | 其逻辑 `/` 锚定在子目录下，无法看到父目录文件 |
| **多仓协作** | 将 RepoB 加入 `reference_roots` | `Read` 可通，`Write` 报错（除非加入 writable） |

## 10. 与 Nimbus Core 代码结构对齐

- **Context 存放**：建议放在 `src/nimbus/core/mmu.py` (Memory Management Unit) 或 `agent.py`。
- **Resolver 位置**：`src/nimbus/core/tools/resolver.py`。
- **Gate 逻辑**：修改 `src/nimbus/core/gate.py` 的 `dispatch` 方法。
