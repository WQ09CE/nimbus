"""Basic usage example for OpenNotebook.

This example demonstrates:
1. Creating a NotebookAgent with a mock LLM
2. Handling file uploads
3. Running conversations
4. Registering custom skills
"""

import asyncio
from nimbus.core import NotebookAgent


class MockLLMClient:
    """Mock LLM client for demonstration purposes.

    In production, replace with actual LLM client (e.g., OpenAI, Anthropic).
    """

    async def complete(self, prompt: str) -> str:
        """Simulate LLM completion."""
        # Simple keyword-based responses for demo
        prompt_lower = prompt.lower()

        if "hello" in prompt_lower or "hi" in prompt_lower:
            return '{"mode": "direct", "response": "Hello! How can I help you today?"}'

        if "analyze" in prompt_lower and "csv" in prompt_lower:
            return """{
                "mode": "multi_step",
                "tasks": [{
                    "type": "chat",
                    "skill": "chat",
                    "params": {
                        "message": "Analyzing the CSV file...",
                        "context": "User wants to analyze data"
                    }
                }]
            }"""

        if "skill" in prompt_lower:
            # For chat skill execution
            return "I've analyzed the context and here's my response based on your data."

        return '{"mode": "direct", "response": "I understand. Let me help you with that."}'


async def main():
    """Demonstrate basic NotebookAgent usage."""
    print("=" * 60)
    print("OpenNotebook Basic Usage Example")
    print("=" * 60)

    # 1. Create agent with mock LLM
    llm_client = MockLLMClient()
    agent = NotebookAgent(
        llm_client=llm_client,
        system_prompt="You are a helpful data analysis assistant.",
    )

    print("\n[1] Agent initialized with default 'chat' skill")
    print(f"    Available skills: {agent.executor.get_skill_names()}")

    # 2. Simulate file upload
    print("\n[2] Simulating file upload...")
    agent.on_file_upload(
        filename="sales_data.csv",
        file_type="csv",
        summary="Monthly sales data with 500 rows, columns: date, product, revenue",
    )
    print("    File 'sales_data.csv' uploaded and pinned to context")

    # 3. Run a simple greeting
    print("\n[3] Running conversation: 'Hello!'")
    response = await agent.run("Hello!")
    print(f"    Response: {response.text}")

    # 4. Run analysis request
    print("\n[4] Running conversation: 'Can you analyze my CSV file?'")
    response = await agent.run("Can you analyze my CSV file?")
    print(f"    Response: {response.text}")

    # 5. Check memory state
    print("\n[5] Memory state:")
    print(f"    Conversation turns: {agent.memory.get_turn_count()}")
    print(f"    Pinned files: {agent.memory.get_pinned_count()}")
    print(f"    Context preview:\n{'-' * 40}")
    context = agent.memory.get_context(recent_count=5)
    for line in context.split("\n")[:10]:
        print(f"    {line}")

    # 6. Register custom skill
    print("\n[6] Registering custom 'summarize' skill...")

    async def summarize_skill(text: str, max_length: int = 100) -> str:
        """Custom summarization skill."""
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."

    agent.register_skill("summarize", summarize_skill)
    print(f"    Available skills: {agent.executor.get_skill_names()}")

    # 7. File removal
    print("\n[7] Removing file from context...")
    agent.on_file_remove("sales_data.csv")
    print(f"    Pinned files after removal: {agent.memory.get_pinned_count()}")

    # 8. Reset agent
    print("\n[8] Resetting agent...")
    agent.reset()
    print(f"    Conversation turns after reset: {agent.memory.get_turn_count()}")
    print(f"    Pinned files after reset: {agent.memory.get_pinned_count()}")

    print("\n" + "=" * 60)
    print("Example completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
