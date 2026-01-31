"""OpenNotebook Skills.

Architecture Layer: 2 (Application)
Von Neumann Role: Libraries (libc, shared libraries)

In the Agent OS architecture, skills serve as user-space libraries that
provide reusable functionality to applications:
- synthesize -> printf/sprintf (output formatting)
- search -> DNS/network libraries (external lookups)
- summarize -> compression libraries (data reduction)
- rag -> database client libraries (persistent storage access)

Skills are higher-level abstractions built on top of tools (syscalls),
similar to how libc wraps syscalls with convenient APIs.

This module provides:
1. Builtin skill functions (synthesize, search, summarize, rag, draft)
2. Skill definition schema for loading skills from Markdown files
3. Skill loader for discovering and loading skills
4. Skill validator for validating skill definitions
"""

__layer__ = 2  # Application Layer
__role__ = "Libraries"  # Shared libraries and utilities

from .synthesize import synthesize, create_synthesize_skill
from .search import web_search, search_with_context
from .summarize import summarize_text, extract_keywords, summarize_with_keywords
from .rag import RAGResult, create_rag_skill
from .draft import create_draft_skill

# Skill loading system
from .schema import (
    SkillParameter,
    SkillDefinition,
    SkillRegistry,
)
from .loader import (
    SkillLoader,
    SkillLoadError,
    create_skill_loader,
)
from .validator import (
    SkillValidator,
    ValidationError,
    validate_skill,
    validate_skill_file,
)

# MCP Protocol adapter
from .mcp import (
    # Conversion functions
    skill_to_mcp_tool,
    mcp_tool_to_skill,
    skills_to_mcp_tools,
    mcp_tools_to_skills,
    # Transport
    MCPTransport,
    StdioTransport,
    HTTPTransport,
    # Client
    MCPClient,
    MCPTool,
    MCPServerInfo,
    MCPToolProvider,
    # JSON-RPC
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    # Convenience functions
    create_mcp_client_stdio,
    create_mcp_client_http,
)

__all__ = [
    # Synthesize skills
    "synthesize",
    "create_synthesize_skill",
    # Search skills
    "web_search",
    "search_with_context",
    # Summarize skills
    "summarize_text",
    "extract_keywords",
    "summarize_with_keywords",
    # RAG skills
    "RAGResult",
    "create_rag_skill",
    # Draft skills
    "create_draft_skill",
    # Skill schema
    "SkillParameter",
    "SkillDefinition",
    "SkillRegistry",
    # Skill loader
    "SkillLoader",
    "SkillLoadError",
    "create_skill_loader",
    # Skill validator
    "SkillValidator",
    "ValidationError",
    "validate_skill",
    "validate_skill_file",
    # MCP Protocol adapter
    "skill_to_mcp_tool",
    "mcp_tool_to_skill",
    "skills_to_mcp_tools",
    "mcp_tools_to_skills",
    "MCPTransport",
    "StdioTransport",
    "HTTPTransport",
    "MCPClient",
    "MCPTool",
    "MCPServerInfo",
    "MCPToolProvider",
    "JSONRPCError",
    "JSONRPCRequest",
    "JSONRPCResponse",
    "create_mcp_client_stdio",
    "create_mcp_client_http",
]
