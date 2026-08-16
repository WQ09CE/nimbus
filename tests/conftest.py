"""Pytest configuration for nimbus tests."""

import os
import tempfile

import pytest


def pytest_configure(config):
    """Register asyncio marker."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as an asyncio test."
    )


# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)


@pytest.fixture(scope="session", autouse=True)
def _isolate_session_storage():
    """Keep every default-constructed SessionStorage out of the real
    ~/.nimbus/sessions. Tests that build a RuntimeLoop/AgentOS without an
    explicit storage used to write orphan session logs (and snapshots) into
    the user's live directory on every pytest run."""
    with tempfile.TemporaryDirectory(prefix="nimbus-test-sessions-") as d:
        old = os.environ.get("NIMBUS_SESSIONS_DIR")
        os.environ["NIMBUS_SESSIONS_DIR"] = d
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("NIMBUS_SESSIONS_DIR", None)
            else:
                os.environ["NIMBUS_SESSIONS_DIR"] = old
