"""Nimbus ACP Agent - Main ACP protocol handler.

This module implements the ACP (Agent Client Protocol) agent interface
for Nimbus. It handles JSON-RPC method routing and coordinates with
the adapter to execute operations.

Protocol specification: https://agentclientprotocol.com/overview/introduction
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable

from .adapter import ACPConfig, NimbusACPAdapter
from .jsonrpc import (
    JSONRPCHandler,
    JSONRPCServer,
    JSONRPCClient,
    Connection,
    StdioConnection,
    create_stdio_connection,
    InvalidParams,
    MethodNotFound,
)
from .types import (
    AgentCapabilities,
    ContentBlock,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PermissionOption,
    RequestPermissionOutcome,
    RequestPermissionResponse,
    SessionPromptResponse,
    SessionUpdate,
    SetSessionModeResponse,
    TextContent,
)

log = logging.getLogger("nimbus.acp.agent")

# ACP Protocol version supported by this implementation
PROTOCOL_VERSION = 1

# Nimbus agent implementation info
AGENT_INFO: Implementation = {
    "name": "nimbus",
    "title": "Nimbus Agent",
    "version": "0.1.0",
}

# Nimbus agent capabilities
AGENT_CAPABILITIES: AgentCapabilities = {
    "loadSession": False,
    "promptCapabilities": {
        "audio": False,
        "embeddedContent": True,
        "image": False,
    },
}


class NimbusACPAgent:
    """Nimbus implementation of ACP Agent.

    This class implements the ACP protocol methods and coordinates with
    the NimbusACPAdapter for actual execution. It handles:
    - Protocol initialization handshake
    - Session lifecycle (create, prompt, cancel)
    - Mode management
    - Sending session updates back to the client

    Attributes:
        config: Agent configuration.
        adapter: The adapter that maps ACP to Nimbus operations.
        handler: JSON-RPC method handler.
        connection: Optional connection for sending notifications.
        client: Optional JSON-RPC client for agent->client requests.
    """

    def __init__(self, config: ACPConfig | None = None) -> None:
        """Initialize the ACP agent.

        Args:
            config: Optional configuration. Uses defaults if not provided.
        """
        self.config = config or ACPConfig()
        self.adapter = NimbusACPAdapter(self.config)
        self.handler = JSONRPCHandler()
        self.connection: Connection | None = None
        self.client: JSONRPCClient | None = None

        # Client capabilities received during initialize
        self._client_capabilities: dict[str, Any] = {}
        self._client_info: Implementation | None = None
        self._initialized: bool = False

        # Pending permission requests
        self._permission_futures: dict[str, asyncio.Future[RequestPermissionOutcome]] = {}

        self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Register all ACP method handlers with the JSON-RPC handler."""
        # Core protocol methods
        self.handler.method("initialize")(self._handle_initialize)

        # Session methods
        self.handler.method("session/new")(self._handle_session_new)
        self.handler.method("session/prompt")(self._handle_session_prompt)
        self.handler.method("session/set_mode")(self._handle_session_set_mode)

        # Session notifications (no response)
        self.handler.notification("session/cancel")(self._handle_session_cancel)

        log.debug("ACP method handlers registered")

    async def _handle_initialize(
        self,
        protocolVersion: int,
        clientCapabilities: dict[str, Any] | None = None,
        clientInfo: dict[str, Any] | None = None,
    ) -> InitializeResponse:
        """Handle initialize request from client.

        This is the first message in the ACP protocol handshake. The client
        sends its capabilities and version, and we respond with ours.

        Args:
            protocolVersion: Protocol version the client supports.
            clientCapabilities: Client's capabilities (fs, terminal, etc).
            clientInfo: Client implementation info.

        Returns:
            InitializeResponse with our capabilities and version.

        Raises:
            InvalidParams: If protocol version is incompatible.
        """
        log.info(
            f"Initialize request: version={protocolVersion}, "
            f"client={clientInfo.get('name') if clientInfo else 'unknown'}"
        )

        # Validate protocol version
        if protocolVersion < PROTOCOL_VERSION:
            raise InvalidParams(
                f"Protocol version {protocolVersion} not supported. "
                f"Minimum required: {PROTOCOL_VERSION}"
            )

        # Store client info
        self._client_capabilities = clientCapabilities or {}
        self._client_info = clientInfo  # type: ignore
        self._initialized = True

        response: InitializeResponse = {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": AGENT_CAPABILITIES,
        }

        log.info("Initialize complete")
        return response

    async def _handle_session_new(
        self,
        cwd: str,
        mcpServers: list[dict[str, Any]] | None = None,
        _meta: dict[str, Any] | None = None,
    ) -> NewSessionResponse:
        """Handle session/new request.

        Creates a new Nimbus session with a CodeAgent instance.

        Args:
            cwd: Working directory for the session.
            mcpServers: Optional MCP server configurations.
            _meta: Optional metadata.

        Returns:
            NewSessionResponse with session ID and initial state.
        """
        log.info(f"Creating new session with cwd={cwd}")

        session_id, session_info = await self.adapter.create_session(
            cwd=cwd,
            mcp_servers=mcpServers,
        )

        response: NewSessionResponse = {
            "sessionId": session_id,
            "models": session_info.get("models"),
            "modes": session_info.get("modes"),
        }

        log.info(f"Session created: {session_id}")
        return response

    async def _handle_session_prompt(
        self,
        sessionId: str,
        prompt: list[dict[str, Any]],
        _meta: dict[str, Any] | None = None,
    ) -> SessionPromptResponse:
        """Handle session/prompt request.

        Executes a prompt in the specified session. During execution,
        sends session/update notifications for progress.

        Args:
            sessionId: The session ID.
            prompt: List of content blocks forming the prompt.
            _meta: Optional metadata.

        Returns:
            SessionPromptResponse with stop reason.

        Raises:
            InvalidParams: If session not found or prompt invalid.
        """
        log.info(f"Session {sessionId} prompt received")

        # Extract text content from prompt
        text_content = self._extract_prompt_text(prompt)
        if not text_content:
            raise InvalidParams("No text content in prompt")

        log.debug(f"Prompt text: {text_content[:100]}...")

        # Run the prompt and stream updates
        stop_reason = "end_turn"
        try:
            async for update in self.adapter.run_prompt(sessionId, text_content):
                await self.send_session_update(sessionId, update)

        except ValueError as e:
            log.error(f"Session prompt error: {e}")
            raise InvalidParams(str(e))

        except asyncio.CancelledError:
            log.info(f"Session {sessionId} prompt cancelled")
            stop_reason = "cancelled"

        except Exception as e:
            log.exception(f"Unexpected error in session {sessionId}: {e}")
            # Send error as update before returning
            await self.send_session_update(
                sessionId,
                {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": f"Error: {str(e)}"},
                },
            )
            stop_reason = "end_turn"

        response: SessionPromptResponse = {
            "stopReason": stop_reason,
        }

        log.info(f"Session {sessionId} prompt complete: {stop_reason}")
        return response

    def _extract_prompt_text(self, prompt: list[dict[str, Any]]) -> str:
        """Extract text content from prompt content blocks.

        Args:
            prompt: List of content block dictionaries.

        Returns:
            Combined text content.
        """
        text_parts: list[str] = []

        for block in prompt:
            if block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    text_parts.append(text)
            elif block.get("type") == "resource":
                # Handle embedded resources
                resource = block.get("resource", {})
                if "text" in resource:
                    text_parts.append(resource["text"])

        return "\n".join(text_parts)

    async def _handle_session_cancel(
        self,
        sessionId: str,
        _meta: dict[str, Any] | None = None,
    ) -> None:
        """Handle session/cancel notification.

        Cancels any running operation in the session.

        Args:
            sessionId: The session ID to cancel.
            _meta: Optional metadata.
        """
        log.info(f"Cancel requested for session {sessionId}")
        await self.adapter.cancel_session(sessionId)

    async def _handle_session_set_mode(
        self,
        sessionId: str,
        modeId: str,
        _meta: dict[str, Any] | None = None,
    ) -> SetSessionModeResponse:
        """Handle session/set_mode request.

        Changes the current mode for a session.

        Args:
            sessionId: The session ID.
            modeId: The mode ID to set.
            _meta: Optional metadata.

        Returns:
            SetSessionModeResponse (empty on success).

        Raises:
            InvalidParams: If session not found or mode invalid.
        """
        log.info(f"Setting mode for session {sessionId} to {modeId}")

        try:
            await self.adapter.set_mode(sessionId, modeId)
        except ValueError as e:
            raise InvalidParams(str(e))

        response: SetSessionModeResponse = {}
        return response

    # =========================================================================
    # Agent -> Client Methods
    # =========================================================================

    async def send_session_update(
        self,
        session_id: str,
        update: SessionUpdate | dict[str, Any],
    ) -> None:
        """Send a session/update notification to the client.

        Args:
            session_id: The session ID.
            update: The session update to send.
        """
        if self.client is None:
            log.debug("No client connection, skipping session update")
            return

        params = {
            "sessionId": session_id,
            "update": update,
        }

        try:
            await self.client.notify("session/update", params)
        except Exception as e:
            log.warning(f"Failed to send session update: {e}")

    async def request_permission(
        self,
        session_id: str,
        tool_call: dict[str, Any],
        options: list[PermissionOption],
    ) -> RequestPermissionResponse:
        """Request permission from the client for a tool call.

        This sends a session/request_permission request to the client
        and waits for their response.

        Args:
            session_id: The session ID.
            tool_call: The tool call requiring permission.
            options: Available permission options.

        Returns:
            RequestPermissionResponse with the selected outcome.

        Raises:
            RuntimeError: If no client connection.
        """
        if self.client is None:
            raise RuntimeError("No client connection for permission request")

        params = {
            "sessionId": session_id,
            "toolCall": tool_call,
            "options": options,
        }

        result = await self.client.call("session/request_permission", params)

        response: RequestPermissionResponse = {
            "outcome": result.get("outcome", {"outcome": "cancelled"}),
        }
        return response

    # =========================================================================
    # Server Lifecycle
    # =========================================================================

    async def serve_stdio(self) -> None:
        """Serve ACP over stdio.

        This sets up stdio-based communication and processes requests
        until the connection is closed.
        """
        log.info("Starting Nimbus ACP agent on stdio")

        # Create stdio connection
        connection = await create_stdio_connection()
        self.connection = connection
        self.client = JSONRPCClient(connection)

        # Create and run server
        server = JSONRPCServer(self.handler, connection)

        try:
            await server.serve()
        except Exception as e:
            log.exception(f"Server error: {e}")
        finally:
            await connection.close()
            log.info("Nimbus ACP agent stopped")

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC request.

        This is useful for testing or when not using the full server.

        Args:
            request: JSON-RPC request dictionary.

        Returns:
            Response dictionary, or None for notifications.
        """
        response = await self.handler.handle(request)
        return response.to_dict() if response else None


def create_acp_agent(
    cwd: str | None = None,
    llm_model: str | None = None,
    llm_url: str | None = None,
    **kwargs: Any,
) -> NimbusACPAgent:
    """Convenience function to create an ACP agent.

    Args:
        cwd: Working directory.
        llm_model: LLM model to use.
        llm_url: Optional custom LLM API URL.
        **kwargs: Additional ACPConfig parameters.

    Returns:
        Configured NimbusACPAgent instance.
    """
    config = ACPConfig(
        cwd=cwd,
        llm_model=llm_model,
        llm_url=llm_url,
        **kwargs,
    )
    return NimbusACPAgent(config)


async def main() -> None:
    """Main entry point for running the ACP agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Nimbus ACP Agent")
    parser.add_argument("--cwd", help="Working directory")
    parser.add_argument("--model", help="LLM model to use")
    parser.add_argument("--api-url", help="Custom LLM API URL")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Create and run agent
    agent = create_acp_agent(
        cwd=args.cwd,
        llm_model=args.model,
        llm_url=args.api_url,
    )

    await agent.serve_stdio()


if __name__ == "__main__":
    asyncio.run(main())
