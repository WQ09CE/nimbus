"""Nimbus CLI Module.

Provides command-line interface for managing Nimbus server and sessions.

Commands:
    nimbus serve        Start the HTTP server
    nimbus session      Manage sessions (list, create, delete)
    nimbus config       Manage configuration
"""

from .main import app

__all__ = ["app"]
