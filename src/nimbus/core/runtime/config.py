import dataclasses

@dataclasses.dataclass
class VCPUConfig:
    """Configuration for vCPU."""
    max_iterations: int = 50
    default_timeout: int = 60
    max_consecutive_thoughts: int = 8
    max_consecutive_empty_responses: int = 3
    max_hallucinations: int = 3
    llm_call_timeout: float = 300.0
    max_context_tokens: int = 100000
    goal_max_length: int = 4000
    emit_step_events: bool = True
    pin_goal: bool = True
    compact_on_limit: bool = True
    max_compactions: int = 10
    dry_run: bool = False
