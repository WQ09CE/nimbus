"""Tests for SimplePlanner."""

import unittest
import asyncio
from nimbus.core.planner import SimplePlanner
from nimbus.core.types import Plan, TaskType


class MockLLMClient:
    """Mock LLM client for testing."""

    def __init__(self, response: str):
        self.response = response
        self.last_prompt = None

    async def complete(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self.response


class TestSimplePlanner(unittest.TestCase):
    """Test cases for SimplePlanner class."""

    def test_extract_json_direct(self):
        """Test extracting direct JSON."""
        client = MockLLMClient("")
        planner = SimplePlanner(client)

        data = planner._extract_json('{"mode": "direct", "response": "Hello"}')
        self.assertEqual(data["mode"], "direct")
        self.assertEqual(data["response"], "Hello")

    def test_extract_json_from_code_block(self):
        """Test extracting JSON from markdown code block."""
        client = MockLLMClient("")
        planner = SimplePlanner(client)

        text = """Here's my plan:
```json
{"mode": "direct", "response": "Test"}
```
"""
        data = planner._extract_json(text)
        self.assertEqual(data["mode"], "direct")

    def test_extract_json_embedded(self):
        """Test extracting JSON embedded in text."""
        client = MockLLMClient("")
        planner = SimplePlanner(client)

        text = 'I think the answer is {"mode": "direct", "response": "OK"} yeah'
        data = planner._extract_json(text)
        self.assertEqual(data["mode"], "direct")

    def test_extract_json_invalid(self):
        """Test that invalid JSON raises error."""
        client = MockLLMClient("")
        planner = SimplePlanner(client)

        with self.assertRaises(Exception):
            planner._extract_json("no json here at all")

    def test_parse_direct_response(self):
        """Test parsing direct response plan."""
        client = MockLLMClient("")
        planner = SimplePlanner(client)

        response = '{"mode": "direct", "response": "Hello, how can I help?"}'
        plan = planner._parse_response(response)

        self.assertTrue(plan.is_direct())
        self.assertEqual(plan.direct_response, "Hello, how can I help?")
        self.assertEqual(len(plan.tasks), 0)

    def test_parse_multi_step_response(self):
        """Test parsing multi-step plan."""
        client = MockLLMClient("")
        planner = SimplePlanner(client)

        response = """{
            "mode": "multi_step",
            "tasks": [
                {"type": "chat", "skill": "chat", "params": {"message": "hi"}}
            ]
        }"""
        plan = planner._parse_response(response)

        self.assertFalse(plan.is_direct())
        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(plan.tasks[0].type, TaskType.CHAT)
        self.assertEqual(plan.tasks[0].skill, "chat")

    def test_parse_invalid_falls_back_to_direct(self):
        """Test that invalid JSON falls back to direct response."""
        client = MockLLMClient("")
        planner = SimplePlanner(client)

        response = "I don't understand the format, but here's my answer."
        plan = planner._parse_response(response)

        self.assertTrue(plan.is_direct())
        self.assertIn("answer", plan.direct_response)

    def test_create_plan_direct(self):
        """Test create_plan for direct response."""
        response = '{"mode": "direct", "response": "Hi there!"}'
        client = MockLLMClient(response)
        planner = SimplePlanner(client)

        plan = asyncio.run(
            planner.create_plan(
                goal="Say hello",
                context="",
                available_skills=["chat"],
            )
        )

        self.assertTrue(plan.is_direct())
        self.assertEqual(plan.direct_response, "Hi there!")

    def test_create_plan_multi_step(self):
        """Test create_plan for multi-step."""
        response = """{
            "mode": "multi_step",
            "tasks": [
                {"type": "analyze", "skill": "analyze", "params": {"data": "test"}}
            ]
        }"""
        client = MockLLMClient(response)
        planner = SimplePlanner(client)

        plan = asyncio.run(
            planner.create_plan(
                goal="Analyze the data",
                context="file.csv uploaded",
                available_skills=["chat", "analyze"],
            )
        )

        self.assertFalse(plan.is_direct())
        self.assertEqual(len(plan.tasks), 1)
        self.assertEqual(plan.tasks[0].type, TaskType.ANALYZE)


class TestPlan(unittest.TestCase):
    """Test cases for Plan dataclass."""

    def test_plan_direct_constructor(self):
        """Test Plan.direct() class method."""
        plan = Plan.direct("Hello")

        self.assertEqual(plan.mode, "direct")
        self.assertEqual(plan.direct_response, "Hello")
        self.assertEqual(plan.tasks, [])
        self.assertTrue(plan.is_direct())

    def test_plan_multi_step_constructor(self):
        """Test Plan.multi_step() class method."""
        from nimbus.core.types import Task

        task = Task(
            id="t1",
            type=TaskType.CHAT,
            skill="chat",
            params={"message": "hi"},
        )
        plan = Plan.multi_step([task])

        self.assertEqual(plan.mode, "multi_step")
        self.assertIsNone(plan.direct_response)
        self.assertEqual(len(plan.tasks), 1)
        self.assertFalse(plan.is_direct())


if __name__ == "__main__":
    unittest.main()
