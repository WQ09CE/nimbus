"""Tests for nimbus.acp.jsonrpc module."""

import asyncio
import json
import pytest

from nimbus.acp.jsonrpc import (
    # Error codes
    PARSE_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
    # Exceptions
    JSONRPCError,
    ParseError,
    InvalidRequest,
    MethodNotFound,
    InvalidParams,
    InternalError,
    # Data classes
    Request,
    Response,
    # Handler
    JSONRPCHandler,
)


class TestErrorCodes:
    """Test standard JSON-RPC error codes."""

    def test_parse_error_code(self):
        assert PARSE_ERROR == -32700

    def test_invalid_request_code(self):
        assert INVALID_REQUEST == -32600

    def test_method_not_found_code(self):
        assert METHOD_NOT_FOUND == -32601

    def test_invalid_params_code(self):
        assert INVALID_PARAMS == -32602

    def test_internal_error_code(self):
        assert INTERNAL_ERROR == -32603


class TestJSONRPCError:
    """Test JSONRPCError exception class."""

    def test_basic_error(self):
        error = JSONRPCError(code=-32600, message="Invalid Request")
        assert error.code == -32600
        assert error.message == "Invalid Request"
        assert error.data is None

    def test_error_with_data(self):
        error = JSONRPCError(code=-32602, message="Invalid params", data={"field": "name"})
        assert error.code == -32602
        assert error.message == "Invalid params"
        assert error.data == {"field": "name"}

    def test_to_dict(self):
        error = JSONRPCError(code=-32600, message="Invalid Request")
        result = error.to_dict()
        assert result == {"code": -32600, "message": "Invalid Request"}

    def test_to_dict_with_data(self):
        error = JSONRPCError(code=-32602, message="Invalid params", data={"field": "name"})
        result = error.to_dict()
        assert result == {
            "code": -32602,
            "message": "Invalid params",
            "data": {"field": "name"},
        }


class TestSpecificErrors:
    """Test specific error subclasses."""

    def test_parse_error(self):
        error = ParseError("Bad JSON")
        assert error.code == PARSE_ERROR
        assert error.message == "Bad JSON"

    def test_invalid_request(self):
        error = InvalidRequest("Missing method")
        assert error.code == INVALID_REQUEST
        assert error.message == "Missing method"

    def test_method_not_found(self):
        error = MethodNotFound("unknown_method")
        assert error.code == METHOD_NOT_FOUND
        assert error.message == "unknown_method"

    def test_invalid_params(self):
        error = InvalidParams("Wrong type")
        assert error.code == INVALID_PARAMS
        assert error.message == "Wrong type"

    def test_internal_error(self):
        error = InternalError("Server crashed")
        assert error.code == INTERNAL_ERROR
        assert error.message == "Server crashed"


class TestRequest:
    """Test Request dataclass."""

    def test_basic_request(self):
        req = Request(method="echo", params={"message": "hello"}, id=1)
        assert req.method == "echo"
        assert req.params == {"message": "hello"}
        assert req.id == 1
        assert not req.is_notification

    def test_notification(self):
        req = Request(method="log", params={"level": "info"})
        assert req.method == "log"
        assert req.id is None
        assert req.is_notification

    def test_to_dict(self):
        req = Request(method="add", params={"a": 1, "b": 2}, id="req-1")
        result = req.to_dict()
        assert result == {
            "jsonrpc": "2.0",
            "method": "add",
            "params": {"a": 1, "b": 2},
            "id": "req-1",
        }

    def test_to_dict_notification(self):
        req = Request(method="notify", params={"msg": "hello"})
        result = req.to_dict()
        assert result == {
            "jsonrpc": "2.0",
            "method": "notify",
            "params": {"msg": "hello"},
        }
        assert "id" not in result

    def test_to_dict_empty_params(self):
        req = Request(method="ping", id=1)
        result = req.to_dict()
        assert result == {
            "jsonrpc": "2.0",
            "method": "ping",
            "id": 1,
        }
        assert "params" not in result

    def test_to_json(self):
        req = Request(method="echo", params={"msg": "test"}, id=1)
        json_bytes = req.to_json()
        parsed = json.loads(json_bytes)
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["method"] == "echo"
        assert parsed["params"] == {"msg": "test"}
        assert parsed["id"] == 1

    def test_from_dict(self):
        data = {"jsonrpc": "2.0", "method": "add", "params": {"a": 1}, "id": 1}
        req = Request.from_dict(data)
        assert req.method == "add"
        assert req.params == {"a": 1}
        assert req.id == 1

    def test_from_dict_with_list_params(self):
        data = {"jsonrpc": "2.0", "method": "add", "params": [1, 2], "id": 1}
        req = Request.from_dict(data)
        assert req.method == "add"
        # List params converted to dict
        assert req.params == {"0": 1, "1": 2}


class TestResponse:
    """Test Response dataclass."""

    def test_success_response(self):
        resp = Response.success(id=1, result="hello")
        assert resp.id == 1
        assert resp.result == "hello"
        assert resp.error_obj is None
        assert not resp.is_error

    def test_error_response(self):
        resp = Response.error(id=1, code=-32600, message="Invalid Request")
        assert resp.id == 1
        assert resp.result is None
        assert resp.error_obj == {"code": -32600, "message": "Invalid Request"}
        assert resp.is_error

    def test_error_response_with_data(self):
        resp = Response.error(
            id=1, code=-32602, message="Invalid params", data={"field": "a"}
        )
        assert resp.error_obj == {
            "code": -32602,
            "message": "Invalid params",
            "data": {"field": "a"},
        }

    def test_from_exception(self):
        error = JSONRPCError(code=-32601, message="Method not found")
        resp = Response.from_exception(id=1, exc=error)
        assert resp.id == 1
        assert resp.error_obj == {"code": -32601, "message": "Method not found"}

    def test_to_dict_success(self):
        resp = Response.success(id=1, result={"sum": 3})
        result = resp.to_dict()
        assert result == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"sum": 3},
        }
        assert "error" not in result

    def test_to_dict_error(self):
        resp = Response.error(id=1, code=-32600, message="Bad request")
        result = resp.to_dict()
        assert result == {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Bad request"},
        }
        assert "result" not in result

    def test_to_json(self):
        resp = Response.success(id=1, result="ok")
        json_bytes = resp.to_json()
        parsed = json.loads(json_bytes)
        assert parsed["jsonrpc"] == "2.0"
        assert parsed["id"] == 1
        assert parsed["result"] == "ok"


class TestJSONRPCHandler:
    """Test JSONRPCHandler class."""

    @pytest.fixture
    def handler(self):
        h = JSONRPCHandler()

        @h.method("echo")
        async def echo(message: str) -> str:
            return message

        @h.method("add")
        async def add(a: int, b: int) -> int:
            return a + b

        @h.method()  # Auto-detect name
        async def multiply(x: int, y: int) -> int:
            return x * y

        @h.notification("log")
        async def log_message(level: str, message: str) -> None:
            pass  # Notifications don't return

        return h

    def test_method_registration(self, handler):
        methods = handler.list_methods()
        assert "echo" in methods
        assert "add" in methods
        assert "multiply" in methods
        assert "log" in methods

    @pytest.mark.asyncio
    async def test_handle_method_call(self, handler):
        request = {
            "jsonrpc": "2.0",
            "method": "echo",
            "params": {"message": "hello"},
            "id": 1,
        }
        response = await handler.handle(request)
        assert response is not None
        assert response.id == 1
        assert response.result == "hello"
        assert response.error_obj is None

    @pytest.mark.asyncio
    async def test_handle_add(self, handler):
        request = {
            "jsonrpc": "2.0",
            "method": "add",
            "params": {"a": 10, "b": 20},
            "id": 2,
        }
        response = await handler.handle(request)
        assert response.result == 30

    @pytest.mark.asyncio
    async def test_handle_notification(self, handler):
        request = {
            "jsonrpc": "2.0",
            "method": "log",
            "params": {"level": "info", "message": "test"},
        }
        response = await handler.handle(request)
        assert response is None  # Notifications don't get responses

    @pytest.mark.asyncio
    async def test_method_not_found(self, handler):
        request = {
            "jsonrpc": "2.0",
            "method": "unknown_method",
            "params": {},
            "id": 1,
        }
        response = await handler.handle(request)
        assert response.is_error
        assert response.error_obj["code"] == METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_invalid_jsonrpc_version(self, handler):
        request = {
            "jsonrpc": "1.0",
            "method": "echo",
            "params": {"message": "test"},
            "id": 1,
        }
        response = await handler.handle(request)
        assert response.is_error
        assert response.error_obj["code"] == INVALID_REQUEST

    @pytest.mark.asyncio
    async def test_missing_method(self, handler):
        request = {
            "jsonrpc": "2.0",
            "params": {},
            "id": 1,
        }
        response = await handler.handle(request)
        assert response.is_error
        assert response.error_obj["code"] == INVALID_REQUEST

    @pytest.mark.asyncio
    async def test_invalid_params(self, handler):
        request = {
            "jsonrpc": "2.0",
            "method": "add",
            "params": {"wrong_param": 1},  # Wrong parameter name
            "id": 1,
        }
        response = await handler.handle(request)
        assert response.is_error
        assert response.error_obj["code"] == INVALID_PARAMS

    @pytest.mark.asyncio
    async def test_positional_params(self, handler):
        request = {
            "jsonrpc": "2.0",
            "method": "add",
            "params": [5, 3],  # Positional params as array
            "id": 1,
        }
        response = await handler.handle(request)
        assert response.result == 8

    def test_parse_request_valid(self, handler):
        data = b'{"jsonrpc": "2.0", "method": "echo", "params": {"message": "test"}, "id": 1}'
        request = handler.parse_request(data)
        assert request.method == "echo"
        assert request.params == {"message": "test"}
        assert request.id == 1

    def test_parse_request_invalid_json(self, handler):
        data = b"not valid json"
        with pytest.raises(ParseError):
            handler.parse_request(data)

    def test_parse_request_wrong_version(self, handler):
        data = b'{"jsonrpc": "1.0", "method": "test", "id": 1}'
        with pytest.raises(InvalidRequest):
            handler.parse_request(data)

    @pytest.mark.asyncio
    async def test_handle_batch(self, handler):
        requests = [
            {"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1},
            {"jsonrpc": "2.0", "method": "echo", "params": {"message": "hi"}, "id": 2},
        ]
        responses = await handler.handle_batch(requests)
        assert len(responses) == 2
        assert responses[0].result == 3
        assert responses[1].result == "hi"

    @pytest.mark.asyncio
    async def test_handle_batch_with_notifications(self, handler):
        requests = [
            {"jsonrpc": "2.0", "method": "add", "params": {"a": 1, "b": 2}, "id": 1},
            {"jsonrpc": "2.0", "method": "log", "params": {"level": "info", "message": "test"}},
        ]
        responses = await handler.handle_batch(requests)
        assert len(responses) == 1  # Only the non-notification gets a response
        assert responses[0].result == 3

    @pytest.mark.asyncio
    async def test_sync_handler(self):
        """Test that sync handlers work too."""
        h = JSONRPCHandler()

        @h.method("sync_echo")
        def sync_echo(msg: str) -> str:  # Not async
            return msg

        request = {
            "jsonrpc": "2.0",
            "method": "sync_echo",
            "params": {"msg": "hello"},
            "id": 1,
        }
        response = await h.handle(request)
        assert response.result == "hello"


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_handler_raises_exception(self):
        h = JSONRPCHandler()

        @h.method("fail")
        async def fail() -> str:
            raise RuntimeError("Something went wrong")

        request = {"jsonrpc": "2.0", "method": "fail", "params": {}, "id": 1}
        response = await h.handle(request)
        assert response.is_error
        assert response.error_obj["code"] == INTERNAL_ERROR
        assert "Something went wrong" in response.error_obj["message"]

    @pytest.mark.asyncio
    async def test_handler_raises_jsonrpc_error(self):
        h = JSONRPCHandler()

        @h.method("custom_error")
        async def custom_error() -> str:
            raise InvalidParams("Custom validation error")

        request = {"jsonrpc": "2.0", "method": "custom_error", "params": {}, "id": 1}
        response = await h.handle(request)
        assert response.is_error
        assert response.error_obj["code"] == INVALID_PARAMS
        assert response.error_obj["message"] == "Custom validation error"

    def test_request_string_id(self):
        req = Request(method="test", id="abc-123")
        assert req.id == "abc-123"
        d = req.to_dict()
        assert d["id"] == "abc-123"

    def test_response_null_result(self):
        resp = Response.success(id=1, result=None)
        d = resp.to_dict()
        assert d["result"] is None
        assert "error" not in d
