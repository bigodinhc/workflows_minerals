"""Main-menu switchboard handler.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.callback_data import MenuAction
from bot.middlewares.auth import RoleMiddleware
from bot.states import WriterInput, BroadcastMessage
from reports_nav import reports_show_types
from status_builder import build_status_message
import query_handlers

logger = logging.getLogger(__name__)

callbacks_menu_router = Router(name="callbacks_menu")
callbacks_menu_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


@callbacks_menu_router.callback_query(MenuAction.filter())
async def on_menu_action(query: CallbackQuery, callback_data: MenuAction, state: FSMContext):
    chat_id = query.message.chat.id
    await query.answer("")
    target = callback_data.target

    if target == "reports":
        await reports_show_types(chat_id)
    elif target == "queue":
        try:
            body, markup = query_handlers.format_queue_page(page=1)
            await query.message.answer(body, reply_markup=markup)
        except Exception:
            pass
    elif target == "history":
        try:
            await query.message.answer(query_handlers.format_history())
        except Exception:
            pass
    elif target == "rejections":
        try:
            await query.message.answer(query_handlers.format_rejections())
        except Exception:
            pass
    elif target == "stats":
        try:
            today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            await query.message.answer(query_handlers.format_stats(today_iso))
        except Exception:
            pass
    elif target == "status":
        try:
            await query.message.answer(build_status_message())
        except Exception:
            pass
    elif target == "reprocess":
        await query.message.answer("Uso: `/reprocess <item\\_id>`\n\nDigite o comando com o ID do item.")
    elif target == "list":
        await query.message.answer("Uso: `/list [busca]`\n\nDigite o comando ou `/list` pra ver todos.")
    elif target == "add":
        await query.message.answer("Uso: `/add`\n\nDigite o comando pra iniciar.")
    elif target == "writer":
        await state.set_state(WriterInput.waiting_text)
        await query.message.answer(
            "🖋️ *Writer — 3 agentes IA*\n\n"
            "Cole ou digite o texto que sera processado por:\n"
            "1\\. Writer — redige\n"
            "2\\. Reviewer — revisa\n"
            "3\\. Finalizer — formata\n\n"
            "Use `/cancel` para cancelar.",
        )
    elif target == "broadcast":
        await state.set_state(BroadcastMessage.waiting_text)
        await query.message.answer(
            "📲 *Enviar mensagem direta*\n\n"
            "Digite o texto que sera enviado para todos os contatos WhatsApp.\n\n"
            "Use `/cancel` para cancelar.",
        )
    elif target == "help":
        try:
            await query.message.answer(query_handlers.format_help())
        except Exception:
            pass
