# Nimbus Web-UI 技术现状文档

## 1. 架构概览 (Architecture Overview)

Nimbus Web-UI 采用现代化的前后端分离架构，旨在提供高性能、低延迟的智能助手交互体验。

- **前端 (Frontend):** 基于 Next.js 14 (App Router) 构建，利用 React Server Components (RSC) 和客户端组件的混合模式。
- **后端 (Backend):** 基于 FastAPI 构建的 Python 服务，负责编排 AI 代理、管理会话状态及处理长连接。
- **通信协议 (Communication):** 混合使用 HTTP REST（用于元数据管理）和 Server-Sent Events (SSE)（用于实时流式响应）。

## 2. 核心机制详解

### 2.1 流式传输 (Streaming via SSE)
Web-UI 实现了基于 `Server-Sent Events` 的完整流式协议：
- **服务端:** 通过 `EventSourceResponse` 持续推送 `text/event-stream` 内容。
- **客户端:** 使用自定义的流解析器处理不同类型的事件（如 `token`, `status`, `tool_call`, `error`）。
- **优化:** 实现了打字机效果（Typewriter effect）增强用户感知流畅度。

### 2.2 持久化与存储 (Persistence)
- **数据库:** 使用 PostgreSQL 进行结构化数据存储。
- **消息存储:** 所有的对话记录、工具调用结果和元数据均持久化在 `messages` 表中。
- **文件存储:** 附件和生成的构件（Artifacts）存储在本地或云端 S3 兼容存储。

### 2.3 会话管理 (Session Management)
- **多会话支持:** 每个用户可以拥有多个独立的 `Session`。
- **上下文窗口:** 系统根据 LLM 的上下文限制自动处理历史消息的截断或摘要（Summary）。
- **状态同步:** 前端通过 `useSWR` 或 `React Query` 保持会话列表与后端的实时同步。

### 2.4 消息注入 (Message Injection)
- **机制:** 支持系统级消息和用户不可见的消息注入，用于引导模型行为或补充 RAG 上下文。
- **钩子:** 在 `pre-processing` 阶段，通过拦截器向 `history` 中动态插入隐藏的 `contextual messages`。

### 2.5 中断机制 (Interrupt Mechanisms)
- **用户中断:** 支持前端发送 `SIGINT` 或特定的取消请求到后端，立即停止 LLM 生成或工具执行。
- **条件中断:** 在复杂的工作流（Workflow）中，系统可以在特定节点设置 `breakpoint`，等待人工确认（Human-in-the-loop）后再继续。

## 3. 当前状态总结

| 功能模块 | 状态 | 备注 |
| :--- | :--- | :--- |
| SSE Streaming | ✅ 已实现 | 支持多消息并行流 |
| Postgres 持久化 | ✅ 已实现 | 事务性保证消息一致性 |
| 会话切换 | ✅ 已实现 | 无缝切换不同对话上下文 |
| 消息注入 | ⚠️ 迭代中 | 正在优化 RAG 相关的注入逻辑 |
| 任务中断 | ✅ 已实现 | 能够响应前端取消操作 |

## 4. 后续改进建议
- 引入 WebSocket 以支持双向全双工实时交互。
- 进一步优化 SSE 连接在网络不稳定情况下的自动重连逻辑（Exponential Backoff）。
- 增强消息注入的透明度监控，便于开发者调试上下文污染问题。
