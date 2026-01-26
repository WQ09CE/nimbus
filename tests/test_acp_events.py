"""Tests for Nimbus ACP Event Converter.

This module tests the conversion of Nimbus SSE events to ACP SessionUpdate format.
"""

import pytest

from nimbus.acp.events import ACPEventConverter, convert_event, get_converter


class TestACPEventConverter:
    """Tests for ACPEventConverter class."""

    @pytest.fixture
    def converter(self) -> ACPEventConverter:
        """Create a fresh converter instance."""
        return ACPEventConverter()

    # -------------------------------------------------------------------------
    # Basic conversion tests
    # -------------------------------------------------------------------------

    def test_convert_unknown_event_returns_none(self, converter: ACPEventConverter):
        """Unknown event types should return None."""
        event = {"event": "unknown_event", "data": {"foo": "bar"}}
        result = converter.convert(event)
        assert result is None

    def test_convert_missing_event_type_returns_none(self, converter: ACPEventConverter):
        """Events without event type should return None."""
        event = {"data": {"foo": "bar"}}
        result = converter.convert(event)
        assert result is None

    def test_convert_empty_event_returns_none(self, converter: ACPEventConverter):
        """Empty events should return None."""
        event = {}
        result = converter.convert(event)
        assert result is None

    # -------------------------------------------------------------------------
    # Planning events
    # -------------------------------------------------------------------------

    def test_convert_planning_event(self, converter: ACPEventConverter):
        """planning event should convert to agent_thought_chunk."""
        event = {
            "event": "planning",
            "data": {"message": "Creating execution plan..."}
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "agent_thought_chunk"
        assert result["content"]["type"] == "text"
        assert result["content"]["text"] == "Creating execution plan..."

    def test_convert_status_event(self, converter: ACPEventConverter):
        """event.status should convert to agent_thought_chunk."""
        event = {
            "event": "event.status",
            "data": {
                "status": "executing",
                "message": "Running tasks...",
                "dagID": "dag_123",
                "totalTasks": 5
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "agent_thought_chunk"
        assert "Running tasks..." in result["content"]["text"]
        assert "[DAG: dag_123]" in result["content"]["text"]
        assert "[Tasks: 5]" in result["content"]["text"]

    def test_convert_status_event_minimal(self, converter: ACPEventConverter):
        """event.status with minimal data should still convert."""
        event = {
            "event": "event.status",
            "data": {"status": "planning"}
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "agent_thought_chunk"
        assert "planning" in result["content"]["text"]

    # -------------------------------------------------------------------------
    # DAG events
    # -------------------------------------------------------------------------

    def test_convert_dag_created(self, converter: ACPEventConverter):
        """dag_created should convert to plan with entries."""
        event = {
            "event": "dag_created",
            "data": {
                "nodes": [
                    {"id": "node_1", "name": "Read config file", "status": "pending"},
                    {"id": "node_2", "name": "Parse content", "status": "pending"},
                    {"id": "node_3", "name": "Apply changes", "status": "pending"},
                ]
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "plan"
        assert len(result["entries"]) == 3

        # Check first entry
        assert result["entries"][0]["content"] == "Read config file"
        assert result["entries"][0]["status"] == "pending"
        assert result["entries"][0]["priority"] == "medium"

    def test_convert_dag_created_with_status_mapping(self, converter: ACPEventConverter):
        """dag_created should map node statuses correctly."""
        event = {
            "event": "dag_created",
            "data": {
                "nodes": [
                    {"name": "Task 1", "status": "completed"},
                    {"name": "Task 2", "status": "running"},
                    {"name": "Task 3", "status": "failed"},
                    {"name": "Task 4", "status": "cancelled"},
                ]
            }
        }
        result = converter.convert(event)

        assert result["entries"][0]["status"] == "completed"
        assert result["entries"][1]["status"] == "in_progress"
        assert result["entries"][2]["status"] == "completed"  # failed -> completed
        assert result["entries"][3]["status"] == "completed"  # cancelled -> completed

    def test_convert_dag_created_empty_nodes(self, converter: ACPEventConverter):
        """dag_created with no nodes should return empty entries."""
        event = {"event": "dag_created", "data": {"nodes": []}}
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "plan"
        assert result["entries"] == []

    # -------------------------------------------------------------------------
    # Task start events
    # -------------------------------------------------------------------------

    def test_convert_task_start(self, converter: ACPEventConverter):
        """task_start should convert to tool_call with pending status."""
        event = {
            "event": "task_start",
            "data": {
                "task_id": "task_001",
                "name": "Read config",
                "tool": "Read",
                "input": {"file_path": "/path/to/config.json"}
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "tool_call"
        assert result["toolCallId"] == "task_001"
        assert result["title"] == "Read config"
        assert result["kind"] == "read"
        assert result["status"] == "pending"
        assert result["rawInput"] == {"file_path": "/path/to/config.json"}
        assert len(result["locations"]) == 1
        assert result["locations"][0]["path"] == "/path/to/config.json"

    def test_convert_tool_start(self, converter: ACPEventConverter):
        """tool.start should convert to tool_call (OpenCode format)."""
        event = {
            "event": "tool.start",
            "data": {
                "taskID": "task_002",
                "name": "Grep",
                "input": {"path": "/src", "pattern": "TODO"}
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "tool_call"
        assert result["toolCallId"] == "task_002"
        assert result["title"] == "Grep"
        assert result["kind"] == "search"
        assert result["status"] == "pending"

    # -------------------------------------------------------------------------
    # Task completion events
    # -------------------------------------------------------------------------

    def test_convert_task_done(self, converter: ACPEventConverter):
        """task_done should convert to tool_call_update with completed status."""
        event = {
            "event": "task_done",
            "data": {
                "task_id": "task_001",
                "tool": "Read",
                "result": "file content here",
                "input": {"file_path": "/path/to/file"}
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "tool_call_update"
        assert result["toolCallId"] == "task_001"
        assert result["status"] == "completed"
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "content"
        assert result["content"][0]["content"]["text"] == "file content here"

    def test_convert_task_done_with_edit(self, converter: ACPEventConverter):
        """task_done for Edit tool should produce diff content."""
        event = {
            "event": "task_done",
            "data": {
                "task_id": "task_003",
                "tool": "Edit",
                "input": {
                    "file_path": "/path/to/file.py",
                    "old_string": "def foo():",
                    "new_string": "def bar():"
                },
                "result": {"success": True}
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "tool_call_update"
        assert result["status"] == "completed"
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "diff"
        assert result["content"][0]["path"] == "/path/to/file.py"
        assert result["content"][0]["oldText"] == "def foo():"
        assert result["content"][0]["newText"] == "def bar():"

    def test_convert_tool_done(self, converter: ACPEventConverter):
        """tool.done should convert to tool_call_update (OpenCode format)."""
        event = {
            "event": "tool.done",
            "data": {
                "taskID": "task_004",
                "result": "Search results here",
                "durationMs": 150
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "tool_call_update"
        assert result["toolCallId"] == "task_004"
        assert result["status"] == "completed"

    # -------------------------------------------------------------------------
    # Task failure events
    # -------------------------------------------------------------------------

    def test_convert_task_failed(self, converter: ACPEventConverter):
        """task_failed should convert to tool_call_update with failed status."""
        event = {
            "event": "task_failed",
            "data": {
                "task_id": "task_001",
                "error": "File not found"
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "tool_call_update"
        assert result["toolCallId"] == "task_001"
        assert result["status"] == "failed"
        assert result["rawOutput"]["error"] == "File not found"

    def test_convert_tool_error(self, converter: ACPEventConverter):
        """tool.error should convert to tool_call_update (OpenCode format)."""
        event = {
            "event": "tool.error",
            "data": {
                "taskID": "task_005",
                "error": "Permission denied"
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "tool_call_update"
        assert result["toolCallId"] == "task_005"
        assert result["status"] == "failed"
        assert result["rawOutput"]["error"] == "Permission denied"

    # -------------------------------------------------------------------------
    # Content streaming events
    # -------------------------------------------------------------------------

    def test_convert_content_delta(self, converter: ACPEventConverter):
        """content.delta should convert to agent_message_chunk."""
        event = {
            "event": "content.delta",
            "data": {"text": "I'll help you with that..."}
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "agent_message_chunk"
        assert result["content"]["type"] == "text"
        assert result["content"]["text"] == "I'll help you with that..."

    def test_convert_content_delta_empty(self, converter: ACPEventConverter):
        """content.delta with empty text should still convert."""
        event = {"event": "content.delta", "data": {}}
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "agent_message_chunk"
        assert result["content"]["text"] == ""

    # -------------------------------------------------------------------------
    # Permission request events
    # -------------------------------------------------------------------------

    def test_convert_permission_request(self, converter: ACPEventConverter):
        """permission_request should convert to tool_call."""
        event = {
            "event": "permission_request",
            "data": {
                "request_id": "perm_001",
                "tool": "Bash",
                "args": {"command": "rm -rf /tmp/test"}
            }
        }
        result = converter.convert(event)

        assert result is not None
        assert result["sessionUpdate"] == "tool_call"
        assert result["toolCallId"] == "perm_001"
        assert result["title"] == "Permission: Bash"
        assert result["kind"] == "execute"
        assert result["status"] == "pending"
        assert result["rawInput"]["command"] == "rm -rf /tmp/test"

    # -------------------------------------------------------------------------
    # Tool kind mapping
    # -------------------------------------------------------------------------

    def test_tool_kind_mapping(self, converter: ACPEventConverter):
        """Tool names should map to correct ACP ToolKind."""
        mappings = [
            ("Read", "read"),
            ("read_file", "read"),
            ("Grep", "search"),
            ("grep_content", "search"),
            ("Glob", "search"),
            ("glob_files", "search"),
            ("Bash", "execute"),
            ("bash", "execute"),
            ("Edit", "edit"),
            ("edit_file", "edit"),
            ("Write", "edit"),
            ("write_file", "edit"),
            ("WebSearch", "fetch"),
            ("web_search", "fetch"),
            ("WebFetch", "fetch"),
            ("unknown_tool", "other"),
        ]

        for tool_name, expected_kind in mappings:
            assert converter._map_tool_kind(tool_name) == expected_kind

    # -------------------------------------------------------------------------
    # Module-level functions
    # -------------------------------------------------------------------------

    def test_get_converter_singleton(self):
        """get_converter should return same instance."""
        c1 = get_converter()
        c2 = get_converter()
        assert c1 is c2

    def test_convert_event_function(self):
        """convert_event convenience function should work."""
        event = {
            "event": "content.delta",
            "data": {"text": "Hello"}
        }
        result = convert_event(event)

        assert result is not None
        assert result["sessionUpdate"] == "agent_message_chunk"
        assert result["content"]["text"] == "Hello"


class TestLocationExtraction:
    """Tests for location extraction from task inputs."""

    @pytest.fixture
    def converter(self) -> ACPEventConverter:
        return ACPEventConverter()

    def test_extract_path_from_file_path(self, converter: ACPEventConverter):
        """Should extract path from file_path input."""
        task = {"input": {"file_path": "/path/to/file.txt"}}
        locations = converter._extract_locations(task)

        assert len(locations) == 1
        assert locations[0]["path"] == "/path/to/file.txt"

    def test_extract_path_from_path(self, converter: ACPEventConverter):
        """Should extract path from path input."""
        task = {"input": {"path": "/another/path"}}
        locations = converter._extract_locations(task)

        assert len(locations) == 1
        assert locations[0]["path"] == "/another/path"

    def test_extract_path_with_line(self, converter: ACPEventConverter):
        """Should include line number if available."""
        task = {"input": {"path": "/file.txt", "line": 42}}
        locations = converter._extract_locations(task)

        assert len(locations) == 1
        assert locations[0]["path"] == "/file.txt"
        assert locations[0]["line"] == 42

    def test_extract_no_path(self, converter: ACPEventConverter):
        """Should return empty list if no path found."""
        task = {"input": {"pattern": "TODO"}}
        locations = converter._extract_locations(task)

        assert locations == []

    def test_extract_empty_input(self, converter: ACPEventConverter):
        """Should handle empty input."""
        task = {"input": {}}
        locations = converter._extract_locations(task)
        assert locations == []

        task = {}
        locations = converter._extract_locations(task)
        assert locations == []


class TestToolContentBuilding:
    """Tests for building tool call content from results."""

    @pytest.fixture
    def converter(self) -> ACPEventConverter:
        return ACPEventConverter()

    def test_build_content_from_string_result(self, converter: ACPEventConverter):
        """String result should produce text content."""
        task = {"result": "File content here", "tool": "Read"}
        content = converter._build_tool_content(task)

        assert len(content) == 1
        assert content[0]["type"] == "content"
        assert content[0]["content"]["type"] == "text"
        assert content[0]["content"]["text"] == "File content here"

    def test_build_content_from_dict_result(self, converter: ACPEventConverter):
        """Dict result with content key should produce text content."""
        task = {"result": {"content": "Some content"}, "tool": "Read"}
        content = converter._build_tool_content(task)

        assert len(content) == 1
        assert content[0]["content"]["text"] == "Some content"

    def test_build_content_from_dict_with_text(self, converter: ACPEventConverter):
        """Dict result with text key should produce text content."""
        task = {"result": {"text": "Some text"}, "tool": "Grep"}
        content = converter._build_tool_content(task)

        assert len(content) == 1
        assert content[0]["content"]["text"] == "Some text"

    def test_build_diff_content_for_edit(self, converter: ACPEventConverter):
        """Edit tool should produce diff content."""
        task = {
            "tool": "Edit",
            "input": {
                "file_path": "/file.py",
                "old_string": "old",
                "new_string": "new"
            },
            "result": {}
        }
        content = converter._build_tool_content(task)

        assert len(content) == 1
        assert content[0]["type"] == "diff"
        assert content[0]["path"] == "/file.py"
        assert content[0]["oldText"] == "old"
        assert content[0]["newText"] == "new"

    def test_build_empty_content(self, converter: ACPEventConverter):
        """Empty result should produce empty content."""
        task = {"result": {}, "tool": "Unknown"}
        content = converter._build_tool_content(task)

        assert content == []
