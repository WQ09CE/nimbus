"""Logging system for Nimbus using Loguru.

Features:
- Beautiful colored console output
- JSON file logging with rotation
- Exception catching decorator
- Structured logging with context binding
- Multi-agent support with agent_id/task_id/trace_id context
- Process-safe logging with enqueue
- Intercepts standard logging module (for kernel layer compatibility)
"""

import logging
import sys
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger as _loguru_logger

# Re-export loguru's logger for direct use
logger = _loguru_logger


class InterceptHandler(logging.Handler):
    """Handler to intercept standard logging and redirect to loguru.

    This allows kernel layer (vcpu, scheduler) which uses standard logging
    to have their logs unified into the loguru system.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Use record's pathname and lineno directly for accurate source info
        # Bind the original logger name and location
        logger.bind(
            name=record.name,
        ).opt(
            exception=record.exc_info,
        ).log(level, f"{record.name}:{record.funcName}:{record.lineno} - {record.getMessage()}")


# Default configuration
DEFAULT_LOG_DIR = "./.logs"
DEFAULT_ROTATION = "10 MB"
DEFAULT_RETENTION = "7 days"

# Context variables for multi-agent tracing
_agent_id: ContextVar[Optional[str]] = ContextVar("agent_id", default=None)
_task_id: ContextVar[Optional[str]] = ContextVar("task_id", default=None)
_trace_id: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


class LogLevel(Enum):
    """Log level enumeration."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class LogEvent:
    """Structured log event for programmatic access."""

    event: str
    level: LogLevel
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "level": self.level.value,
            "timestamp": self.timestamp.isoformat(),
            **self.data,
        }


def _format_context(record: dict) -> str:
    """Format agent context for log message."""
    extra = record.get("extra", {})
    parts = []
    if extra.get("agent_id"):
        parts.append(f"agent={extra['agent_id']}")
    if extra.get("task_id"):
        parts.append(f"task={extra['task_id']}")
    if extra.get("trace_id"):
        parts.append(f"trace={extra['trace_id'][:8]}")  # Short trace_id
    return f"[{' '.join(parts)}] " if parts else ""


def setup_logging(
    level: str = "INFO",
    log_dir: str = DEFAULT_LOG_DIR,
    log_file: Optional[str] = None,
    rotation: str = DEFAULT_ROTATION,
    retention: str = DEFAULT_RETENTION,
    console: Optional[bool] = None,
    colorize: bool = True,
    json_file: bool = False,
    enqueue: bool = True,
    intercept_stdlib: bool = True,
) -> str:
    """Configure logging with console and file output.

    Args:
        level: Minimum log level (TRACE, DEBUG, INFO, SUCCESS, WARNING, ERROR, CRITICAL).
        log_dir: Directory for log files.
        log_file: Log file name (default: nimbus.log).
        rotation: When to rotate (e.g., "10 MB", "1 day", "00:00").
        retention: How long to keep old logs (e.g., "7 days", "1 week").
        console: Enable console output.
        colorize: Enable colored console output.
        json_file: Enable JSON format for file logs (for ELK/Grafana).
        enqueue: Enable queue-based logging for multi-process safety.
        intercept_stdlib: Intercept standard logging module (for kernel layer).

    Returns:
        Path to the log file.
    """
    import os

    # Remove default handler
    logger.remove()

    # Check environment variable for console logging
    if console is None:
        env_console = os.environ.get("NIMBUS_LOG_CONSOLE", "true").lower()
        console = env_console not in ("false", "0", "no", "off")

    # Intercept standard logging module (for kernel layer: vcpu, scheduler)
    if intercept_stdlib:
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
        # Also intercept specific loggers used by kernel
        for logger_name in [
            "nimbus.kernel.vcpu",
            "nimbus.kernel.scheduler",
            "nimbus.apps.code_agent",
            "nimbus.llm.gemini",
        ]:
            stdlib_logger = logging.getLogger(logger_name)
            stdlib_logger.handlers = [InterceptHandler()]
            stdlib_logger.propagate = False

    # Ensure log directory exists
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Determine log file path
    if log_file is None:
        log_file = "nimbus.log"
    log_file_path = log_path / log_file
    json_file_path = log_path / log_file.replace(".log", ".json")

    # Console handler (colored, human-readable) with agent context
    if console:

        def console_formatter(record):
            ctx = _format_context(record)
            ctx_part = f"<yellow>{ctx}</yellow>" if ctx else ""
            return (
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                + ctx_part
                + "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>\n{exception}"
            )

        logger.add(
            sys.stderr,
            format=console_formatter,
            level=level,
            colorize=colorize,
            enqueue=enqueue,
        )

    # File handler (plain text, for quick reading)
    def file_formatter(record):
        ctx = _format_context(record)
        return (
            "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
            + (f"{ctx}" if ctx else "")
            + "{name}:{function}:{line} | {message}\n{exception}"
        )

    logger.add(
        str(log_file_path),
        format=file_formatter,
        level=level,
        rotation=rotation,
        retention=retention,
        encoding="utf-8",
        enqueue=enqueue,
    )

    # JSON file handler (structured, for analysis)
    if json_file:
        logger.add(
            str(json_file_path),
            format="{message}",
            level=level,
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
            serialize=True,  # Loguru's built-in JSON serialization
            enqueue=enqueue,
        )

    logger.info(f"Logging initialized | log_dir={log_dir} level={level}")

    return str(log_file_path)


def quick_setup(
    level: str = "INFO",
    log_dir: str = DEFAULT_LOG_DIR,
    session_id: Optional[str] = None,
) -> str:
    """Quick setup with sensible defaults.

    Args:
        level: Log level.
        log_dir: Log directory.
        session_id: Optional session ID for log file naming.

    Returns:
        Path to log file.
    """
    if session_id:
        log_file = f"nimbus_{session_id}.log"
    else:
        log_file = "nimbus.log"

    return setup_logging(
        level=level,
        log_dir=log_dir,
        log_file=log_file,
    )


def get_logger(name: str = "nimbus"):
    """Get a contextualized logger.

    Args:
        name: Logger context name.

    Returns:
        Bound logger instance.
    """
    return logger.bind(name=name)


def get_agent_logger(
    agent_id: str,
    task_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    name: str = "agent",
):
    """Get a logger bound with agent context.

    Args:
        agent_id: Unique agent identifier.
        task_id: Optional task identifier.
        trace_id: Optional trace identifier for distributed tracing.
        name: Logger name.

    Returns:
        Bound logger instance with agent context.

    Example:
        log = get_agent_logger("agent-001", task_id="task-123")
        log.info("Processing started")
        # Output: 2024-01-01 12:00:00 | INFO | [agent=agent-001 task=task-123] ...
    """
    ctx = {"name": name, "agent_id": agent_id}
    if task_id:
        ctx["task_id"] = task_id
    if trace_id:
        ctx["trace_id"] = trace_id
    return logger.bind(**ctx)


class AgentLogContext:
    """Context manager for agent logging scope.

    Example:
        with AgentLogContext(agent_id="agent-001", task_id="task-123"):
            logger.info("Inside agent context")
            # All logs in this block will have agent context
    """

    def __init__(
        self,
        agent_id: str,
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ):
        self.agent_id = agent_id
        self.task_id = task_id
        self.trace_id = trace_id
        self._tokens = []

    def __enter__(self):
        self._tokens.append(_agent_id.set(self.agent_id))
        if self.task_id:
            self._tokens.append(_task_id.set(self.task_id))
        if self.trace_id:
            self._tokens.append(_trace_id.set(self.trace_id))

        # Also bind to loguru
        ctx = {"agent_id": self.agent_id}
        if self.task_id:
            ctx["task_id"] = self.task_id
        if self.trace_id:
            ctx["trace_id"] = self.trace_id
        self._context = logger.contextualize(**ctx)
        self._context.__enter__()
        return self

    def __exit__(self, *args):
        self._context.__exit__(*args)
        for token in reversed(self._tokens):
            token.var.reset(token)


def agent_context(
    agent_id: str,
    task_id: Optional[str] = None,
    trace_id: Optional[str] = None,
):
    """Context manager for agent logging scope.

    Example:
        with agent_context("agent-001", task_id="task-123"):
            logger.info("Inside agent context")
    """
    return AgentLogContext(agent_id, task_id, trace_id)


# Convenience: expose @logger.catch for exception handling
catch = logger.catch


# Context manager for temporary log context
def log_context(**kwargs):
    """Context manager to add temporary context to logs.

    Example:
        with log_context(request_id="123", user="alice"):
            logger.info("Processing request")
    """
    return logger.contextualize(**kwargs)


# Structured logging helper
def log_event(event: str, level: str = "INFO", **data):
    """Log a structured event with data.

    Args:
        event: Event name.
        level: Log level.
        **data: Additional structured data.

    Example:
        log_event("task_started", task_id="t001", skill="rag_search")
    """
    log_func = getattr(logger, level.lower())
    if data:
        data_str = " ".join(f"{k}={v}" for k, v in data.items())
        log_func(f"{event} | {data_str}")
    else:
        log_func(event)


# Module-level convenience functions
def debug(msg: str, **kwargs):
    """Log debug message."""
    log_event(msg, "DEBUG", **kwargs)


def info(msg: str, **kwargs):
    """Log info message."""
    log_event(msg, "INFO", **kwargs)


def success(msg: str, **kwargs):
    """Log success message."""
    log_event(msg, "SUCCESS", **kwargs)


def warning(msg: str, **kwargs):
    """Log warning message."""
    log_event(msg, "WARNING", **kwargs)


def error(msg: str, **kwargs):
    """Log error message."""
    log_event(msg, "ERROR", **kwargs)


def critical(msg: str, **kwargs):
    """Log critical message."""
    log_event(msg, "CRITICAL", **kwargs)
