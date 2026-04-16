"""Tests for execution.scripts.rebuild_dedup migration."""
import json
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    return fake


def test_dry_run_does_not_mutate(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:old_id", json.dumps({
        "title": "Test article",
        "archivedAt": "2026-04-14T10:00:00+00:00",
    }))
    fake_redis.sadd("platts:seen:2026-04-14", "old_id")
    result = rebuild(fake_redis, dry_run=True)
    assert result["unique_ids"] >= 1
    assert fake_redis.exists("platts:seen:2026-04-14") == 1
    assert fake_redis.zcard("platts:seen") == 0


def test_execute_populates_global_seen(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:old_id", json.dumps({
        "title": "Test article",
        "archivedAt": "2026-04-14T10:00:00+00:00",
    }))
    result = rebuild(fake_redis, dry_run=False)
    assert result["unique_ids"] >= 1
    assert fake_redis.zcard("platts:seen") == result["unique_ids"]


def test_execute_deletes_dated_seen_keys(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.sadd("platts:seen:2026-04-14", "a", "b")
    fake_redis.sadd("platts:seen:2026-04-15", "c")
    fake_redis.set("platts:archive:2026-04-14:a", json.dumps({"title": "A"}))
    rebuild(fake_redis, dry_run=False)
    assert fake_redis.exists("platts:seen:2026-04-14") == 0
    assert fake_redis.exists("platts:seen:2026-04-15") == 0


def test_execute_idempotent(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:old_id", json.dumps({
        "title": "Test article",
    }))
    result1 = rebuild(fake_redis, dry_run=False)
    result2 = rebuild(fake_redis, dry_run=False)
    assert result1["unique_ids"] == result2["unique_ids"]
    assert fake_redis.zcard("platts:seen") == result1["unique_ids"]


def test_execute_deduplicates_same_title_different_ids(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:id_a", json.dumps({
        "title": "EU steel deal",
    }))
    fake_redis.set("platts:archive:2026-04-15:id_b", json.dumps({
        "title": "EU steel deal",
    }))
    result = rebuild(fake_redis, dry_run=False)
    assert result["archive_count"] == 2
    assert result["unique_ids"] == 1
    assert fake_redis.zcard("platts:seen") == 1


def test_execute_skips_missing_title(fake_redis):
    from execution.scripts.rebuild_dedup import rebuild
    fake_redis.set("platts:archive:2026-04-14:no_title", json.dumps({
        "something": "else",
    }))
    fake_redis.set("platts:archive:2026-04-14:ok", json.dumps({
        "title": "Valid",
    }))
    result = rebuild(fake_redis, dry_run=False)
    assert result["skipped"] == 1
    assert result["unique_ids"] == 1
