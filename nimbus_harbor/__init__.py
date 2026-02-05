"""
Nimbus Harbor Integration - Agent adapter for Harbor evaluation framework.

This module provides integration with the Harbor evaluation framework,
allowing Nimbus to be evaluated on various benchmarks.

Usage:
    harbor run -p nimbus_harbor/tasks/simple-coding-test \
        --agent-import-path nimbus_harbor.nimbus_agent:NimbusAgent
"""

from nimbus_harbor.nimbus_agent import NimbusAgent

__all__ = ["NimbusAgent"]
