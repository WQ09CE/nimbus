"""Tool base classes and registry for Nimbus Agent Framework.

Architecture Layer: 0 (Infrastructure)
Von Neumann Role: ISA (Instruction Set Architecture)

In the Agent OS architecture, tools represent the Instruction Set Architecture.
They define the operations that agents can perform on the external world,
similar to how CPU instructions define operations on memory and I/O.

Tool definitions are analogous to opcode specifications:
- ToolParameter -> Operand specification
- ToolDefinition -> Instruction format
- ToolRegistry -> Instruction decoder/dispatcher

This module provides the foundation for defining and executing tools that can
be used by code agents. Tools are similar to skills but designed specifically
for code-related operations like reading files, searching, and executing commands.

Provides:
    - ToolParameter: Parameter definition (similar to SkillParameter)
    - ToolDefinition: Tool metadata and schema
    - ToolRegistry: Tool registration and lookup
    - @tool decorator: Simplified tool definition

Example:
    >>> @tool(
    ...     name="Read",
    ...     description="Read file contents",
    ...     parameters=[
    ...         ToolParameter("file_path", "string", "Path to file", required=True),
    ...     ]
    ... )
    ... async def read_file(file_path: str, **context) -> str:
    ...     async with aiofiles.open(file_path, 'r') as f:
    ...         return await f.read()
    ...
    >>> registry = ToolRegistry()
    >>> registry.register_decorated(read_file)
    >>> result = await registry.execute("Read", {"file_path": "/tmp/test.txt"})
"""

__layer__ = 0  # Infrastructure Layer
__role__ = "ISA"  # Instruction Set Architecture - tool interface definitions

import asyncio
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, Iterator, List, Literal, Optional, TypeVar

# Type for decorated tool functions
F = TypeVar("F", bound=Callable[..., Any])

ToolCategory = Literal["core", "extension", "skill"]

RESERVED_TOOL_NAMES = frozenset({"Read", "Write", "Edit", "Bash"})

class ToolNameConflictError(Exception):
    """Raised when a tool name conflicts with a reserved name."""
    pass


class ToolExecutionError(Exception):
    """Exception raised when tool execution fails.

    Attributes:
        tool_name: Name of the tool that failed.
        message: Error description.
        original_error: The underlying exception, if any.
    """

    def __init__(
        self,
        tool_name: str,
        message: str,
        original_error: Optional[Exception] = None,
    ):
        self.tool_name = tool_name
        self.message = message
        self.original_error = original_error
        super().__init__(f"Tool '{tool_name}' failed: {message}")


@dataclass
class ToolParameter:
    """Definition of a tool parameter.

    Represents a single parameter that a tool accepts. Parameters are
    converted to JSON Schema format for LLM tool use APIs.

    Attributes:
        name: Parameter name (identifier).
        type: Parameter type (string, integer, number, boolean, array, object).
        description: Human-readable description.
        required: Whether the parameter is required. Defaults to True.
        default: Optional default value.
        enum: Optional list of allowed values.
        items: Optional item type for array parameters.
        properties: Optional property definitions for object parameters.

    Example:
        >>> param = ToolParameter(
        ...     name="file_path",
        ...     type="string",
        ...     description="Absolute path to the file to read",
        ...     required=True,
        ... )
        >>> param.to_json_schema()
        {'type': 'string', 'description': 'Absolute path to the file to read'}
    """

    name: str
    type: str  # string, integer, number, boolean, array, object
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None
    items: Optional[Dict[str, Any]] = None  # For array types
    properties: Optional[Dict[str, Any]] = None  # For object types

    def to_json_schema(self) -> Dict[str, Any]:
        """Convert parameter to JSON Schema format.

        Returns:
            Dictionary conforming to JSON Schema specification.
        """
        # Map tool types to JSON Schema types
        type_mapping = {
            "string": "string",
            "number": "number",
            "integer": "integer",
            "boolean": "boolean",
            "array": "array",
            "object": "object",
        }

        schema: Dict[str, Any] = {
            "type": type_mapping.get(self.type, self.type),
            "description": self.description,
        }

        if self.enum:
            schema["enum"] = self.enum

        if self.default is not None:
            schema["default"] = self.default

        if self.type == "array" and self.items:
            schema["items"] = self.items

        if self.type == "object" and self.properties:
            schema["properties"] = self.properties

        return schema

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolParameter":
        """Create a ToolParameter from a dictionary.

        Args:
            data: Dictionary with parameter data.

        Returns:
            ToolParameter instance.
        """
        return cls(
            name=data["name"],
            type=data.get("type", "string"),
            description=data.get("description", ""),
            required=data.get("required", True),
            default=data.get("default"),
            enum=data.get("enum"),
            items=data.get("items"),
            properties=data.get("properties"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation.

        Returns:
            Dictionary with all non-None fields.
        """
        result: Dict[str, Any] = {
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "required": self.required,
        }

        if self.enum:
            result["enum"] = self.enum
        if self.default is not None:
            result["default"] = self.default
        if self.items:
            result["items"] = self.items
        if self.properties:
            result["properties"] = self.properties

        return result


@dataclass
class ToolDefinition:
    """Definition of a tool that can be executed by an agent.

    A tool represents a callable function that the LLM can invoke for
    code-related operations. Tools can be converted to various LLM
    tool formats (Claude Tool Use, OpenAI Function Calling).

    Attributes:
        name: Unique tool identifier.
        description: Human-readable description of what the tool does.
        parameters: List of parameter definitions.
        dangerous: If True, requires explicit user permission before execution.

    Example:
        >>> tool_def = ToolDefinition(
        ...     name="Read",
        ...     description="Read file contents from the filesystem",
        ...     parameters=[
        ...         ToolParameter("file_path", "string", "Path to file", required=True),
        ...         ToolParameter("encoding", "string", "File encoding", required=False, default="utf-8"),
        ...     ],
        ...     dangerous=False,
        ... )
        >>> tool_def.to_tool_use_format()
        {'name': 'Read', 'description': '...', 'input_schema': {...}}
    """

    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)
    category: Optional[ToolCategory] = None  # NEW: Tool Classification
    dangerous: bool = False  # If True, requires permission

    roles: Optional[List[str]] = None  # List of roles allowed to use this tool (None = all)

    def get_required_parameters(self) -> List[ToolParameter]:
        """Get all required parameters.

        Returns:
            List of parameters marked as required.
        """
        return [p for p in self.parameters if p.required]

    def get_optional_parameters(self) -> List[ToolParameter]:
        """Get all optional parameters.

        Returns:
            List of parameters not marked as required.
        """
        return [p for p in self.parameters if not p.required]

    def to_tool_use_format(self) -> Dict[str, Any]:
        """Convert to Claude Tool Use API format.

        This format is compatible with Anthropic's Claude API for tool use.

        Returns:
            Dictionary in Claude Tool Use format:
            {
                "name": "tool_name",
                "description": "...",
                "input_schema": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }

    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI Function Calling format.

        This format is compatible with OpenAI's function calling API.

        Returns:
            Dictionary in OpenAI Function format:
            {
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": {
                        "type": "object",
                        "properties": {...},
                        "required": [...]
                    }
                }
            }
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []

        for param in self.parameters:
            properties[param.name] = param.to_json_schema()
            if param.required:
                required.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation.

        Returns:
            Dictionary with all tool definition data.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters],
            "category": self.category,
            "dangerous": self.dangerous,
            "roles": self.roles,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolDefinition":
        """Create a ToolDefinition from a dictionary.

        Args:
            data: Dictionary with tool definition data.

        Returns:
            ToolDefinition instance.
        """
        parameters = [ToolParameter.from_dict(p) for p in data.get("parameters", [])]

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            parameters=parameters,
            category=data.get("category"),
            dangerous=data.get("dangerous", False),
            roles=data.get("roles"),
        )

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"ToolDefinition(name={self.name!r}, "
            f"parameters={len(self.parameters)}, "
            f"category={self.category!r}, "
            f"dangerous={self.dangerous}, "
            f"roles={self.roles})"
        )


class ToolRegistry:
    """Registry for code tools.

    Provides methods to register, lookup, and execute tools. Supports both
    sync and async tool functions.

    Example:
        >>> registry = ToolRegistry()
        >>> registry.register(read_tool_def, read_file_func)
        >>> definitions = registry.get_definitions(format="claude")
        >>> result = await registry.execute("Read", {"file_path": "/tmp/test.txt"})
    """

    def __init__(self) -> None:
        """Initialize an empty tool registry."""
        self._tools: Dict[str, tuple[ToolDefinition, Callable[..., Any]]] = {}

    def register(
        self,
        definition: ToolDefinition,
        func: Callable[..., Any],
    ) -> None:
        """Register a tool with its implementation.

        Args:
            definition: Tool definition with metadata and schema.
            func: Implementation function (sync or async).

        Raises:
            ValueError: If a tool with the same name is already registered.
            ToolNameConflictError: If a skill tool uses a reserved name.
        """
        # Check for reserved names (only for Skill tools)
        # Note: Core tools themselves also call register(), so we only check if category is skill
        if definition.category == "skill" and definition.name in RESERVED_TOOL_NAMES:
            raise ToolNameConflictError(
                f"Skill tool '{definition.name}' conflicts with reserved Core tool name"
            )

        if definition.name in self._tools:
            # Allow overwriting for now to support dynamic registration in tests/demos
            # raise ValueError(f"Tool '{definition.name}' is already registered")
            pass
        self._tools[definition.name] = (definition, func)

    def register_decorated(self, func: Callable[..., Any]) -> None:
        """Register a function decorated with @tool.

        Args:
            func: Function decorated with the @tool decorator.

        Raises:
            ValueError: If the function is not decorated with @tool.
        """
        if not hasattr(func, "_tool_definition"):
            raise ValueError(f"Function '{func.__name__}' is not decorated with @tool")
        definition: ToolDefinition = getattr(func, "_tool_definition")
        self.register(definition, func)

    def unregister(self, name: str) -> Optional[ToolDefinition]:
        """Unregister a tool by name.

        Args:
            name: Tool name to unregister.

        Returns:
            The removed tool definition, or None if not found.
        """
        entry = self._tools.pop(name, None)
        return entry[0] if entry else None

    def clear(self) -> None:
        """Unregister all tools."""
        self._tools.clear()

    def get(self, name: str) -> Optional[tuple[ToolDefinition, Callable[..., Any]]]:
        """Get tool definition and function by name.

        Args:
            name: Tool name to look up.

        Returns:
            Tuple of (ToolDefinition, function) if found, None otherwise.
        """
        return self._tools.get(name)

    def get_definition(self, name: str) -> Optional[ToolDefinition]:
        """Get tool definition by name.

        Args:
            name: Tool name to look up.

        Returns:
            ToolDefinition if found, None otherwise.
        """
        entry = self._tools.get(name)
        return entry[0] if entry else None

    def get_function(self, name: str) -> Optional[Callable[..., Any]]:
        """Get tool function by name.

        Args:
            name: Tool name to look up.

        Returns:
            Tool function if found, None otherwise.
        """
        entry = self._tools.get(name)
        return entry[1] if entry else None

    def list_tools(self) -> List[str]:
        """List all registered tool names.

        Returns:
            List of tool names.
        """
        return list(self._tools.keys())

    def list_by_category(self, category: ToolCategory) -> List[str]:
        """List tool names by category.
        
        Args:
            category: The tool category to filter by ("core", "extension", "skill").
            
        Returns:
            List of tool names.
        """
        return [
            name for name, (defn, _) in self._tools.items()
            if defn.category == category
        ]

    def get_categories_summary(self) -> Dict[ToolCategory, List[str]]:
        """Get a summary of tools grouped by category.
        
        Returns:
            Dictionary mapping categories to lists of tool names.
        """
        summary = {"core": [], "extension": [], "skill": [], "uncategorized": []}
        for name, (defn, _) in self._tools.items():
            cat = defn.category if defn.category else "uncategorized"
            if cat not in summary:
                summary[cat] = []
            summary[cat].append(name)
        return summary

    def list_dangerous_tools(self) -> List[str]:
        """List tools marked as dangerous.

        Returns:
            List of dangerous tool names.
        """
        return [name for name, (defn, _) in self._tools.items() if defn.dangerous]

    def get_definitions(
        self,
        format: str = "claude",
        role: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all permissible tool definitions in specified format.

        Args:
            format: Target format ("claude" or "openai").
            role: Optional role name to filter tools by. 
                  If provided, only returns tools where role is in tool.roles
                  or tool.roles is None (all).

        Returns:
            List of tool definitions in the specified format.

        Raises:
            ValueError: If format is not recognized.
        """
        definitions = []
        for defn, _ in self._tools.values():
            # Role check
            if role and defn.roles and role not in defn.roles:
                continue

            definitions.append(defn)

        if format == "claude":
            return [defn.to_tool_use_format() for defn in definitions]
        elif format == "openai":
            return [defn.to_openai_format() for defn in definitions]
        else:
            raise ValueError(f"Unknown format: {format}. Use 'claude' or 'openai'.")

    async def execute(
        self,
        name: str,
        params: Dict[str, Any],
        **context: Any,
    ) -> Any:
        """Execute a tool by name with parameters.

        Handles both sync and async tool functions transparently.

        Args:
            name: Tool name to execute.
            params: Parameters to pass to the tool.
            **context: Additional context (e.g., session, permissions).

        Returns:
            Tool execution result.

        Raises:
            ToolExecutionError: If tool not found or execution fails.
        """
        entry = self._tools.get(name)
        if entry is None:
            raise ToolExecutionError(name, f"Tool '{name}' not found in registry")

        definition, func = entry

        try:
            # Merge params with context
            call_kwargs = {**params, **context}

            # Check if function is async
            if asyncio.iscoroutinefunction(func):
                result = await func(**call_kwargs)
            else:
                # Run sync function in executor to avoid blocking
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: func(**call_kwargs),
                )
            return result

        except ToolExecutionError:
            # Re-raise ToolExecutionError as-is
            raise
        except Exception as e:
            raise ToolExecutionError(
                name,
                str(e),
                original_error=e,
            ) from e

    def is_dangerous(self, name: str) -> bool:
        """Check if a tool is marked as dangerous.

        Args:
            name: Tool name to check.

        Returns:
            True if tool is dangerous, False otherwise.
        """
        entry = self._tools.get(name)
        return entry[0].dangerous if entry else False

    def __len__(self) -> int:
        """Return number of registered tools."""
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def __iter__(self) -> Iterator[str]:
        """Iterate over tool names."""
        return iter(self._tools.keys())


def tool(
    name: str,
    description: str,
    parameters: Optional[List[ToolParameter]] = None,
    category: Optional[ToolCategory] = None,
    dangerous: bool = False,
) -> Callable[[F], F]:
    """Decorator to define a tool function.

    Attaches a ToolDefinition to the decorated function, making it easy
    to register with a ToolRegistry.

    Args:
        name: Unique tool identifier.
        description: Human-readable description.
        parameters: List of ToolParameter definitions.
        category: Tool category ("core", "extension", "skill").
        dangerous: If True, requires permission before execution.

    Returns:
        Decorator function.

    Example:
        >>> @tool(
        ...     name="Read",
        ...     description="Read file contents",
        ...     parameters=[...],
        ...     category="core"
        ... )
        ... async def read_file(...)
    """

    def decorator(func: F) -> F:
        # Create tool definition
        definition = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters or [],
            category=category,
            dangerous=dangerous,
        )

        # Attach definition to function
        func._tool_definition = definition  # type: ignore

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        # Also attach to wrapper for async functions
        wrapper._tool_definition = definition  # type: ignore

        # Return original function to preserve async nature
        return func

    return decorator


# Global default registry
_default_registry: Optional[ToolRegistry] = None


def get_default_registry() -> ToolRegistry:
    """Get the default global tool registry.

    Creates the registry on first access (lazy initialization).

    Returns:
        The default ToolRegistry instance.
    """
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry


def register_tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Register a decorated tool function with the default registry.

    Convenience decorator that combines @tool with registration.

    Args:
        func: Function decorated with @tool.

    Returns:
        The original function.

    Example:
        >>> @register_tool
        ... @tool(name="MyTool", description="...")
        ... async def my_tool(**kwargs):
        ...     pass
    """
    get_default_registry().register_decorated(func)
    return func
