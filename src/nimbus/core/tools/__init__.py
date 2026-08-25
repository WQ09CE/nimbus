"""Nimbus Next Tools — 5 core tools for agent interaction."""

from typing import Dict, Optional

from ..protocol import ToolTraits

_BUILTIN_TRAITS: Optional[Dict[str, ToolTraits]] = None


def builtin_tool_traits() -> Dict[str, ToolTraits]:
    """Traits declared by the builtin tool decorators, keyed by tool name.

    Imports lazily: spawn_agent pulls in the agent stack, which must not load
    as a side effect of importing this package (KernelGate imports it).
    """
    global _BUILTIN_TRAITS
    if _BUILTIN_TRAITS is None:
        from .bash import bash_command
        from .edit import edit_file
        from .glob import glob_search
        from .grep import grep_search
        from .read import read_file
        from .spawn_agent import spawn_agent
        from .submit_result import submit_result
        from .update_plan import update_plan
        from .write import write_file

        _BUILTIN_TRAITS = {
            fn._tool_definition.name: fn._tool_definition.traits  # type: ignore[attr-defined]
            for fn in (
                read_file, write_file, edit_file, bash_command, grep_search,
                glob_search, spawn_agent, submit_result, update_plan,
            )
        }
    return _BUILTIN_TRAITS
