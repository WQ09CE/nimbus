"""Cross-File Refactoring Capability Test.

This module tests an agent's ability to correctly perform cross-file API
migration/renaming tasks. It is designed to be a high-discrimination test
that separates strong models from weak ones.

Capability: cross_file_refactoring

Evaluation Criteria:
- Location Accuracy (30%): Whether all correct call sites were identified
- Modification Accuracy (30%): Whether modifications were applied correctly
- No False Positives (20%): Whether unrelated code was left untouched
- Tests Pass (20%): Whether tests pass after refactoring

Challenge Design:
- Multiple files need to be modified
- Some files have independent functions with the same name (trap)
- Documentation needs to be updated
- Tests must pass after refactoring
"""

import pytest
import shutil
import tempfile
import subprocess
import sys
from pathlib import Path
from typing import Optional, Dict, Any

from tests.evaluation.refactoring_metrics import (
    RefactoringScore,
    RefactoringExpectation,
    RefactoringEvaluator,
    create_api_migration_expectation,
    analyze_refactoring_diff,
)


# =============================================================================
# Test Data Paths
# =============================================================================

TEST_DATA_DIR = Path(__file__).parent.parent / "data" / "refactoring"
SAMPLE_PROJECT_PATH = TEST_DATA_DIR / "sample_project"
GOLDEN_PATH = TEST_DATA_DIR / "golden"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def workspace(tmp_path):
    """Create a temporary workspace with a copy of the sample project.

    Yields:
        Path to the temporary workspace containing the sample project copy.
    """
    workspace_path = tmp_path / "workspace"
    shutil.copytree(SAMPLE_PROJECT_PATH, workspace_path)
    yield workspace_path
    # Cleanup is handled by tmp_path


@pytest.fixture
def expectation():
    """Provide the standard API migration expectation."""
    return create_api_migration_expectation()


@pytest.fixture
def evaluator(expectation):
    """Create a refactoring evaluator with golden answer."""
    return RefactoringEvaluator(GOLDEN_PATH, expectation)


# =============================================================================
# Helper Functions
# =============================================================================


def run_tests_in_workspace(workspace_path: Path) -> bool:
    """Run pytest in the workspace and return whether tests pass.

    Args:
        workspace_path: Path to the workspace containing tests.

    Returns:
        True if all tests pass, False otherwise.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(workspace_path / "tests"), "-v", "--tb=short"],
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


def apply_mock_refactoring(workspace_path: Path, correct: bool = True) -> None:
    """Apply a mock refactoring for testing the evaluation framework.

    Args:
        workspace_path: Path to the workspace to refactor.
        correct: If True, apply correct refactoring. If False, apply incorrect.
    """
    if correct:
        # Copy golden files to workspace
        for golden_file in GOLDEN_PATH.rglob("*"):
            if golden_file.is_dir():
                continue
            rel_path = golden_file.relative_to(GOLDEN_PATH)
            workspace_file = workspace_path / rel_path
            workspace_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(golden_file, workspace_file)
    else:
        # Apply incorrect refactoring (rename ALL old_api, including in data.py)
        for py_file in workspace_path.rglob("*.py"):
            content = py_file.read_text()
            # Naively replace all occurrences (wrong approach)
            new_content = content.replace("old_api", "new_api")
            py_file.write_text(new_content)


# =============================================================================
# Tests: Evaluation Framework
# =============================================================================


@pytest.mark.capability("cross_file_refactoring")
class TestEvaluationFramework:
    """Tests for the evaluation framework itself."""

    def test_sample_project_exists(self):
        """Sample project directory should exist with expected structure."""
        assert SAMPLE_PROJECT_PATH.exists()
        assert (SAMPLE_PROJECT_PATH / "core" / "client.py").exists()
        assert (SAMPLE_PROJECT_PATH / "core" / "utils.py").exists()
        assert (SAMPLE_PROJECT_PATH / "services" / "auth.py").exists()
        assert (SAMPLE_PROJECT_PATH / "services" / "data.py").exists()
        assert (SAMPLE_PROJECT_PATH / "tests" / "test_client.py").exists()
        assert (SAMPLE_PROJECT_PATH / "README.md").exists()

    def test_golden_exists(self):
        """Golden answer directory should exist with expected structure."""
        assert GOLDEN_PATH.exists()
        assert (GOLDEN_PATH / "core" / "client.py").exists()
        assert (GOLDEN_PATH / "services" / "data.py").exists()

    def test_golden_has_correct_changes(self):
        """Golden should have old_api renamed to new_api in correct files."""
        # core/client.py should have new_api
        client_content = (GOLDEN_PATH / "core" / "client.py").read_text()
        assert "def new_api(" in client_content
        assert "def old_api(" not in client_content

        # services/data.py should still have old_api (independent function)
        data_content = (GOLDEN_PATH / "services" / "data.py").read_text()
        assert "def old_api(" in data_content

    def test_evaluator_initialization(self, evaluator, expectation):
        """Evaluator should initialize correctly."""
        assert evaluator.expectation == expectation
        assert len(evaluator._golden_contents) > 0

    def test_correct_refactoring_scores_high(self, workspace, evaluator):
        """Correct refactoring should achieve high scores."""
        # Apply correct refactoring
        apply_mock_refactoring(workspace, correct=True)

        # Evaluate
        score = evaluator.evaluate(workspace, tests_passed=True)

        assert score.location_accuracy >= 0.9
        assert score.modification_accuracy >= 0.9
        assert score.no_false_positive >= 0.9
        assert score.total >= 0.9

    def test_incorrect_refactoring_scores_low(self, workspace, evaluator):
        """Incorrect refactoring (changing data.py) should score lower."""
        # Apply incorrect refactoring (changes everything)
        apply_mock_refactoring(workspace, correct=False)

        # Evaluate
        score = evaluator.evaluate(workspace, tests_passed=True)

        # Should fail on no_false_positive because data.py was modified
        assert score.no_false_positive < 1.0

    def test_no_changes_scores_low(self, workspace, evaluator):
        """No changes at all should score low on location accuracy.

        Note: modification_accuracy uses similarity scoring, so even unchanged
        files will have high similarity to golden (since they differ only by
        the renamed method). The key metric for detecting missing changes is
        location_accuracy, which checks if old_api calls still exist.
        """
        # Don't apply any changes
        score = evaluator.evaluate(workspace, tests_passed=True)

        # Should fail on location_accuracy because nothing was changed
        # (old_api calls still exist in the workspace)
        assert score.location_accuracy < 0.5
        # Total score should be below threshold due to location_accuracy
        assert score.total < 0.8


# =============================================================================
# Tests: Refactoring Analysis
# =============================================================================


@pytest.mark.capability("cross_file_refactoring")
class TestRefactoringAnalysis:
    """Tests for refactoring analysis utilities."""

    def test_analyze_diff_correct_refactoring(self, workspace):
        """Analyze diff for correct refactoring."""
        apply_mock_refactoring(workspace, correct=True)

        analysis = analyze_refactoring_diff(SAMPLE_PROJECT_PATH, workspace)

        # Files that should be changed
        expected_changed = {
            "core/client.py",
            "core/utils.py",
            "services/auth.py",
            "tests/test_client.py",
            "README.md",
        }

        # Files that should NOT be changed
        expected_unchanged = {
            "services/data.py",
        }

        for file_path in expected_changed:
            assert file_path in analysis["files_changed"], f"{file_path} should be changed"

        for file_path in expected_unchanged:
            assert file_path in analysis["files_unchanged"], f"{file_path} should not be changed"

    def test_analyze_diff_counts_changes(self, workspace):
        """Analyze diff should count additions and deletions."""
        apply_mock_refactoring(workspace, correct=True)

        analysis = analyze_refactoring_diff(SAMPLE_PROJECT_PATH, workspace)

        # Should have some changes
        assert analysis["total_additions"] > 0
        assert analysis["total_deletions"] > 0


# =============================================================================
# Tests: Scoring Details
# =============================================================================


@pytest.mark.capability("cross_file_refactoring")
class TestScoringDetails:
    """Tests for detailed scoring behavior."""

    def test_score_weights(self):
        """Test that score weights sum correctly."""
        score = RefactoringScore(
            location_accuracy=1.0,
            modification_accuracy=1.0,
            no_false_positive=1.0,
            tests_pass=1.0,
        )
        # 0.3 + 0.3 + 0.2 + 0.2 = 1.0
        assert score.total == 1.0

        score2 = RefactoringScore(
            location_accuracy=0.5,
            modification_accuracy=0.5,
            no_false_positive=0.5,
            tests_pass=0.5,
        )
        assert score2.total == 0.5

    def test_score_string_representation(self):
        """Test score string representation."""
        score = RefactoringScore(
            location_accuracy=0.8,
            modification_accuracy=0.9,
            no_false_positive=1.0,
            tests_pass=0.5,
        )
        score_str = str(score)
        assert "location_accuracy=0.80" in score_str
        assert "modification_accuracy=0.90" in score_str
        assert "no_false_positive=1.00" in score_str
        assert "tests_pass=0.50" in score_str

    def test_partial_modification_accuracy(self, workspace, evaluator):
        """Partial modifications should give partial credit."""
        # Only modify client.py correctly
        golden_client = (GOLDEN_PATH / "core" / "client.py").read_text()
        (workspace / "core" / "client.py").write_text(golden_client)

        score = evaluator.evaluate(workspace, tests_passed=False)

        # Should have some credit but not full
        assert 0 < score.modification_accuracy < 1.0


# =============================================================================
# Tests: Task Description
# =============================================================================


@pytest.mark.capability("cross_file_refactoring")
class TestTaskDescription:
    """Tests related to the refactoring task description."""

    def test_task_description(self):
        """Verify the task description is clear and complete."""
        task = """
        Refactor the APIClient class to rename its `old_api()` method to `new_api()`.

        Requirements:
        1. Rename the method definition in core/client.py
        2. Update ALL call sites across the codebase:
           - core/utils.py
           - services/auth.py
           - tests/test_client.py
        3. Update documentation in README.md
        4. Ensure all tests pass after refactoring

        IMPORTANT:
        - Only modify the APIClient.old_api() method
        - Do NOT modify the standalone old_api() function in services/data.py
          (it is an independent function for legacy data format conversion)
        """

        # Task description should mention key points
        assert "old_api" in task
        assert "new_api" in task
        assert "APIClient" in task
        assert "services/data.py" in task
        assert "NOT" in task or "not" in task.lower()


# =============================================================================
# Tests: Integration with Agent (Mock)
# =============================================================================


@pytest.mark.capability("cross_file_refactoring")
class TestAgentIntegration:
    """Integration tests for agent-based refactoring (using mocks)."""

    @pytest.fixture
    def mock_agent_response(self):
        """Mock agent response simulating a correct refactoring."""
        return {
            "files_modified": [
                "core/client.py",
                "core/utils.py",
                "services/auth.py",
                "tests/test_client.py",
                "README.md",
            ],
            "files_preserved": [
                "services/data.py",
            ],
            "reasoning": (
                "I identified that APIClient.old_api() is called in utils.py, "
                "auth.py, and test_client.py. The old_api() function in data.py "
                "is independent and should not be modified."
            ),
        }

    def test_mock_agent_correct_file_identification(self, mock_agent_response, expectation):
        """Mock agent should correctly identify files to modify."""
        files_to_modify = set(mock_agent_response["files_modified"])
        files_to_preserve = set(mock_agent_response["files_preserved"])

        # Check files to modify
        for file_path in expectation.files_to_modify:
            assert file_path in files_to_modify, f"Agent should modify {file_path}"

        # Check files to preserve
        for file_path in expectation.files_to_preserve:
            assert file_path not in files_to_modify, f"Agent should NOT modify {file_path}"

    @pytest.mark.asyncio
    async def test_full_refactoring_workflow(self, workspace, evaluator):
        """Test the full refactoring workflow (using mock for now).

        This test simulates what would happen with a real agent:
        1. Agent receives the task
        2. Agent analyzes the codebase
        3. Agent performs refactoring
        4. Evaluator scores the result
        """
        # Simulate agent performing correct refactoring
        apply_mock_refactoring(workspace, correct=True)

        # Evaluate the result
        score = evaluator.evaluate(workspace, tests_passed=True)

        # Verify all metrics are high for correct refactoring
        assert score.location_accuracy >= 0.9, "Location accuracy should be high"
        assert score.modification_accuracy >= 0.9, "Modification accuracy should be high"
        assert score.no_false_positive >= 0.9, "Should not have false positives"
        assert score.tests_pass == 1.0, "Tests should pass"
        assert score.total >= 0.9, "Total score should be high"

        # Print detailed results for debugging
        print(f"\nRefactoring Score:\n{score}")
        print(f"\nDetails: {score.details}")


# =============================================================================
# Tests: Edge Cases
# =============================================================================


@pytest.mark.capability("cross_file_refactoring")
class TestEdgeCases:
    """Edge case tests for the refactoring evaluation."""

    def test_empty_workspace(self, tmp_path, evaluator):
        """Empty workspace should score zero."""
        empty_workspace = tmp_path / "empty"
        empty_workspace.mkdir()

        score = evaluator.evaluate(empty_workspace, tests_passed=False)

        assert score.location_accuracy == 0.0
        assert score.modification_accuracy == 0.0

    def test_partial_refactoring(self, workspace, evaluator):
        """Partial refactoring should get partial credit."""
        # Only modify the main client.py file
        golden_client = (GOLDEN_PATH / "core" / "client.py").read_text()
        (workspace / "core" / "client.py").write_text(golden_client)

        score = evaluator.evaluate(workspace, tests_passed=False)

        # Should have some credit
        assert score.modification_accuracy > 0.0
        # But not full credit
        assert score.modification_accuracy < 1.0

    def test_extra_files_modified(self, workspace, evaluator):
        """Extra file modifications should not affect score negatively."""
        apply_mock_refactoring(workspace, correct=True)

        # Add an extra file
        extra_file = workspace / "extra.py"
        extra_file.write_text("# Extra file\n")

        score = evaluator.evaluate(workspace, tests_passed=True)

        # Should still score high
        assert score.total >= 0.9

    def test_failed_tests_reduces_score(self, workspace, evaluator):
        """Failed tests should reduce the total score."""
        apply_mock_refactoring(workspace, correct=True)

        # Evaluate with tests failed
        score_fail = evaluator.evaluate(workspace, tests_passed=False)

        # Evaluate with tests passed
        score_pass = evaluator.evaluate(workspace, tests_passed=True)

        # Tests passing should give higher total score
        assert score_pass.total > score_fail.total
        assert score_fail.tests_pass == 0.0
        assert score_pass.tests_pass == 1.0


# =============================================================================
# Benchmark Tests (for real agent evaluation)
# =============================================================================


@pytest.mark.capability("cross_file_refactoring")
@pytest.mark.slow
class TestBenchmark:
    """Benchmark tests for measuring agent performance.

    These tests are designed to be run with real agents to measure
    their cross-file refactoring capabilities.
    """

    @pytest.fixture
    def benchmark_task(self):
        """Provide the benchmark task description."""
        return """
        ## Task: API Migration

        Rename the `old_api()` method of the `APIClient` class to `new_api()`.

        ### Requirements

        1. **Method Definition**: Rename `def old_api(...)` to `def new_api(...)`
           in `core/client.py`

        2. **Call Sites**: Update all calls to `client.old_api()` or
           `self.client.old_api()` across the codebase

        3. **Documentation**: Update references in README.md

        4. **Tests**: Update test method names and assertions

        ### CRITICAL WARNING

        The file `services/data.py` contains an INDEPENDENT function named
        `old_api()` that is NOT related to `APIClient.old_api()`.

        DO NOT modify `services/data.py`. This function handles legacy data
        format conversion and must remain unchanged.

        ### Success Criteria

        - All `APIClient.old_api()` calls are renamed to `new_api()`
        - `services/data.py` is NOT modified
        - All tests pass after refactoring
        """

    def test_benchmark_task_clarity(self, benchmark_task):
        """Verify benchmark task is clear and includes warnings."""
        # Task should clearly state what to change
        assert "old_api" in benchmark_task
        assert "new_api" in benchmark_task
        assert "APIClient" in benchmark_task

        # Task should warn about the trap
        assert "services/data.py" in benchmark_task
        assert "NOT" in benchmark_task or "not" in benchmark_task.lower()
        assert "INDEPENDENT" in benchmark_task or "independent" in benchmark_task.lower()

    @pytest.mark.skip(reason="Requires real agent implementation")
    async def test_agent_refactoring_benchmark(self, workspace, evaluator, benchmark_task):
        """Benchmark test for real agent evaluation.

        This test should be enabled when testing with a real agent.
        It measures the agent's ability to:
        1. Understand the refactoring task
        2. Identify all relevant call sites
        3. Avoid false positives (the trap in data.py)
        4. Produce valid code that passes tests
        """
        # TODO: Replace with actual agent call
        # result = await agent.execute(benchmark_task, workspace=workspace)

        # Evaluate the result
        tests_passed = run_tests_in_workspace(workspace)
        score = evaluator.evaluate(workspace, tests_passed=tests_passed)

        # Store results for analysis
        results = {
            "task": "api_migration",
            "score": {
                "location_accuracy": score.location_accuracy,
                "modification_accuracy": score.modification_accuracy,
                "no_false_positive": score.no_false_positive,
                "tests_pass": score.tests_pass,
                "total": score.total,
            },
            "details": score.details,
        }

        print(f"\nBenchmark Results:\n{results}")

        # Minimum threshold for passing
        assert score.total >= 0.7, f"Agent failed benchmark with score {score.total}"
