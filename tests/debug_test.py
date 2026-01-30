"""Debug test for analyzing failed sandbox tests."""

import asyncio
import tempfile
from pathlib import Path

from nimbus.core.logging import setup_logging
from nimbus.apps.code_agent import CodeAgent


async def test_add_class_method():
    """Test adding a method to a class - this is one of the failing tests."""
    setup_logging(level='DEBUG', log_dir='.logs', console=True, enqueue=False)

    workspace = tempfile.mkdtemp()
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
    Path(workspace, 'cart.py').write_text(code)

    agent = CodeAgent(workspace=workspace, llm_provider='gemini')

    result = await agent.run(
        goal="Add a 'get_total' method to the ShoppingCart class in cart.py that calculates and returns the total price of all items (price * quantity for each item)",
        allowed_tools={'Read', 'Edit', 'Write', 'Glob', 'Grep', 'Bash'}
    )

    print(f"\n=== RESULT ===")
    print(f"Status: {result['status']}")
    print(f"Turns: {result['turns']}")
    print(f"Output: {result['output'][:500] if result['output'] else '(empty)'}")

    content = Path(workspace, 'cart.py').read_text()
    has_method = 'def get_total' in content or 'get_total' in content
    print(f"\n=== FILE CONTENT ===")
    print(content)
    print(f"\n=== HAS get_total: {has_method} ===")

    await agent.close()


if __name__ == '__main__':
    asyncio.run(test_add_class_method())
