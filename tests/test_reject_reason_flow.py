"""Tests for rejection reason flow via redis_queries.

The old app.py in-memory REJECT_REASON_STATE has been replaced by Aiogram FSM
(RejectReason.waiting_reason state). These tests verify the underlying Redis
feedback persistence that the FSM handlers call.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import fakeredis
import pytest
from unittest.mock import patch


@pytest.fixture
def fake_redis():
    """Provide a fakeredis client and patch redis_queries to use it."""
    client = fakeredis.FakeRedis(decode_responses=True)
    with patch("redis_queries._client", client):
        with patch("redis_queries._get_client", return_value=client):
            yield client


def test_save_feedback_creates_hash(fake_redis):
    import redis_queries
    fid = redis_queries.save_feedback(
        action="draft_reject", item_id="d123", chat_id=42, reason="", title="Test",
    )
    assert fid
    data = fake_redis.hgetall(f"webhook:feedback:{fid}")
    assert data["action"] == "draft_reject"
    assert data["item_id"] == "d123"
    assert data["chat_id"] == "42"
    assert data["reason"] == ""
    assert data["title"] == "Test"


def test_update_feedback_reason(fake_redis):
    import redis_queries
    fid = redis_queries.save_feedback(
        action="curate_reject", item_id="c456", chat_id=42, reason="", title="Item",
    )
    ok = redis_queries.update_feedback_reason(fid, "too noisy")
    assert ok is True
    data = fake_redis.hgetall(f"webhook:feedback:{fid}")
    assert data["reason"] == "too noisy"


def test_update_feedback_reason_missing_key(fake_redis):
    import redis_queries
    ok = redis_queries.update_feedback_reason("nonexistent-key", "reason")
    assert ok is False


def test_save_feedback_skip_reason(fake_redis):
    """Simulate 'pular' — feedback saved with empty reason, never updated."""
    import redis_queries
    fid = redis_queries.save_feedback(
        action="draft_reject", item_id="d789", chat_id=42, reason="", title="Skip test",
    )
    data = fake_redis.hgetall(f"webhook:feedback:{fid}")
    assert data["reason"] == ""
