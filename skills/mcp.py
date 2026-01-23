"""MCP (Model Context Protocol) adapter for Nimbus Agent Framework.

This module provides:
1. Conversion between Nimbus SkillDefinition and MCP Tool Schema
2. MCPClient for communicating with MCP Servers (stdio and HTTP transport)
3. MCPToolProvider for dynamically discovering and calling tools from MCP Servers

MCP Protocol Reference:
- JSON-RPC 2.0 based protocol
- Supports stdio and HTTP transports
- Tool discovery via `tools/list` method
- Tool execution via `tools/call` method
"""

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import aiohttp

from .schema import SkillDefinition, SkillParameter

logger = logging.getLogger(__name__)


# =============================================================================
# Conversion Functions
# =============================================================================


def skill_to_mcp_tool(skill: SkillDefinition) -> Dict[str, Any]:
    """Convert Nimbus SkillDefinition to MCP Tool schema.

    MCP Tool Schema follows JSON Schema format similar to OpenAI function calling.

    Args:
        skill: Nimbus skill definition

    Returns:
        MCP-compatible tool definition with the following structure:
        {
            "name": "tool_name",
            "description": "Tool description",
            "inputSchema": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
    """
    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param in skill.parameters:
        prop_schema: Dict[str, Any] = {
            "type": param.type,
            "description": param.description,
        }
        if param.enum:
            prop_schema["enum"] = param.enum
        if param.default is not None:
            prop_schema["default"] = param.default
        if param.items:
            prop_schema["items"] = param.items
        if param.properties:
            prop_schema["properties"] = param.properties

        properties[param.name] = prop_schema

        if param.required:
            required.append(param.name)

    return {
        "name": skill.name,
        "description": skill.description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        }
    }


def mcp_tool_to_skill(tool: Dict[str, Any], source: str) -> SkillDefinition:
    """Convert MCP Tool schema to Nimbus SkillDefinition.

    Args:
        tool: MCP tool definition
        source: Source identifier (e.g., "mcp:filesystem", "mcp:git")

    Returns:
        Nimbus skill definition
    """
    input_schema = tool.get("inputSchema", {})
    properties = input_schema.get("properties", {})
    required_params = set(input_schema.get("required", []))

    parameters: List[SkillParameter] = []
    for name, prop in properties.items():
        param = SkillParameter(
            name=name,
            type=prop.get("type", "string"),
            description=prop.get("description", ""),
            required=name in required_params,
            enum=prop.get("enum"),
            default=prop.get("default"),
            items=prop.get("items"),
            properties=prop.get("properties"),
        )
        parameters.append(param)

    return SkillDefinition(
        name=tool["name"],
        description=tool.get("description", ""),
        parameters=parameters,
        source_path=source,
        tags=["mcp", source.replace("mcp:", "")],
    )


def skills_to_mcp_tools(skills: List[SkillDefinition]) -> List[Dict[str, Any]]:
    """Convert multiple skills to MCP tool format.

    Args:
        skills: List of Nimbus skill definitions

    Returns:
        List of MCP-compatible tool definitions
    """
    return [skill_to_mcp_tool(skill) for skill in skills]


def mcp_tools_to_skills(
    tools: List[Dict[str, Any]], source: str
) -> List[SkillDefinition]:
    """Convert multiple MCP tools to Nimbus skill format.

    Args:
        tools: List of MCP tool definitions
        source: Source identifier

    Returns:
        List of Nimbus skill definitions
    """
    return [mcp_tool_to_skill(tool, source) for tool in tools]


# =============================================================================
# JSON-RPC 2.0 Protocol
# =============================================================================


class JSONRPCError(Exception):
    """JSON-RPC error response."""

    def __init__(
        self,
        code: int,
        message: str,
        data: Optional[Any] = None,
        request_id: Optional[Union[str, int]] = None,
    ):
        self.code = code
        self.message = message
        self.data = data
        self.request_id = request_id
        super().__init__(f"JSON-RPC Error {code}: {message}")


# Standard JSON-RPC 2.0 error codes
class JSONRPCErrorCode(Enum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


@dataclass
class JSONRPCRequest:
    """JSON-RPC 2.0 request object."""

    method: str
    params: Optional[Dict[str, Any]] = None
    id: Optional[Union[str, int]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-RPC request dictionary."""
        request: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": self.method,
        }
        if self.params is not None:
            request["params"] = self.params
        if self.id is not None:
            request["id"] = self.id
        return request

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict())


@dataclass
class JSONRPCResponse:
    """JSON-RPC 2.0 response object."""

    id: Optional[Union[str, int]]
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JSONRPCResponse":
        """Create from dictionary."""
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=data.get("error"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "JSONRPCResponse":
        """Create from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    def raise_for_error(self) -> None:
        """Raise JSONRPCError if response contains an error."""
        if self.error:
            raise JSONRPCError(
                code=self.error.get("code", -1),
                message=self.error.get("message", "Unknown error"),
                data=self.error.get("data"),
                request_id=self.id,
            )


# =============================================================================
# Transport Layer
# =============================================================================


class MCPTransport(ABC):
    """Abstract base class for MCP transport implementations."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection."""
        pass

    @abstractmethod
    async def send_request(
        self, request: JSONRPCRequest, timeout: float = 30.0
    ) -> JSONRPCResponse:
        """Send a request and wait for response."""
        pass

    @abstractmethod
    async def send_notification(self, request: JSONRPCRequest) -> None:
        """Send a notification (no response expected)."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        pass


class StdioTransport(MCPTransport):
    """MCP transport over stdio (subprocess communication).

    This transport spawns a subprocess and communicates via stdin/stdout.
    Each message is a single line of JSON.
    """

    def __init__(
        self,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ):
        """Initialize stdio transport.

        Args:
            command: Command and arguments to spawn (e.g., ["npx", "-y", "@modelcontextprotocol/server-filesystem"])
            env: Environment variables for subprocess
            cwd: Working directory for subprocess
        """
        self.command = command
        self.env = env
        self.cwd = cwd
        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id = 0
        self._pending_requests: Dict[Union[str, int], asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        """Check if subprocess is running."""
        return self._process is not None and self._process.returncode is None

    async def connect(self) -> None:
        """Start the subprocess and begin reading responses."""
        if self.is_connected:
            return

        # Prepare environment
        process_env = dict(os.environ) if self.env is None else self.env.copy()

        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=process_env,
            cwd=self.cwd,
        )

        # Start background reader
        self._reader_task = asyncio.create_task(self._read_responses())

        logger.info(f"MCP stdio transport connected: {' '.join(self.command)}")

    async def disconnect(self) -> None:
        """Stop the subprocess."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._process = None

        # Cancel pending requests
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        logger.info("MCP stdio transport disconnected")

    async def _read_responses(self) -> None:
        """Background task to read responses from subprocess stdout."""
        if not self._process or not self._process.stdout:
            return

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    data = json.loads(line.decode("utf-8").strip())
                    response = JSONRPCResponse.from_dict(data)

                    # Match response to pending request
                    if response.id is not None and response.id in self._pending_requests:
                        future = self._pending_requests.pop(response.id)
                        if not future.done():
                            future.set_result(response)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse JSON-RPC response: {e}")
                except Exception as e:
                    logger.error(f"Error processing response: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Response reader error: {e}")

    def _next_request_id(self) -> int:
        """Generate next request ID."""
        self._request_id += 1
        return self._request_id

    async def send_request(
        self, request: JSONRPCRequest, timeout: float = 30.0
    ) -> JSONRPCResponse:
        """Send a request and wait for response."""
        if not self.is_connected or not self._process or not self._process.stdin:
            raise RuntimeError("Transport not connected")

        async with self._lock:
            # Assign request ID if not set
            if request.id is None:
                request.id = self._next_request_id()

            # Create future for response
            future: asyncio.Future[JSONRPCResponse] = asyncio.get_event_loop().create_future()
            self._pending_requests[request.id] = future

            # Send request
            request_bytes = (request.to_json() + "\n").encode("utf-8")
            self._process.stdin.write(request_bytes)
            await self._process.stdin.drain()

        # Wait for response
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            self._pending_requests.pop(request.id, None)
            raise TimeoutError(f"Request timed out after {timeout}s")

    async def send_notification(self, request: JSONRPCRequest) -> None:
        """Send a notification (no response expected)."""
        if not self.is_connected or not self._process or not self._process.stdin:
            raise RuntimeError("Transport not connected")

        # Notifications must not have an id
        request.id = None

        async with self._lock:
            request_bytes = (request.to_json() + "\n").encode("utf-8")
            self._process.stdin.write(request_bytes)
            await self._process.stdin.drain()


class HTTPTransport(MCPTransport):
    """MCP transport over HTTP.

    This transport communicates with an MCP server via HTTP POST requests.
    Each request is a JSON-RPC 2.0 message.
    """

    def __init__(
        self,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ):
        """Initialize HTTP transport.

        Args:
            base_url: Base URL of the MCP server (e.g., "http://localhost:3000")
            headers: Optional HTTP headers to include in requests
            timeout: Default request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_id = 0
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Check if HTTP session is active."""
        return self._connected and self._session is not None and not self._session.closed

    async def connect(self) -> None:
        """Create HTTP session."""
        if self.is_connected:
            return

        self._session = aiohttp.ClientSession(
            headers={
                "Content-Type": "application/json",
                **self.headers,
            }
        )
        self._connected = True
        logger.info(f"MCP HTTP transport connected: {self.base_url}")

    async def disconnect(self) -> None:
        """Close HTTP session."""
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("MCP HTTP transport disconnected")

    def _next_request_id(self) -> int:
        """Generate next request ID."""
        self._request_id += 1
        return self._request_id

    async def send_request(
        self, request: JSONRPCRequest, timeout: Optional[float] = None
    ) -> JSONRPCResponse:
        """Send a request and wait for response."""
        if not self.is_connected or not self._session:
            raise RuntimeError("Transport not connected")

        # Assign request ID if not set
        if request.id is None:
            request.id = self._next_request_id()

        timeout_val = timeout or self.timeout

        try:
            async with self._session.post(
                f"{self.base_url}/rpc",
                json=request.to_dict(),
                timeout=aiohttp.ClientTimeout(total=timeout_val),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return JSONRPCResponse.from_dict(data)
        except aiohttp.ClientError as e:
            raise RuntimeError(f"HTTP request failed: {e}")
        except asyncio.TimeoutError:
            raise TimeoutError(f"Request timed out after {timeout_val}s")

    async def send_notification(self, request: JSONRPCRequest) -> None:
        """Send a notification (no response expected)."""
        if not self.is_connected or not self._session:
            raise RuntimeError("Transport not connected")

        # Notifications must not have an id
        request.id = None

        try:
            async with self._session.post(
                f"{self.base_url}/rpc",
                json=request.to_dict(),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as resp:
                resp.raise_for_status()
        except aiohttp.ClientError as e:
            logger.warning(f"Notification failed: {e}")


# =============================================================================
# MCP Client
# =============================================================================


@dataclass
class MCPServerInfo:
    """Information about an MCP server."""

    name: str
    version: str
    protocol_version: str = "2024-11-05"
    capabilities: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPTool:
    """MCP tool definition with metadata."""

    name: str
    description: str
    input_schema: Dict[str, Any]
    server_name: str

    def to_skill(self) -> SkillDefinition:
        """Convert to Nimbus SkillDefinition."""
        return mcp_tool_to_skill(
            {
                "name": self.name,
                "description": self.description,
                "inputSchema": self.input_schema,
            },
            source=f"mcp:{self.server_name}",
        )


class MCPClient:
    """Client for communicating with MCP servers.

    Supports both stdio and HTTP transports.
    Provides methods for:
    - Initializing connection
    - Listing available tools
    - Calling tools
    """

    def __init__(
        self,
        name: str,
        transport: MCPTransport,
        client_info: Optional[Dict[str, str]] = None,
    ):
        """Initialize MCP client.

        Args:
            name: Name to identify this client
            transport: Transport implementation (stdio or HTTP)
            client_info: Optional client information to send during initialization
        """
        self.name = name
        self.transport = transport
        self.client_info = client_info or {
            "name": "nimbus-agent",
            "version": "0.1.0",
        }
        self.server_info: Optional[MCPServerInfo] = None
        self._tools: Dict[str, MCPTool] = {}
        self._initialized = False

    @property
    def is_connected(self) -> bool:
        """Check if client is connected and initialized."""
        return self.transport.is_connected and self._initialized

    @property
    def tools(self) -> Dict[str, MCPTool]:
        """Get discovered tools."""
        return self._tools

    async def connect(self) -> MCPServerInfo:
        """Connect to MCP server and initialize.

        Returns:
            Server information

        Raises:
            RuntimeError: If connection or initialization fails
        """
        # Connect transport
        await self.transport.connect()

        # Send initialize request
        request = JSONRPCRequest(
            method="initialize",
            params={
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "clientInfo": self.client_info,
            },
        )

        response = await self.transport.send_request(request)
        response.raise_for_error()

        result = response.result or {}
        self.server_info = MCPServerInfo(
            name=result.get("serverInfo", {}).get("name", "unknown"),
            version=result.get("serverInfo", {}).get("version", "unknown"),
            protocol_version=result.get("protocolVersion", "2024-11-05"),
            capabilities=result.get("capabilities", {}),
        )

        # Send initialized notification
        await self.transport.send_notification(
            JSONRPCRequest(method="notifications/initialized")
        )

        self._initialized = True
        logger.info(
            f"MCP client initialized: {self.server_info.name} v{self.server_info.version}"
        )

        return self.server_info

    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        self._initialized = False
        self._tools.clear()
        self.server_info = None
        await self.transport.disconnect()

    async def list_tools(self, refresh: bool = False) -> List[MCPTool]:
        """List available tools from the server.

        Args:
            refresh: Force refresh from server even if cached

        Returns:
            List of available tools
        """
        if not self.is_connected:
            raise RuntimeError("Client not connected")

        if self._tools and not refresh:
            return list(self._tools.values())

        request = JSONRPCRequest(method="tools/list")
        response = await self.transport.send_request(request)
        response.raise_for_error()

        result = response.result or {}
        tools_data = result.get("tools", [])

        self._tools.clear()
        for tool_data in tools_data:
            tool = MCPTool(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
                server_name=self.name,
            )
            self._tools[tool.name] = tool

        logger.debug(f"Discovered {len(self._tools)} tools from {self.name}")
        return list(self._tools.values())

    async def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> Any:
        """Call a tool on the server.

        Args:
            name: Tool name
            arguments: Tool arguments
            timeout: Request timeout in seconds

        Returns:
            Tool execution result

        Raises:
            RuntimeError: If client not connected
            JSONRPCError: If tool execution fails
        """
        if not self.is_connected:
            raise RuntimeError("Client not connected")

        request = JSONRPCRequest(
            method="tools/call",
            params={
                "name": name,
                "arguments": arguments or {},
            },
        )

        response = await self.transport.send_request(request, timeout=timeout)
        response.raise_for_error()

        result = response.result or {}

        # MCP tool results have a specific format
        content = result.get("content", [])
        is_error = result.get("isError", False)

        if is_error:
            # Extract error message from content
            error_text = ""
            for item in content:
                if item.get("type") == "text":
                    error_text += item.get("text", "")
            raise RuntimeError(f"Tool execution failed: {error_text}")

        # Extract text content for simple results
        if len(content) == 1 and content[0].get("type") == "text":
            return content[0].get("text", "")

        return content

    async def __aenter__(self) -> "MCPClient":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()


# =============================================================================
# MCP Tool Provider
# =============================================================================


class MCPToolProvider:
    """Provider for dynamically discovering and calling tools from MCP servers.

    This class manages multiple MCP server connections and provides a unified
    interface for tool discovery and execution.
    """

    def __init__(self):
        """Initialize MCP tool provider."""
        self._clients: Dict[str, MCPClient] = {}
        self._tool_to_client: Dict[str, str] = {}

    @property
    def servers(self) -> List[str]:
        """Get list of connected server names."""
        return list(self._clients.keys())

    @property
    def tools(self) -> Dict[str, MCPTool]:
        """Get all available tools from all servers."""
        all_tools: Dict[str, MCPTool] = {}
        for client in self._clients.values():
            all_tools.update(client.tools)
        return all_tools

    async def add_stdio_server(
        self,
        name: str,
        command: List[str],
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> MCPServerInfo:
        """Add an MCP server via stdio transport.

        Args:
            name: Server name for identification
            command: Command to spawn server process
            env: Environment variables
            cwd: Working directory

        Returns:
            Server information
        """
        if name in self._clients:
            raise ValueError(f"Server '{name}' already exists")

        transport = StdioTransport(command=command, env=env, cwd=cwd)
        client = MCPClient(name=name, transport=transport)

        server_info = await client.connect()
        self._clients[name] = client

        # Discover tools
        tools = await client.list_tools()
        for tool in tools:
            self._tool_to_client[tool.name] = name

        logger.info(f"Added MCP server '{name}' with {len(tools)} tools")
        return server_info

    async def add_http_server(
        self,
        name: str,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> MCPServerInfo:
        """Add an MCP server via HTTP transport.

        Args:
            name: Server name for identification
            base_url: Server base URL
            headers: Optional HTTP headers

        Returns:
            Server information
        """
        if name in self._clients:
            raise ValueError(f"Server '{name}' already exists")

        transport = HTTPTransport(base_url=base_url, headers=headers)
        client = MCPClient(name=name, transport=transport)

        server_info = await client.connect()
        self._clients[name] = client

        # Discover tools
        tools = await client.list_tools()
        for tool in tools:
            self._tool_to_client[tool.name] = name

        logger.info(f"Added MCP server '{name}' with {len(tools)} tools")
        return server_info

    async def remove_server(self, name: str) -> None:
        """Remove an MCP server.

        Args:
            name: Server name to remove
        """
        if name not in self._clients:
            return

        client = self._clients.pop(name)
        await client.disconnect()

        # Remove tool mappings
        self._tool_to_client = {
            tool: server
            for tool, server in self._tool_to_client.items()
            if server != name
        }

        logger.info(f"Removed MCP server '{name}'")

    async def refresh_tools(self, server_name: Optional[str] = None) -> List[MCPTool]:
        """Refresh tool list from server(s).

        Args:
            server_name: Specific server to refresh, or None for all

        Returns:
            List of all available tools
        """
        if server_name:
            if server_name not in self._clients:
                raise ValueError(f"Unknown server: {server_name}")
            clients = [self._clients[server_name]]
        else:
            clients = list(self._clients.values())

        all_tools: List[MCPTool] = []
        for client in clients:
            tools = await client.list_tools(refresh=True)
            for tool in tools:
                self._tool_to_client[tool.name] = client.name
            all_tools.extend(tools)

        return all_tools

    async def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> Any:
        """Call a tool by name.

        Args:
            name: Tool name
            arguments: Tool arguments
            timeout: Request timeout

        Returns:
            Tool execution result

        Raises:
            ValueError: If tool not found
            RuntimeError: If tool execution fails
        """
        if name not in self._tool_to_client:
            raise ValueError(f"Unknown tool: {name}")

        server_name = self._tool_to_client[name]
        client = self._clients[server_name]

        return await client.call_tool(name, arguments, timeout)

    def get_skill(self, tool_name: str) -> Optional[SkillDefinition]:
        """Get a Nimbus SkillDefinition for a tool.

        Args:
            tool_name: Tool name

        Returns:
            SkillDefinition or None if not found
        """
        if tool_name not in self._tool_to_client:
            return None

        server_name = self._tool_to_client[tool_name]
        client = self._clients[server_name]
        tool = client.tools.get(tool_name)

        return tool.to_skill() if tool else None

    def get_all_skills(self) -> List[SkillDefinition]:
        """Get all tools as Nimbus SkillDefinitions.

        Returns:
            List of SkillDefinitions
        """
        skills: List[SkillDefinition] = []
        for tool in self.tools.values():
            skills.append(tool.to_skill())
        return skills

    def get_server_status(self) -> List[Dict[str, Any]]:
        """Get status of all connected servers.

        Returns:
            List of server status dictionaries
        """
        status_list = []
        for name, client in self._clients.items():
            status = {
                "name": name,
                "status": "connected" if client.is_connected else "disconnected",
                "tools": list(client.tools.keys()),
            }
            if client.server_info:
                status["version"] = client.server_info.version
                status["protocol_version"] = client.server_info.protocol_version
            status_list.append(status)
        return status_list

    async def close(self) -> None:
        """Close all server connections."""
        for name in list(self._clients.keys()):
            await self.remove_server(name)

    async def __aenter__(self) -> "MCPToolProvider":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()


# =============================================================================
# Convenience Functions
# =============================================================================


async def create_mcp_client_stdio(
    name: str,
    command: List[str],
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
) -> MCPClient:
    """Create and connect an MCP client with stdio transport.

    Args:
        name: Client name
        command: Server command
        env: Environment variables
        cwd: Working directory

    Returns:
        Connected MCPClient
    """
    transport = StdioTransport(command=command, env=env, cwd=cwd)
    client = MCPClient(name=name, transport=transport)
    await client.connect()
    return client


async def create_mcp_client_http(
    name: str,
    base_url: str,
    headers: Optional[Dict[str, str]] = None,
) -> MCPClient:
    """Create and connect an MCP client with HTTP transport.

    Args:
        name: Client name
        base_url: Server URL
        headers: HTTP headers

    Returns:
        Connected MCPClient
    """
    transport = HTTPTransport(base_url=base_url, headers=headers)
    client = MCPClient(name=name, transport=transport)
    await client.connect()
    return client
