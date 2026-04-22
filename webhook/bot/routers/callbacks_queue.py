"""Callback handlers for queue navigation and bulk actions."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import (
    QueuePage, QueueOpen,
    QueueModeToggle, QueueSelToggle, QueueSelAll, QueueSelNone,
    QueueBulkPrompt, QueueBulkConfirm, QueueBulkCancel,
)
from bot.config import get_bot
from bot.middlewares.auth import RoleMiddleware
import query_handlers
import redis_queries
from execution.curation import redis_client as curation_redis
from execution.curation import telegram_poster
from webhook import queue_selection

logger = logging.getLogger(__name__)

callbacks_queue_router = Router(name="callbacks_queue")
callbacks_queue_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


def _current_mode(chat_id: int) -> tuple[str, set[str]]:
    if queue_selection.is_select_mode(chat_id):
        return "select", queue_selection.get_selection(chat_id)
    return "normal", set()


async def _rerender(query: CallbackQuery, page: int = 1) -> None:
    """Re-render the /queue message in place, honoring current mode."""
    chat_id = query.message.chat.id
    mode, selected = _current_mode(chat_id)
    try:
        body, markup = query_handlers.format_queue_page(
            page=page, mode=mode, selected=selected,
        )
    except Exception as exc:
        logger.error(f"queue rerender error: {exc}")
        return
    await get_bot().edit_message_text(
        body,
        chat_id=chat_id,
        message_id=query.message.message_id,
        reply_markup=markup,
    )


# ── Queue navigation ──

@callbacks_queue_router.callback_query(QueuePage.filter())
async def on_queue_page(query: CallbackQuery, callback_data: QueuePage):
    await query.answer("")
    await _rerender(query, page=callback_data.page)


@callbacks_queue_router.callback_query(QueueOpen.filter())
async def on_queue_open(query: CallbackQuery, callback_data: QueueOpen):
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
        await asyncio.to_thread(
            telegram_poster.post_for_curation, chat_id, item, preview_base_url,
        )
    except Exception as exc:
        logger.error(f"queue_open post error: {exc}")
        await query.message.answer("❌ Erro ao abrir card.")


# ── Select mode enter/exit ──

@callbacks_queue_router.callback_query(QueueModeToggle.filter())
async def on_queue_mode(query: CallbackQuery, callback_data: QueueModeToggle):
    chat_id = query.message.chat.id
    if callback_data.action == "enter":
        queue_selection.enter_mode(chat_id)
        await query.answer("Modo seleção ativado")
    else:
        queue_selection.exit_mode(chat_id)
        await query.answer("Saiu do modo seleção")
    await _rerender(query, page=1)
