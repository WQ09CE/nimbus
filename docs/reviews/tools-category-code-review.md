# Code Review: Tools Classification Phase 1 实现

> **Reviewer**: Core Agent  
> **Commit**: `faeb42e refactor: Tools Classification Phase 1 & Lint Fixes`  
> **对照文档**: `docs/design/tools-category-proposal.md` v0.2  
> **日期**: 2025-02-11

## 总结

| 维度 | 完成度 | 说明 |
|------|--------|------|
| 基础设施（ToolDefinition.category, ToolRegistry 方法） | ✅ 100% | category 字段、list_by_category、get_categories_summary、clear() 均已实现 |
| Skill 域独立 Registry | ⚠️ 50% | `_skill_tools` 已创建，skill tools 已注册其中，但**未接入系统的工具可见性和执行链路** |
| Category 标记 | ⚠️ 12% | 仅 skill tools 标记了 `category="skill"`，其余 8+ 个工具全是 None |
| CoreBash 移除 | ❌ 0% | 完全未动 |
| 命名冲突保护 | ❌ 0% | 未实现 |
| 测试 | ❌ | `test_register_duplicate_raises` 失败 |

---

## 🔴 P0: Skill Tools 断路（最紧急）

### 问题描述

`_skill_tools` 独立 Registry 已创建（`agentos.py:167`），skill tools 也已注册其中（`agentos.py:342`）。但整个系统获取工具定义和执行工具的链路**都只查 `self._tools`**，完全忽略 `_skill_tools`。

这意味着 WebSearch、WebFetch 等所有 skill tools **注册到了空气中**——LLM 看不到定义，调用时也找不到 handler。**比重构前更糟**（以前至少注册在 `_tools` 里能用）。

### 受影响位置（6 处）

**工具定义获取（LLM 看不到 skill tools）：**

1. `agentos.py:467` — `spawn()` 内获取工具列表
```python
tools_list = self._tools.get_definitions(format="openai", role=_role)
# ❌ 不包含 _skill_tools
```

2. `agentos.py:720` — `chat()` 内获取工具列表
```python
tools_list = self._tools.get_definitions(format="openai", role="chat")
# ❌ 不包含 _skill_tools
```

3. `agentos.py:828` — 另一处获取工具列表（如果存在）
```python
tools_list = self._tools.get_definitions(format="openai")
# ❌ 不包含 _skill_tools
```

**工具执行（调用 skill tool 会报 tool not found）：**

4. `agentos.py:1465` — `_create_gate()` 传给 KernelGate 的 tool_executor
```python
def _create_gate(self, pid, role, local_tools=None):
    return KernelGate(
        pid=pid,
        tool_executor=self._tools,  # ❌ 不包含 _skill_tools
        ...
    )
```

**工具列表查询：**

5. `agentos.py:1398` — `list_tools()`
```python
def list_tools(self):
    return self._tools.list_tools()  # ❌ 不包含 _skill_tools
```

6. `agentos.py:1437` — 状态信息中的工具列表
```python
"tools": self._tools.list_tools(),  # ❌ 不包含 _skill_tools
```

### 修复方案

按提案 A+ 的设计，需要实现 CompositeView：

**方案一（推荐，最简单）**：在 AgentOS 上加几个聚合方法，在每个调用点合并两个 registry 的结果：

```python
# agentos.py — 新增聚合方法

def _get_all_definitions(self, format: str = "openai", role: Optional[str] = None) -> List:
    """获取所有工具定义（core + extension + skill）"""
    return self._tools.get_definitions(format=format, role=role) + \
           self._skill_tools.get_definitions(format=format, role=role)

def _find_tool(self, name: str):
    """在两个 registry 中查找工具"""
    entry = self._tools.get(name)
    if entry:
        return entry
    return self._skill_tools.get(name)

def _execute_tool(self, name: str, params: dict, **context):
    """在两个 registry 中查找并执行工具"""
    entry = self._tools.get(name)
    if entry is None:
        entry = self._skill_tools.get(name)
    if entry is None:
        raise ToolExecutionError(name, f"Tool '{name}' not found")
    # ... execute
```

然后把 6 处调用点全部替换为聚合方法。

**方案二**：写一个 `CompositeToolRegistry` 包装类，实现与 `ToolRegistry` 相同的接口，内部代理到多个子 registry。然后 `_create_gate` 传 composite 实例。

两种方案都可以，方案一改动更小。

---

## 🔴 P0: CoreBash 移除

### 问题描述

提案明确要求移除 CoreBash（§3.2 "去掉 CoreBash 的理由"）。当前代码完全未动：

### 需要改的位置（4 处）

1. **`src/nimbus/orchestration/tools.py:253-426`** — 移除以下代码段：
   - `CORE_BASH_BLACKLIST_PREFIXES` (L253-292)
   - `CORE_BASH_BLACKLIST_PATTERNS` (L293-298)
   - `SAFE_REDIRECT_TARGETS` (L300-302)
   - `_has_dangerous_redirect()` (L305-316)
   - `_is_curl_safe()` (L319-333)
   - `is_command_readonly()` (L336-377)
   - `register_core_bash()` (L382-426)

2. **`src/nimbus/agentos.py:1571-1575`** — 移除 CoreBash 注册：
```python
# 删除这 3 行：
from nimbus.orchestration.tools import register_core_bash  # L1571
# Register CoreBash                                        # L1574
register_core_bash(os, roles=["core", "chat"])              # L1575
```

3. **`src/nimbus/orchestration/prompts.py:40-41`** — 修改 CORE_INSTRUCTIONS：
```python
# 把：
- **CoreBash**: Read-only exploration (ls, grep, find, cat). NO modification commands.
# 改为：
- **Bash**: Execute shell commands (ls, grep, find, cat, git, python, etc.)
```

4. **`src/nimbus/orchestration/prompts.py`** — CORE_INSTRUCTIONS 中 "Your Toolkit" 部分也需要更新，把 CoreBash 引用改成 Bash。

同时，在 `create_agentos(profile="core")` 中，Bash 不应该只给 executor role，core 也应该能用。修改 `agentos.py` 中的工具注册逻辑：
```python
# 当前（错误）：
register_default_tools(os, workspace=ws, tools=["Read"])                              # 所有人
register_default_tools(os, workspace=ws, tools=["Write", "Edit", "Bash"], roles=["executor"])  # 仅 executor

# 应改为：
register_default_tools(os, workspace=ws, tools=["Read", "Bash"])                       # 所有人
register_default_tools(os, workspace=ws, tools=["Write", "Edit"], roles=["executor"])  # 仅 executor
```

---

## 🟡 P1: Category 标记缺失

### 问题描述

只有 skill tools 通过 `agentos.py:338` 标记了 `category="skill"`，其余工具的 category 全是 `None`。

### 需要改的位置

**Core Tools（4 个）** — 在 `src/nimbus/tools/__init__.py` 的字典定义中无法直接加 category（它们是 dict 不是 ToolDefinition）。有两个改法：

改法 A（推荐）：在 `agentos.py` 的 `_parse_legacy_tool()` 中给 Core tools 打标：
```python
def _parse_legacy_tool(data: Dict[str, Any]) -> ToolDefinition:
    # ... 现有参数解析 ...
    return ToolDefinition(
        name=data["name"],
        description=data.get("description", ""),
        parameters=params,
        category="core",  # 加这一行
    )
```

改法 B：把 `__init__.py` 的 READ_TOOL/WRITE_TOOL/EDIT_TOOL/BASH_TOOL 从 dict 改成 ToolDefinition 对象。改动更大，暂不推荐。

**Extension Tools（5 个）**：

1. `src/nimbus/orchestration/tools.py` — `DISPATCH_TOOL_DEF` 和 `VERIFY_TOOL_DEF` 是 dict，在 `agentos.py` 的 `register_tool()` 调用时没传 category。修改 `agentos.py` 中对应的 `os.register_tool(...)` 调用，找到一种方式传入 category。

   最简单的做法：在 `register_tool()` 方法签名中增加 `category` 参数：
   ```python
   def register_tool(self, name, func, description="", parameters=None, 
                      roles=None, category=None):  # 加 category
       # ... 
       definition = ToolDefinition(..., category=category)
   ```
   然后在注册 Dispatch/Verify/ReviewCommittee 时传 `category="extension"`。

2. `src/nimbus/tools/memo.py` — Memo 通过 `create_memo_tool()` 返回 dict 定义，在 `agentos.py` 中注册。同样需要标记 `category="extension"`。

3. `src/nimbus/agentos.py` — `ReloadSkills` 的 ToolDefinition 创建处（约 L184），加 `category="extension"`。

---

## 🟡 P1: 命名冲突保护

### 问题描述

提案 §3.6 要求 Core 工具名为保留字，skill 注册时若冲突应拒绝。

### 修复

在 `src/nimbus/tools/base.py` 的 `ToolRegistry` 中：

```python
RESERVED_TOOL_NAMES = frozenset({"Read", "Write", "Edit", "Bash"})

class ToolNameConflictError(Exception):
    """Raised when a tool name conflicts with a reserved name."""
    pass

class ToolRegistry:
    def register(self, definition, func):
        if definition.name in self._tools:
            # 允许覆盖（保持现有行为，但考虑未来收紧）
            pass
        # 如果是 skill 类型工具，检查是否与保留名冲突
        if definition.category == "skill" and definition.name in RESERVED_TOOL_NAMES:
            raise ToolNameConflictError(
                f"Skill tool '{definition.name}' conflicts with reserved Core tool name"
            )
        self._tools[definition.name] = (definition, func)
```

---

## 🟡 P2: 测试修复

### test_register_duplicate_raises

`tests/test_tools_base.py:228` — 测试期望 `register()` 重复注册抛 `ValueError`，但当前代码允许静默覆盖。

两个选择：
- A）修改 `register()` 让它对重复注册抛错（提案未要求，可能破坏其他逻辑）
- B）修改测试以匹配当前行为（静默覆盖）

**建议选 B**：把测试改为验证"重复注册不报错，第二次注册覆盖第一次"。

---

## 修复优先级

| 优先级 | 任务 | 预估改动量 |
|--------|------|-----------|
| **P0** | Skill tools 接入系统（CompositeView 或聚合方法） | ~30 行，6 处调用点 |
| **P0** | CoreBash 移除 | ~200 行删除 + 少量修改 |
| **P1** | Category 标记（core × 4, extension × 5） | ~15 行 |
| **P1** | 命名冲突保护 | ~15 行 |
| **P2** | 测试修复 | ~5 行 |

P0 务必一起做完。Skill tools 断路是回归 bug（比改之前更糟），CoreBash 是提案的明确要求。

---

*Review 完毕。修复后请再次提交 review。*
