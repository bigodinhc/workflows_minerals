"""Join requests for the private client channel.

Telegram delivers a chat_join_request update when someone opens the
/convite link. The admin gets an approve/decline card; approving calls
approve_chat_join_request. Requires allowed_updates to include
'chat_join_request' in set_webhook (see main.py).
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery, ChatJoinRequest

from bot.callback_data import ChannelJoinApproval
from bot.config import get_bot, TELEGRAM_CLIENT_CHANNEL_ID
from bot.keyboards import build_channel_join_keyboard
from bot.users import ADMIN_CHAT_ID, format_user_label, is_admin

logger = logging.getLogger(__name__)

channel_join_router = Router(name="channel_join")


@channel_join_router.chat_join_request()
async def on_join_request(request: ChatJoinRequest):
    if str(request.chat.id) != str(TELEGRAM_CLIENT_CHANNEL_ID):
        return
    user = request.from_user
    if not ADMIN_CHAT_ID:
        logger.warning("join request received but ADMIN_CHAT_ID unset")
        return
    bot = get_bot()
    label = format_user_label(user)
    await bot.send_message(
        ADMIN_CHAT_ID,
        f"🔔 *Pedido de entrada no canal*\n\n"
        f"Nome: {user.full_name}\n"
        f"User: {label}\n"
        f"ID: `{user.id}`",
        reply_markup=build_channel_join_keyboard(user.id),
    )
    logger.info(f"channel join request from {user.id}")


@channel_join_router.callback_query(ChannelJoinApproval.filter())
async def on_join_decision(query: CallbackQuery, callback_data: ChannelJoinApproval):
    if not is_admin(query.from_user.id):
        await query.answer("Nao autorizado")
        return

    bot = get_bot()
    user_id = callback_data.user_id

    if callback_data.action == "approve":
        try:
            await bot.approve_chat_join_request(TELEGRAM_CLIENT_CHANNEL_ID, user_id)
        except Exception as exc:
            logger.error(f"approve_chat_join_request failed: {exc}")
            await query.answer(f"❌ {str(exc)[:60]}", show_alert=True)
            return
        await query.answer("✅ Aprovado")
        await bot.edit_message_text(
            f"✅ *Entrada aprovada* — `{user_id}`",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            reply_markup=None,
        )
        logger.info(f"channel join approved for {user_id}")
    else:
        try:
            await bot.decline_chat_join_request(TELEGRAM_CLIENT_CHANNEL_ID, user_id)
        except Exception as exc:
            logger.error(f"decline_chat_join_request failed: {exc}")
            await query.answer(f"❌ {str(exc)[:60]}", show_alert=True)
            return
        await query.answer("❌ Recusado")
        await bot.edit_message_text(
            f"❌ *Entrada recusada* — `{user_id}`",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            reply_markup=None,
        )
        logger.info(f"channel join declined for {user_id}")
