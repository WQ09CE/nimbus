"""Handoff bus — graceful takeover over NATS JetStream (nimbus-lab R3.2).

A pod that must go away (SIGTERM) pauses its sessions at a step seam, binds the
sandbox snapshot, then ANNOUNCES each paused session here. Any live pod in the
queue group consumes the announcement and resumes the session from its durable
checkpoint + binding. At-least-once with ack_wait/max_deliver: a consumer that
dies (or freezes) mid-resume lets the broker redeliver to another pod.

  stream  NIMBUS_HANDOFF        subjects nimbus.handoff.>
  consumer pods (queue group)   ack_wait 15s, max_deliver 3
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("nimbus.handoff")

STREAM = "NIMBUS_HANDOFF"
SUBJECT = "nimbus.handoff.resume"
GROUP = "pods"


class HandoffBus:
    def __init__(self, url: str, pod_id: str, ack_wait_s: float = 15.0, max_deliver: int = 3):
        self.url = url
        self.pod_id = pod_id
        self.ack_wait_s = ack_wait_s
        self.max_deliver = max_deliver
        self._nc: Any = None
        self._js: Any = None
        self._sub: Any = None
        self._consumer_task: Optional[asyncio.Task] = None
        self.consumed = 0

    async def connect(self) -> None:
        import nats  # optional extra: nimbus[lab]
        from nats.js.api import RetentionPolicy, StreamConfig

        self._nc = await nats.connect(self.url, name=f"nimbus-{self.pod_id}")
        self._js = self._nc.jetstream()
        try:
            await self._js.add_stream(StreamConfig(name=STREAM, subjects=[SUBJECT + ".>", SUBJECT],
                                                   retention=RetentionPolicy.WORK_QUEUE))
        except Exception:
            pass  # exists

    async def announce(self, session_id: str, reason: str = "graceful_shutdown", **extra: Any) -> None:
        payload = {"session_id": session_id, "from_pod": self.pod_id, "reason": reason,
                   "at": f"{time.time():.3f}", **extra}
        ack = await self._js.publish(SUBJECT, json.dumps(payload).encode())
        logger.warning("handoff announced: %s (%s) seq=%s", session_id, reason, getattr(ack, "seq", "?"))

    async def start_consumer(self, handler: Callable[[dict], Awaitable[bool]]) -> None:
        """handler(payload) -> True to ack (taken or nothing to do), False to nak."""
        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

        cfg = ConsumerConfig(durable_name=GROUP, deliver_group=GROUP, ack_policy=AckPolicy.EXPLICIT,
                             ack_wait=self.ack_wait_s, max_deliver=self.max_deliver,
                             deliver_policy=DeliverPolicy.ALL)
        self._sub = await self._js.subscribe(SUBJECT, queue=GROUP, durable=GROUP, config=cfg, manual_ack=True)

        async def loop() -> None:
            async for msg in self._sub.messages:
                try:
                    payload = json.loads(msg.data)
                except Exception:
                    await msg.term()
                    continue
                self.consumed += 1
                try:
                    ok = await handler(payload)
                except Exception as e:
                    logger.warning("handoff handler failed for %s: %s", payload.get("session_id"), e)
                    ok = False
                if ok:
                    await msg.ack()
                else:
                    await msg.nak(delay=1.0)

        self._consumer_task = asyncio.create_task(loop(), name="handoff-consumer")

    async def stop_consuming(self) -> None:
        """Leave the queue group BEFORE pausing sessions so our own announcements
        cannot be delivered back to this (dying) pod."""
        if self._sub is not None:
            try:
                await self._sub.unsubscribe()
            except Exception:
                pass
            self._sub = None
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except (asyncio.CancelledError, Exception):
                pass
            self._consumer_task = None

    async def close(self) -> None:
        await self.stop_consuming()
        if self._nc is not None:
            try:
                await self._nc.drain()
            except Exception:
                pass
            self._nc = None
