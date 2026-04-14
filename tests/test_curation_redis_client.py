"""Tests for execution.curation.redis_client."""
import json
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    """Inject fakeredis as the module-level Redis client."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _reset_client_cache(monkeypatch):
    """Ensure no cached client leaks between tests."""
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_client", None)


def test_set_and_get_staging_roundtrip(fake_redis):
    from execution.curation.redis_client import set_staging, get_staging
    item = {"id": "abc123", "title": "Test", "fullText": "body"}
    set_staging("abc123", item)
    got = get_staging("abc123")
    assert got == item


def test_set_staging_applies_48h_ttl(fake_redis):
    from execution.curation.redis_client import set_staging
    set_staging("abc123", {"id": "abc123"})
    ttl = fake_redis.ttl("platts:staging:abc123")
    # 48h = 172800s; allow some slack
    assert 172700 <= ttl <= 172800


def test_get_staging_returns_none_for_missing(fake_redis):
    from execution.curation.redis_client import get_staging
    assert get_staging("missing") is None


def test_archive_moves_from_staging_to_archive(fake_redis):
    from execution.curation.redis_client import set_staging, archive
    item = {"id": "abc123", "title": "Test"}
    set_staging("abc123", item)
    archived = archive("abc123", "2026-04-14", chat_id=12345)
    # Staging gone, archive present
    assert fake_redis.get("platts:staging:abc123") is None
    raw = fake_redis.get("platts:archive:2026-04-14:abc123")
    data = json.loads(raw)
    assert data["title"] == "Test"
    assert data["archivedBy"] == 12345
    assert "archivedAt" in data
    assert archived == data


def test_archive_returns_none_if_staging_missing(fake_redis):
    from execution.curation.redis_client import archive
    result = archive("missing", "2026-04-14", chat_id=1)
    assert result is None


def test_discard_deletes_staging(fake_redis):
    from execution.curation.redis_client import set_staging, discard
    set_staging("abc123", {"id": "abc123"})
    discard("abc123")
    assert fake_redis.get("platts:staging:abc123") is None


def test_seen_set_membership(fake_redis):
    from execution.curation.redis_client import is_seen, mark_seen
    assert is_seen("2026-04-14", "abc123") is False
    mark_seen("2026-04-14", "abc123")
    assert is_seen("2026-04-14", "abc123") is True


def test_mark_seen_applies_30d_ttl(fake_redis):
    from execution.curation.redis_client import mark_seen
    mark_seen("2026-04-14", "abc123")
    ttl = fake_redis.ttl("platts:seen:2026-04-14")
    # 30d = 2592000s
    assert 2591000 <= ttl <= 2592000


def test_get_archive_roundtrip(fake_redis):
    from execution.curation.redis_client import set_staging, archive, get_archive
    set_staging("abc", {"id": "abc", "title": "Hello"})
    archive("abc", "2026-04-14", chat_id=1)
    got = get_archive("2026-04-14", "abc")
    assert got["title"] == "Hello"
    assert got["archivedBy"] == 1


def test_get_archive_missing_returns_none(fake_redis):
    from execution.curation.redis_client import get_archive
    assert get_archive("2026-04-14", "missing") is None


def test_rationale_flag_set_once_per_day(fake_redis):
    from execution.curation.redis_client import (
        is_rationale_processed,
        set_rationale_processed,
    )
    assert is_rationale_processed("2026-04-14") is False
    assert set_rationale_processed("2026-04-14") is True  # first time — NX wins
    assert is_rationale_processed("2026-04-14") is True
    assert set_rationale_processed("2026-04-14") is False  # second time — NX loses
