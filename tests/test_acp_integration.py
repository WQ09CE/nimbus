"""Integration tests for Nimbus ACP module.

This module tests the end-to-end integration of ACP components:
- NimbusACPAgent protocol handling
- ACPEventConverter workflow conversion
- ACPSessionManager lifecycle
- ACPPermissionHandler flow
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# Test imports
from nimbus.acp import (
    ACPConfig,
    NimbusACPAgent,
    ACPSessionManager,
    ACPEventConverter,
    ACPPermissionHandler,
)
from nimbus.acp.jsonrpc import Request, Response


class TestACPAgentIntegration:
    """Integration tests for NimbusACPAgent."""

    @pytest.fixture
    def agent(self):
        """Create a fresh agent instance."""
        config = ACPConfig(cwd="/tmp/test")
        return NimbusACPAgent(config)

    @pytest.mark.asyncio
    async def test_initialize_protocol(self, agent):
        """Test protocol initialization."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {"fs": {"readTextFile": True}},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        }
        response = await agent.handle_request(request)

        assert response is not None
        assert "result" in response
        assert response["result"]["protocolVersion"] == 1
        assert "agentCapabilities" in response["result"]

    @pytest.mark.asyncio
    async def test_initialize_protocol_minimal(self, agent):
        """Test protocol initialization with minimal params."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        }
        response = await agent.handle_request(request)

        assert response is not None
        assert "result" in response
        assert response["result"]["protocolVersion"] == 1

    @pytest.mark.asyncio
    async def test_initialize_rejects_old_protocol(self, agent):
        """Test that old protocol versions are rejected."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 0,
                "clientCapabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        }
        response = await agent.handle_request(request)

        assert response is not None
        assert "error" in response
        # Invalid params error
        assert response["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_session_new(self, agent):
        """Test session creation."""
        # First initialize
        await agent.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        })

        # Then create session
        response = await agent.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": "/tmp/test", "mcpServers": []}
        })

        assert response is not None
        assert "result" in response
        assert "sessionId" in response["result"]

    @pytest.mark.asyncio
    async def test_session_new_with_mcp_servers(self, agent):
        """Test session creation with MCP servers."""
        # Initialize first
        await agent.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"}
            }
        })

        mcp_servers = [
            {"name": "test-server", "command": "npx", "args": ["-y", "test-mcp"]}
        ]
        response = await agent.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "session/new",
            "params": {"cwd": "/tmp/test", "mcpServers": mcp_servers}
        })

        assert response is not None
        assert "result" in response
        assert "sessionId" in response["result"]

    @pytest.mark.asyncio
    async def test_unknown_method_returns_error(self, agent):
        """Test that unknown methods return proper error."""
        response = await agent.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "unknown/method",
            "params": {}
        })

        assert response is not None
        assert "error" in response
        # Method not found error
        assert response["error"]["code"] == -32601


class TestACPEventConversion:
    """Test event conversion end-to-end."""

    def test_full_workflow_events(self):
        """Test converting a full workflow sequence."""
        converter = ACPEventConverter()

        # Simulate workflow: planning -> task_start -> task_done -> complete
        events = [
            {"event": "planning", "data": {"message": "Analyzing request..."}},
            {"event": "task_start", "data": {
                "task_id": "t1",
                "name": "Read file",
                "tool": "Read",
                "input": {"path": "/test.py"}
            }},
            {"event": "task_done", "data": {"task_id": "t1", "result": "file content"}},
            {"event": "content.delta", "data": {"text": "Done!"}},
        ]

        converted = [converter.convert(e) for e in events]

        assert converted[0]["sessionUpdate"] == "agent_thought_chunk"
        assert converted[1]["sessionUpdate"] == "tool_call"
        assert converted[1]["status"] == "pending"
        assert converted[2]["sessionUpdate"] == "tool_call_update"
        assert converted[2]["status"] == "completed"
        assert converted[3]["sessionUpdate"] == "agent_message_chunk"

    def test_dag_workflow_events(self):
        """Test converting DAG-based workflow events."""
        converter = ACPEventConverter()

        events = [
            {"event": "dag_created", "data": {
                "nodes": [
                    {"id": "n1", "name": "Read config", "status": "pending"},
                    {"id": "n2", "name": "Process data", "status": "pending"},
                ]
            }},
            {"event": "task_start", "data": {
                "task_id": "t1",
                "name": "Read config",
                "tool": "Read",
                "input": {"file_path": "/config.json"}
            }},
            {"event": "task_done", "data": {
                "task_id": "t1",
                "tool": "Read",
                "result": '{"key": "value"}',
                "input": {"file_path": "/config.json"}
            }},
        ]

        converted = [converter.convert(e) for e in events]

        # DAG created should produce plan
        assert converted[0]["sessionUpdate"] == "plan"
        assert len(converted[0]["entries"]) == 2

        # Task start should produce tool_call with location
        assert converted[1]["sessionUpdate"] == "tool_call"
        assert len(converted[1]["locations"]) == 1
        assert converted[1]["locations"][0]["path"] == "/config.json"

        # Task done should produce tool_call_update
        assert converted[2]["sessionUpdate"] == "tool_call_update"
        assert converted[2]["status"] == "completed"

    def test_error_workflow_events(self):
        """Test converting error workflow events."""
        converter = ACPEventConverter()

        events = [
            {"event": "task_start", "data": {
                "task_id": "t1",
                "name": "Read file",
                "tool": "Read",
                "input": {"file_path": "/nonexistent.txt"}
            }},
            {"event": "task_failed", "data": {
                "task_id": "t1",
                "error": "File not found: /nonexistent.txt"
            }},
        ]

        converted = [converter.convert(e) for e in events]

        assert converted[0]["sessionUpdate"] == "tool_call"
        assert converted[0]["status"] == "pending"

        assert converted[1]["sessionUpdate"] == "tool_call_update"
        assert converted[1]["status"] == "failed"
        assert "File not found" in converted[1]["rawOutput"]["error"]


class TestACPSessionManagement:
    """Test session management."""

    def test_session_lifecycle(self):
        """Test complete session lifecycle."""
        manager = ACPSessionManager()

        # Create
        session = manager.create_session("/tmp/test", [])
        assert session.id.startswith("acp-sess-")
        assert session.nimbus_session_id.startswith("nimbus-")

        # Get
        retrieved = manager.get_session(session.id)
        assert retrieved is not None
        assert retrieved.cwd == "/tmp/test"

        # Update state
        manager.set_busy(session.id, True)
        assert manager.get_session(session.id).is_busy

        # Delete
        assert manager.delete_session(session.id)
        assert manager.get_session(session.id) is None

    def test_session_with_mcp_servers(self):
        """Test session creation with MCP servers."""
        manager = ACPSessionManager()
        mcp_servers = [
            {"name": "server1", "command": "npx", "args": ["-y", "mcp-server"]}
        ]

        session = manager.create_session("/tmp/test", mcp_servers)
        assert len(session.mcp_servers) == 1
        assert session.mcp_servers[0]["name"] == "server1"

    def test_session_nimbus_id_lookup(self):
        """Test getting session by Nimbus ID."""
        manager = ACPSessionManager()
        session = manager.create_session("/tmp/test", [])

        # Lookup by nimbus ID
        found = manager.get_session_by_nimbus_id(session.nimbus_session_id)
        assert found is not None
        assert found.id == session.id

        # Non-existent nimbus ID
        not_found = manager.get_session_by_nimbus_id("nonexistent")
        assert not_found is None

    def test_session_busy_state(self):
        """Test session busy state management."""
        manager = ACPSessionManager()
        session = manager.create_session("/tmp/test", [])

        # Initially not busy
        assert not session.is_busy

        # Set busy
        manager.set_busy(session.id, True)
        assert manager.get_session(session.id).is_busy

        # Clear busy
        manager.set_busy(session.id, False)
        assert not manager.get_session(session.id).is_busy

    def test_session_cancel_request(self):
        """Test session cancellation request."""
        manager = ACPSessionManager()
        session = manager.create_session("/tmp/test", [])

        # Create a mock task
        async def mock_task():
            await asyncio.sleep(10)

        loop = asyncio.new_event_loop()
        task = loop.create_task(mock_task())

        # Set busy with task
        manager.set_busy(session.id, True, task)

        # Request cancel
        assert manager.request_cancel(session.id)
        assert manager.is_cancel_requested(session.id)

        # Clean up - properly cancel and await the task
        task.cancel()
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        loop.close()

    def test_session_model_mode_updates(self):
        """Test model and mode updates."""
        manager = ACPSessionManager()
        session = manager.create_session("/tmp/test", [])

        # Update model
        assert manager.update_model(session.id, "claude-3-opus")
        assert manager.get_session(session.id).model_id == "claude-3-opus"

        # Update mode
        assert manager.update_mode(session.id, "code")
        assert manager.get_session(session.id).mode_id == "code"

        # Non-existent session
        assert not manager.update_model("nonexistent", "model")
        assert not manager.update_mode("nonexistent", "mode")

    def test_list_sessions(self):
        """Test listing all sessions."""
        manager = ACPSessionManager()

        # Create multiple sessions
        s1 = manager.create_session("/tmp/test1", [])
        s2 = manager.create_session("/tmp/test2", [])
        s3 = manager.create_session("/tmp/test3", [])

        sessions = manager.list_sessions()
        assert len(sessions) == 3

        session_ids = {s.id for s in sessions}
        assert s1.id in session_ids
        assert s2.id in session_ids
        assert s3.id in session_ids


class TestACPPermissionFlow:
    """Test permission handling."""

    @pytest.mark.asyncio
    async def test_permission_always_allow(self):
        """Test always-allow permission."""
        handler = ACPPermissionHandler()
        handler.add_always_allowed("Read")

        allowed, option_id = await handler.request_permission(
            "sess-1", "Read", {"path": "/test.py"}
        )

        assert allowed
        assert option_id == "allow_always"

    @pytest.mark.asyncio
    async def test_permission_always_deny(self):
        """Test always-deny permission."""
        handler = ACPPermissionHandler()
        handler.add_always_denied("Bash")

        allowed, option_id = await handler.request_permission(
            "sess-1", "Bash", {"command": "rm -rf /"}
        )

        assert not allowed
        assert option_id == "reject_always"

    def test_permission_rules_management(self):
        """Test managing permission rules."""
        handler = ACPPermissionHandler()

        # Add always allowed
        handler.add_always_allowed("Read")
        assert handler.is_always_allowed("Read")
        assert not handler.is_always_denied("Read")

        # Add always denied
        handler.add_always_denied("Bash")
        assert handler.is_always_denied("Bash")
        assert not handler.is_always_allowed("Bash")

        # Switching from allowed to denied
        handler.add_always_denied("Read")
        assert handler.is_always_denied("Read")
        assert not handler.is_always_allowed("Read")

        # Clear rules
        handler.clear_always_rules()
        assert not handler.is_always_allowed("Read")
        assert not handler.is_always_denied("Bash")

    @pytest.mark.asyncio
    async def test_permission_with_sender_callback(self):
        """Test permission request with sender callback."""
        handler = ACPPermissionHandler()

        # Mock sender that approves
        async def mock_sender(session_id, tool_call, options):
            return {"outcome": "selected", "optionId": "allow_once"}

        handler.set_request_sender(mock_sender)

        allowed, option_id = await handler.request_permission(
            "sess-1", "Write", {"file_path": "/test.py", "content": "code"}
        )

        assert allowed
        assert option_id == "allow_once"

    @pytest.mark.asyncio
    async def test_permission_with_rejection(self):
        """Test permission request with rejection."""
        handler = ACPPermissionHandler()

        # Mock sender that rejects
        async def mock_sender(session_id, tool_call, options):
            return {"outcome": "selected", "optionId": "reject_once"}

        handler.set_request_sender(mock_sender)

        allowed, option_id = await handler.request_permission(
            "sess-1", "Bash", {"command": "dangerous"}
        )

        assert not allowed
        assert option_id == "reject_once"

    @pytest.mark.asyncio
    async def test_permission_with_cancellation(self):
        """Test permission request with cancellation."""
        handler = ACPPermissionHandler()

        # Mock sender that cancels
        async def mock_sender(session_id, tool_call, options):
            return {"outcome": "cancelled"}

        handler.set_request_sender(mock_sender)

        allowed, option_id = await handler.request_permission(
            "sess-1", "Edit", {"file_path": "/test.py"}
        )

        assert not allowed
        assert option_id == "cancelled"

    @pytest.mark.asyncio
    async def test_permission_allow_always_updates_rules(self):
        """Test that allow_always updates future permissions."""
        handler = ACPPermissionHandler()

        # Mock sender that returns allow_always
        async def mock_sender(session_id, tool_call, options):
            return {"outcome": "selected", "optionId": "allow_always"}

        handler.set_request_sender(mock_sender)

        # First request goes through sender
        allowed, option_id = await handler.request_permission(
            "sess-1", "Grep", {"pattern": "TODO"}
        )
        assert allowed
        assert option_id == "allow_always"

        # Rule should be set now
        assert handler.is_always_allowed("Grep")

        # Second request should be auto-approved without sender
        handler.set_request_sender(None)  # Remove sender
        allowed, option_id = await handler.request_permission(
            "sess-1", "Grep", {"pattern": "FIXME"}
        )
        assert allowed
        assert option_id == "allow_always"

    @pytest.mark.asyncio
    async def test_permission_no_sender_raises_error(self):
        """Test that missing sender raises RuntimeError."""
        handler = ACPPermissionHandler()

        with pytest.raises(RuntimeError, match="No permission request sender"):
            await handler.request_permission(
                "sess-1", "Unknown", {}
            )

    def test_permission_tool_kind_mapping(self):
        """Test tool kind mapping."""
        handler = ACPPermissionHandler()

        assert handler._get_tool_kind("Read") == "read"
        assert handler._get_tool_kind("Grep") == "search"
        assert handler._get_tool_kind("Bash") == "execute"
        assert handler._get_tool_kind("Edit") == "edit"
        assert handler._get_tool_kind("Unknown") == "other"


class TestACPImports:
    """Test that all public APIs are properly exported."""

    def test_main_imports(self):
        """Test main module imports."""
        from nimbus.acp import (
            # Server
            ACPServer,
            run_server,
            run_server_async,
            # Agent
            ACPConfig,
            NimbusACPAdapter,
            NimbusSession,
            NimbusACPAgent,
            create_acp_agent,
            PROTOCOL_VERSION,
            # Events
            ACPEventConverter,
            get_converter,
            convert_event,
            # Session
            ACPSessionState,
            ACPSessionManager,
            get_session_manager,
            # Permission
            PendingPermission,
            ACPPermissionHandler,
            get_permission_handler,
            reset_permission_handler,
            # JSON-RPC
            Request,
            Response,
            JSONRPCHandler,
            JSONRPCServer,
            JSONRPCClient,
        )

        # Verify they are the expected types
        assert PROTOCOL_VERSION == 1
        assert callable(create_acp_agent)
        assert callable(get_converter)
        assert callable(convert_event)
        assert callable(get_session_manager)
        assert callable(get_permission_handler)

    def test_jsonrpc_error_codes(self):
        """Test JSON-RPC error codes are exported."""
        from nimbus.acp import (
            PARSE_ERROR,
            INVALID_REQUEST,
            METHOD_NOT_FOUND,
            INVALID_PARAMS,
            INTERNAL_ERROR,
        )

        assert PARSE_ERROR == -32700
        assert INVALID_REQUEST == -32600
        assert METHOD_NOT_FOUND == -32601
        assert INVALID_PARAMS == -32602
        assert INTERNAL_ERROR == -32603

    def test_jsonrpc_exceptions(self):
        """Test JSON-RPC exceptions are exported."""
        from nimbus.acp import (
            JSONRPCError,
            ParseError,
            InvalidRequest,
            MethodNotFound,
            InvalidParams,
            InternalError,
        )

        # Verify they are exception classes
        assert issubclass(ParseError, JSONRPCError)
        assert issubclass(InvalidRequest, JSONRPCError)
        assert issubclass(MethodNotFound, JSONRPCError)
        assert issubclass(InvalidParams, JSONRPCError)
        assert issubclass(InternalError, JSONRPCError)

    def test_type_exports(self):
        """Test type definitions are exported."""
        from nimbus.acp import (
            # Capabilities
            ClientCapabilities,
            AgentCapabilities,
            # Content types
            TextContent,
            ImageContent,
            ContentBlock,
            # Session updates
            SessionUpdate,
            AgentMessageChunk,
            AgentThoughtChunk,
            # Tool calls
            ToolCall,
            ToolCallUpdate,
            ToolKind,
            ToolCallStatus,
            # Plan
            Plan,
            PlanEntry,
        )

        # These are TypedDict types, verify they exist
        assert TextContent is not None
        assert ToolCall is not None
        assert Plan is not None
