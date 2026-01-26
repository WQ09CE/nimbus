"""Sample project for cross-file refactoring tests.

This is a minimal API client library with the following structure:
- core/client.py: Main APIClient class with old_api() method
- core/utils.py: Utility functions that use client.old_api()
- services/auth.py: Authentication service using client.old_api()
- services/data.py: Data service with its own old_api() function (NOT related to APIClient)

The refactoring task is to rename APIClient.old_api() to new_api() without
affecting the independent old_api() function in services/data.py.
"""

__version__ = "1.0.0"
