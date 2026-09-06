"""Session-log contract gate (nimbus-lab R5.2): a reader opens a log only if its
contract >= the log's. A rollout that rolls the contract back would otherwise
misread the newer log — v1 pods resuming v2 sessions re-executed a step per session."""

import json

import pytest

from nimbus.core.session_log import (
    SESSION_LOG_CONTRACT,
    ContractNewerError,
    SessionEvent,
    SessionLog,
    log_contract,
)
from nimbus.core.storage import SessionStorage


def _write(path, contract):
    ev = [
        ("user/message", {"message": {"role": "user", "content": "go"}}),
        ("turn/start", {"turn": 1, "contract": contract}),
        ("step/start", {"turn": 1, "step": 1}),
        ("assistant/message", {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "Bash", "arguments": "{}"}}]}}),
    ]
    with open(path, "w") as f:
        for i, (t, d) in enumerate(ev):
            f.write(json.dumps({"seq": i, "type": t, "time": 1.0, "data": d}) + "\n")


def test_log_contract_is_the_highest_turn_stamp_and_defaults_to_one():
    ev = [SessionEvent(0, "turn/start", 1.0, {"turn": 1}), SessionEvent(1, "turn/start", 1.0, {"turn": 2, "contract": 3})]
    assert log_contract(ev) == 3 and log_contract(ev[:1]) == 1 and log_contract([]) == 1


def test_reader_refuses_a_newer_log_and_does_not_quarantine_it(tmp_path):
    p = tmp_path / "s.jsonl"
    _write(p, SESSION_LOG_CONTRACT + 1)
    with pytest.raises(ContractNewerError) as e:
        SessionLog.load(p)
    assert e.value.log_contract == SESSION_LOG_CONTRACT + 1 and e.value.mine == SESSION_LOG_CONTRACT
    with pytest.raises(ContractNewerError):
        SessionLog.open(p)  # not a ValueError: no *.corrupt rename, no repair
    assert p.exists() and not (tmp_path / "s.jsonl.corrupt").exists()
    _write(p, SESSION_LOG_CONTRACT)
    assert len(SessionLog.load(p).events) == 4  # same or older contract: readable


def test_snapshot_fallback_never_masks_a_newer_log(tmp_path, monkeypatch):
    monkeypatch.delenv("NIMBUS_LOG_STORE", raising=False)
    st = SessionStorage(str(tmp_path))
    st.save_session("s", "paused", messages=[{"role": "user", "content": "go"}] * 6, vcpu_state={})
    _write(tmp_path / "s.jsonl", SESSION_LOG_CONTRACT + 1)
    with pytest.raises(ContractNewerError):
        st.load_session("s")  # a short log used to lose to the fuller snapshot silently
