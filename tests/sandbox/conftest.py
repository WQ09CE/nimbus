"""Pytest configuration and fixtures for sandbox tests.

Sandbox tests are integration tests that use real LLM backends.
They are skipped by default and only run when NIMBUS_SANDBOX_TESTS=1.
"""

import os
import pytest
from pathlib import Path


def pytest_configure(config):
    """Register sandbox marker."""
    config.addinivalue_line(
        "markers",
        "sandbox: mark test as sandbox integration test (requires real LLM)"
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow-running"
    )


def pytest_collection_modifyitems(config, items):
    """Skip sandbox tests unless explicitly enabled."""
    if not os.getenv("NIMBUS_SANDBOX_TESTS"):
        skip_sandbox = pytest.mark.skip(
            reason="Set NIMBUS_SANDBOX_TESTS=1 to run sandbox tests"
        )
        for item in items:
            if "sandbox" in item.keywords:
                item.add_marker(skip_sandbox)


# =============================================================================
# LLM Configuration Fixtures
# =============================================================================


@pytest.fixture
def llm_provider():
    """Get LLM provider.

    Priority:
    1. NIMBUS_TEST_PROVIDER environment variable
    2. Unified config from ~/.nimbus/config.json (agents.core)
    3. Default: None (use unified config default)
    """
    env_provider = os.getenv("NIMBUS_TEST_PROVIDER")
    if env_provider:
        return env_provider
    # Return None to let SandboxRunner use unified config
    return None


@pytest.fixture
def llm_model():
    """Get LLM model.

    Priority:
    1. NIMBUS_TEST_MODEL environment variable
    2. Unified config from ~/.nimbus/config.json (agents.core)
    3. Default: None (use unified config default)
    """
    env_model = os.getenv("NIMBUS_TEST_MODEL")
    if env_model:
        return env_model
    # Return None to let SandboxRunner use unified config
    return None


@pytest.fixture
def enable_logging():
    """Get logging flag from environment.

    Set NIMBUS_TEST_LOGGING=1 to enable logging to .logs/nimbus.log
    """
    return bool(os.getenv("NIMBUS_TEST_LOGGING"))


# =============================================================================
# Workspace Fixtures
# =============================================================================


@pytest.fixture
def sandbox_workspace(tmp_path):
    """Create a temporary workspace directory.

    This provides a simple temp directory for tests that don't need
    the full SandboxRunner setup.

    Returns:
        Path to temporary workspace directory.
    """
    workspace = tmp_path / "sandbox"
    workspace.mkdir()
    return workspace


@pytest.fixture
def sample_python_project(sandbox_workspace):
    """Create a sample Python project in the workspace.

    Creates:
    - src/main.py
    - src/utils.py
    - tests/test_main.py
    - README.md

    Returns:
        Path to workspace root.
    """
    from .scenarios.sample_files import create_sample_project
    create_sample_project(sandbox_workspace)
    return sandbox_workspace


# =============================================================================
# Assertion Helpers
# =============================================================================


@pytest.fixture
def assert_file_contains():
    """Factory for asserting file content.

    Usage:
        assert_file_contains(runner.workspace / "main.py", "def hello")
    """
    def _assert(file_path: Path, expected: str, case_sensitive: bool = True):
        content = file_path.read_text()
        if not case_sensitive:
            content = content.lower()
            expected = expected.lower()
        assert expected in content, f"Expected '{expected}' in file {file_path}"
    return _assert


@pytest.fixture
def assert_file_not_contains():
    """Factory for asserting file doesn't contain content."""
    def _assert(file_path: Path, not_expected: str, case_sensitive: bool = True):
        content = file_path.read_text()
        if not case_sensitive:
            content = content.lower()
            not_expected = not_expected.lower()
        assert not_expected not in content, f"Unexpected '{not_expected}' in file {file_path}"
    return _assert
