"""
Nimbus Orchestration Layer — Dual-Agent Architecture

Provides Core/Executor dual-agent orchestration on top of AgentOS,
without modifying the kernel (VCPU/MMU/Gate).

Core Agent: task decomposition, dispatch, verification (read-only)
Executor Agent: code implementation with full tool permissions

Usage:
    from nimbus.orchestration import DualAgentOrchestrator

    orchestrator = DualAgentOrchestrator(llm_client=llm, workspace=Path("/app"))
    result = await orchestrator.run("Build a gRPC KV store server...")
"""

from .dual_agent import DualAgentOrchestrator, OrchestratorConfig

__all__ = ["DualAgentOrchestrator", "OrchestratorConfig"]
