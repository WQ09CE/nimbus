"""Test code analysis capabilities with real LLM.

These tests verify that CodeAgent can analyze code:
- Understanding code structure
- Identifying bugs
- Suggesting improvements
- Explaining code
"""

import pytest
from .runner import SandboxRunner
from .scenarios.sample_files import (
    PYTHON_SIMPLE,
    PYTHON_WITH_BUG,
    PYTHON_CLASS_EXAMPLE,
    create_sample_project,
)

pytestmark = pytest.mark.sandbox


class TestCodeUnderstanding:
    """Test code understanding capabilities."""

    @pytest.mark.asyncio
    async def test_explain_function(self, llm_provider, llm_model):
        """Agent can explain what a function does."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("main.py", PYTHON_SIMPLE)

            response = await runner.run("Explain what the greet function in main.py does")

            response_lower = response.text.lower()
            # Should explain the function's purpose
            assert any(
                word in response_lower
                for word in ["greeting", "return", "hello", "name", "string"]
            ), f"Expected explanation of greet function: {response.text[:300]}"

    @pytest.mark.asyncio
    async def test_describe_class_structure(self, llm_provider, llm_model):
        """Agent can describe a class structure."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("calculator.py", PYTHON_CLASS_EXAMPLE)

            response = await runner.run(
                "Describe the Calculator class in calculator.py - "
                "what are its attributes and methods?"
            )

            response_lower = response.text.lower()
            # Should mention key attributes and methods
            assert any(
                word in response_lower
                for word in ["value", "history", "add", "subtract", "method"]
            ), f"Expected class description: {response.text[:300]}"

    @pytest.mark.asyncio
    async def test_understand_project_structure(self, llm_provider, llm_model):
        """Agent can understand a project's structure."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            create_sample_project(runner.workspace)

            response = await runner.run(
                "Explore this project and describe its structure - "
                "what directories and main files does it have?"
            )

            response_lower = response.text.lower()
            # Should mention key directories/files
            assert any(
                word in response_lower
                for word in ["src", "tests", "main", "readme", "directory", "folder"]
            ), f"Expected project structure description: {response.text[:300]}"


class TestBugDetection:
    """Test bug detection capabilities."""

    @pytest.mark.asyncio
    async def test_find_division_by_zero(self, llm_provider, llm_model):
        """Agent can identify division by zero bug."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("buggy.py", PYTHON_WITH_BUG)

            response = await runner.run(
                "Review buggy.py and identify any potential bugs or issues"
            )

            response_lower = response.text.lower()
            # Should identify at least one of the bugs
            assert any(
                word in response_lower
                for word in ["empty", "zero", "division", "bug", "error", "exception"]
            ), f"Expected bug identification: {response.text[:300]}"

    @pytest.mark.asyncio
    async def test_identify_empty_list_bug(self, llm_provider, llm_model):
        """Agent can identify empty list handling bug."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
def calculate_average(numbers):
    total = sum(numbers)
    return total / len(numbers)
'''
            runner.create_file("average.py", code)

            response = await runner.run(
                "What bug exists in the calculate_average function in average.py?"
            )

            response_lower = response.text.lower()
            assert any(
                word in response_lower
                for word in ["empty", "zero", "list", "division", "len"]
            ), f"Expected empty list bug identification: {response.text[:300]}"


class TestCodeImprovement:
    """Test code improvement suggestion capabilities."""

    @pytest.mark.asyncio
    async def test_suggest_type_hints(self, llm_provider, llm_model):
        """Agent can suggest adding type hints."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
def process(data):
    result = []
    for item in data:
        result.append(item * 2)
    return result
'''
            runner.create_file("process.py", code)

            response = await runner.run(
                "How could I improve process.py? Consider type hints and best practices."
            )

            response_lower = response.text.lower()
            assert any(
                word in response_lower
                for word in ["type", "hint", "list", "annotation", "comprehension"]
            ), f"Expected improvement suggestions: {response.text[:300]}"

    @pytest.mark.asyncio
    async def test_suggest_error_handling(self, llm_provider, llm_model):
        """Agent can suggest adding error handling."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
def read_config(path):
    with open(path) as f:
        return json.load(f)
'''
            runner.create_file("config.py", code)

            response = await runner.run(
                "What error handling should be added to read_config in config.py?"
            )

            response_lower = response.text.lower()
            assert any(
                word in response_lower
                for word in ["try", "except", "error", "exception", "filenotfound", "json"]
            ), f"Expected error handling suggestions: {response.text[:300]}"


class TestCodeExplanation:
    """Test code explanation capabilities."""

    @pytest.mark.asyncio
    async def test_explain_complex_function(self, llm_provider, llm_model):
        """Agent can explain a more complex function."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
def fibonacci(n, memo=None):
    """Generate nth Fibonacci number using memoization."""
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fibonacci(n - 1, memo) + fibonacci(n - 2, memo)
    return memo[n]
'''
            runner.create_file("fib.py", code)

            response = await runner.run(
                "Explain how the fibonacci function in fib.py works, "
                "including the memoization technique"
            )

            response_lower = response.text.lower()
            assert any(
                word in response_lower
                for word in ["memo", "cache", "recursive", "fibonacci", "store"]
            ), f"Expected memoization explanation: {response.text[:300]}"

    @pytest.mark.asyncio
    async def test_explain_decorator(self, llm_provider, llm_model):
        """Agent can explain a decorator pattern."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
import functools
import time

def timer(func):
    """Decorator to measure function execution time."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"{func.__name__} took {end - start:.2f} seconds")
        return result
    return wrapper

@timer
def slow_function():
    time.sleep(1)
    return "done"
'''
            runner.create_file("decorators.py", code)

            response = await runner.run(
                "Explain how the timer decorator in decorators.py works"
            )

            response_lower = response.text.lower()
            assert any(
                word in response_lower
                for word in ["decorator", "wrap", "time", "function", "measure"]
            ), f"Expected decorator explanation: {response.text[:300]}"


class TestProjectAnalysis:
    """Test whole-project analysis capabilities."""

    @pytest.mark.asyncio
    async def test_analyze_dependencies(self, llm_provider, llm_model):
        """Agent can analyze module dependencies."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            runner.create_file("src/models.py", "class User:\n    pass")
            runner.create_file(
                "src/services.py",
                "from src.models import User\n\ndef get_user():\n    return User()"
            )
            runner.create_file(
                "src/api.py",
                "from src.services import get_user\n\ndef handle():\n    return get_user()"
            )

            response = await runner.run(
                "Analyze the dependencies between modules in the src directory"
            )

            response_lower = response.text.lower()
            assert any(
                word in response_lower
                for word in ["import", "depend", "models", "services", "api"]
            ), f"Expected dependency analysis: {response.text[:300]}"
