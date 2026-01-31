"""
Test: find_and_fix_bug  
Difficulty: medium
Category: bug_fixing

Tests binary search implementation.
"""

import sys
from pathlib import Path


def test_binary_search_found(workspace: Path):
    """Test finding elements that exist."""
    sys.path.insert(0, str(workspace))
    try:
        from binary_search import binary_search
        
        arr = [1, 3, 5, 7, 9, 11, 13]
        assert binary_search(arr, 1) == 0
        assert binary_search(arr, 7) == 3
        assert binary_search(arr, 13) == 6
    finally:
        sys.path.pop(0)
        if 'binary_search' in sys.modules:
            del sys.modules['binary_search']


def test_binary_search_not_found(workspace: Path):
    """Test searching for elements that don't exist."""
    sys.path.insert(0, str(workspace))
    try:
        from binary_search import binary_search
        
        arr = [1, 3, 5, 7, 9]
        assert binary_search(arr, 0) == -1
        assert binary_search(arr, 4) == -1
        assert binary_search(arr, 10) == -1
    finally:
        sys.path.pop(0)
        if 'binary_search' in sys.modules:
            del sys.modules['binary_search']


def test_binary_search_edge_cases(workspace: Path):
    """Test edge cases."""
    sys.path.insert(0, str(workspace))
    try:
        from binary_search import binary_search
        
        # Empty array
        assert binary_search([], 5) == -1
        
        # Single element - found
        assert binary_search([5], 5) == 0
        
        # Single element - not found
        assert binary_search([5], 3) == -1
        
        # Two elements
        assert binary_search([1, 2], 1) == 0
        assert binary_search([1, 2], 2) == 1
        assert binary_search([1, 2], 3) == -1
    finally:
        sys.path.pop(0)
        if 'binary_search' in sys.modules:
            del sys.modules['binary_search']


def test_binary_search_large_array(workspace: Path):
    """Test with larger array."""
    sys.path.insert(0, str(workspace))
    try:
        from binary_search import binary_search
        
        arr = list(range(0, 1000, 2))  # Even numbers 0-998
        assert binary_search(arr, 0) == 0
        assert binary_search(arr, 500) == 250
        assert binary_search(arr, 998) == 499
        assert binary_search(arr, 1) == -1  # Odd number not in array
    finally:
        sys.path.pop(0)
        if 'binary_search' in sys.modules:
            del sys.modules['binary_search']
