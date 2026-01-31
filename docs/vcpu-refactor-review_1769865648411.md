# AI Council Report: 请评审 Nimbus vCPU 模块的代码结构并给出重构建议。 ## 文件信息 - 文件：src/nimbus/core/runtime/vcpu.py - 行数：1,691 行 - 主类 VCPU：~1,400 行，28 个方法，15+ 实例变量 ## 当前职责（过多） 1. 核心执行循环 (Think-Act-Observe) 2. 7 种 Action 分发处理 (TOOL_CALL, SUB_CALL, RETURN, THOUGHT, POST_IPC, REQUEST_REPLAN, CANCEL) 3. 工具调用优化 (name repair, terminal hints, edit history) 4. Doom Loop 检测与恢复 5. 智能错误处理 (ErrorHandlerRegistry) 6. 上下文压缩 (Compaction) 7. 事件发射 8. LLM 失败处理与报告生成 ## 复杂方法 | 方法 | 行数 | 圈复杂度 | |------|------|---------| | step() | ~200 | 高 (~15) | | _handle_tool_call() | ~130 | 高 (~12) | | execute() | ~150 | 中 (~10) | | _handle_empty_result() | ~80 | 中 (~8) | ## 状态变量过多 (15+) _iteration, _consecutive_thoughts, _is_running, _is_done, _final_result, _compaction_count, _compaction_callback, _recent_tool_calls, _doom_loop_count, _consecutive_errors, _consecutive_empty_responses, _error_registry, _tool_failure_counts, _path_not_found_count ## 考虑的重构方向 方案 A: 拆分为多文件 vcpu/core.py, handlers.py, doom_loop.py, compaction.py, failure_reporter.py 方案 B: 策略模式 ActionHandler Protocol 方案 C: 最小改动，提取 DoomLoopDetector 和 FailureReporter 类 请给出：1. 推荐的重构方案 2. 如何保持向后兼容 3. 是否有遗漏的职责分离点 4. 优先级排序

1/31/2026, 9:20:48 PM

### Opinion from Claude Opus 4.5 (Deep Thinker)

现在我对代码有了全面了解，让我给出专业的评审意见和重构建议。

---

# Nimbus vCPU 模块代码评审报告

## 📊 现状分析

### 已有的良好设计
1. ✅ **ErrorHandlerRegistry** 已拆分到独立文件 - 这是正确方向
2. ✅ **MMU、Decoder、Gate** 都已解耦
3. ✅ 清晰的 Protocol 定义 (LLMClient, LLMResponse)
4. ✅ 配置类 VCPUConfig 独立

### 核心问题

| 问题 | 严重度 | 影响 |
|------|--------|------|
| 单一职责违反 | 🔴 高 | 28个方法，职责混杂，难以测试 |
| 状态爆炸 | 🔴 高 | 15+状态变量，状态转换难以追踪 |
| God Method | 🟡 中 | step()/execute() 200行，圈复杂度高 |
| 隐式耦合 | 🟡 中 | Doom Loop 和 Empty Response 检测逻辑交织 |

---

## 🎯 推荐方案：**渐进式策略模式 + 状态对象提取**

我推荐 **方案 B + C 的混合**，分三个阶段执行：

### Phase 1: 提取状态对象 (低风险，立即收益)

```python
# vcpu/state.py - 新文件
@dataclass
class ExecutionState:
    """vCPU 执行状态，解决状态爆炸问题"""
    iteration: int = 0
    consecutive_thoughts: int = 0
    consecutive_errors: int = 0
    consecutive_empty_responses: int = 0
    compaction_count: int = 0
    doom_loop_count: int = 0
    is_running: bool = False
    is_done: bool = False
    final_result: Optional[ToolResult] = None
    
    # 工具追踪
    recent_tool_calls: List[Tuple[str, str]] = field(default_factory=list)
    tool_failure_counts: Dict[str, int] = field(default_factory=dict)
    
    def reset(self):
        """重置为初始状态"""
        self.iteration = 0
        self.consecutive_thoughts = 0
        # ... 其他字段
    
    def on_tool_success(self, tool_name: str):
        """工具成功时更新状态"""
        self.consecutive_errors = 0
        self.tool_failure_counts[tool_name] = 0
    
    def on_tool_failure(self, tool_name: str) -> int:
        """工具失败时更新状态，返回失败次数"""
        self.tool_failure_counts[tool_name] = self.tool_failure_counts.get(tool_name, 0) + 1
        return self.tool_failure_counts[tool_name]
```

**收益**：状态变量从 15+ 降到 3 个（_state, _error_registry, _compaction_callback）

### Phase 2: 提取 DoomLoopDetector (中风险)

```python
# vcpu/doom_loop.py - 新文件
@dataclass
class DoomLoopDetector:
    """Doom Loop 检测器（学习自 opencode）"""
    threshold: int = 3
    _recent_calls: List[Tuple[str, str]] = field(default_factory=list)
    
    def record_call(self, tool_name: str, args: Dict) -> Optional[DoomLoopFault]:
        """记录调用，如果检测到 doom loop 返回 Fault"""
        args_json = json.dumps(args, sort_keys=True)
        current = (tool_name, args_json)
        self._recent_calls.append(current)
        
        # 保持窗口大小
        if len(self._recent_calls) > self.threshold:
            self._recent_calls = self._recent_calls[-self.threshold:]
        
        # 检测
        if len(self._recent_calls) == self.threshold:
            if all(call == current for call in self._recent_calls):
                self._recent_calls.clear()
                return DoomLoopFault(tool_name, args, self.threshold)
        
        return None
    
    def on_different_call(self):
        """不同调用时清除历史"""
        if len(self._recent_calls) > 1:
            self._recent_calls = self._recent_calls[-1:]
```

### Phase 3: ActionHandler Protocol (中等风险)

```python
# vcpu/handlers.py - 新文件
class ActionHandler(Protocol):
    """Action 处理器协议"""
    kind: str  # "TOOL_CALL", "RETURN", etc.
    
    async def handle(
        self, 
        action: ActionIR, 
        ctx: HandlerContext
    ) -> ToolResult: ...

@dataclass
class HandlerContext:
    """Handler 执行上下文"""
    gate: KernelGate
    mmu: MMU
    state: ExecutionState
    config: VCPUConfig
    emit_event: Callable[[str, Dict], None]
    doom_detector: DoomLoopDetector
    error_registry: ErrorHandlerRegistry

class ToolCallHandler(ActionHandler):
    kind = "TOOL_CALL"
    
    async def handle(self, action: ActionIR, ctx: HandlerContext) -> ToolResult:
        # 当前 _handle_tool_call 的逻辑移到这里
        ...

class ReturnHandler(ActionHandler):
    kind = "RETURN"
    
    async def handle(self, action: ActionIR, ctx: HandlerContext) -> ToolResult:
        # 当前 _handle_return 的逻辑
        ...
```

### Phase 4: 失败报告器提取 (低风险)

```python
# vcpu/failure_reporter.py - 新文件
class FailureReporter:
    """LLM 失败时生成友好报告"""
    
    def __init__(self, alu: LLMClient):
        self.alu = alu
    
    async def generate_report(
        self, 
        goal: str, 
        fault: Fault, 
        context: List[Dict]
    ) -> str:
        """让 LLM 生成自然语言失败报告"""
        # 当前 _generate_llm_failure_response 逻辑
        ...
    
    def get_doom_loop_guidance(self, tool_name: str) -> str:
        """获取 doom loop 的恢复指导"""
        # 当前 _get_doom_loop_guidance 逻辑
        ...
```

---

## 📁 推荐的目录结构

```
vcpu/
├── __init__.py          # 导出 VCPU, VCPUConfig
├── core.py              # VCPU 主类（~400行，只保留核心循环）
├── state.py             # ExecutionState 数据类
├── handlers/
│   ├── __init__.py      # 导出所有 handlers
│   ├── base.py          # ActionHandler Protocol, HandlerContext
│   ├── tool_call.py     # ToolCallHandler
│   ├── return_handler.py
│   ├── thought.py
│   └── sub_call.py
├── doom_loop.py         # DoomLoopDetector
├── failure_reporter.py  # FailureReporter
└── compaction.py        # CompactionManager (如果需要)
```

---

## 🔄 向后兼容策略

```python
# vcpu/__init__.py
from .core import VCPU, VCPUConfig, StepResult

# 保持原有导入路径兼容
__all__ = ["VCPU", "VCPUConfig", "StepResult"]

# 如果有人直接 import vcpu.py，提供 deprecation warning
import warnings
warnings.warn(
    "Importing from nimbus.core.runtime.vcpu directly is deprecated. "
    "Use nimbus.core.runtime.vcpu.VCPU instead.",
    DeprecationWarning
)
```

**关键**：保持 `VCPU` 的公开 API 不变：
- `__init__()` 参数不变
- `execute()` / `step()` 签名不变
- `VCPUConfig` 字段不变

---

## 🚨 遗漏的职责分离点

你的分析遗漏了这些可以进一步分离的点：

| 隐藏职责 | 当前位置 | 建议 |
|----------|----------|------|
| **Tool Name Repair** | `_handle_tool_call` 开头 | 提取到 `ToolNameResolver` |
| **Terminal Tool Hints** | `_handle_tool_call` 结尾 | 移入 Handler 的 post-process |
| **Goal Summarization** | `_prepare_goal_for_pinning` | 移到 MMU 或独立 `GoalManager` |
| **Context Dumping** | `_dump_context_to_file` | 移到调试模块或 MMU |
| **Empty Response Detection** | `step()` 中间 | 与 DoomLoop 合并为 `AnomalyDetector` |

---

## 📋 优先级排序

| 优先级 | 任务 | 理由 | 预估工时 |
|--------|------|------|----------|
| **P0** | 提取 `ExecutionState` | 立即降低复杂度，零风险 | 2h |
| **P0** | 提取 `DoomLoopDetector` | 逻辑独立，易测试 | 2h |
| **P1** | 提取 `FailureReporter` | 职责清晰，无依赖 | 1h |
| **P1** | 拆分 `_handle_tool_call` | 最复杂的方法 | 3h |
| **P2** | 实现 `ActionHandler` Protocol | 需要更多设计 | 4h |
| **P3** | 完整目录重构 | 等 P0-P2 稳定后 | 1d |

---

## 🧪 测试策略

重构时必须保持测试覆盖：

```python
# 建议的测试结构
tests/
└── core/runtime/vcpu/
    ├── test_state.py           # ExecutionState 单元测试
    ├── test_doom_loop.py       # DoomLoopDetector 单元测试
    ├── test_handlers.py        # 各 Handler 单元测试
    ├── test_vcpu_integration.py # 集成测试（保持现有）
    └── test_backward_compat.py  # 向后兼容性测试
```

---

## 💡 总结建议

1. **不推荐方案 A（纯文件拆分）**：仅仅拆分文件不解决根本问题，反而增加导入复杂度

2. **推荐 Phase 1 + Phase 2 作为 MVP**：
   - 提取 `ExecutionState` + `DoomLoopDetector`
   - 保持 VCPU 公开 API 不变
   - 立即获得可测试性提升

3. **Phase 3 作为后续迭代**：
   - ActionHandler Protocol 是更大的架构变更
   - 建议在 P0/P1 稳定后再做

**一句话**：先治标（状态提取），再治本（策略模式）。


### Opinion from GPT-5.2 (Generalist)

现在我对代码结构有了完整了解。让我给出专业的评审意见：

---

# vCPU 模块重构评审报告

## 📊 现状分析

经过代码审查，vcpu.py 确实存在 **上帝类（God Class）** 问题。核心指标：

| 指标 | 当前值 | 健康值 | 严重程度 |
|------|--------|--------|----------|
| 总行数 | 1,692 | <500 | 🔴 严重 |
| VCPU 类行数 | ~1,400 | <300 | 🔴 严重 |
| 实例变量 | 15+ | <8 | 🟡 警告 |
| 公共方法 | 28 | <10 | 🟡 警告 |
| step() 圈复杂度 | ~15 | <10 | 🔴 严重 |
| 职责数量 | 8 | 1-2 | 🔴 严重 |

## 🎯 职责识别（遗漏点补充）

您已识别的 8 项职责 + 我发现的遗漏：

| # | 职责 | 行数 | 独立性 |
|---|------|------|--------|
| 1 | Think-Act-Observe 核心循环 | ~350 | 核心 |
| 2 | 7 种 Action 分发处理 | ~250 | ✅ 可提取 |
| 3 | 工具名修复/终端提示 | ~80 | ✅ 可提取 |
| 4 | Doom Loop 检测 | ~100 | ✅ 可提取 |
| 5 | 智能错误恢复 | ~200 | ✅ 已提取 |
| 6 | 上下文压缩 (Compaction) | ~100 | ✅ 可提取 |
| 7 | 事件发射 | ~30 | ✅ 可提取 |
| 8 | LLM 失败报告生成 | ~150 | ✅ 可提取 |
| **9** | **Goal 预处理（摘要）** | ~50 | ✅ 遗漏 |
| **10** | **调试/诊断（context dump）** | ~50 | ✅ 遗漏 |

## 💡 推荐方案：**方案 C+ (增强版最小改动)**

### 为什么不选方案 A（多文件拆分）

- **风险高**：改动面太大，需要大量测试
- **过度设计**：当前项目规模不需要 5 个文件的复杂度
- **导入混乱**：增加循环依赖风险

### 为什么不选方案 B（策略模式）

- **过于抽象**：7 种 Action Handler 各自独特，共享逻辑少
- **性能开销**：Protocol dispatch 比 dict lookup 慢
- **未来收益不明确**：Action 类型不太可能频繁扩展

### 方案 C+ 详细设计

```
src/nimbus/core/runtime/
├── vcpu.py              # 核心循环（精简到 ~600 行）
├── decoder.py           # 已有
├── error_handler.py     # 已有 ✅
├── doom_loop.py         # 新增：Doom Loop 检测器
├── failure_reporter.py  # 新增：失败报告生成器
├── compaction.py        # 新增：压缩策略（从 vcpu 移出）
└── __init__.py          # 更新导出
```

### 提取优先级排序

| 优先级 | 组件 | 难度 | 收益 | 理由 |
|--------|------|------|------|------|
| **P0** | `DoomLoopDetector` | 低 | 高 | 状态隔离明确，无副作用 |
| **P1** | `FailureReporter` | 低 | 高 | 纯函数，易测试 |
| **P2** | `Compaction` 提取 | 中 | 中 | 与 MMU 交互，需谨慎 |
| **P3** | `ActionDispatcher` | 高 | 中 | 7 种 handler 各有特殊逻辑 |
| **P4** | 工具优化逻辑 | 低 | 低 | 稳定，暂不动 |

---

## 📝 实现建议

### 1. DoomLoopDetector（P0，立即做）

```python
# doom_loop.py
from dataclasses import dataclass, field
from typing import List, Tuple, Optional
import json

@dataclass
class DoomLoopResult:
    is_loop: bool
    consecutive_count: int
    guidance: Optional[str] = None

class DoomLoopDetector:
    """检测相同工具调用的无限循环"""
    
    def __init__(self, threshold: int = 3):
        self.threshold = threshold
        self._recent_calls: List[Tuple[str, str]] = []
    
    def check(self, tool_name: str, args: dict) -> DoomLoopResult:
        """检查是否进入 doom loop"""
        args_json = json.dumps(args, sort_keys=True)
        current = (tool_name, args_json)
        
        self._recent_calls.append(current)
        if len(self._recent_calls) > self.threshold:
            self._recent_calls = self._recent_calls[-self.threshold:]
        
        if len(self._recent_calls) == self.threshold:
            if all(call == current for call in self._recent_calls):
                self._recent_calls.clear()
                return DoomLoopResult(
                    is_loop=True,
                    consecutive_count=self.threshold,
                    guidance=self._get_guidance(tool_name)
                )
        
        return DoomLoopResult(is_loop=False, consecutive_count=0)
    
    def reset(self):
        self._recent_calls.clear()
    
    def _get_guidance(self, tool_name: str) -> str:
        # 移动现有的 _get_doom_loop_guidance 逻辑
        ...
```

**向后兼容**：
```python
# vcpu.py 中
from .doom_loop import DoomLoopDetector

class VCPU:
    def __init__(self, ...):
        ...
        self._doom_detector = DoomLoopDetector(threshold=3)
        
    async def _handle_tool_call(self, action: ActionIR) -> ToolResult:
        # 旧代码
        # result = self._check_doom_loop(action)
        
        # 新代码（委托）
        result = self._doom_detector.check(action.name, action.args)
        if result.is_loop:
            return self._create_doom_loop_error(result)
        ...
```

### 2. FailureReporter（P1）

```python
# failure_reporter.py
from dataclasses import dataclass
from typing import Optional, Protocol
import re

class LLMClient(Protocol):
    async def chat(self, messages: list, tools: None) -> object: ...

@dataclass
class FailureContext:
    goal: str
    fault_code: str
    fault_message: str
    iterations: int
    recent_errors: list[str]

class FailureReporter:
    """生成用户友好的失败报告"""
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client
    
    async def generate_report(self, ctx: FailureContext) -> str:
        """优先使用 LLM 生成自然语言报告，失败则使用模板"""
        if self.llm:
            try:
                return await self._llm_report(ctx)
            except Exception:
                pass
        return self._template_report(ctx)
    
    async def _llm_report(self, ctx: FailureContext) -> str:
        # 移动 _generate_llm_failure_response 逻辑
        ...
    
    def _template_report(self, ctx: FailureContext) -> str:
        # 移动 _generate_graceful_failure_report 逻辑
        ...
```

### 3. 重构后的 VCPU 骨架

```python
# vcpu.py (重构后约 600 行)
class VCPU:
    def __init__(self, ...):
        # 核心组件
        self.alu = alu
        self.decoder = decoder
        self.gate = gate
        self.mmu = mmu
        self.config = config or VCPUConfig()
        self.tools = tools or []
        
        # 提取的组件（组合优于继承）
        self._doom_detector = DoomLoopDetector()
        self._error_registry = ErrorHandlerRegistry()
        self._failure_reporter = FailureReporter(alu)
        
        # 最小状态（从 15+ 减少到 8）
        self._iteration = 0
        self._consecutive_thoughts = 0
        self._is_running = False
        self._is_done = False
        self._final_result = None
        self._compaction_count = 0
        self._consecutive_empty = 0
        self._compaction_callback = None
    
    async def execute(self, goal: str) -> ToolResult:
        """主循环保持清晰"""
        self._reset()
        self._is_running = True
        self._prepare_goal(goal)
        
        try:
            while not self._is_done:
                if self._should_compact():
                    await self._do_compaction()
                    continue
                
                step = await self.step()
                
                if step.fault:
                    if self._should_terminate(step.fault):
                        return await self._graceful_termination(goal, step.fault)
                    self._handle_retryable_fault(step.fault)
                
                if step.is_final:
                    return step.final_result
        finally:
            self._is_running = False
    
    async def step(self) -> StepResult:
        """单步执行：Think → Decode → Execute"""
        # 精简后的核心逻辑
        ...
```

---

## 🔒 向后兼容策略

### API 兼容

1. **保持 VCPU 公共接口不变**：
   - `execute(goal: str) -> ToolResult` ✅
   - `step() -> StepResult` ✅
   - `set_compaction_callback()` ✅
   - 属性: `iteration`, `is_running`, `is_done`, `get_state()` ✅

2. **废弃警告**（可选）：
   ```python
   @property
   def _doom_loop_count(self) -> int:
       warnings.warn("Use _doom_detector.count instead", DeprecationWarning)
       return self._doom_detector.count
   ```

### 测试兼容

1. 现有测试保持不变
2. 为新组件添加单元测试
3. 集成测试覆盖组合行为

---

## 📅 实施计划

| 阶段 | 内容 | 时间 | 风险 |
|------|------|------|------|
| Phase 1 | 提取 DoomLoopDetector | 2h | 低 |
| Phase 2 | 提取 FailureReporter | 2h | 低 |
| Phase 3 | 简化 VCPU 状态变量 | 1h | 低 |
| Phase 4 | 提取 Compaction 逻辑 | 3h | 中 |
| Phase 5 | ActionDispatcher（可选） | 4h | 高 |

**建议**：先完成 Phase 1-3，观察效果后再决定是否继续。

---

## 🎯 最终收益

| 指标 | 当前 | 重构后 | 改善 |
|------|------|--------|------|
| VCPU 行数 | ~1,400 | ~600 | -57% |
| 实例变量 | 15+ | 8 | -47% |
| step() 复杂度 | ~15 | ~8 | -47% |
| 单元测试覆盖 | 困难 | 容易 | ✅ |
| 可读性 | 低 | 高 | ✅ |

**核心原则**：宁可多组合几个小类，也不要一个大类包揽一切。


### Opinion from Gemini 3 Pro High (Reasoning)

基于深入的代码审查，我给出以下重构建议：

---

# Nimbus vCPU 代码重构评审报告

## 一、核心问题诊断

| 问题 | 严重度 | 影响 |
|------|--------|------|
| **上帝类** | 🔴 高 | VCPU 承担 8+ 职责，1,400 行代码 |
| **状态爆炸** | 🔴 高 | 15+ 实例变量，状态转换难以追踪 |
| **高圈复杂度** | 🟡 中 | step() CC=15, _handle_tool_call() CC=12 |
| **隐式依赖** | 🟡 中 | doom loop 检测、failure counting 散落各处 |

## 二、推荐方案：**渐进式重构（C → A 演进）**

### Phase 1（优先 - 1周内）：提取核心子系统

```
src/nimbus/core/runtime/
├── vcpu.py              # 精简后 ~500 行，仅保留核心循环
├── doom_loop.py         # 🆕 ~120 行
├── failure_reporter.py  # 🆕 ~150 行  
├── tool_optimizer.py    # 🆕 ~100 行（name repair, hints）
├── decoder.py           # 已存在
└── error_handler.py     # 已存在
```

**具体拆分：**

```python
# doom_loop.py
@dataclass
class DoomLoopDetector:
    """无限循环检测器 - 单一职责"""
    threshold: int = 3
    _recent_calls: List[Tuple[str, str]] = field(default_factory=list)
    
    def record_call(self, tool_name: str, args: Dict) -> Optional[DoomLoopError]:
        """记录调用，检测循环。返回 None 表示正常"""
        ...
    
    def reset(self) -> None: ...
    def get_guidance(self, tool_name: str) -> str: ...
```

```python
# failure_reporter.py
class FailureReporter:
    """失败报告生成器 - 独立可测试"""
    
    async def generate_llm_response(self, goal, fault, context) -> str: ...
    def generate_template_response(self, goal, fault) -> str: ...
```

```python
# tool_optimizer.py  
class ToolCallOptimizer:
    """工具调用优化器"""
    CANONICAL_NAMES: ClassVar[Dict[str, str]] = {...}
    TERMINAL_TOOLS: ClassVar[Set[str]] = {"Edit", "Write", "Bash"}
    
    def repair_tool_name(self, name: str) -> Tuple[str, bool]:
        """返回 (规范名, 是否修复)"""
        ...
    
    def should_inject_hint(self, tool_name: str, status: str) -> bool: ...
    def get_terminal_hint(self) -> str: ...
```

### Phase 2（2-3周）：重构状态管理

```python
# execution_state.py
@dataclass
class ExecutionState:
    """vCPU 执行状态 - 不可变快照"""
    iteration: int = 0
    consecutive_thoughts: int = 0
    is_running: bool = False
    is_done: bool = False
    compaction_count: int = 0
    
    def with_iteration(self, n: int) -> "ExecutionState":
        """返回新状态"""
        return replace(self, iteration=n)
```

### Phase 3（可选）：策略模式处理 Action

仅在需要**插件化 Action 类型**时采用：

```python
class ActionHandler(Protocol):
    async def handle(self, action: ActionIR, ctx: ExecutionContext) -> ToolResult: ...

# 注册机制
vcpu.register_handler("TOOL_CALL", ToolCallHandler())
vcpu.register_handler("SUB_CALL", SubCallHandler())
```

## 三、向后兼容策略

```python
# vcpu.py - 保持公开接口不变
class VCPU:
    def __init__(self, alu, decoder, gate, mmu, config=None, tools=None):
        # 内部组合新模块
        self._doom_detector = DoomLoopDetector(threshold=DOOM_LOOP_THRESHOLD)
        self._failure_reporter = FailureReporter(alu)
        self._tool_optimizer = ToolCallOptimizer()
        ...
    
    # 公开 API 保持不变
    async def execute(self, goal: str) -> ToolResult: ...
    async def step(self) -> StepResult: ...
    
    # 废弃但保留的属性（加警告）
    @property  
    def _recent_tool_calls(self) -> List[tuple]:
        warnings.warn("Use doom_detector instead", DeprecationWarning)
        return self._doom_detector._recent_calls
```

## 四、遗漏的职责分离点

1. **上下文组装逻辑** (`step()` 中 60+ 行日志/调试代码)
   → 提取为 `ContextLogger` 或用装饰器

2. **Compaction 策略** (`_do_compaction`, `_compact_mmu`, `_prepare_goal_for_pinning`)
   → 提取为 `CompactionStrategy` 类

3. **Tool Result 后处理** (hint 注入、memory 更新)
   → 可纳入 `ToolCallOptimizer.post_process()`

4. **事件发射** (`_emit_event` 遍布全类)
   → 考虑 AOP 或装饰器模式

## 五、优先级排序

| 优先级 | 重构项 | 收益 | 风险 | 工时 |
|--------|--------|------|------|------|
| **P0** | 提取 `DoomLoopDetector` | 🟢 高 | 🟢 低 | 2h |
| **P0** | 提取 `FailureReporter` | 🟢 高 | 🟢 低 | 3h |
| **P1** | 提取 `ToolCallOptimizer` | 🟢 高 | 🟢 低 | 2h |
| **P1** | `ExecutionState` 数据类 | 🟡 中 | 🟢 低 | 2h |
| **P2** | 提取 `CompactionStrategy` | 🟡 中 | 🟡 中 | 4h |
| **P3** | Action Handler 策略模式 | 🟡 中 | 🟡 中 | 8h |

## 六、重构后预期效果

| 指标 | 当前 | 目标 |
|------|------|------|
| VCPU 行数 | ~1,400 | ~500 |
| 方法数 | 28 | ~15 |
| 实例变量 | 15+ | 8 |
| step() 圈复杂度 | ~15 | ~8 |
| 单元测试覆盖 | ? | 可独立测试各子系统 |

## 七、总结建议

**不推荐方案 B（纯策略模式）** —— 7 种 Action 类型是固定的，策略模式增加复杂度但扩展价值有限。

**推荐渐进式演进**：
1. 先做 P0/P1（1 天可完成），立竿见影
2. 验证稳定后再做 P2
3. P3 仅在真正需要插件化时再考虑

这样既降低了风险，又能快速获得代码质量收益。
