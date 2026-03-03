import time
import uuid
from typing import List
from pydantic import BaseModel, Field

class StrategyModel(BaseModel):
    """Represents a procedural memory: a condition-to-action agent strategy."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    condition: str
    action: str
    success_rate: float = 1.0
    use_count: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    last_used: float = Field(default_factory=time.time)
    tags: List[str] = Field(default_factory=list)
