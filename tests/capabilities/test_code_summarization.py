"""Tests for code summarization capability.

This module tests the agent's ability to accurately summarize code,
understand functions and classes, and avoid hallucinations.

Capability: code_summarization
"""

import pytest
from typing import List, Optional

from tests.evaluation.metrics import (
    CodeSummarizationMetrics,
    SummarizationExpectation,
)


# =============================================================================
# Sample Code Snippets for Testing
# =============================================================================

SAMPLE_FUNCTION = '''
def calculate_fibonacci(n: int) -> int:
    """Calculate the nth Fibonacci number.

    Args:
        n: The position in the Fibonacci sequence (0-indexed).

    Returns:
        The nth Fibonacci number.

    Raises:
        ValueError: If n is negative.
    """
    if n < 0:
        raise ValueError("n must be non-negative")
    if n <= 1:
        return n

    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
'''

SAMPLE_CLASS = '''
class UserManager:
    """Manages user accounts and authentication.

    Attributes:
        db: Database connection for user storage.
        cache: Optional cache for session data.
    """

    def __init__(self, db_connection, cache=None):
        self.db = db_connection
        self.cache = cache
        self._sessions = {}

    def create_user(self, username: str, email: str) -> int:
        """Create a new user account."""
        user_id = self.db.insert_user(username, email)
        return user_id

    def authenticate(self, username: str, password: str) -> bool:
        """Authenticate a user with credentials."""
        user = self.db.get_user(username)
        return user and user.verify_password(password)

    def get_session(self, user_id: int) -> dict:
        """Retrieve user session data."""
        if self.cache and user_id in self._sessions:
            return self._sessions[user_id]
        return self.db.get_session(user_id)
'''

SAMPLE_MODULE = '''
"""
Data Processing Module

This module provides utilities for processing and transforming data.
It includes functions for filtering, mapping, and aggregating datasets.
"""

import pandas as pd
from typing import List, Callable, Any

def filter_data(data: pd.DataFrame, condition: Callable) -> pd.DataFrame:
    """Filter dataframe rows based on a condition function."""
    return data[data.apply(condition, axis=1)]

def transform_column(data: pd.DataFrame, column: str, func: Callable) -> pd.DataFrame:
    """Apply transformation function to a specific column."""
    result = data.copy()
    result[column] = result[column].apply(func)
    return result

class DataPipeline:
    """A configurable data processing pipeline."""

    def __init__(self):
        self.steps = []

    def add_step(self, step: Callable):
        """Add a processing step to the pipeline."""
        self.steps.append(step)

    def run(self, data: pd.DataFrame) -> pd.DataFrame:
        """Execute all pipeline steps on the data."""
        result = data
        for step in self.steps:
            result = step(result)
        return result
'''


# =============================================================================
# Test Cases
# =============================================================================


@pytest.mark.capability("code_summarization")
class TestFileSummaryAccuracy:
    """Tests for file summary accuracy."""

    @pytest.fixture
    def metrics(self):
        return CodeSummarizationMetrics()

    def test_file_summary_accuracy_perfect(self, metrics):
        """Perfect summary should mention all key concepts."""
        summary = """
        This module provides data processing utilities including filtering,
        transforming, and pipeline-based processing. It uses pandas DataFrame
        and includes a DataPipeline class for chaining operations.
        """

        expectation = SummarizationExpectation(
            required_mentions=["data", "processing", "DataFrame", "pipeline"],
            min_coverage=0.7,
        )

        results = metrics.evaluate(SAMPLE_MODULE, summary, expectation)
        summary_dict = metrics.summary(results)

        assert summary_dict["coverage"] == 1.0
        assert summary_dict["meets_min_coverage"] == 1.0

    def test_file_summary_accuracy_partial(self, metrics):
        """Partial summary should score proportionally."""
        summary = """
        A module for working with data. It has some functions for filtering.
        """

        expectation = SummarizationExpectation(
            required_mentions=["data", "filtering", "pipeline", "transform"],
            min_coverage=0.7,
        )

        results = metrics.evaluate(SAMPLE_MODULE, summary, expectation)
        summary_dict = metrics.summary(results)

        # Only "data" and "filtering" mentioned (2/4 = 0.5)
        assert summary_dict["coverage"] == 0.5
        assert summary_dict["meets_min_coverage"] == 0.0

    def test_file_summary_length_constraint(self, metrics):
        """Summary should respect length constraints."""
        short_summary = "A data processing module."
        long_summary = "A" * 1000

        expectation = SummarizationExpectation(
            max_length=100,
        )

        # Short summary should pass
        results = metrics.evaluate(SAMPLE_MODULE, short_summary, expectation)
        summary_dict = metrics.summary(results)
        assert summary_dict["within_length"] == 1.0

        # Long summary should fail
        results = metrics.evaluate(SAMPLE_MODULE, long_summary, expectation)
        summary_dict = metrics.summary(results)
        assert summary_dict["within_length"] == 0.0


@pytest.mark.capability("code_summarization")
class TestFunctionUnderstanding:
    """Tests for function understanding."""

    @pytest.fixture
    def metrics(self):
        return CodeSummarizationMetrics()

    def test_function_understanding_complete(self, metrics):
        """Complete understanding should identify all function aspects."""
        summary = """
        The calculate_fibonacci function computes the nth Fibonacci number.
        It takes an integer parameter n and returns an integer result.
        Raises ValueError for negative inputs.
        """

        result = metrics.evaluate_function_understanding(
            function_code=SAMPLE_FUNCTION,
            summary=summary,
            expected_params=["n"],
            expected_return="int",
        )

        assert result.value == 1.0
        assert result.details["params_covered"] == 1
        assert result.details["return_mentioned"] is True

    def test_function_understanding_partial(self, metrics):
        """Partial understanding should score proportionally."""
        summary = """
        A function that calculates Fibonacci numbers using an iterative approach.
        """

        result = metrics.evaluate_function_understanding(
            function_code=SAMPLE_FUNCTION,
            summary=summary,
            expected_params=["n", "max_value"],  # max_value doesn't exist
            expected_return="integer",
        )

        # Only partial match
        assert result.value < 1.0

    def test_function_understanding_no_params(self, metrics):
        """Function with no expected params should handle gracefully."""
        summary = "Returns the current timestamp."

        result = metrics.evaluate_function_understanding(
            function_code="def now(): return time.time()",
            summary=summary,
            expected_params=[],
            expected_return="timestamp",
        )

        # Return type mentioned
        assert result.value == 1.0


@pytest.mark.capability("code_summarization")
class TestClassUnderstanding:
    """Tests for class understanding."""

    @pytest.fixture
    def metrics(self):
        return CodeSummarizationMetrics()

    def test_class_understanding_complete(self, metrics):
        """Complete class understanding should identify methods and attributes."""
        summary = """
        UserManager class handles user accounts and authentication.
        Methods include create_user for new accounts, authenticate for login,
        and get_session for retrieving session data. Uses db connection and
        optional cache.
        """

        result = metrics.evaluate_class_understanding(
            class_code=SAMPLE_CLASS,
            summary=summary,
            expected_methods=["create_user", "authenticate", "get_session"],
            expected_attributes=["db", "cache"],
        )

        assert result.value == 1.0

    def test_class_understanding_partial_methods(self, metrics):
        """Partial method coverage should score proportionally."""
        summary = """
        UserManager handles user creation and authentication.
        It uses a database connection for persistence.
        """

        result = metrics.evaluate_class_understanding(
            class_code=SAMPLE_CLASS,
            summary=summary,
            expected_methods=["create_user", "authenticate", "get_session", "delete_user"],
            expected_attributes=["db", "cache"],
        )

        # Not all methods or attributes mentioned
        assert result.value < 1.0

    def test_class_understanding_no_expectations(self, metrics):
        """Empty expectations should return 1.0."""
        result = metrics.evaluate_class_understanding(
            class_code=SAMPLE_CLASS,
            summary="A class for managing users.",
            expected_methods=[],
            expected_attributes=[],
        )

        assert result.value == 1.0


@pytest.mark.capability("code_summarization")
class TestHallucinationDetection:
    """Tests for detecting hallucinations in summaries."""

    @pytest.fixture
    def metrics(self):
        return CodeSummarizationMetrics()

    def test_hallucination_detection_clean(self, metrics):
        """Summary without hallucinations should pass."""
        summary = """
        The calculate_fibonacci function computes Fibonacci numbers iteratively.
        It takes a single parameter n and returns the corresponding number.
        """

        expectation = SummarizationExpectation(
            required_mentions=["fibonacci"],
            forbidden_mentions=["recursive", "memoization", "cache", "decorator"],
        )

        results = metrics.evaluate(SAMPLE_FUNCTION, summary, expectation)
        summary_dict = metrics.summary(results)

        assert summary_dict["no_hallucination"] == 1.0

    def test_hallucination_detection_false_claims(self, metrics):
        """Summary with false claims should be flagged."""
        summary = """
        The calculate_fibonacci function uses recursive calls with memoization
        for efficient computation. It employs a decorator for caching results.
        """

        expectation = SummarizationExpectation(
            required_mentions=["fibonacci"],
            forbidden_mentions=["recursive", "memoization", "decorator"],
        )

        results = metrics.evaluate(SAMPLE_FUNCTION, summary, expectation)
        summary_dict = metrics.summary(results)

        # Contains forbidden (hallucinated) terms
        assert summary_dict["no_hallucination"] < 1.0

    def test_hallucination_detection_invented_methods(self, metrics):
        """Invented methods or attributes should be detected."""
        summary = """
        UserManager class provides user management including delete_user,
        update_profile, and send_notification methods.
        """

        expectation = SummarizationExpectation(
            forbidden_mentions=["delete_user", "update_profile", "send_notification"],
        )

        results = metrics.evaluate(SAMPLE_CLASS, summary, expectation)
        summary_dict = metrics.summary(results)

        # Contains invented methods
        assert summary_dict["no_hallucination"] == 0.0


@pytest.mark.capability("code_summarization")
class TestSummarizationEdgeCases:
    """Edge cases for code summarization."""

    @pytest.fixture
    def metrics(self):
        return CodeSummarizationMetrics()

    def test_empty_code(self, metrics):
        """Empty code should be handled gracefully."""
        expectation = SummarizationExpectation()
        results = metrics.evaluate("", "No code provided.", expectation)
        summary_dict = metrics.summary(results)

        # Should not crash
        assert summary_dict["coverage"] == 1.0

    def test_empty_summary(self, metrics):
        """Empty summary should score poorly."""
        expectation = SummarizationExpectation(
            required_mentions=["function", "data"],
        )
        results = metrics.evaluate(SAMPLE_FUNCTION, "", expectation)
        summary_dict = metrics.summary(results)

        assert summary_dict["coverage"] == 0.0

    def test_case_insensitive_matching(self, metrics):
        """Matching should be case-insensitive."""
        summary = "A FIBONACCI calculator function."

        expectation = SummarizationExpectation(
            required_mentions=["fibonacci", "function"],
        )

        results = metrics.evaluate(SAMPLE_FUNCTION, summary, expectation)
        summary_dict = metrics.summary(results)

        # Should match despite case differences
        assert summary_dict["coverage"] == 1.0

    def test_no_required_mentions(self, metrics):
        """No required mentions should default to 1.0 coverage."""
        expectation = SummarizationExpectation()
        results = metrics.evaluate(SAMPLE_FUNCTION, "Any summary.", expectation)
        summary_dict = metrics.summary(results)

        assert summary_dict["coverage"] == 1.0

    def test_no_forbidden_mentions(self, metrics):
        """No forbidden mentions should default to 1.0 for no_hallucination."""
        expectation = SummarizationExpectation()
        results = metrics.evaluate(SAMPLE_FUNCTION, "Any summary.", expectation)
        summary_dict = metrics.summary(results)

        assert summary_dict["no_hallucination"] == 1.0


@pytest.mark.capability("code_summarization")
class TestSummarizationMetricsIntegration:
    """Integration tests for summarization metrics."""

    @pytest.fixture
    def metrics(self):
        return CodeSummarizationMetrics()

    def test_full_evaluation(self, metrics):
        """Full evaluation with all expectations."""
        summary = """
        The DataPipeline class provides a configurable data processing pipeline.
        It has an add_step method for adding processing steps and a run method
        for executing the pipeline on pandas DataFrame data.
        """

        expectation = SummarizationExpectation(
            required_mentions=["DataPipeline", "add_step", "run", "DataFrame"],
            forbidden_mentions=["delete_step", "pause", "resume"],
            max_length=500,
            min_coverage=0.75,
        )

        results = metrics.evaluate(SAMPLE_MODULE, summary, expectation)
        summary_dict = metrics.summary(results)

        assert summary_dict["coverage"] == 1.0
        assert summary_dict["no_hallucination"] == 1.0
        assert summary_dict["within_length"] == 1.0
        assert summary_dict["meets_min_coverage"] == 1.0

    def test_combined_function_and_class_understanding(self, metrics):
        """Combined understanding of functions and classes."""
        combined_code = SAMPLE_FUNCTION + "\n\n" + SAMPLE_CLASS

        # Function understanding
        func_result = metrics.evaluate_function_understanding(
            function_code=SAMPLE_FUNCTION,
            summary="Calculates Fibonacci using parameter n and returns int.",
            expected_params=["n"],
            expected_return="int",
        )

        # Class understanding
        class_result = metrics.evaluate_class_understanding(
            class_code=SAMPLE_CLASS,
            summary="UserManager with create_user, authenticate methods and db attribute.",
            expected_methods=["create_user", "authenticate"],
            expected_attributes=["db"],
        )

        assert func_result.value == 1.0
        assert class_result.value == 1.0
