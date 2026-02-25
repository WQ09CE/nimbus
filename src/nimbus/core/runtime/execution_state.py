"""
Execution State - vCPU 执行状态管理

将 vCPU 的 15+ 实例变量集中到一个数据类中，
简化状态管理和重置逻辑。

设计原则：
- 状态集中：所有执行相关状态在一处管理
- 易于重置：reset() 方法一次性重置所有状态
- 可序列化：支持调试和检查点
- 状态追踪：提供状态变更的辅助方法
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from nimbus.core.persistence import ExecutionStateModel


@dataclass
class ExecutionState:
    """
    vCPU 执行状态

    集中管理所有执行相关的状态变量，解决状态爆炸问题。

    Example:
        state = ExecutionState()

        # 开始执行
        state.is_running = True
        state.iteration += 1

        # 重置
        state.reset()
    """

    # 迭代控制
    iteration: int = 0
    max_iterations: int = 50

    # 执行状态
    is_running: bool = False
    is_done: bool = False
    final_result: Optional[Any] = None

    # 连续计数器
    consecutive_thoughts: int = 0
    consecutive_errors: int = 0
    consecutive_empty_responses: int = 0

    # Compaction 相关
    compaction_count: int = 0
    max_compactions: int = 1  # 默认允许 1 次压缩，由 VCPU 逻辑配合

    # 工具失败跟踪
    tool_failure_counts: Dict[str, int] = field(default_factory=dict)
    max_tool_failures: int = 6

    # 路径解析计数（用于文件查找恢复）
    path_not_found_count: int = 0

    # Doom loop 累计计数（跨检测周期）
    doom_loop_count: int = 0

    # 中断控制
    interruption_requested: bool = False

    # Productive work tracking (A+B anti-premature-termination)
    has_productive_work: bool = False
    # Terminal work = direct output artifacts (Write/Edit/Bash)
    # After terminal work, first text is almost certainly a task summary.
    # After coordination work (Explore/Implement/etc), first text may be planning.
    has_terminal_work: bool = False

    # Suppress streaming after poke (transient, not persisted)
    suppress_streaming: bool = False
    pending_thought_text: str = ""

    def reset(self) -> None:
        """重置所有状态到初始值"""
        self.iteration = 0
        self.is_running = False
        self.is_done = False
        self.final_result = None
        self.consecutive_thoughts = 0
        self.consecutive_errors = 0
        self.consecutive_empty_responses = 0
        self.compaction_count = 0
        self.tool_failure_counts.clear()
        self.path_not_found_count = 0
        self.doom_loop_count = 0
        self.interruption_requested = False
        self.has_productive_work = False
        self.has_terminal_work = False
        self.suppress_streaming = False
        self.pending_thought_text = ""

    def start_execution(self) -> None:
        """开始执行"""
        self.reset()
        self.is_running = True

    def finish_execution(self, result: Any) -> None:
        """完成执行"""
        self.is_done = True
        self.is_running = False
        self.final_result = result

    def increment_iteration(self) -> int:
        """
        增加迭代计数

        Returns:
            新的迭代次数
        """
        self.iteration += 1
        return self.iteration

    def should_compact(self) -> bool:
        """
        检查是否应该进行压缩

        Returns:
            True 如果达到迭代限制且未超过最大压缩次数
        """
        return (
            self.iteration >= self.max_iterations and self.compaction_count < self.max_compactions
        )

    def record_compaction(self) -> int:
        """
        记录一次压缩

        Returns:
            新的压缩次数
        """
        self.compaction_count += 1
        self.iteration = 0  # 重置迭代计数
        return self.compaction_count

    def on_thought(self) -> int:
        """
        记录一次思考（无工具调用的响应）

        Returns:
            连续思考次数
        """
        self.consecutive_thoughts += 1
        return self.consecutive_thoughts

    def on_action(self) -> None:
        """记录一次动作（有工具调用），重置思考计数"""
        self.consecutive_thoughts = 0
        self.pending_thought_text = ""
        self.suppress_streaming = False

    # Productive tools = tools that create/modify output (not just reading)
    PRODUCTIVE_TOOLS = frozenset({
        "Write", "Edit", "Bash",
        "Dispatch", "Explore", "Implement", "Design", "Test",
    })

    # Terminal tools = tools that produce direct output artifacts.
    # After terminal work, first text-only response = task summary (immediate RETURN).
    # Coordination tools (Explore/Implement/etc) don't trigger this shortcut.
    TERMINAL_TOOLS = frozenset({"Write", "Edit", "Bash"})

    def on_productive_action(self, tool_name: str) -> None:
        """Record that a productive tool was called."""
        if tool_name in self.PRODUCTIVE_TOOLS:
            self.has_productive_work = True
        if tool_name in self.TERMINAL_TOOLS:
            self.has_terminal_work = True

    def on_tool_success(self, tool_name: str) -> None:
        """
        记录工具成功

        Args:
            tool_name: 工具名称
        """
        self.consecutive_errors = 0
        self.tool_failure_counts[tool_name] = 0

    def on_tool_failure(self, tool_name: str) -> int:
        """
        记录工具失败

        Args:
            tool_name: 工具名称

        Returns:
            该工具的累计失败次数
        """
        self.consecutive_errors += 1
        self.tool_failure_counts[tool_name] = self.tool_failure_counts.get(tool_name, 0) + 1
        return self.tool_failure_counts[tool_name]

    def is_tool_failing_too_much(self, tool_name: str) -> bool:
        """
        检查工具是否失败过多

        Args:
            tool_name: 工具名称

        Returns:
            True 如果失败次数超过阈值
        """
        return self.tool_failure_counts.get(tool_name, 0) >= self.max_tool_failures

    def on_empty_response(self) -> int:
        """
        记录空响应

        Returns:
            连续空响应次数
        """
        self.consecutive_empty_responses += 1
        return self.consecutive_empty_responses

    def on_valid_response(self) -> None:
        """记录有效响应，重置空响应计数"""
        self.consecutive_empty_responses = 0

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典（用于序列化/调试）

        Returns:
            状态字典
        """
        return {
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "is_running": self.is_running,
            "is_done": self.is_done,
            "has_final_result": self.final_result is not None,
            "consecutive_thoughts": self.consecutive_thoughts,
            "consecutive_errors": self.consecutive_errors,
            "consecutive_empty_responses": self.consecutive_empty_responses,
            "compaction_count": self.compaction_count,
            "max_compactions": self.max_compactions,
            "tool_failure_counts": dict(self.tool_failure_counts),
            "path_not_found_count": self.path_not_found_count,
            "doom_loop_count": self.doom_loop_count,
            "has_productive_work": self.has_productive_work,
            "has_terminal_work": self.has_terminal_work,
        }

    @classmethod
    def from_config(
        cls,
        max_iterations: int = 50,
        max_compactions: int = 10,
        max_tool_failures: int = 6,
    ) -> "ExecutionState":
        """
        从配置创建状态对象

        Args:
            max_iterations: 最大迭代次数
            max_compactions: 最大压缩次数
            max_tool_failures: 单工具最大失败次数

        Returns:
            配置好的 ExecutionState
        """
        return cls(
            max_iterations=max_iterations,
            max_compactions=max_compactions,
            max_tool_failures=max_tool_failures,
        )

    def create_snapshot(self) -> ExecutionStateModel:
        """Create a snapshot of the execution state."""
        return ExecutionStateModel(
            iteration=self.iteration,
            max_iterations=self.max_iterations,
            is_running=self.is_running,
            is_done=self.is_done,
            final_result=self.final_result,
            consecutive_thoughts=self.consecutive_thoughts,
            consecutive_errors=self.consecutive_errors,
            consecutive_empty_responses=self.consecutive_empty_responses,
            compaction_count=self.compaction_count,
            max_compactions=self.max_compactions,
            tool_failure_counts=dict(self.tool_failure_counts),
            path_not_found_count=self.path_not_found_count,
            doom_loop_count=self.doom_loop_count,
            has_productive_work=self.has_productive_work,
            has_terminal_work=self.has_terminal_work,
        )

    def restore_from_snapshot(self, snapshot: ExecutionStateModel) -> None:
        """Restore state from snapshot."""
        self.iteration = snapshot.iteration
        self.max_iterations = snapshot.max_iterations
        self.is_running = snapshot.is_running
        self.is_done = snapshot.is_done
        self.final_result = snapshot.final_result
        self.consecutive_thoughts = snapshot.consecutive_thoughts
        self.consecutive_errors = snapshot.consecutive_errors
        self.consecutive_empty_responses = snapshot.consecutive_empty_responses
        self.compaction_count = snapshot.compaction_count
        self.max_compactions = snapshot.max_compactions
        self.tool_failure_counts = dict(snapshot.tool_failure_counts)
        self.path_not_found_count = snapshot.path_not_found_count
        self.doom_loop_count = snapshot.doom_loop_count
        self.has_productive_work = getattr(snapshot, 'has_productive_work', False)
        self.has_terminal_work = getattr(snapshot, 'has_terminal_work', False)
