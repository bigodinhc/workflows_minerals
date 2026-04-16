"""All slash-command handlers.

Public router (no auth middleware): placeholder, /start handled by onboarding.py
Admin router (with RoleMiddleware): everything else
Shared router (admin + subscriber): /settings, /menu
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot.config import get_bot, ANTHROPIC_API_KEY, SHEET_ID, TELEGRAM_WEBHOOK_URL
from bot.states import AddContact, NewsInput
from bot.keyboards import build_main_menu_keyboard
from bot.middlewares.auth import RoleMiddleware
import contact_admin
import query_handlers
from status_builder import build_status_message
from reports_nav import reports_show_types
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

# ── Public router (no auth) ──

public_router = Router(name="commands_public")

# /start is handled by onboarding.py

# ── Admin router (with auth middleware) ──

admin_router = Router(name="commands_admin")
admin_router.message.middleware(RoleMiddleware(allowed_roles={"admin"}))


@admin_router.message(Command("status"))
async def cmd_status(message: Message):
    try:
        body = build_status_message()
    except Exception as exc:
        logger.error(f"/status failed: {exc}")
        body = f"⚠️ Erro ao gerar status: {str(exc)[:100]}"
    await message.answer(body)


@admin_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Cancelado.")


@admin_router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext):
    await state.set_state(AddContact.waiting_data)
    await message.answer(contact_admin.render_add_prompt())


@admin_router.message(Command("list"))
async def cmd_list(message: Message):
    parts = (message.text or "").split(None, 1)
    search = parts[1].strip() if len(parts) > 1 else None
    await _render_list_view(message.chat.id, page=1, search=search)


@admin_router.message(Command("reprocess"))
async def cmd_reprocess(message: Message):
    parts = (message.text or "").split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Uso: `/reprocess <item_id>`\n\n"
            "O item\\_id é o `🆔` mostrado no rodapé dos cards de curadoria.\n"
            "Busca em staging (48h) e depois em archive (7d).",
        )
        return
    await _reprocess_item(message.chat.id, parts[1].strip())


@admin_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(query_handlers.format_help())


@admin_router.message(Command("history"))
async def cmd_history(message: Message):
    try:
        body = query_handlers.format_history()
    except Exception as exc:
        logger.error(f"/history error: {exc}")
        await message.answer("❌ Erro ao consultar arquivo.")
        return
    await message.answer(body)


@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        body = query_handlers.format_stats(today_iso)
    except Exception as exc:
        logger.error(f"/stats error: {exc}")
        await message.answer("❌ Erro ao calcular stats.")
        return
    await message.answer(body)


@admin_router.message(Command("rejections"))
async def cmd_rejections(message: Message):
    try:
        body = query_handlers.format_rejections()
    except Exception as exc:
        logger.error(f"/rejections error: {exc}")
        await message.answer("❌ Erro ao listar recusas.")
        return
    await message.answer(body)


@admin_router.message(Command("queue"))
async def cmd_queue(message: Message):
    try:
        body, markup = query_handlers.format_queue_page(page=1)
    except Exception as exc:
        logger.error(f"/queue error: {exc}")
        await message.answer("❌ Erro ao consultar staging.")
        return
    await message.answer(body, reply_markup=markup)


@admin_router.message(Command("reports"))
async def cmd_reports(message: Message):
    await reports_show_types(message.chat.id)


@admin_router.message(Command("workflows"))
async def cmd_workflows(message: Message):
    from workflow_trigger import render_workflow_list
    wf_text, wf_markup = await render_workflow_list()
    await message.answer(wf_text, reply_markup=wf_markup)


@admin_router.message(Command("s"))
async def cmd_menu(message: Message):
    await message.answer("🥸 *SuperMustache BOT*", reply_markup=build_main_menu_keyboard())


# ── Shared router (admin + subscriber) ──

shared_router = Router(name="commands_shared")
shared_router.message.middleware(RoleMiddleware(allowed_roles={"admin", "subscriber"}))


@shared_router.message(Command("settings"))
async def cmd_settings(message: Message):
    from bot.routers.settings import show_subscription_panel
    await show_subscription_panel(message.chat.id)


@shared_router.message(Command("menu"))
async def cmd_menu_reply(message: Message):
    from bot.keyboards import build_reply_keyboard
    from bot.users import is_admin
    await message.answer("🥸 *SuperMustache BOT*", reply_markup=build_reply_keyboard(is_admin=is_admin(message.chat.id)))


# ── Helpers ──

async def _render_list_view(chat_id, page, search, message_id=None):
    """Fetch contacts and render list message with keyboard."""
    bot = get_bot()
    try:
        sheets = SheetsClient()
        per_page = 10
        contacts, total_pages = await asyncio.to_thread(
            sheets.list_contacts, SHEET_ID, search=search, page=page, per_page=per_page,
        )
        all_contacts, _ = await asyncio.to_thread(
            sheets.list_contacts, SHEET_ID, search=search, page=1, per_page=10_000,
        )
        total = len(all_contacts)

        msg = contact_admin.render_list_message(
            contacts, total=total, page=page, per_page=per_page, search=search,
        )
        kb = contact_admin.build_list_keyboard(
            contacts, page=page, total_pages=total_pages, search=search,
        )

        if message_id is None:
            await bot.send_message(chat_id, msg, reply_markup=kb)
        else:
            await bot.edit_message_text(msg, chat_id=chat_id, message_id=message_id, reply_markup=kb)
    except Exception as e:
        logger.error(f"_render_list_view failed: {e}")
        err_msg = "❌ Erro ao acessar planilha. Tente novamente."
        if message_id:
            await bot.edit_message_text(err_msg, chat_id=chat_id, message_id=message_id)
        else:
            await bot.send_message(chat_id, err_msg)


async def _reprocess_item(chat_id, item_id):
    """Re-run the 3-agent pipeline on a curation item pulled from Redis."""
    from bot.routers._helpers import find_curation_item, run_pipeline_and_archive
    bot = get_bot()
    item = await asyncio.to_thread(find_curation_item, item_id)
    if item is None:
        await bot.send_message(chat_id, f"❌ Item `{item_id}` não encontrado em staging nem archive recente.")
        return
    raw_text = (
        f"Title: {item.get('title', '')}\n"
        f"Date: {item.get('publishDate', '')}\n"
        f"Source: {item.get('source', '')}\n\n"
        f"{item.get('fullText', '')}"
    )
    progress = await bot.send_message(chat_id, f"🖋️ *Reprocessando via Writer*\n🆔 `{item_id}`")
    asyncio.create_task(run_pipeline_and_archive(chat_id, raw_text, progress.message_id, item_id))
