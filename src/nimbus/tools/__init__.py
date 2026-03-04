"""Tools package for Nimbus Agent Framework.

Based on pi-coding-agent design philosophy: 4 tools are all you need.

- Read: Smart truncation (2000 lines/50KB), image support, offset/limit
- Write: Auto directory creation, overwrites
- Edit: Fuzzy matching fallback, BOM/CRLF preservation, diff output
- Bash: Streaming output, temp files for large output, default 60s timeout

For glob/grep functionality, use Bash:
- `bash "find . -name '*.py'"`
- `bash "rg 'pattern' ."`

Tool registration is now declarative: each tool module uses the @tool decorator
to self-register into the global ToolRegistry. This __init__.py imports the
modules (triggering decorator execution) and exposes helpers for consumers.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

# Base classes — must be imported first (no @tool dependency)
from nimbus.tools.base import (
    ToolDefinition,
    ToolExecutionError,
    ToolParameter,
    ToolRegistry,
    get_default_registry,
    infer_parameters_from_func,
    register_tool,
    tool,
)

# Sandbox — no @tool, just infrastructure
from nimbus.tools.sandbox import Sandbox, SandboxError

# ---------------------------------------------------------------------------
# Import tool modules — each uses @tool to mark the function, then we
# explicitly register them into the global registry here.
# ---------------------------------------------------------------------------
from nimbus.tools.bash import bash_command
from nimbus.tools.edit import edit_file
from nimbus.tools.read import read_file
from nimbus.tools.write import write_file

# NimFS artifact tools (IPC -- keep registered)
from nimbus.tools.nimfs_tools import (              # noqa: F401
    nimfs_list_artifacts,
    nimfs_read_artifact,
    nimfs_write_artifact,
)

# Unified memory tools
from nimbus.tools.memo_tools import memo, recall, read_memo

# ---------------------------------------------------------------------------
# Explicitly register all @tool-decorated functions into the default registry.
# The @tool decorator only attaches _tool_definition; it does NOT auto-register.
# Registration must happen here (the package init) so that all importers that
# do `from nimbus.tools.base import get_default_registry` see the same registry.
# ---------------------------------------------------------------------------
_registry = get_default_registry()
for _fn in [
    bash_command,
    edit_file,
    read_file,
    write_file,
    nimfs_list_artifacts,
    nimfs_read_artifact,
    nimfs_write_artifact,
    memo,
    recall,
    read_memo,
]:
    if hasattr(_fn, "_tool_definition"):
        try:
            _registry.register_decorated(_fn)
        except ValueError:
            pass  # Already registered (e.g. re-import in tests)

if TYPE_CHECKING:
    from nimbus.agentos import AgentOS


# =============================================================================
# Backward-compatible collections (derived from registry, no longer hardcoded)
# =============================================================================

def _build_legacy_collections() -> Tuple[List[Dict[str, Any]], Dict[str, Callable]]:
    """Build ALL_TOOLS / TOOL_FUNCTIONS from the global registry (lazy, called once)."""
    registry = get_default_registry()
    all_tools: List[Dict[str, Any]] = []
    tool_functions: Dict[str, Callable] = {}

    for name in registry.list_tools():
        entry = registry.get(name)
        if entry is None:
            continue
        td, func = entry
        all_tools.append(td.to_dict())
        tool_functions[name] = func

    return all_tools, tool_functions


ALL_TOOLS, TOOL_FUNCTIONS = _build_legacy_collections()

# Legacy per-tool aliases (still used by some tests / consumers)
def _get_tool_dict(name: str) -> Dict[str, Any]:
    for t in ALL_TOOLS:
        if t["name"] == name:
            return t
    raise KeyError(f"Tool '{name}' not found in registry")

READ_TOOL  = _get_tool_dict("Read")
WRITE_TOOL = _get_tool_dict("Write")
EDIT_TOOL  = _get_tool_dict("Edit")
BASH_TOOL  = _get_tool_dict("Bash")

# NimFS collections (kept for backward compat with callers that imported them)
NIMFS_TOOLS: List[Dict[str, Any]] = [
    t for t in ALL_TOOLS if t["name"].startswith("NimFS")
]
NIMFS_TOOL_FUNCTIONS: Dict[str, Callable] = {
    name: fn for name, fn in TOOL_FUNCTIONS.items() if name.startswith("NimFS")
}


# =============================================================================
# Helper Functions
# =============================================================================


def get_all_tools() -> List[Dict[str, Any]]:
    """Get all tool definitions (as dicts) from the registry."""
    registry = get_default_registry()
    result = []
    for name in registry.list_tools():
        entry = registry.get(name)
        if entry:
            td, _ = entry
            result.append(td.to_dict())
    return result


def get_tool(name: str) -> Dict[str, Any] | None:
    """Get a tool definition dict by name."""
    registry = get_default_registry()
    entry = registry.get(name)
    if entry is None:
        return None
    td, _ = entry
    return td.to_dict()


def get_tool_function(name: str) -> Callable | None:
    """Get a tool function by name."""
    return get_default_registry().get_function(name)


def create_workspace_wrapper(
    func: Callable,
    workspace: Path,
    allowed_paths: Optional[List[Path]] = None,
) -> Callable:
    """Create a wrapper that injects workspace and allowed_paths into tool calls.

    The wrapper preserves the _tool_definition attribute so that AgentOS
    can detect the @tool decorator and use the pre-built ToolDefinition.
    """

    async def wrapper(**kwargs: Any) -> Any:
        kwargs["workspace"] = workspace
        if allowed_paths:
            kwargs["allowed_paths"] = allowed_paths
        return await func(**kwargs)

    # Preserve @tool decorator metadata so AgentOS recognises this as a decorated tool
    if hasattr(func, "_tool_definition"):
        wrapper._tool_definition = func._tool_definition  # type: ignore[attr-defined]

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
        tools: Optional list of specific tool names to register.

    Returns:
        List of registered tool names.
    """
    if workspace is None:
        workspace = Path.cwd()

    registry = get_default_registry()
    tools_to_register = tools or registry.list_tools()

    registered = []
    for name in tools_to_register:
        entry = registry.get(name)
        if entry is None:
            continue
        td, func = entry

        nimbus_home = Path.home() / ".nimbus"
        wrapped_func = create_workspace_wrapper(func, workspace, allowed_paths=[nimbus_home])

        tool_dict = td.to_dict()
        os.register_tool(
            name=name,
            func=wrapped_func,
            description=tool_dict.get("description", ""),
            parameters=tool_dict.get("parameters"),
            category=td.category,
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

    registry = get_default_registry()
    result = []
    for name in registry.list_tools():
        entry = registry.get(name)
        if entry is None:
            continue
        td, func = entry
        wrapped_func = create_workspace_wrapper(func, workspace)
        tool_dict = td.to_dict()
        result.append((
            name,
            wrapped_func,
            tool_dict.get("description", ""),
            tool_dict.get("parameters", {}),
        ))
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
    "infer_parameters_from_func",
    # Sandbox
    "Sandbox",
    "SandboxError",
    # Tool functions — core
    "read_file",
    "write_file",
    "edit_file",
    "bash_command",
    # Tool functions — NimFS Artifacts
    "nimfs_write_artifact",
    "nimfs_read_artifact",
    "nimfs_list_artifacts",
    # Tool functions — Memory (unified)
    "memo",
    "recall",
    "read_memo",
    # Backward-compat collections
    "READ_TOOL",
    "WRITE_TOOL",
    "EDIT_TOOL",
    "BASH_TOOL",
    "ALL_TOOLS",
    "TOOL_FUNCTIONS",
    "NIMFS_TOOLS",
    "NIMFS_TOOL_FUNCTIONS",
    # Helper functions
    "get_all_tools",
    "get_tool",
    "get_tool_function",
    "create_workspace_wrapper",
    "register_default_tools",
    "iterate_tools",
]
