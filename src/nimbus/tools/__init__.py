"""Tools package for Nimbus Agent Framework.

Based on pi-coding-agent design philosophy: 4 tools are all you need.

- Read: Smart truncation (2000 lines/50KB), image support, offset/limit
- Write: Auto directory creation, overwrites
- Edit: Fuzzy matching fallback, BOM/CRLF preservation, diff output
- Bash: Streaming output, temp files for large output, default 60s timeout

For glob/grep functionality, use Bash:
- `bash "find . -name '*.py'"`
- `bash "rg 'pattern' ."`
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

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
from nimbus.tools.bash import bash_command
from nimbus.tools.edit import edit_file

# Core Tool functions (4 tools based on pi-coding-agent)
from nimbus.tools.read import read_file

# Sandbox
from nimbus.tools.sandbox import Sandbox, SandboxError
from nimbus.tools.write import write_file

if TYPE_CHECKING:
    from nimbus.agentos import AgentOS


# =============================================================================
# Tool Definitions
# =============================================================================

READ_TOOL: Dict[str, Any] = {
    "name": "Read",
    "description": (
        "Read the contents of a file. Supports text files and images (jpg, png, gif, webp). "
        "Images are sent as attachments. For text files, output is truncated to 2000 lines "
        "or 50KB (whichever is hit first). Use offset/limit for large files. "
        "When you need the full file, continue with offset until complete."
    ),
    "function": read_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to read (relative or absolute)",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (1-indexed)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read",
            },
        },
        "required": ["file_path"],
    },
}

WRITE_TOOL: Dict[str, Any] = {
    "name": "Write",
    "description": (
        "Write content to a file. Creates the file if it doesn't exist, "
        "overwrites if it does. Automatically creates parent directories."
    ),
    "function": write_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to write (relative or absolute)",
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
    "description": (
        "Edit a file by replacing exact text. The oldText must match exactly "
        "(including whitespace). Use this for precise, surgical edits. "
        "Falls back to fuzzy matching if exact match fails."
    ),
    "function": edit_file,
    "parameters": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file to edit (relative or absolute)",
            },
            "old_text": {
                "type": "string",
                "description": "Exact text to find and replace (must match exactly)",
            },
            "new_text": {
                "type": "string",
                "description": "New text to replace the old text with",
            },
        },
        "required": ["file_path", "old_text", "new_text"],
    },
}

BASH_TOOL: Dict[str, Any] = {
    "name": "Bash",
    "description": (
        "Execute a bash command in the current working directory. Returns stdout and stderr. "
        "Output is truncated to last 2000 lines or 50KB (whichever is hit first). "
        "If truncated, full output is saved to a temp file. "
        "Optionally provide a timeout in seconds (default: 60s). "
        "Use for: running tests (pytest), searching files (find, rg, grep), "
        "listing directories (ls), git operations, installing packages, etc."
    ),
    "function": bash_command,
    "parameters": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Bash command to execute",
            },
            "timeout": {
                "type": "number",
                "description": "Timeout in seconds (default: 60)",
            },
        },
        "required": ["command"],
    },
}


# =============================================================================
# Tool Collections
# =============================================================================

ALL_TOOLS: List[Dict[str, Any]] = [
    READ_TOOL,
    WRITE_TOOL,
    EDIT_TOOL,
    BASH_TOOL,
]

TOOL_FUNCTIONS: Dict[str, Callable] = {
    "Read": read_file,
    "Write": write_file,
    "Edit": edit_file,
    "Bash": bash_command,
}


# =============================================================================
# Helper Functions
# =============================================================================


def get_all_tools() -> List[Dict[str, Any]]:
    """Get all tool definitions."""
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


def create_workspace_wrapper(func: Callable, workspace: Path, allowed_paths: Optional[List[Path]] = None) -> Callable:
    """Create a wrapper that injects workspace and allowed_paths into tool calls."""

    async def wrapper(**kwargs: Any) -> Any:
        kwargs["workspace"] = workspace
        if allowed_paths:
            kwargs["allowed_paths"] = allowed_paths
        return await func(**kwargs)

    return wrapper


def register_default_tools(
    os: "AgentOS",
    workspace: Path | None = None,
    tools: List[str] | None = None,
    roles: List[str] | Dict[str, List[str]] | None = None,
) -> List[str]:
    """Register default tools with AgentOS.

    Args:
        os: AgentOS instance to register tools with.
        workspace: Workspace path for tool sandboxing.
        tools: Optional list of specific tool names to register.
        roles: Optional roles configuration. Can be a list (applied to all) or dict (tool_name -> roles).

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

        nimbus_home = Path.home() / ".nimbus"
        wrapped_func = create_workspace_wrapper(func, workspace, allowed_paths=[nimbus_home])

        # Determine roles for this tool
        tool_roles = None
        if isinstance(roles, list):
            tool_roles = roles
        elif isinstance(roles, dict):
            tool_roles = roles.get(name)

        os.register_tool(
            name=name,
            func=wrapped_func,
            description=tool_def.get("description", ""),
            parameters=tool_def.get("parameters"),
            roles=tool_roles,
        )

        registered.append(name)

    return registered


def iterate_tools(
    workspace: Path | None = None,
) -> List[Tuple[str, Callable, str, Dict[str, Any]]]:
    """Iterate over tools with workspace injection.

    Args:
        workspace: Workspace path for tool sandboxing.

    Returns:
        List of (name, wrapped_func, description, parameters) tuples.
    """
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
    "bash_command",
    # Tool definitions
    "READ_TOOL",
    "WRITE_TOOL",
    "EDIT_TOOL",
    "BASH_TOOL",
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
