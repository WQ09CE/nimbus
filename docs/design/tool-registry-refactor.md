# 工具注册系统简化重构方案

> 状态: 设计草稿 | 作者: Architect Agent | 日期: 2025-01

---

## 1. 现状分析

### 1.1 当前注册链路（6层）

```
@tool 装饰器
    ↓  (仅挂 _tool_definition，不注册)
tools/__init__.py 显式循环
    ↓  register_decorated → get_default_registry()
全局 ToolRegistry (死中转)
    ↓  register_default_tools()
AgentOS.__init__ 硬编码注册 Read/Write/Edit/Bash（无 workspace）
    ↓  再次 register_default_tools (有 workspace，覆盖上面)
bootstrap.py 按 profile 分支注册
    ↓  roles 在此处设置，与工具定义分离
CompositeToolRegistry → ProcessFactory.build → LLM 可见
```

### 1.2 具体痛点（代码定位）

| # | 问题 | 文件 | 位置 |
|---|------|------|------|
| P1 | 白名单遗漏：新工具必须手动加入 bootstrap 分支 | `bootstrap.py` | orchestrator 分支整体 |
| P2 | 双重注册：AgentOS.__init__ 先注册 4 个 kernel tools（无 workspace），bootstrap 再注册一遍 | `agentos.py:179-239` vs `bootstrap.py:全文` |
| P3 | 全局 Registry 是死中转：只当数据源，运行时不参与 | `tools/__init__.py:_registry` |
| P4 | roles 散落在 bootstrap：工具定义（`memo_tools.py`）和权限（`bootstrap.py`）在两个文件 | `bootstrap.py:67-80` |
| P5 | ALL_TOOLS / TOOL_FUNCTIONS：纯遗留变量，agentos.py:179 仍在 import | `agentos.py:179` |
| P6 | category 基本无用：仅 skill 冲突检查用到 | `base.py:register()` |

### 1.3 Memo/Recall/ReadMemo 遗漏案例复盘

**根因**：工具在 `memo_tools.py` 用 `@tool(category="extension")` 定义，但 `@tool` 装饰器**不写 roles**。在 orchestrator profile 分支里，需要手动补 `register_default_tools(os, tools=["Memo","Recall","ReadMemo"], roles=["orchestrator","chat"])`。这一步在某次重构中遗漏。如果 roles 在定义时声明，就不会有这个问题。

---

## 2. 设计目标

| 目标 | 说明 |
|------|------|
| 声明式权限 | 工具的 `roles` 在 `@tool` 装饰时声明，不在 bootstrap 设置 |
| 单一注册路径 | 消除双重注册（kernel tools 只注册一次）和死中转层 |
| 零配置新增 | 加了 `@tool(roles=...)` 就自动对正确角色可见 |
| 向后兼容 | skills 系统不变；specialist tools 保持手动注册 |

---

## 3. 重构方案

### 3.1 核心变更：`@tool` 装饰器增加 `roles` 参数

**目标文件**：`src/nimbus/tools/base.py`

```python
# 变更前
def tool(
    name: str,
    description: str,
    parameters: Optional[List[ToolParameter]] = None,
    category: Optional[ToolCategory] = None,
    dangerous: bool = False,
) -> Callable[[F], F]:

# 变更后
def tool(
    name: str,
    description: str,
    parameters: Optional[List[ToolParameter]] = None,
    category: Optional[ToolCategory] = None,
    dangerous: bool = False,
    roles: Optional[List[str]] = None,  # ← 新增
) -> Callable[[F], F]:
```

`ToolDefinition` 已有 `roles: Optional[List[str]]` 字段，只需在 `tool()` 的 `decorator` 内传入：

```python
definition = ToolDefinition(
    name=name,
    description=description,
    parameters=resolved_params,
    category=category,
    dangerous=dangerous,
    roles=roles,  # ← 透传
)
```

### 3.2 各工具声明式 roles

**目标文件**：各工具模块（`read.py`, `write.py`, `edit.py`, `bash.py`, `nimfs_tools.py`, `memo_tools.py`）

```python
# read.py
@tool(name="Read", description="...", category="core")
# roles=None → 所有角色可见（保持现状）

# write.py / edit.py
@tool(name="Write", description="...", category="core",
      roles=["executor", "implementer", "architect", "explorer", "tester"])

# nimfs_tools.py
@tool(name="NimFSWriteArtifact", description="...", category="nimfs",
      roles=["executor", "implementer", "architect", "tester"])

@tool(name="NimFSReadArtifact",  description="...", category="nimfs")
# roles=None → 所有角色

@tool(name="NimFSListArtifacts", description="...", category="nimfs")
# roles=None → 所有角色

# memo_tools.py
@tool(name="Memo",     description="...", category="extension",
      roles=["orchestrator", "chat"])
@tool(name="Recall",   description="...", category="extension",
      roles=["orchestrator", "chat"])
@tool(name="ReadMemo", description="...", category="extension",
      roles=["orchestrator", "chat"])
```

### 3.3 `register_default_tools` 简化

**目标文件**：`src/nimbus/tools/__init__.py`

移除显式 `tools=` 和 `roles=` 参数传递。函数改为：

```python
def register_default_tools(
    os: "AgentOS",
    workspace: Path | None = None,
    tools: List[str] | None = None,
    # roles 参数保留但不再必须传
) -> List[str]:
    registry = get_default_registry()
    tools_to_register = tools or registry.list_tools()

    for name in tools_to_register:
        entry = registry.get(name)
        if entry is None:
            continue
        td, func = entry
        wrapped_func = create_workspace_wrapper(func, workspace, ...)
        # roles 直接从 td.roles 取，不需要外部传入
        os.register_tool(
            name=name,
            func=wrapped_func,
            description=td.description,
            parameters=td.to_dict()["parameters"],
            roles=td.roles,   # ← 从定义取，不从调用方传
        )
```

### 3.4 `bootstrap.py` orchestrator 分支简化

**目标文件**：`src/nimbus/orchestration/bootstrap.py`

```python
# 变更前（~80行注册代码）
register_default_tools(os, workspace=ws, tools=["Read", "Bash"])
register_default_tools(os, workspace=ws, tools=["Write", "Edit"],
    roles=["executor", "implementer", "architect", "explorer", "tester"])
register_default_tools(os, workspace=ws, tools=["NimFSReadArtifact", "NimFSListArtifacts"])
register_default_tools(os, workspace=ws, tools=["NimFSWriteArtifact"],
    roles=["executor", "implementer", "architect", "tester"])
register_default_tools(os, workspace=ws, tools=["Memo", "Recall", "ReadMemo"],
    roles=["orchestrator", "chat"])

# 变更后（1行）
register_default_tools(os, workspace=ws)
# roles 已在各工具的 @tool(roles=...) 中声明，自动应用
```

### 3.5 消除双重注册（AgentOS.__init__）

**目标文件**：`src/nimbus/agentos.py`，`__init__` 方法约 `L174-L265`

现状：`AgentOS.__init__` 在 `kernel_tools=True` 时注册 `Read/Write/Edit/Bash`（无 workspace）。这 4 个工具随后被 `bootstrap.py` 的 `register_default_tools`（有 workspace）覆盖。

**方案**：删除 `AgentOS.__init__` 中的 kernel_tools 自动注册代码块（L174-L265），将注册权完全交给 `bootstrap.py`（或 `create_agent_os` 调用方）。

```python
# agentos.py __init__ 中删除：
if self.config.kernel_tools:
    from nimbus.tools import BASH_TOOL, EDIT_TOOL, READ_TOOL, TOOL_FUNCTIONS, WRITE_TOOL
    ...（整个 kernel_tools 注册块）
```

`kernel_tools` config 字段可保留（用于 system prompt 注入），但工具注册逻辑移出。

### 3.6 清理遗留变量

**低优先级，向后兼容期保留，后续处理**：

- `ALL_TOOLS`, `TOOL_FUNCTIONS`, `NIMFS_TOOLS`, `NIMFS_TOOL_FUNCTIONS`：标记 `@deprecated`
- `READ_TOOL`, `WRITE_TOOL`, `EDIT_TOOL`, `BASH_TOOL`：标记 `@deprecated`
- `category` 字段：继续保留，仅用于 skill 冲突检查

---

## 4. 变更范围与文件清单

| 文件 | 变更类型 | 具体内容 |
|------|---------|---------|
| `tools/base.py` | **修改** | `tool()` 装饰器增加 `roles` 参数 |
| `tools/read.py` | **修改** | `@tool` 无需加 `roles`（None=全部） |
| `tools/write.py` | **修改** | `@tool(roles=[...executor roles...])` |
| `tools/edit.py` | **修改** | 同 write.py |
| `tools/bash.py` | **修改** | 无需加 `roles`（None=全部） |
| `tools/nimfs_tools.py` | **修改** | 各工具加 `roles` |
| `tools/memo_tools.py` | **修改** | 3 个工具加 `roles=["orchestrator","chat"]` |
| `tools/__init__.py` | **修改** | `register_default_tools` 移除显式 `roles=` 传递 |
| `agentos.py` | **修改** | 删除 `__init__` 中 kernel_tools 注册块（L174-L265） |
| `orchestration/bootstrap.py` | **修改** | orchestrator 分支的 5 次 `register_default_tools` 合并为 1 次 |

**不变更**：
- `tools/composite.py` — CompositeToolRegistry 不变
- `skills/` — 整个 skills 系统不变
- `orchestration/specialist_tools.py` — specialist tools 手动注册方式保留
- 测试文件 — 行为不变，接口向后兼容

---

## 5. 新增工具流程（重构后）

```python
# 1. 在工具模块定义
@tool(
    name="MyNewTool",
    description="...",
    category="extension",
    roles=["orchestrator", "chat"],   # ← 在这里声明权限
)
async def my_new_tool(...):
    ...

# 2. 在 tools/__init__.py 的 for _fn in [...] 列表加入
from nimbus.tools.my_new_tool import my_new_tool
for _fn in [..., my_new_tool]:
    ...

# 完成！bootstrap.py 不需要改动
```

> 📝 后续优化（可选）：如果去掉 `tools/__init__.py` 的显式循环（改为扫描模块自动注册），则步骤 2 也可省略，真正实现零配置。

---

## 6. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| `register_tool(roles=...)` 外部覆盖失效 | 低：bootstrap 不再显式传 roles | `register_tool` 保留 `roles` 参数，外部传入仍可覆盖 `td.roles` |
| 删除 kernel_tools 注册块影响单独使用 AgentOS | 中：直接 `AgentOS(...)` 不调用 bootstrap 的场景 | `kernel_tools=True` 改为仅注入 system prompt，不负责注册；或提供 `register_kernel_tools(os)` 便捷函数 |
| 遗留测试 import ALL_TOOLS 失败 | 低 | 变量保留，仅标记废弃 |
| roles=None vs roles=[] 语义混淆 | 低 | 文档明确：`None` = 无限制，`[]` = 任何角色都不可见 |

---

## 7. 实施顺序（建议）

```
Phase 1（核心，1-2h）
  1. base.py: @tool 装饰器加 roles 参数
  2. memo_tools.py: 加 roles=["orchestrator","chat"]
  3. write.py, edit.py: 加 roles=[executor roles]
  4. nimfs_tools.py: NimFSWriteArtifact 加 roles
  5. tools/__init__.py: register_default_tools 改为从 td.roles 取
  6. bootstrap.py: 合并 5 次调用为 1 次

Phase 2（清理，30min）
  7. agentos.py: 删除 kernel_tools 注册块，仅保留 system prompt 注入

Phase 3（可选，后续）
  8. 标记遗留变量为 deprecated
  9. tools/__init__.py: 考虑自动扫描注册，消除显式循环
```

---

## 8. 总结

| 改动 | Before | After |
|------|--------|-------|
| 注册层数 | 6 层 | 4 层（去掉死中转+双重注册） |
| 新增工具配置点 | 2个（工具定义 + bootstrap） | 1个（仅工具定义） |
| roles 维护位置 | bootstrap.py 集中 | 各工具 @tool 装饰器 |
| 遗漏风险 | 高（每次加工具都要改 bootstrap） | 低（零配置） |
| 代码行数（bootstrap orchestrator分支） | ~80 行 | ~40 行 |
