"""Tools package for Nimbus Agent Framework.

Provides base classes and registry for code tools that can be executed by agents.

Core components:
    - ToolParameter, ToolDefinition, ToolRegistry: Base classes for tool definition
    - Sandbox, SandboxError: Security sandbox for file access
    - read_file, glob_files, grep_content: Core file operation tools
"""

from nimbus.tools.base import (
    ToolDefinition,
    ToolExecutionError,
    ToolParameter,
    ToolRegistry,
    get_default_registry,
    register_tool,
    tool,
)
from nimbus.tools.glob import glob_files
from nimbus.tools.grep import FILE_TYPE_PATTERNS, grep_content
from nimbus.tools.read import read_file
from nimbus.tools.sandbox import Sandbox, SandboxError

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
    # Tools
    "read_file",
    "glob_files",
    "grep_content",
    "FILE_TYPE_PATTERNS",
]
