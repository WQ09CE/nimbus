"""Nimbus ACP (Agent Client Protocol) module.

This module implements the ACP protocol to enable Nimbus to work with
ACP-compatible clients like Toad TUI.
"""

from .types import *
from .events import (
    ACPEventConverter,
    get_converter,
    convert_event,
)
from .session import ACPSessionState, ACPSessionManager, get_session_manager
from .permission import (
    PendingPermission,
    ACPPermissionHandler,
    get_permission_handler,
    reset_permission_handler,
)
from .jsonrpc import (
    # Error codes
    PARSE_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    INVALID_PARAMS,
    INTERNAL_ERROR,
    # Exceptions
    JSONRPCError,
    ParseError,
    InvalidRequest,
    MethodNotFound,
    InvalidParams,
    InternalError,
    # Data classes
    Request,
    Response,
    # Handler
    JSONRPCHandler,
    # Connection
    Connection,
    StdioConnection,
    create_stdio_connection,
    # Server and Client
    JSONRPCServer,
    JSONRPCClient,
)
from .adapter import ACPConfig, NimbusACPAdapter, NimbusSession
from .agent import NimbusACPAgent, create_acp_agent, PROTOCOL_VERSION
from .server import ACPServer, run_server, run_server_async

__all__ = [
    # ACP Server
    "ACPServer",
    "run_server",
    "run_server_async",
    # ACP Agent and Adapter
    "ACPConfig",
    "NimbusACPAdapter",
    "NimbusSession",
    "NimbusACPAgent",
    "create_acp_agent",
    "PROTOCOL_VERSION",
    # Event conversion
    "ACPEventConverter",
    "get_converter",
    "convert_event",
    # Session management
    "ACPSessionState",
    "ACPSessionManager",
    "get_session_manager",
    # Permission handling
    "PendingPermission",
    "ACPPermissionHandler",
    "get_permission_handler",
    "reset_permission_handler",
    # JSON-RPC Error codes
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    # JSON-RPC Exceptions
    "JSONRPCError",
    "ParseError",
    "InvalidRequest",
    "MethodNotFound",
    "InvalidParams",
    "InternalError",
    # JSON-RPC Data classes
    "Request",
    "Response",
    # JSON-RPC Handler
    "JSONRPCHandler",
    # JSON-RPC Connection
    "Connection",
    "StdioConnection",
    "create_stdio_connection",
    # JSON-RPC Server and Client
    "JSONRPCServer",
    "JSONRPCClient",
    # Types below
    # Capabilities
    "FileSystemCapability",
    "ClientCapabilities",
    "PromptCapabilities",
    "AgentCapabilities",
    # Implementation
    "Implementation",
    "AuthMethod",
    # Environment and Terminal
    "EnvVariable",
    "TerminalExitStatus",
    "McpServer",
    # Content types
    "Annotations",
    "TextContent",
    "ImageContent",
    "AudioContent",
    "EmbeddedResourceText",
    "EmbeddedResourceBlob",
    "EmbeddedResourceContent",
    "ResourceLinkContent",
    "ContentBlock",
    # Session updates
    "UserMessageChunk",
    "AgentMessageChunk",
    "AgentThoughtChunk",
    "SessionUpdate",
    "SessionNotification",
    # Tool calls
    "ToolCallContentContent",
    "ToolCallContentDiff",
    "ToolCallContentTerminal",
    "ToolCallContent",
    "ToolKind",
    "ToolCallStatus",
    "ToolCallLocation",
    "ToolCallId",
    "ToolCall",
    "ToolCallUpdate",
    "ToolCallUpdatePermissionRequest",
    # Plan
    "PlanEntry",
    "Plan",
    # Session modes and models
    "SessionModeId",
    "SessionMode",
    "SessionModeState",
    "ModelId",
    "ModelInfo",
    "SessionModelState",
    # Commands
    "AvailableCommandInput",
    "AvailableCommand",
    "AvailableCommandsUpdate",
    "CurrentModeUpdate",
    # Permissions
    "PermissionOptionKind",
    "PermissionOptionId",
    "PermissionOption",
    "OutcomeCancelled",
    "OutcomeSelected",
    "RequestPermissionOutcome",
    # RPC Responses
    "InitializeResponse",
    "NewSessionResponse",
    "SessionPromptResponse",
    "RequestPermissionResponse",
    "CreateTerminalResponse",
    "KillTerminalCommandResponse",
    "TerminalOutputResponse",
    "ReleaseTerminalResponse",
    "WaitForTerminalExitResponse",
    "SetSessionModeResponse",
]
