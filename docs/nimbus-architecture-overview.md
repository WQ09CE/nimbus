---
title: Nimbus 项目完整架构报告
generated: 2025-02-20
source: Explorer Agent (Sonnet) 自动探索生成
---

# 🏗️ Nimbus 项目完整架构报告

---

## 📋 项目基本信息

| 属性 | 值 |
|------|-----|
| **项目名称** | Nimbus |
| **版本** | 0.2.0 |
| **描述** | Nimbus Agent Framework — 受冯·诺依曼架构启发的下一代 AI AgentOS 框架 |
| **授权** | MIT |
| **Python 要求** | >= 3.10 |
| **构建工具** | Hatchling (src layout) |
| **状态** | Alpha (Development Status 3) |
| **入口点** | `nimbus.cli.main:cli` |

---

## 📁 顶层目录结构

```
nimbus/
├── src/nimbus/          # 核心源码（src layout）
├── tests/               # 测试套件（90 个 .py 文件）
├── docs/                # 设计文档（~40+ 篇 Markdown）
├── web-ui/              # 前端 UI（FastAPI + SSE）
├── deploy/              # 部署配置
├── scripts/             # 工具脚本
├── skills/              # 技能定义
├── examples/            # 示例代码
├── proposals/           # 设计提案
├── jobs/                # 批量任务
├── bridge/              # 跨框架桥接
├── agent/               # 子 agent 定义
├── bin/                 # 可执行入口
├── pyproject.toml       # 项目元信息 & 构建配置
├── Makefile             # 常用命令
├── README.md            # 项目文档
└── uv.lock              # 依赖锁定
```

**代码规模统计：**
- 源码文件总数：**105 个 Python 文件**
- 源码总行数：**约 32,352 行**
- 测试文件：**90 个**（含 E2E、单元测试、压力测试）

---

## 🧩 核心依赖

```toml
# 生产依赖
aiosqlite>=0.19.0       # 异步 SQLite ORM（会话持久化）
fastapi>=0.100.0         # HTTP 服务框架
uvicorn[standard]>=0.20.0 # ASGI 服务器
typer>=0.9.0             # CLI 框架
rich>=13.0.0             # 终端美化输出
pydantic>=2.0.0          # 数据验证
sse-starlette>=1.6.0     # SSE 推流支持
loguru>=0.7.0            # 结构化日志
aiohttp>=3.9.0           # 异步 HTTP 客户端
httpx>=0.24.0            # 同步/异步 HTTP
html2text>=2024.2.26     # HTML 解析
duckduckgo-search>=6.0.0 # 网络搜索
PyYAML>=6.0.0            # YAML 配置解析

# 可选依赖组
anthropic>=0.20.0   [llm]  # Anthropic API 客户端
openai>=1.0.0       [llm]  # OpenAI API 客户端
chromadb>=0.4.0     [rag]  # 向量数据库
```

---

## 🏛️ 核心架构设计

Nimbus 将 LLM 视为"CPU"，围绕冯·诺依曼架构构建完整的 AI 操作系统：

```
┌────────────────────────────────────────────────────────────────┐
│                        AgentOS (顶层集成)                       │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                         vCPU                             │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌──────────┐   │  │
│  │  │  ALU    │→ │Decoder  │→ │  Gate   │→ │   MMU    │   │  │
│  │  │ (LLM)   │  │(解码器) │  │(系统调用)│  │(内存管理) │   │  │
│  │  └─────────┘  └─────────┘  └─────────┘  └──────────┘   │  │
│  │       ↑                                        │         │  │
│  │       └────────────────────────────────────────┘         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────┐   │
│  │  Scheduler  │  │   NimFS     │  │  Orchestration Layer  │   │
│  │  (DAG 调度)  │  │ (虚拟文件系统)│  │  (多 Agent 协作)     │   │
│  └─────────────┘  └─────────────┘  └──────────────────────┘   │
└────────────────────────────────────────────────────────────────┘
```

---

## 📂 src/nimbus/ 模块详细分析

### 1. 顶层入口文件

| 文件 | 行数 | 功能 |
|------|------|------|
| `agentos.py` | 1851 | **AgentOS 主协调器**：统一入口，整合 vCPU/MMU/Gate/Scheduler/Decoder |
| `config.py` | ~ | 全局配置加载 |
| `__init__.py` | ~ | 公共 API 导出：`create_agent_os` 工厂函数 |

---

### 2. `core/` — 核心基础设施（36 个文件）

#### `core/protocol.py`（364 行）
**系统脊椎（ISA/ABI 定义）**，所有组件必须遵守的数据结构：
- `ActionIR` — 指令集：`TOOL_CALL / THOUGHT / RETURN / FAULT / PARALLEL`
- `ToolResult` — 工具标准返回值
- `Fault` — 结构化异常，支持自愈与路由
- `Event` — 可观测事件（SSE 推流用）
- `ArtifactRef` — NimFS 引用句柄
- `NIMFS_OFFLOAD_THRESHOLD = 8000`（超出自动卸载到 NimFS）

#### `core/profile.py`
**Agent 人格配置**（`AgentProfile` 数据类）：
- `role`: `executor / standard / explorer / implementer / architect / tester / orchestrator`
- `allowed_tools`: 工具白名单
- 内置工具集定义：`_NIMFS_READ`, `_NIMFS_ALL`

#### `core/config.py`
**类型安全配置系统**：
- `LLMConfig` — 模型、温度、Token 上限
- `MemoryConfig` — 各层内存预算
- `RuntimeConfig` — 超时、重试、并发

#### `core/scheduler.py`
**DAG 任务调度器**：
- 状态机：`PENDING → READY → RUNNING → SUCCEEDED/FAILED/CANCELLED`
- 并发执行独立任务
- 级联取消（下游传播）

#### `core/session.py`
**会话管理（JSONL 格式）**：
- 树状结构（支持分支/回溯）
- 每条 entry 含 `id + parentId`

#### `core/persistence.py`
会话/执行状态的持久化模型

#### `core/compaction.py`
**智能上下文压缩**：
- 基于阈值自动触发
- 保留最近 N 条消息
- 用 LLM 生成历史摘要

#### `core/session_pool.py`
会话池管理

#### `core/memory/`（4 个文件）
| 文件 | 功能 |
|------|------|
| `mmu.py`（1099行）| **MMU 内存管理单元**："Anchor & Stream" 机制核心 |
| `context.py` | `PinnedContext`（不可压缩锚点）+ `StackFrame`（栈帧）+ `Message` |
| `state_manager.py` | 确定性项目状态跟踪（文件工作集、命令执行状态） |
| `__init__.py` | 模块导出 |

**MMU 内存布局：**
```
┌──────────────────────────────────┐
│  The Anchor（永远置顶，不可压缩）  │  ← 系统规则 + 当前目标 + 工作区信息
├──────────────────────────────────┤
│  Global Summary（滚动摘要）        │  ← 历史事件压缩摘要
├──────────────────────────────────┤
│  The Stream（执行流）              │  ← StackFrame 动态历史
└──────────────────────────────────┘
Token 预算：最大 180k，Anchor 专用 10k，Stream 170k
触发压缩阈值：使用率达 90%
```

#### `core/runtime/`（13 个文件）
| 文件 | 功能 |
|------|------|
| `vcpu.py`（1859行）| **vCPU 核心执行引擎**，Think-Act-Observe 循环主体 |
| `decoder.py` | **指令解码器**（Firewall）：LLM 输出 → ActionIR，拦截幻觉 |
| `execution_state.py` | vCPU 执行状态数据类（集中管理 15+ 状态变量） |
| `action_context.py` | 动作执行上下文（依赖注入容器） |
| `checkpoint_manager.py` | 检查点管理（会话状态持久化与恢复） |
| `doom_loop.py` | **Doom Loop 检测器**：连续相同工具调用 3 次判定为死循环 |
| `empty_result_handler.py` | "成功但无结果"场景处理（如搜索无匹配） |
| `error_handler.py` | 错误处理注册表（策略模式） |
| `recovery_executor.py` | 错误恢复执行器（与策略解耦） |
| `thought_handler.py` | 思维链处理器 |
| `interrupt_handler.py` | 中断信号处理 |
| `scheduler_handler.py` | 调度器回调处理 |
| `__init__.py` | 模块导出 |

#### `core/nimfs/`（5 个文件）
| 文件 | 功能 |
|------|------|
| `manager.py`（646行）| **NimFSManager**：虚拟共享磁盘主管理器 |
| `models.py` | 数据模型：`ArtifactTTL / ArtifactStatus / MemoryCategory / MemoryEntry` |
| `gc.py` | 垃圾回收器（TTL 分级清理） |
| `project_id.py` | 项目 ID 管理 + 路径安全校验（KernelGate） |
| `__init__.py` | 模块导出 |

---

### 3. `os/` — 操作系统层（2 个文件）

| 文件 | 功能 |
|------|------|
| `gate.py`（526行）| **KernelGate**：所有工具调用的统一入口 |
| `__init__.py` | 模块导出 |

KernelGate 职责：
- 超时强制执行（`asyncio.wait_for`）
- 异常打包（`Exception → Fault → ToolResult`）
- 事件发射（`TOOL_STARTED/TOOL_FINISHED`，供 SSE 推流）

---

### 4. `tools/` — 工具层（12 个文件）

| 文件 | 功能 |
|------|------|
| `base.py` | **工具 ISA 基础类**：`ToolParameter / ToolDefinition / ToolRegistry / @tool 装饰器` |
| `read.py` | `Read` 工具：文件读取 |
| `write.py` | `Write` 工具：文件写入 |
| `edit.py` | `Edit` 工具：文件编辑（精确替换） |
| `bash.py` | `Bash` 工具：Shell 命令执行 |
| `nimfs_tools.py` | **NimFS 6 个工具**：`NimFSWriteArtifact / NimFSReadArtifact / NimFSListArtifacts / NimFSWriteMemory / NimFSSearchMemory / NimFSLoadContext` |
| `memo.py` | Memo 工具（Agent 便签本） |
| `context_tools.py` | 上下文工具 |
| `sandbox.py` | 沙箱工具 |
| `composite.py` | 复合工具 |
| `utils.py` | 工具辅助函数 |
| `__init__.py` | 工具注册与导出 |

---

### 5. `adapters/` — LLM 适配层（6 个文件）

| 文件 | 功能 |
|------|------|
| `llm_factory.py` | **LLM 工厂**：统一创建 Anthropic / OpenAI / Google Gemini 客户端 |
| `direct_adapter.py` | 直连适配器（绕过 OAuth） |
| `anthropic_oauth.py` | Anthropic OAuth 认证适配 |
| `openai_codex_oauth.py` | OpenAI Codex OAuth 适配 |
| `types.py` | 适配器类型定义 |
| `__init__.py` | 模块导出 |

**支持的模型别名（快捷方式）：**
```python
MODEL_ALIASES = {
    "claude": "anthropic/claude-opus-4-6",
    "sonnet": "anthropic/claude-sonnet-4-6",
    "haiku":  "anthropic/claude-haiku-4-5-20251001",
    "gpt":    "openai/gpt-4o",
    "codex":  "openai-codex/gpt-5.3-codex",
    "gemini": "google/gemini-3.1-pro-preview",
    "gemini-flash": "google/gemini-3-flash-preview",
}
```

---

### 6. `orchestration/` — 多 Agent 编排层（7 个文件）

| 文件 | 功能 |
|------|------|
| `tools.py` | `Dispatch / Verify` 工具（双 Agent 编排） |
| `specialist_tools.py` | **专家工具系统**：`Explore / Implement / Design / Test`（类型化元工具） |
| `context_protocol.py` | **GoalDocument**：结构化目标文档，支持 nimfs:// 引用自动展开 |
| `prompts.py` | 各角色 Prompt 模板管理 |
| `review_tool.py` | 代码审查工具 |
| `workspace_diff.py` | 工作区快照 Diff |
| `__init__.py` | 模块导出 |

**专家 Agent 角色体系：**
```
Orchestrator（协调者）
    ├── Explore   → explorer 角色（只读调查）
    ├── Implement → implementer 角色（代码实现）
    ├── Design    → architect 角色（架构设计）
    └── Test      → tester 角色（测试验证）
```

---

### 7. `server/` — HTTP 服务层（17 个文件）

| 文件 | 功能 |
|------|------|
| `app.py` | **FastAPI 应用工厂**（含生命周期管理） |
| `api.py` | 主 REST API 路由（会话/消息/权限/DAG/技能） |
| `api_ai_sdk.py` | AI SDK 兼容接口 |
| `api_openai.py` | OpenAI API 兼容接口 |
| `api_vibe.py` | Vibe 模式 API |
| `api_debug.py` | 调试接口 |
| `api_logs.py` | 日志接口 |
| `api_utils.py` | API 工具函数 |
| `sse.py` | **SSEHub**：服务端推送事件枢纽 |
| `session_v2.py` | 会话管理 v2 |
| `message_cache.py` | 消息缓存 |
| `permission.py` | 权限管理器 |
| `log_hub.py` | 日志聚合枢纽 |
| `llm_adapter.py` | 服务端 LLM 适配 |
| `middleware.py` | 中间件（CORS/日志） |
| `models.py` | 请求/响应 Pydantic 模型 |
| `__init__.py` | 模块导出 |

---

### 8. `cli/` — 命令行层（8 个文件）

| 命令 | 功能 |
|------|------|
| `nimbus serve` | 启动 HTTP 服务器 |
| `nimbus run` | 单次任务执行（one-shot 模式） |
| `nimbus session` | 会话管理（list/show/delete） |
| `nimbus config` | 配置查看与修改 |
| `nimbus acp` | 以 ACP Agent 模式启动 |

---

### 9. `skills/` — 技能系统（5 个文件）

| 文件 | 功能 |
|------|------|
| `models.py` | `SkillManifest / SkillToolConfig / SkillToolArg` 数据模型 |
| `loader.py` | 技能加载器（从 SKILL.md 解析） |
| `manager.py` | 技能管理器 |
| `tools.py` | 技能工具注册 |
| `__init__.py` | 模块导出 |

---

### 10. `storage/` — 持久化层（2 个文件）

| 文件 | 功能 |
|------|------|
| `sqlite.py` | **SQLiteStorage**：主存储实现（会话/消息/DAG/内存检查点/权限） |
| `__init__.py` | 导出 `SQLiteStorage` |

---

### 11. `agents/` + `data/` — Agent 配置

```
agents/
└── default.yaml      # 默认 Agent 配置（模型/内存/运行时参数）

data/agents/
├── core.yaml         # 核心协调 Agent
├── coder.yaml        # 代码实现专家
├── explorer.yaml     # 代码探索专家（只读）
├── researcher.yaml   # 研究专家
└── reviewer.yaml     # 代码审查专家
```

---

### 12. `utils/` — 工具函数（4 个文件）
日志、辅助函数等通用工具。

### 13. `testing/` — 测试支持（2 个文件）
| 文件 | 功能 |
|------|------|
| `mock_llm.py` | Mock LLM 客户端（单元测试用） |
| `__init__.py` | 测试工具导出 |
