"""Tests for bash execution capability.

This module tests the Bash tool's command execution, error handling,
timeout handling, and safety checks.

Capability: bash_execution
"""

import pytest
import asyncio
from pathlib import Path

from src.nimbus.tools.bash import bash_command

from tests.evaluation.metrics import (
    BashExecutionMetrics,
    BashExpectation,
)


# =============================================================================
# Test Cases
# =============================================================================


@pytest.mark.capability("bash_execution")
class TestBasicCommandExecution:
    """Tests for basic command execution."""

    @pytest.fixture
    def metrics(self):
        return BashExecutionMetrics()

    @pytest.mark.asyncio
    async def test_basic_command_execution(self, tmp_path, metrics):
        """Basic commands should execute and return output."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!\n")

        # Execute cat command (or equivalent)
        result = await bash_command(
            f"cat {test_file}",
            workspace=tmp_path,
        )

        assert "Hello, World!" in result

        # Evaluate with metrics
        expectation = BashExpectation(
            expected_exit_code=0,
            expected_output_contains=["Hello"],
        )

        metrics_results = metrics.evaluate(
            exit_code=0,
            stdout=result,
            stderr="",
            execution_time=0.1,
            expectation=expectation,
        )
        summary = metrics.summary(metrics_results)

        assert summary["exit_code_correct"] == 1.0
        assert summary["output_contains"] == 1.0

    @pytest.mark.asyncio
    async def test_command_with_arguments(self, tmp_path, metrics):
        """Commands with arguments should work correctly."""
        # Create test files
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")

        result = await bash_command(
            f"ls -1 {tmp_path}",
            workspace=tmp_path,
        )

        assert "file1.txt" in result
        assert "file2.txt" in result

    @pytest.mark.asyncio
    async def test_command_with_pipe(self, tmp_path, metrics):
        """Piped commands should execute correctly."""
        # Create test file with multiple lines
        test_file = tmp_path / "lines.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5\n")

        result = await bash_command(
            f"cat {test_file} | wc -l",
            workspace=tmp_path,
        )

        # Should contain "5" (5 lines)
        assert "5" in result.strip()

    @pytest.mark.asyncio
    async def test_command_no_output(self, tmp_path, metrics):
        """Commands with no output should return appropriate message."""
        # Create an empty file silently
        result = await bash_command(
            f"touch {tmp_path}/empty.txt",
            workspace=tmp_path,
        )

        # Should indicate no output or return empty
        assert result == "(no output)" or result.strip() == ""


@pytest.mark.capability("bash_execution")
class TestErrorHandling:
    """Tests for error handling in bash execution."""

    @pytest.fixture
    def metrics(self):
        return BashExecutionMetrics()

    @pytest.mark.asyncio
    async def test_error_handling_file_not_found(self, tmp_path, metrics):
        """Missing file errors should be handled gracefully."""
        result = await bash_command(
            f"cat {tmp_path}/nonexistent.txt",
            workspace=tmp_path,
        )

        # Should contain error message
        assert "No such file" in result or "Exit code:" in result

    @pytest.mark.asyncio
    async def test_error_handling_command_not_found(self, tmp_path, metrics):
        """Non-existent commands should return error."""
        result = await bash_command(
            "nonexistentcommand123456",
            workspace=tmp_path,
        )

        # Should indicate command not found
        assert "not found" in result.lower() or "Exit code:" in result

    @pytest.mark.asyncio
    async def test_error_handling_permission_denied(self, tmp_path, metrics):
        """Permission errors should be captured."""
        # Create a file and remove read permission
        test_file = tmp_path / "noread.txt"
        test_file.write_text("secret")
        test_file.chmod(0o000)

        try:
            result = await bash_command(
                f"cat {test_file}",
                workspace=tmp_path,
            )

            # Should contain permission error
            assert "Permission denied" in result or "Exit code:" in result
        finally:
            # Restore permissions for cleanup
            test_file.chmod(0o644)

    def test_error_handling_metrics(self, metrics):
        """Error handling metric should correctly identify errors."""
        # Test when error is expected and occurs
        result = metrics.evaluate_error_handling(
            exit_code=1,
            stderr="Error: file not found",
            expected_error_handled=True,
        )
        assert result.value == 1.0

        # Test when no error is expected but occurs
        result = metrics.evaluate_error_handling(
            exit_code=1,
            stderr="Error occurred",
            expected_error_handled=False,
        )
        assert result.value == 0.0

        # Test when no error expected and none occurs
        result = metrics.evaluate_error_handling(
            exit_code=0,
            stderr="",
            expected_error_handled=False,
        )
        assert result.value == 1.0


@pytest.mark.capability("bash_execution")
class TestTimeoutHandling:
    """Tests for timeout handling in bash execution."""

    @pytest.fixture
    def metrics(self):
        return BashExecutionMetrics()

    @pytest.mark.asyncio
    async def test_timeout_handling_quick_command(self, tmp_path, metrics):
        """Quick commands should complete within timeout."""
        result = await bash_command(
            "echo 'fast'",
            timeout=5000,  # 5 second timeout
            workspace=tmp_path,
        )

        assert "fast" in result

    @pytest.mark.asyncio
    async def test_timeout_handling_slow_command(self, tmp_path, metrics):
        """Slow commands should be killed on timeout."""
        with pytest.raises(asyncio.TimeoutError) as exc_info:
            await bash_command(
                "sleep 10",
                timeout=100,  # 100ms timeout
                workspace=tmp_path,
            )

        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_timeout_capped_at_max(self, tmp_path):
        """Timeout should be capped at maximum value."""
        # This should not raise - timeout gets capped
        result = await bash_command(
            "echo 'test'",
            timeout=999999999,  # Exceeds max, should be capped
            workspace=tmp_path,
        )

        assert "test" in result

    def test_timeout_metrics(self, metrics):
        """Timeout metrics should correctly evaluate execution time."""
        expectation = BashExpectation(
            max_execution_time=5.0,
        )

        # Within time limit
        results = metrics.evaluate(
            exit_code=0,
            stdout="output",
            stderr="",
            execution_time=2.0,
            expectation=expectation,
        )
        summary = metrics.summary(results)
        assert summary["within_time_limit"] == 1.0

        # Exceeds time limit
        results = metrics.evaluate(
            exit_code=0,
            stdout="output",
            stderr="",
            execution_time=10.0,
            expectation=expectation,
        )
        summary = metrics.summary(results)
        assert summary["within_time_limit"] == 0.0


@pytest.mark.capability("bash_execution")
class TestOutputParsing:
    """Tests for output parsing and formatting."""

    @pytest.fixture
    def metrics(self):
        return BashExecutionMetrics()

    @pytest.mark.asyncio
    async def test_output_parsing_stdout(self, tmp_path, metrics):
        """Standard output should be captured correctly."""
        result = await bash_command(
            "echo 'line1'; echo 'line2'; echo 'line3'",
            workspace=tmp_path,
        )

        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    @pytest.mark.asyncio
    async def test_output_parsing_stderr(self, tmp_path, metrics):
        """Standard error should be captured correctly."""
        result = await bash_command(
            "echo 'error message' >&2",
            workspace=tmp_path,
        )

        assert "error message" in result

    @pytest.mark.asyncio
    async def test_output_parsing_mixed(self, tmp_path):
        """Mixed stdout and stderr should both be captured."""
        result = await bash_command(
            "echo 'stdout'; echo 'stderr' >&2",
            workspace=tmp_path,
        )

        assert "stdout" in result
        assert "stderr" in result

    @pytest.mark.asyncio
    async def test_output_truncation(self, tmp_path):
        """Very long output should be truncated."""
        # Generate long output
        result = await bash_command(
            "for i in $(seq 1 10000); do echo 'line $i with some padding text to make it longer'; done",
            workspace=tmp_path,
        )

        # Output should be truncated if too long
        # The exact truncation behavior depends on MAX_OUTPUT_LENGTH
        assert len(result) <= 35000  # MAX_OUTPUT_LENGTH + some margin

    def test_output_contains_metrics(self, metrics):
        """Output contains metrics should check for expected strings."""
        expectation = BashExpectation(
            expected_output_contains=["success", "completed"],
        )

        # Both strings present
        results = metrics.evaluate(
            exit_code=0,
            stdout="Operation success, task completed",
            stderr="",
            execution_time=1.0,
            expectation=expectation,
        )
        summary = metrics.summary(results)
        assert summary["output_contains"] == 1.0

        # Only one string present
        results = metrics.evaluate(
            exit_code=0,
            stdout="Operation success",
            stderr="",
            execution_time=1.0,
            expectation=expectation,
        )
        summary = metrics.summary(results)
        assert summary["output_contains"] == 0.5


@pytest.mark.capability("bash_execution")
class TestSafetyChecks:
    """Tests for safety checks on dangerous commands."""

    @pytest.fixture
    def metrics(self):
        return BashExecutionMetrics()

    @pytest.mark.asyncio
    async def test_empty_command_rejected(self, tmp_path):
        """Empty commands should be rejected."""
        with pytest.raises(ValueError) as exc_info:
            await bash_command("", workspace=tmp_path)

        assert "empty" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_whitespace_only_command_rejected(self, tmp_path):
        """Whitespace-only commands should be rejected."""
        with pytest.raises(ValueError) as exc_info:
            await bash_command("   ", workspace=tmp_path)

        assert "empty" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_invalid_timeout_rejected(self, tmp_path):
        """Invalid timeout values should be rejected."""
        with pytest.raises(ValueError) as exc_info:
            await bash_command("echo 'test'", timeout=0, workspace=tmp_path)

        assert "positive" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_negative_timeout_rejected(self, tmp_path):
        """Negative timeout should be rejected."""
        with pytest.raises(ValueError) as exc_info:
            await bash_command("echo 'test'", timeout=-1000, workspace=tmp_path)

        assert "positive" in str(exc_info.value).lower()

    def test_output_not_contains_metrics(self, metrics):
        """Forbidden output patterns should be detected."""
        expectation = BashExpectation(
            expected_output_not_contains=["ERROR", "FATAL", "password"],
        )

        # No forbidden patterns
        results = metrics.evaluate(
            exit_code=0,
            stdout="All good, operation successful",
            stderr="",
            execution_time=1.0,
            expectation=expectation,
        )
        summary = metrics.summary(results)
        assert summary["output_not_contains"] == 1.0

        # Contains forbidden pattern
        results = metrics.evaluate(
            exit_code=0,
            stdout="ERROR: something went wrong",
            stderr="",
            execution_time=1.0,
            expectation=expectation,
        )
        summary = metrics.summary(results)
        assert summary["output_not_contains"] < 1.0


@pytest.mark.capability("bash_execution")
class TestWorkingDirectory:
    """Tests for working directory handling."""

    @pytest.mark.asyncio
    async def test_cwd_parameter(self, tmp_path):
        """cwd parameter should change working directory."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "file.txt").write_text("in subdir")

        result = await bash_command(
            "ls",
            cwd=str(subdir),
            workspace=tmp_path,
        )

        assert "file.txt" in result

    @pytest.mark.asyncio
    async def test_cwd_not_found(self, tmp_path):
        """Non-existent cwd should raise error."""
        with pytest.raises(FileNotFoundError):
            await bash_command(
                "ls",
                cwd=str(tmp_path / "nonexistent"),
                workspace=tmp_path,
            )

    @pytest.mark.asyncio
    async def test_cwd_is_file(self, tmp_path):
        """cwd pointing to a file should raise error."""
        file_path = tmp_path / "file.txt"
        file_path.write_text("content")

        with pytest.raises(NotADirectoryError):
            await bash_command(
                "ls",
                cwd=str(file_path),
                workspace=tmp_path,
            )


@pytest.mark.capability("bash_execution")
class TestBashMetricsIntegration:
    """Integration tests for bash execution metrics."""

    @pytest.fixture
    def metrics(self):
        return BashExecutionMetrics()

    def test_full_metrics_evaluation(self, metrics):
        """Full metrics evaluation with all expectations."""
        expectation = BashExpectation(
            expected_exit_code=0,
            expected_output_contains=["success", "done"],
            expected_output_not_contains=["error", "failed"],
            max_execution_time=5.0,
        )

        # Perfect execution
        results = metrics.evaluate(
            exit_code=0,
            stdout="Operation success, task done!",
            stderr="",
            execution_time=1.0,
            expectation=expectation,
        )
        summary = metrics.summary(results)

        assert summary["exit_code_correct"] == 1.0
        assert summary["output_contains"] == 1.0
        assert summary["output_not_contains"] == 1.0
        assert summary["within_time_limit"] == 1.0

    def test_failing_execution_metrics(self, metrics):
        """Failing execution should score poorly on relevant metrics."""
        expectation = BashExpectation(
            expected_exit_code=0,
            expected_output_contains=["success"],
            expected_output_not_contains=["error"],
        )

        # Failed execution
        results = metrics.evaluate(
            exit_code=1,
            stdout="",
            stderr="error: command failed",
            execution_time=1.0,
            expectation=expectation,
        )
        summary = metrics.summary(results)

        assert summary["exit_code_correct"] == 0.0
        assert summary["output_contains"] == 0.0
        assert summary["output_not_contains"] == 0.0  # "error" is in output
