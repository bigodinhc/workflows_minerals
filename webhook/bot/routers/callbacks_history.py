"""Callback handlers for the news bank (/history): day navigation + open card.

Items are read from Supabase (platts_news), so already-sent/archived news stay
consultable — unlike /queue, which only sees the live Redis staging.
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from bot.callback_data import HistNav, HistOpen
from bot.middlewares.auth import RoleMiddleware
import query_handlers
from execution.curation import news_repo
from execution.curation import telegram_poster

logger = logging.getLogger(__name__)

callbacks_history_router = Router(name="callbacks_history")
callbacks_history_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


@callbacks_history_router.callback_query(HistNav.filter())
async def on_hist_nav(query: CallbackQuery, callback_data: HistNav):
    """Re-render the bank message in place for a new day or type filter."""
    await query.answer("")
    try:
        text, markup = query_handlers.format_history_page(
            callback_data.date, flt=callback_data.flt,
        )
    except Exception as exc:
        logger.error(f"hist_nav format error: {exc}")
        await query.message.answer("❌ Erro ao consultar o banco.")
        return
    try:
        await query.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as exc:
        # 'message is not modified' quando o usuário repete o mesmo filtro/dia.
        logger.warning(f"hist_nav edit failed: {exc}")


@callbacks_history_router.callback_query(HistOpen.filter())
async def on_hist_open(query: CallbackQuery, callback_data: HistOpen):
    """Open one bank item as a full card, reading from Supabase."""
    chat_id = query.message.chat.id
    try:
        item = await asyncio.to_thread(news_repo.get_by_id, callback_data.item_id)
    except Exception as exc:
        logger.error(f"hist_open supabase error: {exc}")
        await query.answer("⚠️ Supabase indisponível")
        return
    if item is None:
        await query.answer("⚠️ Notícia não encontrada no banco")
        return
    await query.answer("")
    preview_base_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")
    try:
        await asyncio.to_thread(
            telegram_poster.post_from_history, chat_id, item, preview_base_url,
        )
    except Exception as exc:
        logger.error(f"hist_open post error: {exc}")
        await query.message.answer("❌ Erro ao abrir card.")
