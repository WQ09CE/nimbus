# VCPU 重构最终决议

**评审委员会**: Claude Opus 4.5 / GPT-5.2 / Gemini 3 Pro High  
**主席**: Claude Sonnet  
**批准日期**: 2026-02-03  
**状态**: ✅ 已批准

---

## 一、决议摘要

| 项目 | 决议 |
|------|------|
| 原提案 | `docs/vcpu-refactor-proposal.md` |
| 评审结果 | ✅ 批准，需按本决议修订执行 |
| 目标代码量 | VCPU 从 1759 行减少到 ~500 行 |
| 核心改动 | 引入 RecoveryExecutor，简化状态属性 |

---

## 二、修订后的实施计划

### 2.1 优先级（最终版）

| 阶段 | 任务 | 工时 | 风险 | 前置条件 |
|------|------|------|------|----------|
| **P0** | ~~移除 Compaction 逻辑~~ | - | - | ✅ 已完成 |
| **P0.5** | 补充 VCPU 错误处理测试 | 3h | 🟢 低 | 无 |
| **P1** | 简化状态属性 | 2h | 🟢 低 | P0.5 |
| **P2** | 引入 RecoveryExecutor | 4h | 🟡 中 | P1 |
| **P3** | 引入 ActionContext 解耦 | 2h | 🟢 低 | P2 |
| **P4** | 提取 CheckpointManager | 2h | 🟢 低 | 可选 |
| **P5** | 提取 ToolDispatcher | 3h | 🟡 中 | 可选，视 P2/P3 效果 |

### 2.2 与原提案的差异

| 方面 | 原提案 | 最终决议 | 理由 |
|------|--------|----------|------|
| ActionDispatcher | P1 必做 | P5 可选 | 三位专家指出状态耦合问题，收益有限 |
| 错误处理整合 | 合并到 Registry | 新增 RecoveryExecutor | 保持 Registry 无状态，分离策略/执行 |
| 状态属性 | P2 | **P1** | 零风险高收益，三位专家共识 |
| 目标行数 | ~400 | **~500** | 更务实的估计 |
| 测试 | 未提及 | **P0.5 必做** | 重构前必须有测试覆盖 |

---

## 三、目标架构

```
┌──────────────────────────────────────────────────────────────────┐
│                          AgentOS                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ CompactionManager (已完成)                                │    │
│  └──────────────────────────────────────────────────────────┘    │
│                              │                                    │
│  ┌───────────────────────────▼────────────────────────────────┐  │
│  │                    VCPU (~500 lines)                        │  │
│  │                                                             │  │
│  │  核心职责:                                                  │  │
│  │  1. Think-Act-Observe 循环 (execute, step)                 │  │
│  │  2. 状态管理 (state 公开属性)                               │  │
│  │  3. 事件发送 (_emit_event)                                  │  │
│  │                                                             │  │
│  │  保留的 handlers:                                           │  │
│  │  - _handle_return (修改 VCPU 状态)                          │  │
│  │  - _handle_cancel (修改 VCPU 状态)                          │  │
│  │                                                             │  │
│  │  委托的逻辑:                                                 │  │
│  │  - 工具执行 → Gate                                          │  │
│  │  - 错误恢复 → RecoveryExecutor                              │  │
│  └─────────────────────────────┬──────────────────────────────┘  │
│                                │                                  │
│  ┌─────────────────────────────▼──────────────────────────────┐  │
│  │              RecoveryExecutor (新增 ~150 lines)             │  │
│  │                                                             │  │
│  │  职责: 连接策略层与执行层                                    │  │
│  │  - 持有 ErrorHandlerRegistry (获取恢复策略)                 │  │
│  │  - 持有 Gate (执行 auto_tool)                               │  │
│  │  - 管理重试逻辑                                             │  │
│  │  - 返回结构化的 RecoveryResult                              │  │
│  └─────────────────────────────┬──────────────────────────────┘  │
│                                │                                  │
│  ┌─────────────────────────────▼──────────────────────────────┐  │
│  │         ErrorHandlerRegistry (保持不变 ~628 lines)          │  │
│  │                                                             │  │
│  │  职责: 纯策略层（无状态）                                    │  │
│  │  - classify_error() → ToolErrorCode                        │  │
│  │  - handle_error() → RecoveryAction                         │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 四、各阶段详细设计

### P0.5: 补充测试

**目标文件**: `tests/core/test_vcpu_error_handling.py`

**需覆盖的场景**:
```python
class TestVCPUErrorHandling:
    # 基础错误处理
    async def test_tool_error_triggers_recovery()
    async def test_file_not_found_auto_lists_directory()
    async def test_edit_string_not_found_reads_file()
    
    # 渐进式恢复
    async def test_first_failure_tries_auto_tool()
    async def test_second_failure_injects_hint()
    async def test_third_failure_skips_recovery()
    
    # 边界条件
    async def test_max_consecutive_errors_terminates()
    async def test_doom_loop_detection()
    async def test_empty_result_handling()
```

### P1: 简化状态属性

**改动**:
```python
# Before: 12+ property 代理
class VCPU:
    @property
    def _iteration(self) -> int:
        return self._state.iteration
    
    @_iteration.setter
    def _iteration(self, value: int) -> None:
        self._state.iteration = value
    # ... 12+ more properties

# After: 公开 state，删除所有代理
class VCPU:
    def __init__(self, ...):
        self.state = ExecutionState()  # 公开属性
    
    async def step(self):
        self.state.iteration += 1  # 直接访问
```

**预计删除**: ~100 行

### P2: 引入 RecoveryExecutor

**新文件**: `src/nimbus/core/runtime/recovery_executor.py`

```python
from dataclasses import dataclass
from typing import Any, Dict, Optional

from nimbus.core.protocol import ActionIR, Fault, ToolResult
from nimbus.core.runtime.error_handler import ErrorHandlerRegistry, RecoveryAction
from nimbus.os.gate import KernelGate


@dataclass
class RecoveryResult:
    """恢复执行结果"""
    success: bool
    result: Optional[ToolResult] = None
    should_abort: bool = False
    inject_message: Optional[str] = None
    modified_args: Optional[Dict[str, Any]] = None


class RecoveryExecutor:
    """
    错误恢复执行器
    
    连接策略层 (ErrorHandlerRegistry) 与执行层 (Gate)。
    VCPU 委托错误恢复逻辑到此类。
    
    职责:
    1. 调用 Registry 获取恢复策略
    2. 执行 auto_tool 等恢复动作
    3. 返回结构化结果供 VCPU 处理
    """
    
    def __init__(
        self,
        registry: ErrorHandlerRegistry,
        gate: KernelGate,
        workspace: str,
    ):
        self.registry = registry
        self.gate = gate
        self.workspace = workspace
    
    async def try_recover(
        self,
        action: ActionIR,
        fault: Fault,
        attempt: int,
    ) -> RecoveryResult:
        """
        尝试从工具错误中恢复
        
        Args:
            action: 失败的 action
            fault: 错误信息
            attempt: 第几次尝试 (1-based)
        
        Returns:
            RecoveryResult 包含恢复结果和后续指令
        """
        # 1. 获取恢复策略
        recovery = await self.registry.handle_error(
            fault_message=str(fault.message) if fault else "Unknown error",
            tool_name=action.name,
            args=action.args or {},
            workspace=self.workspace,
        )
        
        if recovery is None or recovery.action_type == "skip":
            return RecoveryResult(success=False)
        
        # 2. 执行恢复动作
        return await self._execute(recovery, attempt)
    
    async def _execute(
        self,
        recovery: RecoveryAction,
        attempt: int,
    ) -> RecoveryResult:
        """执行具体的恢复动作"""
        
        match recovery.action_type:
            case "auto_tool":
                # 自动执行恢复工具 (如 ls, Read)
                result = await self.gate.syscall_tool(
                    recovery.auto_tool,
                    recovery.auto_args or {},
                )
                return RecoveryResult(
                    success=True,
                    result=result,
                    inject_message=recovery.hint,
                )
            
            case "inject_hint":
                # 注入提示消息
                return RecoveryResult(
                    success=True,
                    inject_message=recovery.hint,
                )
            
            case "modify_args":
                # 返回修改后的参数，让 VCPU 重试
                return RecoveryResult(
                    success=True,
                    modified_args=recovery.modified_args,
                )
            
            case _:
                return RecoveryResult(success=False)
```

**VCPU 中的改动**:
```python
# Before: _handle_tool_error() 150+ 行复杂逻辑

# After: 简化为委托调用
async def _handle_tool_error(
    self,
    action: ActionIR,
    result: ToolResult,
) -> ToolResult:
    attempt = self.state.on_tool_failure(action.name)
    
    # 检查是否应该终止
    if self.state.is_tool_failing_too_much(action.name):
        return self._generate_graceful_failure(result)
    
    # 委托给 RecoveryExecutor
    recovery = await self.recovery_executor.try_recover(
        action, result.fault, attempt
    )
    
    if not recovery.success:
        return result
    
    # 处理恢复结果
    if recovery.inject_message:
        self.mmu.add_system_message(recovery.inject_message)
    
    if recovery.result:
        self.mmu.add_tool_result(action, recovery.result)
        return recovery.result
    
    return result
```

**预计删除**: ~100 行（从 VCPU）
**预计新增**: ~150 行（RecoveryExecutor）

### P3: ActionContext 解耦

```python
@dataclass
class ActionContext:
    """Action 执行上下文，解耦 handler 对 VCPU 的直接依赖"""
    gate: KernelGate
    mmu: MMU
    recovery_executor: RecoveryExecutor
    workspace: str
    emit_event: Callable[[str, Dict], None]
```

**用途**: 为未来可能的 ToolDispatcher 提取做准备，同时提高可测试性。

---

## 五、验收标准

### 代码指标

| 指标 | 当前 | 目标 | 验收方式 |
|------|------|------|----------|
| VCPU 行数 | 1759 | ≤550 | `wc -l vcpu.py` |
| Property 代理数 | 12+ | 0 | grep 统计 |
| `_handle_tool_error` 行数 | ~150 | ≤50 | 代码审查 |

### 测试指标

| 指标 | 目标 | 验收方式 |
|------|------|----------|
| 错误处理测试覆盖 | ≥80% | pytest --cov |
| 现有 e2e 测试 | 全部通过 | CI |
| 新增单元测试 | ≥10 个 | pytest 统计 |

### 功能验收

- [ ] 无限上下文测试通过 (`tests/e2e_infinite_context.py`)
- [ ] 错误恢复行为不变（ls 自动执行、Edit 自动读取等）
- [ ] Doom loop 检测正常工作
- [ ] 中断/取消功能正常

---

## 六、风险与缓解

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| 重构引入 bug | 🟡 中 | P0.5 先补充测试，每阶段运行全量测试 |
| 接口变化影响 AgentOS | 🟢 低 | VCPU 公开方法签名保持不变 |
| RecoveryExecutor 与 Gate 交互问题 | 🟢 低 | 复用现有 Gate 接口 |

---

## 七、时间表

| 周 | 任务 | 交付物 |
|----|------|--------|
| W1 | P0.5 补充测试 | `tests/core/test_vcpu_error_handling.py` |
| W1 | P1 状态简化 | VCPU 删除 property 代理 |
| W2 | P2 RecoveryExecutor | 新文件 + VCPU 集成 |
| W2 | P3 ActionContext | 解耦完成 |
| W3 | 验收测试 | 全部测试通过 |

---

## 八、审批

| 角色 | 签署 | 日期 |
|------|------|------|
| 评审主席 | Claude Sonnet | 2026-02-03 |
| 项目负责人 | (待签署) | |

---

## 附录

### A. 相关文档
- 原提案: `docs/vcpu-refactor-proposal.md`
- 现有错误处理: `src/nimbus/core/runtime/error_handler.py`
- 执行状态: `src/nimbus/core/runtime/execution_state.py`

### B. 参考代码
- VCPU: `src/nimbus/core/runtime/vcpu.py`
- Gate: `src/nimbus/os/gate.py`
- MMU: `src/nimbus/core/memory/mmu.py`
