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


def test_set_status_archived_sets_fields(fake_sb):
    from execution.curation.news_repo import set_status
    upd = fake_sb.table.return_value.update.return_value
    upd.eq.return_value.execute.return_value = MagicMock(data=[{"id": "abc"}])
    ok = set_status("abc", "archived", chat_id=999)
    payload = fake_sb.table.return_value.update.call_args[0][0]
    assert payload["status"] == "archived"
    assert payload["archived_by"] == 999
    assert "archived_at" in payload
    assert ok is True


def test_set_status_rejected_sets_reason(fake_sb):
    from execution.curation.news_repo import set_status
    upd = fake_sb.table.return_value.update.return_value
    upd.eq.return_value.execute.return_value = MagicMock(data=[{"id": "abc"}])
    set_status("abc", "rejected", reason="fora de escopo")
    payload = fake_sb.table.return_value.update.call_args[0][0]
    assert payload["status"] == "rejected"
    assert payload["reject_reason"] == "fora de escopo"
    assert "rejected_at" in payload


def test_set_status_returns_false_when_no_row(fake_sb):
    from execution.curation.news_repo import set_status
    upd = fake_sb.table.return_value.update.return_value
    upd.eq.return_value.execute.return_value = MagicMock(data=[])
    assert set_status("missing", "archived", chat_id=1) is False


def test_set_status_bulk_uses_in_filter(fake_sb):
    from execution.curation.news_repo import set_status_bulk
    upd = fake_sb.table.return_value.update.return_value
    upd.in_.return_value.execute.return_value = MagicMock(data=[{"id": "a"}, {"id": "b"}])
    n = set_status_bulk(["a", "b"], "archived", chat_id=1)
    assert fake_sb.table.return_value.update.return_value.in_.call_args[0][0] == "id"
    assert n == 2


def test_get_by_id_returns_row(fake_sb):
    from execution.curation.news_repo import get_by_id
    chain = fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=[{"id": "abc", "title": "T"}])
    got = get_by_id("abc")
    assert got["title"] == "T"


def test_get_by_id_returns_none_when_empty(fake_sb):
    from execution.curation.news_repo import get_by_id
    chain = fake_sb.table.return_value.select.return_value.eq.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=[])
    assert get_by_id("missing") is None


def test_list_by_status_orders_and_limits(fake_sb):
    from execution.curation.news_repo import list_by_status
    chain = fake_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=[{"id": "a"}])
    rows = list_by_status("archived", limit=10)
    assert rows == [{"id": "a"}]


def test_search_uses_text_search(fake_sb):
    from execution.curation.news_repo import search
    chain = fake_sb.table.return_value.select.return_value.text_search.return_value.limit.return_value
    chain.execute.return_value = MagicMock(data=[{"id": "a", "title": "iron ore"}])
    rows = search("iron ore", limit=5)
    args = fake_sb.table.return_value.select.return_value.text_search.call_args
    assert args[0][0] == "fts"
    assert args[0][1] == "iron ore"
    assert rows[0]["title"] == "iron ore"


def test_item_to_row_does_not_mutate_or_alias_input():
    """House rule: _item_to_row must not mutate the caller's dict nor alias it into raw."""
    from execution.curation.news_repo import _item_to_row
    item = {"title": "T", "fullText": "body", "extra": [1, 2]}
    snapshot = dict(item)
    row = _item_to_row("abc", item, status="staged")
    assert item == snapshot            # input untouched
    assert row["raw"] is not item      # raw is a decoupled copy
    assert row["raw"] == item          # but equal in content
