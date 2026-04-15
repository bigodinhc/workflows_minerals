"""Tests for webhook.query_handlers formatters."""
import json
import time
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from webhook import redis_queries
    monkeypatch.setattr(redis_queries, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_queries, "_client", None)
    return fake


def test_help_text_lists_all_commands():
    from webhook.query_handlers import format_help
    text = format_help()
    assert "/queue" in text
    assert "/history" in text
    assert "/rejections" in text
    assert "/stats" in text
    assert "/status" in text
    assert "/reprocess" in text
    assert "/add" in text
    assert "/list" in text
    assert "/cancel" in text
    assert text.startswith("*COMANDOS*")


def test_history_empty(fake_redis):
    from webhook.query_handlers import format_history
    text = format_history()
    assert text == "*ARQUIVADOS*\n\nNenhum item arquivado."


def test_history_formats_items(fake_redis):
    from webhook.query_handlers import format_history
    fake_redis.set("platts:archive:2026-04-14:a", json.dumps({
        "id": "a", "title": "Bonds Municipais Sustentam Aço no Q2",
        "archivedAt": "2026-04-14T10:00:00+00:00"
    }))
    fake_redis.set("platts:archive:2026-04-13:b", json.dumps({
        "id": "b", "title": "Greve Port Hedland",
        "archivedAt": "2026-04-13T08:00:00+00:00"
    }))
    text = format_history()
    assert "*ARQUIVADOS · 2 mais recentes*" in text
    assert "1. Bonds Municipais Sustentam Aço no Q2 — 14/abr" in text
    assert "2. Greve Port Hedland — 13/abr" in text


def test_history_truncates_long_title(fake_redis):
    from webhook.query_handlers import format_history
    long_title = "A" * 80
    fake_redis.set("platts:archive:2026-04-15:x", json.dumps({
        "id": "x", "title": long_title,
        "archivedAt": "2026-04-15T10:00:00+00:00"
    }))
    text = format_history()
    assert "A" * 60 + "…" in text
    assert "A" * 61 not in text


def test_history_escapes_markdown_specials_in_title(fake_redis):
    """Titles with *, _, [, ` must be escaped to avoid Telegram 400 errors."""
    from webhook.query_handlers import format_history, _escape_md
    fake_redis.set("platts:archive:2026-04-15:x", json.dumps({
        "id": "x", "title": "Vale_Q2 *bonds* [draft] `code`",
        "archivedAt": "2026-04-15T10:00:00+00:00",
    }))
    text = format_history()
    # Raw specials must NOT appear unescaped
    assert "*bonds*" not in text
    assert "Vale_Q2" not in text
    assert "[draft]" not in text
    assert "`code`" not in text
    # Escaped form must appear
    assert _escape_md("Vale_Q2 *bonds* [draft] `code`") in text


def test_escape_md_helper():
    from webhook.query_handlers import _escape_md
    assert _escape_md("a*b_c[d]`e") == r"a\*b\_c\[d\]\`e"
    assert _escape_md("") == ""
    assert _escape_md(None) == ""


def test_stats_empty_day(fake_redis):
    from webhook.query_handlers import format_stats
    text = format_stats("2026-04-15")
    assert "*HOJE · 15/abr*" in text
    assert "Scraped     0" in text
    assert "Staging     0" in text
    assert "Arquivados  0" in text
    assert "Recusados   0" in text
    assert "Pipeline    0" in text


def test_stats_populated(fake_redis):
    from webhook.query_handlers import format_stats
    fake_redis.sadd("platts:seen:2026-04-15", "a", "b", "c", "d")
    fake_redis.set("platts:staging:s1", json.dumps({"id": "s1"}))
    fake_redis.set("platts:archive:2026-04-15:x1", json.dumps({"id": "x1"}))
    fake_redis.set("platts:archive:2026-04-15:x2", json.dumps({"id": "x2"}))
    fake_redis.sadd("platts:pipeline:processed:2026-04-15", "p1")
    text = format_stats("2026-04-15")
    assert "Scraped     4" in text
    assert "Staging     1" in text
    assert "Arquivados  2" in text
    assert "Pipeline    1" in text
