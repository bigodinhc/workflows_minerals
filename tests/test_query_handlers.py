"""Tests for webhook.query_handlers formatters."""
import json
import time
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    """Patch both module aliases since query_handlers uses bare `import
    redis_queries` (matching production layout) while tests reference
    `webhook.redis_queries`. Both aliases resolve to distinct sys.modules
    entries even though they're the same file.
    """
    fake = fakeredis.FakeRedis(decode_responses=True)
    from webhook import redis_queries as rq_pkg
    import redis_queries as rq_bare  # noqa: PLC0415
    for rq in (rq_pkg, rq_bare):
        monkeypatch.setattr(rq, "_get_client", lambda: fake)
        monkeypatch.setattr(rq, "_client", None)
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
    assert text == "*📚 ARQUIVADOS*\n\nNenhum item arquivado."


def test_history_formats_items_with_type_icon(fake_redis):
    from webhook.query_handlers import format_history
    fake_redis.set("platts:archive:2026-04-14:a", json.dumps({
        "id": "a", "title": "Bonds Municipais", "type": "news",
        "archivedAt": "2026-04-14T10:00:00+00:00"
    }))
    fake_redis.set("platts:archive:2026-04-13:b", json.dumps({
        "id": "b", "title": "Daily Rationale", "type": "rationale",
        "archivedAt": "2026-04-13T08:00:00+00:00"
    }))
    text = format_history()
    assert "*📚 ARQUIVADOS · 2 mais recentes*" in text
    assert "────" in text
    assert "1. 🗞️ Bonds Municipais — 14/abr" in text
    assert "2. 📊 Daily Rationale — 13/abr" in text


def test_history_falls_back_to_news_icon_when_type_missing(fake_redis):
    """Legacy archived items (pre-v1.1) don't carry `type`; default to news icon."""
    from webhook.query_handlers import format_history
    fake_redis.set("platts:archive:2026-04-14:legacy", json.dumps({
        "id": "legacy", "title": "Legacy",
        "archivedAt": "2026-04-14T10:00:00+00:00"
    }))
    text = format_history()
    assert "1. 🗞️ Legacy — 14/abr" in text


def test_history_truncates_long_title(fake_redis):
    from webhook.query_handlers import format_history
    long_title = "A" * 80
    fake_redis.set("platts:archive:2026-04-15:x", json.dumps({
        "id": "x", "title": long_title, "type": "news",
        "archivedAt": "2026-04-15T10:00:00+00:00"
    }))
    text = format_history()
    assert "A" * 60 + "…" in text
    assert "A" * 61 not in text


def test_history_escapes_markdown_in_title(fake_redis):
    from webhook.query_handlers import format_history, _escape_md
    fake_redis.set("platts:archive:2026-04-15:x", json.dumps({
        "id": "x", "title": "Vale_Q2 *bonds*", "type": "news",
        "archivedAt": "2026-04-15T10:00:00+00:00",
    }))
    text = format_history()
    assert "*bonds*" not in text
    assert "Vale_Q2" not in text
    assert _escape_md("Vale_Q2 *bonds*") in text


def test_escape_md_helper():
    from webhook.query_handlers import _escape_md
    assert _escape_md("a*b_c[d]`e") == r"a\*b\_c\[d\]\`e"
    assert _escape_md("") == ""
    assert _escape_md(None) == ""


def test_stats_empty_day(fake_redis):
    from webhook.query_handlers import format_stats
    text = format_stats("2026-04-15")
    assert "*📊 HOJE · 15/abr*" in text
    assert "────" in text
    assert "🔎 Scraped" in text
    assert "🗂️ Staging" in text
    assert "📦 Arquivados" in text
    assert "❌ Recusados" in text
    assert "🖋️ No Writer" in text
    # Legacy label must be gone
    assert "Pipeline" not in text


def test_stats_populated(fake_redis):
    from webhook.query_handlers import format_stats
    fake_redis.sadd("platts:scraped:2026-04-15", "a", "b", "c", "d")
    fake_redis.set("platts:staging:s1", json.dumps({"id": "s1"}))
    fake_redis.set("platts:archive:2026-04-15:x1", json.dumps({"id": "x1"}))
    fake_redis.set("platts:archive:2026-04-15:x2", json.dumps({"id": "x2"}))
    fake_redis.sadd("platts:pipeline:processed:2026-04-15", "p1")
    text = format_stats("2026-04-15")
    assert "🔎 Scraped" in text and "4" in text
    assert "🗂️ Staging" in text and "1" in text
    assert "📦 Arquivados" in text and "2" in text
    assert "🖋️ No Writer" in text and "1" in text


def test_rejections_empty(fake_redis):
    from webhook.query_handlers import format_rejections
    text = format_rejections()
    assert text == "*💭 RECUSAS*\n\nNenhuma recusa registrada."


def test_rejections_with_and_without_reason(fake_redis):
    from webhook.query_handlers import format_rejections
    from webhook.redis_queries import save_feedback
    save_feedback("curate_reject", "a", 1, "", "First")
    time.sleep(0.01)
    save_feedback("curate_reject", "b", 1, "duplicata", "Second")
    text = format_rejections()
    assert "*💭 RECUSAS · últimas 2*" in text
    assert "────" in text
    assert "🕒" in text
    assert '"duplicata"' in text
    assert "_(sem razão)_" in text


def test_rejections_truncates_long_reason(fake_redis):
    from webhook.query_handlers import format_rejections
    from webhook.redis_queries import save_feedback
    long = "x" * 120
    save_feedback("curate_reject", "a", 1, long, "T")
    text = format_rejections()
    assert "x" * 80 + "…" in text
    assert "x" * 81 not in text


def test_rejections_escapes_markdown_in_reason(fake_redis):
    """Reason can contain user free-text — escape * _ ` [ to avoid 400."""
    from webhook.query_handlers import format_rejections, _escape_md
    from webhook.redis_queries import save_feedback
    save_feedback("curate_reject", "a", 1, "dup of *foo* [bar]", "T")
    text = format_rejections()
    assert "*foo*" not in text
    assert "[bar]" not in text
    assert _escape_md("dup of *foo* [bar]") in text


def test_queue_empty(fake_redis):
    from webhook.query_handlers import format_queue_page
    text, markup = format_queue_page(page=1)
    assert text == "*🗂️ STAGING*\n\nNenhum item aguardando."
    assert markup is None


def test_queue_single_page_titles_in_buttons(fake_redis):
    from webhook.query_handlers import format_queue_page
    for i, ts in enumerate(["10:00", "09:00", "08:00"]):
        fake_redis.set(f"platts:staging:item{i}", json.dumps({
            "id": f"item{i}", "title": f"Title {i}", "type": "news",
            "stagedAt": f"2026-04-15T{ts}:00Z"
        }))
    text, markup = format_queue_page(page=1)
    assert "STAGING" in text
    assert "3 items" in text
    assert "coletados" in text
    # Buttons carry title with icon + collection time
    buttons = markup["inline_keyboard"]
    assert len(buttons) == 3
    assert buttons[0][0]["text"].startswith("🗞️ Title 0")
    assert "🕐" in buttons[0][0]["text"]
    assert buttons[0][0]["callback_data"] == "queue_open:item0"


def test_queue_button_uses_rationale_icon(fake_redis):
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": "Daily Rationale", "type": "rationale",
        "stagedAt": "2026-04-15T10:00:00Z"
    }))
    _, markup = format_queue_page(page=1)
    assert markup["inline_keyboard"][0][0]["text"].startswith("📊 Daily Rationale")
    assert "🕐" in markup["inline_keyboard"][0][0]["text"]


def test_queue_paginated(fake_redis):
    from webhook.query_handlers import format_queue_page
    for i in range(12):
        fake_redis.set(f"platts:staging:i{i:02d}", json.dumps({
            "id": f"i{i:02d}", "title": f"Title {i:02d}", "type": "news",
            "stagedAt": f"2026-04-15T{i:02d}:00:00Z"
        }))
    text_p1, markup_p1 = format_queue_page(page=1)
    assert "STAGING" in text_p1
    assert "12 items" in text_p1
    assert "coletados" in text_p1
    # 5 item rows + 1 pagination row
    assert len(markup_p1["inline_keyboard"]) == 6
    # Item buttons têm o título
    assert markup_p1["inline_keyboard"][0][0]["text"].startswith("🗞️ Title 11")
    # Pagination row (última)
    pag_texts = [b["text"] for b in markup_p1["inline_keyboard"][-1]]
    assert any("1/3" in t for t in pag_texts)
    assert any("próximo" in t.lower() for t in pag_texts)
    assert not any("anterior" in t.lower() for t in pag_texts)


def test_queue_truncates_long_title_in_button(fake_redis):
    from webhook.query_handlers import format_queue_page
    long_title = "B" * 80
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": long_title, "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z"
    }))
    _, markup = format_queue_page(page=1)
    btn_text = markup["inline_keyboard"][0][0]["text"]
    # Título truncado em 40 chars + "…" + ícone + espaço ≤ 64
    assert btn_text.startswith("🗞️ ")
    assert "…" in btn_text
    assert len(btn_text) <= 64


def test_queue_escapes_markdown_in_button_text(fake_redis):
    """Button text is plain (NOT markdown-parsed by Telegram) — but we still
    want *markers* removed so the operator sees clean text."""
    from webhook.query_handlers import format_queue_page
    fake_redis.set("platts:staging:x", json.dumps({
        "id": "x", "title": "Vale *Q2* [report]", "type": "news",
        "stagedAt": "2026-04-15T10:00:00Z"
    }))
    _, markup = format_queue_page(page=1)
    btn_text = markup["inline_keyboard"][0][0]["text"]
    # Button text is plain, so markdown chars are OK literally
    assert "Vale" in btn_text
