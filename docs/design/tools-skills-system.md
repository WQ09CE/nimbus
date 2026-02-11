# Nimbus Agent OS — Tools & Skills 系统设计文档

> **版本**: 1.0  
> **最后更新**: 2025-01  
> **状态**: Living Document

---

## 1. 概述

Nimbus Agent OS 采用**三层工具体系**，将 AI Agent 的能力从底层原语到高层技能进行分层组织：

```
Kernel Tools → Orchestration Tools → Skill Tools
```

### 设计哲学

| 原则 | 含义 |
|------|------|
| **"4 tools are all you need"** | Read / Write / Edit / Bash 四个内核工具即可完成几乎所有文件系统操作，其余工具皆为上层封装 |
| **"File System as API"** | 技能以目录形式存在，`SKILL.md` 即接口定义，目录即能力——无需注册中心，放入即可用 |
| **Dual-Agent Architecture** | Core Agent（架构师，只读探索）与 Executor Agent（工程师，全权限执行）分离，最小权限原则 |

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AgentOS / ToolRegistry                       │
│                         （统一注册中心）                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─── Layer 3: Skill Tools (动态加载) ──────────────────────────┐   │
│  │  web-search    code-scout    skill-creator    hello-world    │   │
│  │  (来自 SKILL.md，ScriptTool 包装，运行时热加载)               │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              ▲                                      │
│                              │ SkillManager.load()                  │
│  ┌─── Layer 2: Orchestration Tools ─────────────────────────────┐   │
│  │  Dispatch │ Verify │ ReviewCommittee │ CoreBash              │   │
│  │  (编排、验证、多模型审查、安全过滤)                            │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              ▲                                      │
│                              │ 直接调用                             │
│  ┌─── Layer 1: Kernel Tools (内核工具) ─────────────────────────┐   │
│  │  Read │ Write │ Edit │ Bash │ Memo │ ReloadSkills            │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─── Planned (未实装) ─────────────────────────────────────────┐   │
│  │  Context Tools: ScrollHistory │ CopyToClipboard              │   │
│  │  (仅有 JSON Schema 定义，无执行实现)                          │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Layer 1: Kernel Tools（内核工具）

### 3.1 核心工具集

| 工具 | 文件位置 | 功能描述 | 关键特性 |
|------|----------|----------|----------|
| **Read** | `src/nimbus/tools/read.py` | 读取文件内容，支持文本和图片 | 支持 `offset`/`limit` 分页读取；图片作为附件发送；截断至 2000 行或 50KB |
| **Write** | `src/nimbus/tools/write.py` | 创建或覆盖文件 | 自动创建父目录；完整覆盖写入 |
| **Edit** | `src/nimbus/tools/edit.py` | 精确文本替换编辑 | `oldText` 精确匹配 → 替换为 `newText`；匹配失败时回退到模糊匹配 |
| **Bash** | `src/nimbus/tools/bash.py` | 执行 shell 命令 | 可配置超时（默认 60s）；输出截断至 2000 行/50KB；截断时保存完整输出到临时文件 |
| **Memo** | `src/nimbus/tools/memo.py` | 读写持久化备忘录 `.nimbus/memo.md` | 支持 `read`/`write`/`append`/`clear` 四种操作；MemoManager 管理生命周期；Agent 唯一的长期记忆 |
| **ReloadSkills** | `src/nimbus/skills/tools.py` | 从磁盘重新加载所有技能 | 可指定额外扫描目录；调用后新技能立即可用 |

### 3.2 基础设施

#### ToolParameter → JSON Schema 转换

```python
@dataclass
class ToolParameter:
    name: str
    type: str           # "string", "integer", "boolean", "array", "object"
    description: str
    required: bool = True
    enum: list = None   # 可选枚举值
```

每个 `ToolParameter` 自动转换为 JSON Schema 属性，组装进 `inputSchema` / `parameters` 字段。

#### ToolDefinition 双格式支持

```python
@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: List[ToolParameter]
    handler: Callable
    
    def to_claude_format(self) -> dict:
        """Claude Tool Use 格式 (inputSchema)"""
        ...
    
    def to_openai_format(self) -> dict:
        """OpenAI Function Calling 格式 (parameters)"""
        ...
```

同一个 `ToolDefinition` 可以输出为 **Claude Tool Use** 和 **OpenAI Function Calling** 两种 API 格式，实现多模型兼容。

#### ToolRegistry: 注册 / 查找 / 执行

```python
class ToolRegistry:
    def register(tool: ToolDefinition) -> None    # 注册工具（允许覆盖）
    def get(name: str) -> ToolDefinition           # 按名称查找
    def list_tools(role: str = None) -> List       # 列出工具，支持角色过滤
    async def execute(name: str, **kwargs) -> Any  # 执行工具，sync/async 透明处理
```

- **sync/async 透明处理**：如果 handler 是同步函数，自动包装为 async 执行
- **角色过滤**：根据 Agent 角色返回其可用的工具子集

#### @tool 装饰器: 声明式工具定义

```python
@tool(name="Read", description="Read file contents", parameters=[...])
def read_file(file_path: str, offset: int = None, limit: int = None):
    ...
```

装饰器将函数注册到全局 ToolRegistry，开发者只需声明元信息。

#### Sandbox 沙箱

`sandbox.py` 提供文件系统访问控制，限制工具只能操作工作区内的文件。

> ⚠️ **当前状态**：Read 工具已切换到 YOLO Mode，绕过沙箱检查。

### 3.3 关键文件

| 文件 | 职责 |
|------|------|
| `src/nimbus/tools/__init__.py` | 工具包入口，定义 `ALL_TOOLS` 列表和 `TOOL_FUNCTIONS` 映射 |
| `src/nimbus/tools/base.py` | `ToolParameter`, `ToolDefinition`, `ToolRegistry`, `@tool` 装饰器 |
| `src/nimbus/tools/read.py` | Read 工具实现 |
| `src/nimbus/tools/write.py` | Write 工具实现 |
| `src/nimbus/tools/edit.py` | Edit 工具实现 |
| `src/nimbus/tools/bash.py` | Bash 工具实现 |
| `src/nimbus/tools/memo.py` | Memo 工具 + MemoManager |
| `src/nimbus/tools/sandbox.py` | 沙箱机制（文件访问控制） |
| `src/nimbus/tools/context_tools.py` | ScrollHistory / CopyToClipboard（仅 Schema，未实装） |
| `src/nimbus/tools/utils.py` | 工具辅助函数 |

---

## 4. Layer 2: Orchestration Tools（编排工具）

### 4.1 Dual-Agent 架构

```
┌──────────────────────┐          Dispatch          ┌──────────────────────┐
│     Core Agent       │ ─────────────────────────▶ │   Executor Agent     │
│     （架构师）        │                            │   （工程师）          │
│                      │ ◀───── diff + result ───── │                      │
│  权限: 只读探索       │                            │  权限: 全读写执行     │
│  工具: Read,CoreBash │                            │  工具: Read,Write,   │
│    Dispatch,Verify   │                            │    Edit,Bash         │
│    ReviewCommittee   │                            │                      │
│    Memo,ReloadSkills │                            │                      │
│    Skill Tools       │                            │                      │
└──────────────────────┘                            └──────────────────────┘
```

- **Core Agent**：负责规划、分析、拆解任务，只能通过 `Dispatch` 委派写操作
- **Executor Agent**：接收具体任务，拥有完整的文件读写和命令执行权限

### 4.2 Dispatch 工具

**核心流程**：

```
检查调度限制 → 注入上下文 → 工作区快照 → spawn 子进程(Executor)
    → Executor 执行任务 → 工作区 diff → 返回结果摘要
```

**配置项**：

| 参数 | 值 | 说明 |
|------|-----|------|
| `max_dispatches` | 8 | 单次会话最大调度次数 |
| `total_timeout` | 900s | 所有调度的总时间限制 |
| `single_timeout` | 120s | 单次调度超时 |
| `executor_max_iterations` | 15 | Executor 单次调度最大迭代轮次 |

**模型别名映射**：

```python
MODEL_ALIASES = {
    "claude":   "claude-sonnet-4-20250514",
    "gpt":      "gpt-4.1",
    "gemini":   "gemini-2.5-flash",
    "o3":       "o3",
    "o4-mini":  "o4-mini",
    ...
}
```

**自动上下文注入**：

上一次 Dispatch 执行产生的变更文件内容会被自动注入到下一次 Dispatch 的上下文中，确保 Executor 始终了解最新状态，无需 Core Agent 手动传递。

### 4.3 Verify 工具

提供 **8 种检查类型**，用于验证 Executor 的执行结果：

| 检查类型 | 说明 |
|----------|------|
| `file_exists` | 文件是否存在 |
| `file_not_exists` | 文件是否不存在 |
| `file_contains` | 文件是否包含指定内容 |
| `file_not_contains` | 文件是否不包含指定内容 |
| `command_succeeds` | 命令是否执行成功（exit code 0） |
| `command_output_contains` | 命令输出是否包含指定字符串 |
| `port_listening` | 指定端口是否在监听 |
| `process_running` | 指定进程是否在运行 |

用法示例：
```json
{
  "checks": [
    {"type": "file_exists", "path": "src/app.py"},
    {"type": "command_succeeds", "command": "pytest tests/"},
    {"type": "port_listening", "port": 8080}
  ]
}
```

### 4.4 ReviewCommittee 工具

多模型并行审查机制：

```
spawn 多个 Reviewer Agent（不同模型）
    → 并行执行审查 (wait_all)
    → 保存审查报告到 docs/reviews/
    → 返回格式化的多视角审查结果
```

每个 Reviewer 是纯推理 Agent（无工具），从不同模型视角审视代码/设计，提供多元化反馈。

### 4.5 CoreBash 安全过滤

Core Agent 使用的受限 Bash 工具，通过**黑名单机制**阻止危险操作：

| 黑名单类别 | 示例 |
|------------|------|
| 文件写操作 | `rm`, `mv`, `cp`, `mkdir`, `touch`, `chmod`, `chown` |
| 包管理 | `pip install`, `npm install`, `apt-get`, `brew install` |
| Git 写操作 | `git commit`, `git push`, `git merge`, `git rebase` |
| 代码执行 | `python` (非只读), `node -e`, `eval`, `exec` |
| 危险网络操作 | `curl -X POST`, `wget`, `ssh`, `scp` |

**额外安全检查**：

- **重定向检测**：阻止 `>`, `>>` 等输出重定向
- **curl 安全检查**：只允许 GET 请求，阻止 `-X POST`/`-d`/`--data` 等
- **管道分段检查**：对管道 `|` 分隔的每一段分别检查黑名单

### 4.6 Prompt 系统

```python
class PromptManager:
    def get_system_prompt(role: str, model_id: str) -> str:
        """根据角色和模型生成 system prompt"""
```

`PromptManager` 组合生成策略：

| 维度 | 选项 | 说明 |
|------|------|------|
| **role** | `core` / `executor` | 决定工具列表、行为准则、权限边界 |
| **model_id** | `claude` / `gpt` / `gemini` / ... | 决定 prompt 风格、格式偏好、特殊指令 |

最终 system prompt = 基础规则 + 角色规则 + 模型适配 + 技能指令（来自 SKILL.md）

### 4.7 关键文件

| 文件 | 职责 |
|------|------|
| `src/nimbus/orchestration/tools.py` | Verify、CoreBash 工具实现；`DISPATCH_TOOL_DEF`、`VERIFY_TOOL_DEF` 定义 |
| `src/nimbus/orchestration/dispatch_tool.py` | `DispatchTool` 类：Core→Executor 调度核心逻辑 |
| `src/nimbus/orchestration/review_tool.py` | `ReviewTool` 类：多模型并行审查 |
| `src/nimbus/orchestration/prompts.py` | `PromptManager`：角色×模型 system prompt 生成 |
| `src/nimbus/orchestration/workspace_diff.py` | 工作区快照与 diff 比较 |

---

## 5. Layer 3: Skill System（动态技能系统）

### 5.1 设计理念

> **"File System as API"** — 目录即能力

遵循 **Agent Skills Open Standard**：

- 一个目录 = 一个技能
- `SKILL.md` = 技能接口定义（frontmatter 元数据 + markdown 指令）
- 脚本文件 = 工具实现
- 放入 `skills/` 目录即自动发现，无需手动注册

### 5.2 架构组件

```
┌─────────────┐     解析      ┌───────────────┐    注册     ┌──────────────┐
│  SKILL.md   │ ───────────▶ │ SkillManifest │ ─────────▶ │ ToolRegistry │
│  (YAML +    │              │ SkillToolConfig│            │              │
│   Markdown) │              └───────────────┘            └──────────────┘
└─────────────┘                     │                           ▲
                                    │ 包装                      │
                                    ▼                           │
                             ┌──────────────┐    加载     ┌──────────────┐
                             │  ScriptTool  │ ◀───────── │ SkillLoader  │
                             │  (执行包装器) │            │              │
                             └──────────────┘            └──────────────┘
                                                               ▲
                                                               │ 协调
                                                         ┌──────────────┐
                                                         │ SkillManager │
                                                         │ (生命周期)    │
                                                         └──────────────┘
```

| 组件 | 职责 |
|------|------|
| **SkillManifest** | 解析 `SKILL.md` frontmatter，提取元数据（name, version, description, tools） |
| **SkillToolConfig** | 单个工具的配置：脚本路径、参数定义、描述 |
| **SkillManager** | 技能生命周期管理：发现、加载、注册、热重载 |
| **ScriptTool** | 将脚本文件包装为可调用的工具函数 |
| **SkillLoader** | 从文件系统加载并解析技能目录 |

### 5.3 SKILL.md 规范

```markdown
---
name: web-search
version: 1.0.0
description: 搜索网络获取最新信息
tools:
  - name: web_search
    description: 使用搜索引擎搜索关键词
    script: search.py
    parameters:
      - name: query
        type: string
        description: 搜索关键词
        required: true
      - name: max_results
        type: integer
        description: 最大结果数量
        required: false
---

# Web Search Skill

## Instructions

当用户需要搜索网络信息时，使用 `web_search` 工具。
优先使用精确的搜索关键词，避免过于宽泛的查询。
返回结果时注明信息来源。
```

- **YAML frontmatter**：`name` / `version` / `description` / `tools` 列表
- **Markdown body**：`Instructions` 部分会被注入到 Agent 的 System Prompt 中

### 5.4 ScriptTool 执行机制

**自动解释器识别**：

| 文件扩展名 | 解释器 |
|------------|--------|
| `.py` | `python3` |
| `.sh` | `bash` |
| `.js` | `node` |

**kwargs → CLI 参数转换**：

```python
# 调用: web_search(query="AI news", max_results=5)
# 转换为:
# python3 search.py --query "AI news" --max_results 5
```

**异步子进程执行**：

```python
async def execute(self, **kwargs) -> str:
    cmd = [interpreter, script_path] + kwargs_to_args(kwargs)
    proc = await asyncio.create_subprocess_exec(*cmd, ...)
    stdout, stderr = await proc.communicate()
    return stdout
```

### 5.5 已有技能实例

| 技能 | 位置 | 工具 | 说明 |
|------|------|------|------|
| **web-search** | `skills/web-search/SKILL.md` | `web_search` | 网络搜索，获取最新信息 |
| **code-scout** | `examples/skills/code-scout/SKILL.md` | `scout_codebase` | 代码库扫描与分析 |
| **skill-creator** | `examples/skills/skill-creator/SKILL.md` | `create_skill` | 自动创建新技能 |
| **hello-world** | `tests/fixtures/skills/hello/SKILL.md` | `hello` | 测试用示例技能 |

### 5.6 关键文件

| 文件 | 职责 |
|------|------|
| `src/nimbus/skills/__init__.py` | 技能包入口 |
| `src/nimbus/skills/models.py` | `SkillManifest`, `SkillToolConfig` 数据模型 |
| `src/nimbus/skills/loader.py` | `SkillLoader`：从文件系统加载技能 |
| `src/nimbus/skills/manager.py` | `SkillManager`：技能生命周期管理 |
| `src/nimbus/skills/tools.py` | `ScriptTool`, `ReloadSkills` 工具 |
| `src/nimbus/skills/README.md` | 技能系统说明文档 |

---

## 6. 角色权限系统（RBAC）

| 角色 | 可用工具 | 说明 |
|------|----------|------|
| **Core** | `Read`, `CoreBash`, `Dispatch`, `Verify`, `ReviewCommittee`, `Memo`, `ReloadSkills`, Skill Tools | 架构师角色，只读探索 + 编排调度 |
| **Executor** | `Read`, `Write`, `Edit`, `Bash` | 工程师角色，全权限执行文件操作 |
| **Reviewer** | _(无工具)_ | 纯推理角色，用于 ReviewCommittee 中的审查 |
| **Standard** | 所有工具 | 默认角色，不受限制 |

权限控制流程：

```
Agent 请求工具 → ToolRegistry.list_tools(role) → 过滤可用工具 → 返回工具列表
```

---

## 7. 工具注册流程

采用**两阶段注册**机制：

### 阶段一：AgentOS.__init__ 自动注册

```python
class AgentOS:
    def __init__(self):
        self.registry = ToolRegistry()
        
        # 自动注册所有 Kernel Tools
        for tool_def in ALL_TOOLS:
            self.registry.register(tool_def)
        
        # 加载并注册 Skill Tools
        self.skill_manager = SkillManager()
        self.skill_manager.load_all()
```

### 阶段二：create_agentos(profile=xxx) 按角色注册

```python
def create_agentos(profile: str = "standard") -> AgentOS:
    os = AgentOS()
    
    if profile == "core":
        # 注册编排工具: Dispatch, Verify, ReviewCommittee, CoreBash
        os.registry.register(DISPATCH_TOOL_DEF)
        os.registry.register(VERIFY_TOOL_DEF)
        ...
    elif profile == "executor":
        # 仅保留 Read, Write, Edit, Bash
        ...
    
    return os
```

---

## 8. 已知问题与改进方向

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | `context_tools.py` 的 `ScrollHistory`/`CopyToClipboard` 仅有 Schema 未实装 | 🟡 低 | 定义了 JSON Schema 但无 handler 实现，调用会失败 |
| 2 | `sandbox.py` 的 Read 切换到 YOLO Mode 绕过沙箱 | 🔴 高 | Read 工具不再受沙箱路径限制，可读取工作区外的文件 |
| 3 | Skill Tools 没有角色限制 | 🟡 中 | 所有加载的技能工具对所有角色可见，无法针对特定角色限制技能访问 |
| 4 | `memo.py` 特殊注册路径，不在 `ALL_TOOLS` 中 | 🟡 低 | Memo 工具通过独立路径注册，未纳入 `ALL_TOOLS` 统一管理 |
| 5 | `@tool` 装饰器全局注册 vs AgentOS 直接注册两套路径 | 🟡 中 | 存在两种注册机制，可能导致工具重复注册或遗漏 |
| 6 | `ToolRegistry.register` 允许静默覆盖 | 🟡 中 | 同名工具重复注册时无警告，可能导致意外行为 |
| 7 | Skill 热重载无法清除已注入的 System Prompt instructions | 🟡 中 | `ReloadSkills` 重新加载工具定义，但之前注入的 prompt 指令仍残留 |
| 8 | `SkillManager` 有重复赋值 bug (`self.skill_dirs`) | 🟢 低 | `self.skill_dirs` 被赋值两次，第二次覆盖第一次 |

### 改进方向

- **实装 Context Tools**：完成 ScrollHistory / CopyToClipboard 的 handler 实现
- **沙箱加固**：恢复 Read 的沙箱检查，或设计更灵活的白名单机制
- **技能 RBAC**：在 `SKILL.md` 中增加 `roles` 字段，限制技能可用角色
- **统一注册路径**：消除 `@tool` 全局注册与 AgentOS 注册的二元性
- **注册冲突检测**：`ToolRegistry.register` 增加重复注册警告或异常
- **热重载完整性**：ReloadSkills 时同步清理已注入的 prompt instructions

---

## 附录 A: 完整工具清单

| 工具名称 | 层级 | 可用角色 | 文件位置 | 状态 |
|----------|------|----------|----------|------|
| `Read` | Layer 1 | Core, Executor, Standard | `src/nimbus/tools/read.py` | ✅ 活跃 |
| `Write` | Layer 1 | Executor, Standard | `src/nimbus/tools/write.py` | ✅ 活跃 |
| `Edit` | Layer 1 | Executor, Standard | `src/nimbus/tools/edit.py` | ✅ 活跃 |
| `Bash` | Layer 1 | Executor, Standard | `src/nimbus/tools/bash.py` | ✅ 活跃 |
| `Memo` | Layer 1 | Core, Standard | `src/nimbus/tools/memo.py` | ✅ 活跃 |
| `ReloadSkills` | Layer 1 | Core, Standard | `src/nimbus/skills/tools.py` | ✅ 活跃 |
| `Dispatch` | Layer 2 | Core | `src/nimbus/orchestration/dispatch_tool.py` | ✅ 活跃 |
| `Verify` | Layer 2 | Core | `src/nimbus/orchestration/tools.py` | ✅ 活跃 |
| `ReviewCommittee` | Layer 2 | Core | `src/nimbus/orchestration/review_tool.py` | ✅ 活跃 |
| `CoreBash` | Layer 2 | Core | `src/nimbus/orchestration/tools.py` | ✅ 活跃 |
| `web_search` | Layer 3 | Core, Standard | `skills/web-search/` | ✅ 活跃 |
| `scout_codebase` | Layer 3 | Core, Standard | `examples/skills/code-scout/` | ✅ 活跃 |
| `create_skill` | Layer 3 | Core, Standard | `examples/skills/skill-creator/` | ✅ 活跃 |
| `hello` | Layer 3 | Core, Standard | `tests/fixtures/skills/hello/` | ✅ 活跃 |
| `ScrollHistory` | Planned | — | `src/nimbus/tools/context_tools.py` | 📋 仅定义 |
| `CopyToClipboard` | Planned | — | `src/nimbus/tools/context_tools.py` | 📋 仅定义 |

> **图例**: ✅ 活跃 — 完整实现并在用 | ⚠️ 半成品 — 部分实现 | 📋 仅定义 — 仅有 Schema 无实现

---

*本文档由 Nimbus Agent OS 自动生成，如有更新请同步修改。*
