from nimbus.adapters.direct_adapter import DirectAdapter, LLMConfig, _get_builtin_openai_tool_schema, _merge_codex_function_call_item
from nimbus.core.tools.registry import ToolDefinition, ToolParameter


def test_merge_codex_function_call_uses_streamed_arguments_when_done_item_has_none():
    pending = {
        "id": "call_123",
        "name": "Read",
        "arguments": '{"path":"README.md","offset":1}',
    }
    item = {
        "type": "function_call",
        "id": "fc_123",
        "call_id": "call_123",
        "name": "Read",
    }

    merged = _merge_codex_function_call_item(pending, item)

    assert merged == {
        "id": "call_123",
        "name": "Read",
        "arguments": {"path": "README.md", "offset": 1},
    }


def test_merge_codex_function_call_uses_inline_done_arguments_for_gpt_54_shape():
    pending = {
        "id": "call_456",
        "name": "Bash",
        "arguments": "",
    }
    item = {
        "type": "function_call",
        "id": "fc_456",
        "call_id": "call_456",
        "name": "Bash",
        "arguments": '{"command":"pwd"}',
    }

    merged = _merge_codex_function_call_item(pending, item)

    assert merged == {
        "id": "call_456",
        "name": "Bash",
        "arguments": {"command": "pwd"},
    }


def test_merge_codex_function_call_supports_dict_arguments_without_json_roundtrip():
    merged = _merge_codex_function_call_item(
        None,
        {
            "type": "function_call",
            "id": "fc_789",
            "call_id": "call_789",
            "name": "Write",
            "arguments": {"path": "a.txt", "content": "hello"},
        },
    )

    assert merged == {
        "id": "call_789",
        "name": "Write",
        "arguments": {"path": "a.txt", "content": "hello"},
    }


def test_openai_tool_schema_marks_tools_strict_and_no_additional_properties():
    tool = ToolDefinition(
        name="Bash",
        description="Run shell commands",
        parameters=[ToolParameter("command", "string", "Shell command", required=True)],
    )

    schema = tool.to_openai_format()

    assert schema["function"]["strict"] is True
    assert schema["function"]["parameters"]["additionalProperties"] is False
    assert schema["function"]["parameters"]["required"] == ["command"]


def test_convert_tools_wraps_simplified_function_shape_with_schema():
    adapter = DirectAdapter.__new__(DirectAdapter)
    adapter.config = LLMConfig()

    tools = [{
        "type": "function",
        "name": "Bash",
        "description": "Run shell commands",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
            "additionalProperties": False,
        },
        "strict": True,
    }]

    converted = adapter._convert_tools(tools)

    assert converted == [{
        "type": "function",
        "function": {
            "name": "Bash",
            "description": "Run shell commands",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }]


def test_builtin_openai_tool_schema_contains_read_properties():
    schema = _get_builtin_openai_tool_schema("Read")

    assert schema is not None
    assert schema["function"]["parameters"]["properties"]["file_path"]["type"] == "string"
    assert schema["function"]["parameters"]["additionalProperties"] is False


def test_convert_tools_to_responses_api_recovers_missing_builtin_parameters():
    adapter = DirectAdapter.__new__(DirectAdapter)
    adapter.config = LLMConfig()

    converted = adapter._convert_tools_to_responses_api([
        {
            "type": "function",
            "name": "Read",
            "description": "Read file contents. Supports offset/limit for large files.",
            "parameters": {},
            "strict": True,
        }
    ])

    assert converted[0]["name"] == "Read"
    assert converted[0]["parameters"]["properties"]["file_path"]["type"] == "string"
    assert converted[0]["parameters"]["required"] == ["file_path"]
    assert converted[0]["parameters"]["additionalProperties"] is False
    assert "strict" not in converted[0]
