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
            raise ToolExecutionError(f"Invalid JSON payload: {payload[:50]}...")
            
        process = agentos_ref.get_process(target_pid)
        if not process:
            raise ToolExecutionError(f"Process {target_pid} not found or not running.")
            
        msg = IPCMessage(
            sender_pid=sender_pid,
            target_pid=target_pid,
            type=message_type, # type: ignore
            payload=payload_dict
        )

        process.inbox.append(msg)
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
            raise ToolExecutionError("Inbox not available")

        messages = []
        while process.inbox:
            msg = process.inbox.pop(0)
            if hasattr(msg, 'to_dict'):
                messages.append(msg.to_dict())
            else:
                messages.append({"content": str(msg)})
                
        if not messages:
            return "Inbox is empty."
            
        return json.dumps(messages, indent=2)
        
    return definition, execute
