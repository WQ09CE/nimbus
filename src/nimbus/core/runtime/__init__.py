"""
Nimbus v2 Runtime - Decoder and execution components.

Components:
- VCPU: Core execution engine (Think-Act-Observe loop)
- DoomLoopDetector: Detects infinite tool call loops
- FSMExecutionState: Centralized execution state management
- FailureReporter: Generates user-friendly failure reports
- ErrorHandlerRegistry: Smart error recovery handlers
"""

from nimbus.core.runtime.decoder import InstructionDecoder
from nimbus.core.runtime.doom_loop import DoomLoopDetector, DoomLoopResult
from nimbus.core.runtime.error_handler import ErrorHandlerRegistry, RecoveryAction
from nimbus.core.runtime.states import FSMExecutionState
from nimbus.core.runtime.failure_reporter import FailureContext, FailureReporter
from nimbus.core.runtime.vcpu import VCPU
from nimbus.core.runtime.config import VCPUConfig

__all__ = [
    # Core
    "VCPU",
    "VCPUConfig",
    "InstructionDecoder",
    # Extracted components
    "DoomLoopDetector",
    "DoomLoopResult",
    "FSMExecutionState",
    "FailureReporter",
    "FailureContext",
    "ErrorHandlerRegistry",
    "RecoveryAction",
]
