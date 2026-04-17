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


def test_build_keyboard_has_5_buttons():
    from execution.curation.telegram_poster import build_keyboard
    kb = build_keyboard("abc123", preview_url="https://example.com/preview/abc123")
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    assert len(all_buttons) == 5
    urls = [b.get("url") for b in all_buttons if "url" in b]
    callbacks = [b.get("callback_data") for b in all_buttons if "callback_data" in b]
    assert "https://example.com/preview/abc123" in urls
    assert "curate:archive:abc123" in callbacks
    assert "curate:reject:abc123" in callbacks
    assert "curate:pipeline:abc123" in callbacks
    assert "curate:send_raw:abc123" in callbacks


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


def test_format_message_escapes_markdown_specials_in_title():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "x1",
        "title": "US-China trade_war [update] *flash* `test`",
        "fullText": "body",
        "publishDate": "date",
        "source": "Source",
        "author": "",
        "tabName": "",
    }
    msg = format_message(item)
    # Every special char in the dynamic title must be preceded by a backslash
    assert "\\_" in msg
    assert "\\*" in msg
    assert "\\[" in msg
    assert "\\`" in msg


def test_format_message_escapes_source_and_id():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "abc_123",
        "title": "Normal",
        "fullText": "body",
        "publishDate": "date",
        "source": "Source *X*",
        "author": "",
        "tabName": "",
    }
    msg = format_message(item)
    assert "Source \\*X\\*" in msg
    assert "abc\\_123" in msg


def test_post_for_curation_raises_on_missing_id(monkeypatch):
    from execution.curation import telegram_poster
    monkeypatch.setattr(telegram_poster, "_send_message", lambda *a, **k: None)
    import pytest
    with pytest.raises(ValueError, match="item\\['id'\\]"):
        telegram_poster.post_for_curation(
            chat_id=1,
            item={"title": "no id"},
            preview_base_url="https://example.com",
        )


def test_post_for_curation_raises_on_invalid_preview_url(monkeypatch):
    from execution.curation import telegram_poster
    monkeypatch.setattr(telegram_poster, "_send_message", lambda *a, **k: None)
    import pytest
    with pytest.raises(ValueError, match="absolute http"):
        telegram_poster.post_for_curation(
            chat_id=1,
            item={"id": "x"},
            preview_base_url="",
        )
    with pytest.raises(ValueError, match="absolute http"):
        telegram_poster.post_for_curation(
            chat_id=1,
            item={"id": "x"},
            preview_base_url="example.com/no-protocol",
        )


def test_format_message_uses_rationale_icon_when_type_rationale():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "r1", "type": "rationale",
        "title": "Daily Rationale",
        "fullText": "preview text",
        "publishDate": "04/15/2026",
        "source": "rmw_market",
        "tabName": "Rationale",
        "author": "",
    }
    msg = format_message(item)
    # Rationale icon on title, not news
    assert msg.startswith("*📊 Daily Rationale*")


def test_format_message_uses_news_icon_for_default_type():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "n1",
        "title": "Iron Ore Drops",
        "fullText": "preview",
        "publishDate": "04/15/2026",
        "source": "platts",
        "tabName": "News",
        "author": "",
    }
    msg = format_message(item)
    # No `type` field → defaults to news icon
    assert msg.startswith("*🗞️ Iron Ore Drops*")


def test_format_message_single_line_meta_for_non_flash():
    from execution.curation.telegram_poster import format_message
    item = {
        "id": "abc",
        "title": "X",
        "fullText": "preview",
        "publishDate": "14/04 13:46 UTC",
        "source": "Platts",
        "tabName": "Iron Ore News",
        "author": "",
    }
    msg = format_message(item)
    # meta in a single "·"-separated line
    assert " · " in msg
    assert "📅" in msg and "📰" in msg and "🔖" in msg


def test_build_keyboard_uses_writer_label():
    from execution.curation.telegram_poster import build_keyboard
    kb = build_keyboard("abc123", preview_url="https://example.com/preview/abc123")
    all_buttons = [b for row in kb["inline_keyboard"] for b in row]
    texts = [b["text"] for b in all_buttons]
    assert "🖋️ Writer" in texts
    assert "🤖 3 Agents" not in texts


def test_build_keyboard_has_2x2_layout_plus_url_row():
    from execution.curation.telegram_poster import build_keyboard
    kb = build_keyboard("abc123", preview_url="https://example.com/preview/abc123")
    rows = kb["inline_keyboard"]
    # Layout: row 1 = [Ler completo, Arquivar], row 2 = [Writer, WhatsApp], row 3 = [Recusar]
    assert len(rows) == 3
    assert len(rows[0]) == 2
    assert len(rows[1]) == 2
    assert len(rows[2]) == 1
    row0_texts = [b["text"] for b in rows[0]]
    row1_texts = [b["text"] for b in rows[1]]
    row2_texts = [b["text"] for b in rows[2]]
    assert "📖 Ler completo" in row0_texts
    assert "✅ Arquivar" in row0_texts
    assert "🖋️ Writer" in row1_texts
    assert "📲 WhatsApp" in row1_texts
    assert "❌ Recusar" in row2_texts
