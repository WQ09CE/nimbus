from types import SimpleNamespace

import pytest

from nimbus_chat_lab.research import Research, window_filters


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-09-11T23:55:04Z", "2026-09-12T23:55:04Z", ("2026-09-11", "2026-09-13")),
        ("2026-09-12T07:55:04+08:00", "2026-09-13T07:55:04+08:00", ("2026-09-11", "2026-09-13")),
        ("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z", ("2026-09-11", "2026-09-12")),
        ("2026-12-31T12:00:00Z", "2027-01-01T00:00:00.000001Z", ("2026-12-31", "2027-01-02")),
        ("2024-02-28T23:30:00-02:00", "2024-02-29T23:00:00-02:00", ("2024-02-29", "2024-03-02")),
    ],
)
def test_covering_utc_dates(start, end, expected):
    filters, exact = window_filters({"start": start, "end": end}, {"allowed_x_handles": ["abc"]})
    assert (filters["from_date"], filters["to_date"]) == expected
    assert filters["allowed_x_handles"] == ["abc"]
    assert exact["start"].endswith("+00:00")


@pytest.mark.parametrize(
    "window",
    [
        {},
        {"start": "2026-09-12", "end": "2026-09-13"},
        {"start": "2026-09-12T00:00:00", "end": "2026-09-13T00:00:00Z"},
        {"start": "2026-09-13T00:00:00Z", "end": "2026-09-12T00:00:00Z"},
        {"start": "2026-09-12T00:00:00Z", "end": "2026-09-12T00:00:00Z"},
        {"start": "2026-01-01T00:00:00Z", "end": "2026-09-12T00:00:00Z"},
        {"start": None, "end": "2026-09-12T00:00:00Z"},
        {"start": "2026-09-11T00:00:00Z", "end": "2026-09-12T00:00:00Z", "extra": 1},
    ],
)
def test_bad_windows(window):
    with pytest.raises(ValueError):
        window_filters(window)


def test_legacy_pass_through_and_conflict():
    old = {"from_date": "2026-09-11", "to_date": "2026-09-12"}
    assert window_filters(None, old) == (old, None)
    with pytest.raises(ValueError):
        window_filters({"start": "2026-09-11T23:55:04Z", "end": "2026-09-12T23:55:04Z"}, old)


async def test_real_dispatch_uses_computed_envelope_and_records_it():
    calls, events = [], []

    async def search(query, source, **options):
        calls.append((query, source, options))
        return {"text": "bounded provider evidence", "source_count": 1}

    async def record(*args):
        events.append(args)

    r = Research(
        SimpleNamespace(search=search),
        SimpleNamespace(data_scope="public", record_search_event=record, record_research=record),
    )
    result = await r.run(
        "Public news",
        "x",
        "discover",
        window={"start": "2026-09-11T23:55:04Z", "end": "2026-09-12T23:55:04Z"},
    )
    assert calls[0][2]["x_filters"] == {"from_date": "2026-09-11", "to_date": "2026-09-13"}
    assert "until:2026-09-13" in calls[0][0]
    assert result["search_window"]["exact_timestamp_filter_verified"] is False
    assert events[0][0]["x_window"]["end"] == "2026-09-12T23:55:04+00:00"
    assert r.used == 1 and r.active == 0


async def test_invalid_window_never_spends_budget_or_dispatches():
    async def nope(*args, **kwargs):
        pytest.fail("must not dispatch or record external research")

    r = Research(SimpleNamespace(search=nope), SimpleNamespace(data_scope="public"))
    window = {"start": "2026-09-11T23:55:04Z", "end": "2026-09-12T23:55:04Z"}
    for source, query, filters in [
        ("web", "q", None),
        ("x", "q", {"to_date": "2026-09-12"}),
        ("x", "x" * 12000, None),
    ]:
        result = await r.run(query, source, filters=filters, window=window)
        assert result["output"]["error"] == "invalid_search_window"
        assert result["output"]["dispatched"] is False
    assert r.used == 0
    r.state.data_scope = "health"
    assert (await r.run("q", "x", window=window))["output"]["error"] == "private_scope_no_search"
