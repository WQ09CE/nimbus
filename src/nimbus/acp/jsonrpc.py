"""JSON-RPC 2.0 implementation for Nimbus ACP.

This module provides a complete JSON-RPC 2.0 infrastructure including:
- Request/Response data classes
- Error handling with standard error codes
- Method routing and dispatching
- Connection abstraction for stdio transport

Specification: https://www.jsonrpc.org/specification
"""

from __future__ import annotations

import asyncio
import json
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar, Awaitable

log = logging.getLogger("nimbus.acp.jsonrpc")

# JSON-RPC 2.0 Standard Error Codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Type aliases
JSONValue = str | int | float | bool | None
JSONType = dict[str, Any] | list[Any] | str | int | float | bool | None
HandlerFunc = Callable[..., Awaitable[Any] | Any]
T = TypeVar("T")


class JSONRPCError(Exception):
    """JSON-RPC error with code, message, and optional data."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert error to JSON-RPC error object."""
        error_obj: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.data is not None:
            error_obj["data"] = self.data
        return error_obj

    def __repr__(self) -> str:
        return f"JSONRPCError(code={self.code}, message={self.message!r}, data={self.data!r})"


class ParseError(JSONRPCError):
    """Invalid JSON was received by the server."""

    def __init__(self, message: str = "Parse error", data: Any = None) -> None:
        super().__init__(PARSE_ERROR, message, data)


class InvalidRequest(JSONRPCError):
    """The JSON sent is not a valid Request object."""

    def __init__(self, message: str = "Invalid Request", data: Any = None) -> None:
        super().__init__(INVALID_REQUEST, message, data)


class MethodNotFound(JSONRPCError):
    """The method does not exist / is not available."""

    def __init__(self, message: str = "Method not found", data: Any = None) -> None:
        super().__init__(METHOD_NOT_FOUND, message, data)


class InvalidParams(JSONRPCError):
    """Invalid method parameter(s)."""

    def __init__(self, message: str = "Invalid params", data: Any = None) -> None:
        super().__init__(INVALID_PARAMS, message, data)


class InternalError(JSONRPCError):
    """Internal JSON-RPC error."""

    def __init__(self, message: str = "Internal error", data: Any = None) -> None:
        super().__init__(INTERNAL_ERROR, message, data)


@dataclass
class Request:
    """JSON-RPC 2.0 Request object.

    Attributes:
        method: The name of the method to be invoked.
        params: Parameter values to be used during method invocation.
        id: Request identifier. None for notifications.
    """

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = None

    @property
    def is_notification(self) -> bool:
        """Check if this request is a notification (no response expected)."""
        return self.id is None

    def to_dict(self) -> dict[str, Any]:
        """Convert request to JSON-RPC request object."""
        obj: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": self.method,
        }
        if self.params:
            obj["params"] = self.params
        if self.id is not None:
            obj["id"] = self.id
        return obj

    def to_json(self) -> bytes:
        """Serialize request to JSON bytes."""
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Request:
        """Create a Request from a dictionary."""
        method = data.get("method")
        if not isinstance(method, str):
            raise InvalidRequest("'method' must be a string")
        params = data.get("params", {})
        if not isinstance(params, (dict, list)):
            raise InvalidRequest("'params' must be an object or array")
        # Convert list params to dict with positional keys if needed
        if isinstance(params, list):
            params = {str(i): v for i, v in enumerate(params)}
        request_id = data.get("id")
        if request_id is not None and not isinstance(request_id, (int, str)):
            raise InvalidRequest("'id' must be a string, number, or null")
        return cls(method=method, params=params, id=request_id)


@dataclass
class Response:
    """JSON-RPC 2.0 Response object.

    Attributes:
        id: The request id that this response is replying to.
        result: The result of a successful method call.
        error: The error object if the method call failed.
    """

    id: int | str
    result: Any = None
    error_obj: dict[str, Any] | None = None

    @classmethod
    def success(cls, id: int | str, result: Any) -> Response:
        """Create a successful response."""
        return cls(id=id, result=result, error_obj=None)

    @classmethod
    def error(
        cls,
        id: int | str,
        code: int,
        message: str,
        data: Any = None,
    ) -> Response:
        """Create an error response."""
        err: dict[str, Any] = {
            "code": code,
            "message": message,
        }
        if data is not None:
            err["data"] = data
        return cls(id=id, error_obj=err)

    @classmethod
    def from_exception(cls, id: int | str, exc: JSONRPCError) -> Response:
        """Create an error response from a JSONRPCError exception."""
        return cls(id=id, error_obj=exc.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Convert response to JSON-RPC response object."""
        obj: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self.id,
        }
        if self.error_obj is not None:
            obj["error"] = self.error_obj
        else:
            obj["result"] = self.result
        return obj

    def to_json(self) -> bytes:
        """Serialize response to JSON bytes."""
        return json.dumps(self.to_dict()).encode("utf-8")

    @property
    def is_error(self) -> bool:
        """Check if this response is an error response."""
        return self.error_obj is not None


@dataclass
class MethodInfo:
    """Metadata about a registered method."""

    name: str
    handler: HandlerFunc
    is_notification: bool = False


class JSONRPCHandler:
    """JSON-RPC method router and dispatcher.

    Provides decorators for registering method handlers and handles
    dispatching incoming requests to the appropriate handler.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, MethodInfo] = {}

    def method(self, name: str | None = None) -> Callable[[HandlerFunc], HandlerFunc]:
        """Decorator to register a method handler.

        Args:
            name: The method name. If None, uses the function name.

        Returns:
            Decorator function.

        Example:
            @handler.method("echo")
            async def echo(message: str) -> str:
                return message
        """

        def decorator(func: HandlerFunc) -> HandlerFunc:
            method_name = name if name is not None else func.__name__
            self._handlers[method_name] = MethodInfo(
                name=method_name,
                handler=func,
                is_notification=False,
            )
            return func

        return decorator

    def notification(
        self, name: str | None = None
    ) -> Callable[[HandlerFunc], HandlerFunc]:
        """Decorator to register a notification handler.

        Notifications do not return responses.

        Args:
            name: The notification name. If None, uses the function name.

        Returns:
            Decorator function.

        Example:
            @handler.notification("log")
            async def log(level: str, message: str) -> None:
                print(f"[{level}] {message}")
        """

        def decorator(func: HandlerFunc) -> HandlerFunc:
            method_name = name if name is not None else func.__name__
            self._handlers[method_name] = MethodInfo(
                name=method_name,
                handler=func,
                is_notification=True,
            )
            return func

        return decorator

    def parse_request(self, data: bytes) -> Request:
        """Parse raw bytes into a Request object.

        Args:
            data: Raw JSON bytes.

        Returns:
            Parsed Request object.

        Raises:
            ParseError: If the data is not valid JSON.
            InvalidRequest: If the JSON is not a valid JSON-RPC request.
        """
        try:
            obj = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ParseError(f"Invalid JSON: {e}")

        if not isinstance(obj, dict):
            raise InvalidRequest("Request must be an object")

        # Validate JSON-RPC version
        jsonrpc = obj.get("jsonrpc")
        if jsonrpc != "2.0":
            raise InvalidRequest(f"Invalid JSON-RPC version: {jsonrpc!r}, expected '2.0'")

        return Request.from_dict(obj)

    async def handle(self, request: dict[str, Any]) -> Response | None:
        """Handle an incoming JSON-RPC request.

        Args:
            request: The request dictionary (already parsed JSON).

        Returns:
            Response object, or None for notifications.
        """
        request_id = request.get("id")

        try:
            # Validate JSON-RPC version
            jsonrpc = request.get("jsonrpc")
            if jsonrpc != "2.0":
                raise InvalidRequest(
                    f"Invalid JSON-RPC version: {jsonrpc!r}, expected '2.0'"
                )

            # Get method name
            method_name = request.get("method")
            if not isinstance(method_name, str):
                raise InvalidRequest("'method' must be a string")

            # Look up handler
            method_info = self._handlers.get(method_name)
            if method_info is None:
                raise MethodNotFound(f"Method not found: {method_name!r}")

            # Get params
            params = request.get("params", {})
            if isinstance(params, list):
                # Convert positional params to keyword args based on signature
                params = self._positional_to_keyword(method_info.handler, params)
            elif not isinstance(params, dict):
                raise InvalidParams("'params' must be an object or array")

            # Call handler
            try:
                result = method_info.handler(**params)
                if inspect.isawaitable(result):
                    result = await result
            except TypeError as e:
                # Likely a parameter binding issue
                raise InvalidParams(f"Invalid parameters: {e}")

            # Return response (None for notifications)
            if request_id is None:
                return None

            return Response.success(request_id, result)

        except JSONRPCError as e:
            if request_id is None:
                # Don't return errors for notifications
                log.warning(f"Error handling notification: {e}")
                return None
            return Response.from_exception(request_id, e)
        except Exception as e:
            log.exception(f"Unexpected error handling request: {e}")
            if request_id is None:
                return None
            return Response.error(
                request_id,
                INTERNAL_ERROR,
                f"Internal error: {e}",
            )

    def _positional_to_keyword(
        self, func: HandlerFunc, params: list[Any]
    ) -> dict[str, Any]:
        """Convert positional parameters to keyword arguments.

        Args:
            func: The handler function.
            params: List of positional parameters.

        Returns:
            Dictionary of keyword arguments.
        """
        sig = inspect.signature(func)
        param_names = list(sig.parameters.keys())

        if len(params) > len(param_names):
            raise InvalidParams(
                f"Too many parameters: got {len(params)}, expected at most {len(param_names)}"
            )

        return {name: value for name, value in zip(param_names, params)}

    async def handle_batch(
        self, requests: list[dict[str, Any]]
    ) -> list[Response] | None:
        """Handle a batch of JSON-RPC requests.

        Args:
            requests: List of request dictionaries.

        Returns:
            List of Response objects, or None if all were notifications.
        """
        if not requests:
            return [Response.error(None, INVALID_REQUEST, "Empty batch")]  # type: ignore

        responses: list[Response] = []
        for req in requests:
            if not isinstance(req, dict):
                responses.append(
                    Response.error(None, INVALID_REQUEST, "Invalid Request")  # type: ignore
                )
                continue

            response = await self.handle(req)
            if response is not None:
                responses.append(response)

        return responses if responses else None

    def list_methods(self) -> list[str]:
        """List all registered method names."""
        return list(self._handlers.keys())


class Connection(Protocol):
    """Protocol for JSON-RPC connections."""

    async def send(self, data: bytes) -> None:
        """Send data over the connection."""
        ...

    async def receive(self) -> bytes:
        """Receive data from the connection."""
        ...


class StdioConnection:
    """Stdio-based JSON-RPC connection.

    Uses length-prefixed framing for message boundaries.
    Format: Content-Length: <length>\r\n\r\n<content>
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer

    async def send(self, data: bytes) -> None:
        """Send a message with Content-Length header.

        Args:
            data: The message bytes to send.
        """
        header = f"Content-Length: {len(data)}\r\n\r\n"
        self._writer.write(header.encode("ascii") + data)
        await self._writer.drain()

    async def receive(self) -> bytes:
        """Receive a message with Content-Length header.

        Returns:
            The message bytes.

        Raises:
            EOFError: If the connection is closed.
            ValueError: If the header is malformed.
        """
        # Read headers
        content_length: int | None = None

        while True:
            line = await self._reader.readline()
            if not line:
                raise EOFError("Connection closed")

            line_str = line.decode("ascii").strip()

            # Empty line signals end of headers
            if not line_str:
                break

            # Parse header
            if line_str.startswith("Content-Length:"):
                try:
                    content_length = int(line_str.split(":", 1)[1].strip())
                except ValueError as e:
                    raise ValueError(f"Invalid Content-Length header: {e}")

        if content_length is None:
            raise ValueError("Missing Content-Length header")

        # Read content
        data = await self._reader.readexactly(content_length)
        return data

    async def close(self) -> None:
        """Close the connection."""
        self._writer.close()
        await self._writer.wait_closed()


async def create_stdio_connection() -> StdioConnection:
    """Create a StdioConnection using stdin/stdout.

    Returns:
        A StdioConnection instance.
    """
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)

    loop = asyncio.get_running_loop()
    await loop.connect_read_pipe(lambda: protocol, asyncio.sys.stdin)

    writer_transport, writer_protocol = await loop.connect_write_pipe(
        asyncio.streams.FlowControlMixin, asyncio.sys.stdout
    )
    writer = asyncio.StreamWriter(writer_transport, writer_protocol, reader, loop)

    return StdioConnection(reader, writer)


class JSONRPCServer:
    """A simple JSON-RPC server that handles requests over a connection."""

    def __init__(self, handler: JSONRPCHandler, connection: Connection) -> None:
        self._handler = handler
        self._connection = connection
        self._running = False

    async def serve(self) -> None:
        """Start serving JSON-RPC requests.

        Runs until the connection is closed or stop() is called.
        """
        self._running = True

        while self._running:
            try:
                data = await self._connection.receive()
            except EOFError:
                log.info("Connection closed")
                break
            except Exception as e:
                log.error(f"Error receiving data: {e}")
                break

            try:
                obj = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                response = Response.error(None, PARSE_ERROR, f"Parse error: {e}")  # type: ignore
                await self._connection.send(response.to_json())
                continue

            # Handle batch or single request
            if isinstance(obj, list):
                responses = await self._handler.handle_batch(obj)
                if responses:
                    batch_response = json.dumps(
                        [r.to_dict() for r in responses]
                    ).encode("utf-8")
                    await self._connection.send(batch_response)
            elif isinstance(obj, dict):
                response = await self._handler.handle(obj)
                if response is not None:
                    await self._connection.send(response.to_json())
            else:
                response = Response.error(
                    None, INVALID_REQUEST, "Request must be an object or array"  # type: ignore
                )
                await self._connection.send(response.to_json())

    def stop(self) -> None:
        """Stop the server."""
        self._running = False


class JSONRPCClient:
    """A simple JSON-RPC client for sending requests over a connection."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._request_id = 0
        self._pending: dict[int | str, asyncio.Future[Any]] = {}

    def _next_id(self) -> int:
        """Generate the next request ID."""
        self._request_id += 1
        return self._request_id

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Call a remote method and wait for the response.

        Args:
            method: The method name.
            params: Optional method parameters.

        Returns:
            The method result.

        Raises:
            JSONRPCError: If the server returns an error.
        """
        request_id = self._next_id()
        request = Request(method=method, params=params or {}, id=request_id)

        # Create future for response
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        try:
            await self._connection.send(request.to_json())
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a notification (no response expected).

        Args:
            method: The method name.
            params: Optional method parameters.
        """
        request = Request(method=method, params=params or {}, id=None)
        await self._connection.send(request.to_json())

    async def receive_response(self) -> None:
        """Receive and dispatch a response.

        This should be called in a loop or task to handle incoming responses.
        """
        data = await self._connection.receive()
        obj = json.loads(data.decode("utf-8"))

        if not isinstance(obj, dict):
            log.warning(f"Received non-object response: {obj}")
            return

        request_id = obj.get("id")
        if request_id is None:
            log.warning(f"Received response without id: {obj}")
            return

        future = self._pending.get(request_id)
        if future is None:
            log.warning(f"Received response for unknown request: {request_id}")
            return

        if "error" in obj:
            error = obj["error"]
            future.set_exception(
                JSONRPCError(
                    code=error.get("code", INTERNAL_ERROR),
                    message=error.get("message", "Unknown error"),
                    data=error.get("data"),
                )
            )
        else:
            future.set_result(obj.get("result"))
