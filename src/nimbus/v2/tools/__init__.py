"""V2 Native Tools Package.

This package provides native v2 tools for AgentOS, replacing the need for
v1 tool adapters. Each tool is a simple async function with a corresponding
tool definition dict.

Usage:
    from nimbus.v2.tools import get_all_tools, register_default_tools

    # Get all tool definitions
    tools = get_all_tools()

    # Register with AgentOS
    os = AgentOS(llm_client=llm)
    register_default_tools(os, workspace=Path.cwd())
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Tuple

from .bash import BASH_TOOL, bash_command, KILL_TOOL, kill_process
from .edit import EDIT_TOOL, edit_file
from .glob import GLOB_TOOL, glob_files
from .grep import GREP_TOOL, grep_content

# Import tool definitions and functions
from .read import READ_TOOL, read_file
from .write import WRITE_TOOL, write_file

# Return result tool definition (control flow tool)
RETURN_RESULT_TOOL: Dict[str, Any] = {
    "name": "return_result",
    "description": "Return the final result when you have completed the task. MUST be called to finish.",
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
    """Return the final result. This is a control flow tool handled by the decoder."""
    return result


if TYPE_CHECKING:
    from nimbus.v2.agentos import AgentOS

# All available tools
ALL_TOOLS: List[Dict[str, Any]] = [
    READ_TOOL,
    GLOB_TOOL,
    GREP_TOOL,
    BASH_TOOL,
    KILL_TOOL,
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
    "Kill": kill_process,
    "Write": write_file,
    "Edit": edit_file,
    "return_result": return_result,
}


def get_all_tools() -> List[Dict[str, Any]]:
    """Get all v2 tool definitions.

    Returns:
        List of tool definition dicts.
    """
    return ALL_TOOLS.copy()


def get_tool(name: str) -> Dict[str, Any] | None:
    """Get a tool definition by name.

    Args:
        name: Tool name.

    Returns:
        Tool definition dict or None if not found.
    """
    for tool in ALL_TOOLS:
        if tool["name"] == name:
            return tool
    return None


def get_tool_function(name: str) -> Callable | None:
    """Get a tool function by name.

    Args:
        name: Tool name.

    Returns:
        Tool function or None if not found.
    """
    return TOOL_FUNCTIONS.get(name)


def create_workspace_wrapper(
    func: Callable,
    workspace: Path,
) -> Callable:
    """Create a wrapper that injects workspace into tool calls.

    Args:
        func: Original tool function.
        workspace: Workspace path to inject.

    Returns:
        Wrapped function that includes workspace.
    """
    async def wrapper(**kwargs: Any) -> Any:
        # Inject workspace if not provided
        if "workspace" not in kwargs:
            kwargs["workspace"] = workspace
        return await func(**kwargs)

    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


def register_default_tools(
    os: "AgentOS",
    workspace: Path | None = None,
    tools: List[str] | None = None,
) -> List[str]:
    """Register default v2 tools with AgentOS.

    Args:
        os: AgentOS instance to register tools with.
        workspace: Workspace path for tool sandboxing.
        tools: Optional list of tool names to register. If None, registers all.

    Returns:
        List of registered tool names.

    Example:
        os = AgentOS(llm_client=llm)
        register_default_tools(os, workspace=Path.cwd())
        # Or register specific tools:
        register_default_tools(os, workspace=Path.cwd(), tools=["Read", "Glob", "Grep"])
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

        # Create wrapped function that includes workspace
        wrapped_func = create_workspace_wrapper(func, workspace)

        # Register with AgentOS
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
    """Iterate over all tools with workspace injection.

    This is compatible with the V1ToV2ToolAdapter interface.

    Args:
        workspace: Workspace path for tool sandboxing.

    Yields:
        Tuple of (name, wrapped_func, description, parameters) for each tool.
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
    # Tool functions
    "read_file",
    "glob_files",
    "grep_content",
    "bash_command",
    "write_file",
    "edit_file",
    "return_result",
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
