"""Common types and schemas for Sub-agent Result Contract."""

from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class SubAgentStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    ABORTED = "aborted"

class Artifact(BaseModel):
    path: str
    description: Optional[str] = None
    type: Optional[str] = None  # e.g., "file", "url", "data"

class SubAgentResult(BaseModel):
    status: SubAgentStatus
    summary: str = Field(..., description="High-level summary of the execution (50-100 words)")
    key_findings: List[str] = Field(default_factory=list, description="Key conclusions or findings")
    artifacts: List[Artifact] = Field(default_factory=list, description="Important files or data produced")
    files_touched: List[str] = Field(default_factory=list, description="List of modified or created files")
    todos_completed: List[str] = Field(default_factory=list, description="Completed task points")
    todos_remaining: List[str] = Field(default_factory=list, description="Remaining or blocked task points")
    errors: List[str] = Field(default_factory=list, description="Encountered errors or blockers")
    scratchpad_path: Optional[str] = None
    
    def to_parent_text(self) -> str:
        """Format the result as a readable string for the parent agent."""
        lines = [
            f"Sub-agent status: {self.status.value.upper()}",
            f"**Summary:** {self.summary}",
        ]
        
        if self.key_findings:
            lines.append("\n**Key Findings:**")
            lines.extend([f"  - {f}" for f in self.key_findings])
            
        if self.artifacts:
            lines.append("\n**Artifacts:**")
            lines.extend([f"  - {a.path} ({a.description or 'no description'})" for a in self.artifacts])
            
        if self.files_touched:
            lines.append(f"\n**Files touched:** {', '.join(self.files_touched)}")
            
        if self.todos_remaining:
            lines.append("\n**Remaining Todos:**")
            lines.extend([f"  - {t}" for t in self.todos_remaining])
            
        if self.errors:
            lines.append("\n**Errors:**")
            lines.extend([f"  - {e}" for e in self.errors])
            
        if self.scratchpad_path:
            lines.append(f"\n**Full scratchpad:** `{self.scratchpad_path}`")
            
        return "\n".join(lines)
