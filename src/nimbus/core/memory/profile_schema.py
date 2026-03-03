import time
from typing import Any, Dict, List, Literal, Optional
from datetime import datetime

from pydantic import BaseModel, Field

class ProfileEntityModel(BaseModel):
    """Represents a structured semantic memory entity."""
    key: str
    value: str
    entity_type: Literal["preference", "tech_stack", "project_context", "decision", "other"] = "other"
    confidence: Literal["verified", "inferred", "uncertain"] = "inferred"
    source_session_id: Optional[str] = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_accessed: float = Field(default_factory=time.time)
    access_count: int = 0
    tags: List[str] = Field(default_factory=list)
