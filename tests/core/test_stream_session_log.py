"""StreamSessionLog: Valkey-stream store with an ownership fence at the write point.

Unit tests run on a small sync fake of redis (xrange/eval/rename) that emulates
the fenced-XADD script; `test_live_*` exercise the real Lua against a running
Valkey when NIMBUS_LEDGER_URL is reachable (nimbus-lab), otherwise skip.
"""

import json
import os

import pytest

from nimbus.core.session_log import OwnershipLostError, StreamSessionLog


class FakeStreamRedis:
    """Emulates: HGET epoch fence + XADD (via eval), xrange, rename, hset."""

    def __init__(self):
        self.h, self.streams, self.counters = {}, {}, {}

    def hset(self, key, mapping=None, **kw):
        self.h.setdefault(key, {}).update(mapping or kw)

    def eval(self, script, numkeys, *args):
        turn_key, stream_key, epoch, *events = args
        cur = self.h.get(turn_key, {}).get("epoch")
        if epoch != "" and cur is not None and cur != epoch:
            self.counters["ledger:rejected_writes"] = self.counters.get("ledger:rejected_writes", 0) + 1
            raise Exception(f"OWNERSHIP_LOST current={cur} mine={epoch}")
        s = self.streams.setdefault(stream_key, [])
        for e in events:
            s.append((f"{len(s)}-0", {"e": e}))
        return len(events)

    def xrange(self, key):
        return list(self.streams.get(key, []))

    def rename(self, src, dst):
        self.streams[dst] = self.streams.pop(src)


@pytest.fixture
def r():
    return FakeStreamRedis()


def test_events_land_in_the_stream_and_reload_contiguously(r):
    log = StreamSessionLog(r, "s1", epoch=1)
    r.hset("turn:s1", mapping={"epoch": "1"})
    log.append("user/message", {"message": {"role": "user", "content": "hi"}})
    log.append("turn/start", {"turn": 1})
    log.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})  # causal -> flush
    assert [json.loads(f["e"])["seq"] for _, f in r.xrange("sess:s1:log")] == [0, 1, 2]
    again = StreamSessionLog.load(r, "s1")
    assert [e.type for e in again.events] == ["user/message", "turn/start", "turn/end"]


def test_stale_epoch_is_rejected_on_first_causal_write_and_writer_is_fenced(r):
    r.hset("turn:s1", mapping={"epoch": "2"})       # another pod took over
    zombie = StreamSessionLog(r, "s1", epoch=1)
    zombie.append("assistant/message", {"message": {"role": "assistant", "content": "x"}})  # buffered
    with pytest.raises(OwnershipLostError):
        zombie.append("tool/result", {"message": {"role": "tool", "content": "y"}})       # causal -> flush -> rejected
    assert r.xrange("sess:s1:log") == []            # nothing from the zombie reached the stream
    assert zombie.fenced and zombie.rejected == 2
    zombie.append("turn/end", {"turn": 1, "reason": {"kind": "aborted"}})                  # backstop: silent no-op
    zombie.flush()
    assert r.xrange("sess:s1:log") == [] and r.counters["ledger:rejected_writes"] == 1


def test_unowned_session_writes_without_a_fence(r):
    log = StreamSessionLog(r, "s2")                 # e.g. fork seeding: no turn record, epoch None
    log.append("seed/applied", {"n": 1})
    assert len(r.xrange("sess:s2:log")) == 1


def test_open_repairs_a_crashed_tail_under_the_new_epoch(r):
    r.hset("turn:s3", mapping={"epoch": "1"})
    dead = StreamSessionLog(r, "s3", epoch=1)
    dead.append("user/message", {"message": {"role": "user", "content": "go"}})
    dead.append("turn/start", {"turn": 1})
    dead.append("step/start", {"turn": 1, "step": 1})
    dead.append("assistant/message", {"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]}})
    r.hset("turn:s3", mapping={"epoch": "2"})       # pod-b claims, then opens the session
    taken = StreamSessionLog.open(r, "s3", epoch=2)
    kinds = [(e.type, e.data.get("reason", {}).get("kind")) for e in taken.events[-3:]]
    assert kinds == [("tool/result", None), ("step/end", None), ("turn/end", "interrupted")]
    assert json.loads(r.xrange("sess:s3:log")[-1][1]["e"])["type"] == "turn/end"  # closers durable


def test_corrupt_stream_is_quarantined_not_truncated(r):
    r.streams["sess:s4:log"] = [("0-0", {"e": json.dumps({"seq": 5, "type": "x", "time": 0, "data": {}})})]
    log = StreamSessionLog.open(r, "s4")
    assert log.events == [] and any(k.startswith("sess:s4:log.corrupt.") for k in r.streams)


# --- live Lua against a real Valkey (nimbus-lab) ------------------------------------

def _live_client():
    url = os.environ.get("NIMBUS_LEDGER_URL", "redis://127.0.0.1:6379")
    try:
        import redis

        c = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.3)
        c.ping()
        return c
    except Exception:
        return None


@pytest.mark.skipif(_live_client() is None, reason="no Valkey reachable")
def test_live_fenced_xadd_rejects_old_epoch():
    c = _live_client()
    sid = "pytest-fence"
    for k in (f"sess:{sid}:log", f"turn:{sid}"):
        c.delete(k)
    c.hset(f"turn:{sid}", mapping={"epoch": "1"})
    owner = StreamSessionLog(c, sid, epoch=1)
    owner.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    c.hset(f"turn:{sid}", mapping={"epoch": "2"})    # takeover
    zombie = StreamSessionLog(c, sid, epoch=1)
    with pytest.raises(OwnershipLostError):
        zombie.append("turn/end", {"turn": 1, "reason": {"kind": "completed"}})
    assert len(c.xrange(f"sess:{sid}:log")) == 1
    for k in (f"sess:{sid}:log", f"turn:{sid}"):
        c.delete(k)
