"""Tools package for Nimbus Agent Framework.

Provides base classes and registry for code tools that can be executed by agents.

Core components:
    - ToolParameter, ToolDefinition, ToolRegistry: Base classes for tool definition
    - Sandbox, SandboxError: Security sandbox for file access
    - read_file, write_file, edit_file: File operation tools
    - glob_files, grep_content, code_search: Search tools
    - bash_command: Command execution tool
    - web_fetch, web_search: Web tools
    - SmartPathResolver, FileTreeCache: Path resolution and caching
    - ToolRetryMiddleware: Intelligent tool retry with error enhancement
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
from nimbus.tools.bash import bash_command
from nimbus.tools.batch import BatchExecutionError, batch_tool
from nimbus.tools.edit import edit_file
from nimbus.tools.filetree import FileTreeCache, FileTreeEntry
from nimbus.tools.glob import glob_files
from nimbus.tools.grep import FILE_TYPE_PATTERNS, grep_content
from nimbus.tools.middleware import (
    EnhancedToolError,
    MiddlewareChain,
    ToolMiddleware,
    ToolRetryConfig,
    ToolRetryMiddleware,
)
from nimbus.tools.read import read_file
from nimbus.tools.resolver import PathCandidate, SmartPathResolver
from nimbus.tools.sandbox import Sandbox, SandboxError
from nimbus.tools.webfetch import clear_cache as clear_webfetch_cache
from nimbus.tools.webfetch import web_fetch
from nimbus.tools.websearch import WebSearchError, clear_executor as clear_websearch_executor
from nimbus.tools.websearch import web_search
from nimbus.tools.write import write_file
from nimbus.tools.search import code_search
from nimbus.tools.subagent import (
    SubagentContext,
    SubagentExecutor,
    SubagentResult,
    SubagentStatus,
    SubagentType,
    subagent_task,
    get_subagent_result,
    cancel_subagent,
    list_subagents,
    get_executor as get_subagent_executor,
    reset_executor as reset_subagent_executor,
    MAX_DEPTH as SUBAGENT_MAX_DEPTH,
    MAX_CONCURRENT as SUBAGENT_MAX_CONCURRENT,
)

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
    # Path resolution and caching
    "SmartPathResolver",
    "PathCandidate",
    "FileTreeCache",
    "FileTreeEntry",
    # Middleware
    "ToolMiddleware",
    "ToolRetryMiddleware",
    "ToolRetryConfig",
    "EnhancedToolError",
    "MiddlewareChain",
    # Tools
    "read_file",
    "write_file",
    "edit_file",
    "glob_files",
    "grep_content",
    "code_search",
    "bash_command",
    "batch_tool",
    "BatchExecutionError",
    "web_fetch",
    "clear_webfetch_cache",
    "web_search",
    "WebSearchError",
    "clear_websearch_executor",
    "FILE_TYPE_PATTERNS",
    # Subagent tools
    "SubagentContext",
    "SubagentExecutor",
    "SubagentResult",
    "SubagentStatus",
    "SubagentType",
    "subagent_task",
    "get_subagent_result",
    "cancel_subagent",
    "list_subagents",
    "get_subagent_executor",
    "reset_subagent_executor",
    "SUBAGENT_MAX_DEPTH",
    "SUBAGENT_MAX_CONCURRENT",
]
