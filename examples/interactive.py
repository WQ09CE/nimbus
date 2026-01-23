#!/usr/bin/env python3
"""Interactive CLI for OpenNotebook with Ollama."""

import asyncio
import aiohttp
import sys


class OllamaClient:
    """Ollama LLM client."""

    def __init__(self, model: str = "gemma3n", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    async def complete(self, prompt: str) -> str:
        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 1024},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Ollama error: {resp.status}")
                data = await resp.json()
                return data.get("response", "")


def print_help():
    """Print help message."""
    print("""
Commands:
  /help          - Show this help
  /upload <file> - Simulate file upload (e.g., /upload report.pdf)
  /remove <file> - Remove uploaded file
  /files         - List uploaded files
  /memory        - Show memory state
  /clear         - Clear conversation history
  /skills        - List available skills
  /quit          - Exit

Just type normally to chat with the agent.
""")


async def main():
    from nimbus.core import NotebookAgent

    print("=" * 50)
    print("  OpenNotebook Interactive Demo")
    print("  Model: gemma3n (Ollama)")
    print("=" * 50)
    print("\nInitializing agent...", end=" ", flush=True)

    llm = OllamaClient(model="gemma3n")
    agent = NotebookAgent(
        llm_client=llm,
        system_prompt="你是一个智能笔记本助手。用中文简洁友好地回答问题。",
    )
    print("Ready!\n")
    print_help()

    while True:
        try:
            user_input = input("\n[You] ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if not user_input:
            continue

        # Handle commands
        if user_input.startswith("/"):
            cmd_parts = user_input.split(maxsplit=1)
            cmd = cmd_parts[0].lower()
            arg = cmd_parts[1] if len(cmd_parts) > 1 else ""

            if cmd == "/quit" or cmd == "/exit":
                print("Bye!")
                break
            elif cmd == "/help":
                print_help()
            elif cmd == "/upload":
                if arg:
                    ftype = arg.split(".")[-1].upper() if "." in arg else "FILE"
                    agent.on_file_upload(arg, ftype, f"User uploaded {arg}")
                    print(f"[System] File '{arg}' uploaded and added to context.")
                else:
                    print("[System] Usage: /upload <filename>")
            elif cmd == "/remove":
                if arg:
                    agent.on_file_remove(arg)
                    print(f"[System] File '{arg}' removed from context.")
                else:
                    print("[System] Usage: /remove <filename>")
            elif cmd == "/files":
                pinned = agent.memory.pinned
                if pinned:
                    print("[System] Uploaded files:")
                    for k, v in pinned.items():
                        print(f"  - {k}: {v}")
                else:
                    print("[System] No files uploaded.")
            elif cmd == "/memory":
                print(f"[System] Conversation turns: {agent.memory.get_turn_count()}")
                print(f"[System] Pinned items: {agent.memory.get_pinned_count()}")
            elif cmd == "/clear":
                agent.clear_memory()
                print("[System] Conversation history cleared.")
            elif cmd == "/skills":
                skills = agent.executor.get_skill_names()
                print(f"[System] Available skills: {', '.join(skills)}")
            else:
                print(f"[System] Unknown command: {cmd}. Type /help for help.")
            continue

        # Normal chat
        print("[Agent] ", end="", flush=True)
        try:
            response = await agent.run(user_input)
            print(response.text)
            if response.error:
                print(f"[Error] {response.error}")
        except Exception as e:
            print(f"[Error] {e}")


if __name__ == "__main__":
    asyncio.run(main())
