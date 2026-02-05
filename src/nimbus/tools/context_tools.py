"""
Context Management Tools

Tools for the Agent to manage its own memory and context window.
Allows "paging" through history when the context window is full.
"""

from typing import Dict, Any
from nimbus.tools import tool

# Definition for ScrollHistory
SCROLL_HISTORY_DEF = {
    "name": "ScrollHistory",
    "description": "Scroll through conversation history to view past messages that are currently outside your context window.",
    "parameters": {
        "type": "object",
        "properties": {
            "direction": {
                "type": "string",
                "enum": ["up", "down"],
                "description": "'up' to see older messages, 'down' to see newer messages."
            },
            "steps": {
                "type": "integer",
                "description": "Number of messages to scroll. Default is 10.",
                "default": 10
            }
        },
        "required": ["direction"]
    }
}

# Definition for CopyToClipboard
COPY_TO_CLIPBOARD_DEF = {
    "name": "CopyToClipboard",
    "description": "Copy important information (code snippets, variable values, requirements) to a persistent clipboard. This information will remain visible in your context even when you scroll through history.",
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The content to copy to clipboard."
            }
        },
        "required": ["content"]
    }
}

# The implementation is dynamically generated per process to capture MMU instance.
# This file serves as the schema definition source.
