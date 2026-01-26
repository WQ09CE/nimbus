"""Tool middleware for retry and error enhancement.

This module provides middleware components for wrapping tool execution
with intelligent retry logic and error enhancement.

Example:
    >>> middleware = ToolRetryMiddleware(resolver, config)
    >>> result = await middleware.wrap_execute(registry, "Read", {"file_path": "utils"})
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Protocol

from .base import ToolExecutionError

if TYPE_CHECKING:
    from .base import ToolRegistry
    from .resolver import PathCandidate, SmartPathResolver


class ToolMiddleware(Protocol):
    """Protocol for tool middleware.

    Middleware can intercept tool execution to add cross-cutting concerns
    like logging, caching, retry logic, etc.
    """

    async def wrap(
        self,
        next_fn: Callable[..., Awaitable[Any]],
        name: str,
        params: Dict[str, Any],
        **context: Any,
    ) -> Any:
        """Wrap the next execution function.

        Args:
            next_fn: The next function in the middleware chain.
            name: Tool name.
            params: Tool parameters.
            **context: Additional context.

        Returns:
            Tool execution result.
        """
        ...


@dataclass
class ToolRetryConfig:
    """Configuration for tool retry middleware.

    Attributes:
        max_retries: Maximum number of retry attempts (default: 2).
        auto_resolve: Whether to auto-resolve paths (default: True).
        auto_resolve_threshold: Score threshold for auto-selection (default: 0.9).
        ask_on_ambiguous: Whether to ask user on ambiguous paths (default: True).
        inject_context_on_fail: Whether to inject context on failure (default: True).
    """

    max_retries: int = 2
    auto_resolve: bool = True
    auto_resolve_threshold: float = 0.9
    ask_on_ambiguous: bool = True
    inject_context_on_fail: bool = True


# Type alias for clarification callback
ClarificationCallback = Callable[
    [str, List[str]],  # message, options
    Awaitable[Optional[str]],  # selected option or None
]


@dataclass
class EnhancedToolError(ToolExecutionError):
    """Enhanced tool error with suggestions.

    Extends ToolExecutionError with path suggestions and additional context.

    Attributes:
        suggestions: List of suggested paths.
        candidates: PathCandidate objects for suggestions.
    """

    suggestions: List[str] = field(default_factory=list)
    candidates: List["PathCandidate"] = field(default_factory=list)

    def __init__(
        self,
        tool_name: str,
        message: str,
        original_error: Optional[Exception] = None,
        suggestions: Optional[List[str]] = None,
        candidates: Optional[List["PathCandidate"]] = None,
    ):
        super().__init__(tool_name, message, original_error)
        self.suggestions = suggestions or []
        self.candidates = candidates or []


# Path parameter names for different tools
PATH_PARAM_MAPPING: Dict[str, str] = {
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "Glob": "path",
    "Grep": "path",
}


class ToolRetryMiddleware:
    """Middleware for intelligent tool retry with path resolution.

    Wraps tool execution to:
    1. Pre-resolve ambiguous paths
    2. Retry on FileNotFoundError with alternative candidates
    3. Enhance error messages with suggestions

    Example:
        >>> resolver = SmartPathResolver(workspace)
        >>> middleware = ToolRetryMiddleware(resolver)
        >>> result = await middleware.wrap_execute(registry, "Read", {"file_path": "utils"})
    """

    def __init__(
        self,
        resolver: "SmartPathResolver",
        config: Optional[ToolRetryConfig] = None,
        clarification_callback: Optional[ClarificationCallback] = None,
    ) -> None:
        """Initialize the middleware.

        Args:
            resolver: SmartPathResolver for path resolution.
            config: Retry configuration (default: ToolRetryConfig()).
            clarification_callback: Callback for user clarification.
        """
        self.resolver = resolver
        self.config = config or ToolRetryConfig()
        self.clarify = clarification_callback

    async def wrap_execute(
        self,
        registry: "ToolRegistry",
        name: str,
        params: Dict[str, Any],
        **context: Any,
    ) -> Any:
        """Wrap tool execution with retry logic.

        Args:
            registry: Tool registry to execute with.
            name: Tool name.
            params: Tool parameters.
            **context: Additional context.

        Returns:
            Tool execution result.

        Raises:
            EnhancedToolError: If all retries fail.
        """
        # Phase 1: Pre-resolve paths if enabled
        if self.config.auto_resolve:
            params = await self._pre_resolve(name, params)

        # Phase 2: Execute with retry
        last_error: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return await registry.execute(name, params, **context)

            except FileNotFoundError as e:
                last_error = e
                # Try to recover from file not found
                recovered_params = await self._on_file_not_found(
                    name, params, e, attempt
                )
                if recovered_params is not None:
                    params = recovered_params
                else:
                    break

            except ToolExecutionError as e:
                last_error = e
                if not self._is_retryable(e):
                    break

        # Phase 3: Enhance error with suggestions
        raise self._enhance_error(last_error or Exception("Unknown error"), name, params)

    async def wrap(
        self,
        next_fn: Callable[..., Awaitable[Any]],
        name: str,
        params: Dict[str, Any],
        **context: Any,
    ) -> Any:
        """Middleware protocol implementation.

        Args:
            next_fn: Next function in chain.
            name: Tool name.
            params: Tool parameters.
            **context: Additional context.

        Returns:
            Tool execution result.
        """
        # Phase 1: Pre-resolve paths if enabled
        if self.config.auto_resolve:
            params = await self._pre_resolve(name, params)

        # Phase 2: Execute with retry
        last_error: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                return await next_fn(name, params, **context)

            except FileNotFoundError as e:
                last_error = e
                recovered_params = await self._on_file_not_found(
                    name, params, e, attempt
                )
                if recovered_params is not None:
                    params = recovered_params
                else:
                    break

            except ToolExecutionError as e:
                last_error = e
                if not self._is_retryable(e):
                    break

        # Phase 3: Enhance error
        raise self._enhance_error(last_error or Exception("Unknown error"), name, params)

    async def _pre_resolve(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Pre-resolve path parameters before execution.

        Args:
            tool_name: Tool name.
            params: Tool parameters.

        Returns:
            Parameters with resolved paths.
        """
        path_param = self._get_path_param(tool_name)
        if path_param is None or path_param not in params:
            return params

        original_path = params[path_param]

        # Skip if already absolute and exists
        path_obj = Path(original_path)
        if path_obj.is_absolute() and path_obj.exists():
            return params

        # Try to resolve
        resolved = self.resolver.resolve_single(
            original_path,
            threshold=self.config.auto_resolve_threshold,
        )

        if resolved:
            params = params.copy()
            params[path_param] = str(resolved)
            params["_original_path"] = original_path
            params["_auto_resolved"] = True

        return params

    async def _on_file_not_found(
        self,
        tool_name: str,
        params: Dict[str, Any],
        error: FileNotFoundError,
        attempt: int,
    ) -> Optional[Dict[str, Any]]:
        """Handle FileNotFoundError with recovery strategies.

        Args:
            tool_name: Tool name.
            params: Current parameters.
            error: The error that occurred.
            attempt: Current attempt number.

        Returns:
            Recovered parameters, or None if recovery not possible.
        """
        path_param = self._get_path_param(tool_name)
        if path_param is None:
            return None

        original_path = params.get("_original_path", params.get(path_param, ""))

        # Get candidates
        candidates = self.resolver.resolve(original_path)

        if not candidates:
            return None

        # Strategy 1: Auto-select if high confidence
        if candidates[0].score >= self.config.auto_resolve_threshold:
            params = params.copy()
            params[path_param] = str(candidates[0].path)
            return params

        # Strategy 2: Ask user for clarification
        if self.config.ask_on_ambiguous and self.clarify:
            message = f"File not found: {original_path}\nDid you mean one of these?"
            options = [str(c.path) for c in candidates]

            for i, c in enumerate(candidates):
                message += f"\n  {i + 1}. {c.path} ({c.reason}, score: {c.score:.2f})"

            selected = await self.clarify(message, options)

            if selected:
                params = params.copy()
                params[path_param] = selected
                return params

        return None

    def _is_retryable(self, error: Exception) -> bool:
        """Check if an error is retryable.

        Args:
            error: The error to check.

        Returns:
            True if the error is retryable.
        """
        # FileNotFoundError is handled separately
        if isinstance(error, FileNotFoundError):
            return True

        # Connection errors are retryable
        if isinstance(error, (ConnectionError, TimeoutError)):
            return True

        # ToolExecutionError with certain original errors
        if isinstance(error, ToolExecutionError):
            if isinstance(error.original_error, (FileNotFoundError, ConnectionError, TimeoutError)):
                return True

        return False

    def _enhance_error(
        self,
        error: Exception,
        tool_name: str,
        params: Dict[str, Any],
    ) -> EnhancedToolError:
        """Enhance an error with suggestions.

        Args:
            error: Original error.
            tool_name: Tool name.
            params: Tool parameters.

        Returns:
            Enhanced error with suggestions.
        """
        path_param = self._get_path_param(tool_name)
        original_path = ""
        if path_param:
            original_path = params.get("_original_path", params.get(path_param, "unknown"))

        # Get suggestions
        candidates = self.resolver.resolve(original_path) if original_path else []
        suggestions = [str(c.path) for c in candidates[:3]]

        # Build enhanced message
        if suggestions:
            suggestion_text = "\n".join(f"  - {s}" for s in suggestions)
            enhanced_msg = (
                f"File not found: {original_path}\n"
                f"Did you mean:\n{suggestion_text}"
            )
        else:
            enhanced_msg = f"File not found: {original_path}"

        return EnhancedToolError(
            tool_name=tool_name,
            message=enhanced_msg,
            original_error=error if isinstance(error, Exception) else None,
            suggestions=suggestions,
            candidates=candidates,
        )

    def _get_path_param(self, tool_name: str) -> Optional[str]:
        """Get the path parameter name for a tool.

        Args:
            tool_name: Tool name.

        Returns:
            Parameter name, or None if tool doesn't have a path parameter.
        """
        return PATH_PARAM_MAPPING.get(tool_name)

    def __repr__(self) -> str:
        """Return string representation."""
        return (
            f"ToolRetryMiddleware(resolver={self.resolver!r}, "
            f"config={self.config!r})"
        )


class MiddlewareChain:
    """Chain of middleware for tool execution.

    Provides a fluent API for building middleware chains.

    Example:
        >>> chain = MiddlewareChain()
        >>> chain.use(retry_middleware)
        >>> chain.use(logging_middleware)
        >>> result = await chain.execute(registry, "Read", params)
    """

    def __init__(self) -> None:
        """Initialize an empty middleware chain."""
        self._middleware: List[ToolMiddleware] = []

    def use(self, middleware: ToolMiddleware) -> "MiddlewareChain":
        """Add middleware to the chain.

        Args:
            middleware: Middleware to add.

        Returns:
            Self for method chaining.
        """
        self._middleware.append(middleware)
        return self

    async def execute(
        self,
        registry: "ToolRegistry",
        name: str,
        params: Dict[str, Any],
        **context: Any,
    ) -> Any:
        """Execute a tool through the middleware chain.

        Args:
            registry: Tool registry.
            name: Tool name.
            params: Tool parameters.
            **context: Additional context.

        Returns:
            Tool execution result.
        """
        # Build the execution chain
        async def core_execute(name: str, params: Dict[str, Any], **ctx: Any) -> Any:
            return await registry.execute(name, params, **ctx)

        # Wrap with middleware (in reverse order)
        execute_fn = core_execute
        for middleware in reversed(self._middleware):
            # Capture middleware in closure
            mw = middleware
            prev_fn = execute_fn

            async def wrapped(
                name: str,
                params: Dict[str, Any],
                _mw: ToolMiddleware = mw,
                _prev: Callable[..., Awaitable[Any]] = prev_fn,
                **ctx: Any,
            ) -> Any:
                return await _mw.wrap(_prev, name, params, **ctx)

            execute_fn = wrapped

        return await execute_fn(name, params, **context)

    def __len__(self) -> int:
        """Return number of middleware in chain."""
        return len(self._middleware)

    def __repr__(self) -> str:
        """Return string representation."""
        return f"MiddlewareChain(middleware={len(self._middleware)})"
