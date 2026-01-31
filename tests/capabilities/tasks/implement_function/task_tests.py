"""
Test: implement_function
Difficulty: easy
Category: implementation

Verifies that the agent can implement a function from specification.
"""

import sys
from pathlib import Path


def test_fizzbuzz_exists(workspace: Path):
    """Test that fizzbuzz.py exists with fizzbuzz function."""
    fizzbuzz_path = workspace / "fizzbuzz.py"
    assert fizzbuzz_path.exists(), "fizzbuzz.py does not exist"
    
    sys.path.insert(0, str(workspace))
    try:
        from fizzbuzz import fizzbuzz
        assert callable(fizzbuzz), "fizzbuzz is not a function"
    finally:
        sys.path.pop(0)
        if 'fizzbuzz' in sys.modules:
            del sys.modules['fizzbuzz']


def test_fizzbuzz_small(workspace: Path):
    """Test fizzbuzz with small input."""
    sys.path.insert(0, str(workspace))
    try:
        from fizzbuzz import fizzbuzz
        result = fizzbuzz(5)
        expected = ["1", "2", "Fizz", "4", "Buzz"]
        assert result == expected, f"fizzbuzz(5) = {result}, expected {expected}"
    finally:
        sys.path.pop(0)
        if 'fizzbuzz' in sys.modules:
            del sys.modules['fizzbuzz']


def test_fizzbuzz_15(workspace: Path):
    """Test fizzbuzz with 15 (includes FizzBuzz)."""
    sys.path.insert(0, str(workspace))
    try:
        from fizzbuzz import fizzbuzz
        result = fizzbuzz(15)
        expected = [
            "1", "2", "Fizz", "4", "Buzz",
            "Fizz", "7", "8", "Fizz", "Buzz",
            "11", "Fizz", "13", "14", "FizzBuzz"
        ]
        assert result == expected, f"fizzbuzz(15) = {result}, expected {expected}"
    finally:
        sys.path.pop(0)
        if 'fizzbuzz' in sys.modules:
            del sys.modules['fizzbuzz']


def test_fizzbuzz_edge_cases(workspace: Path):
    """Test fizzbuzz edge cases."""
    sys.path.insert(0, str(workspace))
    try:
        from fizzbuzz import fizzbuzz
        
        # Empty case
        assert fizzbuzz(0) == [], f"fizzbuzz(0) should be []"
        
        # Single element
        assert fizzbuzz(1) == ["1"], f"fizzbuzz(1) should be ['1']"
        
        # Just Fizz
        assert fizzbuzz(3) == ["1", "2", "Fizz"], f"fizzbuzz(3) failed"
    finally:
        sys.path.pop(0)
        if 'fizzbuzz' in sys.modules:
            del sys.modules['fizzbuzz']
