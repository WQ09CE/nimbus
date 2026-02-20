# Nimbus: The Next-Generation AgentOS Framework

> **Building Resilient, Long-Horizon AI Agents with Operating System Principles.**

Nimbus 是一款受冯·诺伊曼架构启发的 AI Agent 操作系统框架。它不仅仅是一个库，而是一个完整的运行时环境，旨在解决大语言模型（LLM）在复杂任务处理中的上下文漂移、状态管理失效以及长程对话稳定性等核心挑战。

---

## 🏗 核心架构 (Core Architecture)

Nimbus 将 LLM 视为中央处理器，并围绕其构建了完整的计算机系统抽象：

### 1. **vCPU (Virtual Central Processing Unit)**
- **执行循环**: 实现经典的 `Think-Act-Observe` 循环。
- **指令解码**: `InstructionDecoder` 将 LLM 的原始输出转化为可执行的系统指令（Action）。
- **抢占式调度**: 支持任务挂起、恢复及多 Agent 并发协作。

### 2. **MMU (Memory Management Unit)**
- **Anchor & Stream 机制**: 
    - **Anchor (锚点)**: 永久固定系统规则、核心目标和环境上下文，对抗 LLM 的近因偏见。
    - **Stream (执行流)**: 采用栈帧（StackFrame）管理动态历史，支持自动压缩与重要信息提取。
- **智能分页与换入换出**: 当上下文接近限制（默认 180k tokens）时，自动将不重要细节卸载至 NimFS，仅保留关键状态。

### 3. **NimFS (Nimbus File System)**
- **Artifact 交互**: 为 Agent 提供确定的工作空间，通过文件系统管理中间产物（Artifacts）。
- **持久化存储**: 跨会话的任务进度和知识沉淀。

### 4. **KernelGate (权限与安全)**
- **隔离环境**: 所有工具调用均通过 Gate 进行权限校验和环境隔离。
- **确定性状态更新**: 采用 `StateManager` 确保环境变化是可预测且原子化的。

---

## 🌟 特色机制 (Distinctive Features)

### ⚓ Anchor & Stream (状态锚定)
Nimbus 摒弃了传统的“纯滑动窗口”记忆模式。通过 **Anchor** 确保 LLM 永远不会忘记“我是谁”和“我要做什么”，而 **Stream** 则负责记录详细的执行过程。这种设计显著提升了 Agent 在数小时甚至数天长程任务中的稳定性。

### 📦 Artifact 导向的交互
Agent 不仅仅是在对话，而是在生成和修改 **Artifacts**（代码、文档、报告）。所有输出均版本化管理，支持回滚和跨 Agent 共享。

### 🤝 子 Agent 协作 (Sub-Agent Orchestration)
利用 **Stack Frames** 隔离机制，Nimbus 支持父 Agent 创建隔离的子运行环境。子 Agent 完成任务后，其过程会被“蒸馏”为关键结论返回给父 Agent，有效防止上下文污染。

---

## 🛠 核心工具集 (Core Toolset)

- **Execution**: 动态代码执行环境（Bash, Python）。
- **Search & Retrieval**: 集成向量数据库与传统检索。
- **System Control**: 对 Agent 运行时进行自我检查与配置更新。
- **NimFS Ops**: 高级文件操作与 Artifacts 管理。

---

## 🚀 快速开始

```bash
# 克隆仓库
git clone https://github.com/your-repo/nimbus.git
cd nimbus

# 安装依赖
pip install -e .

# 启动 AgentOS 交互式 CLI
python -m nimbus.cli
```

---

## 📈 未来路线图

- [ ] **分布式 vCPU**: 跨机器的 Agent 集群协作。
- [ ] **内核态自演化**: Agent 自动优化其工作流和工具集。
- [ ] **视觉 MMU**: 支持多模态上下文的统一管理。

---

*Nimbus — 赋予 LLM 真正的系统级执行能力。*
