# Code Agent 开源项目取经报告

> 调研时间: 2026-01-24
> 目的: 为 Nimbus code agent 收集工具设计和架构参考

---

## 一、Python Code Agent 开源项目全景

### Top-tier 项目 (按 Stars 排序)

| 项目 | Stars | 语言 | 核心特点 |
|------|-------|------|----------|
| [OpenHands](https://github.com/OpenHands/OpenHands) | 67k | Python 77.5% | SDK+CLI+GUI 三层架构，云端扩展，支持 1000s agent |
| [Cline](https://github.com/cline/cline) | 57k | TypeScript+Go | VSCode 扩展，强大 MCP 集成，自定义工具创建 |
| [GPT Engineer](https://github.com/AntonOsika/gpt-engineer) | 55k | Python 98.8% | 自然语言生成代码库，支持 vision 输入 |
| [AutoGen](https://github.com/microsoft/autogen) | 54k | Python | 微软三层架构 (Core/AgentChat/Extensions)，MCP 集成 |
| [CrewAI](https://github.com/crewAIInc/crewAI) | 43k | Python 100% | Crews+Flows 双架构，独立于 LangChain |
| [Aider](https://github.com/Aider-AI/aider) | 40k | Python 80% | 终端配对编程，Git 深度集成，100+ 语言 |

### 专业化项目

| 项目 | Stars | 核心特点 |
|------|-------|----------|
| [Continue](https://github.com/continuedev/continue) | 31k | VSCode/JetBrains 扩展，消息传递架构 |
| [Goose](https://github.com/block/goose) | 28k | Rust+TypeScript，MCP 集成，本地运行 |
| [Composio](https://github.com/ComposioHQ/composio) | 26k | 100+ 工具集成平台，25+ agent 框架支持 |
| [SmolAgents](https://github.com/huggingface/smolagents) | 25k | ~1000 行核心代码，code-first 设计，性能提升 30% |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 24k | 图结构 agent，durable execution，记忆系统 |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | 18k | Agent-Computer Interface (ACI)，单 YAML 配置 |
| [AgentScope](https://github.com/agentscope-ai/agentscope) | 16k | 阿里，MsgHub 消息路由，支持 K8s |

### 高性能专项

| 项目 | Stars | 亮点 |
|------|-------|------|
| [XAgent](https://github.com/OpenBMB/XAgent) | 8.5k | Dispatcher/Planner/Actor 三层架构，Docker 沙箱 |
| [Refact.ai](https://github.com/smallcloudai/refact) | 3.4k | **SWE-bench Verified #1 (74.4%)**，完全自主执行 |

### 架构模式总结

| 模式 | 代表项目 | 优点 |
|------|----------|------|
| **分层架构** | AutoGen, OpenHands | Core→AgentChat→Extensions，灵活易扩展 |
| **图结构** | LangGraph | 可视化，checkpoint/resume，Human-in-the-loop |
| **Crews+Flows** | CrewAI | 自主协作 + 事件驱动，生产就绪 |
| **ACI 设计** | SWE-agent | LM-centric 命令，单 YAML 配置 |
| **Dispatcher/Planner/Actor** | XAgent | 职责分离清晰 |
| **Code-First** | SmolAgents | 输出 Python 而非 JSON，极简高效 |

---

## 二、OpenCode 工具列表 (14 个核心工具)

> 项目地址: https://github.com/sst/opencode
> 70,000+ Stars | 650,000+ 月活 | Go 语言实现

### 工具清单

#### 文件操作类 (5个)

| 工具 | 功能 | 参数 | 权限 |
|------|------|------|------|
| `read` | 读取文件内容 | `paths: string[]` | read |
| `write` | 创建或覆盖文件 | `path, content` | edit |
| `edit` | 精确字符串替换 | `path, content/patches` | edit |
| `patch` | 应用 patch 文件 | - | edit |
| `list` | 列出目录内容 | `path, recursive?` | list |

**edit 工具亮点:**
- 双模式：完整内容 或 patch 数组
- 9 种回退策略
- 快照保护
- 自动格式化集成

#### 搜索类 (2个)

| 工具 | 功能 | 参数 | 权限 |
|------|------|------|------|
| `grep` | 正则搜索 (ripgrep) | `pattern, paths?, case_sensitive, fixed_strings` | grep |
| `glob` | 文件模式匹配 | `patterns: string[]` | glob |

#### 执行类 (2个)

| 工具 | 功能 | 参数 | 权限 |
|------|------|------|------|
| `bash` | Shell 命令执行 | `command, cwd?, timeout?` | bash |
| `task` | Subagent 委托 | `description, prompt, subagent_type, session_id?` | task |

**bash 工具特性:**
- 捕获 stdout/stderr
- 返回 exit code
- 默认 60s 超时

**task 工具特性:**
- 支持指定 subagent 类型
- 子会话不能嵌套 task/todo

#### 网络访问类 (3个)

| 工具 | 功能 | 权限 |
|------|------|------|
| `webfetch` | 获取网页内容 | webfetch |
| `websearch` | 网络搜索 | websearch |
| `codesearch` | 语义代码搜索 | codesearch |

#### 代码智能类 (1个)

| 工具 | 功能 | 权限 | 备注 |
|------|------|------|------|
| `lsp` | LSP 查询 | lsp | 实验性，需 `OPENCODE_EXPERIMENTAL_LSP_TOOL=true` |

**LSP 能力:**
- 定义跳转
- 引用查找
- 悬停信息
- 调用层次

#### 任务管理类 (3个)

| 工具 | 功能 | 作用域 |
|------|------|--------|
| `todowrite` | 创建/更新待办 | 会话级 |
| `todoread` | 读取待办列表 | 会话级 |
| `skill` | 加载 SKILL.md | 技能注入 |

### Agent 模式与权限

| Agent | 工具权限 | 说明 |
|-------|----------|------|
| Build | 全部允许 | 默认开发模式 |
| Plan | 限制模式 | write/edit/patch/bash 设为 `ask` |
| General | 全功能 (除 todo) | 子 agent，可修改文件 |
| Explore | 只读 | 代码库探索，无修改权限 |

### 权限系统

三级权限: `allow` / `ask` / `deny`
- 支持模式匹配
- 会话级规则
- 通过 `PermissionNext.ask()` 执行

---

## 三、Aider 工具设计 (30+ 命令)

> 项目地址: https://github.com/paul-gauthier/aider
> 40k Stars | Python 80% | 终端 AI 配对编程

### 命令清单

#### 文件上下文管理

| 命令 | 功能 | 示例 |
|------|------|------|
| `/add` | 添加文件到上下文 | `/add file.py` |
| `/drop` | 从上下文移除文件 | `/drop file.py` |
| `/read-only` | 添加只读文件 (仅参考) | `/read-only docs.md` |
| `/ls` | 列出当前上下文文件 | - |
| `/clear` | 清空聊天历史 | - |

#### 代码编辑

| 命令 | 功能 | 说明 |
|------|------|------|
| `/architect` | 架构模式 | 只讨论不改代码 |
| `/code` | 编码模式 | 标准编辑 |
| `/ask` | 问答模式 | 仅提问，不修改 |
| `/diff` | 显示未提交更改 | - |
| `/undo` | 撤销上次提交 | - |

#### Git 集成

| 命令 | 功能 | 示例 |
|------|------|------|
| `/commit` | 提交更改 | `/commit -m "fix bug"` |
| `/git` | 执行 Git 命令 | `/git status` |
| `/undo` | 撤销最后一次 aider 提交 | - |
| `/diff` | 查看工作区差异 | - |

#### 执行和测试

| 命令 | 功能 | 示例 |
|------|------|------|
| `/run` | 执行 Shell 命令 | `/run pytest tests/` |
| `/test` | 运行测试 | - |
| `/lint` | 运行 linter 并自动修复 | - |
| `/web` | 启动 Playwright 浏览器 | - |

#### 模型和配置

| 命令 | 功能 |
|------|------|
| `/model` | 切换 AI 模型 |
| `/models` | 列出可用模型 |
| `/tokens` | 显示 token 使用情况 |
| `/settings` | 显示当前设置 |

#### 其他

| 命令 | 功能 |
|------|------|
| `/help` | 显示帮助 |
| `/voice` | 启用语音输入 |
| `/paste` | 粘贴图片或文本 |
| `/copy` | 复制消息到剪贴板 |
| `/exit` | 退出 |

### 核心架构设计

#### 1. Repo Map 技术 ⭐⭐⭐

```
策略:
├── 使用 tree-sitter 解析整个代码库
├── 生成函数/类定义地图
├── 仅占用 1-2K tokens
└── 提供"全局视野"，智能推荐相关文件
```

**价值:** 不需要把所有文件加载到上下文，大幅节省 token

#### 2. 代码编辑模式

| 模式 | 适用场景 | 优势 |
|------|----------|------|
| **Unified Diff** | 默认 | 高效，仅传输变更，适合大文件 |
| **Whole File** | 小文件/重构 | 完整替换，避免合并冲突 |
| **Architect** | 设计讨论 | 只规划不修改 |

**编辑流程:**
```
用户请求 → LLM 生成 Diff → Aider 解析应用 → Git 自动提交 → 验证
```

#### 3. 自动化 Git 工作流

```
每次编辑 → 自动 commit → LLM 生成 commit msg → 可一键 undo
```

**特性:**
- 每次编辑自动 commit (可配置)
- 智能生成 commit message
- /undo 安全回滚
- 完整操作历史

#### 4. 多模型支持

- 支持 30+ 模型 (OpenAI, Anthropic, Gemini, Deepseek...)
- 动态切换 (`/model`)
- 自动适配 context window 大小

---

## 四、工具对比分析

### 功能对比表

| 工具类别 | OpenCode | Aider | 建议 |
|---------|----------|-------|------|
| **文件操作** | read, write, edit, patch, list | /add, /drop, /read-only | OpenCode 的 patch 模式 + Aider 的只读区分 |
| **搜索** | grep, glob, codesearch | Repo Map + 语义搜索 | **Aider 的 Repo Map 必须有** |
| **执行** | bash, task | /run, /test, /lint | task 的 subagent 委托很有价值 |
| **网络** | webfetch, websearch | - | OpenCode 完整网络能力 |
| **代码智能** | lsp (实验性) | tree-sitter 解析 | LSP 是未来方向 |
| **任务管理** | todowrite, todoread, skill | - | skill 加载机制 |
| **Git** | (通过 bash) | /commit, /undo, /diff | **Aider 的自动 commit+undo** |
| **模式切换** | Plan/Build/Explore | /architect, /code, /ask | 三模式设计 |

### 顶级设计借鉴

| 设计 | 来源 | 价值 | 优先级 |
|------|------|------|--------|
| **Repo Map** | Aider | 1-2K tokens 表示整个代码库 | ⭐⭐⭐ 高 |
| **Task 委托** | OpenCode | subagent 类型化，并行探索 | ⭐⭐⭐ 高 |
| **Edit Patch 模式** | OpenCode | 9 种回退策略，精确修改 | ⭐⭐⭐ 高 |
| **三模式设计** | Aider | architect/code/ask 职责分离 | ⭐⭐ 中 |
| **权限系统** | OpenCode | allow/ask/deny 三级权限 | ⭐⭐ 中 |
| **自动 Git** | Aider | 编辑→commit→undo 闭环 | ⭐⭐ 中 |
| **LSP 集成** | OpenCode | 定义/引用/调用层次 | ⭐ 低 (实验性) |

---

## 五、Nimbus 工具建设建议

### 第一优先级 (核心能力)

| Tool | 功能 | 参考来源 |
|------|------|----------|
| `read` | 批量读取文件 (`paths: string[]`) | OpenCode |
| `write` | 创建/覆盖文件 | OpenCode |
| `edit` | 精确替换 + patch 模式 | OpenCode |
| `bash` | Shell 执行 (timeout, cwd) | OpenCode |
| `grep` | 正则搜索 (ripgrep) | OpenCode |
| `glob` | 文件模式匹配 | OpenCode |

### 第二优先级 (增强体验)

| Tool | 功能 | 参考来源 |
|------|------|----------|
| `task` | Subagent 委托 | OpenCode |
| `repo_map` | 代码库全局视野 (tree-sitter) | Aider |
| `git` | commit/undo/diff 集成 | Aider |
| `webfetch` | 网页内容获取 | OpenCode |

### 第三优先级 (高级功能)

| Tool | 功能 | 参考来源 |
|------|------|----------|
| `lsp` | 定义/引用/悬停 | OpenCode |
| `codesearch` | 语义代码搜索 | OpenCode |
| `skill` | 技能加载 | OpenCode |

### 推荐架构

```
ToolRegistry (统一管理)
├── 内置工具 (14个核心)
├── 自定义工具 (用户扩展)
└── MCP 工具 (协议集成)

PermissionSystem (三级权限)
├── allow (自动执行)
├── ask (请求确认)
└── deny (禁止)

AgentModes (模式切换)
├── Build (全功能)
├── Plan (只读+ask)
└── Explore (只读)
```

---

## 六、GPT Engineer 深度分析

> 项目地址: https://github.com/AntonOsika/gpt-engineer
> 55k Stars | Python 98.8% | 自然语言生成代码库

### 独特架构：无函数调用

GPT Engineer **不使用 Function Calling**，采用纯对话模式：
```
用户提示 → LLM 对话 → 正则解析代码块 → FilesDict → 磁盘文件
```

**优势:**
- 减少 Token 成本（不需要工具定义）
- 简化提示工程（直接要求格式化输出）
- 降低调试复杂度（纯文本响应）

### 核心组件

| 组件 | 文件 | 功能 |
|------|------|------|
| **FilesDict** | `files_dict.py` | 统一文件抽象，`dict` 子类，`{path: content}` |
| **chat_to_files_dict** | `chat_to_files.py` | 正则提取代码块（三反引号标记） |
| **parse_diffs** | `chat_to_files.py` | 提取 unified diff 块 |
| **apply_diffs** | `chat_to_files.py` | 应用 diff 到文件（带自动纠错） |
| **Diff** | `diff.py` | 最大模块，diff 解析和验证 |
| **FileStore** | `file_store.py` | push/pull 批量文件 I/O |
| **DiskMemory** | `disk_memory.py` | 键值存储，支持二进制检测 |
| **DiskExecutionEnv** | `disk_execution_env.py` | 临时目录隔离执行 |
| **self_heal** | `custom_steps.py` | 自动测试-修复循环 |

### 工具清单

| 工具 | 类型 | 输入 | 输出 |
|------|------|------|------|
| `chat_to_files_dict` | 解析器 | AI 对话文本 | FilesDict |
| `parse_diffs` | 解析器 | diff 文本 | List[Diff] |
| `apply_diffs` | 编辑器 | diffs + files | FilesDict |
| `FileStore.push` | 写入 | FilesDict | - |
| `FileStore.pull` | 读取 | - | FilesDict |
| `gen_code` | 生成 | prompt | FilesDict |
| `improve_fn` | 改进 | prompt + files | FilesDict |
| `self_heal` | 修复 | files + error | FilesDict |

### 代码生成流程

```
用户输入 (prompt)
    ↓
[AI 交互] ai.start(system_prompt + user_prompt)
    ↓
[对话解析] chat_to_files_dict(response)
    ↓ 正则提取代码块
FilesDict {"file.py": "code..."}
    ↓
[文件写入] FileStore.push(files)
    ↓
[执行测试] execution_env.popen("python main.py")
    ↓
[如果失败] self_heal() → improve_fn() → parse_diffs() → apply_diffs()
    ↓
循环直到成功...
```

### Diff 自动纠错机制 ⭐⭐⭐

GPT Engineer 的 `diff.py` (19KB) 是最复杂的模块：
- **Hunk 类**: 处理单个变更块（ADD/REMOVE/RETAIN）
- **前向块比较算法**: 自动修正 LLM 生成的错误 diff
- **自动检测**: 跳过行、错误行、无效 hunk
- **容错处理**: 移除无法应用的变更块

### 自愈循环 (self_heal) ⭐⭐⭐

```python
for attempt in range(MAX_ATTEMPTS):
    result = execute(files)
    if result.success:
        break
    files = improve_fn(ai, f"Fix error: {result.stderr}", files)
```

### 可借鉴设计点

| 设计 | 价值 | Nimbus 应用建议 |
|------|------|-----------------|
| **FilesDict 抽象** | 统一文件表示 | 为 Edit/Write 提供数据结构 |
| **对话解析机制** | 降低 Token 成本 | Skill 提供"结构化输出解析" |
| **Diff 自动纠错** | 提升编辑可靠性 | Edit 工具集成验证逻辑 |
| **临时目录隔离** | 安全执行 | Bash 工具"沙箱模式" |
| **self_heal 循环** | 自动化修复 | @舌+@身 联动技能 |
| **日志存档** | 调试回溯 | Session 增强日志记录 |

---

## 七、Nimbus 现有工具实现

### 已实现工具 (3个)

| 工具 | 文件 | 参数 | 特性 |
|------|------|------|------|
| **Read** | `tools/read.py` | `file_path, offset, limit` | 行号显示、二进制检测、UTF-8 fallback |
| **Grep** | `tools/grep.py` | `pattern, path, glob, type, context_*` | 30+ 文件类型、正则支持、上下文行 |
| **Glob** | `tools/glob.py` | `pattern, path, limit` | 按修改时间排序 |

### 基础设施

| 组件 | 文件 | 功能 |
|------|------|------|
| `ToolParameter` | `tools/base.py` | 参数定义，JSON Schema 转换 |
| `ToolDefinition` | `tools/base.py` | 工具元数据，Claude/OpenAI 格式 |
| `ToolRegistry` | `tools/base.py` | 注册表，sync/async 执行 |
| `@tool` 装饰器 | `tools/base.py` | 简化工具定义 |
| `Sandbox` | `tools/sandbox.py` | 路径验证，防止目录穿越 |

### 缺失工具

| 工具 | 优先级 | 参考 |
|------|--------|------|
| **Write** | ⭐⭐⭐ 高 | OpenCode |
| **Edit** | ⭐⭐⭐ 高 | OpenCode + GPT Engineer diff |
| **Bash** | ⭐⭐⭐ 高 | OpenCode |
| Task | ⭐⭐ 中 | OpenCode |
| Git | ⭐⭐ 中 | Aider |

---

## 八、工具建设路线图

### 第一阶段：核心工具 (必须)

| 工具 | 功能 | 参数设计 | 参考 |
|------|------|----------|------|
| **Write** | 创建/覆盖文件 | `file_path, content` | OpenCode |
| **Edit** | 精确替换 | `file_path, old_string, new_string, replace_all` | OpenCode |
| **Bash** | Shell 执行 | `command, cwd, timeout` | OpenCode |

### 第二阶段：增强功能

| 工具 | 功能 | 参考 |
|------|------|------|
| **Edit (diff 模式)** | unified diff 应用 | GPT Engineer |
| **Bash (沙箱)** | 临时目录隔离 | GPT Engineer |
| **FilesDict** | 统一文件抽象 | GPT Engineer |

### 第三阶段：高级能力

| 工具 | 功能 | 参考 |
|------|------|------|
| **self_heal** | 自动测试-修复 | GPT Engineer |
| **Git** | commit/undo/diff | Aider |
| **Repo Map** | 代码库全局视野 | Aider |

---

🔧 Claude Code 内置工具清单                                                                                                                   
                                                                                                                                                
  文件操作 (4个)                                                                                                                                
  ┌──────────────┬────────────────────┬────────────────────────────────────────────────┐                                                        
  │     工具     │        功能        │                    核心参数                    │                                                        
  ├──────────────┼────────────────────┼────────────────────────────────────────────────┤                                                        
  │ Read         │ 读取文件           │ file_path, offset, limit                       │                                                        
  ├──────────────┼────────────────────┼────────────────────────────────────────────────┤                                                        
  │ Write        │ 创建/覆盖文件      │ file_path, content                             │                                                        
  ├──────────────┼────────────────────┼────────────────────────────────────────────────┤                                                        
  │ Edit         │ 精确字符串替换     │ file_path, old_string, new_string, replace_all │                                                        
  ├──────────────┼────────────────────┼────────────────────────────────────────────────┤                                                        
  │ NotebookEdit │ Jupyter 单元格编辑 │ notebook_path, cell_id, new_source, edit_mode  │                                                        
  └──────────────┴────────────────────┴────────────────────────────────────────────────┘                                                        
  搜索 (2个)                                                                                                                                    
  ┌──────┬──────────────┬──────────────────────────────────────────────────┐                                                                    
  │ 工具 │     功能     │                     核心参数                     │                                                                    
  ├──────┼──────────────┼──────────────────────────────────────────────────┤                                                                    
  │ Glob │ 文件模式匹配 │ pattern, path                                    │                                                                    
  ├──────┼──────────────┼──────────────────────────────────────────────────┤                                                                    
  │ Grep │ 正则内容搜索 │ pattern, path, glob, type, output_mode, -A/-B/-C │                                                                    
  └──────┴──────────────┴──────────────────────────────────────────────────┘                                                                    
  执行 (1个)                                                                                                                                    
  ┌──────┬────────────────┬─────────────────────────────────────┐                                                                               
  │ 工具 │      功能      │              核心参数               │                                                                               
  ├──────┼────────────────┼─────────────────────────────────────┤                                                                               
  │ Bash │ Shell 命令执行 │ command, timeout, run_in_background │                                                                               
  └──────┴────────────────┴─────────────────────────────────────┘                                                                               
  网络 (2个)                                                                                                                                    
  ┌───────────┬──────────────┬─────────────────────────────────────────┐                                                                        
  │   工具    │     功能     │                核心参数                 │                                                                        
  ├───────────┼──────────────┼─────────────────────────────────────────┤                                                                        
  │ WebFetch  │ 获取网页内容 │ url, prompt                             │                                                                        
  ├───────────┼──────────────┼─────────────────────────────────────────┤                                                                        
  │ WebSearch │ 网络搜索     │ query, allowed_domains, blocked_domains │                                                                        
  └───────────┴──────────────┴─────────────────────────────────────────┘                                                                        
  Agent/任务 (5个)                                                                                                                              
  ┌────────────┬──────────────┬─────────────────────────────────────────────────────────┐                                                       
  │    工具    │     功能     │                        核心参数                         │                                                       
  ├────────────┼──────────────┼─────────────────────────────────────────────────────────┤                                                       
  │ Task       │ 启动子 agent │ subagent_type, prompt, run_in_background, allowed_tools │                                                       
  ├────────────┼──────────────┼─────────────────────────────────────────────────────────┤                                                       
  │ TaskOutput │ 获取任务输出 │ task_id, block, timeout                                 │                                                       
  ├────────────┼──────────────┼─────────────────────────────────────────────────────────┤                                                       
  │ TaskStop   │ 停止任务     │ task_id                                                 │                                                       
  ├────────────┼──────────────┼─────────────────────────────────────────────────────────┤                                                       
  │ TaskCreate │ 创建待办     │ subject, description, activeForm                        │                                                       
  ├────────────┼──────────────┼─────────────────────────────────────────────────────────┤                                                       
  │ TaskUpdate │ 更新待办     │ taskId, status, ...                                     │                                                       
  └────────────┴──────────────┴─────────────────────────────────────────────────────────┘                                                       
  交互 (4个)                                                                                                                                    
  ┌─────────────────┬──────────────┬───────────────────────┐                                                                                    
  │      工具       │     功能     │       核心参数        │                                                                                    
  ├─────────────────┼──────────────┼───────────────────────┤                                                                                    
  │ AskUserQuestion │ 向用户提问   │ questions (多选/单选) │                                                                                    
  ├─────────────────┼──────────────┼───────────────────────┤                                                                                    
  │ EnterPlanMode   │ 进入计划模式 │ -                     │                                                                                    
  ├─────────────────┼──────────────┼───────────────────────┤                                                                                    
  │ ExitPlanMode    │ 退出计划模式 │ allowedPrompts        │                                                                                    
  ├─────────────────┼──────────────┼───────────────────────┤                                                                                    
  │ Skill           │ 执行技能     │ skill, args           │                                                                                    
  └─────────────────┴──────────────┴───────────────────────┘                                                                                    
  ---                                                                                                                                           
  与 Nimbus 现有工具对比                                                                                                                        
  ┌──────────────┬─────────────┬─────────┬──────────┐                                                                                           
  │     工具     │ Claude Code │ Nimbus  │   差距   │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ Read         │ ✅ 完整     │ ✅ 有   │ 接近     │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ Write        │ ✅ 完整     │ ❌ 缺失 │ 需新增   │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ Edit         │ ✅ 完整     │ ❌ 缺失 │ 需新增   │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ Glob         │ ✅ 完整     │ ✅ 有   │ 接近     │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ Grep         │ ✅ 完整     │ ✅ 有   │ 接近     │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ Bash         │ ✅ 完整     │ ❌ 缺失 │ 需新增   │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ WebFetch     │ ✅ 完整     │ ❌ 缺失 │ 中优先级 │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ WebSearch    │ ✅ 完整     │ ❌ 缺失 │ 中优先级 │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ Task         │ ✅ 完整     │ ❌ 缺失 │ 中优先级 │                                                                                           
  ├──────────────┼─────────────┼─────────┼──────────┤                                                                                           
  │ NotebookEdit │ ✅ 完整     │ ❌ 缺失 │ 低优先级 │                                                                                           
  └──────────────┴─────────────┴─────────┴──────────┘                                                                                           
  Claude Code 工具设计亮点                                                                                                                      
                                                                                                                                                
  1. Edit 的 replace_all 参数 - 支持全局替换                                                                                                    
  2. Grep 的 output_mode - content / files_with_matches / count                                                                                 
  3. Grep 的上下文参数 - -A, -B, -C 类似 grep                                                                                                   
  4. Bash 的 run_in_background - 后台执行                                                                                                       
  5. Task 的 allowed_tools - 预授权工具列表                                                                                                     
  6. Task 的 subagent_type - 多种专用 agent                                                                                                     
                                                                                 
                                                                                 
## 九、参考资源

### 项目链接

**Top-tier:**
- https://github.com/OpenHands/OpenHands
- https://github.com/Aider-AI/aider
- https://github.com/cline/cline
- https://github.com/microsoft/autogen
- https://github.com/sst/opencode

**Multi-Agent:**
- https://github.com/crewAIInc/crewAI
- https://github.com/langchain-ai/langgraph
- https://github.com/agentscope-ai/agentscope

**Specialized:**
- https://github.com/huggingface/smolagents
- https://github.com/SWE-agent/SWE-agent
- https://github.com/smallcloudai/refact

### 文档资源

- [OpenCode Tools Documentation](https://opencode.ai/docs/tools/)
- [Aider Commands Reference](https://aider.chat/docs/commands.html)
- [MCP Protocol Specification](https://modelcontextprotocol.io/)
