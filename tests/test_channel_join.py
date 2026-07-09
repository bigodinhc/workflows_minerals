"""Channel join requests: admin card + approve/decline callbacks."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.approve_chat_join_request = AsyncMock()
    bot.decline_chat_join_request = AsyncMock()
    return bot


def _join_request(chat_id: str = "-1001234", user_id: int = 555):
    req = MagicMock()
    req.chat.id = int(chat_id)
    req.from_user.id = user_id
    req.from_user.full_name = "Cliente Teste"
    req.from_user.username = "cliente"
    req.from_user.first_name = "Cliente"
    return req


def _callback(user_id: int = 777, data: str = ""):
    cb = MagicMock()
    cb.from_user.id = user_id
    cb.message.chat.id = 999
    cb.message.message_id = 1
    cb.answer = AsyncMock()
    return cb


@pytest.mark.asyncio
async def test_join_request_notifies_admin(mock_bot):
    import bot.routers.channel_join as cj
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch.object(cj, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"), \
         patch.object(cj, "ADMIN_CHAT_ID", 999):
        await cj.on_join_request(_join_request())
    args, kwargs = mock_bot.send_message.await_args
    assert args[0] == 999
    assert "555" in args[1]
    assert kwargs["reply_markup"] is not None
    assert kwargs["parse_mode"] is None


@pytest.mark.asyncio
async def test_join_request_other_chat_ignored(mock_bot):
    import bot.routers.channel_join as cj
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch.object(cj, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"), \
         patch.object(cj, "ADMIN_CHAT_ID", 999):
        await cj.on_join_request(_join_request(chat_id="-1009999"))
    mock_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_calls_api_and_updates_card(mock_bot):
    import bot.routers.channel_join as cj
    from bot.callback_data import ChannelJoinApproval
    cb_data = ChannelJoinApproval(action="approve", user_id=555)
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch.object(cj, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"), \
         patch("bot.routers.channel_join.is_admin", return_value=True):
        await cj.on_join_decision(_callback(), cb_data)
    mock_bot.approve_chat_join_request.assert_awaited_once_with("-1001234", 555)
    mock_bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_decline_calls_api(mock_bot):
    import bot.routers.channel_join as cj
    from bot.callback_data import ChannelJoinApproval
    cb_data = ChannelJoinApproval(action="decline", user_id=555)
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch.object(cj, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"), \
         patch("bot.routers.channel_join.is_admin", return_value=True):
        await cj.on_join_decision(_callback(), cb_data)
    mock_bot.decline_chat_join_request.assert_awaited_once_with("-1001234", 555)


@pytest.mark.asyncio
async def test_non_admin_cannot_decide(mock_bot):
    import bot.routers.channel_join as cj
    from bot.callback_data import ChannelJoinApproval
    cb_data = ChannelJoinApproval(action="approve", user_id=555)
    cb = _callback()
    with patch.object(cj, "get_bot", return_value=mock_bot), \
         patch("bot.routers.channel_join.is_admin", return_value=False):
        await cj.on_join_decision(cb, cb_data)
    mock_bot.approve_chat_join_request.assert_not_awaited()
    cb.answer.assert_awaited_once()
