"""Nimbus - AI Agent Framework with vCPU-based process model.

Nimbus v2 provides a production-ready agent framework with:
- vCPU + Process model for robust execution
- pi-ai integration for multi-provider LLM support
- Tool system (Read, Write, Edit, Glob, Grep, Bash)
- SSE streaming API
- Web UI

Example:
    from nimbus.v2 import create_agent_os
    
    agent_os = create_agent_os(
        llm_client=your_llm_client,
        tools=tools,
    )
    result = await agent_os.run("Find all Python files")

For legacy v1 modules, see nimbus.legacy
"""

# v2 架构 - 主要导出
from .v2 import (
    AgentOS,
    AgentOSConfig,
    create_agent_os,
)

__version__ = "0.4.0"
__all__ = [
    # v2 核心
    "AgentOS",
    "AgentOSConfig", 
    "create_agent_os",
]
