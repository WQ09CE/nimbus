"""
Doom Loop Detector - 检测工具调用的无限循环

学习自 opencode 的 processor.ts，当同一工具以相同参数被连续调用
超过阈值次数时，判定为 doom loop 并中断执行。

设计原则：
- 单一职责：只负责检测，不负责恢复
- 无副作用：不修改外部状态
- 可测试：纯逻辑，易于单元测试
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# 默认阈值（来自 opencode）
DEFAULT_DOOM_LOOP_THRESHOLD = 3


@dataclass
class DoomLoopResult:
    """
    Doom Loop 检测结果

    Attributes:
        is_loop: 是否检测到 doom loop
        consecutive_count: 连续相同调用次数
        tool_name: 触发 doom loop 的工具名（仅当 is_loop=True）
        guidance: 恢复指导文本（仅当 is_loop=True）
    """

    is_loop: bool
    consecutive_count: int
    tool_name: Optional[str] = None
    guidance: Optional[str] = None

    @classmethod
    def ok(cls) -> "DoomLoopResult":
        """创建一个正常（非 doom loop）的结果"""
        return cls(is_loop=False, consecutive_count=0)

    @classmethod
    def detected(cls, tool_name: str, count: int, guidance: str) -> "DoomLoopResult":
        """创建一个检测到 doom loop 的结果"""
        return cls(
            is_loop=True,
            consecutive_count=count,
            tool_name=tool_name,
            guidance=guidance,
        )


class DoomLoopDetector:
    """
    Doom Loop 检测器

    跟踪最近的工具调用，检测相同调用的连续重复。

    Example:
        detector = DoomLoopDetector(threshold=3)

        result = detector.check("Read", {"path": "foo.py"})
        if result.is_loop:
            print(f"Doom loop detected: {result.guidance}")
    """

    # 工具特定的恢复指导
    GUIDANCE_MAP: Dict[str, str] = {
        "Edit": (
            "EDIT TOOL GUIDANCE:\n"
            "1. Use the Read tool FIRST to see the current file content\n"
            "2. Common failure reasons:\n"
            "   - The old_string does not match the file content exactly\n"
            "   - The file was already modified by a previous successful edit\n"
            "   - Whitespace or indentation mismatch\n"
            "   - The text appears multiple times (need more context)\n"
            "3. Recovery steps:\n"
            "   - Read the file to get the current state\n"
            "   - If the change you wanted is already there, move on\n"
            "   - If you need a different edit, use text from the fresh Read\n"
            "4. If your task is complete, finish by responding with your result"
        ),
        "Write": (
            "WRITE TOOL GUIDANCE:\n"
            "- If Write is failing repeatedly, the file path may be invalid\n"
            "- Check if the directory exists using Glob or Bash\n"
            "- Ensure you have permission to write to this location\n"
            "- Consider using a different approach if Write keeps failing"
        ),
        "Bash": (
            "BASH TOOL GUIDANCE:\n"
            "- The same command is failing repeatedly\n"
            "- Check if the command syntax is correct\n"
            "- Verify required dependencies are installed\n"
            "- Try a different approach to achieve the same goal"
        ),
        "Read": (
            "READ TOOL GUIDANCE:\n"
            "- The file may not exist at the specified path\n"
            "- Use Glob to search for the correct file path\n"
            "- Check if the path is relative vs absolute"
        ),
        "Glob": (
            "GLOB TOOL GUIDANCE:\n"
            "- The pattern may not match any files\n"
            "- Try a broader pattern (e.g., **/*.py instead of specific path)\n"
            "- Verify the search directory is correct\n"
            "- If the file doesn't exist, stop and report the issue"
        ),
        "Grep": (
            "GREP TOOL GUIDANCE:\n"
            "- The search pattern may not exist in any files\n"
            "- Try a simpler or broader search pattern\n"
            "- Check if the path/directory is correct"
        ),
    }

    def __init__(self, threshold: int = DEFAULT_DOOM_LOOP_THRESHOLD):
        """
        初始化检测器

        Args:
            threshold: 触发 doom loop 的连续相同调用次数
        """
        self.threshold = threshold
        self._recent_calls: List[Tuple[str, str]] = []
        self._loop_count = 0  # 累计检测到的 doom loop 次数

    def _normalize_args_for_comparison(self, tool_name: str, args: Dict) -> Dict:
        """Normalize args for doom loop comparison.

        For some tools, only key parameters matter for loop detection.
        E.g., Read with same file_path but different limit is still a loop.
        """
        if tool_name == "Read":
            # file_path + offset define the read position; different offsets = paginated reads, not loops
            return {
                "file_path": args.get("file_path", ""),
                "offset": args.get("offset", 0),
            }
        if tool_name == "Edit":
            # file_path + old_string are the key parameters
            return {
                "file_path": args.get("file_path", ""),
                "old_string": args.get("old_string", ""),
            }
        return args

    def check(self, tool_name: str, args: Dict) -> DoomLoopResult:
        """
        检查是否进入 doom loop

        Args:
            tool_name: 工具名称
            args: 工具参数

        Returns:
            DoomLoopResult，包含是否检测到 doom loop 及相关信息
        """
        # 归一化参数后序列化以便比较
        normalized_args = self._normalize_args_for_comparison(tool_name, args)
        args_json = json.dumps(normalized_args, sort_keys=True)
        current_call = (tool_name, args_json)

        # 记录此次调用
        self._recent_calls.append(current_call)

        # 保持窗口大小
        if len(self._recent_calls) > self.threshold:
            self._recent_calls = self._recent_calls[-self.threshold :]

        # 检测 doom loop
        if len(self._recent_calls) == self.threshold:
            if all(call == current_call for call in self._recent_calls):
                # 检测到 doom loop
                self._loop_count += 1
                self._recent_calls.clear()  # 清除以允许恢复

                guidance = self.get_guidance(tool_name)
                return DoomLoopResult.detected(
                    tool_name=tool_name,
                    count=self.threshold,
                    guidance=guidance,
                )

        return DoomLoopResult.ok()

    def get_guidance(self, tool_name: str) -> str:
        """
        获取工具特定的恢复指导

        Args:
            tool_name: 工具名称

        Returns:
            恢复指导文本
        """
        return self.GUIDANCE_MAP.get(
            tool_name,
            (
                f"GENERAL GUIDANCE:\n"
                f"- The {tool_name} tool is failing with the same arguments\n"
                f"- Review the error message from previous attempts\n"
                f"- Try a different approach or different arguments\n"
                f"- If stuck, stop and explain what went wrong"
            ),
        )

    def reset(self) -> None:
        """重置检测器状态"""
        self._recent_calls.clear()
        self._loop_count = 0

    def on_different_tool(self) -> None:
        """
        当调用了不同的工具时调用

        这会清除历史记录，因为不同工具的调用打破了连续性
        """
        if len(self._recent_calls) > 1:
            # 只保留最后一个调用
            self._recent_calls = self._recent_calls[-1:]

    @property
    def loop_count(self) -> int:
        """累计检测到的 doom loop 次数"""
        return self._loop_count

    @property
    def recent_calls(self) -> List[Tuple[str, str]]:
        """最近的调用记录（用于调试）"""
        return self._recent_calls.copy()
