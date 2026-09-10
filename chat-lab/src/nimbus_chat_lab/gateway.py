import asyncio
from contextlib import suppress

from .telegram import TelegramError, split_text


class Gateway:
    def __init__(self, store, telegram, bot_id: int, *, drafts=False):
        self.store, self.telegram, self.bot_id = store, telegram, bot_id
        self.enable_drafts = drafts
        self.draft_seen = {}
        self.draft_not_before = 0

    async def poll_once(self, username):
        updates = await self.telegram.call(
            "getUpdates",
            {
                "offset": await self.store.offset(self.bot_id),
                "timeout": 30,
                "limit": 100,
                "allowed_updates": ["message"],
            },
        )
        if not isinstance(updates, list):
            raise TelegramError("failed", "invalid_updates")
        return await self.store.ingest(self.bot_id, username, updates)

    async def deliver_once(self):
        row = await self.store.claim_delivery(self.bot_id)
        if row is None:
            return False
        payload = {
            "chat_id": row["chat_id"],
            "text": row["text"],
            "link_preview_options": {"is_disabled": True},
        }
        if row["thread_id"]:
            payload["message_thread_id"] = row["thread_id"]
        try:
            result = await self.telegram.call("sendMessage", payload)
            if not isinstance(result, dict) or type(result.get("message_id")) is not int:
                raise TelegramError("uncertain", "invalid_message_receipt")
            await self.store.settle_delivery(row, "sent", message_id=result["message_id"])
        except TelegramError as error:
            state = (
                "pending"
                if error.disposition == "retry" and row["tries"] < 5
                else error.disposition
            )
            if state == "retry":
                state = "failed"
            await self.store.settle_delivery(
                row, state, delay=error.retry_after, error_class=error.error_class
            )
        # If DB write failed after send, leave sending: scanner -> uncertain, never rerun engine.
        return True

    async def draft_once(self):
        if not self.enable_drafts or asyncio.get_running_loop().time() < self.draft_not_before:
            return
        rows = await self.store.drafts(self.bot_id)
        active = {r["id"] for r in rows}
        self.draft_seen = {k: v for k, v in self.draft_seen.items() if k in active}
        for row in rows:
            if self.draft_seen.get(row["id"]) == row["seq"]:
                continue
            payload = {
                "chat_id": row["chat_id"],
                "draft_id": row["id"].int % (2**31 - 1) + 1,
                "text": split_text(row["text"])[0],
            }
            if row["thread_id"]:
                payload["message_thread_id"] = row["thread_id"]
            # can_stop remains off until stopped_message_generation is durably handled.
            try:
                await self.telegram.call("sendMessageDraft", payload)
                self.draft_seen[row["id"]] = row["seq"]
            except TelegramError as error:
                if error.error_class == "rate_limit":
                    await self.store.cooldown(self.bot_id, error.retry_after)
                self.draft_not_before = asyncio.get_running_loop().time() + max(
                    3, error.retry_after
                )
                if error.disposition == "failed":
                    self.enable_drafts = False  # Optional feature; durable final send still works.
                return

    async def run(self, stop):
        # A DB session lock prevents a second polling process, even under a different cwd.
        async with await self.store.connect() as lock:
            await lock.set_autocommit(True)
            row = await (
                await lock.execute(
                    "SELECT pg_try_advisory_lock(%s) AS acquired",
                    (0x6E69000000000000 + self.bot_id,),
                )
            ).fetchone()
            if not row["acquired"]:
                raise RuntimeError("Another gateway already owns this bot")
            me = await self.telegram.call("getMe", {})
            if me.get("id") != self.bot_id or not me.get("is_bot") or not me.get("username"):
                raise RuntimeError("Bot identity does not match operator-provisioned identity")
            await self.store.offset(self.bot_id)
            await self.store.recover()

            async def polling():
                while not stop.is_set():
                    try:
                        await self.poll_once(me["username"])
                    except TelegramError as error:
                        if error.disposition == "failed":
                            raise
                        await asyncio.sleep(max(2, error.retry_after))

            async def delivery():
                while not stop.is_set():
                    sent = await self.deliver_once()
                    await asyncio.sleep(3.2 if sent else 0.3)

            async def maintenance():
                while not stop.is_set():
                    await lock.execute(
                        "SELECT 1"
                    )  # Loss of the single-active lock connection is fatal.
                    await self.store.recover()
                    await self.draft_once()
                    await asyncio.sleep(1)

            tasks = [asyncio.create_task(f()) for f in (polling, delivery, maintenance)]
            stopping = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait(
                    [*tasks, stopping], return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    await (
                        task
                    )  # propagate loop failure instead of silently losing a background task
            finally:
                for task in [*tasks, stopping]:
                    task.cancel()
                for task in [*tasks, stopping]:
                    with suppress(asyncio.CancelledError, Exception):
                        await task
