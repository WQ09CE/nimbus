"""Tools package - v2 使用的核心工具

保留被 v2 复用的基础工具，其他已移到 nimbus.legacy.tools
"""

__layer__ = 0  # Infrastructure Layer
__role__ = "ISA"  # Instruction Set Architecture

from nimbus.tools.base import (
    ToolDefinition,
    ToolExecutionError,
    ToolParameter,
    ToolRegistry,
    get_default_registry,
    register_tool,
    tool,
)
from nimbus.tools.sandbox import Sandbox, SandboxError
from nimbus.tools.read import read_file
from nimbus.tools.write import write_file
from nimbus.tools.edit import edit_file
from nimbus.tools.glob import glob_files
from nimbus.tools.grep import grep_content, FILE_TYPE_PATTERNS
from nimbus.tools.bash import bash_command

__all__ = [
    # Base classes
    "ToolParameter",
    "ToolDefinition",
    "ToolRegistry",
    "ToolExecutionError",
    "tool",
    "get_default_registry",
    "register_tool",
    # Sandbox
    "Sandbox",
    "SandboxError",
    # Core tools (used by v2)
    "read_file",
    "write_file",
    "edit_file",
    "glob_files",
    "grep_content",
    "bash_command",
    "FILE_TYPE_PATTERNS",
]
