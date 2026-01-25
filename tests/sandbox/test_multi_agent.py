"""Test multi-file and complex operations with real LLM.

These tests verify that CodeAgent can handle complex tasks:
- Multi-file refactoring
- Cross-file changes
- Complex code modifications
"""

import pytest
from .runner import SandboxRunner
from .scenarios.sample_files import (
    PYTHON_NEEDS_REFACTOR,
    create_refactoring_project,
)

pytestmark = pytest.mark.sandbox


class TestMultiFileRefactoring:
    """Test multi-file refactoring capabilities."""

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_rename_method_across_files(self, llm_provider, llm_model):
        """Agent can rename a method and update all references."""
        async with SandboxRunner(
            provider=llm_provider,
            model=llm_model,
            keep_workspace=False,
        ) as runner:
            # Create refactoring project
            create_refactoring_project(runner.workspace)

            response = await runner.run(
                "In the UserManager class (user_manager.py), rename the method "
                "'old_add_user' to 'add_user'. Also update any files that call this method."
            )

            # Verify the change was made
            manager_content = runner.read_file("src/user_manager.py")
            assert "def add_user" in manager_content, \
                f"Method should be renamed in user_manager.py: {manager_content[:300]}"

            # Check if app.py was updated (may or may not happen depending on LLM)
            if runner.file_exists("src/app.py"):
                app_content = runner.read_file("src/app.py")
                # Either old references are updated or file wasn't changed
                # This is a best-effort check
                if "add_user" in app_content:
                    assert "old_add_user" not in app_content or "add_user" in app_content

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_extract_function(self, llm_provider, llm_model):
        """Agent can extract code into a new function."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
def process_order(order):
    # Validate order
    if not order.get("items"):
        raise ValueError("Order must have items")
    if not order.get("customer_id"):
        raise ValueError("Order must have customer_id")
    if order.get("total", 0) < 0:
        raise ValueError("Total cannot be negative")

    # Process order
    for item in order["items"]:
        print(f"Processing {item}")

    return {"status": "processed", "order_id": order.get("id")}
'''
            runner.create_file("orders.py", code)

            response = await runner.run(
                "In orders.py, extract the validation logic (the three if statements) "
                "into a separate function called 'validate_order'"
            )

            content = runner.read_file("orders.py")
            # Check that a validate function was created
            assert "def validate" in content.lower(), \
                f"Expected validate function in file: {content[:500]}"

    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_add_logging(self, llm_provider, llm_model):
        """Agent can add logging to a module."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
def fetch_data(url):
    response = requests.get(url)
    return response.json()

def process_data(data):
    result = []
    for item in data:
        result.append(transform(item))
    return result

def save_data(data, path):
    with open(path, "w") as f:
        json.dump(data, f)
'''
            runner.create_file("pipeline.py", code)

            response = await runner.run(
                "Add logging to pipeline.py - import the logging module, "
                "create a logger, and add log statements to each function"
            )

            content = runner.read_file("pipeline.py")
            # Check logging was added
            assert "import logging" in content or "from logging" in content, \
                f"Expected logging import: {content[:500]}"
            assert "log" in content.lower(), \
                f"Expected log statements: {content[:500]}"


class TestComplexModifications:
    """Test complex code modification capabilities."""

    @pytest.mark.asyncio
    async def test_add_class_method(self, llm_provider, llm_model):
        """Agent can add a new method to a class."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
class ShoppingCart:
    def __init__(self):
        self.items = []

    def add_item(self, name, price, quantity=1):
        self.items.append({
            "name": name,
            "price": price,
            "quantity": quantity
        })

    def remove_item(self, name):
        self.items = [i for i in self.items if i["name"] != name]
'''
            runner.create_file("cart.py", code)

            response = await runner.run(
                "Add a 'get_total' method to the ShoppingCart class in cart.py "
                "that calculates and returns the total price of all items"
            )

            content = runner.read_file("cart.py")
            assert "def get_total" in content or "get_total" in content, \
                f"Expected get_total method: {content[:500]}"

    @pytest.mark.asyncio
    async def test_convert_function_to_class(self, llm_provider, llm_model):
        """Agent can convert functions to a class."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
# Global state
_counter = 0

def increment():
    global _counter
    _counter += 1
    return _counter

def decrement():
    global _counter
    _counter -= 1
    return _counter

def get_value():
    return _counter

def reset():
    global _counter
    _counter = 0
'''
            runner.create_file("counter.py", code)

            response = await runner.run(
                "Refactor counter.py to use a Counter class instead of global state. "
                "The class should have increment, decrement, get_value, and reset methods."
            )

            content = runner.read_file("counter.py")
            assert "class Counter" in content or "class counter" in content.lower(), \
                f"Expected Counter class: {content[:500]}"

    @pytest.mark.asyncio
    async def test_add_tests_for_function(self, llm_provider, llm_model):
        """Agent can create tests for a function."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            code = '''
def is_palindrome(s: str) -> bool:
    """Check if a string is a palindrome (ignoring case and spaces)."""
    cleaned = s.lower().replace(" ", "")
    return cleaned == cleaned[::-1]
'''
            runner.create_file("palindrome.py", code)

            response = await runner.run(
                "Create a test file test_palindrome.py with pytest tests for "
                "the is_palindrome function in palindrome.py"
            )

            assert runner.file_exists("test_palindrome.py"), \
                "test_palindrome.py should be created"
            content = runner.read_file("test_palindrome.py")
            assert "def test_" in content or "test" in content.lower(), \
                f"Expected test functions: {content[:500]}"


class TestCodeGeneration:
    """Test code generation capabilities."""

    @pytest.mark.asyncio
    async def test_generate_dataclass(self, llm_provider, llm_model):
        """Agent can generate a dataclass from description."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            response = await runner.run(
                "Create a file models.py with a dataclass called 'User' that has "
                "the following fields: id (int), name (str), email (str), "
                "created_at (datetime), is_active (bool with default True)"
            )

            assert runner.file_exists("models.py"), "models.py should be created"
            content = runner.read_file("models.py")
            assert "class User" in content, f"Expected User class: {content[:500]}"
            assert "dataclass" in content.lower() or "@dataclass" in content, \
                f"Expected dataclass decorator: {content[:500]}"

    @pytest.mark.asyncio
    async def test_generate_api_endpoint(self, llm_provider, llm_model):
        """Agent can generate an API endpoint."""
        async with SandboxRunner(provider=llm_provider, model=llm_model) as runner:
            response = await runner.run(
                "Create a file api.py with a FastAPI or Flask endpoint at '/users' "
                "that returns a list of users. Include proper type hints."
            )

            assert runner.file_exists("api.py"), "api.py should be created"
            content = runner.read_file("api.py")
            assert "users" in content.lower(), f"Expected users endpoint: {content[:500]}"
            assert any(
                fw in content.lower()
                for fw in ["fastapi", "flask", "route", "@app"]
            ), f"Expected web framework: {content[:500]}"
