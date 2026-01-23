"""Pytest configuration for nimbus tests."""

import pytest


def pytest_configure(config):
    """Register asyncio marker."""
    config.addinivalue_line(
        "markers", "asyncio: mark test as an asyncio test."
    )


# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)
