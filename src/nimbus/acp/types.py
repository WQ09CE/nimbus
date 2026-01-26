"""Nimbus ACP Protocol Type Definitions.

This module defines all types required for the ACP (Agent Client Protocol).
These types are compatible with Toad's implementation and follow the ACP
protocol specification at https://agentclientprotocol.com/protocol/schema
"""

from typing import Literal, Required, TypeAlias, TypedDict, Union


class SchemaDict(TypedDict, total=False):
    """Base class for all ACP schema types."""

    pass


# ---------------------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------------------


class FileSystemCapability(SchemaDict, total=False):
    """File system capabilities that a client supports."""

    readTextFile: bool
    writeTextFile: bool


# https://agentclientprotocol.com/protocol/schema#clientcapabilities
class ClientCapabilities(SchemaDict, total=False):
    """Capabilities that an ACP client supports."""

    fs: FileSystemCapability
    terminal: bool


# https://agentclientprotocol.com/protocol/schema#implementation
class Implementation(SchemaDict, total=False):
    """Information about an ACP implementation (client or agent)."""

    name: Required[str]
    title: str | None
    version: Required[str]


# https://agentclientprotocol.com/protocol/schema#promptcapabilities
class PromptCapabilities(SchemaDict, total=False):
    """Prompt content capabilities that an agent supports."""

    audio: bool
    embeddedContent: bool
    image: bool


# https://agentclientprotocol.com/protocol/schema#agentcapabilities
class AgentCapabilities(SchemaDict, total=False):
    """Capabilities that an ACP agent supports."""

    loadSession: bool
    promptCapabilities: PromptCapabilities


class AuthMethod(SchemaDict, total=False):
    """An authentication method supported by the agent."""

    description: str | None
    id: Required[str]
    name: Required[str]


# ---------------------------------------------------------------------------------------
# Environment and Terminal
# ---------------------------------------------------------------------------------------


# https://agentclientprotocol.com/protocol/schema#envvariable
class EnvVariable(SchemaDict, total=False):
    """An environment variable."""

    _meta: dict
    name: Required[str]
    value: Required[str]


# https://agentclientprotocol.com/protocol/schema#terminalexitstatus
class TerminalExitStatus(SchemaDict, total=False):
    """Exit status of a terminal process."""

    _meta: dict
    exitCode: int | None
    signal: str | None


# https://agentclientprotocol.com/protocol/schema#mcpserver
class McpServer(SchemaDict, total=False):
    """Configuration for an MCP server."""

    args: list[str]
    command: str
    env: list[EnvVariable]
    name: str


# ---------------------------------------------------------------------------------------
# Content Types
# ---------------------------------------------------------------------------------------


# https://modelcontextprotocol.io/specification/2025-06-18/server/resources#annotations
class Annotations(SchemaDict, total=False):
    """Annotations for content blocks."""

    audience: list[str]
    priority: float
    lastModified: str


class TextContent(SchemaDict, total=False):
    """Text content block."""

    type: Required[str]  # Should be "text"
    text: Required[str]
    annotations: Annotations


class ImageContent(SchemaDict, total=False):
    """Image content block."""

    type: Required[str]  # Should be "image"
    data: Required[str]
    mimeType: Required[str]
    url: str
    annotations: Annotations


class AudioContent(SchemaDict, total=False):
    """Audio content block."""

    type: Required[str]  # Should be "audio"
    data: Required[str]
    mimeType: Required[str]
    annotations: Annotations


class EmbeddedResourceText(SchemaDict, total=False):
    """Embedded text resource."""

    uri: Required[str]
    text: Required[str]
    mimeType: str


class EmbeddedResourceBlob(SchemaDict, total=False):
    """Embedded blob resource."""

    uri: Required[str]
    blob: Required[str]
    mimeType: str


# https://agentclientprotocol.com/protocol/content#embedded-resource
class EmbeddedResourceContent(SchemaDict, total=False):
    """Embedded resource content block."""

    type: Required[str]  # Should be "resource"
    resource: EmbeddedResourceText | EmbeddedResourceBlob


class ResourceLinkContent(SchemaDict, total=False):
    """Resource link content block."""

    annotations: Annotations | None
    description: str | None
    mimeType: str | None
    name: Required[str]
    size: int | None
    title: str | None
    type: Required[str]  # Should be "resource_link"
    uri: Required[str]


# https://agentclientprotocol.com/protocol/schema#contentblock
ContentBlock: TypeAlias = Union[
    TextContent,
    ImageContent,
    AudioContent,
    EmbeddedResourceContent,
    ResourceLinkContent,
]


# ---------------------------------------------------------------------------------------
# Session Updates
# ---------------------------------------------------------------------------------------


# https://agentclientprotocol.com/protocol/schema#param-user-message-chunk
class UserMessageChunk(SchemaDict, total=False):
    """A chunk of user message content."""

    content: Required[ContentBlock]
    sessionUpdate: Required[Literal["user_message_chunk"]]


class AgentMessageChunk(SchemaDict, total=False):
    """A chunk of agent message content."""

    content: Required[ContentBlock]
    sessionUpdate: Required[Literal["agent_message_chunk"]]


class AgentThoughtChunk(SchemaDict, total=False):
    """A chunk of agent thought/reasoning content."""

    content: Required[ContentBlock]
    sessionUpdate: Required[Literal["agent_thought_chunk"]]


# ---------------------------------------------------------------------------------------
# Tool Calls
# ---------------------------------------------------------------------------------------


class ToolCallContentContent(SchemaDict, total=False):
    """Tool call content that contains a content block."""

    content: Required[ContentBlock]
    type: Required[Literal["content"]]


# https://agentclientprotocol.com/protocol/schema#param-diff
class ToolCallContentDiff(SchemaDict, total=False):
    """Tool call content that represents a file diff."""

    newText: Required[str]
    oldText: str | None
    path: Required[str]
    type: Required[Literal["diff"]]


class ToolCallContentTerminal(SchemaDict, total=False):
    """Tool call content that references a terminal."""

    terminalId: Required[str]
    type: Required[Literal["terminal"]]


# https://agentclientprotocol.com/protocol/schema#toolcallcontent
ToolCallContent: TypeAlias = Union[
    ToolCallContentContent, ToolCallContentDiff, ToolCallContentTerminal
]


# https://agentclientprotocol.com/protocol/schema#toolkind
ToolKind: TypeAlias = Literal[
    "read",
    "edit",
    "delete",
    "move",
    "search",
    "execute",
    "think",
    "fetch",
    "switch_mode",
    "other",
]

ToolCallStatus: TypeAlias = Literal["pending", "in_progress", "completed", "failed"]


class ToolCallLocation(SchemaDict, total=False):
    """A location referenced by a tool call."""

    line: int | None
    path: Required[str]


ToolCallId: TypeAlias = str


# https://agentclientprotocol.com/protocol/schema#toolcall
class ToolCall(SchemaDict, total=False):
    """A tool call initiated by the agent."""

    _meta: dict
    content: list[ToolCallContent]
    kind: ToolKind
    locations: list[ToolCallLocation]
    rawInput: dict
    rawOutput: dict
    sessionUpdate: Required[Literal["tool_call"]]
    status: ToolCallStatus
    title: Required[str]
    toolCallId: Required[ToolCallId]


# https://agentclientprotocol.com/protocol/schema#toolcallupdate
class ToolCallUpdate(SchemaDict, total=False):
    """An update to an existing tool call."""

    _meta: dict
    content: list[ToolCallContent] | None
    kind: ToolKind | None
    locations: list[ToolCallLocation] | None
    rawInput: dict
    rawOutput: dict
    sessionUpdate: Required[Literal["tool_call_update"]]
    status: ToolCallStatus | None
    title: str | None
    toolCallId: Required[ToolCallId]


# https://agentclientprotocol.com/protocol/schema#param-tool-call
# Used in the session/request_permission call (not the same as ToolCallUpdate)
class ToolCallUpdatePermissionRequest(SchemaDict, total=False):
    """Tool call update for permission requests."""

    _meta: dict
    content: list[ToolCallContent] | None
    kind: ToolKind | None
    locations: list[ToolCallLocation] | None
    rawInput: dict
    rawOutput: dict
    status: ToolCallStatus | None
    title: str | None
    toolCallId: Required[ToolCallId]


# ---------------------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------------------


class PlanEntry(SchemaDict, total=False):
    """An entry in an agent's plan."""

    content: Required[str]
    priority: Literal["high", "medium", "low"]
    status: Literal["pending", "in_progress", "completed"]


# https://agentclientprotocol.com/protocol/schema#param-plan
class Plan(SchemaDict, total=False):
    """A plan containing multiple entries."""

    entries: Required[list[PlanEntry]]
    sessionUpdate: Required[Literal["plan"]]


# ---------------------------------------------------------------------------------------
# Session Modes and Models
# ---------------------------------------------------------------------------------------


SessionModeId: TypeAlias = str


# https://agentclientprotocol.com/protocol/schema#sessionmode
class SessionMode(SchemaDict, total=False):
    """A session mode that the agent supports."""

    _meta: dict
    description: str | None
    id: Required[SessionModeId]
    name: Required[str]


class SessionModeState(SchemaDict, total=False):
    """Current state of session modes."""

    _meta: dict
    availableModes: Required[list[SessionMode]]
    currentModeId: Required[SessionModeId]


ModelId: TypeAlias = str


# https://agentclientprotocol.com/protocol/schema#modelinfo
class ModelInfo(SchemaDict, total=False):
    """Information about an available model."""

    _meta: dict
    description: str | None
    modelId: Required[ModelId]
    name: Required[str]


# https://agentclientprotocol.com/protocol/schema#sessionmodelstate
class SessionModelState(SchemaDict, total=False):
    """Current state of session models."""

    _meta: dict
    availableModels: Required[list[ModelInfo]]
    currentModelId: Required[ModelId]


# ---------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------


class AvailableCommandInput(SchemaDict, total=False):
    """Input hint for an available command."""

    hint: Required[str]


class AvailableCommand(SchemaDict, total=False):
    """A command available in the current session."""

    description: Required[str]
    input: AvailableCommandInput | None
    name: Required[str]


class AvailableCommandsUpdate(SchemaDict, total=False):
    """Update to available commands."""

    availableCommands: Required[list[AvailableCommand]]
    sessionUpdate: Required[Literal["available_commands_update"]]


class CurrentModeUpdate(SchemaDict, total=False):
    """Update to the current mode."""

    currentModeId: Required[str]
    sessionUpdate: Required[Literal["current_mode_update"]]


# ---------------------------------------------------------------------------------------
# Session Update Union
# ---------------------------------------------------------------------------------------


SessionUpdate: TypeAlias = Union[
    UserMessageChunk,
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCall,
    ToolCallUpdate,
    Plan,
    AvailableCommandsUpdate,
    CurrentModeUpdate,
]


class SessionNotification(TypedDict, total=False):
    """A notification about a session update."""

    sessionId: str
    update: SessionUpdate


# ---------------------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------------------


PermissionOptionKind: TypeAlias = Literal[
    "allow_once", "allow_always", "reject_once", "reject_always"
]
PermissionOptionId: TypeAlias = str


class PermissionOption(TypedDict, total=False):
    """A permission option that can be selected."""

    _meta: dict
    kind: Required[PermissionOptionKind]
    name: Required[str]
    optionId: Required[PermissionOptionId]


class OutcomeCancelled(TypedDict, total=False):
    """Permission request was cancelled."""

    outcome: Literal["cancelled"]


class OutcomeSelected(TypedDict, total=False):
    """A permission option was selected."""

    optionId: Required[PermissionOptionId]
    outcome: Literal["selected"]


# https://agentclientprotocol.com/protocol/schema#requestpermissionoutcome
RequestPermissionOutcome: TypeAlias = Union[OutcomeSelected, OutcomeCancelled]


# ---------------------------------------------------------------------------------------
# RPC Responses
# ---------------------------------------------------------------------------------------


class InitializeResponse(SchemaDict, total=False):
    """Response to initialize request."""

    agentCapabilities: AgentCapabilities
    authMethods: list[AuthMethod]
    protocolVersion: Required[int]


# https://agentclientprotocol.com/protocol/schema#newsessionresponse
class NewSessionResponse(SchemaDict, total=False):
    """Response to new session request."""

    _meta: object
    sessionId: Required[str]
    # Unstable from here
    models: SessionModelState | None
    modes: SessionModeState | None


class SessionPromptResponse(SchemaDict, total=False):
    """Response to session prompt request."""

    stopReason: Required[
        Literal[
            "end_turn",
            "max_tokens",
            "max_turn_requests",
            "refusal",
            "cancelled",
        ]
    ]


# https://agentclientprotocol.com/protocol/schema#requestpermissionresponse
class RequestPermissionResponse(TypedDict, total=False):
    """Response to request permission request."""

    _meta: dict
    outcome: Required[RequestPermissionOutcome]


# https://agentclientprotocol.com/protocol/schema#createterminalresponse
class CreateTerminalResponse(TypedDict, total=False):
    """Response to create terminal request."""

    _meta: dict
    terminalId: Required[str]


# https://agentclientprotocol.com/protocol/schema#killterminalcommandresponse
class KillTerminalCommandResponse(TypedDict, total=False):
    """Response to kill terminal command request."""

    _meta: dict


# https://agentclientprotocol.com/protocol/schema#terminaloutputresponse
class TerminalOutputResponse(TypedDict, total=False):
    """Response containing terminal output."""

    _meta: dict
    exitStatus: TerminalExitStatus | None
    output: Required[str]
    truncated: Required[bool]


# https://agentclientprotocol.com/protocol/schema#releaseterminalresponse
class ReleaseTerminalResponse(TypedDict, total=False):
    """Response to release terminal request."""

    _meta: dict


# https://agentclientprotocol.com/protocol/schema#waitforterminalexitresponse
class WaitForTerminalExitResponse(TypedDict, total=False):
    """Response to wait for terminal exit request."""

    _meta: dict
    exitCode: int | None
    signal: str | None


# https://agentclientprotocol.com/protocol/schema#setsessionmoderesponse
class SetSessionModeResponse(TypedDict, total=False):
    """Response to set session mode request."""

    _meta: dict


# Export all types for star import
__all__ = [
    # Base
    "SchemaDict",
    # Capabilities
    "FileSystemCapability",
    "ClientCapabilities",
    "Implementation",
    "PromptCapabilities",
    "AgentCapabilities",
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
    # Session update union
    "SessionUpdate",
    "SessionNotification",
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
