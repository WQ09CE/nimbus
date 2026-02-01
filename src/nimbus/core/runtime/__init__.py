"""
Nimbus v2 Runtime - Decoder and execution components.

Components:
- VCPU: Core execution engine (Think-Act-Observe loop)
- DoomLoopDetector: Detects infinite tool call loops
- ExecutionState: Centralized execution state management
- FailureReporter: Generates user-friendly failure reports
- ErrorHandlerRegistry: Smart error recovery handlers
"""

from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.doom_loop import DoomLoopDetector, DoomLoopResult
from nimbus.core.runtime.error_handler import ErrorHandlerRegistry, RecoveryAction
from nimbus.core.runtime.execution_state import ExecutionState
from nimbus.core.runtime.failure_reporter import FailureContext, FailureReporter
from nimbus.core.runtime.vcpu import VCPU, LLMClient, StepResult, VCPUConfig

__all__ = [
    # Core
    "VCPU",
    "VCPUConfig",
    "StepResult",
    "LLMClient",
    "InstructionDecoder",
    # Extracted components
    "DoomLoopDetector",
    "DoomLoopResult",
    "ExecutionState",
    "FailureReporter",
    "FailureContext",
    "ErrorHandlerRegistry",
    "RecoveryAction",
]
