"""Tests for code modification capability.

This module tests the agent's ability to correctly modify code using
Write and Edit tools while preserving syntax validity and following
the minimal change principle.

Capability: code_modification
"""

import pytest
from pathlib import Path
from typing import Optional

from src.nimbus.tools.write import write_file
from src.nimbus.tools.edit import edit_file

from tests.evaluation.metrics import (
    CodeModificationMetrics,
    ModificationExpectation,
)


# =============================================================================
# Test Cases
# =============================================================================


@pytest.mark.capability("code_modification")
class TestAddFunction:
    """Tests for adding functions to files."""

    @pytest.fixture
    def metrics(self):
        return CodeModificationMetrics()

    @pytest.mark.asyncio
    async def test_add_function_to_file(self, tmp_path, metrics):
        """Adding a new function should produce valid code.

        When adding a function to an existing file, the result should:
        1. Contain the new function
        2. Preserve existing code
        3. Be syntactically valid
        """
        # Create initial file
        file_path = tmp_path / "module.py"
        original_code = '''"""A sample module."""

def existing_function():
    """Existing function."""
    return 42
'''
        file_path.write_text(original_code)

        # New function to add
        new_function = '''

def new_function(x: int) -> int:
    """New function that doubles input."""
    return x * 2
'''

        # Write file with new content
        new_content = original_code + new_function
        await write_file(str(file_path), new_content, workspace=tmp_path)

        # Read result
        modified_code = file_path.read_text()

        # Evaluate
        expectation = ModificationExpectation(
            expected_changes=["def new_function", "return x * 2"],
            preserve_patterns=["def existing_function", "return 42"],
        )

        results = metrics.evaluate(original_code, modified_code, expectation)
        summary = metrics.summary(results)

        assert summary["expected_changes"] == 1.0
        assert summary["pattern_preservation"] == 1.0

        # Check syntax validity
        syntax_result = metrics.evaluate_syntax_validity(modified_code)
        assert syntax_result.value == 1.0

    @pytest.mark.asyncio
    async def test_add_method_to_class(self, tmp_path, metrics):
        """Adding a method to a class should maintain class structure."""
        file_path = tmp_path / "class_module.py"
        original_code = '''class Calculator:
    """A simple calculator class."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b
'''
        file_path.write_text(original_code)

        # Add multiply method
        new_code = '''class Calculator:
    """A simple calculator class."""

    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    def multiply(self, a: int, b: int) -> int:
        """Multiply two numbers."""
        return a * b
'''

        await write_file(str(file_path), new_code, workspace=tmp_path)
        modified_code = file_path.read_text()

        # Evaluate
        expectation = ModificationExpectation(
            expected_changes=["def multiply", "return a * b"],
            preserve_patterns=["class Calculator", "def add"],
        )

        results = metrics.evaluate(original_code, modified_code, expectation)
        summary = metrics.summary(results)

        assert summary["expected_changes"] == 1.0
        assert summary["pattern_preservation"] == 1.0


@pytest.mark.capability("code_modification")
class TestEditExistingFunction:
    """Tests for editing existing functions."""

    @pytest.fixture
    def metrics(self):
        return CodeModificationMetrics()

    @pytest.mark.asyncio
    async def test_edit_existing_function(self, tmp_path, metrics):
        """Editing a function should update only the targeted code."""
        file_path = tmp_path / "edit_module.py"
        original_code = '''def greet(name):
    """Greet a person."""
    return f"Hello, {name}!"

def farewell(name):
    """Say goodbye."""
    return f"Goodbye, {name}!"
'''
        file_path.write_text(original_code)

        # Edit greet function to add uppercase
        await edit_file(
            str(file_path),
            old_string='return f"Hello, {name}!"',
            new_string='return f"Hello, {name.upper()}!"',
            workspace=tmp_path,
        )

        modified_code = file_path.read_text()

        # Evaluate
        expectation = ModificationExpectation(
            expected_changes=["name.upper()"],
            preserve_patterns=["def farewell", "Goodbye"],
        )

        results = metrics.evaluate(original_code, modified_code, expectation)
        summary = metrics.summary(results)

        assert summary["expected_changes"] == 1.0
        assert summary["pattern_preservation"] == 1.0

    @pytest.mark.asyncio
    async def test_edit_with_replace_all(self, tmp_path, metrics):
        """Replace all should update multiple occurrences."""
        file_path = tmp_path / "replace_all.py"
        original_code = '''OLD_NAME = "old"
config = {"name": OLD_NAME}
print(f"Using {OLD_NAME}")
'''
        file_path.write_text(original_code)

        # Replace all occurrences
        await edit_file(
            str(file_path),
            old_string="OLD_NAME",
            new_string="NEW_NAME",
            replace_all=True,
            workspace=tmp_path,
        )

        modified_code = file_path.read_text()

        # All occurrences should be replaced
        assert "OLD_NAME" not in modified_code
        assert modified_code.count("NEW_NAME") == 3


@pytest.mark.capability("code_modification")
class TestPreserveSyntaxValidity:
    """Tests ensuring modifications preserve syntax validity."""

    @pytest.fixture
    def metrics(self):
        return CodeModificationMetrics()

    def test_valid_python_syntax(self, metrics):
        """Valid Python code should pass syntax check."""
        valid_code = '''
def hello():
    print("Hello, World!")

class Greeter:
    def greet(self, name: str) -> str:
        return f"Hello, {name}!"
'''
        result = metrics.evaluate_syntax_validity(valid_code, language="python")
        assert result.value == 1.0

    def test_invalid_python_syntax(self, metrics):
        """Invalid Python code should fail syntax check."""
        invalid_code = '''
def hello(
    print("Missing closing parenthesis"

class Incomplete:
'''
        result = metrics.evaluate_syntax_validity(invalid_code, language="python")
        assert result.value == 0.0
        assert result.details["error"] is not None

    def test_empty_code_valid(self, metrics):
        """Empty code should be considered valid."""
        result = metrics.evaluate_syntax_validity("", language="python")
        assert result.value == 1.0

    def test_non_python_language(self, metrics):
        """Non-Python languages should assume valid (no checker)."""
        js_code = '''
function hello() {
    console.log("Hello, World!");
}
'''
        result = metrics.evaluate_syntax_validity(js_code, language="javascript")
        assert result.value == 1.0


@pytest.mark.capability("code_modification")
class TestMinimalChangePrinciple:
    """Tests for minimal change principle."""

    @pytest.fixture
    def metrics(self):
        return CodeModificationMetrics()

    def test_minimal_change_single_line(self, metrics):
        """Changing a single line should have high minimal_change score."""
        original = '''line 1
line 2
line 3
line 4
line 5
'''
        modified = '''line 1
line 2
line 3 modified
line 4
line 5
'''
        expectation = ModificationExpectation()
        results = metrics.evaluate(original, modified, expectation)
        summary = metrics.summary(results)

        # 4 out of 5 lines unchanged = 0.8
        assert summary["minimal_change"] >= 0.8

    def test_minimal_change_excessive(self, metrics):
        """Excessive changes should have lower minimal_change score."""
        original = '''line 1
line 2
line 3
line 4
line 5
'''
        modified = '''completely different 1
completely different 2
completely different 3
completely different 4
line 5
'''
        expectation = ModificationExpectation()
        results = metrics.evaluate(original, modified, expectation)
        summary = metrics.summary(results)

        # Only 1 out of 5 lines unchanged = 0.2
        assert summary["minimal_change"] <= 0.2


@pytest.mark.capability("code_modification")
class TestEdgeCases:
    """Edge cases for code modification."""

    @pytest.fixture
    def metrics(self):
        return CodeModificationMetrics()

    @pytest.mark.asyncio
    async def test_edit_non_unique_string_fails(self, tmp_path):
        """Editing non-unique string without replace_all should fail."""
        file_path = tmp_path / "non_unique.py"
        code = '''x = 1
y = 1
z = 1
'''
        file_path.write_text(code)

        with pytest.raises(ValueError) as exc_info:
            await edit_file(
                str(file_path),
                old_string="1",
                new_string="2",
                replace_all=False,
                workspace=tmp_path,
            )

        assert "appears 3 times" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_edit_string_not_found(self, tmp_path):
        """Editing non-existent string should fail."""
        file_path = tmp_path / "not_found.py"
        code = "x = 1\n"
        file_path.write_text(code)

        with pytest.raises(ValueError) as exc_info:
            await edit_file(
                str(file_path),
                old_string="nonexistent",
                new_string="replacement",
                workspace=tmp_path,
            )

        assert "not found" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_write_creates_parent_directories(self, tmp_path):
        """Write should create parent directories if needed."""
        file_path = tmp_path / "new_dir" / "subdir" / "file.py"

        code = "print('hello')\n"
        await write_file(str(file_path), code, workspace=tmp_path)

        assert file_path.exists()
        assert file_path.read_text() == code

    @pytest.mark.asyncio
    async def test_write_empty_content(self, tmp_path):
        """Writing empty content should create empty file."""
        file_path = tmp_path / "empty.py"

        await write_file(str(file_path), "", workspace=tmp_path)

        assert file_path.exists()
        assert file_path.read_text() == ""

    @pytest.mark.asyncio
    async def test_edit_same_string_fails(self, tmp_path):
        """Editing with same old and new string should fail."""
        file_path = tmp_path / "same.py"
        code = "x = 1\n"
        file_path.write_text(code)

        with pytest.raises(ValueError) as exc_info:
            await edit_file(
                str(file_path),
                old_string="x = 1",
                new_string="x = 1",
                workspace=tmp_path,
            )

        assert "cannot be the same" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_write_to_directory_fails(self, tmp_path):
        """Writing to a directory path should fail."""
        dir_path = tmp_path / "a_directory"
        dir_path.mkdir()

        with pytest.raises(IsADirectoryError):
            await write_file(str(dir_path), "content", workspace=tmp_path)


@pytest.mark.capability("code_modification")
class TestForbiddenChanges:
    """Tests for detecting forbidden changes."""

    @pytest.fixture
    def metrics(self):
        return CodeModificationMetrics()

    def test_forbidden_changes_detected(self, metrics):
        """Modifications containing forbidden patterns should be flagged."""
        original = '''def safe_function():
    return "safe"
'''
        modified = '''def safe_function():
    import os
    os.system("rm -rf /")  # Dangerous!
    return "safe"
'''
        expectation = ModificationExpectation(
            forbidden_changes=["os.system", "rm -rf"],
        )

        results = metrics.evaluate(original, modified, expectation)
        summary = metrics.summary(results)

        # Both forbidden patterns are present
        assert summary["forbidden_changes_absent"] == 0.0

    def test_forbidden_changes_absent(self, metrics):
        """Clean modifications should pass forbidden changes check."""
        original = "x = 1\n"
        modified = "x = 2\n"

        expectation = ModificationExpectation(
            forbidden_changes=["import os", "exec(", "eval("],
        )

        results = metrics.evaluate(original, modified, expectation)
        summary = metrics.summary(results)

        assert summary["forbidden_changes_absent"] == 1.0


@pytest.mark.capability("code_modification")
class TestPatternPreservation:
    """Tests for pattern preservation during modifications."""

    @pytest.fixture
    def metrics(self):
        return CodeModificationMetrics()

    def test_preserve_important_patterns(self, metrics):
        """Important patterns should be preserved after modification."""
        original = '''# Copyright 2024 MyCompany
# License: MIT

def main():
    print("Hello")

if __name__ == "__main__":
    main()
'''
        modified = '''# Copyright 2024 MyCompany
# License: MIT

def main():
    print("Hello, World!")  # Updated greeting

if __name__ == "__main__":
    main()
'''
        expectation = ModificationExpectation(
            preserve_patterns=[
                "Copyright 2024",
                "License: MIT",
                'if __name__ == "__main__"',
            ],
        )

        results = metrics.evaluate(original, modified, expectation)
        summary = metrics.summary(results)

        assert summary["pattern_preservation"] == 1.0

    def test_detect_broken_patterns(self, metrics):
        """Broken important patterns should be detected."""
        original = '''# Important header comment

def critical_function():
    pass
'''
        modified = '''def critical_function():
    # Header was removed!
    pass
'''
        expectation = ModificationExpectation(
            preserve_patterns=["Important header comment"],
        )

        results = metrics.evaluate(original, modified, expectation)
        summary = metrics.summary(results)

        assert summary["pattern_preservation"] == 0.0
