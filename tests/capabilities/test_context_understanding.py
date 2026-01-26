"""Tests for context understanding capability.

This module tests the agent's ability to understand and utilize
conversation context, including pronoun resolution, cross-turn
references, and information recall.

Capability: context_understanding
"""

import pytest
from typing import Dict, List

from src.nimbus.core.memory import TieredMemoryManager, MemoryConfig, PinnedItem

from tests.evaluation.metrics import (
    ContextUnderstandingMetrics,
    ContextTestCase,
)


# =============================================================================
# Mock Agent Response Generator
# =============================================================================


class MockContextAgent:
    """Mock agent that generates responses based on context."""

    def __init__(self, memory: TieredMemoryManager):
        self.memory = memory

    def respond(self, query: str) -> str:
        """Generate response based on memory context.

        This simulates an agent using context to resolve references.
        """
        context = self.memory.get_context()

        # Simple heuristic-based responses for testing
        query_lower = query.lower()

        # Handle pronoun resolution
        if "it" in query_lower and "python" in context.lower():
            return "It refers to Python, a programming language."

        if "this" in query_lower and "project" in context.lower():
            return "This refers to the project we discussed earlier."

        if "that file" in query_lower:
            # Look for file mentions in context
            if "main.py" in context:
                return "That file is main.py which contains the entry point."
            if "config.yaml" in context:
                return "That file is config.yaml with configuration settings."

        # Handle cross-turn references
        if "earlier" in query_lower or "before" in query_lower:
            if "alice" in context.lower():
                return "Earlier, we discussed that Alice is the user."
            if "project x" in context.lower():
                return "Before, we established this is for Project X."

        # Handle information recall
        if "name" in query_lower:
            if "alice" in context.lower():
                return "The user's name is Alice."

        if "deadline" in query_lower:
            if "march" in context.lower():
                return "The deadline is March 15th."

        # Default response with context reference
        return f"Based on our conversation: {query}"


# =============================================================================
# Test Cases
# =============================================================================


@pytest.mark.capability("context_understanding")
class TestPronounResolution:
    """Tests for pronoun resolution in context."""

    @pytest.fixture
    def metrics(self):
        return ContextUnderstandingMetrics()

    def test_pronoun_resolution_it(self, metrics):
        """Pronouns like 'it' should resolve to the correct referent."""
        memory = TieredMemoryManager()

        # Build context with clear referent
        memory.add_turn_sync("user", "Tell me about Python")
        memory.add_turn_sync("assistant", "Python is a versatile programming language.")
        memory.add_turn_sync("user", "What can I build with it?")

        agent = MockContextAgent(memory)
        response = agent.respond("What can I build with it?")

        result = metrics.evaluate_pronoun_resolution(
            context=memory.get_context(),
            query="What can I build with it?",
            agent_response=response,
            expected_reference="Python",
        )

        assert result.value == 1.0

    def test_pronoun_resolution_this(self, metrics):
        """Demonstrative 'this' should resolve correctly."""
        memory = TieredMemoryManager()

        # Build context
        memory.add_turn_sync("user", "I'm working on a project for image recognition")
        memory.add_turn_sync("assistant", "Image recognition is a great application of ML.")
        memory.add_turn_sync("user", "How difficult is this?")

        agent = MockContextAgent(memory)
        response = agent.respond("How difficult is this project?")

        result = metrics.evaluate_pronoun_resolution(
            context=memory.get_context(),
            query="How difficult is this?",
            agent_response=response,
            expected_reference="project",
        )

        assert result.value == 1.0

    def test_pronoun_resolution_chinese(self, metrics):
        """Chinese pronouns should also resolve correctly."""
        memory = TieredMemoryManager()

        memory.add_turn_sync("user", "I want to learn about TensorFlow")
        memory.add_turn_sync("assistant", "TensorFlow is a machine learning framework.")

        agent = MockContextAgent(memory)
        # Simulate query with implicit reference
        response = "TensorFlow can be used for deep learning and neural networks."

        result = metrics.evaluate_pronoun_resolution(
            context=memory.get_context(),
            query="Tell me more",  # Implicit reference to TensorFlow
            agent_response=response,
            expected_reference="TensorFlow",
        )

        assert result.value == 1.0


@pytest.mark.capability("context_understanding")
class TestCrossTurnReference:
    """Tests for cross-turn reference understanding."""

    @pytest.fixture
    def metrics(self):
        return ContextUnderstandingMetrics()

    def test_cross_turn_reference_explicit(self, metrics):
        """Explicit references to earlier turns should be understood."""
        memory = TieredMemoryManager()

        # Build multi-turn context
        memory.add_turn_sync("user", "My name is Alice")
        memory.add_turn_sync("assistant", "Nice to meet you, Alice!")
        memory.add_turn_sync("user", "I work at Tech Corp")
        memory.add_turn_sync("assistant", "Tech Corp is a great company.")
        memory.add_turn_sync("user", "What did I tell you about myself earlier?")

        agent = MockContextAgent(memory)
        response = agent.respond("What did I tell you about myself earlier?")

        result = metrics.evaluate_cross_turn_reference(
            turns=[
                {"role": "user", "content": "My name is Alice"},
                {"role": "assistant", "content": "Nice to meet you, Alice!"},
            ],
            query="What did I tell you about myself earlier?",
            agent_response=response,
            expected_info="Alice",
        )

        assert result.value == 1.0

    def test_cross_turn_reference_implicit(self, metrics):
        """Implicit references should also be understood."""
        memory = TieredMemoryManager()

        # Pin important context
        memory.pin(PinnedItem(
            id="project",
            type="key_entity",
            content="Working on Project X for client ABC",
            priority=10,
        ))

        memory.add_turn_sync("user", "How should we structure the code?")
        memory.add_turn_sync("assistant", "For Project X, I recommend a modular structure.")

        # Simulate an agent response that correctly references the project
        # (In real tests, this would come from an actual LLM)
        response = "We are discussing Project X for client ABC."

        result = metrics.evaluate_cross_turn_reference(
            turns=[],
            query="What project are we discussing?",
            agent_response=response,
            expected_info="Project X",
        )

        assert result.value == 1.0


@pytest.mark.capability("context_understanding")
class TestInformationRecall:
    """Tests for information recall from context."""

    @pytest.fixture
    def metrics(self):
        return ContextUnderstandingMetrics()

    def test_information_recall_single_fact(self, metrics):
        """Single facts from context should be recalled accurately."""
        memory = TieredMemoryManager()

        memory.add_turn_sync("user", "The deadline for this project is March 15th")
        memory.add_turn_sync("assistant", "I've noted the March 15th deadline.")
        memory.add_turn_sync("user", "We have a budget of $50,000")
        memory.add_turn_sync("assistant", "Budget noted: $50,000")

        agent = MockContextAgent(memory)
        response = agent.respond("When is the deadline?")

        result = metrics.evaluate_information_recall(
            context=memory.get_context(),
            query="When is the deadline?",
            agent_response=response,
            required_facts=["March"],
        )

        assert result.value == 1.0

    def test_information_recall_multiple_facts(self, metrics):
        """Multiple facts should be recalled together."""
        memory = TieredMemoryManager()

        # Pin multiple facts
        memory.pin(PinnedItem(
            id="user_info",
            type="user_instruction",
            content="User: Alice, Role: Developer, Team: Backend",
            priority=10,
        ))

        # Simulate response that recalls facts
        response = "Based on our conversation: Alice is a Developer on the Backend team."

        result = metrics.evaluate_information_recall(
            context=memory.get_context(),
            query="Summarize what you know about me",
            agent_response=response,
            required_facts=["Alice", "Developer", "Backend"],
        )

        assert result.value == 1.0

    def test_information_recall_partial(self, metrics):
        """Partial recall should be scored proportionally."""
        memory = TieredMemoryManager()

        # Response that only recalls some facts
        response = "I know that Alice is on the team."

        result = metrics.evaluate_information_recall(
            context="",
            query="What do you know?",
            agent_response=response,
            required_facts=["Alice", "Developer", "Backend", "Manager"],
        )

        # Only Alice is mentioned, so 1/4 = 0.25
        assert result.value == 0.25


@pytest.mark.capability("context_understanding")
class TestContextWindowUtilization:
    """Tests for context window utilization efficiency."""

    @pytest.fixture
    def metrics(self):
        return ContextUnderstandingMetrics()

    def test_context_window_utilization_efficient(self, metrics):
        """High utilization with relevant content should score well."""
        result = metrics.evaluate_context_window_utilization(
            context_tokens=8000,
            window_size=16000,
            relevance_score=0.9,
        )

        # 50% utilization * 90% relevance = 0.45
        assert result.value == pytest.approx(0.45, rel=0.01)

    def test_context_window_utilization_low(self, metrics):
        """Low utilization should result in lower score."""
        result = metrics.evaluate_context_window_utilization(
            context_tokens=1000,
            window_size=16000,
            relevance_score=0.9,
        )

        # 6.25% utilization * 90% relevance = 0.05625
        assert result.value < 0.1

    def test_context_window_utilization_irrelevant(self, metrics):
        """High utilization with irrelevant content should score poorly."""
        result = metrics.evaluate_context_window_utilization(
            context_tokens=14000,
            window_size=16000,
            relevance_score=0.1,  # Low relevance
        )

        # 87.5% utilization * 10% relevance = 0.0875
        assert result.value < 0.1


@pytest.mark.capability("context_understanding")
class TestContextTestCase:
    """Tests using ContextTestCase dataclass."""

    @pytest.fixture
    def metrics(self):
        return ContextUnderstandingMetrics()

    def test_pronoun_test_case(self, metrics):
        """Test pronoun resolution using ContextTestCase."""
        test_case = ContextTestCase(
            context="User asked about Python programming. Python is versatile.",
            query="What can I do with it?",
            expected_reference="Python",
            test_type="pronoun",
        )

        response = "You can use Python for web development, data science, and more."

        results = metrics.evaluate(test_case, response)

        assert len(results) == 1
        assert results[0].name == "pronoun_resolution"
        assert results[0].value == 1.0

    def test_cross_turn_test_case(self, metrics):
        """Test cross-turn reference using ContextTestCase."""
        test_case = ContextTestCase(
            context="User mentioned working on ProjectAlpha.",
            query="What was the project name again?",
            expected_reference="ProjectAlpha",
            test_type="cross_turn",
        )

        response = "You mentioned working on ProjectAlpha earlier."

        results = metrics.evaluate(test_case, response)

        assert len(results) == 1
        assert results[0].name == "cross_turn_reference"
        assert results[0].value == 1.0

    def test_recall_test_case(self, metrics):
        """Test information recall using ContextTestCase."""
        test_case = ContextTestCase(
            context="The server runs on port 8080 and uses PostgreSQL database.",
            query="What port does the server use?",
            expected_reference="8080",
            test_type="recall",
        )

        response = "The server runs on port 8080."

        results = metrics.evaluate(test_case, response)

        assert len(results) == 1
        assert results[0].name == "information_recall"
        assert results[0].value == 1.0


@pytest.mark.capability("context_understanding")
class TestContextUnderstandingEdgeCases:
    """Edge cases for context understanding."""

    @pytest.fixture
    def metrics(self):
        return ContextUnderstandingMetrics()

    def test_empty_context(self, metrics):
        """Empty context should be handled gracefully."""
        result = metrics.evaluate_pronoun_resolution(
            context="",
            query="What is it?",
            agent_response="I don't have context about what 'it' refers to.",
            expected_reference="Python",
        )

        # Should not find the reference in response
        assert result.value == 0.0

    def test_empty_required_facts(self, metrics):
        """Empty required facts list should return 1.0."""
        result = metrics.evaluate_information_recall(
            context="Some context",
            query="What do you know?",
            agent_response="Some response",
            required_facts=[],
        )

        assert result.value == 1.0

    def test_case_insensitive_matching(self, metrics):
        """Matching should be case-insensitive."""
        result = metrics.evaluate_pronoun_resolution(
            context="Discussing PYTHON programming",
            query="What can it do?",
            agent_response="python can do many things",
            expected_reference="Python",
        )

        # Should match despite case differences
        assert result.value == 1.0

    def test_zero_window_size(self, metrics):
        """Zero window size should not cause division by zero."""
        result = metrics.evaluate_context_window_utilization(
            context_tokens=1000,
            window_size=0,
            relevance_score=0.9,
        )

        # Should handle gracefully
        assert result.value == 0.0
