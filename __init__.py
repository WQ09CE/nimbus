"""OpenNotebook - A simple notebook agent framework.

OpenNotebook provides a minimal but complete agent framework for building
notebook-style AI assistants with:
- Conversation memory with pinned file context
- LLM-based task planning
- Extensible skill system
- Async execution
- Document ingestion and chunking
- RAG-based document Q&A
- Artifact generation (outlines, summaries, notes)

Example:
    from nimbus.core import NotebookAgent
    from nimbus.domain import NotebookContext, Source
    from nimbus.services import IngestionService, RetrievalService

    # Basic usage
    agent = NotebookAgent(llm_client=your_llm_client)
    response = await agent.run("Hello!")
    print(response.text)

    # Notebook mode with RAG
    ingestion = IngestionService()
    retrieval = RetrievalService()
    agent.setup_retrieval(retrieval)
    agent.register_notebook_skills()

    source = ingestion.process_text("Your document content...", "doc.txt")
    agent.add_source(source)
    response = await agent.run("What does the document say about X?")
"""

from .core import (
    NotebookAgent,
    SimpleMemory,
    SimplePlanner,
    SimpleExecutor,
    Task,
    TaskType,
    Plan,
    NotebookResponse,
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

__version__ = "0.2.0"
__all__ = [
    # Core
    "NotebookAgent",
    "SimpleMemory",
    "SimplePlanner",
    "SimpleExecutor",
    "Task",
    "TaskType",
    "Plan",
    "NotebookResponse",
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
]
