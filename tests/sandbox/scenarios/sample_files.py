"""Sample files and project generators for sandbox tests.

This module provides sample file contents and functions to create
test project structures in sandbox workspaces.
"""

from pathlib import Path
from typing import Dict


# =============================================================================
# Sample File Contents
# =============================================================================

PYTHON_SIMPLE = '''"""Simple Python module for testing."""


def greet(name: str) -> str:
    """Return a greeting message.

    Args:
        name: Name to greet.

    Returns:
        Greeting message string.
    """
    return f"Hello, {name}!"


def add(a: int, b: int) -> int:
    """Add two numbers.

    Args:
        a: First number.
        b: Second number.

    Returns:
        Sum of a and b.
    """
    return a + b


if __name__ == "__main__":
    print(greet("World"))
'''

PYTHON_WITH_BUG = '''"""Module with intentional bugs for testing."""


def calculate_average(numbers):
    # BUG: doesn't handle empty list
    total = sum(numbers)
    return total / len(numbers)


def find_max(numbers):
    """Find maximum value in list."""
    if not numbers:
        return None
    return max(numbers)


def divide(a, b):
    # BUG: doesn't handle division by zero
    return a / b


def process_items(items):
    # BUG: modifies input list
    items.sort()
    return items[0] if items else None
'''

PYTHON_NEEDS_REFACTOR = '''"""Module needing refactoring."""


class UserManager:
    """User management class with methods to rename."""

    def __init__(self):
        self.users = []

    def old_add_user(self, name, email):
        """Add user - to be renamed to add_user."""
        self.users.append({"name": name, "email": email})

    def old_remove_user(self, email):
        """Remove user - to be renamed to remove_user."""
        self.users = [u for u in self.users if u["email"] != email]

    def old_get_user(self, email):
        """Get user - to be renamed to get_user."""
        for u in self.users:
            if u["email"] == email:
                return u
        return None

    def old_list_users(self):
        """List all users - to be renamed to list_users."""
        return list(self.users)
'''

PYTHON_CLASS_EXAMPLE = '''"""Example class-based module."""


class Calculator:
    """Simple calculator class."""

    def __init__(self, initial_value: float = 0):
        """Initialize calculator with value.

        Args:
            initial_value: Starting value.
        """
        self.value = initial_value
        self.history = []

    def add(self, n: float) -> float:
        """Add to current value."""
        self.history.append(f"add {n}")
        self.value += n
        return self.value

    def subtract(self, n: float) -> float:
        """Subtract from current value."""
        self.history.append(f"subtract {n}")
        self.value -= n
        return self.value

    def multiply(self, n: float) -> float:
        """Multiply current value."""
        self.history.append(f"multiply {n}")
        self.value *= n
        return self.value

    def reset(self) -> None:
        """Reset calculator to zero."""
        self.value = 0
        self.history.clear()


def create_calculator(initial: float = 0) -> Calculator:
    """Factory function for Calculator."""
    return Calculator(initial)
'''

README_TEMPLATE = '''# Sample Project

This is a sample project for testing.

## Installation

```bash
pip install -e .
```

## Usage

```python
from src.main import greet, add

# Greet someone
message = greet("World")
print(message)  # Hello, World!

# Add numbers
result = add(2, 3)
print(result)  # 5
```

## API Reference

### Functions

- `greet(name: str) -> str`: Returns greeting message
- `add(a: int, b: int) -> int`: Returns sum of two numbers

## Testing

```bash
pytest tests/
```

## License

MIT
'''

TEST_FILE_TEMPLATE = '''"""Tests for main module."""

import pytest
from src.main import greet, add


class TestGreet:
    """Tests for greet function."""

    def test_greet_world(self):
        """Test greeting World."""
        assert greet("World") == "Hello, World!"

    def test_greet_name(self):
        """Test greeting a specific name."""
        assert greet("Alice") == "Hello, Alice!"

    def test_greet_empty(self):
        """Test greeting empty string."""
        assert greet("") == "Hello, !"


class TestAdd:
    """Tests for add function."""

    def test_add_positive(self):
        """Test adding positive numbers."""
        assert add(2, 3) == 5

    def test_add_negative(self):
        """Test adding negative numbers."""
        assert add(-1, -2) == -3

    def test_add_zero(self):
        """Test adding zero."""
        assert add(5, 0) == 5
'''


# =============================================================================
# Project Generators
# =============================================================================


def create_sample_project(workspace: Path) -> Dict[str, Path]:
    """Create a sample Python project structure.

    Creates:
    - src/main.py - Simple functions
    - src/utils.py - Buggy utilities
    - tests/test_main.py - Unit tests
    - README.md - Documentation

    Args:
        workspace: Path to workspace directory.

    Returns:
        Dict mapping relative paths to absolute paths.
    """
    files = {}

    # Create directories (including workspace if it doesn't exist)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "src").mkdir(exist_ok=True)
    (workspace / "tests").mkdir(exist_ok=True)

    # Create source files
    main_path = workspace / "src" / "main.py"
    main_path.write_text(PYTHON_SIMPLE)
    files["src/main.py"] = main_path

    utils_path = workspace / "src" / "utils.py"
    utils_path.write_text(PYTHON_WITH_BUG)
    files["src/utils.py"] = utils_path

    # Create test file
    test_path = workspace / "tests" / "test_main.py"
    test_path.write_text(TEST_FILE_TEMPLATE)
    files["tests/test_main.py"] = test_path

    # Create README
    readme_path = workspace / "README.md"
    readme_path.write_text(README_TEMPLATE)
    files["README.md"] = readme_path

    # Create __init__.py files
    (workspace / "src" / "__init__.py").write_text('"""Source package."""\n')
    (workspace / "tests" / "__init__.py").write_text('"""Tests package."""\n')

    return files


def create_buggy_project(workspace: Path) -> Dict[str, Path]:
    """Create a project with intentional bugs.

    Useful for testing bug detection and fixing capabilities.

    Args:
        workspace: Path to workspace directory.

    Returns:
        Dict mapping relative paths to absolute paths.
    """
    files = {}

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "src").mkdir(exist_ok=True)

    # Main buggy file
    buggy_path = workspace / "src" / "buggy.py"
    buggy_path.write_text(PYTHON_WITH_BUG)
    files["src/buggy.py"] = buggy_path

    # Test file that will fail
    test_content = '''"""Tests for buggy module."""

import pytest
from src.buggy import calculate_average, divide


def test_average_empty_list():
    """This test will fail - empty list bug."""
    result = calculate_average([])
    assert result == 0


def test_divide_by_zero():
    """This test will fail - division by zero."""
    result = divide(10, 0)
    assert result is None
'''
    test_path = workspace / "tests" / "test_buggy.py"
    (workspace / "tests").mkdir(exist_ok=True)
    test_path.write_text(test_content)
    files["tests/test_buggy.py"] = test_path

    return files


def create_refactoring_project(workspace: Path) -> Dict[str, Path]:
    """Create a project that needs refactoring.

    Creates files with methods that need to be renamed across multiple files.

    Args:
        workspace: Path to workspace directory.

    Returns:
        Dict mapping relative paths to absolute paths.
    """
    files = {}

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "src").mkdir(exist_ok=True)

    # User manager with old method names
    manager_path = workspace / "src" / "user_manager.py"
    manager_path.write_text(PYTHON_NEEDS_REFACTOR)
    files["src/user_manager.py"] = manager_path

    # App that uses old method names
    app_content = '''"""Application that uses UserManager."""

from src.user_manager import UserManager


def main():
    """Main entry point."""
    manager = UserManager()

    # Add some users
    manager.old_add_user("Alice", "alice@example.com")
    manager.old_add_user("Bob", "bob@example.com")

    # Get a user
    user = manager.old_get_user("alice@example.com")
    if user:
        print(f"Found user: {user['name']}")

    # List all users
    for u in manager.old_list_users():
        print(f"- {u['name']} ({u['email']})")

    # Remove a user
    manager.old_remove_user("bob@example.com")


if __name__ == "__main__":
    main()
'''
    app_path = workspace / "src" / "app.py"
    app_path.write_text(app_content)
    files["src/app.py"] = app_path

    # Tests using old method names
    test_content = '''"""Tests for UserManager."""

import pytest
from src.user_manager import UserManager


class TestUserManager:
    """Tests for UserManager class."""

    def test_add_user(self):
        """Test adding a user."""
        manager = UserManager()
        manager.old_add_user("Test", "test@example.com")
        assert len(manager.old_list_users()) == 1

    def test_get_user(self):
        """Test getting a user."""
        manager = UserManager()
        manager.old_add_user("Test", "test@example.com")
        user = manager.old_get_user("test@example.com")
        assert user is not None
        assert user["name"] == "Test"

    def test_remove_user(self):
        """Test removing a user."""
        manager = UserManager()
        manager.old_add_user("Test", "test@example.com")
        manager.old_remove_user("test@example.com")
        assert len(manager.old_list_users()) == 0
'''
    (workspace / "tests").mkdir(exist_ok=True)
    test_path = workspace / "tests" / "test_user_manager.py"
    test_path.write_text(test_content)
    files["tests/test_user_manager.py"] = test_path

    return files
