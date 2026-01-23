"""Execution tracing for OpenNotebook.

This module provides:
- Span: Individual trace span for timing and tracking
- Tracer: Context manager for hierarchical tracing
"""

import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional


@dataclass
class Span:
    """Trace span representing a single operation."""
    id: str
    name: str
    parent_id: Optional[str] = None
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "ok"  # "ok" | "error"
    error_message: Optional[str] = None

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute.

        Args:
            key: Attribute name.
            value: Attribute value.
        """
        self.attributes[key] = value

    def set_attributes(self, attributes: Dict[str, Any]) -> None:
        """Set multiple span attributes.

        Args:
            attributes: Dictionary of attributes.
        """
        self.attributes.update(attributes)

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Add an event to the span.

        Args:
            name: Event name.
            attributes: Event attributes.
        """
        self.events.append({
            "name": name,
            "timestamp": datetime.now().isoformat(),
            "attributes": attributes or {}
        })

    def end(self, status: str = "ok", error_message: Optional[str] = None) -> None:
        """End the span.

        Args:
            status: Final status ("ok" or "error").
            error_message: Error message if status is "error".
        """
        self.end_time = datetime.now()
        self.status = status
        if error_message:
            self.error_message = error_message

    @property
    def duration_ms(self) -> int:
        """Get span duration in milliseconds.

        Returns:
            Duration in milliseconds, 0 if not ended.
        """
        if self.end_time:
            delta = self.end_time - self.start_time
            return int(delta.total_seconds() * 1000)
        return 0

    @property
    def is_error(self) -> bool:
        """Check if span ended with error.

        Returns:
            True if status is "error".
        """
        return self.status == "error"

    def to_dict(self) -> Dict[str, Any]:
        """Convert span to dictionary.

        Returns:
            Dictionary representation.
        """
        return {
            "id": self.id,
            "name": self.name,
            "parent_id": self.parent_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_message": self.error_message,
            "attributes": self.attributes,
            "events": self.events,
        }


class Tracer:
    """Execution tracer for hierarchical span tracking."""

    def __init__(self, service_name: str = "nimbus"):
        """Initialize tracer.

        Args:
            service_name: Name of the service being traced.
        """
        self.service_name = service_name
        self._spans: List[Span] = []
        self._current_span: Optional[Span] = None
        self._span_stack: List[Span] = []

    @contextmanager
    def start_span(
        self,
        name: str,
        attributes: Optional[Dict[str, Any]] = None
    ) -> Generator[Span, None, None]:
        """Start a new trace span.

        Args:
            name: Span name.
            attributes: Initial attributes.

        Yields:
            The created span.
        """
        span = Span(
            id=str(uuid.uuid4())[:8],
            name=name,
            parent_id=self._current_span.id if self._current_span else None,
            attributes=attributes or {}
        )

        # Push to stack
        if self._current_span:
            self._span_stack.append(self._current_span)
        self._current_span = span
        self._spans.append(span)

        try:
            yield span
        except Exception as e:
            span.set_attribute("error", str(e))
            span.set_attribute("error_type", type(e).__name__)
            span.end("error", str(e))
            raise
        else:
            span.end("ok")
        finally:
            # Pop from stack
            if self._span_stack:
                self._current_span = self._span_stack.pop()
            else:
                self._current_span = None

    @property
    def current_span(self) -> Optional[Span]:
        """Get the current active span.

        Returns:
            Current span or None.
        """
        return self._current_span

    def get_spans(self) -> List[Span]:
        """Get all recorded spans.

        Returns:
            List of spans.
        """
        return self._spans.copy()

    def get_trace_summary(self) -> Dict[str, Any]:
        """Get summary of all traces.

        Returns:
            Summary dictionary.
        """
        total_duration = sum(s.duration_ms for s in self._spans)
        error_count = sum(1 for s in self._spans if s.is_error)

        return {
            "service": self.service_name,
            "span_count": len(self._spans),
            "total_duration_ms": total_duration,
            "error_count": error_count,
            "spans": [
                {
                    "id": s.id,
                    "name": s.name,
                    "parent_id": s.parent_id,
                    "duration_ms": s.duration_ms,
                    "status": s.status,
                    "attributes": s.attributes,
                }
                for s in self._spans
            ]
        }

    def get_span_tree(self) -> Dict[str, Any]:
        """Get spans organized as a tree.

        Returns:
            Tree structure with nested children.
        """
        # Build lookup
        span_dict = {s.id: s.to_dict() for s in self._spans}
        for s in span_dict.values():
            s["children"] = []

        # Build tree
        roots = []
        for s in span_dict.values():
            if s["parent_id"] is None:
                roots.append(s)
            elif s["parent_id"] in span_dict:
                span_dict[s["parent_id"]]["children"].append(s)

        return {
            "service": self.service_name,
            "roots": roots
        }

    def clear(self) -> None:
        """Clear all recorded spans."""
        self._spans.clear()
        self._current_span = None
        self._span_stack.clear()

    def export_json(self) -> str:
        """Export traces as JSON string.

        Returns:
            JSON string.
        """
        import json
        return json.dumps(self.get_trace_summary(), indent=2, default=str)


# Global tracer instance
_tracer: Optional[Tracer] = None


def get_tracer(service_name: str = "nimbus") -> Tracer:
    """Get or create the global tracer instance.

    Args:
        service_name: Service name for new tracer.

    Returns:
        Tracer instance.
    """
    global _tracer
    if _tracer is None:
        _tracer = Tracer(service_name)
    return _tracer


def reset_tracer() -> None:
    """Reset the global tracer instance."""
    global _tracer
    _tracer = None


# Convenience decorator for tracing functions
def trace(name: Optional[str] = None, attributes: Optional[Dict[str, Any]] = None):
    """Decorator to trace a function.

    Args:
        name: Span name (defaults to function name).
        attributes: Initial attributes.

    Returns:
        Decorator function.
    """
    def decorator(func):
        import asyncio
        import functools

        span_name = name or func.__name__

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                tracer = get_tracer()
                with tracer.start_span(span_name, attributes) as span:
                    span.set_attribute("args_count", len(args))
                    span.set_attribute("kwargs_keys", list(kwargs.keys()))
                    result = await func(*args, **kwargs)
                    return result
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                tracer = get_tracer()
                with tracer.start_span(span_name, attributes) as span:
                    span.set_attribute("args_count", len(args))
                    span.set_attribute("kwargs_keys", list(kwargs.keys()))
                    result = func(*args, **kwargs)
                    return result
            return sync_wrapper

    return decorator
