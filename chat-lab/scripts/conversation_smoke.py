"""Real Astra conversational follow-up; isolated PG, synthetic research, no Telegram."""

import asyncio
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import pgserver
from loguru import logger

from nimbus_chat_lab.agent_engine import AgentEngine
from nimbus_chat_lab.agent_state import AgentState
from nimbus_chat_lab.scheduler import Scheduler
from nimbus_chat_lab.store import Store
from nimbus_chat_lab.worker import Worker


def update(n, text):
    return {
        "update_id": n,
        "message": {
            "message_id": n,
            "from": {"id": 1, "is_bot": False},
            "chat": {"id": 1, "type": "private"},
            "text": text,
        },
    }


async def main():
    logger.remove()
    evidence = Path(__file__).resolve().parents[2] / ".artifacts/conversation-smoke"
    evidence.mkdir(mode=0o700, exist_ok=True)
    root = evidence / uuid4().hex
    with tempfile.TemporaryDirectory(prefix="nimbus-conversation-proof-") as tmp:
        pg = pgserver.get_server(Path(tmp) / "pg", cleanup_mode="stop")
        try:
            store = Store(pg.get_uri(), agent_mode=True)
            await store.initialize()
            await store.authorize(100, 1, 1)
            await store.ingest(100, "synthetic", [update(1, "合成测试配置")])
            parent = await store.claim(uuid4(), lane="chat")
            state = AgentState(store, parent)
            await state.initialize()
            sid = (
                await state.schedule(
                    "create", {"name": "合成日报", "instructions": "合成测试，不检索网络"}
                )
            )["schedule"]["id"]
            await state.schedule("disable", {"id": sid})
            await state.schedule("run_now", {"id": sid})
            scheduler = Scheduler(store)
            await scheduler.tick()
            bg = await store.claim(uuid4(), lane="jobs")
            bgstate = AgentState(store, bg)
            await bgstate.initialize()
            await bgstate.record_research(
                "合成检索：官方发布和开发者经验两组关键词",
                "x",
                {
                    "text": "合成证据：共8个候选，5个在时间窗口外，1个与另一个重复，最终保留2个事件。只覆盖两组关键词，不足以断言全社区只有两件事。",
                    "sources": [
                        "https://example.org/synthetic-one",
                        "https://example.org/synthetic-two",
                    ],
                    "tool_usage": {"x_search_calls": 2},
                },
            )
            await store.finish(bg, "succeeded", "合成日报：本期两个事件，示例一和示例二。")
            await store.finish(parent, "succeeded", "合成配置完成")
            await scheduler.tick()
            async with await store.connect() as c:
                await c.execute(
                    "UPDATE outbox SET state='sent',claimed_at=clock_timestamp() WHERE schedule_run_id IS NOT NULL"
                )
            engine = AgentEngine(
                store,
                root,
                "/home/dennis/.local/share/mise/installs/pi/0.85.1/pi/pi",
                json.loads(
                    (Path.home() / ".config/nimbus-chat-lab/agent-runtime.json").read_text()
                ),
            )
            await store.ingest(
                100,
                "synthetic",
                [
                    update(
                        2,
                        "为什么刚才的合成日报只有两条？请查一下已保存的检索记录，用自然对话解释。不要重新搜索、运行或修改任务。",
                    )
                ],
            )
            claim = await Worker(store, engine, lane="chat", run_timeout=300).run_once()
            async with await store.connect() as c:
                turn = await (
                    await c.execute("SELECT state,result FROM turns WHERE id=%s", (claim.turn_id,))
                ).fetchone()
                final = await (
                    await c.execute(
                        "SELECT text FROM outbox WHERE dedupe_key=%s", (f"final:{claim.turn_id}:0",)
                    )
                ).fetchone()
                unchanged = await (
                    await c.execute(
                        "SELECT enabled,(SELECT count(*) FROM schedule_runs) AS runs FROM schedules WHERE id=%s",
                        (sid,),
                    )
                ).fetchone()
            activities = 0
            for path in root.rglob("*.jsonl"):
                for line in path.read_text().splitlines():
                    event = json.loads(line)
                    if (
                        event.get("type") == "tool/result"
                        and event.get("data", {}).get("message", {}).get("name") == "activity"
                    ):
                        activities += 1
            result = {
                "scope": "REAL ASTRA / SYNTHETIC RESEARCH / ISOLATED PG / NO TELEGRAM",
                "succeeded": turn["state"] == "succeeded",
                "inspected_actual_stored_receipts": activities > 0,
                "plain_final": final["text"] == turn["result"],
                "no_debug_states": not any(
                    s in final["text"] for s in ("queued", "succeeded", "running")
                ),
                "no_deflection": not any(s in final["text"] for s in ("它声称", "当前上下文没有")),
                "no_rerun_or_enable": unchanged == {"enabled": False, "runs": 1},
            }
            (evidence / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
            (evidence / "synthetic-answer.txt").write_text(final["text"])
            print(json.dumps(result, indent=2))
            assert all(v for k, v in result.items() if k != "scope")
        finally:
            pg.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
