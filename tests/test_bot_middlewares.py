"""Tests for role-aware middleware."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
from bot.middlewares.auth import RoleMiddleware


def _make_event(user_id):
    event = MagicMock()
    event.from_user = MagicMock()
    event.from_user.id = user_id
    return event


@pytest.mark.asyncio
async def test_admin_passes_admin_only_middleware():
    mw = RoleMiddleware(allowed_roles={"admin"})
    handler = AsyncMock(return_value="result")
    event = _make_event(12345)
    with patch("bot.middlewares.auth.get_user_role", return_value="admin"):
        result = await mw(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscriber_blocked_by_admin_only():
    mw = RoleMiddleware(allowed_roles={"admin"})
    handler = AsyncMock(return_value="result")
    event = _make_event(99999)
    with patch("bot.middlewares.auth.get_user_role", return_value="subscriber"):
        result = await mw(handler, event, {})
    assert result is None
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscriber_passes_subscriber_middleware():
    mw = RoleMiddleware(allowed_roles={"admin", "subscriber"})
    handler = AsyncMock(return_value="result")
    event = _make_event(55555)
    with patch("bot.middlewares.auth.get_user_role", return_value="subscriber"):
        result = await mw(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_user_blocked():
    mw = RoleMiddleware(allowed_roles={"admin", "subscriber"})
    handler = AsyncMock(return_value="result")
    event = _make_event(77777)
    with patch("bot.middlewares.auth.get_user_role", return_value="unknown"):
        result = await mw(handler, event, {})
    assert result is None
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_event_without_from_user_passes():
    mw = RoleMiddleware(allowed_roles={"admin"})
    handler = AsyncMock(return_value="result")
    event = MagicMock(spec=[])  # no from_user attr
    result = await mw(handler, event, {})
    assert result == "result"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_role_injected_into_data():
    mw = RoleMiddleware(allowed_roles={"admin"})
    handler = AsyncMock(return_value="result")
    event = _make_event(12345)
    data = {}
    with patch("bot.middlewares.auth.get_user_role", return_value="admin"):
        await mw(handler, event, data)
    assert data["user_role"] == "admin"
