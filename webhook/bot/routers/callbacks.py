"""All callback query handlers.

Replaces callback_router.py with Aiogram CallbackData-filtered handlers.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.config import get_bot, SHEET_ID
from bot.callback_data import (
    MenuAction,
    ReportType, ReportYear, ReportMonth, ReportDownload, ReportBack, ReportYears,
    QueuePage, QueueOpen,
    ContactToggle, ContactPage,
    WorkflowRun, WorkflowList,
)
from bot.keyboards import build_main_menu_keyboard, build_approval_keyboard
from bot.middlewares.auth import RoleMiddleware
import contact_admin
import query_handlers
from status_builder import build_status_message
from reports_nav import (
    reports_show_types, reports_show_latest, reports_show_years,
    reports_show_months, reports_show_month_list, handle_report_download,
)
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

callback_router = Router(name="callbacks")
callback_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# ── Menu actions ──

@callback_router.callback_query(MenuAction.filter())
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
        from bot.states import WriterInput
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
        from bot.states import BroadcastMessage
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


# ── Report navigation ──

@callback_router.callback_query(ReportType.filter())
async def on_report_type(query: CallbackQuery, callback_data: ReportType):
    await query.answer("")
    await reports_show_latest(query.message.chat.id, query.message.message_id, callback_data.report_type)


@callback_router.callback_query(ReportYears.filter())
async def on_report_years(query: CallbackQuery, callback_data: ReportYears):
    await query.answer("")
    await reports_show_years(query.message.chat.id, query.message.message_id, callback_data.report_type)


@callback_router.callback_query(ReportYear.filter())
async def on_report_year(query: CallbackQuery, callback_data: ReportYear):
    await query.answer("")
    await reports_show_months(query.message.chat.id, query.message.message_id, callback_data.report_type, callback_data.year)


@callback_router.callback_query(ReportMonth.filter())
async def on_report_month(query: CallbackQuery, callback_data: ReportMonth):
    await query.answer("")
    await reports_show_month_list(
        query.message.chat.id, query.message.message_id,
        callback_data.report_type, callback_data.year, callback_data.month,
    )


@callback_router.callback_query(ReportDownload.filter())
async def on_report_download(query: CallbackQuery, callback_data: ReportDownload):
    ok, msg = await handle_report_download(query.message.chat.id, query.id, callback_data.report_id)
    await query.answer(f"📤 {msg}" if ok else msg)


@callback_router.callback_query(ReportBack.filter())
async def on_report_back(query: CallbackQuery, callback_data: ReportBack):
    await query.answer("")
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    target = callback_data.target
    if target == "types":
        await reports_show_types(chat_id, message_id=message_id)
    elif target.startswith("type:"):
        report_type = target[len("type:"):]
        await reports_show_latest(chat_id, message_id, report_type)
    elif target.startswith("years:"):
        report_type = target[len("years:"):]
        await reports_show_years(chat_id, message_id, report_type)
    elif target.startswith("year:"):
        parts = target[len("year:"):].rsplit(":", 1)
        if len(parts) == 2:
            await reports_show_months(chat_id, message_id, parts[0], int(parts[1]))


# ── Queue navigation ──

@callback_router.callback_query(QueuePage.filter())
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


@callback_router.callback_query(QueueOpen.filter())
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


# ── Contact admin ──

@callback_router.callback_query(ContactToggle.filter())
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


@callback_router.callback_query(ContactPage.filter())
async def on_contact_page(query: CallbackQuery, callback_data: ContactPage):
    from bot.routers.commands import _render_list_view
    await query.answer("")
    search = callback_data.search if callback_data.search else None
    await _render_list_view(
        query.message.chat.id, page=callback_data.page,
        search=search, message_id=query.message.message_id,
    )


# ── Workflow actions ──

@callback_router.callback_query(WorkflowRun.filter())
async def on_workflow_run(query: CallbackQuery, callback_data: WorkflowRun):
    from workflow_trigger import trigger_workflow, find_triggered_run, poll_and_update, _workflow_name_by_id
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    workflow_id = callback_data.workflow_id
    name = _workflow_name_by_id(workflow_id)

    await query.answer(f"Disparando {name}...")
    bot = get_bot()
    await bot.edit_message_text(
        f"🚀 *Disparando {name}...*",
        chat_id=chat_id, message_id=message_id,
        reply_markup={"inline_keyboard": [[{"text": "⬅ Cancelar", "callback_data": WorkflowList(action="list").pack()}]]},
    )

    ok, error = await trigger_workflow(workflow_id)
    if not ok:
        await bot.edit_message_text(
            f"❌ *{name}* — erro ao disparar\n\n`{error}`",
            chat_id=chat_id, message_id=message_id,
            reply_markup={"inline_keyboard": [
                [{"text": "🔄 Tentar novamente", "callback_data": WorkflowRun(workflow_id=workflow_id).pack()}],
                [{"text": "⬅ Workflows", "callback_data": WorkflowList(action="list").pack()}],
            ]},
        )
        return

    await bot.edit_message_text(
        f"🔄 *{name}* rodando...\n\nAguardando conclusao.",
        chat_id=chat_id, message_id=message_id,
    )

    async def _track():
        run_id = await find_triggered_run(workflow_id)
        if run_id is None:
            await bot.edit_message_text(
                f"⚠️ *{name}* — disparado mas nao encontrei o run\n\nVerifique no GitHub.",
                chat_id=chat_id, message_id=message_id,
                reply_markup={"inline_keyboard": [[{"text": "⬅ Workflows", "callback_data": WorkflowList(action="list").pack()}]]},
            )
            return
        await poll_and_update(chat_id, message_id, workflow_id, run_id)

    asyncio.create_task(_track())


@callback_router.callback_query(WorkflowList.filter())
async def on_workflow_list(query: CallbackQuery, callback_data: WorkflowList):
    await query.answer("")
    bot = get_bot()

    if callback_data.action == "list":
        from workflow_trigger import render_workflow_list
        text, markup = await render_workflow_list()
        await bot.edit_message_text(
            text, chat_id=query.message.chat.id,
            message_id=query.message.message_id, reply_markup=markup,
        )
    elif callback_data.action == "back_menu":
        await query.message.answer("🥸 *SuperMustache BOT*", reply_markup=build_main_menu_keyboard())


# ── Nop callback ──

@callback_router.callback_query(lambda q: q.data in ("nop", "noop"))
async def on_nop(query: CallbackQuery):
    await query.answer("")
