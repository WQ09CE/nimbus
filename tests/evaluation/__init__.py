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
    MetricResult,
    CapabilityMetrics,
    AggregateMetrics,
    # Task Decomposition
    DecompositionExpectation,
    TaskDecompositionMetrics,
    # Code Search
    SearchResult,
    SearchExpectation,
    CodeSearchMetrics,
    # Context Compression
    CompressionExpectation,
    ContextCompressionMetrics,
    # Context Understanding
    ContextTestCase,
    ContextUnderstandingMetrics,
    # Code Modification
    ModificationExpectation,
    CodeModificationMetrics,
    # Bash Execution
    BashExpectation,
    BashExecutionMetrics,
    # Code Summarization
    SummarizationExpectation,
    CodeSummarizationMetrics,
    # Repo Understanding
    RepoExpectation,
    RepoUnderstandingMetrics,
)

from .refactoring_metrics import (
    RefactoringScore,
    RefactoringExpectation,
    RefactoringEvaluator,
    create_api_migration_expectation,
    analyze_refactoring_diff,
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
