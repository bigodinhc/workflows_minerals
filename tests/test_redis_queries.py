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


def test_list_archive_recent_empty(fake_redis):
    from webhook.redis_queries import list_archive_recent
    assert list_archive_recent() == []


def test_list_archive_recent_crossdate_sorted(fake_redis):
    from webhook.redis_queries import list_archive_recent
    fake_redis.set("platts:archive:2026-04-13:x", json.dumps({"id": "x", "title": "X", "archivedAt": "2026-04-13T09:00:00+00:00"}))
    fake_redis.set("platts:archive:2026-04-15:y", json.dumps({"id": "y", "title": "Y", "archivedAt": "2026-04-15T14:00:00+00:00"}))
    fake_redis.set("platts:archive:2026-04-14:z", json.dumps({"id": "z", "title": "Z", "archivedAt": "2026-04-14T11:00:00+00:00"}))
    result = list_archive_recent(limit=10)
    assert [d["id"] for d in result] == ["y", "z", "x"]


def test_list_archive_recent_respects_limit(fake_redis):
    from webhook.redis_queries import list_archive_recent
    for i in range(15):
        ts = f"2026-04-15T{i:02d}:00:00+00:00"
        fake_redis.set(f"platts:archive:2026-04-15:i{i}", json.dumps({"id": f"i{i}", "archivedAt": ts}))
    result = list_archive_recent(limit=10)
    assert len(result) == 10


def test_list_archive_recent_derives_date_from_key(fake_redis):
    """Each dict should have archived_date extracted from key middle segment."""
    from webhook.redis_queries import list_archive_recent
    fake_redis.set("platts:archive:2026-04-15:abc", json.dumps({"id": "abc", "archivedAt": "2026-04-15T10:00:00+00:00"}))
    result = list_archive_recent()
    assert result[0]["archived_date"] == "2026-04-15"


def test_save_feedback_creates_hash_and_index(fake_redis):
    from webhook.redis_queries import save_feedback
    key = save_feedback("curate_reject", "abc123", 999, "", "Sample title")
    assert key.endswith("-abc123")
    data = fake_redis.hgetall(f"webhook:feedback:{key}")
    assert data["action"] == "curate_reject"
    assert data["item_id"] == "abc123"
    assert data["chat_id"] == "999"
    assert data["reason"] == ""
    assert data["title"] == "Sample title"
    assert float(data["timestamp"]) > 0
    assert fake_redis.zscore("webhook:feedback:index", key) is not None


def test_save_feedback_empty_reason_allowed(fake_redis):
    from webhook.redis_queries import save_feedback
    key = save_feedback("draft_reject", "draft42", 999, "", "Draft title")
    data = fake_redis.hgetall(f"webhook:feedback:{key}")
    assert data["reason"] == ""


def test_save_feedback_applies_30d_ttl(fake_redis):
    from webhook.redis_queries import save_feedback
    key = save_feedback("curate_reject", "x", 1, "", "T")
    ttl = fake_redis.ttl(f"webhook:feedback:{key}")
    assert 30 * 24 * 3600 - 10 <= ttl <= 30 * 24 * 3600


def test_update_feedback_reason_updates_hash(fake_redis):
    from webhook.redis_queries import save_feedback, update_feedback_reason
    key = save_feedback("curate_reject", "xyz", 1, "", "T")
    updated = update_feedback_reason(key, "duplicate of item foo")
    assert updated is True
    data = fake_redis.hgetall(f"webhook:feedback:{key}")
    assert data["reason"] == "duplicate of item foo"


def test_update_feedback_reason_nonexistent_returns_false(fake_redis):
    from webhook.redis_queries import update_feedback_reason
    assert update_feedback_reason("1234567890-doesnotexist", "whatever") is False


def test_list_feedback_most_recent_first(fake_redis):
    from webhook.redis_queries import save_feedback, list_feedback
    key_a = save_feedback("curate_reject", "a", 1, "reason a", "Title A")
    time.sleep(0.01)
    key_b = save_feedback("curate_reject", "b", 1, "reason b", "Title B")
    time.sleep(0.01)
    key_c = save_feedback("draft_reject", "c", 1, "reason c", "Title C")
    results = list_feedback(limit=10)
    assert [r["item_id"] for r in results] == ["c", "b", "a"]


def test_list_feedback_filter_by_action(fake_redis):
    from webhook.redis_queries import save_feedback, list_feedback
    save_feedback("curate_reject", "a", 1, "", "A")
    save_feedback("draft_reject", "b", 1, "", "B")
    save_feedback("curate_reject", "c", 1, "", "C")
    results = list_feedback(limit=10, action="curate_reject")
    assert len(results) == 2
    assert all(r["action"] == "curate_reject" for r in results)


def test_list_feedback_filter_since_ts(fake_redis):
    from webhook.redis_queries import save_feedback, list_feedback
    save_feedback("curate_reject", "old", 1, "", "Old")
    time.sleep(0.05)
    cutoff = time.time()
    time.sleep(0.05)
    save_feedback("curate_reject", "new", 1, "", "New")
    results = list_feedback(limit=10, since_ts=cutoff)
    assert [r["item_id"] for r in results] == ["new"]
