"""
Nimbus v2.0 Core - Agent OS Architecture

This module contains the v2.0 implementation of the Agent OS architecture,
featuring a unified instruction set (ActionIR), structured results (ToolResult),
and centralized side-effect control (KernelGate).

Key Components:
- protocol: ActionIR, ToolResult, Fault, Event (ISA/ABI definitions)
- runtime/decoder: LLM output → ActionIR translation with hallucination firewall
- runtime/vcpu: Virtual CPU for Think-Act-Observe loop
- os/gate: Unified syscall entry point with permission, timeout, observability
- scheduler: DAG task scheduler with dependency resolution
- memory/mmu: Memory management with call stack support
- agentos: Top-level integration layer (AgentOS)

Usage:
    from nimbus.v2 import AgentOS, AgentOSConfig, create_agent_os

    # Quick start
    os = create_agent_os(llm_client=my_llm, tools={"Read": read_fn})
    result = await os.run("Find all Python files")

    # Full control
    config = AgentOSConfig(max_processes=5, default_timeout=60.0)
    os = AgentOS(llm_client=my_llm, tools={}, config=config)
    os.register_tool("Bash", bash_fn, description="Execute shell commands")
    result = await os.run("Run tests")
"""

__version__ = "2.0.0-alpha"

# Core protocol types
from nimbus.v2.core.protocol import (
    ActionIR,
    ActionKind,
    ToolResult,
    ResultStatus,
    Fault,
    FaultDomain,
    FaultCode,
    Event,
    EventType,
    IPCMessage,
    ArtifactRef,
)

# Runtime components
from nimbus.v2.core.runtime.decoder import InstructionDecoder
from nimbus.v2.core.runtime.vcpu import VCPU, VCPUConfig, LLMClient, StepResult

# Memory management
from nimbus.v2.core.memory.mmu import MMU, MMUConfig
from nimbus.v2.core.memory.context import PinnedContext, StackFrame, Message

# OS components
from nimbus.v2.os.gate import (
    KernelGate,
    SimplePermissionManager,
    SimpleEventStream,
    SimpleIPCBus,
)

# Scheduler
from nimbus.v2.core.scheduler import (
    Scheduler,
    SchedulerConfig,
    DAG,
    Task,
    TaskSpec,
    TaskState,
    EventStream,
    create_dag,
    create_linear_dag,
)

# Top-level AgentOS
from nimbus.v2.agentos import (
    AgentOS,
    AgentOSConfig,
    Process,
    ProcessState,
    ToolRegistry,
    create_agent_os,
)

# LLM Clients
from nimbus.v2.llm import GeminiV2Client, GeminiV2Response

# Native Tools
from nimbus.v2.tools import (
    register_default_tools,
    get_all_tools,
    get_tool,
    iterate_tools,
)

__all__ = [
    # Version
    "__version__",
    # Protocol
    "ActionIR",
    "ActionKind",
    "ToolResult",
    "ResultStatus",
    "Fault",
    "FaultDomain",
    "FaultCode",
    "Event",
    "EventType",
    "IPCMessage",
    "ArtifactRef",
    # Runtime
    "InstructionDecoder",
    "VCPU",
    "VCPUConfig",
    "LLMClient",
    "StepResult",
    # Memory
    "MMU",
    "MMUConfig",
    "PinnedContext",
    "StackFrame",
    "Message",
    # OS
    "KernelGate",
    "SimplePermissionManager",
    "SimpleEventStream",
    "SimpleIPCBus",
    # Scheduler
    "Scheduler",
    "SchedulerConfig",
    "DAG",
    "Task",
    "TaskSpec",
    "TaskState",
    "EventStream",
    "create_dag",
    "create_linear_dag",
    # AgentOS
    "AgentOS",
    "AgentOSConfig",
    "Process",
    "ProcessState",
    "ToolRegistry",
    "create_agent_os",
    # LLM Clients
    "GeminiV2Client",
    "GeminiV2Response",
    # Native Tools
    "register_default_tools",
    "get_all_tools",
    "get_tool",
    "iterate_tools",
]
