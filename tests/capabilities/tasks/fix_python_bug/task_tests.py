"""
Test: fix_python_bug
Difficulty: easy
Category: bug_fixing

Verifies that the agent can find and fix a simple bug.
"""

import sys
from pathlib import Path


def test_divide_normal(workspace: Path):
    """Test normal division works."""
    sys.path.insert(0, str(workspace))
    try:
        from calculator import divide
        assert divide(10, 2) == 5.0
        assert divide(7, 2) == 3.5
        assert divide(-6, 3) == -2.0
    finally:
        sys.path.pop(0)
        if 'calculator' in sys.modules:
            del sys.modules['calculator']


def test_divide_by_zero_returns_none(workspace: Path):
    """Test that division by zero returns None."""
    sys.path.insert(0, str(workspace))
    try:
        from calculator import divide
        result = divide(10, 0)
        assert result is None, f"Expected None for divide(10, 0), got {result}"
    finally:
        sys.path.pop(0)
        if 'calculator' in sys.modules:
            del sys.modules['calculator']


def test_other_functions_unchanged(workspace: Path):
    """Test that other functions still work."""
    sys.path.insert(0, str(workspace))
    try:
        from calculator import add, subtract, multiply
        assert add(2, 3) == 5
        assert subtract(5, 3) == 2
        assert multiply(3, 4) == 12
    finally:
        sys.path.pop(0)
        if 'calculator' in sys.modules:
            del sys.modules['calculator']
