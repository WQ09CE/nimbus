"""Tests for logging and tracing modules."""

import pytest
import time
import tempfile
import os
from datetime import datetime

from nimbus.core.logging import (
    LogLevel,
    LogEvent,
    logger,
    get_logger,
    setup_logging,
    catch,
    log_context,
)
from nimbus.core.tracing import (
    Span,
    Tracer,
    get_tracer,
    reset_tracer,
    trace,
)


class TestLogEvent:
    """Tests for LogEvent dataclass."""

    def test_log_event_creation(self):
        """Test LogEvent creation."""
        event = LogEvent(
            event="test_event",
            level=LogLevel.INFO,
            data={"key": "value"}
        )

        assert event.event == "test_event"
        assert event.level == LogLevel.INFO
        assert event.data["key"] == "value"
        assert isinstance(event.timestamp, datetime)

    def test_log_event_to_dict(self):
        """Test LogEvent to_dict method."""
        event = LogEvent(
            event="test_event",
            level=LogLevel.ERROR,
            data={"error": "something went wrong"}
        )

        d = event.to_dict()

        assert d["event"] == "test_event"
        assert d["level"] == "ERROR"
        assert "timestamp" in d
        assert d["error"] == "something went wrong"


class TestLoguru:
    """Tests for loguru-based logging."""

    def test_get_logger(self):
        """Test get_logger returns bound logger."""
        my_logger = get_logger("test_module")
        assert my_logger is not None

    def test_logger_levels(self):
        """Test all log levels work."""
        # These should not raise
        logger.debug("debug message")
        logger.info("info message")
        logger.warning("warning message")
        logger.error("error message")

    def test_log_context(self):
        """Test log_context context manager."""
        with log_context(request_id="123", user="alice"):
            # Should not raise
            logger.info("Message with context")

    def test_catch_decorator(self):
        """Test @catch decorator."""
        @catch(reraise=False)
        def might_fail():
            raise ValueError("Test error")

        # Should not raise due to @catch
        might_fail()


class TestSpan:
    """Tests for Span dataclass."""

    def test_span_creation(self):
        """Test span creation."""
        span = Span(id="test-1", name="test_span")

        assert span.id == "test-1"
        assert span.name == "test_span"
        assert span.parent_id is None
        assert span.status == "ok"
        assert isinstance(span.start_time, datetime)

    def test_span_set_attribute(self):
        """Test setting span attributes."""
        span = Span(id="test", name="test")

        span.set_attribute("key1", "value1")
        span.set_attributes({"key2": "value2", "key3": 123})

        assert span.attributes["key1"] == "value1"
        assert span.attributes["key2"] == "value2"
        assert span.attributes["key3"] == 123

    def test_span_add_event(self):
        """Test adding events to span."""
        span = Span(id="test", name="test")

        span.add_event("checkpoint1", {"progress": 50})
        span.add_event("checkpoint2")

        assert len(span.events) == 2
        assert span.events[0]["name"] == "checkpoint1"
        assert span.events[0]["attributes"]["progress"] == 50

    def test_span_end(self):
        """Test ending a span."""
        span = Span(id="test", name="test")
        time.sleep(0.01)  # Small delay
        span.end("ok")

        assert span.end_time is not None
        assert span.status == "ok"
        assert span.duration_ms > 0

    def test_span_end_with_error(self):
        """Test ending span with error."""
        span = Span(id="test", name="test")
        span.end("error", "Something failed")

        assert span.status == "error"
        assert span.error_message == "Something failed"
        assert span.is_error is True

    def test_span_to_dict(self):
        """Test span to_dict method."""
        span = Span(id="test", name="test_span")
        span.set_attribute("key", "value")
        span.end()

        d = span.to_dict()

        assert d["id"] == "test"
        assert d["name"] == "test_span"
        assert "start_time" in d
        assert "end_time" in d
        assert d["attributes"]["key"] == "value"


class TestTracer:
    """Tests for Tracer class."""

    def setup_method(self):
        """Reset tracer before each test."""
        reset_tracer()

    def test_tracer_creation(self):
        """Test tracer creation."""
        tracer = Tracer("test-service")
        assert tracer.service_name == "test-service"
        assert len(tracer._spans) == 0

    def test_tracer_start_span(self):
        """Test starting a span."""
        tracer = Tracer("test")

        with tracer.start_span("operation1") as span:
            assert span.name == "operation1"
            assert tracer.current_span == span

        assert tracer.current_span is None
        assert len(tracer.get_spans()) == 1

    def test_tracer_nested_spans(self):
        """Test nested spans."""
        tracer = Tracer("test")

        with tracer.start_span("parent") as parent:
            assert parent.parent_id is None

            with tracer.start_span("child") as child:
                assert child.parent_id == parent.id

                with tracer.start_span("grandchild") as grandchild:
                    assert grandchild.parent_id == child.id

        spans = tracer.get_spans()
        assert len(spans) == 3

    def test_tracer_span_with_exception(self):
        """Test span handling when exception occurs."""
        tracer = Tracer("test")

        with pytest.raises(ValueError):
            with tracer.start_span("failing_op") as span:
                raise ValueError("Test error")

        spans = tracer.get_spans()
        assert len(spans) == 1
        assert spans[0].status == "error"
        assert spans[0].error_message == "Test error"

    def test_tracer_get_trace_summary(self):
        """Test getting trace summary."""
        tracer = Tracer("test-service")

        with tracer.start_span("op1"):
            time.sleep(0.01)

        with tracer.start_span("op2"):
            pass

        summary = tracer.get_trace_summary()

        assert summary["service"] == "test-service"
        assert summary["span_count"] == 2
        assert summary["error_count"] == 0
        assert len(summary["spans"]) == 2

    def test_tracer_get_span_tree(self):
        """Test getting span tree structure."""
        tracer = Tracer("test")

        with tracer.start_span("root1"):
            with tracer.start_span("child1"):
                pass

        with tracer.start_span("root2"):
            pass

        tree = tracer.get_span_tree()

        assert tree["service"] == "test"
        assert len(tree["roots"]) == 2

    def test_tracer_clear(self):
        """Test clearing tracer."""
        tracer = Tracer("test")

        with tracer.start_span("op"):
            pass

        assert len(tracer.get_spans()) == 1

        tracer.clear()

        assert len(tracer.get_spans()) == 0
        assert tracer.current_span is None

    def test_tracer_export_json(self):
        """Test exporting traces as JSON."""
        tracer = Tracer("test")

        with tracer.start_span("op", {"key": "value"}):
            pass

        json_str = tracer.export_json()

        assert "test" in json_str
        assert "op" in json_str


class TestGlobalTracer:
    """Tests for global tracer functions."""

    def setup_method(self):
        """Reset tracer before each test."""
        reset_tracer()

    def test_get_tracer_singleton(self):
        """Test that get_tracer returns singleton."""
        tracer1 = get_tracer()
        tracer2 = get_tracer()

        assert tracer1 is tracer2

    def test_reset_tracer(self):
        """Test resetting global tracer."""
        tracer1 = get_tracer()
        reset_tracer()
        tracer2 = get_tracer()

        assert tracer1 is not tracer2


class TestTraceDecorator:
    """Tests for @trace decorator."""

    def setup_method(self):
        """Reset tracer before each test."""
        reset_tracer()

    def test_trace_sync_function(self):
        """Test tracing synchronous function."""
        @trace()
        def my_function(x, y):
            return x + y

        result = my_function(1, 2)

        assert result == 3

        tracer = get_tracer()
        spans = tracer.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "my_function"

    def test_trace_async_function(self):
        """Test tracing async function."""
        @trace()
        async def my_async_function(x):
            return x * 2

        import asyncio
        result = asyncio.run(my_async_function(5))

        assert result == 10

        tracer = get_tracer()
        spans = tracer.get_spans()
        assert len(spans) == 1
        assert spans[0].name == "my_async_function"

    def test_trace_with_custom_name(self):
        """Test trace decorator with custom name."""
        @trace(name="custom_operation")
        def my_function():
            pass

        my_function()

        tracer = get_tracer()
        spans = tracer.get_spans()
        assert spans[0].name == "custom_operation"

    def test_trace_with_attributes(self):
        """Test trace decorator with initial attributes."""
        @trace(attributes={"operation": "test"})
        def my_function():
            pass

        my_function()

        tracer = get_tracer()
        spans = tracer.get_spans()
        assert spans[0].attributes["operation"] == "test"

    def test_trace_captures_exception(self):
        """Test that trace decorator captures exceptions."""
        @trace()
        def failing_function():
            raise RuntimeError("Test error")

        with pytest.raises(RuntimeError):
            failing_function()

        tracer = get_tracer()
        spans = tracer.get_spans()
        assert len(spans) == 1
        assert spans[0].status == "error"


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_setup_logging_default(self):
        """Test default logging setup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = setup_logging(log_dir=tmpdir, console=False)
            assert os.path.exists(log_path)

    def test_setup_logging_with_level(self):
        """Test logging setup with level."""
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level="DEBUG", log_dir=tmpdir, console=False)

    def test_setup_logging_json_file(self):
        """Test logging setup with JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(log_dir=tmpdir, json_file=True, console=False)
            json_path = os.path.join(tmpdir, "nimbus.json")
            # Log something to create the file
            logger.info("test message")
            assert os.path.exists(json_path)
