# Nimbus 项目现状报告

> 截止 2026-02-14，基于完整代码审查和测试验证。

## 一、项目概况

Nimbus 是一个采用 **类操作系统架构** 的 AI Agent 框架（v0.2.0 Alpha）。核心思想是将 Agent 执行抽象为 vCPU（Think-Act-Observe 循环）、MMU（上下文记忆管理）、Gate（权限隔离的工具访问）三大组件，通过 AgentOS 统一编排。

| 指标 | 数值 |
|------|------|
| Python 源码 | 97 个文件，~29,500 行 |
| Web UI (Next.js) | 41 个文件，~5,800 行 |
| 测试用例 | 472 passed, 21 skipped |
| 版本 | 0.2.0 Alpha |
| Python 版本要求 | ≥ 3.10 |

## 二、代码结构

### 2.1 源码模块分布

```
src/nimbus/                 # 29,500 行
├── core/          (31 files, 11,923 lines)  # 核心引擎
│   ├── runtime/   # vCPU 及其子组件
│   ├── memory/    # MMU 上下文管理
│   ├── models/    # LLM 模型能力清单
│   ├── scheduler.py       # DAG 调度器
│   ├── compaction.py      # 上下文压缩
│   ├── session.py         # Session 管理
│   └── protocol.py        # ActionIR, ToolResult, Fault
├── server/        (19 files, 6,575 lines)   # HTTP API 服务
├── tools/         (11 files, 2,685 lines)   # 内置工具
├── cli/           (8 files, 1,250 lines)    # CLI 命令行
├── orchestration/ (6 files, 1,236 lines)    # 多 Agent 编排
├── storage/       (2 files, 1,240 lines)    # 持久化存储
├── adapters/      (3 files, 518 lines)      # LLM 适配器 (DirectAdapter)
├── os/            (2 files, 504 lines)      # Gate 系统调用
├── skills/        (5 files, 333 lines)      # Skill 热加载系统
├── testing/       (2 files, 408 lines)      # Mock LLM 工具
├── utils/         (3 files, 185 lines)      # 通用工具
├── agentos.py     (1,687 lines)             # AgentOS 主入口
├── agents/        (空目录)
└── data/          (内置 Agent 配置)
```

### 2.2 核心文件

| 文件 | 行数 | 职责 |
|------|------|------|
| `agentos.py` | 1,687 | 顶层编排器，进程管理，工具注册 |
| `core/runtime/vcpu.py` | 1,704 | Think-Act-Observe 执行循环 |
| `core/memory/mmu.py` | 950 | 上下文栈、滑动窗口、图片优化 |
| `core/scheduler.py` | 968 | DAG 任务调度、并行执行 |
| `tools/base.py` | 736 | ToolDefinition、ToolRegistry |
| `core/compaction.py` | 597 | LLM 驱动的上下文压缩 |
| `os/gate.py` | ~400 | 权限隔离的工具分发 |

### 2.3 配套组件

| 组件 | 位置 | 说明 |
|------|------|------|
| **Web UI** | `web-ui/` | Next.js + Tailwind，聊天界面 + Debug 面板 |
| **Bridge** | `bridge/` | (已废弃) 旧 pi-ai-server.ts，已被 DirectAdapter 替代 |
| **Harbor** | `nimbus_harbor/` | Agent 评测任务集（4 个 Docker 化 task） |
| **Deploy** | `deploy/` | systemd/launchd/nginx 配置 |
| **Skills** | `skills/` | 可热加载的技能包（当前：web-search） |
| **Pi Extension** | `pi-extension/` | pi coding agent 扩展 |

## 三、LLM 模型配置现状

模型配置已通过 `NimbusConfig` 统一管理（`src/nimbus/config.py`），遵循分层加载策略：

**加载优先级**: 代码默认值 < `~/.nimbus/config.json` < 环境变量

| 配置方式 | 示例 | 说明 |
|---------|------|------|
| `NimbusConfig.default_model` | `google/gemini-3-flash-preview` | 代码默认值 |
| `~/.nimbus/config.json` | `{"llm": {"default_model": "..."}}` | 用户配置文件 |
| `NIMBUS_MODEL` 环境变量 | `anthropic/claude-sonnet-4-20250514` | 环境变量覆盖 |

**LLM 适配器**: 使用 `DirectAdapter`（基于 LiteLLM）直接调用各厂商 API，无需外部桥接服务。通过 `create_llm_client()` 工厂函数创建。

**模型能力清单** (`core/models/manifest.py`) 定义了三个模型族的特性：
- **GPT 系列**（默认）：原生 tool calling，不需要名称修正
- **Claude 系列**：原生 tool calling，可能边思考边调工具
- **Gemini 系列**：易出现幻觉（文本模拟 tool call），需要名称修正

## 四、测试现状

### 4.1 测试结果总览

```
472 passed, 21 skipped, 0 failed
运行时间：~3 分钟
```

### 4.2 测试分布

| 类别 | 数量 | 说明 |
|------|------|------|
| **vCPU 行为测试** | ~80 | 执行循环、错误处理、doom loop、中断 |
| **MMU 记忆测试** | ~60 | 上下文栈、压缩、图片优化、消息排序 |
| **工具系统测试** | ~40 | Read/Write/Edit/Bash、沙箱、base 类 |
| **AgentOS 集成测试** | ~35 | 初始化、进程管理、DAG 执行 |
| **Scheduler 测试** | ~30 | DAG 调度、并行、依赖管理 |
| **Session 管理** | ~20 | 会话池、执行状态、检查点 |
| **Server API** | ~20 | HTTP 端点、SSE 流 |
| **CLI 测试** | ~20 | 命令行场景测试 |
| **Skills 测试** | ~5 | 技能加载、执行 |
| **能力评估** | ~40 | Agent 代码能力综合测试 |
| **其他** | ~120 | 配置、协议、适配器等 |

### 4.3 Skipped 测试 (21 个)

需要运行外部服务的 E2E 测试，已添加 `pytest.mark.skip`：

- `test_ai_sdk_e2e.py` — AI SDK 端点测试（需 server）
- `test_api_sdk_chat.py` — 聊天 API 测试（需 server）
- `test_opencode_api.py` — OpenCode 兼容 API（需 server）
- `test_v2_e2e_pi_ai.py` — Pi AI 集成测试（需 pi-ai-server）
- `test_task_drift.py` — 任务漂移压测（需 LLM）
- 若干 e2e standalone 脚本（非 pytest 格式，不被收集）

### 4.4 本次修复的 Bug

代码审查过程中发现并修复了 **3 个源码 bug**：

1. **VCPU 缺少 `_doom_loop_count` 属性** — 添加只读 property 代理到 `_doom_detector.loop_count`
2. **MMU `_mark_assistant_tool_call` 不兼容 dataclass** — tool_call 可能是 dict 或 dataclass，需要同时支持
3. **MMU `filter_discardable` 参数未实现** — 声明了参数但从未在 assemble_context 中过滤

## 五、已完成的能力

### 5.1 核心执行引擎
- ✅ vCPU Think-Act-Observe 循环
- ✅ 多模型支持（Claude / GPT / Gemini）+ 模型能力清单
- ✅ 工具名称自动修正（`read` → `Read`）
- ✅ Doom Loop 检测（连续 3 次相同调用则中止）
- ✅ 空结果处理（Glob/Grep 无匹配时给出引导）
- ✅ 错误自动恢复（ErrorHandlerRegistry）
- ✅ 幻觉检测与纠正（文本模拟 tool call 时注入校正指令）
- ✅ Checkpoint 保存/恢复

### 5.2 记忆管理
- ✅ Context Stack（帧栈结构，push/pop）
- ✅ 滑动窗口 + Hot Context（最近 15 条始终可见）
- ✅ LLM 驱动的上下文压缩（Distill & Archive）
- ✅ 滚动摘要 + 预算控制（防止无限增长）
- ✅ 图片 token 优化（去重 + 预算限制）
- ✅ 消息排序安全网（tool_call → tool 顺序保证）
- ✅ Memo 工具（烂笔头，跨压缩持久记忆）
- ✅ Discardable 标记 + 过滤

### 5.3 工具系统
- ✅ 内置工具：Read, Write, Edit, Bash, Memo, ReloadSkills
- ✅ ToolDefinition + ToolParameter 类型系统
- ✅ ToolCategory（core / extension / skill）分类
- ✅ 危险工具标记
- ✅ Skill 热加载（SKILL.md 声明 + 脚本入口）
- ✅ CompositeToolRegistry（核心工具 + 技能工具统一视图）

### 5.4 多进程与调度
- ✅ 进程 spawn/wait，角色权限隔离
- ✅ DAG 调度器（并行执行、依赖管理）
- ✅ AgentProfile 配置（core / executor / standard 模板）

### 5.5 Server 与 API
- ✅ FastAPI HTTP 服务
- ✅ SSE 流式响应
- ✅ 多协议兼容：REST / OpenCode / AI SDK v6 / Vibe
- ✅ Session 管理（创建、列表、中断）
- ✅ Permission 审批机制

### 5.6 Web UI
- ✅ Next.js 聊天界面
- ✅ Markdown 渲染 + 代码高亮
- ✅ Debug 面板（上下文查看）
- ✅ Session 管理面板
- ✅ Playwright E2E 测试框架（tier1/2/3）

## 六、已知问题与待完善

### 6.1 ~~模型配置分散~~ (已解决)
已通过 `NimbusConfig` (`src/nimbus/config.py`) 统一为单一配置源，所有入口均使用 `get_config().default_model`。

### 6.2 Session 持久化未完成
`checkpoint_manager.py` 和 SQLite 存储已就绪，但 `session_v2.py` 未对接 Hibernate/Wake 流程。

### 6.3 空目录
- `src/nimbus/agents/` — 空目录，原计划放预定义 Agent 角色
- `src/nimbus/data/agents/` — 有 YAML 配置但未被使用

### 6.4 旧代码残留
- `core/memory_legacy.py` -- 旧版记忆管理，已被 `core/memory/mmu.py` 取代
- `bridge/` 目录 -- 旧 pi-ai-server (TypeScript)，已被 `DirectAdapter` (LiteLLM) 替代

### 6.5 E2E 测试覆盖
- 20+ 个 e2e 测试脚本（`tests/e2e_*.py`）是独立脚本，非 pytest 格式
- 需要手动启动 server + pi-ai-server 才能跑
- Web UI 的 Playwright 测试需要单独运行

## 七、项目目录最终结构

```
nimbus/
├── src/nimbus/         # Python 源码 (97 files, ~29.5k lines)
├── tests/              # 测试 (472 passed, 21 skipped)
├── web-ui/             # Next.js Web 界面
├── bridge/             # (已废弃) 旧 pi-ai-server，已被 DirectAdapter 替代
├── examples/           # 示例代码 (18 个)
├── nimbus_harbor/      # Agent 评测 (4 个 Docker task)
├── pi-extension/       # Pi coding agent 扩展
├── scripts/            # 辅助脚本
├── skills/             # 可热加载技能包
├── deploy/             # 部署配置
├── docs/               # 文档 (~40 篇)
├── nimbus              # 主启动脚本 (bash)
├── Makefile            # 常用命令
├── pyproject.toml      # Python 项目配置
├── uv.lock             # 依赖锁
└── README.md
```
