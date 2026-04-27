"""Tests for /start early return when user is an OneDrive approver only."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_start_approver_only_does_not_create_pending(monkeypatch, mock_message):
    """User in ONEDRIVE_APPROVER_IDS who has no Redis record and isn't admin
    sees a fixed welcome and is NOT registered as pending."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "555")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    from bot.routers.onboarding import cmd_start

    msg = mock_message(chat_id=555, user_id=555)
    msg.from_user.full_name = "Colega"
    msg.from_user.username = "colega"

    create_pending_mock = MagicMock()

    with patch("bot.routers.onboarding.is_admin", return_value=False), \
         patch("bot.routers.onboarding.get_user_role", return_value="unknown"), \
         patch("bot.routers.onboarding.get_user", return_value=None), \
         patch("bot.routers.onboarding.create_pending_user", create_pending_mock), \
         patch("bot.routers.onboarding.get_bot", return_value=AsyncMock()):
        await cmd_start(msg)

    msg.answer.assert_called_once()
    welcome = msg.answer.call_args.args[0] if msg.answer.call_args.args else ""
    assert "aprovador" in welcome.lower() or "onedrive" in welcome.lower() or "sharepoint" in welcome.lower()
    create_pending_mock.assert_not_called()


@pytest.mark.asyncio
async def test_start_approver_who_is_admin_uses_admin_path(monkeypatch, mock_message):
    """Admin who is also in env list still gets the admin welcome (admin role wins)."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "999")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    from bot.routers.onboarding import cmd_start

    msg = mock_message(chat_id=999, user_id=999)
    msg.from_user.full_name = "Admin"
    msg.from_user.username = "admin"

    with patch("bot.routers.onboarding.get_user_role", return_value="admin"), \
         patch("bot.routers.onboarding.is_admin", return_value=True), \
         patch("bot.routers.onboarding.build_reply_keyboard", return_value=MagicMock()):
        await cmd_start(msg)

    msg.answer.assert_called_once()
    welcome = msg.answer.call_args.args[0] if msg.answer.call_args.args else ""
    assert "admin" in welcome.lower()


@pytest.mark.asyncio
async def test_start_subscriber_in_env_uses_subscriber_path(monkeypatch, mock_message):
    """Existing subscriber who is also in env still uses subscriber welcome."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "888")
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    from bot.routers.onboarding import cmd_start

    msg = mock_message(chat_id=888, user_id=888)
    msg.from_user.full_name = "Sub"

    with patch("bot.routers.onboarding.get_user_role", return_value="subscriber"), \
         patch("bot.routers.onboarding.is_admin", return_value=False), \
         patch("bot.routers.onboarding.build_reply_keyboard", return_value=MagicMock()):
        await cmd_start(msg)

    welcome = msg.answer.call_args.args[0] if msg.answer.call_args.args else ""
    assert "volta" in welcome.lower() or "bem vindo" in welcome.lower()


@pytest.mark.asyncio
async def test_start_unknown_user_not_in_env_creates_pending(monkeypatch, mock_message):
    """Regression: unknown users not in env still get the original pending flow."""
    monkeypatch.setenv("ONEDRIVE_APPROVER_IDS", "555")  # different from user
    from bot.users import get_onedrive_approver_ids
    get_onedrive_approver_ids.cache_clear()

    from bot.routers.onboarding import cmd_start

    msg = mock_message(chat_id=777, user_id=777)
    msg.from_user.full_name = "Stranger"
    msg.from_user.username = "stranger"

    create_pending_mock = MagicMock()

    with patch("bot.routers.onboarding.is_admin", return_value=False), \
         patch("bot.routers.onboarding.get_user_role", return_value="unknown"), \
         patch("bot.routers.onboarding.create_pending_user", create_pending_mock), \
         patch("bot.routers.onboarding.get_bot", return_value=AsyncMock()):
        await cmd_start(msg)

    create_pending_mock.assert_called_once()
