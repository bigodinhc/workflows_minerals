"""Callback handlers for queue navigation and bulk actions.

Originally extracted from webhook/bot/routers/callbacks.py during Phase 2.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from bot.callback_data import (
    QueuePage, QueueOpen, QueueModeToggle,
    QueueSelToggle, QueueSelAll, QueueSelNone,
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
    try:
        await get_bot().edit_message_text(
            body,
            chat_id=chat_id,
            message_id=query.message.message_id,
            reply_markup=markup,
        )
    except TelegramBadRequest as exc:
        # Most common cause: 'message is not modified' when the new content
        # matches the current. Safe to ignore — the user sees the same state.
        logger.warning(f"queue rerender edit failed: {exc}")


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


# ── Item selection ──

@callbacks_queue_router.callback_query(QueueSelToggle.filter())
async def on_queue_sel_toggle(query: CallbackQuery, callback_data: QueueSelToggle):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    try:
        queue_selection.toggle(chat_id, callback_data.item_id)
    except Exception as exc:
        logger.error(f"queue_sel_toggle redis error: {exc}")
        await query.answer("⚠️ Redis indisponível")
        return
    await query.answer("")
    await _rerender(query, page=1)


@callbacks_queue_router.callback_query(QueueSelAll.filter())
async def on_queue_sel_all(query: CallbackQuery, callback_data: QueueSelAll):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    try:
        items = redis_queries.list_staging(limit=200)
    except Exception as exc:
        logger.error(f"queue_sel_all redis error: {exc}")
        await query.answer("⚠️ Redis indisponível")
        return
    ids = [i["id"] for i in items if i.get("id")]
    queue_selection.select_all(chat_id, ids)
    await query.answer(f"{len(ids)} selecionados")
    await _rerender(query, page=1)


@callbacks_queue_router.callback_query(QueueSelNone.filter())
async def on_queue_sel_none(query: CallbackQuery, callback_data: QueueSelNone):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    queue_selection.clear(chat_id)
    await query.answer("Seleção limpa")
    await _rerender(query, page=1)


# ── Bulk action flow ──

_BULK_ACTION_VERBS = {
    "archive": ("Arquivar", "arquivados"),
    "discard": ("Descartar", "descartados"),
}


def _confirm_markup(action: str) -> dict:
    return {
        "inline_keyboard": [[
            {
                "text": "✅ Sim",
                "callback_data": QueueBulkConfirm(action=action).pack(),
            },
            {
                "text": "❌ Cancelar",
                "callback_data": QueueBulkCancel().pack(),
            },
        ]]
    }


@callbacks_queue_router.callback_query(QueueBulkPrompt.filter())
async def on_queue_bulk_prompt(query: CallbackQuery, callback_data: QueueBulkPrompt):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    selected = queue_selection.get_selection(chat_id)
    if not selected:
        await query.answer("Nada selecionado")
        return
    verb_title, _ = _BULK_ACTION_VERBS[callback_data.action]
    prompt = f"{verb_title} {len(selected)} items?"
    await query.answer("")
    await get_bot().edit_message_text(
        prompt,
        chat_id=chat_id,
        message_id=query.message.message_id,
        reply_markup=_confirm_markup(callback_data.action),
    )


@callbacks_queue_router.callback_query(QueueBulkConfirm.filter())
async def on_queue_bulk_confirm(query: CallbackQuery, callback_data: QueueBulkConfirm):
    chat_id = query.message.chat.id
    if not queue_selection.is_select_mode(chat_id):
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    selected = queue_selection.get_selection(chat_id)
    if not selected:
        await query.answer("Seleção expirou, entre no modo novamente")
        return
    ids = sorted(selected)  # deterministic order for bulk op

    if callback_data.action == "archive":
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            result = await asyncio.to_thread(
                curation_redis.bulk_archive, ids, date, chat_id,
            )
        except Exception as exc:
            logger.error(f"bulk_archive failed: {exc}")
            await query.answer("⚠️ Erro ao arquivar")
            return
        ok = len(result["archived"])
        bad = len(result["failed"])
        if ok and bad:
            toast = f"✅ {ok} arquivados, {bad} falhou (expirado ou já removido)"
        elif ok:
            toast = f"✅ {ok} arquivados"
        else:
            toast = "⚠️ Nenhum item arquivado (todos expiraram ou foram removidos)"
    else:  # discard
        try:
            deleted = await asyncio.to_thread(curation_redis.bulk_discard, ids)
        except Exception as exc:
            logger.error(f"bulk_discard failed: {exc}")
            await query.answer("⚠️ Erro ao descartar")
            return
        toast = f"✅ {int(deleted)} descartados"

    queue_selection.exit_mode(chat_id)
    await query.answer(toast)
    await _rerender(query, page=1)


@callbacks_queue_router.callback_query(QueueBulkCancel.filter())
async def on_queue_bulk_cancel(query: CallbackQuery, callback_data: QueueBulkCancel):
    await query.answer("Cancelado")
    await _rerender(query, page=1)
