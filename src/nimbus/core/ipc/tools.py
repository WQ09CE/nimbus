from typing import Any, Dict, Optional, List
from nimbus.tools.base import ToolDefinition, ToolParameter, ToolExecutionError
from nimbus.core.logging import get_logger
from .message import IPCMessage
import json

logger = get_logger("nimbus.core.ipc")

def create_send_message_tool(agentos_ref: Any, sender_pid: str) -> tuple[ToolDefinition, Any]:
    """
    Creates the 'SendMessage' tool which allows an Agent to send structured IPCMessages
    to another specific running Process.
    """
    definition = ToolDefinition(
        name="SendMessage",
        description=(
            "Send a structured message (JSON contract) to another running AgentOS Process. "
            "Use this to command sub-agents, pass results back to your parent, or share data."
        ),
        parameters=[
            ToolParameter(
                name="target_pid",
                type="string",
                description="The Process ID of the recipient agent (e.g., 'proc-abc1234')."
            ),
            ToolParameter(
                name="message_type",
                type="string",
                description="The type of message: 'request', 'response', 'event', 'error'",
                enum=["request", "response", "event", "error"]
            ),
            ToolParameter(
                name="payload",
                type="string",
                description="A JSON-formatted string containing the exact specific data keys expected by the recipient's contract."
            )
        ]
    )

    async def execute(target_pid: str, message_type: str, payload: str) -> str:
        try:
            payload_dict = json.loads(payload)
        except json.JSONDecodeError:
            raise ToolExecutionError("SendMessage", f"Invalid JSON payload: {payload[:50]}...")
            
        # --- Middleware Verify Gate ---
        sender_process = agentos_ref.get_process(sender_pid)
        if sender_process:
            expected_schema_str = sender_process.metadata.get("expected_schema")
            if expected_schema_str:
                try:
                    # Basic JSON structure validation
                    if expected_schema_str.strip().startswith("{"):
                        schema_dict = json.loads(expected_schema_str)
                        missing_keys = [k for k in schema_dict.keys() if k not in payload_dict]
                        if missing_keys:
                            raise ToolExecutionError(
                                "SendMessage",
                                f"[VERIFY GATE] Contract Violation: Your payload is missing required keys: {missing_keys}. "
                                f"Expected structure: {expected_schema_str}. Please format your response to include these keys and resend."
                            )
                except json.JSONDecodeError:
                    pass # Schema was not pure JSON, skip strict key checking
        # ------------------------------
            
        process = agentos_ref.get_process(target_pid)
        if not process:
            raise ToolExecutionError("SendMessage", f"Process {target_pid} not found or not running.")
            
        msg = IPCMessage(
            sender_pid=sender_pid,
            target_pid=target_pid,
            type=message_type, # type: ignore
            payload=payload_dict
        )

        await process.inbox.send(msg)
        return f"Message {msg.id} successfully queued for {target_pid}."

    return definition, execute


def create_read_inbox_tool(agentos_ref: Any, pid: str) -> tuple[ToolDefinition, Any]:
    """
    Creates the 'ReadInbox' tool which allows an agent to synchronously drain its IPCMailbox.
    """
    definition = ToolDefinition(
        name="ReadInbox",
        description=(
            "Read all pending messages in your Inbox. These are messages sent to you by other Agents or Users. "
            "Calling this will remove the messages from your queue."
        ),
        parameters=[]
    )
    
    async def execute() -> str:
        process = agentos_ref.get_process(pid)
        if not process:
            raise ToolExecutionError("ReadInbox", "Inbox not available")

        messages = []
        while process.inbox and process.inbox.qsize() > 0:
            msg = await process.inbox.receive()
            if msg:
                # Format the message for LLM
                # The original instruction had a syntax error here.
                # Assuming the intent was to append a dictionary with a formatted string.
                messages.append({"content": f"From PID {msg.sender_pid} (ID: {msg.id}): {msg.payload}"})
                
        if not messages:
            return "Inbox is empty."
            
        return json.dumps(messages, indent=2)
        
    return definition, execute
