"""Settings and subscription management.

Handles the /settings command and reply keyboard "Settings" button.
Re-uses the same subscription panel from onboarding.
"""

from __future__ import annotations

import logging

from bot.config import get_bot
from bot.keyboards import build_subscription_keyboard
from bot.users import get_user

logger = logging.getLogger(__name__)


async def show_subscription_panel(chat_id: int, message_id: int = None):
    """Show the subscription toggle panel. If message_id given, edit; else send new."""
    bot = get_bot()
    user = get_user(chat_id)
    if user is None:
        await bot.send_message(chat_id, "⚠️ Voce nao esta registrado. Use /start.")
        return

    subs = user.get("subscriptions", {})
    text = "⚙️ *Notificacoes*\n\nEscolha o que receber:\n\nToque para ativar/desativar."

    if message_id:
        await bot.edit_message_text(
            text, chat_id=chat_id, message_id=message_id,
            reply_markup=build_subscription_keyboard(subs),
        )
    else:
        await bot.send_message(
            chat_id, text,
            reply_markup=build_subscription_keyboard(subs),
        )
