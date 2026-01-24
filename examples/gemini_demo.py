"""Demo with Google Gemini model.

This example demonstrates using Nimbus with Google's Gemini API.

Requirements:
    - GEMINI_API_KEY environment variable set
    - Or pass api_key directly to GeminiClient

Usage:
    export GEMINI_API_KEY="your-api-key"
    python examples/gemini_demo.py
"""

import asyncio
import os

from nimbus.core import setup_logging, logger, agent_context
from nimbus.llm import GeminiClient

# Setup logging
setup_logging(level="INFO", log_dir="./.logs", json_file=False)


async def demo_basic():
    """Basic demo: greeting and simple conversation."""
    logger.info("=" * 50)
    logger.info("Part 1: Basic Interaction with Gemini")
    logger.info("=" * 50)

    from nimbus.core import NotebookAgent

    with agent_context("gemini-demo", task_id="basic"):
        # Create Gemini client
        client = GeminiClient()

        # Create agent with Gemini
        agent = NotebookAgent(
            llm_client=client,
            system_prompt="You are a helpful assistant. Reply concisely in Chinese.",
        )

        # Simple greeting
        logger.info("Testing greeting: 'Hello!'")
        response = await agent.run("Hello!")
        logger.success(f"Response: {response.text[:200]}...")

        # Follow-up question
        logger.info("Testing follow-up: 'What can you help me with?'")
        response = await agent.run("What can you help me with?")
        logger.success(f"Response: {response.text[:200]}...")


async def demo_streaming():
    """Demo: streaming output with Gemini."""
    logger.info("=" * 50)
    logger.info("Part 2: Streaming with Gemini")
    logger.info("=" * 50)

    client = GeminiClient()

    print("\n[Streaming] Counting from 1 to 5:")
    print("-" * 40)

    async for chunk in client.stream("Count from 1 to 5, one number per line"):
        print(chunk, end="", flush=True)

    print("\n" + "-" * 40)


async def demo_multi_turn():
    """Demo: multi-turn conversation with memory."""
    logger.info("=" * 50)
    logger.info("Part 3: Multi-turn Conversation")
    logger.info("=" * 50)

    from nimbus.core import NotebookAgent

    with agent_context("gemini-demo", task_id="multi-turn"):
        client = GeminiClient()
        agent = NotebookAgent(
            llm_client=client,
            system_prompt="You are a helpful assistant. Be concise.",
        )

        conversations = [
            "My name is Alice.",
            "What's my name?",
            "Tell me a fun fact about the name Alice.",
        ]

        for i, msg in enumerate(conversations):
            logger.info(f"Turn {i+1}: User says '{msg}'")
            response = await agent.run(msg)
            logger.success(f"Turn {i+1}: Agent responded: {response.text[:150]}...")


async def demo_agent_stream():
    """Demo: streaming with NotebookAgent."""
    logger.info("=" * 50)
    logger.info("Part 4: Agent Streaming")
    logger.info("=" * 50)

    from nimbus.core import NotebookAgent

    client = GeminiClient()
    agent = NotebookAgent(
        llm_client=client,
        system_prompt="You are a helpful assistant.",
    )

    print("\n[Agent Stream] Processing: 'Search for Python tutorials'")
    print("-" * 40)

    async for status in agent.run_stream("Search for Python tutorials"):
        status_type = status.get("type", "unknown")

        if status_type == "status":
            print(f"[STATUS] {status['content']}")
        elif status_type == "planning":
            print(f"[PLANNING] {status['content']}")
        elif status_type == "task_start":
            print(f"[START] Task {status['task_id']}: {status['skill']}")
        elif status_type == "task_done":
            result = str(status.get('result', ''))[:100]
            print(f"[DONE] Task {status['task_id']}: {result}...")
        elif status_type == "direct":
            print(f"[DIRECT] {status['content'][:200]}...")
        elif status_type == "complete":
            print(f"[COMPLETE] {status['content'][:200]}...")
        elif status_type == "error":
            print(f"[ERROR] {status['content']}")

    print("-" * 40)


async def demo_direct_client():
    """Demo: direct client usage without agent."""
    logger.info("=" * 50)
    logger.info("Part 5: Direct Client Usage")
    logger.info("=" * 50)

    async with GeminiClient() as client:
        # Simple completion
        print("\n[Complete] Simple question:")
        response = await client.complete("What is 2 + 2? Just say the number.")
        print(f"Response: {response}")

        # With history
        print("\n[Complete with history] Remembering context:")
        history = [
            {"role": "user", "content": "My favorite color is blue."},
            {"role": "assistant", "content": "Blue is a beautiful color!"},
        ]
        response = await client.complete(
            "What's my favorite color?",
            history=history,
        )
        print(f"Response: {response}")


async def main():
    """Run all demos."""
    # Check for API key
    if not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable is not set.")
        print("Please set it with: export GEMINI_API_KEY='your-api-key'")
        return

    logger.info("=" * 60)
    logger.info("Nimbus + Gemini Demo")
    logger.info("=" * 60)

    await demo_direct_client()
    await demo_streaming()
    await demo_basic()
    await demo_multi_turn()
    await demo_agent_stream()

    logger.success("All demos completed!")


if __name__ == "__main__":
    asyncio.run(main())
