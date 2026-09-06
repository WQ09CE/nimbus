"""Orphan admission at the server manager (nimbus-lab R3): resume iff the
in-flight call is safe to rerun; otherwise fast-fail. Runs on the file log store."""

import asyncio
import json

import pytest

from nimbus.core.session_log import set_repeat_resolver
from nimbus.core.storage import SessionStorage
from nimbus.server.permission import PermissionManager
from nimbus.server.session import SessionManagerV2
from nimbus.server.sse import SSEHub

SID = "sess_orphan"


class FakeLedger:
    pod_id = "b"

    def __init__(self):
        self.resolutions, self.epoch = [], 0

    async def claim(self, session_id, request_id):
        self.epoch += 1
        return self.epoch

    async def release(self, session_id, request_id):
        return True

    async def resolve(self, session_id, resolution):
        self.resolutions.append(resolution)


def _tc(i, name):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}


def _crashed_session(base, in_flight_tool):
    """A session whose log ends inside turn 1 with one call in flight (crash signature)."""
    ev = [
        ("user/message", {"message": {"role": "user", "content": "go"}}),
        ("turn/start", {"turn": 1}),
        ("step/start", {"turn": 1, "step": 1}),
        ("assistant/message", {"message": {"role": "assistant", "content": "", "tool_calls": [_tc(1, in_flight_tool)]}}),
    ]
    with open(base / f"{SID}.jsonl", "w") as f:
        for i, (t, d) in enumerate(ev):
            f.write(json.dumps({"seq": i, "type": t, "time": 1.0, "data": d}) + "\n")
    SessionStorage(str(base)).save_session(SID, "active", messages=[], vcpu_state={})


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.delenv("NIMBUS_LOG_STORE", raising=False)
    m = SessionManagerV2(SSEHub(), PermissionManager(), ledger=FakeLedger())
    m._storage = SessionStorage(str(tmp_path))
    yield m
    set_repeat_resolver(None)


def test_once_in_flight_fast_fails_and_repairs_the_log(manager, tmp_path):
    set_repeat_resolver(lambda n: "once")
    _crashed_session(tmp_path, "Bash")
    resumed = []
    manager.resume_interrupted = lambda sid: resumed.append(sid) or asyncio.sleep(0)
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "a", "request_id": "r"}))
    assert manager._ledger.resolutions == ["fast_fail:Bash"] and resumed == []
    tail = [json.loads(line) for line in open(tmp_path / f"{SID}.jsonl")][-3:]
    assert [e["type"] for e in tail] == ["tool/result", "step/end", "turn/end"]
    assert tail[0]["data"]["code"] == "TOOL_OUTCOME_UNKNOWN" and tail[2]["data"]["reason"]["kind"] == "interrupted"


def test_rerunnable_in_flight_is_resumed_here(manager, tmp_path):
    set_repeat_resolver(lambda n: "keyed")
    _crashed_session(tmp_path, "Write")
    resumed = []

    async def fake_resume(sid):
        resumed.append(sid)
        return True

    manager.resume_interrupted = fake_resume
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "a", "request_id": "r"}))
    assert resumed == [SID] and manager._ledger.resolutions == ["resume"]


def test_own_pod_and_balanced_logs_are_skipped(manager, tmp_path):
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "b", "request_id": "r"}))
    assert manager._ledger.resolutions == ["skipped:local"]
    with open(tmp_path / f"{SID}.jsonl", "w") as f:
        f.write(json.dumps({"seq": 0, "type": "turn/start", "time": 1.0, "data": {"turn": 1}}) + "\n")
        f.write(json.dumps({"seq": 1, "type": "turn/end", "time": 1.0, "data": {"turn": 1, "reason": {"kind": "completed"}}}) + "\n")
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "a", "request_id": "r"}))
    assert manager._ledger.resolutions[-1] == "skipped:balanced"
