# vCPU 代码评审请求

## 📊 文件概览

| 指标 | 数值 |
|------|------|
| **文件** | `src/nimbus/core/runtime/vcpu.py` |
| **行数** | 1,691 行 |
| **类数量** | 4 个 |
| **方法数量** | 28 个 |

---

## 🏗️ 当前结构

### 类和协议

```python
class LLMResponse(Protocol)      # LLM 响应接口
class LLMClient(Protocol)        # LLM 客户端接口
class VCPUConfig                 # 配置数据类
class StepResult                 # 步骤结果数据类
class VCPU                       # 主类 (1400+ 行)
```

### VCPU 类方法分布

| 分类 | 方法 | 行数 | 职责 |
|------|------|------|------|
| **核心循环** | `execute()` | ~150 | 主执行循环 |
| | `step()` | ~200 | 单步执行 |
| | `_execute_action()` | ~50 | Action 分发 |
| **Action Handlers** | `_handle_tool_call()` | ~130 | 工具调用处理 |
| | `_handle_sub_call()` | ~40 | 子进程调用 |
| | `_handle_return()` | ~30 | 返回处理 |
| | `_handle_thought()` | ~50 | 思考处理 |
| | `_handle_post_ipc()` | ~15 | IPC 消息 |
| | `_handle_request_replan()` | ~15 | 重新规划 |
| | `_handle_cancel()` | ~25 | 取消处理 |
| **错误处理** | `_handle_tool_error()` | ~45 | 工具错误恢复 |
| | `_execute_recovery()` | ~100 | 执行恢复动作 |
| | `_handle_empty_result()` | ~80 | 空结果处理 |
| | `_get_doom_loop_guidance()` | ~60 | Doom Loop 指引 |
| | `_generate_graceful_failure_report()` | ~70 | 失败报告生成 |
| | `_generate_llm_failure_response()` | ~50 | LLM 失败响应 |
| **Compaction** | `set_compaction_callback()` | ~15 | 设置回调 |
| | `_do_compaction()` | ~40 | 执行压缩 |
| | `_compact_mmu()` | ~60 | MMU 压缩 |
| **辅助方法** | `_reset()` | ~15 | 状态重置 |
| | `_prepare_goal_for_pinning()` | ~60 | 目标准备 |
| | `_emit_event()` | ~15 | 事件发射 |
| | `_dump_context_to_file()` | ~35 | 调试转储 |
| **属性** | `iteration`, `is_running`, `is_done`, `get_state` | ~25 | 状态访问 |

---

## 🔍 职责分析

### 当前 VCPU 承担的职责

1. **核心执行循环** - Think-Act-Observe
2. **Action 分发** - 7 种 ActionKind 的路由
3. **工具调用优化**
   - Tool name 自动修复 (read → Read)
   - Terminal tool 提示注入
   - Edit 历史跟踪 (防重复编辑)
4. **Doom Loop 检测与恢复**
5. **智能错误处理** (ErrorHandlerRegistry)
6. **上下文压缩** (Compaction)
7. **事件发射**
8. **LLM 失败处理**
9. **调试支持** (context dump)

### 常量定义

```python
TERMINAL_TOOLS = {"Edit", "Write", "Bash"}
DOOM_LOOP_THRESHOLD = 3
TOOL_NAME_CANONICAL = {...}  # 12 个映射
```

---

## ⚠️ 潜在问题

### 1. 单一职责违反
- VCPU 同时处理执行循环、错误恢复、压缩、事件等
- 方法之间耦合度高

### 2. 方法过长
- `step()` ~200 行，包含多层嵌套逻辑
- `_handle_tool_call()` ~130 行，包含 doom loop + 工具执行 + 结果处理

### 3. 状态管理分散
```python
# 实例变量过多 (15+)
self._iteration
self._consecutive_thoughts
self._is_running
self._is_done
self._final_result
self._compaction_count
self._compaction_callback
self._recent_tool_calls
self._doom_loop_count
self._consecutive_errors
self._consecutive_empty_responses
self._error_registry
self._tool_failure_counts
self._path_not_found_count
```

### 4. 重复逻辑
- 多处检查 `result.fault`
- 多处调用 `self.mmu.add_system_message()`
- 多处进行工具执行 `await self.gate.syscall_tool()`

---

## 💡 重构建议

### 方案 A: 职责分离

```
vcpu/
├── __init__.py          # 导出 VCPU
├── core.py              # VCPU 核心循环 (~300行)
├── handlers.py          # Action handlers (~400行)
├── doom_loop.py         # Doom loop 检测 (~150行)
├── compaction.py        # 压缩逻辑 (~150行)
├── failure_reporter.py  # 失败报告 (~150行)
└── config.py            # 配置和常量 (~100行)
```

### 方案 B: 策略模式

```python
class ActionHandler(Protocol):
    async def handle(self, action: ActionIR, vcpu: "VCPU") -> ToolResult: ...

class ToolCallHandler(ActionHandler): ...
class SubCallHandler(ActionHandler): ...
class ReturnHandler(ActionHandler): ...
```

### 方案 C: 最小改动

1. 提取 `DoomLoopDetector` 类
2. 提取 `FailureReporter` 类
3. 保持 VCPU 作为 Facade

---

## 📋 评审问题

1. **哪种重构方案最适合当前阶段？**
2. **如何在重构过程中保持向后兼容？**
3. **有没有遗漏的职责分离点？**
4. **ErrorHandlerRegistry 的位置是否合适？**

---

## 📈 代码复杂度

```
方法复杂度 (圈复杂度估计):
- step(): ~15 (高)
- _handle_tool_call(): ~12 (高)
- execute(): ~10 (中)
- _handle_empty_result(): ~8 (中)
- 其他方法: ~3-5 (低)
```

---

*请专家评审并给出重构建议*
