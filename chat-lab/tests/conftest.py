from uuid import uuid4

import pgserver
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from nimbus_chat_lab.store import Store


@pytest.fixture(scope="session")
def postgres(tmp_path_factory):
    # Bundled PostgreSQL 16.2 is a TEST dependency only. Unix socket, private temp
    # directory, no TCP listener, no existing database/container/service touched.
    server = pgserver.get_server(tmp_path_factory.mktemp("chat-pg"), cleanup_mode="stop")
    yield server
    server.cleanup()


@pytest.fixture
async def store(postgres):
    name = "test_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(postgres.get_uri(), autocommit=True) as c:
        await c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    store = Store(make_conninfo(postgres.get_uri(), dbname=name))
    await store.initialize()
    await store.authorize(100, 1, 1)
    yield store
    async with await psycopg.AsyncConnection.connect(postgres.get_uri(), autocommit=True) as c:
        await c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def update(n=1, text="hello", user=1, chat=1, **extra):
    return {
        "update_id": n,
        "message": {
            "message_id": n,
            "from": {"id": user, "is_bot": False},
            "chat": {"id": chat, "type": "private" if chat > 0 else "supergroup"},
            "text": text,
            **extra,
        },
    }


async def rows(store, query, params=None):
    async with await store.connect() as c:
        return await (await c.execute(query, params)).fetchall()
