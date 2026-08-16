"""Tests for the plan anchor — update_plan tool + MMU plan slot.

The plan is working memory (attention anchor), not durability: pinned near
the top of assembled context, immune to compaction, logged as plan/updated
for trace/restore.
"""

import asyncio
from pathlib import Path

import pytest

from nimbus.core.agent import AgentOS
from nimbus.core.mmu import MESSAGE_OVERHEAD, MMU, MMUConfig, estimate_text_tokens
from nimbus.core.path_context import AgentPathContext
from nimbus.core.storage import SessionStorage
from nimbus.core.session_log import SessionLog, derive_state
from nimbus.core.tools.update_plan import update_plan


class TestUpdatePlanTool:
    @pytest.mark.asyncio
    async def test_sets_mmu_plan_and_reports_progress(self, tmp_path):
        mmu = MMU()
        out = await update_plan(
            todos=["[x] read config", "[ ] write report", "run tests"],
            notes="config uses TOML",
            _mmu=mmu,
            _plan_mirror_path=str(tmp_path / "scratchpad.md"),
        )
        assert "1/3 done" in out
        assert "- [x] read config" in mmu.plan
        assert "- [ ] run tests" in mmu.plan  # bare item gets a checkbox
        assert "config uses TOML" in mmu.plan
        assert (tmp_path / "scratchpad.md").exists()  # human-readable mirror

    @pytest.mark.asyncio
    async def test_works_without_mmu_or_mirror(self):
        out = await update_plan(todos=["[ ] a"])
        assert "0/1 done" in out

    def test_mirror_path_uses_workspace_not_process_cwd(self, tmp_path, monkeypatch):
        workspace = tmp_path / "workspace"
        elsewhere = tmp_path / "server-cwd"
        workspace.mkdir()
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        context = AgentPathContext(
            workspace_root=str(workspace), target_root=str(workspace),
            execution_cwd=str(workspace),
        )
        path = Path(AgentOS._plan_mirror_path("sess_ok", context, None))
        assert path == workspace / ".nimbus" / "sessions" / "sess_ok" / "scratchpad.md"

    def test_mirror_path_uses_storage_and_session_cannot_escape(self, tmp_path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        context = AgentPathContext(
            workspace_root=str(workspace), target_root=str(workspace),
            execution_cwd=str(workspace),
        )
        storage = SessionStorage(str(tmp_path / "storage"))
        path = Path(AgentOS._plan_mirror_path("../../escape", context, storage))
        assert path.is_relative_to(storage.base_dir.resolve())
        assert ".." not in path.relative_to(storage.base_dir.resolve()).parts


class TestPlanAnchor:
    def test_plan_is_assembled_and_logged(self):
        log = SessionLog()
        mmu = MMU()
        mmu.event_sink = log.append
        mmu.set_plan("## Task Plan\n- [ ] step one")
        assembled = mmu.assemble_context()
        assert any("CURRENT PLAN" in str(m.get("content")) for m in assembled)
        assert [e.type for e in log.events] == ["plan/updated"]

    async def test_archive_budget_accounts_for_plan(self, monkeypatch):
        mmu = MMU(MMUConfig(max_context_tokens=1000, keep_recent_tokens=50))
        mmu.set_plan("p" * 800)  # ~200 tokens plus message overhead
        for i in range(8):
            mmu.add_user_message(f"m{i} " + "x" * 180)
        captured = {}

        def fake_drop(messages, target_tokens, keep_recent_tokens):
            captured["target"] = target_tokens
            return list(messages), ""

        monkeypatch.setattr("nimbus.core.mmu._smart_drop", fake_drop)
        monkeypatch.setattr(mmu, "_find_cut_point", lambda keep_recent_tokens: 0)
        await mmu.archive_and_reset()
        expected = int(1000 * 0.7) - (
            estimate_text_tokens(mmu.plan) + MESSAGE_OVERHEAD
        )
        assert captured["target"] <= expected

    def test_plan_survives_compaction(self):
        mmu = MMU(MMUConfig(max_context_tokens=2000))
        mmu.set_plan("## Task Plan\n- [x] done thing")
        for i in range(30):
            mmu.add_user_message(f"message {i} " + "x" * 200)
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            mmu.archive_and_reset()
        )
        assert "done thing" in mmu.plan  # anchor state: compaction can't touch it

    def test_derive_state_tracks_plan(self):
        log = SessionLog()
        log.append("user/message", {"message": {"role": "user", "content": "go"}})
        log.append("plan/updated", {"plan": "v1"})
        log.append("plan/updated", {"plan": "v2"})
        state = derive_state(log.events)
        assert state["plan"] == "v2"  # last write wins
        assert [m["content"] for m in state["messages"]] == ["go"]  # not surface state

    def test_seed_carries_plan(self):
        log = SessionLog()
        log.append("seed/applied", {"messages": [], "summary": "", "plan": "inherited"})
        assert derive_state(log.events)["plan"] == "inherited"
