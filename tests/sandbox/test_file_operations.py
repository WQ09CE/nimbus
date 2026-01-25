"""Test file operations with real LLM.

These tests verify that CodeAgent can perform basic file operations:
- Reading files
- Writing files
- Editing files
- Searching files
"""

import pytest
from .runner import SandboxRunner
from .scenarios.sample_files import PYTHON_SIMPLE, PYTHON_CLASS_EXAMPLE

pytestmark = pytest.mark.sandbox


class TestFileRead:
    """Test file reading capabilities."""

    @pytest.mark.asyncio
    async def test_read_single_file(self, llm_provider, llm_model):
        """Agent can read a single file and describe its contents."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("main.py", PYTHON_SIMPLE)

            response = await runner.run("Read the main.py file and tell me what functions it contains")

            # Response should mention the functions in the file
            response_lower = response.text.lower()
            assert any(word in response_lower for word in ["greet", "add", "function"]), \
                f"Expected function names in response: {response.text[:200]}"

    @pytest.mark.asyncio
    async def test_read_multiple_files(self, llm_provider, llm_model):
        """Agent can read and compare multiple files."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("a.py", "def foo(): pass")
            runner.create_file("b.py", "def bar(): pass")

            response = await runner.run("Read both a.py and b.py and tell me what function each defines")

            response_lower = response.text.lower()
            assert "foo" in response_lower or "bar" in response_lower, \
                f"Expected function names in response: {response.text[:200]}"

    @pytest.mark.asyncio
    async def test_list_files_in_directory(self, llm_provider, llm_model):
        """Agent can list files in a directory."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("src/module_a.py", "# module a")
            runner.create_file("src/module_b.py", "# module b")
            runner.create_file("src/module_c.py", "# module c")
            runner.create_file("README.md", "# readme")

            response = await runner.run("List all Python files in the src directory")

            # Should find the Python files
            response_lower = response.text.lower()
            assert "module" in response_lower or ".py" in response_lower, \
                f"Expected Python files in response: {response.text[:200]}"


class TestFileWrite:
    """Test file writing capabilities."""

    @pytest.mark.asyncio
    async def test_create_new_file(self, llm_provider, llm_model):
        """Agent can create a new file with specified content."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            response = await runner.run(
                "Create a new file called hello.py with a function named 'say_hello' "
                "that prints 'Hello World'"
            )

            # Check file was created
            assert runner.file_exists("hello.py"), "hello.py should be created"
            content = runner.read_file("hello.py")
            assert "def" in content or "hello" in content.lower(), \
                f"Expected function definition in file: {content[:200]}"

    @pytest.mark.asyncio
    async def test_create_file_with_docstring(self, llm_provider, llm_model):
        """Agent can create a file with proper documentation."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            response = await runner.run(
                "Create a Python file called utils.py with a function 'multiply(a, b)' "
                "that multiplies two numbers. Include a proper docstring."
            )

            assert runner.file_exists("utils.py"), "utils.py should be created"
            content = runner.read_file("utils.py")
            # Check for function and docstring
            assert "def" in content, "Should contain function definition"
            assert "multiply" in content.lower(), "Should contain multiply function"


class TestFileEdit:
    """Test file editing capabilities."""

    @pytest.mark.asyncio
    async def test_add_docstring(self, llm_provider, llm_model):
        """Agent can add docstring to a function."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            # Create file without docstring
            runner.create_file("main.py", "def hello(name):\n    return f'Hello, {name}!'")

            response = await runner.run(
                "Edit main.py to add a docstring to the hello function explaining "
                "what it does and its parameters"
            )

            content = runner.read_file("main.py")
            # Check docstring was added (triple quotes)
            assert '"""' in content or "'''" in content, \
                f"Expected docstring in file: {content}"

    @pytest.mark.asyncio
    async def test_fix_syntax_error(self, llm_provider, llm_model):
        """Agent can fix a simple syntax error."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            # Create file with syntax error (missing colon)
            runner.create_file("broken.py", "def hello()\n    return 'hello'")

            response = await runner.run(
                "Fix the syntax error in broken.py"
            )

            content = runner.read_file("broken.py")
            # Check colon was added
            assert "def hello():" in content or "def hello(" in content, \
                f"Expected fixed function definition: {content}"

    @pytest.mark.asyncio
    async def test_add_error_handling(self, llm_provider, llm_model):
        """Agent can add error handling to a function."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file(
                "divide.py",
                "def divide(a, b):\n    return a / b"
            )

            response = await runner.run(
                "Edit divide.py to add error handling for division by zero"
            )

            content = runner.read_file("divide.py")
            # Check for error handling
            assert any(
                kw in content.lower()
                for kw in ["try", "except", "if b == 0", "if b != 0", "zero"]
            ), f"Expected error handling in file: {content}"


class TestFileSearch:
    """Test file search capabilities."""

    @pytest.mark.asyncio
    async def test_grep_content(self, llm_provider, llm_model):
        """Agent can search for content across files."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("a.py", "def process_data(): pass")
            runner.create_file("b.py", "def handle_request(): pass")
            runner.create_file("c.py", "def process_request(): pass")

            response = await runner.run("Find all files that contain the word 'process'")

            response_lower = response.text.lower()
            # Should find a.py and c.py
            assert "a.py" in response_lower or "c.py" in response_lower or "process" in response_lower, \
                f"Expected search results: {response.text[:200]}"

    @pytest.mark.asyncio
    async def test_find_function_definition(self, llm_provider, llm_model):
        """Agent can find where a function is defined."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("utils.py", "def helper_function():\n    return 42")
            runner.create_file("main.py", "from utils import helper_function\n\nhelper_function()")

            response = await runner.run("Find where helper_function is defined")

            response_lower = response.text.lower()
            assert "utils" in response_lower, \
                f"Expected utils.py in response: {response.text[:200]}"

    @pytest.mark.asyncio
    async def test_find_class_methods(self, llm_provider, llm_model):
        """Agent can find all methods of a class."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("calculator.py", PYTHON_CLASS_EXAMPLE)

            response = await runner.run(
                "Find all methods in the Calculator class in calculator.py"
            )

            response_lower = response.text.lower()
            # Should mention the methods
            assert any(
                method in response_lower
                for method in ["add", "subtract", "multiply", "reset"]
            ), f"Expected method names in response: {response.text[:200]}"
