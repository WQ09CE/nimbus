"""Tools package for Nimbus Agent Framework.

Provides core tools for file operations, search, and shell execution.
"""

from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, TYPE_CHECKING

# Base classes
from nimbus.tools.base import (
    ToolDefinition,
    ToolExecutionError,
    ToolParameter,
    ToolRegistry,
    get_default_registry,
    register_tool,
    tool,
)

# Sandbox
from nimbus.tools.sandbox import Sandbox, SandboxError

# Tool functions
from nimbus.tools.read import read_file
from nimbus.tools.write import write_file
from nimbus.tools.edit import edit_file
from nimbus.tools.glob import glob_files
from nimbus.tools.grep import grep_content, FILE_TYPE_PATTERNS
from nimbus.tools.bash import bash_command

if TYPE_CHECKING:
    from nimbus.agentos import AgentOS


# =============================================================================
# Tool Definitions (for AgentOS registration)
# =============================================================================

READ_TOOL: Dict[str, Any] = {
    "name": "Read",
    "description": "Read file contents with optional line range. Returns content with line numbers.",
    "function": read_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read",
            },
            "offset": {
                "type": "integer",
                "description": "Starting line number (0-based). Defaults to 0.",
                "default": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum lines to read. Defaults to 2000.",
                "default": 2000,
            },
        },
        "required": ["file_path"],
    },
}

GLOB_TOOL: Dict[str, Any] = {
    "name": "Glob",
    "description": "Find files matching a glob pattern. Returns list of matching paths.",
    "function": glob_files,
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match (e.g., '**/*.py')",
            },
            "path": {
                "type": "string",
                "description": "Base directory for search. Defaults to workspace.",
            },
        },
        "required": ["pattern"],
    },
}

GREP_TOOL: Dict[str, Any] = {
    "name": "Grep",
    "description": "Search for pattern in files. Returns matching lines with context.",
    "function": grep_content,
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Search pattern (regex supported)",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search",
            },
            "include": {
                "type": "string",
                "description": "File pattern to include (e.g., '*.py')",
            },
        },
        "required": ["pattern"],
    },
}

BASH_TOOL: Dict[str, Any] = {
    "name": "Bash",
    "description": "Execute a shell command. Returns stdout/stderr.",
    "function": bash_command,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Defaults to 120.",
                "default": 120,
            },
        },
        "required": ["command"],
    },
}

WRITE_TOOL: Dict[str, Any] = {
    "name": "Write",
    "description": "Write or append content to a file. Creates parent directories if needed.",
    "function": write_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file",
            },
        },
        "required": ["file_path", "content"],
    },
}

EDIT_TOOL: Dict[str, Any] = {
    "name": "Edit",
    "description": "Edit a file by replacing exact string matches.",
    "function": edit_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit",
            },
            "old_string": {
                "type": "string",
                "description": "Exact string to find and replace",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement string",
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    },
}

RETURN_RESULT_TOOL: Dict[str, Any] = {
    "name": "return_result",
    "description": "Return the final result when you have completed the task.",
    "parameters": {
        "type": "object",
        "properties": {
            "result": {
                "type": "string",
                "description": "The final result to return to the user"
            }
        },
        "required": ["result"]
    }
}


async def return_result(result: str, **kwargs: Any) -> str:
    """Return the final result. Control flow tool handled by decoder."""
    return result


# All available tools
ALL_TOOLS: List[Dict[str, Any]] = [
    READ_TOOL,
    GLOB_TOOL,
    GREP_TOOL,
    BASH_TOOL,
    WRITE_TOOL,
    EDIT_TOOL,
    RETURN_RESULT_TOOL,
]

# Mapping of tool names to their functions
TOOL_FUNCTIONS: Dict[str, Callable] = {
    "Read": read_file,
    "Glob": glob_files,
    "Grep": grep_content,
    "Bash": bash_command,
    "Write": write_file,
    "Edit": edit_file,
    "return_result": return_result,
}


# =============================================================================
# Helper Functions
# =============================================================================

def get_all_tools() -> List[Dict[str, Any]]:
    """Get all available tool definitions."""
    return ALL_TOOLS.copy()


def get_tool(name: str) -> Dict[str, Any] | None:
    """Get a tool definition by name."""
    for tool_def in ALL_TOOLS:
        if tool_def["name"] == name:
            return tool_def
    return None


def get_tool_function(name: str) -> Callable | None:
    """Get a tool function by name."""
    return TOOL_FUNCTIONS.get(name)


def create_workspace_wrapper(func: Callable, workspace: Path) -> Callable:
    """Create a wrapper that injects workspace into tool calls."""
    async def wrapper(**kwargs: Any) -> Any:
        kwargs["workspace"] = workspace
        return await func(**kwargs)
    return wrapper


def register_default_tools(
    os: "AgentOS",
    workspace: Path | None = None,
    tools: List[str] | None = None,
) -> List[str]:
    """Register default tools with AgentOS.
    
    Args:
        os: AgentOS instance to register tools with.
        workspace: Workspace path for tool sandboxing.
        tools: Optional list of tool names to register. If None, registers all.
    
    Returns:
        List of registered tool names.
    """
    if workspace is None:
        workspace = Path.cwd()

    registered = []
    tools_to_register = tools or list(TOOL_FUNCTIONS.keys())

    for name in tools_to_register:
        tool_def = get_tool(name)
        func = get_tool_function(name)

        if tool_def is None or func is None:
            continue

        wrapped_func = create_workspace_wrapper(func, workspace)

        os.register_tool(
            name=name,
            func=wrapped_func,
            description=tool_def.get("description", ""),
            parameters=tool_def.get("parameters"),
        )

        registered.append(name)

    return registered


def iterate_tools(
    workspace: Path | None = None,
) -> List[Tuple[str, Callable, str, Dict[str, Any]]]:
    """Iterate over all tools with workspace injection."""
    if workspace is None:
        workspace = Path.cwd()

    result = []
    for tool_def in ALL_TOOLS:
        name = tool_def["name"]
        func = TOOL_FUNCTIONS.get(name)
        if func is None:
            continue

        wrapped_func = create_workspace_wrapper(func, workspace)
        description = tool_def.get("description", "")
        parameters = tool_def.get("parameters", {})

        result.append((name, wrapped_func, description, parameters))

    return result


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
    # Tool functions
    "read_file",
    "write_file",
    "edit_file",
    "glob_files",
    "grep_content",
    "bash_command",
    "return_result",
    "FILE_TYPE_PATTERNS",
    # Tool definitions
    "READ_TOOL",
    "GLOB_TOOL",
    "GREP_TOOL",
    "BASH_TOOL",
    "WRITE_TOOL",
    "EDIT_TOOL",
    "RETURN_RESULT_TOOL",
    "ALL_TOOLS",
    "TOOL_FUNCTIONS",
    # Helper functions
    "get_all_tools",
    "get_tool",
    "get_tool_function",
    "create_workspace_wrapper",
    "register_default_tools",
    "iterate_tools",
]
