"""Tests for execution.curation.router."""
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    from execution.curation import redis_client
    monkeypatch.setattr(redis_client, "_get_client", lambda: fake)
    monkeypatch.setattr(redis_client, "_client", None)
    return fake


def test_classify_rmw_rationale_is_rationale():
    from execution.curation.router import classify
    item = {"source": "rmw.CFR North China Iron Ore 65% Fe Rationale", "tabName": "CFR North China Iron Ore 65% Fe Rationale"}
    assert classify(item) == "rationale"


def test_classify_rmw_iodex_commentary_is_rationale():
    from execution.curation.router import classify
    item = {"source": "rmw.IODEX Commentary and Rationale", "tabName": "IODEX Commentary and Rationale"}
    assert classify(item) == "rationale"


def test_classify_rmw_lump_is_rationale():
    from execution.curation.router import classify
    item = {"source": "rmw.Lump", "tabName": "Lump"}
    assert classify(item) == "rationale"


def test_classify_rmw_bots_is_curation():
    from execution.curation.router import classify
    item = {"source": "rmw.IODEX BOTs and Summary", "tabName": "IODEX BOTs and Summary"}
    assert classify(item) == "curation"


def test_classify_top_news_is_curation():
    from execution.curation.router import classify
    item = {"source": "Top News - Ferrous Metals", "tabName": ""}
    assert classify(item) == "curation"


def test_classify_flash_is_curation():
    from execution.curation.router import classify
    item = {"source": "allInsights.flash", "tabName": ""}
    assert classify(item) == "curation"


def test_route_items_skips_already_seen(fake_redis, monkeypatch):
    """Seen items are neither staged nor posted."""
    from execution.curation import router, redis_client
    from execution.curation.id_gen import generate_id

    posted = []

    def fake_post(chat_id, item, preview_base_url):
        posted.append(item["id"])

    monkeypatch.setattr(router, "_post_for_curation", fake_post)

    item = {"source": "Top News - Ferrous Metals", "title": "Already Seen", "fullText": "x", "tabName": ""}
    item_id = generate_id(item["source"], item["title"])
    redis_client.mark_seen("2026-04-14", item_id)

    router.route_items(
        items=[item],
        today_date="2026-04-14",
        today_br="14/04/2026",
        chat_id=99,
        preview_base_url="https://example.com",
        rationale_processor=lambda rationale_items, today_br: True,
    )
    assert posted == []


def test_route_items_stages_new_curation(fake_redis, monkeypatch):
    from execution.curation import router, redis_client

    posted = []

    def fake_post(chat_id, item, preview_base_url):
        posted.append(item["id"])

    monkeypatch.setattr(router, "_post_for_curation", fake_post)

    item = {"source": "Top News - Ferrous Metals", "title": "Fresh News", "fullText": "x" * 50, "tabName": ""}
    router.route_items(
        items=[item],
        today_date="2026-04-14",
        today_br="14/04/2026",
        chat_id=99,
        preview_base_url="https://example.com",
        rationale_processor=lambda rationale_items, today_br: True,
    )
    assert len(posted) == 1
    staged = redis_client.get_staging(posted[0])
    assert staged["title"] == "Fresh News"
    assert redis_client.is_seen("2026-04-14", posted[0]) is True


def test_route_items_dispatches_rationale_once(fake_redis, monkeypatch):
    from execution.curation import router

    rationale_calls = []

    def fake_rationale(rationale_items, today_br):
        rationale_calls.append(len(rationale_items))
        return True

    monkeypatch.setattr(router, "_post_for_curation", lambda *a, **kw: None)

    items = [
        {"source": "rmw.CFR North China Iron Ore 65% Fe Rationale", "tabName": "CFR North China Iron Ore 65% Fe Rationale", "title": "R1", "fullText": "r1"},
        {"source": "rmw.Lump", "tabName": "Lump", "title": "R2", "fullText": "r2"},
    ]
    router.route_items(
        items=items,
        today_date="2026-04-14",
        today_br="14/04/2026",
        chat_id=99,
        preview_base_url="https://example.com",
        rationale_processor=fake_rationale,
    )
    assert rationale_calls == [2]


def test_route_items_skips_rationale_if_already_processed(fake_redis, monkeypatch):
    from execution.curation import router, redis_client
    redis_client.set_rationale_processed("2026-04-14")

    rationale_calls = []

    def fake_rationale(rationale_items, today_br):
        rationale_calls.append(len(rationale_items))
        return True

    monkeypatch.setattr(router, "_post_for_curation", lambda *a, **kw: None)

    items = [
        {"source": "rmw.Lump", "tabName": "Lump", "title": "R1", "fullText": "r1"},
    ]
    router.route_items(
        items=items,
        today_date="2026-04-14",
        today_br="14/04/2026",
        chat_id=99,
        preview_base_url="https://example.com",
        rationale_processor=fake_rationale,
    )
    assert rationale_calls == []
