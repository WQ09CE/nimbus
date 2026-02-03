# VCPU 重构提案

## 现状分析

### 代码规模
- **文件**: `src/nimbus/core/runtime/vcpu.py`
- **行数**: 1759 行
- **方法数**: 40+ 个

### 职责分析

VCPU 当前承担了 **7 个不同的职责**：

```
┌─────────────────────────────────────────────────────────────────┐
│                         VCPU (1759 lines)                       │
├─────────────────────────────────────────────────────────────────┤
│ ✅ 核心执行 (应该有)        │ ❌ 错误恢复 (应该分离)           │
│   - execute()               │   - _handle_tool_error()         │
│   - step()                  │   - _execute_recovery()          │
│   - _reset()                │   - _handle_empty_result()       │
│                             │   - _generate_failure_report()   │
├─────────────────────────────┼──────────────────────────────────┤
│ ⚠️ Action Handlers (可分离) │ ❌ Compaction (应该在 AgentOS)   │
│   - _handle_tool_call()     │   - _do_compaction()             │
│   - _handle_return()        │   - _compact_mmu()               │
│   - _handle_cancel()        │   - set_compaction_callback()    │
│   - _handle_post_ipc()      │                                  │
├─────────────────────────────┼──────────────────────────────────┤
│ ❌ Checkpoint (应该分离)    │ ⚠️ 状态属性 (12+ properties)     │
│   - create_checkpoint()     │   - _iteration, _is_running...   │
│   - restore_from_checkpoint │   - 应该提取到 VCPUState        │
├─────────────────────────────┼──────────────────────────────────┤
│ ⚠️ Goal 处理                │ ⚠️ 调试                          │
│   - _prepare_goal_for_pin() │   - _dump_context_to_file()     │
└─────────────────────────────┴──────────────────────────────────┘
```

### 方法分类统计

| 类别 | 方法数 | 应该保留 |
|------|--------|----------|
| 核心执行 | 3 | ✅ 是 |
| Action Handlers | 7 | ⚠️ 可分离 |
| 错误恢复 | 5 | ❌ 应分离 |
| Compaction | 3 | ❌ 应移到 AgentOS |
| Checkpoint | 2 | ❌ 应分离 |
| 状态属性 | 12+ | ⚠️ 应提取 |
| 事件/调试 | 2 | ⚠️ 可分离 |
| 其他 | 4 | ⚠️ 视情况 |

## 问题

### 1. 违反单一职责原则 (SRP)
VCPU 应该只负责 **Think-Act-Observe** 循环，但它还处理：
- 错误恢复策略
- 内存压缩
- 状态持久化
- 调试输出

### 2. 与 ErrorHandlerRegistry 职责重叠
已有 `ErrorHandlerRegistry` 来管理错误恢复，但 VCPU 内部还有：
- `_handle_tool_error()` 
- `_execute_recovery()`
- `_generate_llm_failure_response()`

### 3. Compaction 逻辑分散
- `AgentOS._compaction_for_process()` - 调用 MMU 归档
- `VCPU._do_compaction()` - 执行压缩
- `VCPU._compact_mmu()` - MMU 压缩
- `VCPU.set_compaction_callback()` - 设置回调

### 4. 状态管理混乱
12+ 个 property 方法，大部分是代理 `VCPUState` 的访问：
```python
@property
def _iteration(self) -> int:
    return self._state.iteration

@_iteration.setter  
def _iteration(self, value: int) -> None:
    self._state.iteration = value
```

## 重构方案

### 目标架构

```
┌──────────────────────────────────────────────────────────────┐
│                        AgentOS                                │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                  CompactionManager                       │ │
│  │  - trigger_compaction()                                  │ │
│  │  - archive_and_reset()                                   │ │
│  └─────────────────────────────────────────────────────────┘ │
│                            │                                  │
│  ┌─────────────────────────▼─────────────────────────────┐   │
│  │                      VCPU (~400 lines)                 │   │
│  │  - execute(goal) → ToolResult                          │   │
│  │  - step() → StepResult                                 │   │
│  │  - inject_message()                                    │   │
│  │  - request_pause()                                     │   │
│  └───────┬────────────────┬──────────────────┬───────────┘   │
│          │                │                  │                │
│    ┌─────▼─────┐   ┌──────▼──────┐   ┌──────▼──────┐        │
│    │ Decoder   │   │ Dispatcher  │   │ VCPUState   │        │
│    │           │   │             │   │             │        │
│    │ decode()  │   │ dispatch()  │   │ iteration   │        │
│    │           │   │ handle_*()  │   │ is_running  │        │
│    └───────────┘   └──────┬──────┘   │ is_done     │        │
│                           │          └─────────────┘        │
│                    ┌──────▼──────┐                          │
│                    │ErrorHandler │                          │
│                    │  Registry   │                          │
│                    │             │                          │
│                    │ handle()    │                          │
│                    │ recover()   │                          │
│                    └─────────────┘                          │
└──────────────────────────────────────────────────────────────┘
```

### 分离计划

#### Phase 1: 提取 ActionDispatcher
```python
class ActionDispatcher:
    """Dispatch actions to appropriate handlers."""
    
    def __init__(self, gate: Gate, error_registry: ErrorHandlerRegistry):
        self.gate = gate
        self.error_registry = error_registry
    
    async def dispatch(self, action: ActionIR) -> ToolResult:
        """Route action to handler."""
        match action.kind:
            case "TOOL_CALL": return await self._handle_tool_call(action)
            case "RETURN": return await self._handle_return(action)
            case "CANCEL": return await self._handle_cancel(action)
            ...
    
    async def _handle_tool_call(self, action: ActionIR) -> ToolResult:
        """Execute tool with error recovery."""
        result = await self.gate.syscall_tool(action.name, action.args)
        if result.fault:
            return await self.error_registry.handle(result.fault, action)
        return result
```

#### Phase 2: 移除 Compaction 逻辑
- 删除 `VCPU._do_compaction()`, `_compact_mmu()`, `set_compaction_callback()`
- `AgentOS` 直接处理 `CONTEXT_OVERFLOW` fault
- 已在本次 PR 中部分完成

#### Phase 3: 提取 CheckpointManager
```python
class CheckpointManager:
    """Manage VCPU state checkpoints."""
    
    def create(self, vcpu: VCPU) -> SessionCheckpointModel:
        ...
    
    def restore(self, vcpu: VCPU, checkpoint: SessionCheckpointModel) -> None:
        ...
```

#### Phase 4: 简化状态管理
- 直接使用 `self.state.iteration` 而不是 property 代理
- 或者让 `VCPUState` 成为 VCPU 的公开属性

### 预期结果

| 组件 | 当前行数 | 重构后 |
|------|---------|--------|
| VCPU | 1759 | ~400 |
| ActionDispatcher | - | ~300 |
| CheckpointManager | - | ~100 |
| ErrorHandlerRegistry | 628 | ~700 (合并错误恢复) |
| CompactionManager | - | ~150 (在 AgentOS) |

### 理想的 VCPU

```python
class VCPU:
    """
    Virtual CPU - Pure Think-Act-Observe loop executor.
    
    Responsibilities:
    1. Execute Think-Act-Observe cycle
    2. Manage iteration state
    3. Delegate action dispatch
    4. Signal events
    
    NOT responsible for:
    - Error recovery (→ ErrorHandlerRegistry)
    - Memory compaction (→ AgentOS)  
    - Checkpointing (→ CheckpointManager)
    """
    
    def __init__(
        self,
        alu: LLMClient,           # Think
        decoder: InstructionDecoder,  # Parse
        dispatcher: ActionDispatcher, # Act
        mmu: MMU,                 # Memory
        config: VCPUConfig,
    ):
        self.alu = alu
        self.decoder = decoder
        self.dispatcher = dispatcher
        self.mmu = mmu
        self.config = config
        self.state = VCPUState()
    
    async def execute(self, goal: str) -> ToolResult:
        """Main execution loop."""
        self.state.reset()
        self.mmu.add_user_message(goal)
        
        while self.state.is_running and not self.state.is_done:
            result = await self.step()
            if result.is_final:
                return result.final_result
        
        return ToolResult(status="OK")
    
    async def step(self) -> StepResult:
        """Single Think-Act-Observe cycle."""
        self.state.iteration += 1
        
        # 1. THINK
        messages = self.mmu.assemble_context()
        response = await self.alu.chat(messages, tools=self.tools)
        
        # 2. DECODE
        actions = self.decoder.decode(response)
        
        # 3. ACT
        results = []
        for action in actions:
            result = await self.dispatcher.dispatch(action)
            results.append(result)
            
            # 4. OBSERVE
            self.mmu.record(action, result)
        
        return StepResult(actions=actions, results=results)
```

## 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 接口变化 | 中 | 保持向后兼容的 facade |
| 测试覆盖 | 中 | 先补充单元测试 |
| 性能影响 | 低 | 只是代码重组，无算法变化 |
| 时间成本 | 中 | 分阶段实施 |

## 实施优先级

1. **P0**: 移除 Compaction 逻辑（已完成）
2. **P1**: 提取 ActionDispatcher
3. **P2**: 简化状态属性
4. **P3**: 提取 CheckpointManager

## 参考

- 现有 ErrorHandlerRegistry: `src/nimbus/core/runtime/error_handler.py`
- VCPUState: `src/nimbus/core/runtime/execution_state.py`
- InstructionDecoder: `src/nimbus/core/runtime/decoder.py`
