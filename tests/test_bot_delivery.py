"""Tests for Telegram delivery to subscribers."""
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import fakeredis
import json
import pytest


@pytest.fixture
def fake_redis():
    client = fakeredis.FakeRedis(decode_responses=True)
    with patch("bot.users._get_client", return_value=client):
        yield client


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


def _seed_users(fake_redis):
    fake_redis.set("user:100", json.dumps({
        "chat_id": 100, "name": "A", "username": "a",
        "role": "subscriber", "status": "approved",
        "subscriptions": {"morning_check": True, "daily_report": True},
    }))
    fake_redis.set("user:200", json.dumps({
        "chat_id": 200, "name": "B", "username": "b",
        "role": "subscriber", "status": "approved",
        "subscriptions": {"morning_check": False, "daily_report": True},
    }))


@pytest.mark.asyncio
async def test_deliver_to_subscribers(fake_redis, mock_bot):
    _seed_users(fake_redis)
    with patch("bot.delivery.get_bot", return_value=mock_bot):
        from bot.delivery import deliver_to_subscribers
        results = await deliver_to_subscribers("morning_check", "Test message")
    assert results["sent"] == 1
    assert results["failed"] == 0
    mock_bot.send_message.assert_awaited_once_with(100, "Test message")


@pytest.mark.asyncio
async def test_deliver_to_all_subscribed(fake_redis, mock_bot):
    _seed_users(fake_redis)
    with patch("bot.delivery.get_bot", return_value=mock_bot):
        from bot.delivery import deliver_to_subscribers
        results = await deliver_to_subscribers("daily_report", "Daily msg")
    assert results["sent"] == 2


@pytest.mark.asyncio
async def test_deliver_no_subscribers(fake_redis, mock_bot):
    with patch("bot.delivery.get_bot", return_value=mock_bot):
        from bot.delivery import deliver_to_subscribers
        results = await deliver_to_subscribers("morning_check", "Nobody")
    assert results["sent"] == 0
    mock_bot.send_message.assert_not_awaited()
