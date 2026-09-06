"""Graceful handoff at the manager boundary (nimbus-lab R3.3): pause_all quiesces
running sessions, handoff_all announces the paused ones, on_handoff resumes."""

import asyncio
from datetime import datetime

import pytest

from nimbus.core.storage import SessionStorage
from nimbus.server.permission import PermissionManager
from nimbus.server.session import SessionManagerV2
from nimbus.server.sse import SSEHub


class FakeBus:
    def __init__(self):
        self.announced, self.consuming = [], True

    async def stop_consuming(self):
        self.consuming = False

    async def announce(self, session_id, reason="graceful_shutdown", **extra):
        self.announced.append((session_id, reason))


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.delenv("NIMBUS_LOG_STORE", raising=False)
    m = SessionManagerV2(SSEHub(), PermissionManager())
    m._storage = SessionStorage(str(tmp_path))
    return m


def _save(manager, sid, status):
    manager._storage.save_session(session_id=sid, status=status, messages=[], vcpu_state={},
                                  metadata={"name": sid, "created_at": datetime.now().isoformat()})


def test_pause_all_requests_seam_pause_and_reports_paused_sessions(manager):
    _save(manager, "s1", "active")
    paused_requests = []

    class FakeLoop:
        def request_pause(self):
            paused_requests.append("s1")
            _save(manager, "s1", "paused")      # the loop reaches its seam...
            manager.unregister_task("s1")       # ...and the run ends

    async def run():
        manager._active_loops["s1"] = FakeLoop()
        manager.register_task("s1", asyncio.current_task())
        return await manager.pause_all(timeout_s=2)

    assert asyncio.run(run()) == ["s1"] and paused_requests == ["s1"]


def test_handoff_all_stops_consuming_before_announcing(manager):
    _save(manager, "s1", "active")
    bus = FakeBus()

    class FakeLoop:
        def request_pause(self):
            assert bus.consuming is False        # we must have left the queue group already
            _save(manager, "s1", "paused")
            manager.unregister_task("s1")

    async def run():
        manager._active_loops["s1"] = FakeLoop()
        manager.register_task("s1", asyncio.current_task())
        return await manager.handoff_all(bus, timeout_s=2)

    assert asyncio.run(run()) == ["s1"] and bus.announced == [("s1", "graceful_shutdown")]


def test_on_handoff_resumes_paused_sessions_and_acks_the_rest(manager):
    _save(manager, "s1", "paused")
    calls = []

    async def fake_resume(sid):
        calls.append(sid)
        return {"success": True}

    manager.resume_session = fake_resume
    assert asyncio.run(manager.on_handoff({"session_id": "s1", "from_pod": "a"})) is True
    assert calls == ["s1"]

    async def refused(sid):
        return {"success": False, "error": "Session is 'completed', not paused"}

    manager.resume_session = refused
    assert asyncio.run(manager.on_handoff({"session_id": "s1", "from_pod": "a"})) is True   # nothing to redeliver

    async def transient(sid):
        return {"success": False, "error": "Session not found"}

    manager.resume_session = transient
    assert asyncio.run(manager.on_handoff({"session_id": "s1", "from_pod": "a"})) is False  # nak -> redeliver
