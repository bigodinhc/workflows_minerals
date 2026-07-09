"""Admin /convite command: invite link with join request + QR photo."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture
def mock_message():
    msg = MagicMock()
    msg.chat.id = 999
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    return msg


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    link = MagicMock()
    link.invite_link = "https://t.me/+AbCdEf123"
    bot.create_chat_invite_link = AsyncMock(return_value=link)
    return bot


@pytest.mark.asyncio
async def test_convite_creates_join_request_link_and_qr(mock_message, mock_bot):
    import bot.routers.commands as cmds
    with patch.object(cmds, "get_bot", return_value=mock_bot), \
         patch("bot.config.TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"):
        await cmds.cmd_convite(mock_message)
    _, kwargs = mock_bot.create_chat_invite_link.await_args
    assert kwargs["chat_id"] == "-1001234"
    assert kwargs["creates_join_request"] is True
    assert "member_limit" not in kwargs  # mutually exclusive with join requests
    mock_message.answer_photo.assert_awaited_once()
    _, photo_kwargs = mock_message.answer_photo.await_args
    assert "https://t.me/+AbCdEf123" in photo_kwargs["caption"]


@pytest.mark.asyncio
async def test_convite_without_channel_configured(mock_message, mock_bot):
    import bot.routers.commands as cmds
    with patch.object(cmds, "get_bot", return_value=mock_bot), \
         patch("bot.config.TELEGRAM_CLIENT_CHANNEL_ID", ""):
        await cmds.cmd_convite(mock_message)
    mock_bot.create_chat_invite_link.assert_not_awaited()
    args, _ = mock_message.answer.await_args
    assert "TELEGRAM_CLIENT_CHANNEL_ID" in args[0]


def test_qr_png_bytes_returns_png():
    from bot.routers.commands import _qr_png_bytes
    data = _qr_png_bytes("https://t.me/+AbCdEf123")
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
