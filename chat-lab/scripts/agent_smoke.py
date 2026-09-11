"""Real Astra + Nimbus tools + gVisor, isolated PG, MOCK Telegram. Never production intake."""

import asyncio
import json
import tempfile
from pathlib import Path
from uuid import uuid4

import pgserver
from loguru import logger

from nimbus_chat_lab.agent_engine import AgentEngine
from nimbus_chat_lab.scheduler import Scheduler
from nimbus_chat_lab.store import Store
from nimbus_chat_lab.worker import Worker


async def main():
    logger.remove()
    evidence = Path(__file__).resolve().parents[2] / ".artifacts/agent-model-smoke"
    evidence.mkdir(mode=0o700, exist_ok=True)
    logger.add(str(evidence / "engine-debug.log"), level="DEBUG", enqueue=False)
    with tempfile.TemporaryDirectory(prefix="nimbus-agent-proof-") as tmp:
        pg = pgserver.get_server(Path(tmp) / "pg", cleanup_mode="stop")
        try:
            store = Store(pg.get_uri(), agent_mode=True)
            await store.initialize()
            await store.authorize(100, 1, 1)
            engine = AgentEngine(
                store,
                evidence / uuid4().hex,
                "/home/dennis/.local/share/mise/installs/pi/0.85.1/pi/pi",
                json.loads(
                    (Path.home() / ".config/nimbus-chat-lab/agent-runtime.json").read_text()
                ),
            )
            worker = Worker(store, engine, run_timeout=720)
            nonce = "AGENT-" + uuid4().hex[:8]
            prompt = (
                f"This is an authorized synthetic integration test. Use actual tools, not prose simulation. "
                f"1. In workspace use bash/Python to write proof.txt containing exactly {nonce}, then read it using the read action. "
                f"2. Save persistent memory key proof_nonce with value {nonce}. "
                f"3. Create a daily 08:00 Asia/Shanghai schedule named integration-probe with instructions: Reply exactly {nonce}-SCHEDULED; no tools needed. "
                "4. Disable that schedule (do NOT leave recurring delivery enabled), then run_now once. "
                f"5. List schedules to verify the persisted disabled state and queued run. Finally reply {nonce}-READY and the real schedule ID."
            )
            update = {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "from": {"id": 1, "is_bot": False},
                    "chat": {"id": 1, "type": "private"},
                    "text": prompt,
                },
            }
            await store.ingest(100, "synthetic_bot", [update])
            claim = await worker.run_once()
            async with await store.connect() as c:
                turn = await (
                    await c.execute("SELECT state,result FROM turns WHERE id=%s", (claim.turn_id,))
                ).fetchone()
                memory = await (
                    await c.execute("SELECT value FROM agent_memory WHERE key='proof_nonce'")
                ).fetchone()
                schedules = await (await c.execute("SELECT id,enabled FROM schedules")).fetchall()
                jobs = await (await c.execute("SELECT state FROM schedule_runs")).fetchall()
                archives = await (
                    await c.execute("SELECT octet_length(archive) AS bytes FROM agent_workspaces")
                ).fetchall()
            result = {
                "transport": "MOCK TELEGRAM / ISOLATED PG",
                "parent_succeeded": turn["state"] == "succeeded",
                "nonce_in_reply": nonce + "-READY" in turn["result"],
                "memory_pass": bool(memory and memory["value"] == nonce),
                "one_disabled_schedule": len(schedules) == 1 and not schedules[0]["enabled"],
                "one_queued_run": len(jobs) == 1 and jobs[0]["state"] == "queued",
                "workspace_persisted": bool(archives and archives[0]["bytes"] > 0),
            }
            if all(v for k, v in result.items() if k != "transport"):
                scheduler = Scheduler(store)
                await scheduler.tick()
                background = await worker.run_once()
                await scheduler.tick()
                await scheduler.tick()
                async with await store.connect() as c:
                    t = await (
                        await c.execute(
                            "SELECT state,result FROM turns WHERE id=%s", (background.turn_id,)
                        )
                    ).fetchone()
                    out = await (
                        await c.execute(
                            "SELECT text FROM outbox WHERE dedupe_key LIKE 'scheduled:%'"
                        )
                    ).fetchall()
                result.update(
                    background_succeeded=t["state"] == "succeeded",
                    scheduled_nonce_pass=t["result"] == nonce + "-SCHEDULED",
                    one_scheduled_outbox=len(out) == 1 and nonce + "-SCHEDULED" in out[0]["text"],
                )
            (evidence / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
        finally:
            pg.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
