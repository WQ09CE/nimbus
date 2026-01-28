"""
Nimbus v2.0 Core - Agent OS Architecture

This module contains the v2.0 implementation of the Agent OS architecture,
featuring a unified instruction set (ActionIR), structured results (ToolResult),
and centralized side-effect control (KernelGate).

Key Components:
- protocol: ActionIR, ToolResult, Fault, Event (ISA/ABI definitions)
- runtime/decoder: LLM output → ActionIR translation with hallucination firewall
- os/gate: Unified syscall entry point with permission, timeout, observability
"""

__version__ = "2.0.0-alpha"
