# Nimbus Agent Framework

> AI Agent 框架，采用类操作系统架构。

## Overview

**Nimbus** 是一个模块化 AI Agent 框架（v0.2.0 Alpha），采用冯·诺伊曼启发的架构设计。核心组件：**vCPU**（Think-Act-Observe 执行循环）、**MMU**（上下文记忆管理）、**Gate**（权限隔离的工具访问），通过 **AgentOS** 统一编排。

**核心能力：**
- 🖥️ OS-like 架构 (vCPU / MMU / Gate / Process)
- 🧠 无限上下文支持（滑动窗口 + LLM 压缩 + 归档）
- 📊 DAG 并行任务调度
- 🔒 角色权限隔离的子 Agent 系统
- 🔌 多协议 API（REST / OpenCode / AI SDK v6 / Vibe）
- 🔧 Skill 热加载系统
- 🌐 Web UI（Next.js 聊天界面 + Debug 面板）

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                           AgentOS                               │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    vCPU (~1,700 lines)                     │  │
│  │                                                           │  │
│  │   THINK ──→ ACT ──→ OBSERVE ──→ Continue / Return        │  │
│  │  (LLM Call)  (Tool Exec)  (Results)                       │  │
│  │                                                           │  │
│  │  子组件: DoomLoopDetector | ErrorHandlerRegistry          │  │
│  │         RecoveryExecutor  | CheckpointManager             │  │
│  │         EmptyResultHandler | FailureReporter              │  │
│  └───────────────────┬───────────────────────────────────────┘  │
│                      │                                          │
│       ┌──────────────┼──────────────┐                           │
│       ▼              ▼              ▼                           │
│  ┌─────────┐   ┌──────────┐   ┌───────────┐                    │
│  │   MMU   │   │   Gate   │   │ Scheduler │                    │
│  │ 上下文  │   │ 权限隔离 │   │ DAG 调度  │                    │
│  │ + 压缩  │   │ 工具分发 │   │ 并行执行  │                    │
│  │ + 归档  │   │          │   │           │                    │
│  └─────────┘   └──────────┘   └───────────┘                    │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    Tool System                             │  │
│  │  Core: Read | Write | Edit | Bash | Memo | ReloadSkills   │  │
│  │  Skills: 可热加载技能包 (SKILL.md 声明式)                  │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     HTTP Server (FastAPI)                        │
│   /api/v1/*  │  /session/*  │  /v1/chat/completions  │  /vibe  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Web UI (Next.js)                              │
│           聊天界面  │  Debug 面板  │  Session 管理               │
└─────────────────────────────────────────────────────────────────┘
```

## Project Structure

```
nimbus/
├── src/nimbus/              # Python 源码 (97 files, ~29.5k lines)
│   ├── agentos.py           # AgentOS 主入口 (1,687 lines)
│   ├── core/
│   │   ├── runtime/
│   │   │   ├── vcpu.py              # vCPU 执行引擎 (1,704 lines)
│   │   │   ├── decoder.py           # LLM 响应 → ActionIR 解析
│   │   │   ├── doom_loop.py         # 循环检测
│   │   │   ├── error_handler.py     # 错误分类 & 恢复策略
│   │   │   ├── recovery_executor.py # 错误恢复执行
│   │   │   ├── execution_state.py   # 集中状态管理
│   │   │   └── checkpoint_manager.py
│   │   ├── memory/
│   │   │   ├── mmu.py               # 记忆管理 (950 lines)
│   │   │   ├── context.py           # Message, StackFrame 类型
│   │   │   └── state_manager.py     # 项目状态追踪
│   │   ├── scheduler.py             # DAG 调度器 (968 lines)
│   │   ├── compaction.py            # LLM 驱动的上下文压缩
│   │   ├── protocol.py              # ActionIR, ToolResult, Fault
│   │   └── models/manifest.py       # 多模型能力清单
│   ├── tools/                       # Read, Write, Edit, Bash, Memo...
│   ├── os/gate.py                   # 权限隔离的系统调用
│   ├── server/                      # FastAPI HTTP 服务
│   ├── bridge/                      # pi-ai HTTP/WebSocket 桥接
│   ├── adapters/                    # LLM 适配器 (Pi, Mock)
│   ├── orchestration/               # 多 Agent 编排
│   ├── skills/                      # Skill 热加载引擎
│   ├── cli/                         # 命令行工具
│   └── storage/                     # SQLite 持久化
├── tests/                   # 472 passed, 21 skipped
├── web-ui/                  # Next.js Web 界面
├── bridge/                  # pi-ai-server (TypeScript)
├── examples/                # 示例代码 (18 个)
├── nimbus_harbor/           # Agent 评测任务集
├── skills/                  # 可热加载技能包
├── deploy/                  # 部署配置 (systemd/launchd/nginx)
├── docs/                    # 设计文档
├── Makefile                 # make start/stop/test/dev
├── pyproject.toml           # 项目配置
└── nimbus                   # 主启动脚本
```

## Quick Start

### Installation

```bash
# 基础安装
pip install -e .

# 完整安装（含开发依赖、LLM SDK）
pip install -e ".[all]"
```

### Running

```bash
# 启动所有服务（后台）
make start

# 开发模式（前台）
make dev

# 仅启动 server
nimbus serve --port 8080
```

### Testing

```bash
# 运行全部测试 (472 tests)
make test

# 快速测试（跳过慢速测试）
pytest tests/ -m "not slow"

# Web UI E2E 测试
make test-e2e
```

## Key Concepts

### vCPU 执行循环

vCPU 在每个迭代中执行 Think → Act → Observe 循环：

1. **Think** — 将上下文发送给 LLM，获取下一步计划
2. **Act** — 通过 Gate 执行工具调用（权限检查 + 超时控制）
3. **Observe** — 收集结果，更新上下文，决定继续或返回

内置安全机制：
- **Doom Loop 检测** — 连续 3 次相同调用自动中止
- **错误自动恢复** — ErrorHandlerRegistry 分类错误并尝试修复
- **幻觉纠正** — 检测 LLM 文本模拟 tool call 并注入校正指令
- **工具名修正** — 自动修复 LLM 大小写错误（`read` → `Read`）

### MMU 记忆管理

MMU 实现了支持 **无限会话时长** 的混合记忆架构：

| 层级 | 内容 | 行为 |
|------|------|------|
| **Pinned** | 系统规则、工作区信息、项目状态 | 永不压缩，始终可见 |
| **Memo** | 用户/Agent 的持久笔记 | 跨压缩周期保留 |
| **Hot Context** | 最近 15 条消息 | 始终可见，保证连续性 |
| **History Window** | 历史对话 | 滑动窗口，按 token 预算选取 |
| **Archive** | 压缩后的完整历史 | 写入磁盘文件，可通过工具回读 |

**上下文压缩流程：** 当 token 超过预算时，MMU 执行 Distill & Archive：
1. LLM 生成执行摘要（目标、已完成步骤、下一步）
2. 原始历史写入归档文件
3. 活跃记忆重置为：摘要 + 归档指针

**图片优化：** 自动去重 + token 预算限制，超出时将旧图片替换为占位文本。

### 角色权限隔离

不同角色的 Agent 只能访问授权的工具：

| 角色 | 允许的工具 | 用途 |
|------|-----------|------|
| `eye` | Read, Glob, Grep | 代码探索 |
| `body` | Read, Write, Edit, Bash | 实现编码 |
| `mind` | Read, Glob, Grep | 架构设计 |
| `tongue` | Read, Glob, Bash | 测试验证 |
| `nose` | Read, Glob, Grep | 代码审查 |

### Skill 系统

通过 `SKILL.md` 声明式定义技能，支持运行时热加载：

```yaml
# skills/web-search/SKILL.md
---
name: web-search
version: 1.0.0
tools:
  - name: WebSearch
    entrypoint: scripts/search.py
    args:
      query: { type: string, description: "搜索关键词" }
---
```

调用 `ReloadSkills` 工具即可动态加载新技能。

## API Endpoints

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/health` | GET | 健康检查 |
| `/api/v1/sessions` | POST | 创建会话 |
| `/api/v1/sessions/{id}/chat` | POST | 聊天（SSE 流式） |
| `/session` | POST | 创建会话（OpenCode 兼容） |
| `/session/{id}/message` | POST | 发送消息（OpenCode 兼容） |
| `/v1/chat/completions` | POST | Chat Completions（AI SDK v6） |
| `/vibe/chat` | POST | Vibe coding API |

## LLM 支持

通过 `pi-ai-server` 桥接层调用 LLM，支持：

| 模型 | 状态 | 说明 |
|------|------|------|
| Claude (Anthropic) | ✅ 完整支持 | 默认模型，原生 tool calling |
| GPT-4o (OpenAI) | ✅ 完整支持 | 原生 tool calling |
| Gemini (Google) | ✅ 支持 | 需要额外的幻觉检测和名称修正 |
| Ollama (本地) | ⚠️ 实验性 | 示例代码可用 |

配置方式：
```bash
export NIMBUS_MODEL="anthropic/claude-sonnet-4-20250514"
# 或
export NIMBUS_MODEL="google-antigravity/gemini-3-pro-high"
```

## Development

### 代码规范

- **Formatter**: ruff (line-length=100)
- **Type Checker**: mypy (strict mode)
- **Test**: pytest + pytest-asyncio
- **Python**: 3.10+

```bash
ruff format src/ tests/    # 格式化
ruff check src/ tests/     # Lint
mypy src/nimbus/           # 类型检查
```

### 添加新工具

```python
from nimbus.tools.base import ToolDefinition, ToolParameter

definition = ToolDefinition(
    name="MyTool",
    description="Does something useful",
    parameters=[
        ToolParameter(name="input", type="string", description="输入内容"),
    ],
)

async def my_tool(input: str) -> str:
    return f"Result: {input}"

# 在 AgentOS 中注册
agent_os.register_tool("MyTool", my_tool, description="My tool")
```

### 添加新 Skill

```bash
mkdir -p skills/my-skill/scripts
# 编写 skills/my-skill/SKILL.md（YAML front matter + 描述）
# 编写 skills/my-skill/scripts/main.py（argparse 入口）
# Agent 调用 ReloadSkills 即可加载
```

## Documentation

详细文档位于 `docs/` 目录：

| 文档 | 说明 |
|------|------|
| [project-status-2026-02.md](docs/project-status-2026-02.md) | 项目现状报告 |
| [architecture.md](docs/architecture.md) | 整体架构设计 |
| [vcpu-internals.md](docs/vcpu-internals.md) | vCPU 内部实现详解 |
| [getting-started.md](docs/getting-started.md) | 快速上手指南 |
| [api-reference.md](docs/api-reference.md) | API 参考 |
| [advanced-usage.md](docs/advanced-usage.md) | 高级用法 |
| [skills-development.md](docs/skills-development.md) | Skill 开发指南 |
| [troubleshooting-guide.md](docs/troubleshooting-guide.md) | 故障排查 |
| [TODO.md](docs/TODO.md) | 待办事项 |

## License

MIT
