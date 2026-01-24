"""Domain models for OpenNotebook."""

from pydantic import BaseModel, Field
from typing import List, Dict, Any
from datetime import datetime
from enum import Enum


class SourceType(str, Enum):
    """Type of knowledge source."""
    PDF = "pdf"
    TEXT = "text"
    URL = "url"
    AUDIO = "audio"


class Chunk(BaseModel):
    """Document chunk for retrieval."""
    id: str
    source_id: str
    content: str
    index: int  # Position in source document
    metadata: Dict[str, Any] = {}


class Source(BaseModel):
    """Knowledge source (PDF/webpage/audio)."""
    id: str
    title: str
    source_type: SourceType
    content: str  # Full text content
    chunks: List[Chunk] = []
    metadata: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.now)


class Citation(BaseModel):
    """Citation reference."""
    chunk_id: str
    source_id: str
    source_title: str
    text: str  # Quoted text snippet


class Note(BaseModel):
    """User note."""
    id: str
    content: str
    citations: List[Citation] = []
    created_at: datetime = Field(default_factory=datetime.now)


class Artifact(BaseModel):
    """Generated artifact (outline, summary, table, etc.)."""
    id: str
    artifact_type: str  # outline, summary, table, etc.
    title: str
    data: Dict[str, Any]
    citations: List[Citation] = []


class NotebookContext(BaseModel):
    """Notebook context containing all sources, notes, and artifacts."""
    notebook_id: str
    title: str = "Untitled Notebook"
    sources: List[Source] = []
    notes: List[Note] = []
    artifacts: List[Artifact] = []
    active_source_ids: List[str] = []  # Currently active sources
