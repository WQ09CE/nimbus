from typing import Any, Dict, Optional, Callable, List
from nimbus.tools.base import ToolRegistry, ToolDefinition

class CompositeToolRegistry:
    """
    A unified view over multiple ToolRegistries (Core/Extension + Skill).
    """
    def __init__(self, registries: List[ToolRegistry]):
        self.registries = registries

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Execute a tool by searching all registries."""
        for registry in self.registries:
            if tool_name in registry:
                return await registry.execute(tool_name, args)
        raise ValueError(f"Tool '{tool_name}' not found in any registry")

    def get_definitions(self, format: str = "openai") -> List[Dict[str, Any]]:
        """Get all tool definitions across all registries."""
        definitions = []
        for registry in self.registries:
            definitions.extend(registry.get_definitions(format=format))
        return definitions

    def get_all_funcs(self) -> Dict[str, Callable]:
        """Get a dictionary of all registered functions across all registries."""
        funcs = {}
        for registry in self.registries:
            funcs.update(registry.get_all_funcs())
        return funcs

    def list_tools(self) -> List[str]:
        """List all tools."""
        tools = []
        for registry in self.registries:
            tools.extend(registry.list_tools())
        return tools

    def get_definition(self, name: str) -> Optional[ToolDefinition]:
        for registry in self.registries:
            defn = registry.get_definition(name)
            if defn:
                return defn
        return None
