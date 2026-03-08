"""
EditFuse — Edit 工具文件级熔断器

与 DoomLoopDetector 的区别：
- DoomLoopDetector 要求完全相同参数（LLM 微调 old_text 可绕过）
- EditFuse 按文件级别追踪失败次数，不关心具体参数

当同一文件 Edit 失败次数达到阈值，系统级阻止后续 Edit 调用，
返回强制降级消息。这是不依赖 LLM 遵从的硬性保障。
"""

from typing import Dict, Optional, Set

from nimbus.core.logging import get_logger
from nimbus.core.protocol import ToolResult

logger = get_logger("kernel.vcpu.edit_fuse")


class EditFuse:
    """
    Edit 工具文件级熔断器。

    追踪同一文件的 Edit 失败次数。
    达到阈值后，对该文件的后续 Edit 调用直接返回熔断消息，
    不再执行实际的 Edit 操作。

    成功 Edit 会将对应文件的失败计数重置。
    """

    def __init__(self, max_failures_per_file: int = 4):
        self._file_failures: Dict[str, int] = {}
        self._fused_files: Set[str] = set()
        self.max_failures = max_failures_per_file

    def check_before_edit(self, file_path: str) -> Optional[ToolResult]:
        """
        在 Edit 执行前检查是否已熔断。

        Returns:
            None: 未熔断，允许执行
            ToolResult: 已熔断，直接返回此消息给 LLM
        """
        if file_path in self._fused_files:
            logger.warning(f"🔌 EditFuse BLOCKED Edit on fused file: {file_path}")
            return ToolResult(
                status="ERROR",
                output=(
                    f"🛑 EDIT BLOCKED: '{file_path}' has been fused after "
                    f"{self.max_failures} consecutive failures.\n\n"
                    f"This file cannot be edited with Edit tool anymore in this session.\n\n"
                    f"REQUIRED ACTION:\n"
                    f"  Read(file_path='{file_path}') → then → "
                    f"Write(file_path='{file_path}', content='complete new content')\n"
                    f"OR skip this change and proceed to next task."
                ),
            )
        return None

    def on_edit_success(self, file_path: str) -> None:
        """Edit 成功后重置该文件的失败计数。"""
        if file_path in self._file_failures:
            logger.debug(f"✅ EditFuse reset for {file_path} after success")
            del self._file_failures[file_path]
        self._fused_files.discard(file_path)

    def on_edit_failure(self, file_path: str) -> None:
        """
        记录 Edit 失败。达到阈值时熔断该文件。
        """
        count = self._file_failures.get(file_path, 0) + 1
        self._file_failures[file_path] = count

        if count >= self.max_failures:
            self._fused_files.add(file_path)
            logger.warning(
                f"🔌 EditFuse TRIPPED for '{file_path}' "
                f"after {count} failures (threshold={self.max_failures})"
            )
        else:
            logger.debug(
                f"EditFuse: {file_path} failure {count}/{self.max_failures}"
            )

    def get_failure_count(self, file_path: str) -> int:
        return self._file_failures.get(file_path, 0)

    def is_fused(self, file_path: str) -> bool:
        return file_path in self._fused_files

    def reset(self) -> None:
        """重置所有状态（用于测试或新任务开始）。"""
        self._file_failures.clear()
        self._fused_files.clear()
