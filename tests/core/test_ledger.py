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

    async def hdel(self, key, *fields):
        d = self.h.get(key, {})
        for f in fields:
            d.pop(f, None)

    async def eval(self, script, numkeys, *args):
        # emulate Ledger._CLAIM: HINCRBY epoch + HSET owner fields (+ bytes=0 when fresh) + EXPIRE
        key, pod, req, started, ttl, inc, fresh = args
        d = self.h.setdefault(key, {})
        d["epoch"] = str(int(d.get("epoch", "0")) + 1)
        d.update({"pod": pod, "request_id": req, "started": started, "inc": inc})
        if fresh == "1":
            d["bytes"] = "0"
        self.exp[key] = self.now + int(ttl)
        return int(d["epoch"])

    async def hincrby(self, key, field, n):
        d = self.h.setdefault(key, {})
        d[field] = str(int(d.get(field, "0")) + int(n))
        return int(d[field])

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
    a, b = _ledger(fake, "a"), _ledger(fake, "b")
    assert asyncio.run(a.claim("s1", "req1")) == 1               # first epoch
    assert asyncio.run(fake.hgetall("turn:s1"))["pod"] == "a"
    assert asyncio.run(a.release("s1", "other")) is False        # stale/foreign request keeps the record
    assert asyncio.run(a.release("s1", "req1")) is True
    rec = asyncio.run(fake.hgetall("turn:s1"))
    assert "pod" not in rec and rec["epoch"] == "1"               # released, but the epoch never resets
    assert asyncio.run(b.claim("s1", "req2")) == 2               # takeover bumps the fence token


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


# -- R4: incarnation, per-epoch orphans, ingest accounting, last words --------


def test_same_id_restart_exposes_predecessor_turns_at_once(fake):
    """0903 §7.2: the container restarted under the same pod name. Before R4 the successor's
    heartbeat made the predecessor's turns look owned-and-alive forever."""
    a1, b = _ledger(fake, "a"), _ledger(fake, "b")
    asyncio.run(a1.heartbeat())
    asyncio.run(b.heartbeat())
    asyncio.run(a1.claim("s1", "req1"))
    a2 = _ledger(fake, "a")  # same pod id, new process
    assert a2.inc != a1.inc
    fake.tick(2)
    asyncio.run(a2.heartbeat())  # pod:a exists again, 2 s after the claim
    found = asyncio.run(b.scan_once())
    assert [f["session_id"] for f in found] == ["s1"] and found[0]["inc"] == a1.inc  # no grace: proof, not loss
    assert asyncio.run(a2.scan_once()) == []  # recorded once; the successor itself may also be the detector


def test_turn_is_an_orphan_again_when_its_rescuer_dies(fake):
    """The victim of the R4 drill: resumed on b (epoch 2), then b died too — a second orphaning
    must be recorded (HSETNX per epoch), or nobody ever resumes it."""
    a, b, c = _ledger(fake, "a"), _ledger(fake, "b"), _ledger(fake, "c")
    for led in (a, b, c):
        asyncio.run(led.heartbeat())
    asyncio.run(a.claim("s1", "r1"))
    fake.tick(16)
    for led in (b, c):
        asyncio.run(led.heartbeat())
    assert [f["epoch"] for f in asyncio.run(c.scan_once())] == ["1"]
    asyncio.run(b.claim("s1", "r2", fresh=False))   # rescue on b
    fake.tick(16)                                    # b dies during the rescue
    asyncio.run(c.heartbeat())
    found = asyncio.run(c.scan_once())
    assert [(f["session_id"], f["epoch"], f["pod"]) for f in found] == [("s1", "2", "b")]
    assert asyncio.run(fake.get("ledger:orphans_detected")) == 2
    assert asyncio.run(fake.hgetall("orphan:s1"))["resolution"] == "none"  # latest record wins


def test_ingest_is_charged_per_turn_and_kept_across_a_takeover(fake):
    a, b = _ledger(fake, "a"), _ledger(fake, "b")
    asyncio.run(a.claim("s1", "r1"))
    asyncio.run(a.account("s1", 5 * 2**20))
    asyncio.run(a.account("s1", 3 * 2**20))
    assert asyncio.run(fake.hgetall("turn:s1"))["bytes"] == str(8 * 2**20)
    asyncio.run(b.claim("s1", "r2", fresh=False))   # resume/takeover continues the same turn's count
    assert asyncio.run(fake.hgetall("turn:s1"))["bytes"] == str(8 * 2**20)
    asyncio.run(b.claim("s1", "r3"))                 # a new user turn starts from zero
    assert asyncio.run(fake.hgetall("turn:s1"))["bytes"] == "0"


def test_orphan_record_carries_ingest_and_the_owners_last_words(fake):
    a, b = _ledger(fake, "a"), _ledger(fake, "b")
    asyncio.run(a.heartbeat())
    asyncio.run(b.heartbeat())
    asyncio.run(a.claim("s1", "r1"))
    asyncio.run(a.account("s1", 12 * 2**20))
    last = asyncio.run(fake.hgetall("podlast:a"))
    assert last["inc"] == a.inc and {"rss_mb", "lag_ms", "gc2_ms", "prev_oom_kills"} <= set(last)
    fake.tick(16)                                    # pod:a expired, podlast:a did not
    assert asyncio.run(fake.exists("pod:a")) == 0 and asyncio.run(fake.exists("podlast:a")) == 1
    asyncio.run(b.heartbeat())
    rec = asyncio.run(b.scan_once())[0]
    assert rec["bytes"] == str(12 * 2**20) and rec["inc"] == a.inc
    snap = asyncio.run(b.snapshot())
    assert snap["pods"]["a"]["alive"] is False and snap["pods"]["a"]["inc"] == a.inc
