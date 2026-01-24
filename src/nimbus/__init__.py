"""Nimbus - A code exploration agent framework.

Nimbus provides a minimal but complete agent framework for building
code exploration AI assistants with:
- Read/Glob/Grep tools for code exploration
- DAG-based parallel task execution
- Memory for conversation context
- Extensible skill system
- Async execution

Example:
    from nimbus.core import CodeAgent

    # Basic usage
    agent = CodeAgent(llm_client=your_llm_client)
    response = await agent.run("Find all Python files in src/")
    print(response.text)

    # With custom workspace
    from pathlib import Path
    agent = CodeAgent(
        llm_client=your_llm_client,
        workspace=Path("/path/to/project"),
        planner_type="dag",
    )
    response = await agent.run("Search for 'TODO' comments")
"""

from .core import (
    CodeAgent,
    NotebookAgent,  # Backward compatibility alias
    SimpleMemory,
    SimplePlanner,
    SimpleExecutor,
    Task,
    TaskType,
    Plan,
    AgentResponse,
    NotebookResponse,  # Backward compatibility alias
)

from .domain import (
    SourceType,
    Chunk,
    Source,
    Citation,
    Note,
    Artifact,
    NotebookContext,
)

from .services import (
    IngestionService,
    RetrievalService,
)

from .llm import (
    GeminiClient,
)

__version__ = "0.3.0"
__all__ = [
    # Core
    "CodeAgent",
    "NotebookAgent",  # Backward compatibility alias
    "SimpleMemory",
    "SimplePlanner",
    "SimpleExecutor",
    "Task",
    "TaskType",
    "Plan",
    "AgentResponse",
    "NotebookResponse",  # Backward compatibility alias
    # Domain
    "SourceType",
    "Chunk",
    "Source",
    "Citation",
    "Note",
    "Artifact",
    "NotebookContext",
    # Services
    "IngestionService",
    "RetrievalService",
    # LLM Providers
    "GeminiClient",
]
