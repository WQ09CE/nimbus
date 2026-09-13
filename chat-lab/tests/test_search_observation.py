import json

from test_agent_mode import actor


async def test_research_receipt_keeps_only_safe_observed_modes(store):
    state, claim = await actor(store)
    await state.record_research(
        "public query",
        "x",
        {
            "text": "evidence",
            "sources": ["https://example.com"],
            "search_observation": {
                "trace_status": "observed",
                "keyword_modes": ["Latest", "Top", "SECRET"],
                "calls": [{"query": "SECRET"}],
                "ranking_by_views_verified": True,
                "engagement_filter_verified": True,
            },
        },
    )
    async with await store.connect() as c:
        row = await (
            await c.execute(
                "SELECT text FROM turn_events WHERE turn_id=%s AND kind='research'",
                (claim.turn_id,),
            )
        ).fetchone()
    data = json.loads(row["text"])
    assert "SECRET" not in row["text"]
    assert data["search_observation"]["keyword_modes"] == ["Top", "Latest"]
    assert data["search_observation"]["ranking_by_views_verified"] is False
    assert data["search_observation"]["engagement_filter_verified"] is False


async def test_missing_observation_does_not_invent_mode(store):
    state, claim = await actor(store)
    await state.record_research("public query", "x", {"text": "evidence"})
    async with await store.connect() as c:
        row = await (
            await c.execute(
                "SELECT text FROM turn_events WHERE turn_id=%s AND kind='research'",
                (claim.turn_id,),
            )
        ).fetchone()
    assert "search_observation" not in json.loads(row["text"])
