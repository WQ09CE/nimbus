"""
Nimbus Observability Demo
=========================
This module demonstrates the sub-tool call observability feature.
"""

import time
import random
from dataclasses import dataclass, field
from typing import List


@dataclass
class TraceSpan:
    """Represents a single span in a distributed trace."""
    span_id: str
    operation: str
    duration_ms: float
    status: str = "OK"
    tags: dict = field(default_factory=dict)

    def __repr__(self):
        return f"Span({self.operation}, {self.duration_ms}ms, {self.status})"


@dataclass
class Trace:
    """A collection of spans forming a complete trace."""
    trace_id: str
    spans: List[TraceSpan] = field(default_factory=list)

    def add_span(self, operation: str, duration_ms: float, **tags):
        span = TraceSpan(
            span_id=f"span-{len(self.spans)+1:03d}",
            operation=operation,
            duration_ms=duration_ms,
            tags=tags,
        )
        self.spans.append(span)
        return span

    def summary(self) -> str:
        total = sum(s.duration_ms for s in self.spans)
        return f"Trace[{self.trace_id}]: {len(self.spans)} spans, total={total:.1f}ms"


def simulate_agent_workflow():
    """Simulate an agent workflow with observable sub-steps."""
    trace = Trace(trace_id="demo-trace-001")

    # Simulate planning phase
    trace.add_span("plan", random.uniform(10, 50), phase="planning")

    # Simulate tool calls
    for i in range(3):
        trace.add_span(f"tool_call_{i+1}", random.uniform(20, 100), phase="execution")

    # Simulate verification
    trace.add_span("verify", random.uniform(5, 30), phase="verification")

    return trace


if __name__ == "__main__":
    trace = simulate_agent_workflow()
    print(trace.summary())
    for span in trace.spans:
        print(f"  - {span}")
