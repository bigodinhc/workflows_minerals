"""Callback handlers for queue navigation.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import QueuePage, QueueOpen
from bot.config import get_bot
from bot.middlewares.auth import RoleMiddleware
import query_handlers

logger = logging.getLogger(__name__)

callbacks_queue_router = Router(name="callbacks_queue")
callbacks_queue_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# ── Queue navigation ──

@callbacks_queue_router.callback_query(QueuePage.filter())
async def on_queue_page(query: CallbackQuery, callback_data: QueuePage):
    await query.answer("")
    try:
        body, markup = query_handlers.format_queue_page(page=callback_data.page)
    except Exception as exc:
        logger.error(f"queue_page error: {exc}")
        return
    bot = get_bot()
    await bot.edit_message_text(
        body, chat_id=query.message.chat.id,
        message_id=query.message.message_id, reply_markup=markup,
    )


@callbacks_queue_router.callback_query(QueueOpen.filter())
async def on_queue_open(query: CallbackQuery, callback_data: QueueOpen):
    from execution.curation import redis_client as curation_redis
    from execution.curation import telegram_poster
    chat_id = query.message.chat.id
    try:
        item = curation_redis.get_staging(callback_data.item_id)
    except Exception as exc:
        logger.error(f"queue_open redis error: {exc}")
        await query.answer("⚠️ Redis indisponível")
        return
    if item is None:
        await query.answer("⚠️ Item expirou")
        return
    await query.answer("")
    preview_base_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")
    try:
        await asyncio.to_thread(telegram_poster.post_for_curation, chat_id, item, preview_base_url)
    except Exception as exc:
        logger.error(f"queue_open post error: {exc}")
        await query.message.answer("❌ Erro ao abrir card.")
