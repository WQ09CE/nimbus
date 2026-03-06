# Nimbus Context Sidecar Architecture Proposal
## —— 基于多进程的“双脑”记忆增强方案

### 1. 背景与痛点 (Background & Motivation)

在当前的 Agent 架构中，`nimbus core` 通常承担着双重责任：
1.  **执行 (Execution)**：理解当前指令，调用工具，修改代码。
2.  **记忆 (Memory)**：维护历史上下文，记住用户偏好，理解项目全貌。

随着对话轮数增加和项目复杂度提升，单一上下文窗口面临以下挑战：
*   **上下文溢出 (Context Overflow)**：不得不丢弃早期对话，导致 Agent "忘记" 了最初的架构设计或用户约束。
*   **注意力分散 (Distraction)**：过多的历史细节噪音干扰了当前的推理能力。
*   **认知负载 (Cognitive Load)**：Agent 需要同时处理"怎么改代码"和"为什么要这么改"两个层面的问题。

### 2. 核心概念 (Core Concept)

利用 `nimbus` 的多进程 (Multi-Process) 能力，引入一个专门的 **Context Process (Context Guardian / Memo Agent)**。

*   **Main Process (The Doer)**: 专注于当下的任务执行（“快思考”）。
*   **Context Process (The Observer)**: 专注于维护全局上下文、整理历史、构建知识图谱（“慢思考”）。

这是一种 **"主手 (Main) + 副手 (Sidecar)"** 的架构。副手不直接写代码，而是站在旁边记笔记、整理思路，并在主手困惑时提供关键信息。

### 3. 架构设计 (Architecture Design)

#### 3.1 角色定义

| 特性 | Main Process (Executor) | Context Process (Guardian) |
| :--- | :--- | :--- |
| **主要职责** | 执行工具 (Bash, Edit, Write)，与用户交互 | 监听事件，整理摘要，维护状态，回答 Main 的查询 |
| **思维模式** | 战术性 (Tactical)，微观 | 战略性 (Strategic)，宏观 |
| **输入流** | 用户 Prompt，工具输出 | Main Process 的所有 Event Stream (Copy) |
| **输出流** | 代码变更，最终回答 | 结构化记忆 (Memo)，对 Main 的上下文注入 |

#### 3.2 数据流向 (Data Flow)

1.  **Event Broadcasting (旁路监听)**:
    *   Main Process 在执行每一步操作（接收 User Input, 执行 Tool, 产生 Thought）时，将事件异步广播给 Context Process。
    *   Context Process 处于 "Listening Mode"。

2.  **Active Grooming (主动整理)**:
    *   Context Process 在后台运行一个循环，不断消化接收到的事件。
    *   它执行 **"压缩 (Compression)"** 和 **"结构化 (Structuring)"**：
        *   *Raw Logs* -> "用户修改了 Login 模块" (Summary)
        *   *File Changes* -> 更新内存中的 "Project Structure Map"
        *   *Decisions* -> 记录 "我们决定使用 JWT 而不是 Session" (Decision Log)

3.  **On-Demand Retrieval (按需查询)**:
    *   当 Main Process 遇到困难（例如：Context 满了需要截断，或者不知道某个变量在哪里定义时），它向 Context Process 发起 IPC 请求。
    *   Main: *"Hey, 帮我查一下我们上周定的 API 规范是什么？"*
    *   Context: *"根据第 5 轮对话的记录，我们决定使用 RESTful 风格，且所有时间字段用 ISO8601。"*

### 4. 关键功能模块 (Key Features)

#### 4.1 动态摘要流 (Rolling Summary Stream)
Context Process 维护一份实时更新的 Markdown 文档（即 "Enhanced Memo"）。它不是简单的追加，而是智能重写：
*   **Level 1**: 当前任务的详细步骤。
*   **Level 2**: 过去 1 小时的高层级操作摘要。
*   **Level 3**: 项目启动以来的关键决策树。

#### 4.2 意图锚点 (Intent Anchoring)
Context Process 始终锁定用户的"原始意图"。
*   如果 Main Process 跑偏了（例如开始无休止地优化一个无关紧要的函数），Context Process 可以通过 IPC 发送警告：*"检测到当前操作偏离了‘修复登录 Bug’的主要目标，是否需要纠正？"*

#### 4.3 影子文件树 (Shadow File Tree)
Context Process 维护一个轻量级的项目文件结构快照和简要说明（Skeleton）。
*   当 Main Process 需要 `ls` 或 `find` 时，可以先问 Context Process，减少文件系统 IO 操作，且 Context Process 能提供语义级别的文件定位（例如："存放所有 User 相关模型的文件在哪里？"）。

### 5. 交互协议 (Interaction Protocol)

我们可以定义一套简单的 IPC 协议：

*   `SYNC_EVENT`: Main -> Context (异步，包含 type, payload, timestamp)
*   `QUERY_CONTEXT`: Main -> Context (同步/异步，类似 RAG 查询)
*   `PUSH_HINT`: Context -> Main (中断/提示，用于纠偏或主动提供遗忘的信息)

### 6. 实现路线图建议 (Implementation Roadmap)

1.  **Phase 1: The Scribe (书记员)**
    *   启动 Context Process。
    *   实现单向通信：Main 发送所有 Logs 给 Context。
    *   Context 仅负责将 Log 聚合成一个干净的 `summary.md` 文件。

2.  **Phase 2: The Librarian (图书管理员)**
    *   Context Process 引入向量存储或关键词索引。
    *   Main Process 增加 `query_memo` 工具，允许 Agent 主动查阅历史。

3.  **Phase 3: The Advisor (顾问)**
    *   Context Process 具备分析能力，能够主动监测 Main 的行为模式。
    *   实现 `Interruption` 机制，允许 Context 在 Main 犯错前进行干预。

### 7. 总结 (Conclusion)

这个架构将 `nimbus` 从单核处理器升级为 **双核异构处理器**。
*   Core 负责 **算力 (Compute)**。
*   Context Process 负责 **显存 (VRAM)** 和 **总线 (Bus)**。

这不仅是 Memo 的增强，更是向 **自我反思 (Self-Reflecting) Agent** 迈出的关键一步。
