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
    # set_staging injects stagedAt, so check all original fields are preserved
    assert got["id"] == item["id"]
    assert got["title"] == item["title"]
    assert got["fullText"] == item["fullText"]
    assert "stagedAt" in got


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
    assert is_seen("abc123") is False
    mark_seen("abc123")
    assert is_seen("abc123") is True


def test_mark_seen_uses_sorted_set(fake_redis):
    from execution.curation.redis_client import mark_seen
    mark_seen("abc123")
    assert fake_redis.zscore("platts:seen", "abc123") is not None


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


def test_set_staging_injects_staged_at(fake_redis):
    """set_staging stamps stagedAt UTC ISO8601 so /queue can sort newest-first."""
    import json
    from datetime import datetime, timezone
    from execution.curation.redis_client import set_staging
    set_staging("abc123", {"id": "abc123", "title": "T"})
    raw = fake_redis.get("platts:staging:abc123")
    data = json.loads(raw)
    assert "stagedAt" in data
    parsed = datetime.fromisoformat(data["stagedAt"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert delta < 5


def test_set_staging_preserves_existing_staged_at(fake_redis):
    """If caller already set stagedAt (e.g., reprocess flow), do not overwrite."""
    import json
    from execution.curation.redis_client import set_staging
    fixed = "2026-01-01T00:00:00+00:00"
    set_staging("abc123", {"id": "abc123", "stagedAt": fixed})
    data = json.loads(fake_redis.get("platts:staging:abc123"))
    assert data["stagedAt"] == fixed


def test_seen_global_set_membership(fake_redis):
    """v2: is_seen/mark_seen use global sorted set (no date param)."""
    from execution.curation.redis_client import is_seen, mark_seen
    assert is_seen("abc123") is False
    mark_seen("abc123")
    assert is_seen("abc123") is True


def test_mark_seen_global_idempotent(fake_redis):
    """Calling mark_seen twice keeps ZCARD at 1."""
    from execution.curation.redis_client import mark_seen
    mark_seen("abc123")
    mark_seen("abc123")
    assert fake_redis.zcard("platts:seen") == 1


def test_mark_seen_global_prunes_old_entries(fake_redis):
    """Entries older than 30d are pruned on next mark_seen call."""
    import time
    from execution.curation.redis_client import mark_seen, is_seen
    old_ts = time.time() - (31 * 24 * 60 * 60)
    fake_redis.zadd("platts:seen", {"old_item": old_ts})
    mark_seen("new_item")
    assert is_seen("old_item") is False
    assert is_seen("new_item") is True


def test_staging_exists_true_after_set(fake_redis):
    from execution.curation.redis_client import set_staging, staging_exists
    assert staging_exists("abc123") is False
    set_staging("abc123", {"id": "abc123", "title": "Test"})
    assert staging_exists("abc123") is True


def test_staging_exists_false_after_discard(fake_redis):
    from execution.curation.redis_client import set_staging, discard, staging_exists
    set_staging("abc123", {"id": "abc123", "title": "Test"})
    discard("abc123")
    assert staging_exists("abc123") is False


def test_mark_scraped_populates_dated_set(fake_redis):
    from execution.curation.redis_client import mark_scraped
    mark_scraped("2026-04-16", "abc123")
    assert fake_redis.sismember("platts:scraped:2026-04-16", "abc123")


def test_mark_scraped_applies_30d_ttl(fake_redis):
    from execution.curation.redis_client import mark_scraped
    mark_scraped("2026-04-16", "abc123")
    ttl = fake_redis.ttl("platts:scraped:2026-04-16")
    assert 2591000 <= ttl <= 2592000


def test_mark_scraped_idempotent(fake_redis):
    from execution.curation.redis_client import mark_scraped
    mark_scraped("2026-04-16", "abc123")
    mark_scraped("2026-04-16", "abc123")
    assert fake_redis.scard("platts:scraped:2026-04-16") == 1
