"""Tests for webhook.redis_queries."""
import json
import time
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_get_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _reset_client_cache(monkeypatch):
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_client", None)


def test_list_staging_empty(fake_redis):
    from webhook.redis_queries import list_staging
    assert list_staging() == []


def test_list_staging_sorted_newest_first(fake_redis):
    from webhook.redis_queries import list_staging
    fake_redis.set("platts:staging:a", json.dumps({"id": "a", "title": "A", "stagedAt": "2026-04-15T10:00:00Z"}))
    fake_redis.set("platts:staging:b", json.dumps({"id": "b", "title": "B", "stagedAt": "2026-04-15T12:00:00Z"}))
    fake_redis.set("platts:staging:c", json.dumps({"id": "c", "title": "C", "stagedAt": "2026-04-15T11:00:00Z"}))
    result = list_staging()
    assert [d["id"] for d in result] == ["b", "c", "a"]


def test_list_staging_respects_limit(fake_redis):
    from webhook.redis_queries import list_staging
    for i in range(5):
        fake_redis.set(f"platts:staging:item{i}", json.dumps({"id": f"item{i}", "stagedAt": f"2026-04-15T{i:02d}:00:00Z"}))
    result = list_staging(limit=3)
    assert len(result) == 3


def test_list_staging_skips_malformed_json(fake_redis):
    from webhook.redis_queries import list_staging
    fake_redis.set("platts:staging:good", json.dumps({"id": "good", "title": "ok"}))
    fake_redis.set("platts:staging:bad", "not-json{{{")
    result = list_staging()
    assert len(result) == 1
    assert result[0]["id"] == "good"


def test_list_staging_fills_id_from_key(fake_redis):
    """If the stored JSON lacks 'id', we derive it from the key suffix."""
    from webhook.redis_queries import list_staging
    fake_redis.set("platts:staging:abc123", json.dumps({"title": "no id field"}))
    result = list_staging()
    assert result[0]["id"] == "abc123"
