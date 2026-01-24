"""Storage layer for Nimbus sessions and state.

This module provides persistent storage for:
- Session management (CRUD operations)
- Message history
- DAG state persistence
- Memory checkpoints (TieredMemoryManager serialization)
- Permission rules and requests

Primary class:
- SQLiteStorage: SQLite-based implementation for single-node deployment
"""

from .sqlite import SQLiteStorage

__all__ = ["SQLiteStorage"]
