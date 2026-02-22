# VCPU 架构重构方案：从 God Class 到模块化微内核

## 1. 现状分析与痛点 (Problem Statement)

目前 `src/nimbus/core/runtime/vcpu.py` 文件超过 1700 行，承载了过于沉重的职责，是一个典型的 "God Class"。其主要问题包括：

- **职责过度耦合**：同一个类中包含了状态管理（State）、循环控制（Pipeline）、指令解析（Decoder）、错误恢复（Recovery）和死循环检测（DoomLoop）。
- **可测试性差**：由于逻辑高度嵌套在 `step()` 和 `run()` 方法中，很难对单一组件（如指令解码逻辑）进行单元测试。
- **扩展困难**：引入新的执行模式（如并行工具调用）或新的观测逻辑需要修改核心循环代码，极易引入回归 Bug。
- **认知负荷高**：开发者难以快速定位特定逻辑（例如：死循环检测的阈值逻辑与 LLM 响应解析逻辑混杂在一起）。

## 2. 模块化拆分方案 (Architectural Decomposition)

我们将 VCPU 拆分为以下核心组件，遵循“高内聚、低耦合”原则：

### 2.1 核心组件定义
- **VCPUPipeline (执行流水线)**: 负责 T-A-O (Think-Act-Observe) 的主循环驱动，作为“交响乐指挥”。
- **InstructionDecoder (指令译码器)**: 将 LLM 的原始输出（Raw Response）解析为结构化的 `ActionIR`。
- **ActionExecutor (动作执行器)**: 封装对外部工具（Gate/Syscall）的调用逻辑，处理并发与序列化。
- **DoomLoopDetector (死循环检测器)**: 独立监控执行流，识别并拦截重复、无效的工具调用模式。
- **RecoveryHandler (容错恢复引擎)**: 专门处理工具超时、解析错误等异常，并生成修正指令（Correction Prompt）。
- **VCPUContext (运行上下文)**: 维护 VCPU 的状态（寄存器、计数器、MMU 引用），实现状态与逻辑的分离。

## 3. 目录结构设计 (New Directory Structure)

重构后的代码将分布在 `src/nimbus/core/runtime/vcpu/` 目录下：

```text
src/nimbus/core/runtime/vcpu/
├── __init__.py             # 暴露统一的 VCPU 入口
├── vcpu.py                 # 轻量化的 VCPU 门面类 (Facade)
├── pipeline.py             # Think-Act-Observe 状态机驱动
├── context.py              # VCPUState 与运行时上下文
├── decoder/
│   ├── __init__.py
│   ├── base.py             # 译码器接口定义
│   └── text_decoder.py     # 现有的正则/JSON 解析逻辑
├── executor/
│   ├── __init__.py
│   └── tool_executor.py    # 工具调用与 Syscall 映射
├── monitors/
│   ├── __init__.py
│   ├── doom_loop.py        # 死循环检测逻辑
│   └── telemetry.py        # 性能监控与 Tracer 适配
└── recovery/
    ├── __init__.py
    └── strategist.py       # 错误恢复策略 (Prompt 注入)
```

## 4. 交互协议定义 (Inter-Module Protocol)

模块间通过明确的 `Protocol` 或数据对象进行通信，减少直接依赖。

### 4.1 ActionIR (指令表示)
```python
class ActionIR(BaseModel):
    action_type: ActionType  # THOUGHT, CALL, RETURN, WAIT
    payload: Dict[str, Any]
    raw_output: str
```

### 4.2 核心接口抽象
```python
class IDecoder(Protocol):
    def decode(self, response: str, context: VCPUContext) -> List[ActionIR]: ...

class ILoopMonitor(Protocol):
    def check(self, action: ActionIR, context: VCPUContext) -> None: 
        """抛出 DoomLoopException 如果检测到异常循环"""

class IRecoveryEngine(Protocol):
    def handle_error(self, error: Exception, context: VCPUContext) -> RecoveryAction: ...
```

## 5. 分阶段实施计划 (Phased Implementation)

### 阶段 1：数据与状态抽离 (Data-Logic Separation)
- **目标**：将所有状态变量（`_step_count`, `_history` 等）移入 `VCPUContext`。
- **动作**：创建 `context.py`，保持 `vcpu.py` 逻辑不变，但所有状态访问改为 `self.ctx.xxx`。

### 阶段 2：组件化提取 (Component Extraction)
- **目标**：将 `InstructionDecoder` 和 `DoomLoopDetector` 提取为独立类。
- **动作**：编写单元测试覆盖这些提取出来的逻辑，确保解析逻辑在剥离后行为一致。

### 阶段 3：流水线重构 (Pipeline Refactoring)
- **目标**：重写 `step()` 方法，使其成为简单的组件调用链。
- **动作**：
  ```python
  def step(self):
      response = self.alu.think(self.ctx)
      actions = self.decoder.decode(response, self.ctx)
      for action in actions:
          self.monitor.check(action, self.ctx)
          result = self.executor.execute(action)
          self.mmu.observe(result)
  ```

### 阶段 4：增强恢复机制 (Recovery & Resilience)
- **目标**：将原本散落在各处的 `try-except` 统一由 `RecoveryHandler` 管理。
- **动作**：实现复杂的错误重试策略和模型自我修正引导。

## 6. 稳定性保障措施
1. **影子运行 (Shadow Testing)**：在重构初期，保持旧的 `vcpu.py` 逻辑，同时在后台运行新模块并对比输出 `ActionIR` 的一致性。
2. **集成测试集**：利用现有的 `tests/core/runtime/test_vcpu.py` 作为回归基准。
3. **逐一模块灰度**：先拆分检测器，观察一周无误后再拆分解码器。
