from pathlib import Path

agentos_path = Path("src/nimbus/agentos.py")
content = agentos_path.read_text()

# Strip my fake create_agent_os
content = content.replace('def create_agent_os(llm_client: Any, tools: Optional[Dict[str, Callable]] = None, config: Optional[AgentOSConfig] = None) -> AgentOS:\n    """Helper function to create an AgentOS instance."""\n    return AgentOS(llm_client=llm_client, tools=tools, config=config)', "")

# The missing methods in AgentOS
missing_methods = """
    def _create_gate(
        self,
        pid: str,
        role: str,
        local_tools: Optional[Dict[str, Callable]] = None,
        write_filter: Optional[Callable[[str], bool]] = None
    ) -> "KernelGate":
        # Combine with local tools provided by spawn()
        all_funcs = self._composite_tools.get_all_funcs()
        if local_tools:
            all_funcs.update(local_tools)

        from nimbus.os.gate import KernelGate
        return KernelGate(
            pid=pid,
            tool_executor=self._composite_tools,
            event_stream=self._events,
            default_timeout=self.config.default_timeout,
            local_tools=all_funcs,
            write_filter=write_filter,
        )

    def _emit_event(self, event_type: str, pid: str, data: Dict[str, Any]) -> None:
        self._events.emit(
            Event(
                type=event_type,  # type: ignore
                pid=pid,
                data=data,
            )
        )
"""

# Find where to insert them (before Process Facade)
needle = "    # =========================================================================\n    # Process Facade"
content = content.replace(needle, missing_methods + "\n" + needle)

# Append create_agent_os export
content += """
# =============================================================================
# Factory Functions (re-exported from nimbus.orchestration.bootstrap)
# =============================================================================

# Re-export for backward compatibility
from nimbus.orchestration.bootstrap import create_agent_os  # noqa: F401
"""

agentos_path.write_text(content)
print("Restored missing methods")
