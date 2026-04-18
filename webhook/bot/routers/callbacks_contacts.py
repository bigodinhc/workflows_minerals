"""Callback handlers for contact admin (toggle/list).

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import ContactToggle, ContactPage
from bot.config import SHEET_ID
from bot.middlewares.auth import RoleMiddleware
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

callbacks_contacts_router = Router(name="callbacks_contacts")
callbacks_contacts_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


@callbacks_contacts_router.callback_query(ContactToggle.filter())
async def on_contact_toggle(query: CallbackQuery, callback_data: ContactToggle):
    from bot.routers.commands import _render_list_view
    try:
        sheets = SheetsClient()
        name, new_status = await asyncio.to_thread(sheets.toggle_contact, SHEET_ID, callback_data.phone)
    except ValueError as e:
        await query.answer(f"❌ {str(e)[:100]}")
        return
    except Exception as e:
        logger.error(f"toggle_contact failed: {e}")
        await query.answer("❌ Erro")
        return

    toast = f"✅ {name} ativado" if new_status == "Big" else f"❌ {name} desativado"
    await query.answer(toast)
    await _render_list_view(query.message.chat.id, page=1, search=None, message_id=query.message.message_id)


@callbacks_contacts_router.callback_query(ContactPage.filter())
async def on_contact_page(query: CallbackQuery, callback_data: ContactPage):
    from bot.routers.commands import _render_list_view
    await query.answer("")
    search = callback_data.search if callback_data.search else None
    await _render_list_view(
        query.message.chat.id, page=callback_data.page,
        search=search, message_id=query.message.message_id,
    )
