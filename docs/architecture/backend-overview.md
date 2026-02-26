# Backend Overview: Streaming Architecture & Dispatching

## 1. 三通道流式输出架构 (Three-Channel Streaming)

Nimbus 后端支持三种主流 LLM 供应商的流式协议，通过统一的接口封装实现无缝切换：

- **Anthropic Native**: 支持 Claude 系列模型的 `thought` 块与 `tool_use` 块的分离流式解析。利用 Claude 的原生 XML 结构实现高效的工具调用捕获。
- **OpenAI Codex**: 针对 GPT-4o 等模型优化，支持 Function Calling 的增量式 delta 解析。
- **LiteLLM**: 作为中间层，支持集成 100+ 模型供应商（如 DeepSeek, Gemini, Mistral），将其流式响应统一转化为 Nimbus 内部的 `EventStream`。

## 2. ParallelDispatch 工作原理

`ParallelDispatch` 是 Nimbus 实现多代理协同的核心组件，负责调度 Specialist (如 Explorer, Implementer, Architect) 并处理其生命周期。

### 调度流程
1. **指令接收**: Orchestrator 发送子任务指令。
2. **并发启动**: `ParallelDispatch` 根据任务类型实例化对应的 Specialist 代理，并在独立的协程中运行。
3. **事件拦截**: 拦截 Specialist 的输出流，将其封装为 `sub_tool_events`。
4. **流式反馈**:
   - **Thought Stream**: 实时转发代理的思考过程。
   - **Tool Stream**: 实时转发代理调用的工具名及参数快照。
   - **Result Delivery**: 当代理调用 `SubmitResult` 时，捕获最终结果并结束该子任务槽位 (Slot)。

## 3. NimFS 存储与上下文优化

为了应对长对话和大规模文件操作导致的 Context Window 爆炸，引入了以下优化：

- **Offload Lazy Expansion (Phase 0)**: 
  - **机制**: 在将消息发送给 LLM 之前，自动扫描并展开 `nimfs://artifact/{id}` 引用。
  - **优势**: Agent 无需手动调用 `ReadArtifact` 即可获得完整上下文，减少了往返 (Round-trip) 次数。
- **MMU (Memory Management Unit) 截断策略**:
  - **三层控制**: vCPU (100K 字节安全网) -> MMU Offload (根据配置自动将大文本转为 Artifact) -> Context Optimizer (根据剩余 Token 预算进行视觉截断)。
  - **预览增强**: 自动生成的 Artifact 预览从 300 字符提升至 2000 字符，确保 Agent 在不读取全文的情况下也能理解文件大致内容。
