import asyncio
import os
from nimbus.adapters.direct_adapter import DirectAdapter
from nimbus.adapters.types import LLMConfig
import litellm

# Point litellm to local ollama instance
litellm.api_base = "http://localhost:11434"

async def main():
    config = LLMConfig(
        provider="ollama",
        model_id="qwen3.5:9b",
        temperature=0.0
    )
    adapter = DirectAdapter(config)
    messages = [
        {"role": "user", "content": "Fetch the structure of the agent/demo directory."}
    ]
    tools = [
        {
            "name": "Bash",
            "description": "Execute a bash command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command"}
                },
                "required": ["command"]
            }
        }
    ]
    
    print("Testing adapter stream...")
    async for event in adapter.stream(messages, tools):
        print(f"EVENT: {event}")
        
    print("\nTesting adapter chat...")
    res = await adapter.chat(messages, tools)
    print(f"RESPONSE content: {res.content}")
    print(f"RESPONSE tool_calls: {res.tool_calls}")

if __name__ == "__main__":
    asyncio.run(main())
