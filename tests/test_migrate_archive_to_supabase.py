"""Tests for the one-shot archive→Supabase backfill."""
from unittest.mock import MagicMock
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


def test_backfill_inserts_each_archive_item(fake_redis, monkeypatch):
    from execution.scripts import migrate_archive_to_supabase as mig
    fake_redis.set("platts:archive:2026-06-15:a",
                   json.dumps({"id": "a", "title": "A", "fullText": "x",
                               "archivedAt": "2026-06-15T10:00:00+00:00", "archivedBy": 5}))
    fake_redis.set("platts:archive:2026-06-15:b",
                   json.dumps({"id": "b", "title": "B"}))
    rows = []
    fake_client = MagicMock()
    fake_client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "ok"}])
    def _capture(row, **kw):
        rows.append(row)
        return fake_client.table.return_value.upsert.return_value
    fake_client.table.return_value.upsert.side_effect = _capture
    monkeypatch.setattr(mig, "get_news_client", lambda: fake_client)

    count = mig.backfill()
    assert count == 2
    ids = {r["id"] for r in rows}
    assert ids == {"a", "b"}
    a_row = next(r for r in rows if r["id"] == "a")
    assert a_row["status"] == "archived"
    assert a_row["archived_at"] == "2026-06-15T10:00:00+00:00"
    assert a_row["archived_by"] == 5
