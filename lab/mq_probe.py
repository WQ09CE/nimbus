"""NATS JetStream probe for the frozen-consumer drill: publish N handoff-shaped
messages (session ids that no pod holds, so consumers just ack) and print the
consumer's ack_pending / redelivered counters over time.
usage: mq_probe.py publish N | mq_probe.py watch SECONDS | mq_probe.py purge"""

import asyncio
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from nimbus.infra.handoff import GROUP, STREAM, HandoffBus  # noqa: E402


async def publish(n: int) -> None:
    bus = HandoffBus(os.environ.get("NIMBUS_HANDOFF_URL", "nats://127.0.0.1:4222"), pod_id="probe")
    await bus.connect()
    for i in range(n):
        await bus.announce(f"probe-{int(time.time())}-{i}", reason="mq_probe")
    await bus.close()


async def purge() -> None:
    """Drop every message still in the handoff stream (stale announcements from earlier drills)."""
    bus = HandoffBus(os.environ.get("NIMBUS_HANDOFF_URL", "nats://127.0.0.1:4222"), pod_id="probe")
    await bus.connect()
    await bus._js.purge_stream(STREAM)
    await bus.close()
    print("purged", STREAM)


def consumer_state() -> dict:
    d = json.load(urllib.request.urlopen("http://127.0.0.1:8222/jsz?consumers=true&streams=true", timeout=3))
    for acc in d.get("account_details", []):
        for s in acc.get("stream_detail", []):
            if s["name"] != STREAM:
                continue
            for c in s.get("consumer_detail", []):
                if c["name"] == GROUP:
                    return {"pending": s["state"]["messages"], "ack_pending": c.get("num_ack_pending", 0),
                            "redelivered": c.get("num_redelivered", 0), "delivered": c.get("delivered", {}).get("consumer_seq"),
                            "ack_floor": c.get("ack_floor", {}).get("consumer_seq")}
    return {}


def watch(seconds: float) -> None:
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < seconds:
        st = consumer_state()
        if st != last:
            print(f"  +{time.monotonic() - t0:5.1f}s {st}", flush=True)
            last = st
        time.sleep(1)


if __name__ == "__main__":
    if sys.argv[1:2] == ["publish"]:
        asyncio.run(publish(int(sys.argv[2]) if len(sys.argv) > 2 else 4))
    elif sys.argv[1:2] == ["purge"]:
        asyncio.run(purge())
    else:
        watch(float(sys.argv[2]) if len(sys.argv) > 2 else 30)
