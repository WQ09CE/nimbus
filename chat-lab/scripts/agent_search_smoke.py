"""Actual AgentOS -> Astra native tool call -> Pi xAI OAuth -> X Search; MOCK Telegram."""

import ast
import asyncio
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import pgserver
from loguru import logger

from nimbus_chat_lab.agent_engine import AgentEngine
from nimbus_chat_lab.store import Store
from nimbus_chat_lab.worker import Worker


async def main():
    logger.remove()
    evidence = Path(__file__).resolve().parents[2] / ".artifacts/agent-search-smoke"
    evidence.mkdir(mode=0o700, exist_ok=True)
    root = evidence / uuid4().hex
    with tempfile.TemporaryDirectory(prefix="nimbus-search-proof-") as tmp:
        pg = pgserver.get_server(Path(tmp) / "pg", cleanup_mode="stop")
        try:
            store = Store(pg.get_uri(), agent_mode=True)
            await store.initialize()
            await store.authorize(100, 1, 1)
            engine = AgentEngine(
                store,
                root,
                "/home/dennis/.local/share/mise/installs/pi/0.85.1/pi/pi",
                json.loads(
                    (Path.home() / ".config/nimbus-chat-lab/agent-runtime.json").read_text()
                ),
            )
            prompt = "请实际调用 search 工具（source=x），找过去 7 天 X 社区里一条与 AI Agent 开发相关的原帖。返回原帖链接和一句中文概述即可。不要创建或启用定时任务。这是合成验收，不是真实 Telegram 用户请求。"
            await store.ingest(
                100,
                "synthetic_bot",
                [
                    {
                        "update_id": 1,
                        "message": {
                            "message_id": 1,
                            "from": {"id": 1, "is_bot": False},
                            "chat": {"id": 1, "type": "private"},
                            "text": prompt,
                        },
                    }
                ],
            )
            claim = await Worker(store, engine, run_timeout=600).run_once()
            async with await store.connect() as c:
                turn = await (
                    await c.execute("SELECT state,result FROM turns WHERE id=%s", (claim.turn_id,))
                ).fetchone()
                count = (await (await c.execute("SELECT count(*) AS n FROM schedules")).fetchone())[
                    "n"
                ]
            searches = []
            for path in root.rglob("*.jsonl"):
                for line in path.read_text().splitlines():
                    event = json.loads(line)
                    message = event.get("data", {}).get("message", {})
                    if event.get("type") == "tool/result" and message.get("name") == "search":
                        try:
                            searches.append(ast.literal_eval(message["content"]))
                        except (SyntaxError, ValueError):
                            pass
            sources = [url for s in searches if isinstance(s, dict) for url in s.get("sources", [])]
            calls = sum(
                s.get("tool_usage", {}).get("x_search_calls", 0)
                for s in searches
                if isinstance(s, dict)
            )
            result = {
                "transport": "MOCK TELEGRAM / ISOLATED PG",
                "agent_succeeded": turn["state"] == "succeeded",
                "actual_x_search_calls": calls,
                "provider_source_count": len(sources),
                "final_contains_provider_source": any(url in turn["result"] for url in sources),
                "no_schedule_created": count == 0,
            }
            (evidence / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
            assert (
                result["agent_succeeded"]
                and calls > 0
                and result["final_contains_provider_source"]
                and count == 0
            )
        finally:
            pg.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
