"""CLIENT_DELIVERY_CHANNEL=telegram routes approvals to the channel, not uazapi."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    return bot


@pytest.mark.asyncio
async def test_approval_telegram_mode_posts_to_channel(monkeypatch, mock_bot):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 5, "error": None})
    contacts_mock = AsyncMock()
    with patch("dispatch.get_bot", return_value=mock_bot), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch.get_contacts", contacts_mock):
        from dispatch import process_approval_async
        await process_approval_async(999, "Relatório do dia", "draft-1")
    channel_mock.assert_awaited_once_with("Relatório do dia")
    contacts_mock.assert_not_awaited()  # WhatsApp path never touched
    # Admin got a confirmation message
    confirmations = [c.args[1] for c in mock_bot.send_message.await_args_list]
    assert any("canal" in text.lower() for text in confirmations)


@pytest.mark.asyncio
async def test_approval_telegram_mode_reports_failure(monkeypatch, mock_bot):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    channel_mock = AsyncMock(return_value={"ok": False, "message_id": None, "error": "no channel"})
    with patch("dispatch.get_bot", return_value=mock_bot), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock):
        from dispatch import process_approval_async
        await process_approval_async(999, "Relatório", "draft-2")
    confirmations = [c.args[1] for c in mock_bot.send_message.await_args_list]
    assert any("❌" in text for text in confirmations)


@pytest.mark.asyncio
async def test_approval_uazapi_mode_keeps_whatsapp_path(monkeypatch, mock_bot):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    channel_mock = AsyncMock()
    contacts_mock = AsyncMock(return_value=[])  # empty list → fan-out no-ops
    with patch("dispatch.get_bot", return_value=mock_bot), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch.get_contacts", contacts_mock):
        from dispatch import process_approval_async
        await process_approval_async(999, "Relatório", "draft-3")
    contacts_mock.assert_awaited_once()
    channel_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_test_send_telegram_mode_previews_to_admin(monkeypatch, mock_bot):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")
    contacts_mock = AsyncMock()
    with patch("dispatch.get_bot", return_value=mock_bot), \
         patch("dispatch.get_contacts", contacts_mock):
        from dispatch import process_test_send_async
        await process_test_send_async(999, "draft-4", "Corpo do relatório")
    contacts_mock.assert_not_awaited()
    args, kwargs = mock_bot.send_message.await_args
    assert args[0] == 999
    assert "PREVIEW" in args[1]
    assert kwargs.get("reply_markup") is not None  # approval keyboard attached
    assert kwargs["parse_mode"] is None
