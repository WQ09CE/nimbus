"""Capability tests for Nimbus Code Agent.

This package contains tests organized by agent capability dimensions:
- Task decomposition: Breaking down complex tasks into subtasks
- Code search: Finding code patterns and files
- Context compression: Compressing long conversations while preserving key info
- Context understanding: Understanding conversation context
- Code modification: Correctly modifying code with Write/Edit tools
- Bash execution: Executing bash commands with proper error handling
- Code summarization: Accurately summarizing code structure and purpose
- Repo understanding: Understanding repository structure and dependencies

Each test is marked with @pytest.mark.capability("name") for filtering.

Usage:
    # Run all capability tests
    pytest tests/capabilities/

    # Run specific capability tests
    pytest tests/capabilities/ --capability=code_search

    # Run with verbose output
    pytest tests/capabilities/ -v
"""
