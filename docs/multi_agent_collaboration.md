# 多 Agent 协作与并发机制

## 1. 核心理念与挑战

在处理复杂的代码重构、系统诊断或长链条的数据处理任务时，单个大语言模型 Agent 容易受到上下文长度限制和注意力机制的制约，导致指令遗忘、幻觉甚至重复循环。

Nimbus 通过引入严格分层的 **Orchestrator-Worker** 多 Agent 并发模式来解决这一问题。其核心理念是：**解耦与隔离**。即，通过并行派发多个独立状态的 Sub-Agent，限制它们的关注点（Goal）和能力（Tools），从而在控制整体 Token 消耗的同时提升系统稳定性和效率。

## 2. Orchestrator 的调度策略

Orchestrator 是整个框架的大脑，它不负责具体的编码或冗长日志的审查，它的行为规范如下：
1. **统一管理入口**：响应用户初始请求，维护全局 Scratchpad (任务状态草稿本)。
2. **职责分配**：根据 Scratchpad 中的 TODO 列表，主动拆解任务。
3. **并发派发 (Parallel Spawning)**：
   - 发现相互独立的分析/修改任务时，Orchestrator 会在一个 function call block 中同时触发多个 `spawn_agent`。
   - 每个子 Agent 必须被赋予**高度具体的 Goal**、**关联的 File Paths** 以及**期望输出格式**。
4. **结果聚合与验证**：子任务结束（或超时/失败）后，Orchestrator 负责聚合结果（通常通过读取子 Agent 的 Scratchpad 或结果集），并亲自执行简单的校验动作（如单次 Read 或跑个测试脚本）。

## 3. Sub-Agent 的生命周期与隔离机制

### 3.1 角色的强约束 (Roles & Strict Capabilities)
Nimbus 预设了不同能力的 Sub-Agent 角色：
- **Reader Agent**: 只能获取系统只读权限（Read/Grep 工具）。适用于代码扫描、错误日志排查、代码上下文分析。
- **Worker Agent**: 拥有系统写入和执行权限（Write/Edit/Bash 工具）。适用于精确的代码修改、测试脚本的编写和执行。

**规则约束 (No Nesting)**: 
为了防止多层递归调用带来的状态失控与无限循环，Nimbus 严格规定：**Sub-Agent 绝对不允许嵌套调用**。它们在各自孤立的上下文沙箱中运行，只对分配到的 Goal 负责。

### 3.2 异常处理与超时恢复 (Timeout & Recovery)

多路并发下，部分长耗时任务不可避免地会出现超时或执行失败。Nimbus 拥有一套韧性极强的恢复机制：
1. **任务监控**：Orchestrator 监控所有 spawn_agent 调用的状态。
2. **日志留存 (Scratchpad Trails)**：即便 Sub-Agent 超时或因为异常被中止，其执行过程中的思考记录、错误信息与临时状态都会留存在该 Sub-Agent 专属的 Scratchpad 文件中。
3. **状态恢复 (State Recovery)**：Orchestrator 检测到超时错误后，不会直接报废该任务，而是调用 `Read` 工具去阅读对应 Sub-Agent 的 Scratchpad，提取已完成的进度和失败的原因，调整策略后决定是否重新分配或终止该分支任务。
