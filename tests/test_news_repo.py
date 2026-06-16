"""Tests for execution.curation.news_repo (mock do client supabase)."""
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def fake_sb(monkeypatch):
    """Inject a MagicMock supabase client and reset the cached one."""
    client = MagicMock(name="supabase_client")
    from execution.curation import news_repo
    monkeypatch.setattr(news_repo, "get_news_client", lambda: client)
    return client


def _last_table(client):
    """Return the table name passed to the most recent .table(...) call."""
    return client.table.call_args[0][0]


def test_item_to_row_maps_fields():
    from execution.curation.news_repo import _item_to_row
    item = {
        "title": "Brazil ore climbs", "href": "http://x", "source": "Top News",
        "author": "Reuters", "publishDate": "06/15/2026", "fullText": "body",
        "paragraphs": ["a", "b"], "tables": [{"h": 1}], "metadata": {"w": 2},
        "type": "news", "stagedAt": "2026-06-15T10:00:00+00:00",
    }
    row = _item_to_row("abc123", item, status="staged")
    assert row["id"] == "abc123"
    assert row["status"] == "staged"
    assert row["title"] == "Brazil ore climbs"
    assert row["full_text"] == "body"
    assert row["publish_date"] == "06/15/2026"
    assert row["paragraphs"] == ["a", "b"]
    assert row["raw"] == item
    assert row["scraped_at"] == "2026-06-15T10:00:00+00:00"


def test_item_to_row_omits_none_scraped_at():
    """No stagedAt → scraped_at absent so the DB default now() applies."""
    from execution.curation.news_repo import _item_to_row
    row = _item_to_row("abc", {"title": "T"}, status="staged")
    assert "scraped_at" not in row


def test_upsert_scraped_calls_upsert_with_on_conflict(fake_sb):
    from execution.curation.news_repo import upsert_scraped
    fake_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[{"id": "abc"}])
    inserted = upsert_scraped("abc", {"title": "T", "fullText": "x"})
    assert _last_table(fake_sb) == "platts_news"
    kwargs = fake_sb.table.return_value.upsert.call_args.kwargs
    assert kwargs.get("on_conflict") == "id"
    assert kwargs.get("ignore_duplicates") is True
    assert inserted is True


def test_upsert_scraped_returns_false_on_conflict(fake_sb):
    from execution.curation.news_repo import upsert_scraped
    fake_sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
    assert upsert_scraped("dup", {"title": "T"}) is False
