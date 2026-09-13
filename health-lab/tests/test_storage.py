import json
import os

import pytest

from garmin_readiness.storage import MAX_RAW, Archive, LocalError


def test_private_immutable_archive_and_history(archive):
    for stamp in ("2026-09-10T00:00:00+00:00", "2026-09-11T00:00:00+00:00"):
        archive.record("2026-09-09", "hrv", {"value": 60}, fetched_at=stamp)
    assert len(list(archive.raw.glob("*.json"))) == 1
    assert archive.latest("2026-09-09")["hrv"]["payload"] == {"value": 60}
    for p in (archive.root, archive.raw, archive.tokens):
        assert p.stat().st_mode & 0o777 == 0o700
    for p in [archive.db, *archive.raw.glob("*.json")]:
        assert p.stat().st_mode & 0o777 == 0o600
    assert archive.latest("2026-09-09", "2026-09-09T23:59:59+00:00") == {}
    assert archive.latest("2026-09-09", "2026-09-10T08:00:01+08:00")["hrv"][
        "fetched_at"
    ].startswith("2026-09-10")


def test_later_error_does_not_silently_reuse_old_good_data(archive):
    archive.record("2026-09-01", "sleep", {"sample": True}, fetched_at="2026-09-01T00:00:00Z")
    archive.record(
        "2026-09-01",
        "sleep",
        None,
        status="error",
        error_code="transport",
        fetched_at="2026-09-02T00:00:00Z",
    )
    assert archive.latest("2026-09-01")["sleep"]["payload"] is None
    assert archive.latest("2026-09-01", "2026-09-01T08:00:00Z")["sleep"]["payload"] == {
        "sample": True
    }


def test_cannot_mix_accounts(archive):
    archive.bind("a" * 64)
    archive.bind("a" * 64)
    with pytest.raises(LocalError, match="different_account"):
        archive.bind("b" * 64)


def test_cannot_store_health_in_git(tmp_path):
    (tmp_path / ".git").mkdir()
    with pytest.raises(LocalError, match="inside_git"):
        Archive(tmp_path / "data")


def test_refuses_symlink_and_public_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(LocalError, match="symlink"):
        Archive(link / "child")
    real.chmod(0o755)
    with pytest.raises(LocalError, match="permissions"):
        Archive(real)


def test_refuses_symlink_db(tmp_path):
    a = Archive(tmp_path / "private")
    target = tmp_path / "unrelated"
    target.write_text("unchanged")
    a.db.symlink_to(target)
    with pytest.raises(LocalError, match="symlink"):
        with a.lock():
            pass
    assert target.read_text() == "unchanged"


def test_raw_integrity_and_bound(archive):
    archive.record("2026-09-01", "hrv", {"sample": True})
    path = next(archive.raw.glob("*.json"))
    path.write_text(json.dumps({"sample": False}))
    with pytest.raises(LocalError, match="corrupt"):
        archive.latest("2026-09-01")
    with pytest.raises(LocalError, match="bound"):
        archive.record("2026-09-01", "sleep", {"too_big": "x" * MAX_RAW})


def test_one_writer_lock(archive):
    other = Archive(archive.root)
    with pytest.raises(LocalError, match="another_command"):
        with other.lock():
            pass


def test_failed_raw_write_does_not_publish_partial_object(archive, monkeypatch):
    def fail(_):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr("garmin_readiness.storage.os.fsync", fail)
    with pytest.raises(OSError):
        archive.record("2026-09-01", "hrv", {"sample": True})
    assert list(archive.raw.iterdir()) == []
    assert archive.latest("2026-09-01") == {}


def test_schema_newer_refused_without_ddl(archive):
    with archive.connect() as conn:
        conn.execute("UPDATE meta SET value='99' WHERE key='schema'")
        conn.execute("DROP TABLE reports")
    with pytest.raises(LocalError, match="schema_version"):
        archive.initialize()
    with archive.connect() as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='reports'").fetchall()


def test_hardlink_refused(archive, tmp_path):
    os.link(archive.db, tmp_path / "alias")
    with pytest.raises(LocalError, match="permissions"):
        archive.coverage()
