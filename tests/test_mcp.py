"""Tests for the MCP (Model Context Protocol) adapter module."""

import asyncio
import json
import pytest
from typing import Any, Dict, List

# Import directly from modules to avoid __init__.py import issues
from nimbus.skills.schema import SkillDefinition, SkillParameter
from nimbus.skills.mcp import (
    skill_to_mcp_tool,
    mcp_tool_to_skill,
    skills_to_mcp_tools,
    mcp_tools_to_skills,
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCError,
    JSONRPCErrorCode,
    MCPTool,
    MCPServerInfo,
)


# =============================================================================
# Test Conversion Functions
# =============================================================================


class TestSkillToMCPTool:
    """Tests for skill_to_mcp_tool conversion."""

    def test_basic_conversion(self):
        """Test basic skill to MCP tool conversion."""
        skill = SkillDefinition(
            name="read_file",
            description="Read the contents of a file",
            parameters=[
                SkillParameter(
                    name="path",
                    type="string",
                    description="The file path",
                    required=True,
                ),
            ],
        )

        mcp_tool = skill_to_mcp_tool(skill)

        assert mcp_tool["name"] == "read_file"
        assert mcp_tool["description"] == "Read the contents of a file"
        assert mcp_tool["inputSchema"]["type"] == "object"
        assert "path" in mcp_tool["inputSchema"]["properties"]
        assert mcp_tool["inputSchema"]["required"] == ["path"]

    def test_conversion_with_optional_params(self):
        """Test conversion with optional parameters."""
        skill = SkillDefinition(
            name="search",
            description="Search for text",
            parameters=[
                SkillParameter(
                    name="query",
                    type="string",
                    description="Search query",
                    required=True,
                ),
                SkillParameter(
                    name="max_results",
                    type="integer",
                    description="Maximum results",
                    required=False,
                    default=10,
                ),
            ],
        )

        mcp_tool = skill_to_mcp_tool(skill)

        assert "query" in mcp_tool["inputSchema"]["required"]
        assert "max_results" not in mcp_tool["inputSchema"]["required"]
        assert mcp_tool["inputSchema"]["properties"]["max_results"]["default"] == 10

    def test_conversion_with_enum(self):
        """Test conversion with enum values."""
        skill = SkillDefinition(
            name="set_mode",
            description="Set operation mode",
            parameters=[
                SkillParameter(
                    name="mode",
                    type="string",
                    description="Operation mode",
                    required=True,
                    enum=["fast", "normal", "slow"],
                ),
            ],
        )

        mcp_tool = skill_to_mcp_tool(skill)

        assert mcp_tool["inputSchema"]["properties"]["mode"]["enum"] == [
            "fast",
            "normal",
            "slow",
        ]

    def test_conversion_with_array_param(self):
        """Test conversion with array parameter."""
        skill = SkillDefinition(
            name="process_items",
            description="Process multiple items",
            parameters=[
                SkillParameter(
                    name="items",
                    type="array",
                    description="List of items",
                    required=True,
                    items={"type": "string"},
                ),
            ],
        )

        mcp_tool = skill_to_mcp_tool(skill)

        assert mcp_tool["inputSchema"]["properties"]["items"]["type"] == "array"
        assert mcp_tool["inputSchema"]["properties"]["items"]["items"] == {
            "type": "string"
        }

    def test_conversion_with_object_param(self):
        """Test conversion with object parameter."""
        skill = SkillDefinition(
            name="create_record",
            description="Create a record",
            parameters=[
                SkillParameter(
                    name="data",
                    type="object",
                    description="Record data",
                    required=True,
                    properties={
                        "name": {"type": "string"},
                        "value": {"type": "number"},
                    },
                ),
            ],
        )

        mcp_tool = skill_to_mcp_tool(skill)

        assert mcp_tool["inputSchema"]["properties"]["data"]["type"] == "object"
        assert "name" in mcp_tool["inputSchema"]["properties"]["data"]["properties"]


class TestMCPToolToSkill:
    """Tests for mcp_tool_to_skill conversion."""

    def test_basic_conversion(self):
        """Test basic MCP tool to skill conversion."""
        mcp_tool = {
            "name": "write_file",
            "description": "Write content to a file",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
            },
        }

        skill = mcp_tool_to_skill(mcp_tool, "mcp:filesystem")

        assert skill.name == "write_file"
        assert skill.description == "Write content to a file"
        assert skill.source_path == "mcp:filesystem"
        assert len(skill.parameters) == 2
        assert "mcp" in skill.tags
        assert "filesystem" in skill.tags

    def test_conversion_preserves_required(self):
        """Test that required flag is preserved."""
        mcp_tool = {
            "name": "test",
            "description": "Test tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "required_param": {"type": "string", "description": "Required"},
                    "optional_param": {"type": "string", "description": "Optional"},
                },
                "required": ["required_param"],
            },
        }

        skill = mcp_tool_to_skill(mcp_tool, "mcp:test")

        req_param = next(p for p in skill.parameters if p.name == "required_param")
        opt_param = next(p for p in skill.parameters if p.name == "optional_param")

        assert req_param.required is True
        assert opt_param.required is False

    def test_conversion_with_defaults(self):
        """Test conversion preserves default values."""
        mcp_tool = {
            "name": "fetch",
            "description": "Fetch data",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout",
                        "default": 30,
                    },
                },
                "required": ["url"],
            },
        }

        skill = mcp_tool_to_skill(mcp_tool, "mcp:http")

        timeout_param = next(p for p in skill.parameters if p.name == "timeout")
        assert timeout_param.default == 30

    def test_conversion_handles_missing_description(self):
        """Test conversion handles missing description."""
        mcp_tool = {
            "name": "simple",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        }

        skill = mcp_tool_to_skill(mcp_tool, "mcp:test")

        assert skill.name == "simple"
        assert skill.description == ""


class TestBatchConversion:
    """Tests for batch conversion functions."""

    def test_skills_to_mcp_tools(self):
        """Test batch skill to MCP tool conversion."""
        skills = [
            SkillDefinition(name="tool1", description="Tool 1"),
            SkillDefinition(name="tool2", description="Tool 2"),
        ]

        tools = skills_to_mcp_tools(skills)

        assert len(tools) == 2
        assert tools[0]["name"] == "tool1"
        assert tools[1]["name"] == "tool2"

    def test_mcp_tools_to_skills(self):
        """Test batch MCP tool to skill conversion."""
        tools = [
            {"name": "tool1", "description": "Tool 1", "inputSchema": {}},
            {"name": "tool2", "description": "Tool 2", "inputSchema": {}},
        ]

        skills = mcp_tools_to_skills(tools, "mcp:batch")

        assert len(skills) == 2
        assert skills[0].name == "tool1"
        assert skills[1].name == "tool2"


# =============================================================================
# Test JSON-RPC Protocol
# =============================================================================


class TestJSONRPCRequest:
    """Tests for JSONRPCRequest."""

    def test_basic_request(self):
        """Test basic request creation."""
        request = JSONRPCRequest(method="test/method", id=1)

        assert request.method == "test/method"
        assert request.id == 1
        assert request.params is None

    def test_request_with_params(self):
        """Test request with parameters."""
        request = JSONRPCRequest(
            method="tools/call",
            params={"name": "read_file", "arguments": {"path": "/test"}},
            id=42,
        )

        assert request.params["name"] == "read_file"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        request = JSONRPCRequest(
            method="initialize",
            params={"version": "1.0"},
            id=1,
        )

        d = request.to_dict()

        assert d["jsonrpc"] == "2.0"
        assert d["method"] == "initialize"
        assert d["params"]["version"] == "1.0"
        assert d["id"] == 1

    def test_to_dict_without_id(self):
        """Test notification (no id)."""
        request = JSONRPCRequest(method="notify")

        d = request.to_dict()

        assert "id" not in d

    def test_to_json(self):
        """Test JSON serialization."""
        request = JSONRPCRequest(method="test", id=1)

        json_str = request.to_json()
        parsed = json.loads(json_str)

        assert parsed["jsonrpc"] == "2.0"
        assert parsed["method"] == "test"


class TestJSONRPCResponse:
    """Tests for JSONRPCResponse."""

    def test_success_response(self):
        """Test successful response."""
        response = JSONRPCResponse(id=1, result={"tools": []})

        assert response.id == 1
        assert response.result == {"tools": []}
        assert response.error is None

    def test_error_response(self):
        """Test error response."""
        response = JSONRPCResponse(
            id=1,
            error={"code": -32600, "message": "Invalid Request"},
        )

        assert response.error is not None
        assert response.error["code"] == -32600

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "jsonrpc": "2.0",
            "id": 42,
            "result": {"status": "ok"},
        }

        response = JSONRPCResponse.from_dict(data)

        assert response.id == 42
        assert response.result["status"] == "ok"

    def test_from_json(self):
        """Test creation from JSON string."""
        json_str = '{"jsonrpc": "2.0", "id": 1, "result": "success"}'

        response = JSONRPCResponse.from_json(json_str)

        assert response.id == 1
        assert response.result == "success"

    def test_raise_for_error_success(self):
        """Test raise_for_error with successful response."""
        response = JSONRPCResponse(id=1, result="ok")

        # Should not raise
        response.raise_for_error()

    def test_raise_for_error_failure(self):
        """Test raise_for_error with error response."""
        response = JSONRPCResponse(
            id=1,
            error={
                "code": -32601,
                "message": "Method not found",
                "data": {"method": "unknown"},
            },
        )

        with pytest.raises(JSONRPCError) as exc_info:
            response.raise_for_error()

        assert exc_info.value.code == -32601
        assert exc_info.value.message == "Method not found"
        assert exc_info.value.data == {"method": "unknown"}


class TestJSONRPCError:
    """Tests for JSONRPCError."""

    def test_error_creation(self):
        """Test error creation."""
        error = JSONRPCError(
            code=-32700,
            message="Parse error",
            data={"position": 42},
            request_id=1,
        )

        assert error.code == -32700
        assert error.message == "Parse error"
        assert error.data["position"] == 42
        assert error.request_id == 1
        assert "Parse error" in str(error)


# =============================================================================
# Test MCPTool and MCPServerInfo
# =============================================================================


class TestMCPTool:
    """Tests for MCPTool dataclass."""

    def test_creation(self):
        """Test MCPTool creation."""
        tool = MCPTool(
            name="read_file",
            description="Read file contents",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            server_name="filesystem",
        )

        assert tool.name == "read_file"
        assert tool.server_name == "filesystem"

    def test_to_skill(self):
        """Test conversion to SkillDefinition."""
        tool = MCPTool(
            name="list_dir",
            description="List directory contents",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path"},
                },
                "required": ["path"],
            },
            server_name="filesystem",
        )

        skill = tool.to_skill()

        assert skill.name == "list_dir"
        assert skill.source_path == "mcp:filesystem"
        assert len(skill.parameters) == 1
        assert skill.parameters[0].name == "path"
        assert skill.parameters[0].required is True


class TestMCPServerInfo:
    """Tests for MCPServerInfo dataclass."""

    def test_creation(self):
        """Test MCPServerInfo creation."""
        info = MCPServerInfo(
            name="test-server",
            version="1.0.0",
            protocol_version="2024-11-05",
            capabilities={"tools": {}},
        )

        assert info.name == "test-server"
        assert info.version == "1.0.0"
        assert info.protocol_version == "2024-11-05"

    def test_default_values(self):
        """Test default values."""
        info = MCPServerInfo(name="minimal", version="0.1")

        assert info.protocol_version == "2024-11-05"
        assert info.capabilities == {}


# =============================================================================
# Test Roundtrip Conversion
# =============================================================================


class TestRoundtripConversion:
    """Tests for roundtrip skill <-> MCP tool conversion."""

    def test_roundtrip_basic(self):
        """Test basic roundtrip conversion."""
        original_skill = SkillDefinition(
            name="calculator",
            description="Perform calculations",
            parameters=[
                SkillParameter(
                    name="expression",
                    type="string",
                    description="Math expression",
                    required=True,
                ),
            ],
        )

        # Convert to MCP and back
        mcp_tool = skill_to_mcp_tool(original_skill)
        converted_skill = mcp_tool_to_skill(mcp_tool, "mcp:test")

        # Verify key properties preserved
        assert converted_skill.name == original_skill.name
        assert converted_skill.description == original_skill.description
        assert len(converted_skill.parameters) == len(original_skill.parameters)
        assert converted_skill.parameters[0].name == original_skill.parameters[0].name
        assert converted_skill.parameters[0].required == original_skill.parameters[0].required

    def test_roundtrip_complex(self):
        """Test roundtrip with complex parameters."""
        original_skill = SkillDefinition(
            name="complex_tool",
            description="A complex tool",
            parameters=[
                SkillParameter(
                    name="config",
                    type="object",
                    description="Configuration",
                    required=True,
                    properties={
                        "name": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                ),
                SkillParameter(
                    name="tags",
                    type="array",
                    description="Tags",
                    required=False,
                    items={"type": "string"},
                    default=[],
                ),
                SkillParameter(
                    name="mode",
                    type="string",
                    description="Mode",
                    required=False,
                    enum=["fast", "slow"],
                    default="fast",
                ),
            ],
        )

        # Convert to MCP and back
        mcp_tool = skill_to_mcp_tool(original_skill)
        converted_skill = mcp_tool_to_skill(mcp_tool, "mcp:test")

        # Verify all parameters preserved
        assert len(converted_skill.parameters) == 3

        config_param = next(p for p in converted_skill.parameters if p.name == "config")
        assert config_param.type == "object"
        assert config_param.properties is not None

        tags_param = next(p for p in converted_skill.parameters if p.name == "tags")
        assert tags_param.type == "array"
        assert tags_param.items is not None

        mode_param = next(p for p in converted_skill.parameters if p.name == "mode")
        assert mode_param.enum == ["fast", "slow"]
        assert mode_param.default == "fast"
