"""
Nimbus Persistence Layer

Defines Pydantic models for serialization/deserialization of session state.
This replaces Pickle with a safe, schema-validated JSON format.

Strictly follows "No Pickle" rule from Architecture Committee.
"""

import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# =============================================================================
# Core Data Models
# =============================================================================

class MessageModel(BaseModel):
    """Pydantic model for Message"""
    role: Literal["system", "user", "assistant", "tool"]
    content: Any  # str or list of content blocks
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    meta: Dict[str, Any] = Field(default_factory=dict)

class PinnedContextModel(BaseModel):
    """Pydantic model for PinnedContext"""
    system_rules: str = ""
    workspace_info: str = ""
    env_state: str = ""
    capabilities: str = ""
    custom_anchors: Dict[str, str] = Field(default_factory=dict)
    version: str = "1.0"

class StackFrameModel(BaseModel):
    """Pydantic model for StackFrame"""
    frame_id: str
    goal: str = ""
    messages: List[MessageModel] = Field(default_factory=list)
    state: Literal["ACTIVE", "SUSPENDED", "COMPLETED", "FAILED"] = "ACTIVE"
    parent_frame_id: Optional[str] = None
    result: Any = None
    created_at: float
    meta: Dict[str, Any] = Field(default_factory=dict)

class MemorySnapshotModel(BaseModel):
    """Full snapshot of MMU state"""
    process_id: str = ""
    pinned_context: Optional[PinnedContextModel] = None
    stack: List[StackFrameModel] = Field(default_factory=list)
    tool_markers: Dict[str, Any] = Field(default_factory=dict) # Simplified for now
    frame_discardable: Dict[str, List[str]] = Field(default_factory=dict) # Sets are not JSON serializable by default

class FSMExecutionStateModel(BaseModel):
    """Snapshot of vCPU ExecutionState"""
    iteration_count: int
    max_iterations: int
    consecutive_thoughts: int
    consecutive_errors: int
    consecutive_empty_responses: int
    compaction_count: int
    max_compactions: int
    tool_failure_counts: Dict[str, int]
    path_not_found_count: int
    doom_loop_count: int = 0

class SessionCheckpointModel(BaseModel):
    """Top-level session checkpoint"""
    schema_version: int = 1
    session_id: str
    timestamp: float = Field(default_factory=time.time)
    step_index: int # The vCPU iteration count

    # Core States
    execution_state: FSMExecutionStateModel
    memory_snapshot: MemorySnapshotModel

    # Metadata
    reason: str = "periodic" # periodic, interruption, error
    can_resume: bool = True
