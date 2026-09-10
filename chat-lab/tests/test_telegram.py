import asyncio
import json
from uuid import uuid4

import httpx
import psycopg
import pytest
from conftest import rows, update

from nimbus_chat_lab.cli import token_from_file
from nimbus_chat_lab.engine import EchoEngine
from nimbus_chat_lab.gateway import Gateway
from nimbus_chat_lab.telegram import (
    TelegramClient,
    TelegramError,
    identify,
    parse_update,
    split_text,
)
from nimbus_chat_lab.worker import Worker


@pytest.mark.parametrize(
    "extra",
    [
        {"sender_chat": {"id": -1}},
        {"forward_origin": {"type": "user"}},
        {"from": {"id": 1, "is_bot": True}},
        {"text": "x" * 8001},
        {"text": "/status@otherbot"},
    ],
)
def test_untrusted_or_unaddressed_messages_ignored(extra):
    event = update()
    event["message"].update(extra)
    assert parse_update(event, 100, "nimbusbot") is None


def test_utf16_chunks_and_token_file_policy(tmp_path):
    text = "测试😀" * 2000
    parts = split_text(text)
    assert "".join(parts) == text
    assert all(len(p.encode("utf-16-le")) // 2 <= 3500 for p in parts)
    p = tmp_path / "token"
    p.write_text("100:dummy")
    p.chmod(0o644)
    with pytest.raises(ValueError):
        token_from_file(str(p))
    p.chmod(0o600)
    assert token_from_file(str(p)) == "100:dummy"
    link = tmp_path / "link"
    link.symlink_to(p)
    with pytest.raises(OSError):
        token_from_file(str(link))


async def test_onboarding_only_returns_ids_and_does_not_acknowledge_or_authorize():
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        result = (
            {"id": 100, "is_bot": True}
            if request.url.path.endswith("getMe")
            else [update(text="PRIVATE BODY")]
        )
        return httpx.Response(200, json={"ok": True, "result": result})

    client = TelegramClient("100:testtoken", transport=httpx.MockTransport(transport))
    try:
        result = await identify(client)
        assert result == {
            "bot_id": 100,
            "candidates": [{"user_id": 1, "chat_id": 1}],
            "authorized": False,
        }
        assert "PRIVATE BODY" not in json.dumps(result)
        assert all("offset" not in payload for payload in calls)
    finally:
        await client.close()


async def test_mock_transport_intake_execution_delivery(store):
    calls = []

    def transport(request):
        method = request.url.path.rsplit("/", 1)[-1]
        data = json.loads(request.content)
        calls.append((method, data))
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": [update()]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})

    client = TelegramClient("100:testtoken", transport=httpx.MockTransport(transport))
    try:
        gateway = Gateway(store, client, 100)
        assert await gateway.poll_once("nimbusbot") == ["chat"]
        assert await gateway.poll_once("nimbusbot") == ["duplicate"]
        await Worker(store, EchoEngine()).run_once()
        assert await gateway.deliver_once()
        assert await gateway.deliver_once()
        assert not await gateway.deliver_once()
        assert len(await rows(store, "SELECT * FROM attempts")) == 1
        assert len(await rows(store, "SELECT * FROM outbox WHERE state='sent'")) == 2
        assert calls[1][1]["offset"] == 2
        assert all("parse_mode" not in data for method, data in calls if method == "sendMessage")
    finally:
        await client.close()


async def test_429_retries_delivery_not_execution(store):
    count = 0

    def transport(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(
                429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 11}}
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 20}})

    client = TelegramClient("100:testtoken", transport=httpx.MockTransport(transport))
    try:
        await store.ingest(100, "nimbusbot", [update()])
        gateway = Gateway(store, client, 100)
        await gateway.deliver_once()
        r = (await rows(store, "SELECT * FROM outbox"))[0]
        assert r["state"] == "pending" and r["tries"] == 1
        assert not await gateway.deliver_once()
        await rows(store, "UPDATE outbox SET available_at=clock_timestamp() RETURNING id")
        await rows(store, "UPDATE bots SET send_after='-infinity' RETURNING id")
        await gateway.deliver_once()
        assert (await rows(store, "SELECT state FROM outbox"))[0]["state"] == "sent"
        assert not await rows(store, "SELECT * FROM attempts")
    finally:
        await client.close()


async def test_ambiguous_send_is_not_retried_or_executed(store):
    def transport(request):
        raise httpx.ReadTimeout("SECRET_TOKEN_AND_URL_MUST_NOT_LEAK", request=request)

    client = TelegramClient("100:testtoken", transport=httpx.MockTransport(transport))
    try:
        await store.ingest(100, "nimbusbot", [update()])
        await Worker(store, EchoEngine()).run_once()
        gateway = Gateway(store, client, 100)
        while await gateway.deliver_once():
            pass
        assert {r["state"] for r in await rows(store, "SELECT state FROM outbox")} == {"uncertain"}
        assert (await rows(store, "SELECT state FROM turns"))[0]["state"] == "succeeded"
        assert await store.claim(uuid4()) is None
        with pytest.raises(TelegramError) as e:
            await client.call("sendMessage", {})
        assert str(e.value) == "transport"
    finally:
        await client.close()


async def test_db_loss_after_send_and_sender_recovery(store, monkeypatch):
    client = TelegramClient(
        "100:testtoken",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        ),
    )
    await store.ingest(100, "nimbusbot", [update()])

    async def broken(*args, **kwargs):
        raise psycopg.OperationalError("synthetic lost commit")

    monkeypatch.setattr(store, "settle_delivery", broken)
    try:
        with pytest.raises(psycopg.OperationalError):
            await Gateway(store, client, 100).deliver_once()
        assert (await rows(store, "SELECT state FROM outbox"))[0]["state"] == "sending"
        await rows(
            store,
            "UPDATE outbox SET claimed_at=clock_timestamp()-interval '61 seconds' RETURNING id",
        )
        await store.recover()
        assert (await rows(store, "SELECT state FROM outbox"))[0]["state"] == "uncertain"
        assert await store.claim_delivery(100) is None
    finally:
        await client.close()


async def test_drafts_optional_and_final_survives_unsupported_api(store):
    def transport(request):
        if request.url.path.endswith("/sendMessageDraft"):
            return httpx.Response(400, json={"ok": False, "error_code": 400})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client = TelegramClient("100:testtoken", transport=httpx.MockTransport(transport))
    try:
        await store.ingest(100, "nimbusbot", [update()])
        claim = await store.claim(uuid4())
        await store.progress(claim, "partial")
        gateway = Gateway(store, client, 100, drafts=True)
        await gateway.draft_once()
        assert not gateway.enable_drafts
        assert await store.finish(claim, "succeeded", "final")
        while await gateway.deliver_once():
            pass
        assert len(await rows(store, "SELECT * FROM outbox WHERE state='sent'")) == 2
    finally:
        await client.close()


async def test_gateway_single_active_lock(store):
    started = asyncio.Event()

    async def transport(request):
        if request.url.path.endswith("getMe"):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"id": 100, "is_bot": True, "username": "nimbusbot"}},
            )
        started.set()
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"ok": True, "result": []})

    client = TelegramClient("100:testtoken", transport=httpx.MockTransport(transport))
    stop = asyncio.Event()
    first = asyncio.create_task(Gateway(store, client, 100).run(stop))
    try:
        await asyncio.wait_for(started.wait(), 3)
        with pytest.raises(RuntimeError, match="Another gateway"):
            await Gateway(store, client, 100).run(stop)
    finally:
        stop.set()
        await first
        await client.close()
