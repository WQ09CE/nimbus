"""OpenNotebook Skills.

This module provides:
1. Builtin skill functions (chat, search, summarize, rag, draft)
2. Skill definition schema for loading skills from Markdown files
3. Skill loader for discovering and loading skills
4. Skill validator for validating skill definitions
"""

from .chat import chat, create_chat_skill
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

__all__ = [
    # Chat skills
    "chat",
    "create_chat_skill",
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
]
