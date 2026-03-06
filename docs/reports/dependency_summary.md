# Nimbus 项目依赖总结报告

## 1. 项目概览
- **项目名称**: nimbus (Nimbus Agent Framework)
- **版本**: 0.2.0
- **Python 版本要求**: >=3.10
- **定位**: 笔记本风格的 AI 助手，支持 DAG 规划和分层记忆。

## 2. 核心依赖 (Core Dependencies)
这些依赖是运行 Nimbus 框架所必需的基础库：

| 类别 | 依赖包 | 版本要求 | 用途 |
| :--- | :--- | :--- | :--- |
| **异步与网络** | `aiohttp`, `httpx` | >=3.9.0 / >=0.24.0 | 异步 HTTP 请求处理 |
| **API 服务** | `fastapi`, `uvicorn`, `sse-starlette` | 常用版本 | 构建后端服务与 Server-Sent Events 支持 |
| **数据校验** | `pydantic` | >=2.0.0 | 模型定义与数据校验 |
| **存储** | `aiosqlite` | >=0.19.0 | 异步 SQLite 数据库操作 |
| **CLI & UI** | `typer`, `rich` | 常用版本 | 命令行交互与终端美化 |
| **工具类** | `loguru`, `PyYAML`, `html2text` | 常用版本 | 日志记录、配置解析、HTML 转 Markdown |
| **搜索** | `duckduckgo-search` | >=6.0.0 | 联网搜索功能 |

## 3. 可选依赖 (Optional Dependencies)
项目通过 `extras` 提供了模块化的扩展功能：

### 3.1 LLM 模块 (`[llm]`)
用于支持主流大模型提供商：
- `anthropic >= 0.20.0`
- `openai >= 1.0.0`

### 3.2 RAG 模块 (`[rag]`)
用于支持向量检索增强：
- `chromadb >= 0.4.0`

### 3.3 开发环境 (`[dev]`)
用于项目开发、测试与质量保证：
- `pytest >= 7.0.0`
- `pytest-asyncio >= 0.21.0`
- `httpx` (测试用)

### 3.4 其他
- `[acp]`: 目前使用核心依赖，无额外包。
- `[all]`: 包含上述所有扩展包。

## 4. 构建与质量工具
- **构建系统**: `hatchling`
- **代码规范**: `ruff` (配置行宽 100)
- **类型检查**: `mypy` (要求 `disallow_untyped_defs`)
- **测试框架**: `pytest`
