"""Tests for nimbus.tools.base module."""

import pytest
import asyncio
from nimbus.tools.base import (
    ToolParameter,
    ToolDefinition,
    ToolRegistry,
    ToolExecutionError,
    tool,
    get_default_registry,
)


class TestToolParameter:
    """Tests for ToolParameter class."""

    def test_basic_parameter(self):
        """Test creating a basic string parameter."""
        param = ToolParameter(
            name="file_path",
            type="string",
            description="Path to the file",
            required=True,
        )
        assert param.name == "file_path"
        assert param.type == "string"
        assert param.required is True

    def test_to_json_schema_basic(self):
        """Test JSON Schema conversion for basic parameter."""
        param = ToolParameter(
            name="count",
            type="integer",
            description="Number of items",
            required=True,
        )
        schema = param.to_json_schema()
        assert schema["type"] == "integer"
        assert schema["description"] == "Number of items"
        assert "default" not in schema

    def test_to_json_schema_with_enum(self):
        """Test JSON Schema conversion with enum values."""
        param = ToolParameter(
            name="mode",
            type="string",
            description="Operation mode",
            required=True,
            enum=["read", "write", "append"],
        )
        schema = param.to_json_schema()
        assert schema["enum"] == ["read", "write", "append"]

    def test_to_json_schema_with_default(self):
        """Test JSON Schema conversion with default value."""
        param = ToolParameter(
            name="encoding",
            type="string",
            description="File encoding",
            required=False,
            default="utf-8",
        )
        schema = param.to_json_schema()
        assert schema["default"] == "utf-8"

    def test_to_json_schema_array_type(self):
        """Test JSON Schema conversion for array type."""
        param = ToolParameter(
            name="files",
            type="array",
            description="List of file paths",
            required=True,
            items={"type": "string"},
        )
        schema = param.to_json_schema()
        assert schema["type"] == "array"
        assert schema["items"] == {"type": "string"}

    def test_from_dict(self):
        """Test creating parameter from dictionary."""
        data = {
            "name": "pattern",
            "type": "string",
            "description": "Search pattern",
            "required": True,
            "enum": ["glob", "regex"],
        }
        param = ToolParameter.from_dict(data)
        assert param.name == "pattern"
        assert param.enum == ["glob", "regex"]

    def test_to_dict(self):
        """Test converting parameter to dictionary."""
        param = ToolParameter(
            name="limit",
            type="integer",
            description="Max results",
            required=False,
            default=100,
        )
        data = param.to_dict()
        assert data["name"] == "limit"
        assert data["default"] == 100


class TestToolDefinition:
    """Tests for ToolDefinition class."""

    def test_basic_definition(self):
        """Test creating a basic tool definition."""
        tool_def = ToolDefinition(
            name="Read",
            description="Read file contents",
            parameters=[
                ToolParameter("file_path", "string", "Path to file", required=True),
            ],
        )
        assert tool_def.name == "Read"
        assert len(tool_def.parameters) == 1
        assert tool_def.dangerous is False

    def test_get_required_parameters(self):
        """Test getting required parameters."""
        tool_def = ToolDefinition(
            name="Read",
            description="Read file contents",
            parameters=[
                ToolParameter("file_path", "string", "Path", required=True),
                ToolParameter("encoding", "string", "Encoding", required=False),
            ],
        )
        required = tool_def.get_required_parameters()
        assert len(required) == 1
        assert required[0].name == "file_path"

    def test_get_optional_parameters(self):
        """Test getting optional parameters."""
        tool_def = ToolDefinition(
            name="Read",
            description="Read file contents",
            parameters=[
                ToolParameter("file_path", "string", "Path", required=True),
                ToolParameter("encoding", "string", "Encoding", required=False),
            ],
        )
        optional = tool_def.get_optional_parameters()
        assert len(optional) == 1
        assert optional[0].name == "encoding"

    def test_to_tool_use_format(self):
        """Test Claude Tool Use format conversion."""
        tool_def = ToolDefinition(
            name="Read",
            description="Read file contents",
            parameters=[
                ToolParameter("file_path", "string", "Path to file", required=True),
                ToolParameter("encoding", "string", "Encoding", required=False, default="utf-8"),
            ],
        )
        result = tool_def.to_tool_use_format()

        assert result["name"] == "Read"
        assert result["description"] == "Read file contents"
        assert result["input_schema"]["type"] == "object"
        assert "file_path" in result["input_schema"]["properties"]
        assert result["input_schema"]["required"] == ["file_path"]

    def test_to_openai_format(self):
        """Test OpenAI function calling format conversion."""
        tool_def = ToolDefinition(
            name="Search",
            description="Search for files",
            parameters=[
                ToolParameter("pattern", "string", "Search pattern", required=True),
            ],
        )
        result = tool_def.to_openai_format()

        assert result["type"] == "function"
        assert result["function"]["name"] == "Search"
        assert result["function"]["parameters"]["type"] == "object"
        assert "pattern" in result["function"]["parameters"]["properties"]

    def test_from_dict(self):
        """Test creating definition from dictionary."""
        data = {
            "name": "Execute",
            "description": "Execute a command",
            "parameters": [
                {"name": "command", "type": "string", "description": "Command to run", "required": True},
            ],
            "dangerous": True,
        }
        tool_def = ToolDefinition.from_dict(data)
        assert tool_def.name == "Execute"
        assert tool_def.dangerous is True
        assert len(tool_def.parameters) == 1


class TestToolRegistry:
    """Tests for ToolRegistry class."""

    def test_register_and_get(self):
        """Test registering and retrieving a tool."""
        registry = ToolRegistry()
        tool_def = ToolDefinition(name="Test", description="Test tool")

        def test_func(**kwargs):
            return "result"

        registry.register(tool_def, test_func)

        result = registry.get("Test")
        assert result is not None
        assert result[0].name == "Test"
        assert result[1] == test_func

    def test_register_duplicate_raises(self):
        """Test that registering duplicate name raises error."""
        registry = ToolRegistry()
        tool_def = ToolDefinition(name="Test", description="Test tool")

        registry.register(tool_def, lambda: None)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(tool_def, lambda: None)

    def test_list_tools(self):
        """Test listing all tool names."""
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="A", description="A"), lambda: None)
        registry.register(ToolDefinition(name="B", description="B"), lambda: None)

        tools = registry.list_tools()
        assert set(tools) == {"A", "B"}

    def test_list_dangerous_tools(self):
        """Test listing dangerous tools."""
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="Safe", description="Safe", dangerous=False), lambda: None)
        registry.register(ToolDefinition(name="Danger", description="Danger", dangerous=True), lambda: None)

        dangerous = registry.list_dangerous_tools()
        assert dangerous == ["Danger"]

    def test_get_definitions_claude(self):
        """Test getting definitions in Claude format."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="Read",
                description="Read file",
                parameters=[ToolParameter("path", "string", "File path", required=True)],
            ),
            lambda: None,
        )

        definitions = registry.get_definitions(format="claude")
        assert len(definitions) == 1
        assert definitions[0]["name"] == "Read"
        assert "input_schema" in definitions[0]

    def test_get_definitions_openai(self):
        """Test getting definitions in OpenAI format."""
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(name="Read", description="Read file"),
            lambda: None,
        )

        definitions = registry.get_definitions(format="openai")
        assert len(definitions) == 1
        assert definitions[0]["type"] == "function"

    def test_get_definitions_invalid_format(self):
        """Test that invalid format raises error."""
        registry = ToolRegistry()
        with pytest.raises(ValueError, match="Unknown format"):
            registry.get_definitions(format="invalid")

    def test_unregister(self):
        """Test unregistering a tool."""
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="Test", description="Test"), lambda: None)

        removed = registry.unregister("Test")
        assert removed is not None
        assert removed.name == "Test"
        assert "Test" not in registry

    def test_contains(self):
        """Test __contains__ method."""
        registry = ToolRegistry()
        registry.register(ToolDefinition(name="Test", description="Test"), lambda: None)

        assert "Test" in registry
        assert "Other" not in registry

    def test_len(self):
        """Test __len__ method."""
        registry = ToolRegistry()
        assert len(registry) == 0

        registry.register(ToolDefinition(name="A", description="A"), lambda: None)
        assert len(registry) == 1

    @pytest.mark.asyncio
    async def test_execute_sync_function(self):
        """Test executing a sync tool function."""
        registry = ToolRegistry()

        def add_numbers(a: int, b: int, **kwargs) -> int:
            return a + b

        registry.register(
            ToolDefinition(
                name="Add",
                description="Add numbers",
                parameters=[
                    ToolParameter("a", "integer", "First number", required=True),
                    ToolParameter("b", "integer", "Second number", required=True),
                ],
            ),
            add_numbers,
        )

        result = await registry.execute("Add", {"a": 2, "b": 3})
        assert result == 5

    @pytest.mark.asyncio
    async def test_execute_async_function(self):
        """Test executing an async tool function."""
        registry = ToolRegistry()

        async def async_multiply(x: int, y: int, **kwargs) -> int:
            await asyncio.sleep(0.01)
            return x * y

        registry.register(
            ToolDefinition(name="Multiply", description="Multiply numbers"),
            async_multiply,
        )

        result = await registry.execute("Multiply", {"x": 4, "y": 5})
        assert result == 20

    @pytest.mark.asyncio
    async def test_execute_not_found(self):
        """Test executing non-existent tool raises error."""
        registry = ToolRegistry()

        with pytest.raises(ToolExecutionError) as exc_info:
            await registry.execute("NotFound", {})

        assert "NotFound" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_execute_with_context(self):
        """Test executing tool with context parameters."""
        registry = ToolRegistry()

        def check_context(value: str, session_id: str = None, **kwargs) -> str:
            return f"{value}:{session_id}"

        registry.register(
            ToolDefinition(name="Check", description="Check context"),
            check_context,
        )

        result = await registry.execute("Check", {"value": "test"}, session_id="abc123")
        assert result == "test:abc123"

    @pytest.mark.asyncio
    async def test_execute_error_handling(self):
        """Test that execution errors are wrapped in ToolExecutionError."""
        registry = ToolRegistry()

        def failing_tool(**kwargs):
            raise ValueError("Something went wrong")

        registry.register(
            ToolDefinition(name="Failing", description="Always fails"),
            failing_tool,
        )

        with pytest.raises(ToolExecutionError) as exc_info:
            await registry.execute("Failing", {})

        assert exc_info.value.tool_name == "Failing"
        assert "Something went wrong" in exc_info.value.message
        assert exc_info.value.original_error is not None


class TestToolDecorator:
    """Tests for @tool decorator."""

    def test_decorator_attaches_definition(self):
        """Test that decorator attaches tool definition."""

        @tool(
            name="MyTool",
            description="My tool description",
            parameters=[
                ToolParameter("input", "string", "Input value", required=True),
            ],
        )
        def my_tool(input: str, **kwargs) -> str:
            return input.upper()

        assert hasattr(my_tool, "_tool_definition")
        assert my_tool._tool_definition.name == "MyTool"
        assert my_tool._tool_definition.description == "My tool description"

    def test_decorator_preserves_function(self):
        """Test that decorator preserves original function."""

        @tool(name="Echo", description="Echo input")
        def echo(text: str, **kwargs) -> str:
            return text

        assert echo(text="hello") == "hello"

    def test_decorator_with_async_function(self):
        """Test decorator with async function."""

        @tool(name="AsyncTool", description="Async tool")
        async def async_tool(value: int, **kwargs) -> int:
            return value * 2

        assert hasattr(async_tool, "_tool_definition")
        assert asyncio.iscoroutinefunction(async_tool)

    def test_register_decorated(self):
        """Test registering decorated function."""
        registry = ToolRegistry()

        @tool(
            name="Decorated",
            description="Decorated tool",
            parameters=[
                ToolParameter("x", "integer", "Value", required=True),
            ],
        )
        def decorated_func(x: int, **kwargs) -> int:
            return x + 1

        registry.register_decorated(decorated_func)

        assert "Decorated" in registry
        definition = registry.get_definition("Decorated")
        assert definition.name == "Decorated"

    def test_register_non_decorated_raises(self):
        """Test that registering non-decorated function raises error."""
        registry = ToolRegistry()

        def not_decorated(**kwargs):
            pass

        with pytest.raises(ValueError, match="not decorated"):
            registry.register_decorated(not_decorated)

    def test_dangerous_tool(self):
        """Test creating a dangerous tool."""

        @tool(
            name="DangerousTool",
            description="A dangerous tool",
            dangerous=True,
        )
        def dangerous(**kwargs):
            pass

        assert dangerous._tool_definition.dangerous is True


class TestDefaultRegistry:
    """Tests for default registry functions."""

    def test_get_default_registry(self):
        """Test getting default registry."""
        registry1 = get_default_registry()
        registry2 = get_default_registry()
        assert registry1 is registry2

    def test_default_registry_is_tool_registry(self):
        """Test that default registry is ToolRegistry instance."""
        registry = get_default_registry()
        assert isinstance(registry, ToolRegistry)


class TestToolExecutionError:
    """Tests for ToolExecutionError."""

    def test_error_message(self):
        """Test error message format."""
        error = ToolExecutionError("MyTool", "Something failed")
        assert "MyTool" in str(error)
        assert "Something failed" in str(error)

    def test_error_with_original(self):
        """Test error with original exception."""
        original = ValueError("Original error")
        error = ToolExecutionError("MyTool", "Wrapper message", original_error=original)

        assert error.original_error is original
        assert error.tool_name == "MyTool"
