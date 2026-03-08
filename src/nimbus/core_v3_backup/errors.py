"""
Nimbus Core Errors

Custom exceptions for the Nimbus framework.
"""


class NimbusError(Exception):
    """Base exception for all Nimbus errors."""

    pass


class ContextOverflowError(NimbusError):
    """
    Raised when context exceeds the token budget threshold.

    This exception signals that the MMU's context has grown beyond the
    configured threshold and needs compaction. AgentOS should catch this
    and trigger intelligent context compression.

    Attributes:
        current_tokens: Current number of tokens in context
        max_tokens: Maximum allowed tokens
        threshold: The threshold that was exceeded
    """

    def __init__(
        self,
        current_tokens: int,
        max_tokens: int,
        threshold: int,
        message: str | None = None,
    ):
        self.current_tokens = current_tokens
        self.max_tokens = max_tokens
        self.threshold = threshold

        if message is None:
            message = (
                f"Context overflow: {current_tokens} tokens exceeds threshold "
                f"({threshold}/{max_tokens}). Compaction required."
            )
        super().__init__(message)


class CompactionError(NimbusError):
    """Raised when context compaction fails."""

    pass


class InterruptedError(NimbusError):
    """Raised when execution is interrupted by user."""

    pass
