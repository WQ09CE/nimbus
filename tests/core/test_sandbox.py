"""Tests for the bash sandbox — profile building, env filtering, and
failure attribution (denial vs runner-failure vs ordinary failure)."""

import sys

import pytest

from nimbus.core.path_context import AgentPathContext
from nimbus.core.tools import sandbox
from nimbus.core.tools.bash import bash_command


def _test_ctx(tmp_path) -> AgentPathContext:
    return AgentPathContext(
        workspace_root=str(tmp_path),
        target_root=str(tmp_path),
        execution_cwd=str(tmp_path),
    )


# =============================================================================
# Env whitelist
# =============================================================================


class TestSandboxedEnv:
    def test_drops_credentials(self):
        env = sandbox.sandboxed_env({
            "PATH": "/usr/bin",
            "HOME": "/Users/x",
            "ANTHROPIC_API_KEY": "sk-secret",
            "MY_TOKEN": "t",
            "AWS_SECRET_ACCESS_KEY": "s",
        })
        assert env == {"PATH": "/usr/bin", "HOME": "/Users/x"}


# =============================================================================
# Profile building
# =============================================================================


class TestBuildProfile:
    def test_denies_network_and_confines_writes(self, tmp_path):
        profile = sandbox.build_profile([str(tmp_path)])
        assert "(deny network*)" in profile
        assert "(deny file-write*)" in profile
        assert str(tmp_path.resolve()) in profile

    def test_allow_network_flag(self, tmp_path):
        profile = sandbox.build_profile([str(tmp_path)], allow_network=True)
        assert "(deny network*)" not in profile


# =============================================================================
# Failure attribution (Seatbelt dialect)
# =============================================================================


class TestClassifyOutput:
    def test_ordinary_failure(self):
        assert sandbox.classify_output("gcc: error: no input files") is None

    def test_denial(self):
        out = "touch: /etc/x: Operation not permitted"
        assert sandbox.classify_output(out) == "denial"

    def test_runner_failure(self):
        out = "sandbox-exec: sandbox_compile_file: syntax error near line 3"
        assert sandbox.classify_output(out) == "runner-failure"

    def test_runner_failure_checked_before_denial(self):
        # A broken sandbox must never be misreported as a denial, even when
        # its message contains the denial dialect.
        out = "sandbox-exec: sandbox_apply: Operation not permitted"
        assert sandbox.classify_output(out) == "runner-failure"


# =============================================================================
# Bash integration: sandbox state is never silent
# =============================================================================


class TestBashExitCode:
    async def test_nonzero_exit_survives_cwd_sentinel(self, tmp_path, monkeypatch):
        # Regression: the cwd-sentinel echo used to mask the command's exit
        # status, reporting exit 0 for every failing command.
        monkeypatch.delenv("NIMBUS_BASH_SANDBOX", raising=False)
        result = await bash_command("false", _path_context=_test_ctx(tmp_path))
        assert result["ui_detail"]["exit_code"] == 1
        assert "Exit code: 1" in result["output"]

    async def test_cd_tracking_still_works(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NIMBUS_BASH_SANDBOX", raising=False)
        sub = tmp_path / "sub"
        sub.mkdir()
        ctx = _test_ctx(tmp_path)
        result = await bash_command(f"cd {sub}", _path_context=ctx)
        assert result["ui_detail"]["new_execution_cwd"] == str(sub)


class TestBashSandboxState:
    async def test_off_by_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NIMBUS_BASH_SANDBOX", raising=False)
        result = await bash_command("echo hi", _path_context=_test_ctx(tmp_path))
        assert result["ui_detail"]["sandbox"] == "off"
        assert "UNSANDBOXED" not in result["output"]

    async def test_requested_but_unavailable_is_observable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NIMBUS_BASH_SANDBOX", "1")
        monkeypatch.setattr(sandbox, "sandbox_available", lambda: False)
        result = await bash_command("echo hi", _path_context=_test_ctx(tmp_path))
        assert result["ui_detail"]["sandbox"] == "unavailable"
        assert "UNSANDBOXED" in result["output"]

    @pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
    async def test_active_denial_is_attributed(self, tmp_path, monkeypatch):
        if not sandbox.sandbox_available():
            pytest.skip("sandbox-exec not present")
        monkeypatch.setenv("NIMBUS_BASH_SANDBOX", "1")
        # Write outside writable roots: Seatbelt answers EPERM
        # ("Operation not permitted"), unlike the unsandboxed EACCES
        # ("Permission denied") — that difference is the dialect.
        result = await bash_command(
            "touch /usr/local/.nimbus-sb-test", _path_context=_test_ctx(tmp_path)
        )
        assert result["ui_detail"]["sandbox"] == "active"
        assert result["ui_detail"]["exit_code"] != 0
        assert result["ui_detail"].get("sandbox_verdict") == "denial"
        assert "sandbox denial" in result["output"]

    @pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS-only")
    async def test_active_success_has_no_verdict(self, tmp_path, monkeypatch):
        if not sandbox.sandbox_available():
            pytest.skip("sandbox-exec not present")
        monkeypatch.setenv("NIMBUS_BASH_SANDBOX", "1")
        result = await bash_command("echo hi", _path_context=_test_ctx(tmp_path))
        assert result["ui_detail"]["sandbox"] == "active"
        assert "sandbox_verdict" not in result["ui_detail"]
        assert "hi" in result["output"]
