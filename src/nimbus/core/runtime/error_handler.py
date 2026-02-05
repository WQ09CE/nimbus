"""
智能 Error Handler 系统

借鉴 Claude Code 的错误恢复机制：
- 文件找不到 → 自动列目录帮助定位
- 搜索无匹配 → 建议更宽泛的模式
- 编辑失败 → 自动读取当前内容

设计原则：
1. 错误分类：不同错误类型有不同的恢复策略
2. 渐进式恢复：第 1、2、3 次失败采用不同强度的恢复
3. 智能辅助：自动执行恢复工具（如 ls），而不是只给提示
"""

import json
import os
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

# =============================================================================
# 错误分类
# =============================================================================


class ToolErrorCode(Enum):
    """工具错误分类码"""

    # 文件系统错误
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    DIRECTORY_NOT_FOUND = "DIRECTORY_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    IS_A_DIRECTORY = "IS_A_DIRECTORY"
    NOT_A_DIRECTORY = "NOT_A_DIRECTORY"

    # 搜索/匹配错误
    PATTERN_NO_MATCH = "PATTERN_NO_MATCH"  # Glob/Grep 无匹配
    SEARCH_TOO_BROAD = "SEARCH_TOO_BROAD"  # 匹配太多结果

    # 编辑错误
    STRING_NOT_FOUND = "STRING_NOT_FOUND"  # Edit 找不到目标字符串
    MULTIPLE_MATCHES = "MULTIPLE_MATCHES"  # Edit 匹配多处

    # 执行错误
    COMMAND_FAILED = "COMMAND_FAILED"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    TIMEOUT = "TIMEOUT"

    # 通用
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


# =============================================================================
# 恢复动作
# =============================================================================


@dataclass
class RecoveryAction:
    """
    错误恢复动作

    Attributes:
        action_type: 恢复类型
            - skip: 不处理，让 LLM 自己决定
            - inject_hint: 注入提示消息
            - auto_tool: 自动执行恢复工具
            - modify_args: 修改参数后重试
        hint: 注入给 LLM 的提示消息
        auto_tool: 自动执行的工具名
        auto_args: 自动工具的参数
        modified_args: 修改后的参数（用于重试）
    """

    action_type: Literal["skip", "inject_hint", "auto_tool", "modify_args"]
    hint: Optional[str] = None
    auto_tool: Optional[str] = None
    auto_args: Optional[Dict[str, Any]] = None
    modified_args: Optional[Dict[str, Any]] = None

    @classmethod
    def skip(cls) -> "RecoveryAction":
        """创建一个跳过恢复的动作"""
        return cls(action_type="skip")

    @classmethod
    def inject(cls, hint: str) -> "RecoveryAction":
        """创建一个注入提示的动作"""
        return cls(action_type="inject_hint", hint=hint)

    @classmethod
    def auto_execute(
        cls, tool: str, args: Dict[str, Any], hint: Optional[str] = None
    ) -> "RecoveryAction":
        """创建一个自动执行工具的动作"""
        return cls(
            action_type="auto_tool",
            auto_tool=tool,
            auto_args=args,
            hint=hint,
        )

    @classmethod
    def retry_with(cls, modified_args: Dict[str, Any]) -> "RecoveryAction":
        """创建一个修改参数重试的动作"""
        return cls(action_type="modify_args", modified_args=modified_args)


# =============================================================================
# Error Handler 接口
# =============================================================================


class ErrorHandler(ABC):
    """
    错误处理器抽象基类

    每种错误类型可以有一个专门的 handler，实现渐进式恢复策略。
    """

    @property
    @abstractmethod
    def handled_codes(self) -> List[ToolErrorCode]:
        """此 handler 处理的错误码列表"""
        ...

    @property
    def handled_tools(self) -> Optional[List[str]]:
        """此 handler 处理的工具列表，None 表示所有工具"""
        return None

    def can_handle(self, error_code: ToolErrorCode, tool_name: str) -> bool:
        """检查此 handler 是否能处理给定的错误"""
        if error_code not in self.handled_codes:
            return False
        if self.handled_tools is not None and tool_name not in self.handled_tools:
            return False
        return True

    @abstractmethod
    async def handle(
        self,
        error_code: ToolErrorCode,
        tool_name: str,
        args: Dict[str, Any],
        error_msg: str,
        attempt: int,
        workspace: Optional[str] = None,
    ) -> RecoveryAction:
        """
        处理错误并返回恢复动作

        Args:
            error_code: 错误分类码
            tool_name: 失败的工具名
            args: 工具参数
            error_msg: 错误消息
            attempt: 第几次尝试（1, 2, 3, ...）
            workspace: 工作目录路径

        Returns:
            RecoveryAction 恢复动作
        """
        ...


# =============================================================================
# 内置 Error Handlers
# =============================================================================


class FileNotFoundHandler(ErrorHandler):
    """
    处理文件找不到的情况

    恢复策略：
    1. 第一次：尝试 TypeScript/Node 模块解析（index.ts 等）
    2. 第二次：自动列出目录内容帮助定位
    3. 第三次：建议使用 Glob 搜索
    """

    @property
    def handled_codes(self) -> List[ToolErrorCode]:
        return [ToolErrorCode.FILE_NOT_FOUND]

    @property
    def handled_tools(self) -> Optional[List[str]]:
        return ["Read"]

    async def handle(
        self,
        error_code: ToolErrorCode,
        tool_name: str,
        args: Dict[str, Any],
        error_msg: str,
        attempt: int,
        workspace: Optional[str] = None,
    ) -> RecoveryAction:
        file_path = args.get("file_path", args.get("path", ""))

        if attempt == 1:
            # 第一次：尝试 TypeScript/Node 模块解析
            # 检查是否是目录形式的导入
            if not file_path.endswith((".ts", ".tsx", ".js", ".jsx", ".py")):
                # 可能是目录导入，尝试 index 文件
                for ext in [".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx"]:
                    alt_path = file_path + ext
                    full_path = os.path.join(workspace or ".", alt_path) if workspace else alt_path
                    if os.path.exists(full_path):
                        return RecoveryAction.retry_with({"file_path": alt_path})

            # 没找到替代路径，注入轻微提示
            return RecoveryAction.inject(
                f"💡 File '{file_path}' not found. "
                f"Consider checking the path or using Glob to search."
            )

        elif attempt == 2:
            # 第二次：自动列出目录帮助定位
            dir_path = os.path.dirname(file_path) or "."
            return RecoveryAction.auto_execute(
                tool="Bash",
                args={"command": f"ls -la {dir_path} 2>/dev/null || ls -la ."},
                hint=f"📂 File '{file_path}' not found. Directory contents:",
            )

        else:
            # 第三次及以后：建议 Glob 搜索
            filename = os.path.basename(file_path)
            return RecoveryAction.inject(
                f"🔍 File '{file_path}' still not found after {attempt} attempts.\n\n"
                f"Suggestions:\n"
                f"1. Search with Glob: Glob(pattern='**/{filename}')\n"
                f"2. Find similar files: Glob(pattern='**/*{filename[:5]}*')\n"
                f"3. If this file doesn't exist, stop and report the issue"
            )


class PatternNoMatchHandler(ErrorHandler):
    """
    处理 Glob/Grep 无匹配的情况

    恢复策略：
    1. 第一次：静默跳过，让 LLM 自己调整
    2. 第二次：自动列出当前目录帮助定位
    3. 第三次：建议更宽泛的模式或终止
    """

    @property
    def handled_codes(self) -> List[ToolErrorCode]:
        return [ToolErrorCode.PATTERN_NO_MATCH]

    @property
    def handled_tools(self) -> Optional[List[str]]:
        return ["Glob", "Grep"]

    async def handle(
        self,
        error_code: ToolErrorCode,
        tool_name: str,
        args: Dict[str, Any],
        error_msg: str,
        attempt: int,
        workspace: Optional[str] = None,
    ) -> RecoveryAction:
        pattern = args.get("pattern", "")
        search_path = args.get("path", ".")

        if attempt == 1:
            # 第一次：静默，让 LLM 自己调整
            return RecoveryAction.skip()

        elif attempt == 2:
            # 第二次：自动列出目录内容
            return RecoveryAction.auto_execute(
                tool="Bash",
                args={"command": f"ls -la {search_path} 2>/dev/null | head -20"},
                hint=f"🔍 Pattern '{pattern}' matched nothing in '{search_path}'. Directory contents:",
            )

        else:
            # 第三次及以后：明确指出文件不存在，必须改变策略
            return RecoveryAction.inject(
                f"⚠️ STOP: Pattern '{pattern}' has been tried {attempt} times with no matches.\n\n"
                f"The file you're looking for DOES NOT EXIST in this workspace.\n"
                f"You already saw the directory contents - there are no matching files.\n\n"
                f"YOU MUST NOW:\n"
                f"1. Work with the files that DO exist (check the directory listing above)\n"
                f"2. If you need to verify your work, read the file you modified\n"
                f"3. Stop and report your progress\n\n"
                f"DO NOT try more Glob/Grep patterns for '{pattern}' - it won't help."
            )


class DirectoryAsFileHandler(ErrorHandler):
    """
    处理尝试读取目录的情况

    恢复策略：
    1. 自动列出目录内容
    """

    @property
    def handled_codes(self) -> List[ToolErrorCode]:
        return [ToolErrorCode.IS_A_DIRECTORY]

    @property
    def handled_tools(self) -> Optional[List[str]]:
        return ["Read"]

    async def handle(
        self,
        error_code: ToolErrorCode,
        tool_name: str,
        args: Dict[str, Any],
        error_msg: str,
        attempt: int,
        workspace: Optional[str] = None,
    ) -> RecoveryAction:
        dir_path = args.get("file_path", args.get("path", "."))

        # 总是自动列出目录内容
        return RecoveryAction.auto_execute(
            tool="Bash",
            args={"command": f"ls -la {dir_path}"},
            hint=f"📂 '{dir_path}' is a directory. Contents:",
        )


class EditStringNotFoundHandler(ErrorHandler):
    """
    处理 Edit 找不到目标字符串的情况

    恢复策略：
    1. 第一次：自动读取文件当前内容
    2. 第二次：Grep 搜索类似内容
    3. 第三次：建议检查文件状态或终止
    """

    @property
    def handled_codes(self) -> List[ToolErrorCode]:
        return [ToolErrorCode.STRING_NOT_FOUND]

    @property
    def handled_tools(self) -> Optional[List[str]]:
        return ["Edit"]

    async def handle(
        self,
        error_code: ToolErrorCode,
        tool_name: str,
        args: Dict[str, Any],
        error_msg: str,
        attempt: int,
        workspace: Optional[str] = None,
    ) -> RecoveryAction:
        file_path = args.get("file_path", args.get("path", ""))
        old_string = args.get("old_string", "")[:50]  # 截断以便显示

        if attempt == 1:
            # 第一次：自动读取文件当前内容
            return RecoveryAction.auto_execute(
                tool="Read",
                args={"file_path": file_path},
                hint=f"✏️ Could not find '{old_string}...' in file. Current content:",
            )

        elif attempt == 2:
            # 第二次：Grep 搜索类似内容
            search_term = old_string.split()[0] if old_string.split() else old_string[:10]
            return RecoveryAction.auto_execute(
                tool="Grep",
                args={"pattern": search_term, "path": file_path},
                hint=f"🔍 Searching for similar content in {file_path}:",
            )

        else:
            # 第三次：建议
            return RecoveryAction.inject(
                f"✏️ Edit failed: string not found after {attempt} attempts.\n\n"
                f"Possible reasons:\n"
                f"1. The file was already modified by a previous edit\n"
                f"2. Whitespace or indentation mismatch\n"
                f"3. The string appears differently than expected\n\n"
                f"Next steps:\n"
                f"1. Read the file to see current state\n"
                f"2. If your change is already there, finish by responding with your result\n"
                f"3. If not, use the exact text from the Read output"
            )


class CommandFailedHandler(ErrorHandler):
    """
    处理命令执行失败的情况

    恢复策略：
    1. 第一次：静默，可能是预期的失败
    2. 第二次：建议检查命令语法
    3. 第三次：建议使用替代方法
    """

    @property
    def handled_codes(self) -> List[ToolErrorCode]:
        return [ToolErrorCode.COMMAND_FAILED, ToolErrorCode.COMMAND_NOT_FOUND]

    @property
    def handled_tools(self) -> Optional[List[str]]:
        return ["Bash"]

    async def handle(
        self,
        error_code: ToolErrorCode,
        tool_name: str,
        args: Dict[str, Any],
        error_msg: str,
        attempt: int,
        workspace: Optional[str] = None,
    ) -> RecoveryAction:
        command = args.get("command", "")

        if attempt == 1:
            # 第一次：静默
            return RecoveryAction.skip()

        elif attempt == 2:
            if error_code == ToolErrorCode.COMMAND_NOT_FOUND:
                return RecoveryAction.inject(
                    f"⚠️ Command not found. Check if the required tool is installed.\n"
                    f"You can try: which {command.split()[0]} or apt-get install ..."
                )
            else:
                return RecoveryAction.inject(
                    "⚠️ Command failed. Check the syntax and try again.\n"
                    "You might want to run simpler commands first to debug."
                )

        else:
            return RecoveryAction.inject(
                f"⚠️ Command still failing after {attempt} attempts.\n"
                f"Consider:\n"
                f"1. Using a different approach\n"
                f"2. Breaking down into smaller steps\n"
                f"3. Stopping and reporting the issue"
            )


# =============================================================================
# Error Handler Registry
# =============================================================================


class ErrorHandlerRegistry:
    """
    Error Handler 注册表

    管理所有注册的 error handlers，提供错误分类和处理功能。
    """

    def __init__(self):
        self._handlers: List[ErrorHandler] = []
        self._failure_counts: Dict[str, int] = defaultdict(int)

        # 注册默认 handlers
        self._register_defaults()

    def _register_defaults(self):
        """注册默认的 error handlers"""
        self._handlers = [
            FileNotFoundHandler(),
            PatternNoMatchHandler(),
            DirectoryAsFileHandler(),
            EditStringNotFoundHandler(),
            CommandFailedHandler(),
        ]

    def register(self, handler: ErrorHandler):
        """注册一个 error handler"""
        self._handlers.insert(0, handler)  # 新注册的优先级更高

    def classify_error(self, fault_message: str, tool_name: str = "") -> ToolErrorCode:
        """
        根据错误消息分类错误

        Args:
            fault_message: 错误消息
            tool_name: 工具名称（可选，用于更精确分类）

        Returns:
            ToolErrorCode 错误分类码
        """
        msg = fault_message.lower()

        # 文件系统错误
        if "not found" in msg or "no such file" in msg or "does not exist" in msg:
            if "directory" in msg:
                return ToolErrorCode.DIRECTORY_NOT_FOUND
            if "command" in msg:
                return ToolErrorCode.COMMAND_NOT_FOUND
            return ToolErrorCode.FILE_NOT_FOUND

        if "permission denied" in msg or "access denied" in msg:
            return ToolErrorCode.PERMISSION_DENIED

        if "is a directory" in msg:
            return ToolErrorCode.IS_A_DIRECTORY

        if "not a directory" in msg:
            return ToolErrorCode.NOT_A_DIRECTORY

        # 搜索/匹配错误
        if "no matches" in msg or "no match" in msg or "matched nothing" in msg:
            return ToolErrorCode.PATTERN_NO_MATCH

        if "too many" in msg or "too broad" in msg:
            return ToolErrorCode.SEARCH_TOO_BROAD

        # 编辑错误
        if (
            "string not found" in msg
            or "could not find" in msg
            or "text not found" in msg
            or "no occurrence" in msg
        ):
            return ToolErrorCode.STRING_NOT_FOUND

        if "multiple" in msg and ("match" in msg or "occurrence" in msg):
            return ToolErrorCode.MULTIPLE_MATCHES

        # 执行错误
        if "timeout" in msg or "timed out" in msg:
            return ToolErrorCode.TIMEOUT

        if "failed" in msg or "error" in msg:
            if tool_name == "Bash":
                return ToolErrorCode.COMMAND_FAILED

        return ToolErrorCode.UNKNOWN_ERROR

    def get_call_signature(self, tool_name: str, args: Dict[str, Any]) -> str:
        """生成工具调用的签名（用于跟踪重复调用）"""
        return f"{tool_name}:{json.dumps(args, sort_keys=True)}"

    def record_failure(self, tool_name: str, args: Dict[str, Any]) -> int:
        """
        记录一次失败，返回当前失败次数

        Args:
            tool_name: 工具名
            args: 工具参数

        Returns:
            int: 此调用签名的累计失败次数
        """
        sig = self.get_call_signature(tool_name, args)
        self._failure_counts[sig] += 1
        return self._failure_counts[sig]

    def clear_failure(self, tool_name: str, args: Dict[str, Any]):
        """清除特定调用的失败计数（成功后调用）"""
        sig = self.get_call_signature(tool_name, args)
        if sig in self._failure_counts:
            del self._failure_counts[sig]

    def reset(self):
        """重置所有失败计数"""
        self._failure_counts.clear()

    async def handle_error(
        self,
        fault_message: str,
        tool_name: str,
        args: Dict[str, Any],
        workspace: Optional[str] = None,
    ) -> Optional[RecoveryAction]:
        """
        处理工具错误

        Args:
            fault_message: 错误消息
            tool_name: 失败的工具名
            args: 工具参数
            workspace: 工作目录

        Returns:
            RecoveryAction 或 None（如果无法处理）
        """
        # 分类错误
        error_code = self.classify_error(fault_message, tool_name)

        # 记录失败并获取尝试次数
        attempt = self.record_failure(tool_name, args)

        # 查找能处理的 handler
        for handler in self._handlers:
            if handler.can_handle(error_code, tool_name):
                return await handler.handle(
                    error_code=error_code,
                    tool_name=tool_name,
                    args=args,
                    error_msg=fault_message,
                    attempt=attempt,
                    workspace=workspace,
                )

        # 没有找到 handler，根据尝试次数给出通用建议
        if attempt >= 3:
            return RecoveryAction.inject(
                f"⚠️ Operation failed {attempt} times: {fault_message}\n\n"
                f"Consider trying a different approach or stopping "
                f"to report what you've tried and what obstacles you encountered."
            )

        return None  # 前两次没有 handler 时不干预


# 全局单例（可选）
_default_registry: Optional[ErrorHandlerRegistry] = None


def get_error_handler_registry() -> ErrorHandlerRegistry:
    """获取默认的 error handler registry"""
    global _default_registry
    if _default_registry is None:
        _default_registry = ErrorHandlerRegistry()
    return _default_registry
