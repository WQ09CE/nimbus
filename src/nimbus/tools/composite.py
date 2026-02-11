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

    def get_definitions(self, format: str = "claude", role: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get definitions from all registries."""
        all_defs = []
        for registry in self.registries:
            all_defs.extend(registry.get_definitions(format=format, role=role))
        return all_defs

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
