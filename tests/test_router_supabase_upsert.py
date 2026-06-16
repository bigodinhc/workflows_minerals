"""Tests: router stages in Redis AND upserts to Supabase (best-effort)."""
from unittest.mock import MagicMock
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    return fake


@pytest.fixture
def spy_news(monkeypatch):
    """Spy on news_repo.upsert_scraped as called from router."""
    calls = []
    from execution.curation import router
    def _fake_upsert(item_id, item):
        calls.append((item_id, item))
        return True
    monkeypatch.setattr(router.news_repo, "upsert_scraped", _fake_upsert)
    return calls


def test_route_items_upserts_each_staged_item(fake_redis, spy_news):
    from execution.curation.router import route_items
    items = [{"title": "Brazil ore up", "fullText": "x", "source": "Top News"}]
    counters, staged = route_items(items, today_date="2026-06-16", today_br="16/06/2026")
    assert counters["staged"] == 1
    assert len(spy_news) == 1
    assert spy_news[0][1]["title"] == "Brazil ore up"


def test_route_items_supabase_failure_does_not_block_staging(fake_redis, monkeypatch):
    """A Supabase outage must not stop the item reaching the Redis queue."""
    from execution.curation import router
    monkeypatch.setattr(router.news_repo, "upsert_scraped",
                        MagicMock(side_effect=RuntimeError("supabase down")))
    counters, staged = router.route_items(
        [{"title": "T", "fullText": "x", "source": "Top News"}],
        today_date="2026-06-16", today_br="16/06/2026",
    )
    assert counters["staged"] == 1
    assert fake_redis.exists("platts:staging:" + staged[0]["id"])
