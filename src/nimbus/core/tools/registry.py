"""
Tool Registry — Registration, lookup, and schema export.

The registry is where tool definitions (name + parameters + handler)
are stored and queried. The Gate uses it to execute tools; the Adapter
uses it to export schemas to the LLM API.
"""

import asyncio
import inspect
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


@dataclass
class ToolParameter:
    """Single parameter of a tool."""
    name: str
    type: str  # string, integer, number, boolean, array, object
    description: str
    required: bool = True
    enum: Optional[List[str]] = None
    items: Optional[Dict[str, str]] = None

    def to_json_schema(self) -> Dict[str, Any]:
        schema: Dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            schema["enum"] = self.enum
        if self.items:
            schema["items"] = self.items
        return schema


@dataclass
class ToolDefinition:
    """Metadata for a registered tool."""
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)

    def to_openai_format(self) -> Dict[str, Any]:
        """Export as OpenAI function calling schema."""
        properties = {}
        required = []
        for p in self.parameters:
            properties[p.name] = p.to_json_schema()
            if p.required:
                required.append(p.name)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }

    def to_anthropic_format(self) -> Dict[str, Any]:
        """Export as Anthropic tool use schema."""
        properties = {}
        required = []
        for p in self.parameters:
            properties[p.name] = p.to_json_schema()
            if p.required:
                required.append(p.name)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


class ToolRegistry:
    """Register, lookup, and execute tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, tuple[ToolDefinition, Callable[..., Any]]] = {}

    def register(self, definition: ToolDefinition, func: Callable[..., Any]) -> None:
        self._tools[definition.name] = (definition, func)

    def register_decorated(self, func: Callable[..., Any]) -> None:
        """Register a function decorated with @tool."""
        if not hasattr(func, "_tool_definition"):
            raise ValueError(f"'{func.__name__}' is not decorated with @tool")
        self.register(func._tool_definition, func)

    def get(self, name: str) -> Optional[tuple[ToolDefinition, Callable[..., Any]]]:
        return self._tools.get(name)

    def get_function(self, name: str) -> Optional[Callable[..., Any]]:
        entry = self._tools.get(name)
        return entry[1] if entry else None

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    def get_schemas(self, format: str = "openai") -> List[Dict[str, Any]]:
        """Export all tool schemas in the specified format."""
        if format == "openai":
            return [defn.to_openai_format() for defn, _ in self._tools.values()]
        elif format == "anthropic":
            return [defn.to_anthropic_format() for defn, _ in self._tools.values()]
        raise ValueError(f"Unknown format: {format}. Use 'openai' or 'anthropic'.")

    async def execute(self, name: str, params: Dict[str, Any]) -> Any:
        """Execute a tool by name. Handles sync/async transparently."""
        entry = self._tools.get(name)
        if entry is None:
            raise KeyError(f"Tool '{name}' not found")
        _, func = entry
        if asyncio.iscoroutinefunction(func):
            return await func(**params)
        return await asyncio.get_event_loop().run_in_executor(None, lambda: func(**params))

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


def tool(name: str, description: str, parameters: Optional[List[ToolParameter]] = None) -> Callable[[F], F]:
    """Decorator to define a tool. Attaches ToolDefinition to the function."""
    def decorator(func: F) -> F:
        func._tool_definition = ToolDefinition(  # type: ignore
            name=name,
            description=description,
            parameters=parameters or [],
        )
        return func
    return decorator
