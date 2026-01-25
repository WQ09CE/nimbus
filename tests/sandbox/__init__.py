"""Sandbox testing framework for Nimbus.

This package provides integration testing with real LLM backends,
bypassing the HTTP server layer to test CodeAgent directly.

Key components:
- SandboxRunner: Isolated workspace runner for agent testing
- scenarios: Pre-defined test scenarios and sample files
- conftest: Pytest fixtures and markers

Usage:
    # Enable sandbox tests
    export NIMBUS_SANDBOX_TESTS=1

    # Run sandbox tests
    pytest tests/sandbox/ -v
"""

from .runner import SandboxRunner

__all__ = ["SandboxRunner"]
