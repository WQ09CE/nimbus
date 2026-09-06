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

    async def claim(self, session_id, request_id, fresh=True):
        self.epoch += 1
        return self.epoch

    async def release(self, session_id, request_id):
        return True

    async def resolve(self, session_id, resolution):
        self.resolutions.append(resolution)

    async def account(self, session_id, nbytes):
        self.charged = getattr(self, "charged", []) + [(session_id, nbytes)]

    async def strand(self, session_id, needed, kind, **facts):
        self.stranded = getattr(self, "stranded", []) + [(session_id, needed, kind, facts)]

    async def note_attempt(self, session_id, epoch, progress):
        marks = getattr(self, "marks", [])
        marks.append(progress)
        self.marks = marks
        n = 0
        for m in reversed(marks):
            if m != progress:
                break
            n += 1
        return n


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


def test_ingest_over_budget_is_quarantined_not_resumed(manager, tmp_path, monkeypatch):
    """R4: the cost axis — a rerunnable in-flight call is not enough when the turn had already
    pulled more than the budget into its (dead) pod; resuming would repeat the 0903 cascade."""
    monkeypatch.setenv("NIMBUS_RESUME_INGEST_BUDGET_MB", "8")
    set_repeat_resolver(lambda n: "keyed")
    _crashed_session(tmp_path, "Write")
    resumed = []
    manager.resume_interrupted = lambda sid: resumed.append(sid) or asyncio.sleep(0)
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "a", "request_id": "r",
                                   "bytes": str(9 * 2**20), "owner_mem_pct": "81"}))
    assert resumed == [] and manager._ledger.resolutions == ["quarantine:ingest=9MB"]
    tail = [json.loads(line) for line in open(tmp_path / f"{SID}.jsonl")][-1]
    assert tail["type"] == "turn/end" and tail["data"]["reason"]["kind"] == "interrupted"
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "a", "request_id": "r", "bytes": str(7 * 2**20)}))
    assert manager._ledger.resolutions[-1] == "skipped:balanced"  # already closed by the quarantine


def test_ingest_is_metered_at_the_door_and_posted_past_the_flush_threshold(manager):
    """R4: chunks are charged as they stream (once), the final result adds only the remainder,
    and a turn past INGEST_FLUSH_BYTES posts to the ledger before any step seam."""
    async def run():
        manager._meter_ingest(SID, "c1", 600_000)
        manager._meter_ingest(SID, "c1", 600_000)          # 1.2 MB streamed -> flush task scheduled
        await asyncio.sleep(0)
        manager._meter_ingest(SID, "c1", 1_500_000, final=True)  # raw is 1.5 MB: only +300 KB new
        await manager._charge_ingest(SID)
    asyncio.run(run())
    assert manager._ledger.charged == [(SID, 1_200_000), (SID, 300_000)]
    assert manager._streamed == {} and manager._ingest == {}


def test_a_turn_that_lost_max_attempts_owners_without_progress_is_quarantined(manager, tmp_path, monkeypatch):
    """R4 stall drill: a poison turn (stalls / kills whoever runs it) bounces between pods, each
    takeover a new epoch. Temporal stops at maximum_attempts; so does the ledger's admission.
    R5.2: only owners lost WITHOUT progress count — a rolling crash of the fleet is not a poison turn."""
    monkeypatch.setenv("NIMBUS_RESUME_MAX_ATTEMPTS", "2")
    set_repeat_resolver(lambda n: "keyed")
    _crashed_session(tmp_path, "Write")
    resumed = []

    async def fake_resume(sid):
        resumed.append(sid)
        return True

    manager.resume_interrupted = fake_resume
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "a", "request_id": "r", "epoch": "1"}))
    assert resumed == [SID] and manager._ledger.resolutions == ["resume"]
    manager._ledger.marks = ["0", "3"]  # progressed between deaths (3 real results now): the count restarts
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "b", "request_id": "r", "epoch": "2"}))
    assert resumed == [SID, SID] and manager._ledger.resolutions[-1] == "resume"
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "c", "request_id": "r", "epoch": "3"}))
    assert resumed == [SID, SID] and manager._ledger.resolutions[-1] == "quarantine:attempts=2"  # same marker twice
    tail = [json.loads(line) for line in open(tmp_path / f"{SID}.jsonl")][-1]
    assert tail["type"] == "turn/end" and tail["data"]["reason"]["kind"] == "interrupted"


def test_binding_written_at_a_seam_reaches_the_running_loops_metadata(manager, tmp_path):
    """R5 rolling-deploy drill: a handed-off turn finished on the new pod and the completion
    core dump (loop metadata captured at run start) put the OLD pod's pause binding back."""
    SessionStorage(str(tmp_path)).save_session(SID, "active", messages=[], vcpu_state={},
                                               metadata={"sandbox_binding": {"snapshot_id": "old", "lease_id": "l0"}})

    class FakeLoop:
        metadata = {"sandbox_binding": {"snapshot_id": "old", "lease_id": "l0"}, "llm_config": {}}

    manager._active_loops[SID] = FakeLoop()
    new = {"backend": "vcompute", "snapshot_id": "new", "lease_id": "l1"}
    asyncio.run(manager._save_sandbox_binding(SID, new))
    assert FakeLoop.metadata["sandbox_binding"] == new
    assert manager._storage.load_session(SID)["metadata"]["sandbox_binding"] == new
    asyncio.run(manager._save_sandbox_binding(SID, None))
    assert "sandbox_binding" not in FakeLoop.metadata


def test_orphan_written_by_a_newer_contract_is_refused_untouched(manager, tmp_path):
    """R5.2 rollback: the v1 scanner finds a crashed v2 turn. It must neither grade nor repair it —
    refuse, tell the client, leave the log as it is."""
    from nimbus.core.session_log import SESSION_LOG_CONTRACT
    set_repeat_resolver(lambda n: "keyed")
    _crashed_session(tmp_path, "Write")
    lines = [json.loads(line) for line in open(tmp_path / f"{SID}.jsonl")]
    lines[1]["data"]["contract"] = SESSION_LOG_CONTRACT + 1
    with open(tmp_path / f"{SID}.jsonl", "w") as f:
        for e in lines:
            f.write(json.dumps(e) + "\n")
    resumed = []
    manager.resume_interrupted = lambda sid: resumed.append(sid) or asyncio.sleep(0)
    asyncio.run(manager.on_orphan({"session_id": SID, "pod": "a", "request_id": "r"}))
    assert resumed == [] and manager._ledger.resolutions == [f"refused:contract={SESSION_LOG_CONTRACT + 1}"]
    assert [json.loads(line) for line in open(tmp_path / f"{SID}.jsonl")] == lines  # untouched
    assert manager._ledger.stranded[0][:3] == (SID, SESSION_LOG_CONTRACT + 1, "orphan")  # left for a capable pod


def test_handoff_of_a_newer_contract_session_is_acked_and_stranded(manager):
    from nimbus.core.session_log import ContractNewerError

    async def refuse(sid):
        raise ContractNewerError(2, 1, "test")

    manager.resume_session = refuse
    assert asyncio.run(manager.on_handoff({"session_id": SID, "from_pod": "d"})) is True
    assert manager._ledger.stranded == [(SID, 2, "paused", {"from_pod": "d"})]
