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
