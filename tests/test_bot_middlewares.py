"""Tests for AdminAuthMiddleware."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
from bot.middlewares.auth import AdminAuthMiddleware


@pytest.fixture
def middleware():
    return AdminAuthMiddleware()


def _make_event(user_id):
    event = MagicMock()
    event.from_user = MagicMock()
    event.from_user.id = user_id
    return event


@pytest.mark.asyncio
async def test_authorized_user_passes_through(middleware):
    handler = AsyncMock(return_value="result")
    event = _make_event(12345)
    with patch("bot.middlewares.auth.contact_admin") as mock_ca:
        mock_ca.is_authorized.return_value = True
        result = await middleware(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_unauthorized_user_blocked(middleware):
    handler = AsyncMock(return_value="result")
    event = _make_event(99999)
    with patch("bot.middlewares.auth.contact_admin") as mock_ca:
        mock_ca.is_authorized.return_value = False
        result = await middleware(handler, event, {})
    assert result is None
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_without_from_user_passes(middleware):
    handler = AsyncMock(return_value="result")
    event = MagicMock(spec=[])  # no from_user attr
    result = await middleware(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()
