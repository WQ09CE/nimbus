"""Demo with local Ollama (gemma3n) model."""

import asyncio

from nimbus.core import setup_logging, logger, get_agent_logger, agent_context

# Setup logging with colored output
setup_logging(level="DEBUG", log_dir="./.logs", json_file=False)

# aiohttp is optional - only needed for actual Ollama calls
try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False


class OllamaClient:
    """Ollama LLM client for local inference."""

    def __init__(self, model: str = "gemma3n", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    async def complete(self, prompt: str) -> str:
        """Call Ollama API for completion."""
        if not HAS_AIOHTTP:
            raise RuntimeError("aiohttp is required for Ollama. Install: pip install aiohttp")

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 512,
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Ollama error: {resp.status} - {text}")
                data = await resp.json()
                return data.get("response", "")


async def demo_basic():
    """Basic demo: greeting and file context."""
    from nimbus.core import NotebookAgent

    logger.info("=" * 50)
    logger.info("Part 1: Basic Interaction")
    logger.info("=" * 50)

    # 使用 agent context 包裹整个 agent 生命周期
    with agent_context("notebook-main", task_id="demo-basic"):
        llm = OllamaClient(model="gemma3n")
        agent = NotebookAgent(
            llm_client=llm,
            system_prompt="You are a helpful notebook assistant. Reply in Chinese.",
        )

        # Simple greeting
        logger.info("Testing greeting: 'Hello!'")
        response = await agent.run("Hello!")
        logger.success(f"Response received: {response.text[:100]}...")

        # File upload
        logger.info("Testing file upload simulation...")
        agent.on_file_upload(
            filename="report.pdf",
            file_type="PDF",
            summary="Q3 Sales Report with regional data",
        )

        response = await agent.run("What file did I upload?")
        logger.success(f"Response received: {response.text[:100]}...")


async def demo_skills():
    """Demo: new skills (search, summarize)."""
    from nimbus.core import NotebookAgent
    from nimbus.skills import web_search, summarize_text, extract_keywords

    print("\n" + "=" * 60)
    print("Part 2: Skills Demo")
    print("=" * 60)

    # Direct skill calls (without agent)
    print("\n[Skill] web_search('Python tutorial'):")
    result = await web_search("Python tutorial")
    print(result[:300])

    print("\n[Skill] extract_keywords(...):")
    sample_text = """
    Python is a high-level programming language known for its simplicity.
    It supports multiple programming paradigms including procedural,
    object-oriented, and functional programming. Python is widely used
    in web development, data science, artificial intelligence, and automation.
    """
    keywords = await extract_keywords(sample_text, top_k=5)
    print(f"Keywords: {keywords}")

    print("\n[Skill] summarize_text(...):")
    summary = await summarize_text(sample_text, max_length=100)
    print(f"Summary: {summary}")


async def demo_stream():
    """Demo: streaming output."""
    from nimbus.core import NotebookAgent

    print("\n" + "=" * 60)
    print("Part 3: Stream Output Demo")
    print("=" * 60)

    llm = OllamaClient(model="gemma3n")
    agent = NotebookAgent(
        llm_client=llm,
        system_prompt="You are a helpful assistant.",
    )

    print("\n[Stream] Processing: 'Search for machine learning tutorials'")
    print("-" * 40)

    async for status in agent.run_stream("Search for machine learning tutorials"):
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


async def demo_multi_turn():
    """Demo: multi-turn conversation with logging."""
    from nimbus.core import NotebookAgent

    logger.info("=" * 50)
    logger.info("Part 4: Multi-turn Conversation")
    logger.info("=" * 50)

    with agent_context("notebook-main", task_id="demo-multi-turn"):
        llm = OllamaClient(model="gemma3n")
        agent = NotebookAgent(llm_client=llm)

        conversations = [
            "My name is Alice.",
            "What's my name?",
            "Help me search for Python web frameworks.",
        ]

        for i, msg in enumerate(conversations):
            logger.info(f"Turn {i+1}: User says '{msg}'")
            response = await agent.run(msg)
            logger.success(f"Turn {i+1}: Agent responded: {response.text[:100]}...")

        logger.info(f"Conversation completed. Total turns: {agent.memory.get_turn_count()}")


async def demo_parallel_agents():
    """Demo: parallel sub-agents with separate logging contexts."""
    logger.info("=" * 50)
    logger.info("Part 5: Parallel Sub-Agents Demo")
    logger.info("=" * 50)

    async def sub_agent_work(agent_id: str, task: str):
        """Simulate a sub-agent doing work."""
        log = get_agent_logger(agent_id, task_id=task)
        log.info(f"Starting work on: {task}")
        await asyncio.sleep(0.2)  # Simulate work
        log.debug("Processing...")
        await asyncio.sleep(0.1)
        log.success(f"Completed: {task}")
        return f"{agent_id} finished {task}"

    # 主 agent 分发任务给多个子 agent
    with agent_context("coordinator", task_id="dispatch"):
        logger.info("Dispatching tasks to sub-agents...")

        # 并行执行 3 个子 agent
        results = await asyncio.gather(
            sub_agent_work("eye-001", "search-codebase"),
            sub_agent_work("body-001", "implement-feature"),
            sub_agent_work("nose-001", "review-changes"),
        )

        logger.info(f"All sub-agents completed: {results}")


async def main():
    """Run all demos."""
    logger.info("=" * 60)
    logger.info("OpenNotebook + Ollama Demo (with Logging)")
    logger.info("=" * 60)

    await demo_parallel_agents()
    await demo_basic()
    await demo_skills()
    await demo_stream()
    await demo_multi_turn()

    logger.success("All demos completed!")


if __name__ == "__main__":
    asyncio.run(main())
