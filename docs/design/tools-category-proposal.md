# Nimbus Agent OS — Tools 分类重构提案

> 版本: Draft v0.3
> 状态: RFC (Request for Comments)
>
> **更新记录**：v0.2 - 根据 ReviewCommittee 反馈更新：ReloadSkills 归属调整、分类契约、安全声明、依赖规则、命名冲突策略、方案 A+ 推荐

## 1. 背景与动机

### 1.1 现状问题
当前所有工具平铺在同一个 `ToolRegistry` 中，没有任何分类概念：
- `ToolDefinition` 只有 name/description/parameters/dangerous/roles，没有 category
- `agentos.py` 的 `__init__` 把 kernel tools、skill tools、ReloadSkills 混在一起注册
- `create_agentos()` 把 orchestration tools 直接 `register_tool`，无分类标识
- `__init__.py` 的 `ALL_TOOLS` 只包含 Read/Write/Edit/Bash 四个，Memo/ReloadSkills 等走特殊路径
- 开发者很难一眼看出某个工具"属于哪一层"、"谁提供的"、"能不能替换"

### 1.2 目标
从概念模型到代码实现，建立清晰的三域分类，让工具的**来源、职责、生命周期**一目了然。

## 2. 设计哲学

### 2.1 pi-coding-agent 安全观
引用 pi-coding-agent 的设计哲学：Agent 拥有 Bash + Write 能力，本身就是"上帝权限"。沙箱、黑名单、白名单本质上是安全幻觉——你禁了 `rm`，Agent 可以 `python3 -c "import os; os.remove('file')"`；你加了 Read 沙箱，Agent 用 `Bash("cat /etc/passwd")` 一样读。

**结论：安全边界应该在人机交互层（用户确认/审批），而不是在工具层互相限制。**

因此本提案：
- 不引入工具层的安全限制机制
- **移除 CoreBash**（Bash 的只读过滤包装），所有角色使用同一个 Bash
- 分类的目的是**清晰性和可维护性**，不是安全隔离

### 2.2 类比：操作系统层次

```
┌────────────────────────────────────────────────┐
│  Applications (用户态应用)                      │
│  → Skill Tools: 动态加载，可热插拔              │
├────────────────────────────────────────────────┤
│  System Services (系统服务)                     │
│  → Extension Tools: 按需挂载的编排/增强能力      │
├────────────────────────────────────────────────┤
│  Kernel / Syscalls (内核)                      │
│  → OS Core Tools: 不可替换的操作原语            │
└────────────────────────────────────────────────┘
```

### 2.3 安全模型作用域声明

> **作用域声明**：本提案的安全哲学（"安全边界在人机交互层"）假设 **单信任域执行环境**（如开发者本地机器、受控容器）。在多租户环境或需要权限分离的部署中，应使用 OS 级隔离（容器、namespace、受限用户），这与工具层分类正交、互不矛盾。

**非工具层安全控制清单**（CoreBash 移除后的替代防线）：

| 防线层 | 措施 |
|--------|------|
| **人机交互层** | 高危命令确认提示、操作摘要 + diff 预览 |
| **审计层** | 结构化 tool invocation 日志（caller / tool / params / result / duration） |
| **组织策略层**（可选） | 企业模式下的审批流 |

这些防线在工具层之外，保证即使 Agent 拥有完整 Bash + Write 能力，仍有可观测性和人类干预点。

## 3. 三域模型

### 3.1 OS Core Tools（操作系统原语）

**定义**：Agent OS 内核自带的、不可替换的基础操作能力。就像操作系统的 syscall，是一切上层能力的基石。

| 工具 | 说明 |
|------|------|
| **Read** | 读取文件内容（文本/图片，智能截断，分页） |
| **Write** | 创建或覆盖文件（自动建目录） |
| **Edit** | 精确文本替换（fuzzy fallback） |
| **Bash** | 执行 Shell 命令（超时控制，输出截断） |

**特征**：
- **不可替换**：这四个工具构成最小完备集，任何 Agent 实例都必须具备
- **无状态**：每次调用独立，不依赖上下文
- **底层原语**：其他所有工具都可以用这四个组合实现（理论上）
- **生命周期**：随 AgentOS 实例创建时注册，实例销毁时释放

### 3.2 Extension Tools（扩展工具）

**定义**：在 Core Tools 之上构建的增强能力。按 Agent 的 profile/角色/场景按需挂载，不同配置的 Agent 可以拥有完全不同的 Extension Tools 组合。

| 工具 | 类型 | 说明 |
|------|------|------|
| **Dispatch** | Meta-Tool (编排) | 将子任务分派给 Executor Agent |
| **Verify** | Meta-Tool (编排) | 运行确定性验证检查 |
| **ReviewCommittee** | Meta-Tool (编排) | 并行多模型代码/架构审查 |
| **Memo** | Utility (增强) | Agent 的持久化记忆管理 |
| **ReloadSkills** | Bridge (桥接) | 重新扫描 skill 目录，热重载所有技能 |

**特征**：
- **按需挂载**：不是所有 Agent 都需要。一个简单的单 Agent 不需要 Dispatch；一个不需要记忆的 Agent 不需要 Memo
- **可替换/可扩展**：完全可以用不同实现替换（比如用不同的 Dispatch 策略），或添加新的 Extension Tool
- **有状态**：部分工具维护状态（Dispatch 有 dispatch_count，Memo 有文件状态）
- **生命周期**：在 `create_agentos(profile=xxx)` 阶段根据 profile 注册

**ReloadSkills 归入 Extension 的理由**：
ReloadSkills 是"管理 Skill 的系统服务"，不应与被管理对象同层。类比 Linux 的 `insmod`/`rmmod`——它们是内核提供的模块管理命令，而非内核模块本身。ReloadSkills 管理 Skill 的生命周期（扫描、加载、卸载），逻辑上属于系统服务层（Extension），而非应用层（Skill）。

**去掉 CoreBash 的理由**：
CoreBash 是 Bash 的一个只读过滤包装（黑名单机制）。但按照 §2.1 的安全哲学，这种工具层面的限制没有实际意义。Agent 总有其他方式绕过。移除它可以：
1. 减少概念复杂度（少一个 tool = 少一份理解负担）
2. 消除"安全幻觉"（不给用户虚假的安全感）
3. 简化注册逻辑（不需要 wrap original Bash + filter）

### 3.3 Skill Tools（技能工具）

**定义**：通过 Skill System（SKILL.md）从外部动态加载的能力。来源是文件系统上的目录，运行时可热插拔。

来自 `SKILL.md` 定义、由 `ScriptTool` 包装的各种能力。例如：

| 工具 | 来源 Skill | 说明 |
|------|-----------|------|
| WebSearch | web-search | 搜索网络 |
| WebFetch | web-search | 获取网页内容 |
| ProjectOverview | code-scout | 项目结构扫描 |
| FindPatterns | code-scout | 代码模式搜索 |
| DepCheck | code-scout | 依赖分析 |
| CreateSkill | skill-creator | 创建新技能目录 |
| AddTool | skill-creator | 向技能添加工具 |
| Greet | hello-world | (测试) 问候 |

**特征**：
- **动态加载**：来自文件系统，不硬编码在源码中
- **热插拔**：通过 ReloadSkills 可以在运行时添加/移除/更新
- **自描述**：每个 Skill 自带 instructions（Markdown body），可注入 System Prompt
- **异构执行**：支持 Python/Bash/Node.js 等多种脚本语言
- **生命周期**：由 SkillManager 管理，随 load/reload 动态变化

### 3.4 分类契约：边界判定规则

为保证分类的客观性和可验证性，定义以下判定标准：

#### Core 判定标准（必须**同时**满足）

| 条件 | 描述 |
|------|------|
| **(a) 原语性** | 执行原语 I/O 操作，不能由其他工具组合实现 |
| **(b) 普遍性** | 所有可设想的 Agent profile 都需要它 |
| **(c) 无状态** | 每次调用独立，不维护跨调用状态 |
| **(d) 无依赖** | 不依赖其他工具 |

#### Extension 判定标准（满足**任一**即可）

- 可由 Core Tools 组合实现，但提供更高层语义价值
- 仅部分 profile 需要
- 维护跨调用状态
- 管理其他域的生命周期

#### Skill 判定标准

- 从外部文件系统动态加载
- 通过 SKILL.md 自描述
- 支持运行时热插拔

#### 验证当前分类

| 工具 | 判定 | 说明 |
|------|------|------|
| Read / Write / Edit / Bash | → **Core** ✅ | 满足 (a)(b)(c)(d) 全部 4 条 |
| Memo | → **Extension** ✅ | 可由 Read + Write 组合实现，违反 (a) |
| Dispatch | → **Extension** ✅ | 仅 multi-agent profile 需要，违反 (b) |
| ReloadSkills | → **Extension** ✅ | 管理 Skill 域的生命周期，属于 Extension 判定条件"管理其他域的生命周期" |

### 3.5 依赖方向规则

域之间的依赖方向必须是 **Skill → Extension → Core**，不可逆向：

```
Skill ──depends──▶ Extension ──depends──▶ Core
  ✅                  ✅                  
Core ──✖──▶ Extension ──✖──▶ Skill（禁止逆向依赖）
```

**具体规则**：

| 规则 | 说明 |
|------|------|
| **Core Tools 不可 import extension 或 skill 模块** | Core 层是自包含的，对上层无感知 |
| **Extension Tools 可依赖 Core Tools，不可依赖 Skill Tools** | Extension 可以调用 Read/Write 等原语，但不能反向依赖动态加载的 Skill |
| **Skill Tools 可依赖 Core 和 Extension** | Skill 可以使用任何底层能力 |

违反依赖方向会导致：底层变更被上层约束（"尾巴摇狗"），以及 Skill 热重载时引发 Core/Extension 的不一致。

### 3.6 命名冲突策略

Core 工具名（`Read`、`Write`、`Edit`、`Bash`）为**保留字**：

- **Skill 注册时若名称与 Core / Extension 工具冲突**，`ToolRegistry` 应拒绝注册并报错（抛出 `ToolNameConflictError`）
- 未来如需放宽，可考虑 namespace 前缀方案（如 `skill:WebSearch`），但当前阶段用简单的保留字拒绝即可

**理由**：工具名在 LLM function calling 中是唯一标识符，名称冲突会导致 LLM 调用歧义、路由错误。保留字机制是最简单有效的防线。

## 4. 代码层面的落地方案

### 4.1 方案 A: 轻量级 — 仅加 category 标签

在 `ToolDefinition` 上增加一个 `category` 字段：

```python
from typing import Literal, Optional

ToolCategory = Literal["core", "extension", "skill"]

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: List[ToolParameter]
    category: Optional[ToolCategory] = None  # NEW — 默认 None（未分类）
    dangerous: bool = False
    roles: Optional[List[str]] = None
```

> **设计决策**：默认值为 `None`（未分类），而非 `"core"`。避免新增工具被误归入核心层——Core 应该是最小集合，需要显式声明。

**配套治理**：
- **启动告警**：AgentOS 启动时对所有 `category=None` 的工具输出 warning 日志
- **CI lint**：所有工具定义必须显式声明 category，否则 CI 报错

注册时标记：
```python
# Core
ToolDefinition(name="Read", category="core", ...)
ToolDefinition(name="Bash", category="core", ...)

# Extension
ToolDefinition(name="Dispatch", category="extension", ...)
ToolDefinition(name="Memo", category="extension", ...)
ToolDefinition(name="ReloadSkills", category="extension", ...)

# Skill
ToolDefinition(name="WebSearch", category="skill", ...)
```

ToolRegistry 增加按 category 查询的能力：
```python
class ToolRegistry:
    def list_by_category(self, category: ToolCategory) -> List[str]: ...
    def get_categories_summary(self) -> Dict[ToolCategory, List[str]]: ...
```

**优点**：改动最小，向后兼容，概念清晰
**缺点**：category 只是标签，不影响行为；Skill 热重载时需要逐个过滤

### 4.2 方案 A+: 轻量标签 + Skill 域独立子 Registry（推荐）

在方案 A 的 category 标签基础上，将 Skill 域拆分到独立的 `SkillToolRegistry`：

```python
class AgentOS:
    def __init__(self):
        self._tools = ToolRegistry()           # core + extension
        self._skill_tools = SkillToolRegistry() # skill only
```

**理由**：Skill 需要热重载（clear + reload），如果混在主 registry 里，reload 时需要逐个过滤 `category="skill"` 的条目，容易误删 core/extension 工具。独立 registry 可以直接 `skill_tools.clear()` 再重新加载，简单且安全。

对外通过 CompositeView 暴露统一接口：
```python
def get_all_tools(self) -> List[ToolDefinition]:
    return self._tools.list_all() + self._skill_tools.list_all()
```

**优点**：
- 保留方案 A 的轻量标签（core/extension 仅靠 category 区分，够用）
- Skill 域的物理隔离解决了热重载的实际工程问题
- 对外接口不变（统一的 `get_all_tools`）

**缺点**：比方案 A 多一个 registry 实例，但复杂度增加很小

### 4.3 方案 B: 中量级 — 三 Registry + 统一 Facade

将 ToolRegistry 拆成三个子 registry，外部通过统一 facade 访问：

```python
class AgentOS:
    def __init__(self):
        self._core_tools = ToolRegistry()      # Read/Write/Edit/Bash
        self._extension_tools = ToolRegistry()  # Dispatch/Verify/Memo/ReloadSkills/...
        self._skill_tools = ToolRegistry()      # WebSearch/...
        
        # Unified view
        self._all_tools = CompositeToolRegistry(
            self._core_tools,
            self._extension_tools,
            self._skill_tools,
        )
```

**优点**：物理隔离，生命周期独立管理
**缺点**：改动较大，Core 和 Extension 之间其实不太需要物理隔离

### 4.4 方案 C: 重量级 — 插件化架构

Extension Tools 和 Skill Tools 都通过统一的 Plugin 接口加载：

```python
class ToolPlugin(ABC):
    @abstractmethod
    def get_tools(self) -> List[ToolDefinition]: ...
    @abstractmethod
    def get_handlers(self) -> Dict[str, Callable]: ...

class DispatchPlugin(ToolPlugin): ...
class MemoPlugin(ToolPlugin): ...
class SkillPlugin(ToolPlugin): ...
```

**优点**：最大灵活性，支持第三方插件
**缺点**：过度工程化，当前阶段不必要

### 4.5 推荐

**推荐方案 A+**（轻量标签 + Skill 域独立 Registry），理由：

1. **方案 A 的标签**足够处理 Core / Extension 的区分——它们生命周期相似（随 AgentOS 实例创建），只需概念分层
2. **Skill 的热重载生命周期需要物理隔离**——这是实际工程需求，不是理论洁癖。`skill_tools.clear()` 比"遍历所有工具过滤 category=skill 再逐个删除"更安全、更简洁
3. **改动量适中**——比方案 A 仅多一个 `SkillToolRegistry` 实例，远小于方案 B 的三 registry 拆分
4. **为方案 B 预留空间**——如果未来 Extension 也需要独立生命周期管理，再拆不迟

## 5. 变更清单

### 5.1 去掉 CoreBash

| 文件 | 变更 |
|------|------|
| `src/nimbus/orchestration/tools.py` | 移除 `CORE_BASH_BLACKLIST_PREFIXES`、`is_command_readonly()`、`register_core_bash()` 及相关代码 |
| `src/nimbus/agentos.py` (`create_agentos`) | 移除 `register_core_bash(os, ...)` 调用 |
| `src/nimbus/orchestration/prompts.py` | Core 的 prompt 中移除 "CoreBash" 引用，改为 "Bash" |
| `web-ui/.../ToolDisplay.tsx` | 如有 CoreBash 专用渲染逻辑则移除 |
| 测试文件 | 更新涉及 CoreBash 的测试 |

### 5.2 加 category 标签 + Skill 域拆分（方案 A+）

| 文件 | 变更 |
|------|------|
| `src/nimbus/tools/base.py` | `ToolDefinition` 增加 `category: Optional[ToolCategory] = None` 字段 |
| `src/nimbus/tools/base.py` (`ToolRegistry`) | 增加 `list_by_category()` 和 `get_categories_summary()` 方法；增加启动时 `category=None` 告警 |
| `src/nimbus/tools/__init__.py` | `READ_TOOL` / `WRITE_TOOL` / `EDIT_TOOL` / `BASH_TOOL` 标记 `category="core"` |
| `src/nimbus/tools/memo.py` | `MEMO_TOOL_DEF` 标记 `category="extension"` |
| `src/nimbus/orchestration/tools.py` | `DISPATCH_TOOL_DEF` / `VERIFY_TOOL_DEF` 标记 `category="extension"` |
| `src/nimbus/orchestration/review_tool.py` | `REVIEW_TOOL_DEF` 标记 `category="extension"` |
| `src/nimbus/agentos.py` | ReloadSkills 标记 `category="extension"`；新建 `SkillToolRegistry` 实例管理 Skill 域 |
| `src/nimbus/skills/tools.py` (`ScriptTool`) | 动态生成的 tool_definition 标记 `category="skill"` |
| CI 配置 | 增加 lint 规则：所有工具必须显式声明 category |

### 5.3 命名冲突保护

| 文件 | 变更 |
|------|------|
| `src/nimbus/tools/base.py` | `ToolRegistry.register()` 增加保留字检查，冲突时抛出 `ToolNameConflictError` |

### 5.4 更新设计文档

| 文件 | 变更 |
|------|------|
| `docs/design/tools-skills-system.md` | 更新三域模型描述、移除 CoreBash 相关内容、补充 category 字段说明 |

## 6. 迁移策略

| 阶段 | 内容 | 风险 |
|------|------|------|
| **Phase 1** | 加 `category` 字段（默认 `None`）+ Skill 域拆分独立 `SkillToolRegistry` | 低 — 纯增量，不破坏现有逻辑 |
| **Phase 2** | 标记所有工具的 category + CI lint 规则强制声明 | 低 — 逐个文件改 |
| **Phase 3** | 移除 CoreBash，所有角色统一用 Bash | 中 — 需更新依赖 CoreBash 的代码和测试 |
| **Phase 4** | 更新 prompts 和文档 | 低 |
| **Phase 5** | 按需扩展（如 Extension 也需独立 registry → 演进为方案 B） | 视需求而定 |

每个 Phase 都可以独立 merge，不需要一次性完成。

## 7. 完整工具清单（重构后）

| 工具 | Category | 提供者 | 生命周期 | 可替换 |
|------|----------|--------|----------|--------|
| Read | `core` | AgentOS 内核 | 随实例创建 | ❌ |
| Write | `core` | AgentOS 内核 | 随实例创建 | ❌ |
| Edit | `core` | AgentOS 内核 | 随实例创建 | ❌ |
| Bash | `core` | AgentOS 内核 | 随实例创建 | ❌ |
| Dispatch | `extension` | orchestration 模块 | 按 profile 注册 | ✅ |
| Verify | `extension` | orchestration 模块 | 按 profile 注册 | ✅ |
| ReviewCommittee | `extension` | orchestration 模块 | 按 profile 注册 | ✅ |
| Memo | `extension` | tools/memo 模块 | 按 profile 注册 | ✅ |
| ReloadSkills | `extension` | AgentOS / SkillManager | 随实例创建 | ❌ |
| ~~CoreBash~~ | ~~已移除~~ | — | — | — |
| WebSearch | `skill` | skills/web-search | 动态加载 | ✅ 热插拔 |
| WebFetch | `skill` | skills/web-search | 动态加载 | ✅ 热插拔 |
| (其他 Skill Tools) | `skill` | 各 SKILL.md | 动态加载 | ✅ 热插拔 |
| ~~ScrollHistory~~ | — | 未实装，待定 | — | — |
| ~~CopyToClipboard~~ | — | 未实装，待定 | — | — |

---

*本提案为 RFC 状态，欢迎评审反馈。*
