"""Context analysis stage for planning pipeline.

This stage analyzes whether a user's question can be answered
directly from conversation context without tool calls.
"""

import re
from typing import List, Set, Optional

from ..types import TaskDAG, TaskSource
from ..logging import get_logger
from .protocol import PlannerStage, PlanningContext, PlanningMode

logger = get_logger("planner.context_analyzer")


# Patterns indicating context-dependent questions
CONTEXT_REFERENCE_PATTERNS = [
    # Pronouns referring to previous content
    (r"^(?:这|那|它|他们|她们|它们|这个|那个|这些|那些)", "pronoun_reference"),
    (r"(?:this|that|it|they|these|those)\s+", "pronoun_reference"),
    # Explicit references to previous content
    (r"(?:刚才|刚刚|上面|之前|前面|上一个)", "temporal_reference"),
    (r"(?:just now|above|before|previous|earlier|last)", "temporal_reference"),
    # Questions about previously mentioned things
    (r"(?:是什么|叫什么|在哪|哪个|哪里)", "question_about_context"),
    (r"(?:what is|what's|where is|where's|which one|which file)", "question_about_context"),
    # Summary/analysis of previous content
    (r"(?:总结|概括|分析|解释)(?:一下)?(?:这|那|它|上面|刚才)?", "summarize_context"),
    (r"(?:summarize|explain|analyze)\s+(?:this|that|it|above|the)", "summarize_context"),
    # Among/within previous results
    (r"(?:其中|里面|当中|在.+中)", "among_context"),
    (r"(?:among|within|in\s+them|of\s+them|from\s+(?:these|those))", "among_context"),
]


class ContextAnalyzer:
    """Analyzes if goal can be answered from context.

    This stage checks whether the user's question refers to content
    from previous conversation turns. If so, it can potentially be
    answered directly without needing to invoke tools.

    Scenarios handled:
    1. User asks about previous results - "What is this project's name?"
    2. User uses pronouns referring to earlier content - "Which file is it in?"
    3. Summarize/analyze previous info - "Summarize the directories we've seen"
    4. Select from previous results - "Among them, which handles LLM?"

    Example:
        ```python
        analyzer = ContextAnalyzer()
        ctx = PlanningContext(
            goal="What is this project's name?",
            conversation_context="Previous: Read pyproject.toml: [project] name='nimbus'...",
            ...
        )

        ctx = await analyzer.process(ctx)
        if ctx.metadata.get("context_dependent"):
            # Question refers to previous context
            pass
        ```
    """

    def __init__(
        self,
        patterns: Optional[List[tuple]] = None,
        min_context_length: int = 50,
    ):
        """Initialize the context analyzer.

        Args:
            patterns: List of (regex_pattern, category) tuples.
                     Uses default patterns if None.
            min_context_length: Minimum context length to consider.
                               If context is too short, skip analysis.
        """
        self.patterns = patterns if patterns is not None else CONTEXT_REFERENCE_PATTERNS
        self._compiled_patterns: List[tuple] = []
        self._compile_patterns()
        self.min_context_length = min_context_length

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for efficiency."""
        self._compiled_patterns = []
        for pattern, category in self.patterns:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._compiled_patterns.append((compiled, category))
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern}': {e}")

    @property
    def name(self) -> str:
        """Stage name for logging/tracing."""
        return "context_analyzer"

    async def process(self, ctx: PlanningContext) -> PlanningContext:
        """Analyze if the goal can be answered from context.

        Processing logic:
        1. Check if conversation context is non-empty
        2. Detect context-referencing patterns in the goal
        3. If detected, mark context as relevant for LLM enhancement
        4. Optionally create direct response DAG for simple cases

        Args:
            ctx: The planning context.

        Returns:
            Updated planning context with analysis results.
        """
        goal = ctx.goal.strip()
        context = ctx.conversation_context or ""

        # Skip if context is empty or too short
        if not context or len(context) < self.min_context_length:
            logger.debug("Skipping context analysis: context too short or empty")
            ctx.metadata["context_dependent"] = False
            return ctx

        # Check for context-referencing patterns
        matched_categories = self._detect_context_references(goal)

        if matched_categories:
            logger.info(f"Detected context references: {matched_categories}")
            ctx.metadata["context_dependent"] = True
            ctx.metadata["context_reference_types"] = list(matched_categories)

            # Mark that LLM should pay special attention to context
            ctx.metadata["context_aware_planning"] = True

            # If this is a simple pronoun resolution with rich context,
            # we could potentially answer directly. But for now, let the
            # LLM enhancer handle it with the enhanced prompt.
            # This is safer and more flexible.

            # Future enhancement: For very simple cases like "what is it called?",
            # we could parse the context and create a direct response.

        else:
            logger.debug("No context references detected")
            ctx.metadata["context_dependent"] = False

        return ctx

    def _detect_context_references(self, goal: str) -> Set[str]:
        """Detect context-referencing patterns in the goal.

        Args:
            goal: User's goal/question.

        Returns:
            Set of detected reference categories.
        """
        categories: Set[str] = set()

        for pattern, category in self._compiled_patterns:
            if pattern.search(goal):
                categories.add(category)

        return categories

    def has_context_reference(self, goal: str) -> bool:
        """Quick check if goal contains context references.

        Args:
            goal: User's goal/question.

        Returns:
            True if context references detected.
        """
        return len(self._detect_context_references(goal)) > 0

    def extract_context_type(self, goal: str) -> Optional[str]:
        """Extract the primary context reference type.

        Args:
            goal: User's goal/question.

        Returns:
            Primary reference type or None.
        """
        categories = self._detect_context_references(goal)
        if categories:
            # Return first match (order matters in patterns list)
            for pattern, category in self._compiled_patterns:
                if pattern.search(goal):
                    return category
        return None
