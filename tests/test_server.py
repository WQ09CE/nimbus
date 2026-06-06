"""Tests for Nimbus Server API Layer (nimbus-next).

This module tests:
- Pydantic models validation
- SSE Hub functionality
- SSE Event Builder
- Permission Manager functionality
- API routes (basic structure)
"""

import sys
from datetime import datetime

import pytest

sys.path.insert(0, '.')


class TestServerModels:
    """Test Pydantic models."""

    def test_session_create_defaults(self):
        """Test SessionCreate with default values."""
        from nimbus.server.models import SessionCreate

        session = SessionCreate()
        assert session.name is None
        assert session.workspace_path is None
        assert session.llm_config is None
        assert session.agent_mode == "standard"
        assert session.skills is None
        assert session.plugins is None

    def test_session_create_custom(self):
        """Test SessionCreate with custom values."""
        from nimbus.server.models import SessionCreate

        session = SessionCreate(
            name="test-session",
            workspace_path="/tmp/test",
            llm_config={"provider": "anthropic", "model_id": "claude-sonnet-4-5"},
            agent_mode="dual_agent",
            skills=["goal"],
            plugins=["hello"],
        )
        assert session.name == "test-session"
        assert session.workspace_path == "/tmp/test"
        assert session.llm_config["provider"] == "anthropic"
        assert session.agent_mode == "dual_agent"
        assert session.skills == ["goal"]
        assert session.plugins == ["hello"]

    def test_session_response(self):
        """Test SessionResponse model."""
        from nimbus.server.models import SessionResponse, SessionStatus

        response = SessionResponse(
            id="sess_12345",
            created_at=datetime.now(),
            status=SessionStatus.ACTIVE,
            agent_mode="standard",
        )
        assert response.id == "sess_12345"
        assert response.status == SessionStatus.ACTIVE
        assert response.agent_mode == "standard"
        assert response.skills == []
        assert response.plugins == []

    def test_permission_decision_enum(self):
        """Test PermissionDecision enum values."""
        from nimbus.server.models import PermissionDecision

        assert PermissionDecision.ASK == "ask"
        assert PermissionDecision.ALLOW_ONCE == "allow_once"
        assert PermissionDecision.ALLOW_ALWAYS == "allow_always"
        assert PermissionDecision.DENY == "deny"

    def test_chat_request(self):
        """Test ChatRequest model."""
        from nimbus.server.models import AttachmentCreate, ChatRequest

        request = ChatRequest(
            content="Hello, world!",
            attachments=[
                AttachmentCreate(type="file", path="/tmp/test.txt", name="test.txt")
            ],
        )
        assert request.content == "Hello, world!"
        assert len(request.attachments) == 1
        assert request.attachments[0].type == "file"

    def test_session_detail(self):
        """Test SessionDetail model (replaces v3 DAG tests)."""
        from nimbus.server.models import SessionDetail, SessionStatus

        detail = SessionDetail(
            id="sess_12345",
            created_at=datetime.now(),
            status=SessionStatus.ACTIVE,
            agent_mode="standard",
            workspace_path="/tmp/test",
            memory_stats={"message_count": 10},
        )
        assert detail.id == "sess_12345"
        assert detail.workspace_path == "/tmp/test"
        assert detail.memory_stats["message_count"] == 10

    def test_session_update(self):
        """Test SessionUpdate model."""
        from nimbus.server.models import SessionUpdate

        update = SessionUpdate(
            name="updated-name",
            llm_config={"provider": "google", "model_id": "gemini-3-flash-preview"},
            skills=["goal"],
            plugins=["hello"],
        )
        assert update.name == "updated-name"
        assert update.llm_config["provider"] == "google"
        assert update.skills == ["goal"]
        assert update.plugins == ["hello"]

    def test_sse_event(self):
        """Test SSEEvent model."""
        from nimbus.server.models import SSEEvent

        event = SSEEvent(event="message", data={"content": "Hello"})
        assert event.event == "message"
        assert event.data["content"] == "Hello"

    def test_health_response(self):
        """Test HealthResponse model."""
        from nimbus.server.models import HealthResponse

        health = HealthResponse(status="healthy", version="0.2.0")
        assert health.status == "healthy"
        assert health.version == "0.2.0"


class TestPermissionManager:
    """Test PermissionManager functionality."""

    def test_default_rules(self):
        """Test default permission rules."""
        from nimbus.server.permission import PermissionManager

        manager = PermissionManager()

        # Dangerous tools should default to ASK
        assert manager.get_rule("bash").value == "ask"
        assert manager.get_rule("exec").value == "ask"

        # Safe tools should default to ALLOW_ALWAYS
        assert manager.get_rule("read_file").value == "allow_always"
        assert manager.get_rule("synthesize").value == "allow_always"

    def test_set_rule(self):
        """Test setting permission rules."""
        from nimbus.server.models import PermissionDecision
        from nimbus.server.permission import PermissionManager

        manager = PermissionManager()

        manager.set_rule("custom_tool", PermissionDecision.DENY)
        assert manager.get_rule("custom_tool") == PermissionDecision.DENY

        manager.set_rule("custom_tool", PermissionDecision.ALLOW_ALWAYS)
        assert manager.get_rule("custom_tool") == PermissionDecision.ALLOW_ALWAYS

    def test_get_all_rules(self):
        """Test getting all permission rules."""
        from nimbus.server.permission import PermissionManager

        manager = PermissionManager()
        rules = manager.get_all_rules()

        assert isinstance(rules, dict)
        assert "bash" in rules
        assert "read_file" in rules

    def test_unknown_tool_defaults_to_ask(self):
        """Test that unknown tools default to ASK."""
        from nimbus.server.models import PermissionDecision
        from nimbus.server.permission import PermissionManager

        manager = PermissionManager()

        # Unknown tool should default to ASK
        assert manager.get_rule("unknown_tool") == PermissionDecision.ASK

        # Tool with dangerous keywords should default to ASK
        assert manager.get_rule("dangerous_operation") == PermissionDecision.ASK


class TestSSEHub:
    """Test SSEHub functionality."""

    @pytest.mark.asyncio
    async def test_create_sse_hub(self):
        """Test SSE hub creation."""
        from nimbus.server.sse import SSEHub

        hub = SSEHub(heartbeat_interval=5.0)
        assert hub.get_connection_count() == 0
        assert hub.get_active_sessions() == []

    @pytest.mark.asyncio
    async def test_sse_format(self):
        """Test SSE event formatting."""
        from nimbus.server.sse import SSEHub

        hub = SSEHub()
        formatted = hub._format_sse("test_event", {"key": "value"})

        assert "event: test_event" in formatted
        assert "data:" in formatted
        assert '"key": "value"' in formatted
        assert formatted.endswith("\n\n")


class TestSSEEventBuilder:
    """Test SSE event builder helper (nimbus-next events)."""

    def test_connected_event(self):
        """Test connected event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.connected("sess_123")
        assert event.event == SSEHub.EVENT_CONNECTED
        assert event.data["session_id"] == "sess_123"

    def test_message_start_event(self):
        """Test message_start event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.message_start("msg_123")
        assert event.event == SSEHub.EVENT_MESSAGE_START
        assert event.data["message_id"] == "msg_123"

    def test_message_event(self):
        """Test message event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.message("Hello world")
        assert event.event == SSEHub.EVENT_MESSAGE
        assert event.data["content"] == "Hello world"

    def test_tool_call_event(self):
        """Test tool_call event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.tool_call(
            tool="bash",
            args={"command": "ls -la"},
            action_id="act_123",
        )
        assert event.event == SSEHub.EVENT_TOOL_CALL
        assert event.data["tool"] == "bash"
        assert event.data["args"]["command"] == "ls -la"

    def test_tool_result_event(self):
        """Test tool_result event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.tool_result(
            tool="bash",
            result="output",
            action_id="act_123",
            status="OK",
        )
        assert event.event == SSEHub.EVENT_TOOL_RESULT
        assert event.data["tool"] == "bash"
        assert event.data["status"] == "OK"

    def test_done_event(self):
        """Test done event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.done("OK")
        assert event.event == SSEHub.EVENT_DONE
        assert event.data["status"] == "OK"

    def test_error_event(self):
        """Test error event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.error("execution_error", "Task failed")
        assert event.event == SSEHub.EVENT_ERROR
        assert event.data["code"] == "execution_error"
        assert event.data["message"] == "Task failed"

    def test_heartbeat_event(self):
        """Test heartbeat event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.heartbeat()
        assert event.event == SSEHub.EVENT_HEARTBEAT
        assert "timestamp" in event.data


class TestAPIRouter:
    """Test API router setup."""

    def test_router_creation(self):
        """Test that API router can be created."""
        from nimbus.server.api import router

        assert router is not None

        # Check some routes exist (nimbus-next routes)
        route_paths = [r.path for r in router.routes]
        assert "/health" in route_paths
        assert "/config" in route_paths
        assert "/sessions" in route_paths
        assert "/models" in route_paths
        assert "/skills" in route_paths
        assert "/plugins" in route_paths

    def test_fastapi_app_creation(self):
        """Test FastAPI app can be created with router."""
        from fastapi import FastAPI

        from nimbus.server.api import router

        app = FastAPI()
        app.include_router(router, prefix="/api/v1")

        # Check routes are registered
        all_paths = [r.path for r in app.routes]
        assert "/api/v1/health" in all_paths
        assert "/api/v1/sessions" in all_paths


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
