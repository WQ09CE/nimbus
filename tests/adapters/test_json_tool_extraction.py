"""
Unit tests for JSON tool call extraction — the adapter's defense against
small model format chaos (qwen, ollama, etc.).

These test the pure parsing logic with no LLM calls needed.
"""
import pytest
from nimbus.adapters.direct_adapter import _extract_tool_calls_from_json


class TestExtractToolCallsFromJson:
    """Test JSON tool call extraction — the adapter's defense against small model format chaos."""

    # === Standard formats (should all succeed) ===

    def test_standard_name_arguments(self):
        """OpenAI standard: {"name": "Read", "arguments": {"file_path": "x"}}"""
        result = _extract_tool_calls_from_json('{"name": "Read", "arguments": {"file_path": "x.py"}}')
        assert result == [{"name": "Read", "arguments": {"file_path": "x.py"}}]

    def test_function_wrapper(self):
        """Nested: {"function": {"name": "Read", "arguments": {...}}}"""
        result = _extract_tool_calls_from_json(
            '{"function": {"name": "Read", "arguments": {"file_path": "x.py"}}}'
        )
        assert result == [{"name": "Read", "arguments": {"file_path": "x.py"}}]

    def test_tool_calls_wrapper(self):
        """Qwen/ollama wrapper: {"tool_calls": [{"function": {"name": "Read", "arguments": {...}}}]}"""
        json_str = (
            '{"tool_calls": [{"id": "tc_0", "type": "function", '
            '"function": {"name": "Read", "arguments": {"file_path": "README.md"}}}]}'
        )
        result = _extract_tool_calls_from_json(json_str)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "Read"
        assert result[0]["arguments"] == {"file_path": "README.md"}

    def test_tool_calls_multiple(self):
        """Multiple tool calls in wrapper"""
        json_str = (
            '{"tool_calls": ['
            '{"function": {"name": "Read", "arguments": {"file_path": "a.py"}}}, '
            '{"function": {"name": "Bash", "arguments": {"command": "ls"}}}'
            ']}'
        )
        result = _extract_tool_calls_from_json(json_str)
        assert result is not None
        assert len(result) == 2
        assert result[0]["name"] == "Read"
        assert result[1]["name"] == "Bash"

    # === Small model format variants (qwen specific) ===

    def test_tool_name_key(self):
        """Qwen variant: {"tool_name": "Read", "arguments": {...}}"""
        result = _extract_tool_calls_from_json(
            '{"tool_name": "ProjectOverview", "arguments": {"path": "/tmp", "depth": 3}}'
        )
        assert result == [{"name": "ProjectOverview", "arguments": {"path": "/tmp", "depth": 3}}]

    def test_tool_key(self):
        """Variant: {"tool": "Bash", "args": {"command": "ls"}}"""
        result = _extract_tool_calls_from_json('{"tool": "Bash", "args": {"command": "ls -la"}}')
        assert result == [{"name": "Bash", "arguments": {"command": "ls -la"}}]

    def test_parameters_key(self):
        """Variant: {"name": "Read", "parameters": {"file_path": "x"}}"""
        result = _extract_tool_calls_from_json(
            '{"name": "Read", "parameters": {"file_path": "test.py"}}'
        )
        assert result == [{"name": "Read", "arguments": {"file_path": "test.py"}}]

    def test_arguments_as_string(self):
        """Arguments passed as JSON string instead of object"""
        result = _extract_tool_calls_from_json(
            '{"name": "Read", "arguments": "{\\"file_path\\": \\"x.py\\"}"}'
        )
        assert result == [{"name": "Read", "arguments": {"file_path": "x.py"}}]

    def test_submit_result(self):
        """SubmitResult pattern: {"result": "task done"}"""
        result = _extract_tool_calls_from_json('{"result": "All tests passed."}')
        assert result == [{"name": "SubmitResult", "arguments": {"result": "All tests passed."}}]

    def test_array_of_tool_calls(self):
        """Direct array: [{"name": "Read", ...}, {"name": "Bash", ...}]"""
        json_str = (
            '[{"name": "Read", "arguments": {"file_path": "a.py"}}, '
            '{"name": "Bash", "arguments": {"command": "pwd"}}]'
        )
        result = _extract_tool_calls_from_json(json_str)
        assert len(result) == 2

    # === Should NOT parse as tool calls ===

    def test_error_json_not_tool_call(self):
        """Error JSON should NOT be parsed as a tool call"""
        result = _extract_tool_calls_from_json(
            '{"error": "Command failed due to unbalanced quotes"}'
        )
        assert result is None

    def test_plain_text(self):
        """Plain text is not JSON"""
        result = _extract_tool_calls_from_json("Hello, how can I help?")
        assert result is None

    def test_invalid_json(self):
        """Malformed JSON"""
        result = _extract_tool_calls_from_json("{name: Read}")
        assert result is None

    def test_empty_string(self):
        """Empty string"""
        result = _extract_tool_calls_from_json("")
        assert result is None

    def test_json_without_tool_keys(self):
        """Valid JSON but no tool-related keys"""
        result = _extract_tool_calls_from_json('{"status": "ok", "count": 42}')
        assert result is None

    def test_arguments_not_dict(self):
        """Arguments that can't be parsed to dict"""
        result = _extract_tool_calls_from_json('{"name": "Read", "arguments": "not-json"}')
        assert result is None

    # === Edge cases ===

    def test_extra_fields_ignored(self):
        """Extra fields in the dict don't break parsing"""
        result = _extract_tool_calls_from_json(
            '{"name": "Read", "arguments": {"file_path": "x"}, "id": "tc_0", "type": "function"}'
        )
        assert result == [{"name": "Read", "arguments": {"file_path": "x"}}]

    def test_tool_calls_wrapper_with_nested_function(self):
        """Real qwen output: tool_calls array with items having both id/type/function"""
        json_str = (
            '{"tool_calls": [{"id": "json_extract_txt_0", "type": "function", '
            '"function": {"name": "Read", "arguments": '
            '{"file_path": "/Users/wangqing/sourcecode/agent/agent-framework/nimbus/config/registry.yaml"}}}]}'
        )
        result = _extract_tool_calls_from_json(json_str)
        assert result is not None
        assert result[0]["name"] == "Read"
        assert "file_path" in result[0]["arguments"]

    def test_codeblock_not_handled_here(self):
        """Codeblock stripping happens upstream, not in this function"""
        # This function receives the already-stripped JSON string
        result = _extract_tool_calls_from_json('```json\n{"name": "Read"}\n```')
        assert result is None  # Because the raw string including ``` is not valid JSON
