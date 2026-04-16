"""Tests for webhook.digest (ingestion digest formatter)."""


def test_digest_returns_none_on_zero_staged():
    from digest import format_ingestion_digest
    counters = {"staged": 0, "news_staged": 0, "rationale_staged": 0}
    result = format_ingestion_digest(counters, [])
    assert result is None


def test_digest_news_only_hides_rationale_line():
    from digest import format_ingestion_digest
    counters = {"staged": 3, "news_staged": 3, "rationale_staged": 0}
    items = [
        {"title": "Alpha", "type": "news"},
        {"title": "Beta", "type": "news"},
        {"title": "Gamma", "type": "news"},
    ]
    text, markup = format_ingestion_digest(counters, items)
    assert "Ingestão · 3 novas" in text
    assert "🗞️ 3 notícias" in text
    assert "rationale" not in text.lower()
    assert "🗞️ Alpha" in text
    assert "🗞️ Beta" in text


def test_digest_rationale_only_hides_news_line():
    from digest import format_ingestion_digest
    counters = {"staged": 2, "news_staged": 0, "rationale_staged": 2}
    items = [
        {"title": "Daily Rationale", "type": "rationale"},
        {"title": "Lump Premium", "type": "rationale"},
    ]
    text, _ = format_ingestion_digest(counters, items)
    assert "📊 2 rationale" in text
    assert "notícias" not in text
    assert "📊 Daily Rationale" in text


def test_digest_mixed_shows_tree():
    from digest import format_ingestion_digest
    counters = {"staged": 5, "news_staged": 3, "rationale_staged": 2}
    items = [{"title": f"Item {i}", "type": "news"} for i in range(5)]
    text, _ = format_ingestion_digest(counters, items)
    assert "├ 🗞️ 3 notícias" in text
    assert "└ 📊 2 rationale" in text


def test_digest_preview_limits_to_3_items():
    from digest import format_ingestion_digest
    counters = {"staged": 5, "news_staged": 5, "rationale_staged": 0}
    items = [{"title": f"Title {i}", "type": "news"} for i in range(5)]
    text, _ = format_ingestion_digest(counters, items)
    assert "Title 0" in text
    assert "Title 1" in text
    assert "Title 2" in text
    assert "Title 3" not in text
    assert "+2 mais" in text


def test_digest_no_plus_when_exactly_3():
    from digest import format_ingestion_digest
    counters = {"staged": 3, "news_staged": 3, "rationale_staged": 0}
    items = [{"title": f"T{i}", "type": "news"} for i in range(3)]
    text, _ = format_ingestion_digest(counters, items)
    assert "+0 mais" not in text
    assert "mais" not in text


def test_digest_escapes_markdown_in_titles():
    from digest import format_ingestion_digest
    counters = {"staged": 1, "news_staged": 1, "rationale_staged": 0}
    items = [{"title": "Vale_Q2 *report* [draft]", "type": "news"}]
    text, _ = format_ingestion_digest(counters, items)
    assert "*report*" not in text
    assert "Vale_Q2" not in text
    assert "\\*report\\*" in text or "report" in text.replace("\\*", "")


def test_digest_markup_has_open_queue_button():
    from digest import format_ingestion_digest
    counters = {"staged": 1, "news_staged": 1, "rationale_staged": 0}
    items = [{"title": "X", "type": "news"}]
    _, markup = format_ingestion_digest(counters, items)
    assert markup is not None
    buttons = markup["inline_keyboard"]
    assert len(buttons) == 1
    assert buttons[0][0]["callback_data"] == "queue_page:1"
    assert "🔍" in buttons[0][0]["text"]
    assert "fila" in buttons[0][0]["text"].lower()
