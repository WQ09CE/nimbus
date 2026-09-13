import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeAPI
from garminconnect import Garmin, GarminConnectConnectionError, GarminConnectTooManyRequestsError

from garmin_readiness import cli
from garmin_readiness.features import baseline_report
from garmin_readiness.provider import METHODS, account_identity, authenticate, sync
from garmin_readiness.storage import LocalError


def test_installed_library_cn_and_read_methods_no_network():
    api = Garmin(is_cn=True, retry_attempts=0)
    assert api.client.domain == "garmin.cn"
    assert all(callable(getattr(api, name)) for name in METHODS.values())
    assert api.retry_attempts == 0


def test_installed_sdk_private_token_roundtrip_without_network(archive):
    api = Garmin(is_cn=True, retry_attempts=0)
    api.client.di_token = "synthetic-invalid-access-token"
    api.client.di_refresh_token = "synthetic-invalid-refresh-token"
    api.client.di_client_id = "synthetic-client"
    api.client.dump(str(archive.tokens))
    path = archive.tokens / "garmin_tokens.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(json.loads(path.read_text())) == {"di_token", "di_refresh_token", "di_client_id"}
    api.client.load(str(archive.tokens))
    assert api.client.di_token == "synthetic-invalid-access-token"


def test_synthetic_sync_is_read_only_repeatable_and_offline_report(archive):
    api = FakeAPI()
    sleeps = []
    day = date(2026, 9, 10)
    result = sync(archive, api, day, day, sleep=sleeps.append)
    assert result["ok"] == 8
    assert len(sleeps) == 7 and min(sleeps) >= 1
    assert api.calls[0] == ("get_heart_rates", "2026-09-09")
    assert set(name for name, _ in api.calls) == set(METHODS.values())
    file_count = len(list(archive.raw.glob("*.json")))
    sync(archive, api, day, day, sleep=lambda _: None)
    assert len(list(archive.raw.glob("*.json"))) == file_count
    assert baseline_report(archive, day.isoformat())["current"]["baseline_eligible"]
    assert "password" not in archive.db.read_bytes().decode(errors="replace")


@pytest.mark.parametrize(
    "error,code",
    [
        (GarminConnectTooManyRequestsError("PRIVATE token in upstream error"), "rate_limit"),
        (GarminConnectConnectionError("PRIVATE upstream SSO URL"), "transport"),
    ],
)
def test_api_error_stops_no_retries_no_sensitive_error_archive(archive, error, code):
    api = FakeAPI()

    def fail(day):
        raise error

    api.get_hrv_data = fail
    with pytest.raises(LocalError, match=code):
        sync(archive, api, date(2026, 9, 10), date(2026, 9, 11), sleep=lambda _: None)
    assert len(api.calls) == 1
    assert archive.latest("2026-09-10")["hrv"]["error_code"] == code
    assert b"PRIVATE" not in archive.db.read_bytes()
    assert all("PRIVATE" not in p.read_text() for p in archive.raw.glob("*.json"))


def auth_factory(identity=123):
    def factory(**kwargs):
        assert kwargs["is_cn"] is True and kwargs["retry_attempts"] == 0

        def dump(path):
            p = Path(path) / "garmin_tokens.json"
            p.write_text(json.dumps({"synthetic_token": identity}))
            p.chmod(0o600)

        def load(path):
            assert (Path(path) / "garmin_tokens.json").exists()

        api = SimpleNamespace(
            client=SimpleNamespace(profile={"id": identity}, dump=dump, load=load),
            display_name="synthetic",
            password=kwargs["password"],
        )

        def login(path):
            dump(path)
            return None, None

        api.login = login
        return api

    return factory


def test_interactive_auth_private_and_later_resume(archive):
    api = authenticate(
        archive,
        interactive=True,
        factory=auth_factory(),
        prompt=lambda _: "synthetic-email",
        secret=lambda _: "synthetic-secret",
    )
    assert api.password is None
    assert archive.tokens.joinpath("garmin_tokens.json").stat().st_mode & 0o777 == 0o600
    assert not list(archive.root.glob(".login-*"))

    def no_prompt(_):
        raise AssertionError("No secret prompt on resume")

    authenticate(archive, factory=auth_factory(), prompt=no_prompt, secret=no_prompt)


def test_reauth_wrong_account_preserves_original_token(archive):
    authenticate(
        archive,
        interactive=True,
        factory=auth_factory(),
        prompt=lambda _: "fake",
        secret=lambda _: "fake",
    )
    original = (archive.tokens / "garmin_tokens.json").read_bytes()
    with pytest.raises(LocalError, match="different_account"):
        authenticate(
            archive,
            interactive=True,
            force=True,
            factory=auth_factory(999),
            prompt=lambda _: "other",
            secret=lambda _: "other",
        )
    assert (archive.tokens / "garmin_tokens.json").read_bytes() == original
    assert not list(archive.root.glob(".login-*"))


def test_missing_token_never_tries_credential_login(archive):
    def forbidden(**kwargs):
        raise AssertionError("No network/client construction without token")

    with pytest.raises(LocalError, match="login_required"):
        authenticate(archive, factory=forbidden)


def test_cli_non_tty_refuses_password_pipe(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv", ["garmin-readiness", "--data-dir", str(tmp_path / "private"), "onboard"]
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main() == 1
    assert "交互终端" in capsys.readouterr().err


def test_offline_status_and_labels(archive, monkeypatch, capsys):
    # Release fixture lock only through the fixture; exercise direct methods for label persistence.
    archive.bind(account_identity(FakeAPI()))
    archive.label("2026-09-10", 3, 2, True)
    assert archive.coverage()["label_days"] == 1
    assert "123" not in json.dumps(archive.coverage())
    archive.save_report(baseline_report(archive, "2026-09-10"))
    with archive.connect() as conn:
        assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
