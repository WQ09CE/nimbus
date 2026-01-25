"""Test scenarios and sample files for sandbox tests.

This package provides pre-defined test scenarios and sample file generators
for sandbox integration tests.
"""

from .sample_files import (
    PYTHON_SIMPLE,
    PYTHON_WITH_BUG,
    PYTHON_NEEDS_REFACTOR,
    PYTHON_CLASS_EXAMPLE,
    README_TEMPLATE,
    create_sample_project,
    create_buggy_project,
    create_refactoring_project,
)

__all__ = [
    "PYTHON_SIMPLE",
    "PYTHON_WITH_BUG",
    "PYTHON_NEEDS_REFACTOR",
    "PYTHON_CLASS_EXAMPLE",
    "README_TEMPLATE",
    "create_sample_project",
    "create_buggy_project",
    "create_refactoring_project",
]
