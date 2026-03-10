# Nimbus AgentOS

> **Building Resilient, Long-Horizon AI Agents with Operating System Principles.**

Nimbus 是一款受操作系统内核设计启发的 AI Agent 运行时框架。它将 LLM 视为 CPU，围绕其构建了完整的系统抽象，专注解决 Agent 在复杂长程任务中的上下文漂移、状态管理失效和稳定性问题。

当前版本：**v0.2.0 (Nimbus Next)**

---

## 🏗 核心架构

```
┌─────────────────────────────────────────────────┐
│                AgentOS  (Facade)                 │
├──────────┬──────────┬──────────┬────────────────┤
│   VCPU   │   MMU    │KernelGate│  ALU / Adapter │
│  FSM引擎 │上下文管理│  工具执行  │   LLM 接口层   │
├──────────┴──────────┴──────────┴────────────────┤
│               RuntimeLoop  (驱动层)              │
│          Think → Act → Observe → Think …        │
└─────────────────────────────────────────────────┘
```

### VCPU — 虚拟 CPU

基于有限状态机（FSM）驱动 Think-Act-Observe 循环：

```
                       ┌─────────────────────────────┐
                       ↓                             │
IDLE → THINKING → ACTING → OBSERVING → COMPRESSING ─┘
         ↑                     │             │
         └─────────────────────┘             │ 超过 max_compactions
                                             ↓
                              ERROR ──────► DEAD
```

**COMPRESSING 触发时机（三种）：**
1. 每轮循环开始前主动检测：`token 使用率 > 85%`
2. LLM 调用返回 `CTX_OVERFLOW` 错误时被动触发
3. VCPU 迭代次数超过上限（`BUDGET_EXCEEDED`）时触发，同时重置迭代计数器

**保险机制：** 最多压缩 `3` 次（`max_compactions`），且两次压缩之间必须间隔 `5` 步（`compaction_cooldown`），防止死循环。超出限制直接进入 `DEAD`。

- 最大迭代次数：200（上下文压缩是真正的资源边界）
- 支持实时 Steering 注入（用户可在 Agent 运行中途插入新指令）
- 并行工具调用（`asyncio.gather`）

### MMU — 内存管理单元

- **Pinned Context（锚点）**：固定系统规则、核心目标，防止 LLM 近因偏见导致的"遗忘"
- **动态 Stream**：自动追踪对话历史，token 使用率超过 85% 时触发压缩
- **压缩策略**：滑动窗口（默认）/ 摘要压缩 / 语义相关性筛选（需 embedding 服务）
- 默认 Context Budget：100k tokens（标准）/ 1M tokens（claude-sonnet-4-6）

### KernelGate — 工具执行安全层

- 权限白名单/黑名单过滤
- `asyncio.wait_for` 超时 + 进程组 `SIGKILL` 隔离
- 工具结果分离：`output`（LLM 可见）+ `ui_detail`（仅 UI 展示）

### ALU / Adapter — LLM 接口层

三通道自动选择：

| 通道 | 适用场景 |
|------|----------|
| Anthropic Native (OAuth) | Claude 模型 + Pi OAuth 凭证 |
| OpenAI Codex (OAuth) | Codex 模型 + ChatGPT 订阅凭证 |
| LiteLLM (默认) | Gemini、OpenAI API Key、其他所有模型 |

---

## 🤝 多 Agent 协作：spawn_agent

Unix 哲学：**子 Agent 即子进程**，通过工具调用拉起。

```python
# 同步：父 Agent 阻塞等待结果
spawn_agent(role="Test Engineer", task="为 auth 模块写单测", mode="sync")

# 异步：后台运行，返回 PID
spawn_agent(role="Security Scanner", task="扫描 src/ 目录", mode="async")
```

**核心优势**：
- 子 Agent 拥有独立的 MMU，上下文不污染父 Agent
- 父 Agent 只收到精炼的结果摘要，彻底避免长链任务的记忆崩溃
- 框架层处理超时熔断，父 Agent 只需关心成功/失败

> ⚠️ 当前 `spawn_agent` 为 stub 实现，真实嵌套 `AgentOS` 实例化是下一个里程碑。

---

## 🛠 内置工具集

| 工具 | 描述 |
|------|------|
| `bash` | 执行 shell 命令，支持流式输出 |
| `read` | 读取文件内容，支持 offset/limit |
| `write` | 写入文件，自动创建目录 |
| `edit` | 精确文本替换编辑 |
| `grep` | 正则搜索文件内容 |
| `spawn_agent` | 派生子 Agent 处理复杂独立任务 |

自定义工具通过 `@tool` 装饰器注册：

```python
from nimbus.core.tools.registry import tool

@tool
async def my_tool(param: str) -> str:
    return f"result: {param}"
```

---

## 🌐 Web UI

基于 **Next.js 14 + TypeScript + Tailwind CSS** 的实时对话界面。

- SSE 实时流式渲染（`_pendingStreamMsg` rAF buffer，解决同帧事件覆盖问题）
- 会话管理、文件浏览、工具执行可视化
- 端口：`3000`（生产）/ `3001`（staging）

---

## 🚀 快速开始

```bash
# 克隆仓库
git clone https://github.com/WQ09CE/nimbus.git
cd nimbus

# 安装依赖（推荐使用 uv）
pip install -e ".[llm]"

# 启动服务端
nimbus serve

# 启动 Web UI（另开终端）
cd web-ui && npm install && npm run dev
```

环境变量（二选一）：

```bash
# 使用 API Key
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

# 或使用 Pi OAuth（自动从 ~/.pi/agent/auth.json 读取）
```

---

## 🧪 测试

```bash
# 核心单元测试
pytest tests/core --tb=short

# 全量测试
pytest tests/ --tb=short
```

---

## 📁 项目结构

```
nimbus/
├── src/nimbus/
│   ├── core/
│   │   ├── agent.py          # AgentOS facade
│   │   ├── vcpu.py           # FSM 执行引擎
│   │   ├── mmu.py            # 上下文管理
│   │   ├── gate.py           # KernelGate 安全执行
│   │   ├── loop.py           # RuntimeLoop 驱动层
│   │   ├── adapter.py        # ALU 基类
│   │   ├── decoder.py        # InstructionDecoder
│   │   ├── protocol.py       # Event / ToolCall / ToolResult 协议
│   │   └── tools/            # 内置工具集
│   ├── adapters/
│   │   ├── direct_adapter.py # 三通道 LLM 适配器
│   │   └── llm_factory.py    # 模型工厂
│   ├── server/
│   │   ├── api.py            # FastAPI 路由
│   │   ├── session.py        # SessionManagerV2
│   │   └── sse.py            # SSE Hub
│   └── cli/                  # nimbus CLI
├── web-ui/                   # Next.js 前端
└── tests/                    # 测试套件
```

---

## 📈 路线图

- [ ] **spawn_agent 真实实现** — 嵌套 `AgentOS` 实例化，完成多 Agent 进程树
- [ ] **async spawn 查询 API** — `wait_agent(pid)` / `kill_agent(pid)` 工具
- [ ] **MMU 重构** — 拆分 `mmu.py` 为 `context_manager` / `compressor` / `pinned_store`
- [ ] **进程树 UI 可视化** — Web UI 展示 Agent 父子关系和状态
- [ ] **Semantic Compression** — 接入 embedding 服务，语义相关性筛选压缩策略

---

*Nimbus — 赋予 LLM 真正的系统级执行能力。*
