"""Cross-file refactoring evaluation metrics.

This module provides metrics for evaluating the quality of cross-file
refactoring operations, specifically for API migration tasks.

Metrics:
- location_accuracy: Whether all correct call sites were identified
- modification_accuracy: Whether modifications were applied correctly
- no_false_positive: Whether unrelated code was left untouched
- tests_pass: Whether tests pass after refactoring
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Any, Set, Optional, Tuple
import difflib
import re


@dataclass
class RefactoringScore:
    """Comprehensive score for a refactoring operation.

    Attributes:
        location_accuracy: Score for correctly identifying all call sites (0-1).
        modification_accuracy: Score for correctly applying modifications (0-1).
        no_false_positive: Score for not modifying unrelated code (0-1).
        tests_pass: Score for tests passing after refactoring (0-1).
        details: Additional details about the scoring.
    """

    location_accuracy: float
    modification_accuracy: float
    no_false_positive: float
    tests_pass: float
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> float:
        """Calculate weighted total score.

        Weights:
        - location_accuracy: 30%
        - modification_accuracy: 30%
        - no_false_positive: 20%
        - tests_pass: 20%
        """
        return (
            self.location_accuracy * 0.3
            + self.modification_accuracy * 0.3
            + self.no_false_positive * 0.2
            + self.tests_pass * 0.2
        )

    def __str__(self) -> str:
        return (
            f"RefactoringScore(\n"
            f"  location_accuracy={self.location_accuracy:.2f},\n"
            f"  modification_accuracy={self.modification_accuracy:.2f},\n"
            f"  no_false_positive={self.no_false_positive:.2f},\n"
            f"  tests_pass={self.tests_pass:.2f},\n"
            f"  total={self.total:.2f}\n"
            f")"
        )


@dataclass
class RefactoringExpectation:
    """Expected outcomes for a refactoring task.

    Attributes:
        old_name: The name being refactored from (e.g., "old_api").
        new_name: The name being refactored to (e.g., "new_api").
        files_to_modify: Set of file paths that should be modified.
        files_to_preserve: Set of file paths that should NOT be modified.
        expected_changes_per_file: Dict mapping file path to expected change count.
        class_context: Optional class name to disambiguate methods (e.g., "APIClient").
    """

    old_name: str
    new_name: str
    files_to_modify: Set[str] = field(default_factory=set)
    files_to_preserve: Set[str] = field(default_factory=set)
    expected_changes_per_file: Dict[str, int] = field(default_factory=dict)
    class_context: Optional[str] = None


class RefactoringEvaluator:
    """Evaluator for cross-file refactoring quality.

    This evaluator compares a workspace (after refactoring) against a golden
    answer to measure the quality of the refactoring operation.
    """

    def __init__(self, golden_path: Path, expectation: RefactoringExpectation):
        """Initialize the evaluator.

        Args:
            golden_path: Path to the golden (correctly refactored) project.
            expectation: Expected refactoring outcomes.
        """
        self.golden_path = golden_path
        self.expectation = expectation
        self._golden_contents: Dict[str, str] = {}
        self._load_golden()

    def _load_golden(self) -> None:
        """Load all golden file contents."""
        for file_path in self.golden_path.rglob("*.py"):
            rel_path = file_path.relative_to(self.golden_path)
            self._golden_contents[str(rel_path)] = file_path.read_text()

        # Also load README.md if present
        readme = self.golden_path / "README.md"
        if readme.exists():
            self._golden_contents["README.md"] = readme.read_text()

    def evaluate(self, workspace_path: Path, tests_passed: bool = True) -> RefactoringScore:
        """Evaluate the refactoring quality of a workspace.

        Args:
            workspace_path: Path to the workspace (after refactoring attempt).
            tests_passed: Whether tests pass after refactoring.

        Returns:
            RefactoringScore with detailed metrics.
        """
        details: Dict[str, Any] = {
            "modified_files": [],
            "preserved_files": [],
            "false_positives": [],
            "missing_changes": [],
            "extra_changes": [],
        }

        # Load workspace contents
        workspace_contents: Dict[str, str] = {}
        for file_path in workspace_path.rglob("*.py"):
            rel_path = file_path.relative_to(workspace_path)
            workspace_contents[str(rel_path)] = file_path.read_text()

        readme = workspace_path / "README.md"
        if readme.exists():
            workspace_contents["README.md"] = readme.read_text()

        # Calculate each metric
        location_accuracy = self._evaluate_location_accuracy(
            workspace_contents, details
        )
        modification_accuracy = self._evaluate_modification_accuracy(
            workspace_contents, details
        )
        no_false_positive = self._evaluate_no_false_positive(
            workspace_contents, details
        )
        tests_score = 1.0 if tests_passed else 0.0

        return RefactoringScore(
            location_accuracy=location_accuracy,
            modification_accuracy=modification_accuracy,
            no_false_positive=no_false_positive,
            tests_pass=tests_score,
            details=details,
        )

    def _evaluate_location_accuracy(
        self, workspace: Dict[str, str], details: Dict[str, Any]
    ) -> float:
        """Evaluate whether all correct call sites were identified.

        A call site is considered "identified" if the old_name is no longer
        present where it should have been changed.
        """
        if not self.expectation.files_to_modify:
            return 1.0

        identified = 0
        total = len(self.expectation.files_to_modify)

        for file_path in self.expectation.files_to_modify:
            if file_path not in workspace:
                details["missing_changes"].append(f"{file_path} (file not found)")
                continue

            content = workspace[file_path]
            golden_content = self._golden_contents.get(file_path, "")

            # Check if the file matches golden (meaning refactoring was done)
            # Use method call pattern to be more precise
            old_pattern = self._build_call_pattern(self.expectation.old_name)
            new_pattern = self._build_call_pattern(self.expectation.new_name)

            # Check if old pattern is gone (for class method context)
            has_old = bool(re.search(old_pattern, content))

            # For files that should be modified, old_name should be gone
            # (in the context of the class we're refactoring)
            if not has_old or self._content_matches_golden(content, golden_content):
                identified += 1
                details["modified_files"].append(file_path)
            else:
                details["missing_changes"].append(file_path)

        return identified / total if total > 0 else 1.0

    def _evaluate_modification_accuracy(
        self, workspace: Dict[str, str], details: Dict[str, Any]
    ) -> float:
        """Evaluate whether modifications were applied correctly.

        Compares workspace content against golden content for files
        that should have been modified.
        """
        if not self.expectation.files_to_modify:
            return 1.0

        correct = 0
        total = len(self.expectation.files_to_modify)

        for file_path in self.expectation.files_to_modify:
            if file_path not in workspace:
                continue

            content = workspace[file_path]
            golden_content = self._golden_contents.get(file_path, "")

            if self._content_matches_golden(content, golden_content):
                correct += 1
            else:
                # Calculate similarity for partial credit
                similarity = self._calculate_similarity(content, golden_content)
                correct += similarity
                if similarity < 1.0:
                    details["extra_changes"].append(
                        f"{file_path} (similarity: {similarity:.2f})"
                    )

        return correct / total if total > 0 else 1.0

    def _evaluate_no_false_positive(
        self, workspace: Dict[str, str], details: Dict[str, Any]
    ) -> float:
        """Evaluate whether unrelated code was left untouched.

        Specifically checks files that should NOT be modified.
        """
        if not self.expectation.files_to_preserve:
            return 1.0

        preserved = 0
        total = len(self.expectation.files_to_preserve)

        for file_path in self.expectation.files_to_preserve:
            if file_path not in workspace:
                # File was deleted - this is a false positive
                details["false_positives"].append(f"{file_path} (deleted)")
                continue

            content = workspace[file_path]
            golden_content = self._golden_contents.get(file_path, "")

            # For files to preserve, content should match golden exactly
            # (which means old_api should still be present in data.py)
            if self._content_matches_golden(content, golden_content, strict=True):
                preserved += 1
                details["preserved_files"].append(file_path)
            else:
                details["false_positives"].append(file_path)

        return preserved / total if total > 0 else 1.0

    def _build_call_pattern(self, method_name: str) -> str:
        """Build a regex pattern for method calls.

        This pattern matches:
        - object.method_name(
        - self.method_name(
        """
        # Match method calls like: client.old_api(, self.old_api(
        return rf"\.\s*{re.escape(method_name)}\s*\("

    def _content_matches_golden(
        self, content: str, golden: str, strict: bool = False
    ) -> bool:
        """Check if content matches golden, with optional strictness.

        Args:
            content: The workspace file content.
            golden: The golden file content.
            strict: If True, require exact match. If False, allow minor differences.
        """
        if strict:
            return content.strip() == golden.strip()

        # Normalize whitespace for comparison
        content_normalized = self._normalize_whitespace(content)
        golden_normalized = self._normalize_whitespace(golden)

        return content_normalized == golden_normalized

    def _normalize_whitespace(self, text: str) -> str:
        """Normalize whitespace in text for comparison."""
        # Replace multiple spaces with single space
        text = re.sub(r" +", " ", text)
        # Normalize line endings
        text = text.replace("\r\n", "\n")
        # Strip trailing whitespace from lines
        lines = [line.rstrip() for line in text.split("\n")]
        return "\n".join(lines).strip()

    def _calculate_similarity(self, content: str, golden: str) -> float:
        """Calculate similarity ratio between two texts."""
        matcher = difflib.SequenceMatcher(None, content, golden)
        return matcher.ratio()


def create_api_migration_expectation() -> RefactoringExpectation:
    """Create expectation for the standard API migration test case.

    This is the expectation for renaming APIClient.old_api() to new_api().
    """
    return RefactoringExpectation(
        old_name="old_api",
        new_name="new_api",
        class_context="APIClient",
        files_to_modify={
            "core/client.py",      # Method definition
            "core/utils.py",       # Calls to client.old_api()
            "services/auth.py",    # Calls to self.client.old_api()
            "tests/test_client.py",  # Test calls
            "README.md",           # Documentation
        },
        files_to_preserve={
            "services/data.py",    # Has independent old_api() function
            "services/__init__.py",  # Exports old_api from data.py
        },
        expected_changes_per_file={
            "core/client.py": 3,      # def old_api, docstring, self.old_api
            "core/utils.py": 4,       # 4 calls to client.old_api
            "services/auth.py": 4,    # 4 calls to self.client.old_api
            "tests/test_client.py": 13,  # Multiple test method calls
            "README.md": 5,           # Documentation references
        },
    )


def count_pattern_occurrences(content: str, pattern: str) -> int:
    """Count occurrences of a pattern in content.

    Args:
        content: Text to search in.
        pattern: Regex pattern to search for.

    Returns:
        Number of matches found.
    """
    return len(re.findall(pattern, content))


def analyze_refactoring_diff(
    original_path: Path,
    refactored_path: Path,
) -> Dict[str, Any]:
    """Analyze the differences between original and refactored projects.

    Args:
        original_path: Path to the original project.
        refactored_path: Path to the refactored project.

    Returns:
        Dictionary containing diff analysis.
    """
    analysis = {
        "files_changed": [],
        "files_unchanged": [],
        "total_additions": 0,
        "total_deletions": 0,
        "per_file_changes": {},
    }

    for orig_file in original_path.rglob("*"):
        if orig_file.is_dir():
            continue

        rel_path = orig_file.relative_to(original_path)
        refactored_file = refactored_path / rel_path

        if not refactored_file.exists():
            analysis["files_changed"].append(str(rel_path))
            continue

        orig_content = orig_file.read_text()
        refactored_content = refactored_file.read_text()

        if orig_content != refactored_content:
            analysis["files_changed"].append(str(rel_path))

            # Count line changes
            orig_lines = orig_content.splitlines()
            refactored_lines = refactored_content.splitlines()

            diff = list(difflib.unified_diff(orig_lines, refactored_lines))
            additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
            deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

            analysis["total_additions"] += additions
            analysis["total_deletions"] += deletions
            analysis["per_file_changes"][str(rel_path)] = {
                "additions": additions,
                "deletions": deletions,
            }
        else:
            analysis["files_unchanged"].append(str(rel_path))

    return analysis
