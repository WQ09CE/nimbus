"""Explicit online model smoke. Telegram is MOCKED; Pi/Codex and Nimbus are real."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import httpx
import pgserver
from loguru import logger

from nimbus_chat_lab.engine import NimbusEngine
from nimbus_chat_lab.gateway import Gateway
from nimbus_chat_lab.store import Store
from nimbus_chat_lab.telegram import TelegramClient
from nimbus_chat_lab.worker import Worker


async def run(pg, output):
    store = Store(pg.get_uri())
    await store.initialize()
    await store.authorize(100, 1, 1)
    expected = "NIMBUS-" + uuid4().hex[:12]
    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "from": {"id": 1, "is_bot": False},
            "chat": {"id": 1, "type": "private"},
            "text": f"Reply with exactly {expected}, and nothing else.",
        },
    }
    sent = []

    def transport(request):
        if request.url.path.endswith("getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [update]})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    client = TelegramClient("100:offline_fixture", transport=httpx.MockTransport(transport))
    try:
        gateway = Gateway(store, client, 100)
        await gateway.poll_once("nimbusbot")
        await gateway.poll_once("nimbusbot")
        claim = await Worker(store, NimbusEngine(output / "attempts")).run_once()
        while await gateway.deliver_once():
            pass
        async with await store.connect() as c:
            turn = await (await c.execute("SELECT state,result FROM turns")).fetchone()
            count = (await (await c.execute("SELECT count(*) AS n FROM attempts")).fetchone())["n"]
            event_count = (
                await (await c.execute("SELECT count(*) AS n FROM turn_events")).fetchone()
            )["n"]
        result = {
            "passed": turn["state"] == "succeeded"
            and turn["result"].strip() == expected
            and count == 1,
            "model": "openai-codex/gpt-6-astra via Pi CLI text adapter",
            "engine": "Nimbus AgentOS",
            "telegram": "MOCK TRANSPORT, NOT LIVE TELEGRAM",
            "tools": "disabled/fail-closed",
            "attempt": str(claim.attempt_id),
            "attempt_count": count,
            "progress_snapshots": event_count,
            "turn": turn,
            "sent": sent,
        }
        (output / "verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        assert result["passed"]
    finally:
        await client.close()


if __name__ == "__main__":
    os.umask(0o077)
    logger.disable("nimbus")
    root = Path(__file__).resolve().parents[2] / ".artifacts"
    root.mkdir(exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="nimbus-model-", dir=root))
    print(output)
    with tempfile.TemporaryDirectory(prefix="nimbus-live-pg-") as temporary:
        pg = pgserver.get_server(temporary, cleanup_mode="stop")
        try:
            asyncio.run(run(pg, output))
        finally:
            pg.cleanup()
