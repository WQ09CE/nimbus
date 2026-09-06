"""Crash grading by repeat class + the resume plan (nimbus-lab R3)."""

from nimbus.core.session_log import (
    TOOL_NOT_STARTED,
    TOOL_OUTCOME_UNKNOWN,
    TOOL_RESUMABLE,
    SessionEvent,
    grade_unanswered_calls,
    interrupted_turn_closers,
    resumable_calls,
    set_repeat_resolver,
)


def _tc(i, name):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": "{}"}}


def test_first_unanswered_once_call_is_unknown_and_the_rest_not_started(monkeypatch):
    set_repeat_resolver(lambda n: {"Read": "free", "Bash": "once"}[n])
    graded = grade_unanswered_calls([_tc(1, "Bash"), _tc(2, "Read")], answered_ids=set())
    assert [(c["id"], code) for c, code, _ in graded] == [("c1", TOOL_OUTCOME_UNKNOWN), ("c2", TOOL_NOT_STARTED)]
    set_repeat_resolver(None)


def test_first_unanswered_free_or_keyed_call_is_resumable():
    set_repeat_resolver(lambda n: "keyed")
    graded = grade_unanswered_calls([_tc(1, "Write")], answered_ids=set())
    assert graded[0][1] == TOOL_RESUMABLE and "safe to run again" in graded[0][2]
    set_repeat_resolver(None)


def test_unknown_tool_defaults_to_once():
    set_repeat_resolver(None)
    assert grade_unanswered_calls([_tc(1, "Mystery")], set())[0][1] == TOOL_OUTCOME_UNKNOWN


def test_without_a_registry_builtin_declarations_and_override_still_apply(monkeypatch):
    set_repeat_resolver(None)  # no AgentOS in this process yet (orphan decision on another pod)
    monkeypatch.delenv("NIMBUS_REPEAT_OVERRIDE", raising=False)
    assert grade_unanswered_calls([_tc(1, "Read")], set())[0][1] == TOOL_RESUMABLE   # builtin: free
    assert grade_unanswered_calls([_tc(1, "Bash")], set())[0][1] == TOOL_OUTCOME_UNKNOWN  # builtin: once
    monkeypatch.setenv("NIMBUS_REPEAT_OVERRIDE", "Bash=keyed")
    assert grade_unanswered_calls([_tc(1, "Bash")], set())[0][1] == TOOL_RESUMABLE


def _crashed_log(tool="Bash"):
    ev = [
        ("user/message", {"message": {"role": "user", "content": "go"}}),
        ("turn/start", {"turn": 1}),
        ("step/start", {"turn": 1, "step": 1}),
        ("assistant/message", {"message": {"role": "assistant", "content": "", "tool_calls": [_tc(1, tool), _tc(2, tool)]}}),
    ]
    return [SessionEvent(seq=i, type=t, time=1.0, data=d) for i, (t, d) in enumerate(ev)]


def test_resume_plan_lists_rerunnable_graded_calls_in_order():
    set_repeat_resolver(lambda n: "keyed")
    events = _crashed_log()
    repaired = events + interrupted_turn_closers(events)
    plan = resumable_calls(repaired)
    assert [(p["tool_call"]["id"], p["code"]) for p in plan] == [("c1", TOOL_RESUMABLE), ("c2", TOOL_NOT_STARTED)]
    set_repeat_resolver(None)


def test_resume_plan_excludes_once_in_flight_but_keeps_not_started():
    set_repeat_resolver(None)  # everything is 'once'
    events = _crashed_log()
    plan = resumable_calls(events + interrupted_turn_closers(events))
    assert [(p["tool_call"]["id"], p["code"]) for p in plan] == [("c2", TOOL_NOT_STARTED)]


def test_resume_plan_is_empty_for_a_completed_turn():
    events = _crashed_log() + [SessionEvent(seq=4, type="turn/end", time=1.0, data={"turn": 1, "reason": {"kind": "completed"}})]
    assert resumable_calls(events) == []


def test_replayed_result_replaces_its_placeholder_in_the_projection():
    """Second-generation resume (nimbus-lab R4): the surface rebuilt from the log must hold ONE
    result per call id — the replay's real result in place of the synthetic placeholder —
    otherwise the next taking-over pod (and a strict provider) sees a duplicate tool_call_id."""
    from nimbus.core.session_log import SessionEvent, check_invariants, derive_state

    tc = {"id": "c7", "type": "function", "function": {"name": "Bash", "arguments": "{}"}}
    raw = [
        ("user/message", {"message": {"role": "user", "content": "go"}}),
        ("turn/start", {"turn": 1}),
        ("step/start", {"turn": 1, "step": 1}),
        ("assistant/message", {"message": {"role": "assistant", "content": "", "tool_calls": [tc]}}),
        ("tool/result", {"message": {"role": "tool", "content": "[TOOL_RESUMABLE] crashed", "name": "Bash", "tool_call_id": "c7"},
                         "synthetic": True, "code": "TOOL_RESUMABLE"}),
        ("step/end", {"turn": 1, "step": 1, "synthetic": True}),
        ("turn/end", {"turn": 1, "reason": {"kind": "interrupted"}, "synthetic": True}),
        ("turn/start", {"turn": 2, "continues": 1}),
        ("step/start", {"turn": 2, "step": 1, "resume_replay": True}),
        ("tool/result", {"message": {"role": "tool", "content": "step-7", "name": "Bash", "tool_call_id": "c7"},
                         "resumed": True, "replaces_seq": 4, "graded": "TOOL_RESUMABLE"}),
        ("step/end", {"turn": 2, "step": 1, "resume_replay": True}),
        ("turn/end", {"turn": 2, "reason": {"kind": "completed"}}),
    ]
    events = [SessionEvent(seq=i, type=t, time=1.0, data=d) for i, (t, d) in enumerate(raw)]
    tools = [m for m in derive_state(events)["messages"] if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["c7"] and tools[0]["content"] == "step-7"
    assert check_invariants(events) == []


def test_replay_step_ends_with_a_seam_event():
    """R5.2: the replay step must yield step_end like any other step — the server binds the
    workspace at that seam; without it the NEXT crash restores the snapshot from before the
    replay and the replayed step's effects vanish from the machine."""
    import asyncio
    from types import SimpleNamespace

    from nimbus.core.loop import RuntimeLoop
    from nimbus.core.mmu import MMU
    from nimbus.core.protocol import ToolResult
    from tests.core.test_loop import MockVCPU

    vcpu = MockVCPU([])
    calls = []

    async def syscall_tool(action):
        calls.append(action.name)
        return ToolResult(status="OK", output="step-3")

    vcpu.gate = SimpleNamespace(syscall_tool=syscall_tool)
    loop = RuntimeLoop(vcpu, MMU())
    loop._turn = 2
    loop._resume_plan = [{"tool_call": {"id": "c3", "type": "function", "function": {"name": "Bash", "arguments": "{}"}},
                          "code": "TOOL_RESUMABLE", "seq": 7}]

    async def run():
        return [ev async for ev in loop._replay_resumable_calls()]

    events = asyncio.run(run())
    assert calls == ["Bash"]
    assert [e["type"] for e in events] == ["resume_replay", "step_end"]
    assert events[1]["resume_replay"] is True and events[1]["turn"] == 2
    assert [e.type for e in loop.session_log.events][-2:] == ["tool/result", "step/end"]


def _chain(after_crash_results, in_flight="Bash"):
    """user msg, turn 1 with N completed Bash results, then a call in flight (crash), repaired."""
    from nimbus.core.session_log import SessionEvent, interrupted_turn_closers
    raw = [("user/message", {"message": {"role": "user", "content": "go"}}), ("turn/start", {"turn": 1})]
    for k in range(1, after_crash_results + 1):
        raw += [("step/start", {"turn": 1, "step": k}),
                ("assistant/message", {"message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"c{k}", "type": "function", "function": {"name": "Bash", "arguments": "{}"}}]}}),
                ("tool/result", {"message": {"role": "tool", "content": f"step-{k}", "name": "Bash", "tool_call_id": f"c{k}"}}),
                ("step/end", {"turn": 1, "step": k})]
    k = after_crash_results + 1
    raw += [("step/start", {"turn": 1, "step": k}),
            ("assistant/message", {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{k}", "type": "function", "function": {"name": in_flight, "arguments": "{}"}}]}})]
    ev = [SessionEvent(seq=i, type=t, time=1.0, data=d) for i, (t, d) in enumerate(raw)]
    return ev + interrupted_turn_closers(ev)


def test_results_logged_after_the_binding_cut_are_redone_before_the_in_flight_call():
    """R5.2 rolling crash: the binding was taken at the seam after step 1 (seq 5); steps 2 and 3
    completed in the log but the restored workspace predates them — redo them, then the in-flight
    step 4. Without the cut only step 4 would run and steps 2–3 would vanish from the machine."""
    from nimbus.core.session_log import (
        TOOL_REDO,
        TOOL_RESUMABLE,
        resumable_calls,
        set_repeat_resolver,
    )
    set_repeat_resolver(lambda n: "keyed")
    try:
        ev = _chain(3)
        cut = next(e.seq for e in ev if e.type == "step/end" and e.data.get("step") == 1) + 1
        plan = resumable_calls(ev, after_seq=cut)
        assert [(p["tool_call"]["id"], p["code"]) for p in plan] == [("c2", TOOL_REDO), ("c3", TOOL_REDO), ("c4", TOOL_RESUMABLE)]
        assert [(p["tool_call"]["id"], p["code"]) for p in resumable_calls(ev)] == [("c4", TOOL_RESUMABLE)]  # no cut: log tail
    finally:
        set_repeat_resolver(None)


def test_a_once_result_after_the_cut_is_flagged_not_redone():
    from nimbus.core.session_log import TOOL_ONCE_AFTER_CUT, resumable_calls, set_repeat_resolver
    set_repeat_resolver(lambda n: "once")
    try:
        ev = _chain(2)
        plan = resumable_calls(ev, after_seq=1)
        assert [p["code"] for p in plan][:2] == [TOOL_ONCE_AFTER_CUT, TOOL_ONCE_AFTER_CUT]
    finally:
        set_repeat_resolver(None)
