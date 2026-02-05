# Nimbus + Harbor 集成指南

> 记录日期: 2026-02-05
>
> 本文档记录 Nimbus Agent 与 Harbor 评估框架的集成测试全过程，包括架构设计、部署流程、Bug 修复和测试结果。

---

## 目录

1. [架构概览](#1-架构概览)
2. [Docker 环境配置](#2-docker-环境配置)
3. [Wheel 部署流程](#3-wheel-部署流程)
4. [嵌入式 Agent 脚本](#4-嵌入式-agent-脚本)
5. [关键 Bug 修复记录](#5-关键-bug-修复记录)
6. [测试结果](#6-测试结果)
7. [已知问题与改进方向](#7-已知问题与改进方向)
8. [快速开始](#8-快速开始)

---

## 1. 架构概览

Nimbus 与 Harbor 的集成采用 **Host-Container 分离架构**：LLM 服务运行在宿主机，Agent 执行逻辑运行在 Docker 容器内部。

```
Host 机器 (macOS)                     Docker 容器
====================                  ====================
pi-ai server (port 3031)  <-------->  nimbus agent (httpx)
  |                                      |
  v                                      v
LLM API (Claude/GPT)                 任务执行 (Read/Write/Edit/Bash)
                                         |
                                         v
                                      输出结果 -> Harbor 评估
```

**核心数据流**:

1. Harbor 根据数据集创建 Docker 容器
2. `NimbusAgent.setup()` 将 nimbus wheel 上传到容器并安装
3. `NimbusAgent.run()` 在容器中执行嵌入式 Agent 脚本，调用 `AgentOS.run()`
4. Agent 通过 httpx 连接宿主机的 pi-ai server 获取 LLM 响应
5. Agent 使用工具 (Read/Write/Edit/Bash) 在容器内完成任务
6. Harbor 收集容器状态，执行验证脚本，计算 pass rate

**关键组件**:

| 组件 | 位置 | 作用 |
|------|------|------|
| `nimbus_harbor/nimbus_agent.py` | 宿主机 | Harbor adapter，管理容器生命周期 |
| `nimbus-0.2.0-py3-none-any.whl` | 上传到容器 | Nimbus 核心库 (AgentOS/Tools/Adapters) |
| `/tmp/nimbus_agent.py` | 容器内 | 嵌入式 Agent 脚本 (动态生成) |
| pi-ai server | 宿主机 :3031 | LLM 代理服务 |

---

## 2. Docker 环境配置

### 2.1 Host Gateway IP 差异

不同的 Docker 运行时，容器内访问宿主机的方式不同：

| 运行时 | Host Gateway IP | 配置方式 |
|--------|----------------|----------|
| **Colima** | `192.168.5.2` | 当前默认值 |
| **Docker Desktop** | `host.docker.internal` | 通过 `PI_AI_HOST` 环境变量覆盖 |

在 `nimbus_agent.py` 中，默认使用 Colima 的 IP：

```python
PI_AI_HOST: str = os.environ.get("PI_AI_HOST", "192.168.5.2")
```

如果使用 Docker Desktop，需要设置环境变量：

```bash
export PI_AI_HOST=host.docker.internal
```

### 2.2 容器环境不统一

不同数据集的容器镜像差异很大，不能假设容器内有特定工具：

- 有的容器有 `python3` 但没有 `python`
- 有的容器有 `curl` 但没有 `wget`
- 有的容器连 `pip` 都需要先安装

**应对策略**: Health check 尝试多种方式，依次回退：

```python
health_cmds = [
    f'python3 -c "import urllib.request; ..."',   # 优先 python3
    f'python -c "import urllib.request; ..."',     # 回退 python
    f"curl -sf {url}/health",                      # 回退 curl
    f"wget -q -O - {url}/health",                  # 回退 wget
]
```

### 2.3 工作目录问题

**不要硬编码 `cd /workspace`**。不同数据集的容器工作目录不同：

- 有的容器工作目录是 `/`
- 有的是 `/workspace`
- 有的是 `/home/user`

应该使用 `os.getcwd()` 获取当前工作目录，或者不依赖特定路径。在嵌入式 Agent 脚本中，我们使用 `Path.cwd()` 来动态获取工作目录：

```python
register_default_tools(agent, workspace=Path.cwd())
```

---

## 3. Wheel 部署流程

### 3.1 构建

Nimbus 使用 `hatchling` 构建系统，采用 `src` layout：

```bash
# 构建 wheel
python -m build --wheel --outdir dist
```

构建配置 (`pyproject.toml`)：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "nimbus"
version = "0.2.0"

[tool.hatch.build.targets.wheel]
packages = ["src/nimbus"]

[tool.hatch.build.targets.wheel.sources]
"src" = ""
```

### 3.2 生成产物

```
dist/nimbus-0.2.0-py3-none-any.whl  (~242KB)
```

Wheel 包含 `src/nimbus/` 下的所有核心代码，包括：

- `nimbus.agentos` - AgentOS 核心
- `nimbus.core` - VCPU / MMU / 运行时
- `nimbus.tools` - 内置工具 (Read/Write/Edit/Bash)
- `nimbus.adapters` - LLM 适配器 (PiLLMAdapter)

### 3.3 部署到容器

部署通过 Harbor 的 `environment.upload_file()` API 完成：

```python
# 1. 上传 wheel 到容器
await environment.upload_file(wheel_path, "/tmp/nimbus-0.2.0-py3-none-any.whl")

# 2. 在容器中安装
await environment.exec("pip install /tmp/nimbus-0.2.0-py3-none-any.whl")
```

### 3.4 注意事项

- **nimbus 核心代码改动后需要重新构建 wheel**。如果只改了 `nimbus_agent.py`（adapter 本身），不需要重新构建，因为 adapter 从宿主机文件系统加载，不在 wheel 里。
- `nimbus_harbor/nimbus_agent.py` 本身 **不在 wheel 里**，它是 Harbor adapter，由 Harbor 框架在宿主机上直接加载。
- 如果存在旧的 wheel，`_build_nimbus_wheel()` 会优先使用已有的（按修改时间排序），避免每次都重新构建。

---

## 4. 嵌入式 Agent 脚本

### 4.1 为什么需要嵌入式脚本

Harbor 的执行模型是在 Docker 容器中运行命令。Nimbus Agent 需要在容器内执行完整的 agent loop (多轮 LLM 调用 + 工具执行)，因此我们生成一个自包含的 Python 脚本注入到容器中执行。

### 4.2 Base64 编码传输

由于脚本中包含大量引号、换行、花括号等特殊字符，直接通过 shell 写入会导致转义问题。解决方案是用 base64 编码：

```python
encoded_script = base64.b64encode(agent_script.encode()).decode()
await environment.exec(f"echo '{encoded_script}' | base64 -d > /tmp/nimbus_agent.py")
```

### 4.3 脚本核心逻辑

嵌入式脚本使用 Nimbus 公开 API：

```python
from nimbus.adapters.pi_adapter import PiLLMAdapter, PiLLMConfig
from nimbus.agentos import AgentOS, AgentOSConfig
from nimbus.core.runtime.vcpu import VCPUConfig
from nimbus.tools import register_default_tools

# 创建 LLM 适配器 (连接宿主机 pi-ai)
config = PiLLMConfig(base_url=PI_AI_URL, model=MODEL)
llm = PiLLMAdapter(config)

# 创建 AgentOS 并注册工具
vcpu_config = VCPUConfig(max_iterations=MAX_ITERATIONS)
agent_config = AgentOSConfig(vcpu_config=vcpu_config)
agent = AgentOS(llm_client=llm, config=agent_config)
register_default_tools(agent, workspace=Path.cwd())

# 执行任务
result = await agent.run(instruction)
```

### 4.4 日志收集

Agent 日志写入容器的 `/tmp/nimbus_logs/` 目录，执行完成后由 adapter 提取回宿主机：

```python
# 容器内日志路径
setup_logging(level="INFO", log_dir="/tmp/nimbus_logs", console=True)

# 执行后提取
log_result = await environment.exec("cat /tmp/nimbus_logs/nimbus.log")
nimbus_log_path = self.logs_dir / "nimbus_agent.log"
nimbus_log_path.write_text(log_result.stdout)
```

同时，agent 的 stdout/stderr 也会保存到宿主机的 `logs_dir`。

---

## 5. 关键 Bug 修复记录

### 5.1 LLMClient Protocol 不匹配

**文件**: `vcpu.py:1393`

**问题**: VCPU 调用 `self.alu.complete()` 方法，但 `PiLLMAdapter` 实现的是 `chat()` 方法。

**修复**: 将调用从 `self.alu.complete()` 改为 `self.alu.chat()`，匹配 LLMClient Protocol 定义。

```python
# Before (错误)
response = await self.alu.complete(messages, tools=tools)

# After (正确)
response = await self.alu.chat(messages, tools=tools)
```

### 5.2 工具格式转换问题

**问题**: Nimbus 内部工具格式 `{name, parameters}` 与 pi-ai 期望的 OpenAI 格式 `{type: "function", function: {...}}` 不匹配。

**Nimbus 内部格式**:
```json
{
  "name": "Read",
  "parameters": {
    "type": "object",
    "properties": { "file_path": { "type": "string" } }
  }
}
```

**OpenAI / pi-ai 期望格式**:
```json
{
  "type": "function",
  "function": {
    "name": "Read",
    "description": "Read a file",
    "parameters": {
      "type": "object",
      "properties": { "file_path": { "type": "string" } }
    }
  }
}
```

**修复**: 在 `PiLLMAdapter` 中增加工具格式转换逻辑，将 Nimbus 内部格式适配为 OpenAI 格式后再发送给 pi-ai。

### 5.3 硬编码工作目录

**问题**: 早期版本中 Agent 脚本包含 `cd /workspace &&` 前缀，导致在工作目录不是 `/workspace` 的容器中执行失败。

**修复**: 移除 `cd /workspace &&` 硬编码，使用容器的默认工作目录。

---

## 6. 测试结果

### 6.1 EvoEval@1.0 (100 tasks)

EvoEval 是 Python 函数生成任务，每个 task 要求 agent 根据 docstring 实现一个函数。

| 模型 | Pass Rate | 备注 |
|------|-----------|------|
| `claude-sonnet-4-5` | **70%** | 当前最佳 |
| `claude-sonnet-4` | **64%** | 基线对比 |

**测试参数**:
- `MAX_ITERATIONS`: 20 (nimbus core 默认值为 50)
- `TASK_EXECUTION_TIMEOUT`: 300s (5 分钟)

### 6.2 HumanEvalFix@1.0

| 模型 | Pass Rate | 备注 |
|------|-----------|------|
| `claude-sonnet-4-5` | **0%** | 数据集验证器问题 |

**失败原因**: 容器中缺少 `/tests/test.sh` 验证脚本。这是 HumanEvalFix 数据集本身的问题，不是 Nimbus Agent 的问题。Agent 实际上可能已经正确修复了代码，但由于验证脚本缺失，所有 task 都被判定为失败。

### 6.3 自定义 Tasks (4 tasks)

| 模型 | Pass Rate | 备注 |
|------|-----------|------|
| `claude-sonnet-4-5` | **100%** (4/4) | 手写简单编程题 |

自定义 task 位于 `nimbus_harbor/tasks/simple-coding-test`，用于验证基本的端到端流程。

### 6.4 测试参数分析

当前测试参数相对保守，**未测出 agent 的真实极限**：

| 参数 | 当前值 | Core 默认值 | 建议值 |
|------|--------|-------------|--------|
| `MAX_ITERATIONS` | 20 | 50 | 50 |
| `TASK_EXECUTION_TIMEOUT` | 300s | - | 600s |

提高这两个参数预计能进一步提升 pass rate，因为部分 task 可能因为迭代次数或超时限制而提前终止。

---

## 7. 已知问题与改进方向

### 7.1 数据集相关问题

| 问题 | 影响 | 状态 |
|------|------|------|
| HumanEvalFix 验证器 `/tests/test.sh` 缺失 | 所有 task 判定失败 (0%) | 待 Harbor 侧修复 |
| EvoEval 容器中无 `check_solution.py` | Agent 无法运行标准测试，只能自写测试验证，覆盖率低 | 待调研 |

### 7.2 参数调优

- **MAX_ITERATIONS**: 应提高到 50，匹配 nimbus core 默认值。当前 20 次迭代对于复杂 task 可能不够。
- **TASK_EXECUTION_TIMEOUT**: 应提高到 600s。部分 task 在 agent 还在思考时就被超时终止了。

### 7.3 运行时告警

- **Goal summarization 偶尔失败**: MMU 的 goal summarization 有时因为 LLM 响应格式问题而失败，回退到简单截断。这是 warning 级别，不影响最终结果，但会降低上下文质量。

### 7.4 未来改进方向

- 支持 ATIF 轨迹格式 (当前 `SUPPORTS_ATIF = False`)
- 优化 wheel 构建流程，支持增量更新
- 增加更多 benchmark 数据集 (SWE-bench, MBPP 等)
- 考虑在容器中缓存 wheel，避免每次 task 都重新上传安装

---

## 8. 快速开始

### 8.1 前置条件

1. pi-ai server 运行在宿主机端口 3031
2. Docker 运行时 (Colima 或 Docker Desktop) 已启动
3. Harbor 已安装 (`uv` 环境)

### 8.2 构建 Wheel

```bash
cd /Users/wangqing/sourcecode/agent/agent-framework/nimbus

# 构建 nimbus wheel
python -m build --wheel --outdir dist

# 验证产物
ls -la dist/nimbus-*.whl
# nimbus-0.2.0-py3-none-any.whl  (~242KB)
```

> 注意: 如果 `dist/` 下已有 wheel，adapter 会自动使用最新的，无需每次手动构建。

### 8.3 运行自定义 Task

```bash
uv run harbor run \
  -p nimbus_harbor/tasks/simple-coding-test \
  --agent-import-path nimbus_harbor.nimbus_agent:NimbusAgent
```

### 8.4 运行注册数据集

```bash
# EvoEval (100 tasks, Python 函数生成)
uv run harbor run \
  -d "evoeval@1.0" \
  --agent-import-path nimbus_harbor.nimbus_agent:NimbusAgent

# HumanEvalFix (代码修复，当前有验证器问题)
uv run harbor run \
  -d "humanevalfix@1.0" \
  --agent-import-path nimbus_harbor.nimbus_agent:NimbusAgent
```

### 8.5 切换模型

```bash
# 使用 claude-sonnet-4-5
NIMBUS_MODEL=anthropic/claude-sonnet-4-5 \
  uv run harbor run \
  -d "evoeval@1.0" \
  --agent-import-path nimbus_harbor.nimbus_agent:NimbusAgent

# 使用 claude-sonnet-4
NIMBUS_MODEL=anthropic/claude-sonnet-4 \
  uv run harbor run \
  -d "evoeval@1.0" \
  --agent-import-path nimbus_harbor.nimbus_agent:NimbusAgent
```

### 8.6 使用 Docker Desktop

```bash
# 覆盖 Host Gateway IP
PI_AI_HOST=host.docker.internal \
  uv run harbor run \
  -d "evoeval@1.0" \
  --agent-import-path nimbus_harbor.nimbus_agent:NimbusAgent
```

### 8.7 查看日志

Harbor 的日志目录通常在运行输出中指定。Nimbus 的日志会保存为：

- `nimbus_agent.log` - Agent 运行时日志 (从容器 `/tmp/nimbus_logs/nimbus.log` 提取)
- `nimbus_stdout.log` - Agent 标准输出

---

## 附录: 关键文件索引

| 文件 | 作用 |
|------|------|
| `nimbus_harbor/nimbus_agent.py` | Harbor adapter 主文件 |
| `nimbus_harbor/tasks/` | 自定义测试 task 目录 |
| `pyproject.toml` | Wheel 构建配置 |
| `src/nimbus/agentos.py` | AgentOS 核心 |
| `src/nimbus/adapters/pi_adapter.py` | Pi-AI LLM 适配器 |
| `src/nimbus/tools/` | 内置工具实现 |
| `src/nimbus/core/runtime/vcpu.py` | VCPU 运行时 |
