"""Tests for nimbus_next.tools — registry and core tools."""

import asyncio
import tempfile
from pathlib import Path

import pytest

from nimbus_next.tools.registry import ToolDefinition, ToolParameter, ToolRegistry, tool


# =============================================================================
# Registry Tests
# =============================================================================


class TestToolParameter:
    def test_json_schema(self):
        p = ToolParameter("file_path", "string", "Path to file")
        schema = p.to_json_schema()
        assert schema == {"type": "string", "description": "Path to file"}

    def test_enum_parameter(self):
        p = ToolParameter("mode", "string", "Output mode", enum=["json", "text"])
        schema = p.to_json_schema()
        assert schema["enum"] == ["json", "text"]


class TestToolDefinition:
    def test_openai_format(self):
        defn = ToolDefinition(
            name="Read",
            description="Read a file",
            parameters=[
                ToolParameter("file_path", "string", "Path", required=True),
                ToolParameter("limit", "integer", "Max lines", required=False),
            ],
        )
        schema = defn.to_openai_format()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "Read"
        assert "file_path" in schema["function"]["parameters"]["properties"]
        assert "file_path" in schema["function"]["parameters"]["required"]
        assert "limit" not in schema["function"]["parameters"]["required"]

    def test_anthropic_format(self):
        defn = ToolDefinition(name="Bash", description="Run command", parameters=[])
        schema = defn.to_anthropic_format()
        assert schema["name"] == "Bash"
        assert "input_schema" in schema


class TestToolRegistry:
    def test_register_and_lookup(self):
        reg = ToolRegistry()
        defn = ToolDefinition(name="TestTool", description="test")
        reg.register(defn, lambda: "ok")
        assert "TestTool" in reg
        assert len(reg) == 1
        assert reg.list_tools() == ["TestTool"]

    def test_register_decorated(self):
        @tool(name="MyTool", description="my tool")
        def my_func():
            return "result"

        reg = ToolRegistry()
        reg.register_decorated(my_func)
        assert "MyTool" in reg

    def test_get_function(self):
        reg = ToolRegistry()
        fn = lambda: 42
        reg.register(ToolDefinition(name="T", description=""), fn)
        assert reg.get_function("T") is fn
        assert reg.get_function("missing") is None

    def test_get_schemas_openai(self):
        reg = ToolRegistry()
        reg.register(
            ToolDefinition("A", "tool a", [ToolParameter("x", "string", "param x")]),
            lambda: None,
        )
        reg.register(ToolDefinition("B", "tool b"), lambda: None)
        schemas = reg.get_schemas("openai")
        assert len(schemas) == 2
        assert schemas[0]["function"]["name"] == "A"

    def test_get_schemas_anthropic(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition("A", "tool a"), lambda: None)
        schemas = reg.get_schemas("anthropic")
        assert schemas[0]["name"] == "A"

    def test_unknown_format_raises(self):
        reg = ToolRegistry()
        with pytest.raises(ValueError, match="Unknown format"):
            reg.get_schemas("gemini")

    @pytest.mark.asyncio
    async def test_execute_async(self):
        @tool(name="AsyncTool", description="async")
        async def async_fn(x: str):
            return f"got {x}"

        reg = ToolRegistry()
        reg.register_decorated(async_fn)
        result = await reg.execute("AsyncTool", {"x": "hello"})
        assert result == "got hello"

    @pytest.mark.asyncio
    async def test_execute_sync(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition("SyncTool", "sync"), lambda x: x * 2)
        result = await reg.execute("SyncTool", {"x": 3})
        assert result == 6

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError):
            await reg.execute("Missing", {})


# =============================================================================
# Core Tool Tests
# =============================================================================


class TestReadTool:
    @pytest.mark.asyncio
    async def test_read_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3")
        from nimbus_next.tools.read import read_file
        result = await read_file(str(f))
        assert "line1" in result
        assert "line2" in result

    @pytest.mark.asyncio
    async def test_read_with_offset(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("\n".join(f"line{i}" for i in range(1, 11)))
        from nimbus_next.tools.read import read_file
        result = await read_file(str(f), offset=5, limit=3)
        assert "line5" in result

    @pytest.mark.asyncio
    async def test_read_not_found(self):
        from nimbus_next.tools.read import read_file
        with pytest.raises(FileNotFoundError):
            await read_file("/nonexistent/file.txt")

    @pytest.mark.asyncio
    async def test_read_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "sub").mkdir()
        from nimbus_next.tools.read import read_file
        result = await read_file(str(tmp_path))
        assert "Directory" in result


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_write_new_file(self, tmp_path):
        target = tmp_path / "new" / "file.txt"
        from nimbus_next.tools.write import write_file
        result = await write_file(str(target), "hello world")
        assert "Successfully wrote" in result
        assert target.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_write_overwrite(self, tmp_path):
        target = tmp_path / "file.txt"
        target.write_text("old")
        from nimbus_next.tools.write import write_file
        await write_file(str(target), "new")
        assert target.read_text() == "new"


class TestEditTool:
    @pytest.mark.asyncio
    async def test_exact_replace(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    pass\n")
        from nimbus_next.tools.edit import edit_file
        result = await edit_file(str(f), "pass", "return 42")
        assert "Successfully" in result
        assert "return 42" in f.read_text()

    @pytest.mark.asyncio
    async def test_multiple_occurrences(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("x = 1\nx = 1\n")
        from nimbus_next.tools.edit import edit_file
        with pytest.raises(ValueError, match="occurrences"):
            await edit_file(str(f), "x = 1", "x = 2")

    @pytest.mark.asyncio
    async def test_not_found(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello world")
        from nimbus_next.tools.edit import edit_file
        with pytest.raises(ValueError, match="not found"):
            await edit_file(str(f), "xyz", "abc")


class TestBashTool:
    @pytest.mark.asyncio
    async def test_simple_command(self):
        from nimbus_next.tools.bash import bash_command
        result = await bash_command("echo hello")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_exit_code(self):
        from nimbus_next.tools.bash import bash_command
        result = await bash_command("exit 1")
        assert "Exit code: 1" in result

    @pytest.mark.asyncio
    async def test_empty_command(self):
        from nimbus_next.tools.bash import bash_command
        with pytest.raises(ValueError):
            await bash_command("")

    @pytest.mark.asyncio
    async def test_timeout(self):
        from nimbus_next.tools.bash import bash_command
        result = await bash_command("sleep 10", timeout=0.5)
        assert "timed out" in result


class TestGrepTool:
    @pytest.mark.asyncio
    async def test_grep_file(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    pass\ndef world():\n    pass\n")
        from nimbus_next.tools.grep import grep_search
        result = await grep_search("def", str(f))
        assert "def hello" in result
        assert "def world" in result

    @pytest.mark.asyncio
    async def test_grep_directory(self, tmp_path):
        (tmp_path / "a.py").write_text("import os\n")
        (tmp_path / "b.py").write_text("import sys\n")
        from nimbus_next.tools.grep import grep_search
        result = await grep_search("import", str(tmp_path), glob="*.py")
        assert "a.py" in result
        assert "b.py" in result

    @pytest.mark.asyncio
    async def test_no_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        from nimbus_next.tools.grep import grep_search
        result = await grep_search("xyz", str(tmp_path))
        assert "No matches" in result
