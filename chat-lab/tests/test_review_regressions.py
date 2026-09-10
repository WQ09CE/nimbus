import asyncio
from uuid import uuid4

import httpx
import pytest
from conftest import rows, update

from nimbus_chat_lab.gateway import Gateway
from nimbus_chat_lab.telegram import TelegramClient


@pytest.mark.parametrize("operation", ["renew", "progress", "finish"])
async def test_pause_after_authority_read_cannot_resurrect_lease(store, monkeypatch, operation):
    store.lease_seconds = 0.2
    await store.ingest(100, "nimbusbot", [update()])
    claim = await store.claim(uuid4())
    original = store._owned

    async def pause(c, claim):
        result = await original(c, claim)
        assert result is not None
        await asyncio.sleep(0.3)  # inside the transaction, after the original lease check
        return result

    monkeypatch.setattr(store, "_owned", pause)
    if operation == "renew":
        assert await store.control(claim, renew=True) is None
    elif operation == "progress":
        assert not await store.progress(claim, "expired")
    else:
        assert not await store.finish(claim, "succeeded", "expired")
    assert not await rows(store, "SELECT * FROM turn_events")
    assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "running"
    assert await store.recover() == 1


@pytest.mark.parametrize("failure", ["connect", "rate_limit"])
async def test_delayed_first_chunk_cannot_be_overtaken_and_429_cools_bot(store, failure):
    await store.ingest(100, "nimbusbot", [update()])
    claim = await store.claim(uuid4())
    await store.finish(claim, "succeeded", "a" * 8000)
    await store.authorize(100, 2, 2)
    await store.ingest(100, "nimbusbot", [update(2, "another chat", user=2, chat=2)])
    calls, successful = 0, []

    def transport(request):
        nonlocal calls
        calls += 1
        if calls == 2:  # acknowledgement succeeded, first answer chunk fails
            if failure == "connect":
                raise httpx.ConnectError("fixture", request=request)
            return httpx.Response(
                429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 30}}
            )
        import json

        successful.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": calls}})

    client = TelegramClient("100:test", transport=httpx.MockTransport(transport))
    gateway = Gateway(store, client, 100)
    try:
        assert await gateway.deliver_once()
        assert await gateway.deliver_once()
        if failure == "rate_limit":
            assert not await gateway.deliver_once()  # all chats respect the returned cooldown
        else:
            assert (
                await gateway.deliver_once()
            )  # other chat may proceed, not later chunks in chat 1
            assert successful[-1]["chat_id"] == 2
        assert not await gateway.deliver_once()
        await rows(store, "UPDATE outbox SET available_at=clock_timestamp() RETURNING id")
        await rows(store, "UPDATE bots SET send_after='-infinity' RETURNING id")
        while await gateway.deliver_once():
            pass
        chunks = [p["text"] for p in successful if p["chat_id"] == 1][1:]
        assert len(chunks) == 3
        assert "1/3" in chunks[0] and "2/3" in chunks[1] and "3/3" in chunks[2]
    finally:
        await client.close()
