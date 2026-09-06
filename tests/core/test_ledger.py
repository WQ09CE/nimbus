"""Ledger (nimbus.infra.ledger): heartbeat liveness, ownership records, orphan scanner.

Runs against an in-memory fake of the handful of redis commands the ledger uses.
One manual clock drives both key expiry and the ledger's own time.time(), so
"pod died" (key expired) and "turn is older than the grace" are deterministic.
"""

import asyncio
import fnmatch

import pytest

from nimbus.infra.ledger import Ledger


class FakeRedis:
    def __init__(self):
        self.h, self.s, self.kv, self.exp, self.now = {}, {}, {}, {}, 1000.0

    def tick(self, seconds):
        self.now += seconds
        for k, t in list(self.exp.items()):
            if t <= self.now:
                self.h.pop(k, None)
                self.kv.pop(k, None)
                self.exp.pop(k, None)

    async def hset(self, key, mapping=None, **kw):
        self.h.setdefault(key, {}).update(mapping or kw)

    async def hsetnx(self, key, field, value):
        d = self.h.setdefault(key, {})
        if field in d:
            return 0
        d[field] = value
        return 1

    async def hgetall(self, key):
        return dict(self.h.get(key, {}))

    async def expire(self, key, ttl):
        self.exp[key] = self.now + ttl

    async def exists(self, key):
        return int(key in self.h or key in self.kv)

    async def delete(self, key):
        self.h.pop(key, None)
        self.kv.pop(key, None)
        self.exp.pop(key, None)

    async def sadd(self, key, member):
        self.s.setdefault(key, set()).add(member)

    async def smembers(self, key):
        return set(self.s.get(key, set()))

    async def incr(self, key):
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return self.kv[key]

    async def get(self, key):
        return self.kv.get(key)

    async def scan_iter(self, match="*"):
        for k in list(self.h) + list(self.kv):
            if fnmatch.fnmatch(k, match):
                yield k


@pytest.fixture
def fake(monkeypatch):
    f = FakeRedis()
    monkeypatch.setattr("nimbus.infra.ledger.time.time", lambda: f.now)  # ledger shares the fake clock
    return f


def _ledger(fake, pod):
    return Ledger("fake://", pod_id=pod, port=8000, dead_after_s=15, client=fake)


def test_heartbeat_registers_pod_and_dies_by_expiry(fake):
    a = _ledger(fake, "a")
    asyncio.run(a.heartbeat())
    assert asyncio.run(fake.exists("pod:a")) == 1 and "a" in asyncio.run(fake.smembers("pods"))
    fake.tick(16)  # frozen or killed: no heartbeat renews the key
    assert asyncio.run(fake.exists("pod:a")) == 0


def test_claim_and_release_only_by_same_request(fake):
    a = _ledger(fake, "a")
    asyncio.run(a.claim("s1", "req1"))
    assert asyncio.run(fake.hgetall("turn:s1"))["pod"] == "a"
    assert asyncio.run(a.release("s1", "other")) is False   # stale/foreign request keeps the record
    assert asyncio.run(a.release("s1", "req1")) is True
    assert asyncio.run(fake.exists("turn:s1")) == 0


def test_scanner_flags_turn_whose_pod_is_dead_exactly_once(fake):
    a, b = _ledger(fake, "a"), _ledger(fake, "b")
    asyncio.run(a.heartbeat())
    asyncio.run(b.heartbeat())
    asyncio.run(a.claim("s1", "req1"))
    assert asyncio.run(b.scan_once()) == []          # owner alive: nothing to report
    fake.tick(16)                                    # a stops beating, b keeps going
    asyncio.run(b.heartbeat())
    found = asyncio.run(b.scan_once())
    assert [f["session_id"] for f in found] == ["s1"]
    assert found[0]["pod"] == "a" and found[0]["detected_by"] == "b"
    assert asyncio.run(b.scan_once()) == []          # recorded once; a second scanner is a no-op
    assert asyncio.run(fake.get("ledger:orphans_detected")) == 1
    assert asyncio.run(fake.hgetall("orphan:s1"))["resolution"] == "none"


def test_claim_implies_liveness_and_fresh_turns_get_grace(fake):
    a, b = _ledger(fake, "a"), _ledger(fake, "b")
    asyncio.run(b.heartbeat())
    asyncio.run(a.claim("s1", "req1"))               # no prior heartbeat from a: claim co-writes it
    assert asyncio.run(fake.exists("pod:a")) == 1
    asyncio.run(fake.delete("pod:a"))                # ledger wipe / failover right after the claim
    fake.tick(5)
    assert asyncio.run(b.scan_once()) == []          # inside the dead_after grace: not an orphan
    fake.tick(11)                                    # grace over, pod:a still absent
    asyncio.run(b.heartbeat())
    assert [f["session_id"] for f in asyncio.run(b.scan_once())] == ["s1"]


def test_snapshot_reports_liveness_turns_and_orphans(fake):
    a, b = _ledger(fake, "a"), _ledger(fake, "b")
    asyncio.run(a.heartbeat())
    asyncio.run(b.heartbeat())
    asyncio.run(a.claim("s1", "r"))
    fake.tick(16)
    asyncio.run(b.heartbeat())
    asyncio.run(b.scan_once())
    snap = asyncio.run(b.snapshot())
    assert snap["pods"]["a"]["alive"] is False and snap["pods"]["b"]["alive"] is True
    assert "s1" in snap["turns"] and "s1" in snap["orphans"] and snap["orphans_detected"] == 1
