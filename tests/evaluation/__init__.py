"""Evaluation metrics for capability testing.

This package provides metrics classes for measuring agent capabilities:
- TaskDecompositionMetrics: Measure task decomposition accuracy
- CodeSearchMetrics: Measure code search precision/recall/F1
- ContextCompressionMetrics: Measure memory compression quality
- ContextUnderstandingMetrics: Measure context understanding accuracy
- CodeModificationMetrics: Measure code modification correctness
- BashExecutionMetrics: Measure bash command execution accuracy
- CodeSummarizationMetrics: Measure code summarization quality
- RepoUnderstandingMetrics: Measure repository understanding accuracy
- RefactoringEvaluator: Measure cross-file refactoring quality

Usage:
    from tests.evaluation.metrics import (
        TaskDecompositionMetrics,
        CodeSearchMetrics,
        ContextCompressionMetrics,
        ContextUnderstandingMetrics,
        CodeModificationMetrics,
        BashExecutionMetrics,
        CodeSummarizationMetrics,
        RepoUnderstandingMetrics,
    )

    from tests.evaluation.refactoring_metrics import (
        RefactoringScore,
        RefactoringExpectation,
        RefactoringEvaluator,
    )

    metrics = CodeSearchMetrics()
    results = metrics.evaluate(retrieved, expectation)
    summary = metrics.summary(results)
"""

from .metrics import (
    AggregateMetrics,
    BashExecutionMetrics,
    # Bash Execution
    BashExpectation,
    CapabilityMetrics,
    CodeModificationMetrics,
    CodeSearchMetrics,
    CodeSummarizationMetrics,
    # Context Compression
    CompressionExpectation,
    ContextCompressionMetrics,
    # Context Understanding
    ContextTestCase,
    ContextUnderstandingMetrics,
    # Task Decomposition
    DecompositionExpectation,
    MetricResult,
    # Code Modification
    ModificationExpectation,
    # Repo Understanding
    RepoExpectation,
    RepoUnderstandingMetrics,
    SearchExpectation,
    # Code Search
    SearchResult,
    # Code Summarization
    SummarizationExpectation,
    TaskDecompositionMetrics,
)
from .refactoring_metrics import (
    RefactoringEvaluator,
    RefactoringExpectation,
    RefactoringScore,
    analyze_refactoring_diff,
    create_api_migration_expectation,
)

__all__ = [
    "MetricResult",
    "CapabilityMetrics",
    "AggregateMetrics",
    "DecompositionExpectation",
    "TaskDecompositionMetrics",
    "SearchResult",
    "SearchExpectation",
    "CodeSearchMetrics",
    "CompressionExpectation",
    "ContextCompressionMetrics",
    "ContextTestCase",
    "ContextUnderstandingMetrics",
    "ModificationExpectation",
    "CodeModificationMetrics",
    "BashExpectation",
    "BashExecutionMetrics",
    "SummarizationExpectation",
    "CodeSummarizationMetrics",
    "RepoExpectation",
    "RepoUnderstandingMetrics",
    # Refactoring metrics
    "RefactoringScore",
    "RefactoringExpectation",
    "RefactoringEvaluator",
    "create_api_migration_expectation",
    "analyze_refactoring_diff",
]
