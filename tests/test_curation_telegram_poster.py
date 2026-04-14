"""Tests for execution.curation.telegram_poster."""


def test_build_preview_truncates_long_text():
    from execution.curation.telegram_poster import build_preview
    body = "x" * 1000
    preview = build_preview(body, limit=400)
    assert preview.endswith("...")
    assert len(preview) <= 403


def test_build_preview_keeps_short_text():
    from execution.curation.telegram_poster import build_preview
    preview = build_preview("short text", limit=400)
    assert preview == "short text"


def test_format_message_for_top_news():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "abc123",
        "title": "China steel output lags 2025",
        "fullText": "China's steel production continues to trail year-ago levels...",
        "publishDate": "04/09/2026 13:46 UTC",
        "source": "Top News - Ferrous Metals",
        "author": "Jing Zhang",
        "tabName": "",
    }
    msg = format_message(item)
    assert "Top News - Ferrous Metals" in msg
    assert "Jing Zhang" in msg
    assert "04/09/2026 13:46 UTC" in msg
    assert "China steel output lags 2025" in msg
    assert "abc123" in msg


def test_format_message_for_flash():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "def456",
        "title": "Supreme Court strikes down Trump's global tariffs",
        "fullText": "Supreme Court strikes down Trump's global tariffs",
        "publishDate": "02/20/2026 15:09 UTC",
        "source": "allInsights.flash",
        "author": "",
        "tabName": "",
    }
    msg = format_message(item)
    assert "🔴 FLASH" in msg
    assert "02/20/2026 15:09 UTC" in msg


def test_build_keyboard_has_4_buttons():
    from execution.curation.telegram_poster import build_keyboard
    kb = build_keyboard("abc123", preview_url="https://example.com/preview/abc123")
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    assert len(all_buttons) == 4
    urls = [b.get("url") for b in all_buttons if "url" in b]
    callbacks = [b.get("callback_data") for b in all_buttons if "callback_data" in b]
    assert "https://example.com/preview/abc123" in urls
    assert "curate_archive:abc123" in callbacks
    assert "curate_reject:abc123" in callbacks
    assert "curate_pipeline:abc123" in callbacks


def test_post_for_curation_calls_send_with_keyboard(monkeypatch):
    from execution.curation import telegram_poster
    sent = {}

    def fake_send(chat_id, text, reply_markup=None, parse_mode=None):
        sent["chat_id"] = chat_id
        sent["text"] = text
        sent["reply_markup"] = reply_markup

    monkeypatch.setattr(telegram_poster, "_send_message", fake_send)

    item = {
        "id": "abc123",
        "title": "Test",
        "fullText": "body",
        "publishDate": "date",
        "source": "Top News",
        "author": "",
        "tabName": "",
    }
    telegram_poster.post_for_curation(chat_id=99, item=item, preview_base_url="https://w.example.com")
    assert sent["chat_id"] == 99
    assert "Test" in sent["text"]
    assert sent["reply_markup"] is not None
