"""Tests for Nimbus Server API Layer.

This module tests:
- Pydantic models validation
- SSE Hub functionality
- Permission Manager functionality
- API routes (basic structure)
"""


# Server modules use relative imports that work within the package
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
        assert session.memory_type == "tiered"
        assert session.planner_type == "dag"

    def test_session_create_custom(self):
        """Test SessionCreate with custom values."""
        from nimbus.server.models import SessionCreate

        session = SessionCreate(
            name="test-session",
            workspace_path="/tmp/test",
            memory_type="simple",
            planner_type="simple",
        )
        assert session.name == "test-session"
        assert session.workspace_path == "/tmp/test"
        assert session.memory_type == "simple"
        assert session.planner_type == "simple"

    def test_session_response(self):
        """Test SessionResponse model."""
        from nimbus.server.models import SessionResponse, SessionStatus

        response = SessionResponse(
            id="sess_12345",
            created_at=datetime.now(),
            status=SessionStatus.ACTIVE,
            memory_type="tiered",
            planner_type="dag",
        )
        assert response.id == "sess_12345"
        assert response.status == SessionStatus.ACTIVE

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

    def test_dag_response(self):
        """Test DAGResponse model."""
        from nimbus.server.models import (
            DAGResponse,
            DAGStatsResponse,
            TaskNodeResponse,
            TaskStatusEnum,
        )

        stats = DAGStatsResponse(
            total=5,
            completed=3,
            running=1,
            pending=1,
            failed=0,
            skipped=0,
        )

        node = TaskNodeResponse(
            id="task_1",
            skill="synthesize",
            status=TaskStatusEnum.COMPLETED,
        )

        dag = DAGResponse(
            id="dag_12345",
            goal="Test goal",
            status="running",
            created_at=datetime.now(),
            nodes=[node],
            stats=stats,
        )
        assert dag.id == "dag_12345"
        assert len(dag.nodes) == 1

    def test_skill_response(self):
        """Test SkillResponse model."""
        from nimbus.server.models import SkillParameter, SkillResponse

        skill = SkillResponse(
            name="synthesize",
            description="Chat skill",
            source="builtin",
            parameters=[
                SkillParameter(
                    name="message",
                    type="string",
                    description="Message to send",
                    required=True,
                )
            ],
        )
        assert skill.name == "synthesize"
        assert len(skill.parameters) == 1


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
    """Test SSE event builder helper."""

    def test_connected_event(self):
        """Test connected event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.connected("sess_123")
        assert event.event == SSEHub.EVENT_CONNECTED
        assert event.data["session_id"] == "sess_123"

    def test_planning_event(self):
        """Test planning event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.planning("analyzing")
        assert event.event == SSEHub.EVENT_PLANNING
        assert event.data["status"] == "analyzing"

    def test_dag_created_event(self):
        """Test dag_created event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.dag_created(
            dag_id="dag_123",
            goal="Test goal",
            total_tasks=5,
        )
        assert event.event == SSEHub.EVENT_DAG_CREATED
        assert event.data["dag_id"] == "dag_123"
        assert event.data["total_tasks"] == 5

    def test_task_done_event(self):
        """Test task_done event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.task_done(
            task_id="task_1",
            result="Success",
            duration_ms=150,
        )
        assert event.event == SSEHub.EVENT_TASK_DONE
        assert event.data["task_id"] == "task_1"
        assert event.data["duration_ms"] == 150

    def test_permission_request_event(self):
        """Test permission_request event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.permission_request(
            request_id="perm_123",
            tool="bash",
            args={"command": "ls -la"},
        )
        assert event.event == SSEHub.EVENT_PERMISSION_REQUEST
        assert event.data["request_id"] == "perm_123"
        assert event.data["tool"] == "bash"

    def test_error_event(self):
        """Test error event creation."""
        from nimbus.server.sse import SSEEventBuilder, SSEHub

        event = SSEEventBuilder.error("execution_error", "Task failed")
        assert event.event == SSEHub.EVENT_ERROR
        assert event.data["code"] == "execution_error"
        assert event.data["message"] == "Task failed"


class TestMiddleware:
    """Test middleware helpers."""

    def test_create_error_response(self):
        """Test error response creation."""
        from nimbus.server.middleware import create_error_response

        response = create_error_response(
            status_code=404,
            code="not_found",
            message="Resource not found",
            details={"id": "123"},
        )

        assert response["code"] == "not_found"
        assert response["message"] == "Resource not found"
        assert response["details"]["id"] == "123"
        assert "timestamp" in response


class TestAPIRouter:
    """Test API router setup."""

    def test_router_creation(self):
        """Test that API router can be created."""
        from nimbus.server.api import router

        assert router is not None

        # Check some routes exist
        route_paths = [r.path for r in router.routes]
        assert "/health" in route_paths
        assert "/config" in route_paths
        assert "/sessions" in route_paths
        assert "/skills" in route_paths

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
