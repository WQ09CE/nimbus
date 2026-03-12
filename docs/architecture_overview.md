# Nimbus 架构概览与状态说明

## 1. 核心定位
Nimbus 是一个基于 LLM 的轻量级、高并发的 Agent 调度框架。它的核心设计理念是将复杂任务拆解，通过一个 Orchestrator（协调者）并行调度多个职责单一的 Sub-Agent（如 Reader/Worker）来协同完成，从而提高任务执行效率与稳定性。

## 2. 系统核心架构图

```mermaid
graph TD
    User([User Prompt]) --> Orchestrator
    
    subgraph Nimbus Framework
        Orchestrator[Orchestrator Agent]
        State[Scratchpad / State Manager]
        
        Orchestrator <-->|Read/Write State| State
        Orchestrator -->|Spawn| Reader[Reader Agent]
        Orchestrator -->|Spawn| Worker[Worker Agent]
        Orchestrator -->|Spawn| AgentN[... Agent]
        
        Reader <--> Toolchain
        Worker <--> Toolchain
    end
    
    subgraph Toolchain (Sandbox)
        Bash[Bash Command]
        FileOps[Read/Write/Edit/Grep]
        CustomTools[Custom API Tools]
    end
```

## 3. 核心模块说明

### 3.1 Orchestrator (调度与编排层)
- **职责**: 作为总控节点，负责理解用户意图、规划任务执行路径（更新 Scratchpad）、拆分独立子任务，并拉起对应角色的 Sub-Agent。
- **特点**: Orchestrator 被严格限制为**管理者**角色，原则上不直接调用修改系统的工具（如 Bash/Edit），仅通过轻量级工具（如 Read/Grep）验证结果。

### 3.2 Sub-Agents (工作/分析子节点)
- **职责**: 接收到明确的单一 Goal 后，在一个相对隔离的上下文中独立执行。
- **角色划分**:
  - `Reader`: 只读型，负责长文本检索、代码分析、日志诊断。仅提供 Read/Grep 等工具。
  - `Worker`: 写入/执行型，负责修改代码、执行脚本、操作文件。提供 Bash/Write/Edit 等工具。
- **规则**: 扁平化无嵌套（子 Agent 不能继续拉起孙 Agent），执行完毕后通过自身的 Scratchpad 或结果集向 Orchestrator 汇报。

### 3.3 Toolchain (工具链层)
- **职责**: Agent 与操作系统/业务环境交互的唯一桥梁。
- **现状能力**: 提供标准的 Bash、Read、Write、Edit、Grep 接口。
- **防爆机制**: 内置强大的截断保护（如 Bash 输出限制最后 2000 行/50KB），防止长输出冲爆 LLM 的上下文窗口。

## 4. 运行控制流 (Execution Flow)
1. **意图承接**: Orchestrator 接收用户指令。
2. **状态规划**: 在专属 Scratchpad 中写下 TODO 列表与当前执行状态。
3. **任务派发**: 发现存在可并行的耗时任务，调用 `spawn_agent` 接口同时拉起多个 Reader/Worker。
4. **工具执行**: 隔离的 Sub-Agent 启动，利用分配的 Toolchain 闭环执行，并将结果/日志落地。
5. **状态回收**: Orchestrator 汇总结果，如果有 Agent 超时，通过 `Read` 其日志进行状态恢复。
6. **最终验证**: Orchestrator 进行最终的整体结果校验（跑测试/查代码），任务结束。
