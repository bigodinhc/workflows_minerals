"""Tests for execution.curation.router (v1.1: stager puro)."""
import json
import pytest
import fakeredis


@pytest.fixture(autouse=True)
def _redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    yield fake


def test_classify_returns_rationale_for_rmw_rationale_tab():
    from execution.curation.router import classify
    item = {"source": "rmw", "tabName": "Rationale"}
    assert classify(item) == "rationale"


def test_classify_returns_rationale_for_rmw_lump_tab():
    from execution.curation.router import classify
    item = {"source": "rmw_market", "tabName": "Lump Premium"}
    assert classify(item) == "rationale"


def test_classify_returns_curation_default():
    from execution.curation.router import classify
    item = {"source": "platts", "tabName": "Iron Ore News"}
    assert classify(item) == "curation"


def test_route_items_stages_all_with_type_field(_redis):
    """Every item (curation OR rationale) lands in staging with a `type`."""
    from execution.curation.router import route_items
    items = [
        {"source": "platts", "title": "Iron Ore News 1", "tabName": "News"},
        {"source": "rmw", "title": "Daily Rationale", "tabName": "Rationale"},
    ]
    counters, staged = route_items(
        items=items, today_date="2026-04-15", today_br="15/04/2026",
        logger=None,
    )
    assert counters["total"] == 2
    assert counters["staged"] == 2
    assert counters["rationale_staged"] == 1
    assert counters["news_staged"] == 1
    assert counters["skipped_seen"] == 0
    assert len(staged) == 2
    types = {s["type"] for s in staged}
    assert types == {"news", "rationale"}
    # Cada item tem id preenchido
    assert all(s.get("id") for s in staged)


def test_route_items_respects_is_seen_dedup(_redis):
    from execution.curation.router import route_items
    from execution.curation import redis_client
    from execution.curation.id_gen import generate_id
    item = {"source": "platts", "title": "Duplicated", "tabName": "News"}
    item_id = generate_id("Duplicated")
    redis_client.mark_seen(item_id)
    counters, staged = route_items(
        items=[item], today_date="2026-04-15", today_br="15/04/2026",
        logger=None,
    )
    assert counters["skipped_seen"] == 1
    assert counters["staged"] == 0
    assert staged == []


def test_route_items_does_not_call_telegram(_redis, monkeypatch):
    """Router must NOT post to Telegram — posting is caller's job now."""
    from execution.curation.router import route_items
    from execution.curation import telegram_poster
    def fail_if_called(*args, **kwargs):
        raise AssertionError("router should not call post_for_curation")
    monkeypatch.setattr(telegram_poster, "post_for_curation", fail_if_called)
    route_items(
        items=[{"source": "platts", "title": "X", "tabName": "News"}],
        today_date="2026-04-15", today_br="15/04/2026", logger=None,
    )


def test_route_items_dedup_same_title_different_source(_redis):
    """H2 regression: same title in 'Latest' and 'Top News' must stage once."""
    from execution.curation.router import route_items
    items = [
        {"source": "Latest", "title": "EU reaches deal on steel", "tabName": ""},
        {"source": "Top News - Ferrous Metals", "title": "EU reaches deal on steel", "tabName": ""},
    ]
    counters, staged = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters["staged"] == 1
    # Second item with same title is skipped because the first was just staged
    assert counters["skipped_staged"] == 1
    assert len(staged) == 1


def test_route_items_dedup_across_days(_redis):
    """H1 regression: item seen yesterday must not re-stage today."""
    from execution.curation.router import route_items
    items = [{"source": "platts", "title": "Steel demand forecast", "tabName": "News"}]
    counters1, staged1 = route_items(
        items=items, today_date="2026-04-15", today_br="15/04/2026", logger=None,
    )
    assert counters1["staged"] == 1
    counters2, staged2 = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters2["staged"] == 0
    # Item is still in staging (not yet archived), so skipped_staged fires
    assert counters2["skipped_staged"] == 1


def test_route_items_staging_short_circuit(_redis):
    """Item already in staging (not yet archived) should be skipped."""
    from execution.curation.router import route_items
    from execution.curation import redis_client
    from execution.curation.id_gen import generate_id
    title = "Test"
    item_id = generate_id(title)
    redis_client.set_staging(item_id, {"id": item_id, "title": title})
    items = [{"source": "platts", "title": title, "tabName": "News"}]
    counters, staged = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters["staged"] == 0
    assert counters["skipped_staged"] == 1


def test_route_items_counters_balance(_redis):
    """total == staged + skipped_seen + skipped_staged + skipped_invalid."""
    from execution.curation.router import route_items
    from execution.curation import redis_client
    from execution.curation.id_gen import generate_id
    items = [
        {"source": "platts", "title": "New article", "tabName": "News"},
        {"source": "platts", "title": "New article", "tabName": "News"},
        {"source": "platts", "title": "Staged one", "tabName": "News"},
    ]
    staged_id = generate_id("Staged one")
    redis_client.set_staging(staged_id, {"id": staged_id, "title": "Staged one"})
    counters, _ = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters["total"] == counters["staged"] + counters["skipped_seen"] + counters["skipped_staged"] + counters["skipped_invalid"]


def test_route_items_skips_empty_title(_redis):
    """Item with empty title should be skipped (generate_id raises ValueError)."""
    from execution.curation.router import route_items
    items = [
        {"source": "platts", "title": "", "tabName": "News"},
        {"source": "platts", "title": "Valid article", "tabName": "News"},
    ]
    counters, staged = route_items(
        items=items, today_date="2026-04-16", today_br="16/04/2026", logger=None,
    )
    assert counters["staged"] == 1
    assert len(staged) == 1
