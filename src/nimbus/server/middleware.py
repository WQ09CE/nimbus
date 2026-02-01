"""Middleware for Nimbus Server.

This module provides:
- CORS middleware configuration
- Request logging middleware
- Error handling middleware
"""

import logging
import time
from datetime import datetime
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("nimbus.server")


def setup_cors(
    app: FastAPI,
    allow_origins: list = None,
    allow_credentials: bool = True,
    allow_methods: list = None,
    allow_headers: list = None,
) -> None:
    """
    Configure CORS middleware for the FastAPI app.

    Args:
        app: FastAPI application instance.
        allow_origins: List of allowed origins (default: ["*"]).
        allow_credentials: Allow credentials in CORS requests.
        allow_methods: Allowed HTTP methods (default: ["*"]).
        allow_headers: Allowed headers (default: ["*"]).
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or ["*"],
        allow_credentials=allow_credentials,
        allow_methods=allow_methods or ["*"],
        allow_headers=allow_headers or ["*"],
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests and responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request with logging.

        Args:
            request: Incoming request.
            call_next: Next middleware/handler.

        Returns:
            Response from handler.
        """
        # Start timing
        start_time = time.time()

        # Log request
        logger.info(
            f"Request: {request.method} {request.url.path}",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query": str(request.query_params),
                "client": request.client.host if request.client else "unknown",
            },
        )

        # Process request
        try:
            response = await call_next(request)
        except Exception as e:
            # Log error
            logger.error(
                f"Request failed: {request.method} {request.url.path} - {str(e)}",
                exc_info=True,
            )
            raise

        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)

        # Log response
        logger.info(
            f"Response: {request.method} {request.url.path} - {response.status_code} ({duration_ms}ms)",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        # Add timing header
        response.headers["X-Response-Time"] = f"{duration_ms}ms"

        return response


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to add request ID to each request."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Add request ID to request and response.

        Args:
            request: Incoming request.
            call_next: Next middleware/handler.

        Returns:
            Response with request ID header.
        """
        import uuid

        # Generate or get request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])

        # Store in request state
        request.state.request_id = request_id

        # Process request
        response = await call_next(request)

        # Add to response headers
        response.headers["X-Request-ID"] = request_id

        return response


def setup_middleware(app: FastAPI, enable_logging: bool = True) -> None:
    """
    Configure all middleware for the FastAPI app.

    Args:
        app: FastAPI application instance.
        enable_logging: Enable request logging.
    """
    # CORS - must be added first
    setup_cors(app)

    # Request ID
    app.add_middleware(RequestIDMiddleware)

    # Logging
    if enable_logging:
        app.add_middleware(RequestLoggingMiddleware)


def create_error_response(
    status_code: int,
    code: str,
    message: str,
    details: dict = None,
) -> dict:
    """
    Create a standardized error response.

    Args:
        status_code: HTTP status code.
        code: Error code string.
        message: Human-readable message.
        details: Optional additional details.

    Returns:
        Error response dict.
    """
    return {
        "code": code,
        "message": message,
        "details": details,
        "timestamp": datetime.now().isoformat(),
    }
