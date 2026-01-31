"""Nimbus SSE Event to ACP SessionUpdate Converter.

This module converts Nimbus SSE events to ACP session/update format,
enabling integration with ACP-compatible clients.

Nimbus SSE Event Types:
- event.start / event.done - Message processing lifecycle
- event.status / planning - Planning and execution status
- dag_created - DAG plan creation
- tool.start / task_start - Tool/task execution start
- tool.done / task_done - Tool/task execution complete
- tool.error / task_failed - Tool/task execution failed
- content.delta - Streaming content
- permission_request - Permission request for tool execution
"""

from typing import Any

from .types import (
    AgentMessageChunk,
    AgentThoughtChunk,
    Plan,
    PlanEntry,
    TextContent,
    ToolCall,
    ToolCallContentContent,
    ToolCallContentDiff,
    ToolCallContent,
    ToolCallLocation,
    ToolCallUpdate,
    ToolKind,
    ToolCallStatus,
)


class ACPEventConverter:
    """Converts Nimbus SSE events to ACP session/update format.

    This class provides bidirectional mapping between Nimbus SSE event format
    and the ACP SessionUpdate protocol, enabling Nimbus to work with any
    ACP-compatible client.

    Supported Nimbus Events:
        - planning / event.status: Status updates during planning/execution
        - dag_created: DAG plan creation (maps to ACP Plan)
        - task_start / tool.start: Task execution start (maps to ACP ToolCall)
        - task_done / tool.done: Task completion (maps to ACP ToolCallUpdate)
        - task_failed / tool.error: Task failure (maps to ACP ToolCallUpdate)
        - content.delta: Streaming content (maps to ACP AgentMessageChunk)

    Example:
        >>> converter = ACPEventConverter()
        >>> nimbus_event = {"event": "content.delta", "data": {"text": "Hello"}}
        >>> acp_update = converter.convert(nimbus_event)
        >>> print(acp_update)
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello"}}
    """

    # Mapping from Nimbus TaskStatus to ACP plan entry status
    _TASK_STATUS_MAP: dict[str, str] = {
        "pending": "pending",
        "running": "in_progress",
        "completed": "completed",
        "failed": "completed",  # ACP plan entry doesn't have 'failed' status
        "cancelled": "completed",
    }

    # Mapping from Nimbus tool names to ACP ToolKind
    _TOOL_KIND_MAP: dict[str, ToolKind] = {
        # Read operations
        "read_file": "read",
        "Read": "read",
        # Search operations
        "grep_content": "search",
        "Grep": "search",
        "glob_files": "search",
        "Glob": "search",
        # Execute operations
        "bash": "execute",
        "Bash": "execute",
        # Edit operations
        "edit_file": "edit",
        "Edit": "edit",
        "write_file": "edit",
        "Write": "edit",
        # Fetch operations
        "search": "fetch",
        "web_search": "fetch",
        "WebSearch": "fetch",
        "web_fetch": "fetch",
        "WebFetch": "fetch",
    }

    def convert(self, nimbus_event: dict[str, Any]) -> dict[str, Any] | None:
        """Convert a Nimbus SSE event to ACP SessionUpdate format.

        Args:
            nimbus_event: Nimbus SSE event containing 'event' type and 'data' payload.
                Example: {"event": "content.delta", "data": {"text": "..."}}

        Returns:
            ACP SessionUpdate dict or None if the event type is not supported
            or cannot be converted.

        Raises:
            ValueError: If nimbus_event is missing required fields.
        """
        event_type = nimbus_event.get("event")
        data = nimbus_event.get("data", {})

        if not event_type:
            return None

        # Route to appropriate converter based on event type
        converters = {
            # Planning/status events
            "planning": self.convert_planning_event,
            "event.status": self.convert_status_event,
            # DAG events
            "dag_created": self.convert_dag_created,
            # Task/tool execution events
            "task_start": self.convert_task_start,
            "tool.start": self.convert_tool_start,
            "task_done": self.convert_task_done,
            "tool.done": self.convert_tool_done,
            "task_failed": self.convert_task_failed,
            "tool.error": self.convert_tool_error,
            # Content streaming events
            "content.delta": self.convert_content_delta,
            # Permission events (passthrough with transformation)
            "permission_request": self.convert_permission_request,
        }

        converter = converters.get(event_type)
        if converter:
            return converter(data)

        return None

    def convert_planning_event(self, data: dict[str, Any]) -> AgentThoughtChunk:
        """Convert planning event to agent_thought_chunk.

        Args:
            data: Event data containing 'message' field.

        Returns:
            ACP AgentThoughtChunk with the planning message.
        """
        content: TextContent = {
            "type": "text",
            "text": data.get("message", ""),
        }
        return {
            "sessionUpdate": "agent_thought_chunk",
            "content": content,
        }

    def convert_status_event(self, data: dict[str, Any]) -> AgentThoughtChunk:
        """Convert event.status to agent_thought_chunk.

        Args:
            data: Event data containing 'status' and 'message' fields.

        Returns:
            ACP AgentThoughtChunk with the status message.
        """
        status = data.get("status", "")
        message = data.get("message", "")

        # Build descriptive message from status data
        text_parts = []
        if message:
            text_parts.append(message)
        elif status:
            text_parts.append(f"Status: {status}")

        # Include DAG info if available
        if dag_id := data.get("dagID"):
            text_parts.append(f"[DAG: {dag_id}]")
        if total_tasks := data.get("totalTasks"):
            text_parts.append(f"[Tasks: {total_tasks}]")

        content: TextContent = {
            "type": "text",
            "text": " ".join(text_parts) if text_parts else status,
        }
        return {
            "sessionUpdate": "agent_thought_chunk",
            "content": content,
        }

    def convert_dag_created(self, data: dict[str, Any]) -> Plan:
        """Convert dag_created event to ACP Plan with entries.

        Args:
            data: Event data containing 'nodes' list with DAG node information.

        Returns:
            ACP Plan with entries for each DAG node.
        """
        entries: list[PlanEntry] = []

        for node in data.get("nodes", []):
            entry: PlanEntry = {
                "content": node.get("name", node.get("id", "")),
                "status": self._map_task_status(node.get("status", "pending")),
                "priority": "medium",
            }
            entries.append(entry)

        return {
            "sessionUpdate": "plan",
            "entries": entries,
        }

    def convert_task_start(self, data: dict[str, Any]) -> ToolCall:
        """Convert task_start event to ACP ToolCall (pending).

        Args:
            data: Event data containing task_id, name/tool, and input.

        Returns:
            ACP ToolCall with pending status.
        """
        tool_name = data.get("tool", data.get("name", ""))
        return {
            "sessionUpdate": "tool_call",
            "toolCallId": data.get("task_id", ""),
            "title": data.get("name", tool_name),
            "kind": self._map_tool_kind(tool_name),
            "status": "pending",
            "rawInput": data.get("input", {}),
            "locations": self._extract_locations(data),
        }

    def convert_tool_start(self, data: dict[str, Any]) -> ToolCall:
        """Convert tool.start event to ACP ToolCall (pending).

        This handles the OpenCode-compatible event format.

        Args:
            data: Event data containing taskID, name, and input.

        Returns:
            ACP ToolCall with pending status.
        """
        tool_name = data.get("name", "")
        return {
            "sessionUpdate": "tool_call",
            "toolCallId": data.get("taskID", ""),
            "title": tool_name,
            "kind": self._map_tool_kind(tool_name),
            "status": "pending",
            "rawInput": data.get("input", {}),
            "locations": self._extract_locations(data),
        }

    def convert_task_done(self, data: dict[str, Any]) -> ToolCallUpdate:
        """Convert task_done event to ACP ToolCallUpdate (completed).

        Args:
            data: Event data containing task_id, result, and optional tool info.

        Returns:
            ACP ToolCallUpdate with completed status and result content.
        """
        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": data.get("task_id", ""),
            "status": "completed",
            "rawOutput": data.get("result", {}),
            "content": self._build_tool_content(data),
        }

    def convert_tool_done(self, data: dict[str, Any]) -> ToolCallUpdate:
        """Convert tool.done event to ACP ToolCallUpdate (completed).

        This handles the OpenCode-compatible event format.

        Args:
            data: Event data containing taskID and result.

        Returns:
            ACP ToolCallUpdate with completed status.
        """
        result = data.get("result", "")
        raw_output = {"result": result} if isinstance(result, str) else result

        content: list[ToolCallContent] = []
        if result:
            text_content: TextContent = {
                "type": "text",
                "text": str(result) if not isinstance(result, str) else result,
            }
            content_item: ToolCallContentContent = {
                "type": "content",
                "content": text_content,
            }
            content.append(content_item)

        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": data.get("taskID", ""),
            "status": "completed",
            "rawOutput": raw_output,
            "content": content,
        }

    def convert_task_failed(self, data: dict[str, Any]) -> ToolCallUpdate:
        """Convert task_failed event to ACP ToolCallUpdate (failed).

        Args:
            data: Event data containing task_id and error message.

        Returns:
            ACP ToolCallUpdate with failed status and error information.
        """
        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": data.get("task_id", ""),
            "status": "failed",
            "rawOutput": {"error": data.get("error", "")},
        }

    def convert_tool_error(self, data: dict[str, Any]) -> ToolCallUpdate:
        """Convert tool.error event to ACP ToolCallUpdate (failed).

        This handles the OpenCode-compatible event format.

        Args:
            data: Event data containing taskID and error message.

        Returns:
            ACP ToolCallUpdate with failed status.
        """
        return {
            "sessionUpdate": "tool_call_update",
            "toolCallId": data.get("taskID", ""),
            "status": "failed",
            "rawOutput": {"error": data.get("error", "")},
        }

    def convert_content_delta(self, data: dict[str, Any]) -> AgentMessageChunk:
        """Convert content.delta event to ACP AgentMessageChunk.

        Args:
            data: Event data containing 'text' field.

        Returns:
            ACP AgentMessageChunk with the text content.
        """
        content: TextContent = {
            "type": "text",
            "text": data.get("text", ""),
        }
        return {
            "sessionUpdate": "agent_message_chunk",
            "content": content,
        }

    def convert_permission_request(self, data: dict[str, Any]) -> ToolCall:
        """Convert permission_request event to ACP ToolCall (pending).

        Permission requests are represented as tool calls waiting for approval.

        Args:
            data: Event data containing request_id, tool, and args.

        Returns:
            ACP ToolCall with pending status representing the permission request.
        """
        tool_name = data.get("tool", "")
        return {
            "sessionUpdate": "tool_call",
            "toolCallId": data.get("request_id", ""),
            "title": f"Permission: {tool_name}",
            "kind": self._map_tool_kind(tool_name),
            "status": "pending",
            "rawInput": data.get("args", {}),
            "locations": self._extract_locations_from_args(data.get("args", {})),
        }

    # -------------------------------------------------------------------------
    # Helper Methods
    # -------------------------------------------------------------------------

    def _map_task_status(
        self, nimbus_status: str
    ) -> str:
        """Map Nimbus TaskStatus to ACP plan entry status.

        Args:
            nimbus_status: Nimbus task status string.

        Returns:
            ACP plan entry status ('pending', 'in_progress', or 'completed').
        """
        return self._TASK_STATUS_MAP.get(nimbus_status, "pending")

    def _map_tool_kind(self, tool_name: str) -> ToolKind:
        """Map Nimbus tool name to ACP ToolKind.

        Args:
            tool_name: Nimbus tool name.

        Returns:
            ACP ToolKind enum value.
        """
        return self._TOOL_KIND_MAP.get(tool_name, "other")

    def _extract_locations(self, task: dict[str, Any]) -> list[ToolCallLocation]:
        """Extract file locations from task input.

        Args:
            task: Task data containing 'input' dict with potential path info.

        Returns:
            List of ToolCallLocation dicts.
        """
        return self._extract_locations_from_args(task.get("input", {}))

    def _extract_locations_from_args(
        self, args: dict[str, Any]
    ) -> list[ToolCallLocation]:
        """Extract file locations from tool arguments.

        Args:
            args: Tool arguments dict with potential path/file_path keys.

        Returns:
            List of ToolCallLocation dicts.
        """
        locations: list[ToolCallLocation] = []

        # Check common path argument names
        path = args.get("path") or args.get("file_path")
        if path:
            location: ToolCallLocation = {"path": path}
            # Include line number if available
            if line := args.get("line"):
                location["line"] = line
            locations.append(location)

        return locations

    def _build_tool_content(
        self, task: dict[str, Any]
    ) -> list[ToolCallContent]:
        """Build tool call content from task result.

        Args:
            task: Task data containing 'result', 'tool', and 'input'.

        Returns:
            List of ToolCallContent dicts.
        """
        result = task.get("result", {})
        tool = task.get("tool", "")
        input_data = task.get("input", {})

        # Handle diff content for edit operations
        if tool in ("Edit", "edit_file"):
            diff_content: ToolCallContentDiff = {
                "type": "diff",
                "path": input_data.get("file_path", ""),
                "oldText": input_data.get("old_string", ""),
                "newText": input_data.get("new_string", ""),
            }
            return [diff_content]

        # Handle regular content
        content: list[ToolCallContent] = []

        if isinstance(result, str):
            text_content: TextContent = {
                "type": "text",
                "text": result,
            }
            content_item: ToolCallContentContent = {
                "type": "content",
                "content": text_content,
            }
            content.append(content_item)
        elif isinstance(result, dict):
            # Extract text from result dict
            text = result.get("content", result.get("text", ""))
            if text:
                text_content = {
                    "type": "text",
                    "text": str(text),
                }
                content_item = {
                    "type": "content",
                    "content": text_content,
                }
                content.append(content_item)

        return content


# Module-level singleton for convenience
_default_converter: ACPEventConverter | None = None


def get_converter() -> ACPEventConverter:
    """Get the default ACPEventConverter singleton.

    Returns:
        The default ACPEventConverter instance.
    """
    global _default_converter
    if _default_converter is None:
        _default_converter = ACPEventConverter()
    return _default_converter


def convert_event(nimbus_event: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Nimbus SSE event to ACP SessionUpdate format.

    This is a convenience function that uses the default converter.

    Args:
        nimbus_event: Nimbus SSE event containing 'event' type and 'data' payload.

    Returns:
        ACP SessionUpdate dict or None if the event cannot be converted.
    """
    return get_converter().convert(nimbus_event)


__all__ = [
    "ACPEventConverter",
    "get_converter",
    "convert_event",
]
