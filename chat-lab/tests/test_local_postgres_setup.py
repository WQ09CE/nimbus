import importlib.util
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from nimbus_chat_lab.store import Store


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts/configure_local_postgres.py"
    spec = importlib.util.spec_from_file_location("configure_pg_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_write_never_overwrites_or_follows_symlinks(tmp_path):
    module = load_script()
    original = tmp_path / "original"
    original.write_text("preserve")
    link = tmp_path / "link"
    link.symlink_to(original)
    for path in [original, link]:
        with pytest.raises(FileExistsError):
            module.private_write(path, "changed")
    assert original.read_text() == "preserve"
    fresh = tmp_path / "fresh"
    module.private_write(fresh, "private")
    assert fresh.stat().st_mode & 0o777 == 0o600


def test_prepare_refuses_existing_cluster_before_running_anything(tmp_path, monkeypatch):
    module = load_script()
    monkeypatch.setattr(module, "DATA", tmp_path)
    with pytest.raises(RuntimeError, match="refusing overwrite"):
        module.prepare()


async def test_bootstrap_grants_only_data_access_and_preserves_operator_settings(
    postgres, tmp_path, monkeypatch
):
    # Uses only conftest's isolated PostgreSQL, never the deployed user service.
    module = load_script()
    conf = tmp_path / "config"
    conf.mkdir(mode=0o700)
    original = "NIMBUS_LAB_DSN=\nNIMBUS_BOT_ID=100\n"
    for name in ["gateway.env", "worker.env"]:
        module.private_write(conf / name, original)
    connection = conninfo_to_dict(postgres.get_uri())
    monkeypatch.setattr(module, "CONFIG", conf)
    monkeypatch.setattr(module, "SOCKET", Path(connection["host"]))
    monkeypatch.setattr(module, "PORT", int(connection.get("port", 5432)))
    monkeypatch.setattr(module, "ADMIN", connection.get("user", "postgres"))
    with psycopg.connect(postgres.get_uri()) as c:
        assert (
            c.execute(
                "SELECT 1 FROM pg_roles WHERE rolname IN ('nimbus_chat','nimbus_chat_owner')"
            ).fetchone()
            is None
        )
    try:
        module.bootstrap()
        saved = (conf / "gateway.env").read_text()
        dsn = saved.splitlines()[0].split("=", 1)[1]
        assert "NIMBUS_BOT_ID=100" in saved
        assert (conf / "worker.env").read_text() == saved
        assert list((conf / "backups").glob("*/gateway.env"))[0].read_text() == original
        with psycopg.connect(dsn) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute("CREATE TABLE forbidden_ddl (id int)")
        store = Store(dsn)
        await store.authorize(100, 1, 1)
        assert await store.offset(100) == 0
        with pytest.raises(RuntimeError, match="refusing rotation"):
            module.bootstrap()
    finally:
        with psycopg.connect(postgres.get_uri(), autocommit=True) as c:
            c.execute("DROP DATABASE IF EXISTS nimbus_chat WITH (FORCE)")
            c.execute("DROP ROLE IF EXISTS nimbus_chat")
            c.execute("DROP ROLE IF EXISTS nimbus_chat_owner")
