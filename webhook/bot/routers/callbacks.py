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
    CurateAction, DraftAction, MenuAction,
    ReportType, ReportYear, ReportMonth, ReportDownload, ReportBack, ReportYears,
    QueuePage, QueueOpen,
    ContactToggle, ContactPage,
    WorkflowRun, WorkflowList,
    BroadcastConfirm,
)
from bot.states import AdjustDraft, RejectReason
from bot.keyboards import build_main_menu_keyboard, build_approval_keyboard
from bot.middlewares.auth import RoleMiddleware
from bot.routers._helpers import (
    drafts_get, drafts_contains, drafts_update,
    process_adjustment, run_pipeline_and_archive,
)
import contact_admin
import query_handlers
import redis_queries
from status_builder import build_status_message
from reports_nav import (
    reports_show_types, reports_show_latest, reports_show_years,
    reports_show_months, reports_show_month_list, handle_report_download,
)
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

callback_router = Router(name="callbacks")
callback_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# ── Helper ──

async def _finalize_card(query: CallbackQuery, status_text: str):
    """Edit original message to status_text, removing keyboard. Fallback to new message."""
    bot = get_bot()
    message_id = query.message.message_id
    try:
        await bot.edit_message_text(
            status_text, chat_id=query.message.chat.id,
            message_id=message_id, reply_markup=None,
        )
    except Exception:
        plain = status_text.replace("*", "").replace("`", "").replace("_", "")
        await bot.send_message(query.message.chat.id, plain)


# ── Draft actions (adjust) — must be registered BEFORE generic DraftAction ──

@callback_router.callback_query(DraftAction.filter(F.action == "adjust"))
async def on_draft_adjust(query: CallbackQuery, callback_data: DraftAction, state: FSMContext):
    draft = drafts_get(callback_data.draft_id)
    if not draft:
        await query.answer("❌ Draft não encontrado")
        await _finalize_card(query, "❌ *Draft não encontrado*")
        return
    await state.set_state(AdjustDraft.waiting_feedback)
    await state.update_data(draft_id=callback_data.draft_id)
    await query.answer("✏️ Modo ajuste")
    await _finalize_card(query, "✏️ *Em modo ajuste* — envie o feedback na próxima mensagem")
    await query.message.answer(
        "✏️ *MODO AJUSTE*\n\n"
        "Envie uma mensagem descrevendo o que quer ajustar.\n\n"
        "Exemplos:\n"
        "• _Remova o terceiro parágrafo_\n"
        "• _Adicione que o preço subiu 2%_\n"
        "• _Resuma em menos linhas_\n"
        "• _Mude o título para X_",
    )


# ── Draft actions (reject) — must be registered BEFORE generic DraftAction ──

@callback_router.callback_query(DraftAction.filter(F.action == "reject"))
async def on_draft_reject(query: CallbackQuery, callback_data: DraftAction, state: FSMContext):
    chat_id = query.message.chat.id
    draft_id = callback_data.draft_id

    snapshot_title = ""
    draft = drafts_get(draft_id)
    if draft:
        msg = draft.get("message") or ""
        for line in msg.splitlines():
            stripped = line.strip().lstrip("📊").strip()
            if stripped and stripped != "*MINERALS TRADING*":
                snapshot_title = stripped[:80]
                break
        if not snapshot_title:
            snapshot_title = f"Draft {draft_id[:8]}"
    else:
        snapshot_title = f"Draft {draft_id[:8]}"

    if drafts_contains(draft_id):
        drafts_update(draft_id, status="rejected")

    feedback_key = None
    try:
        feedback_key = redis_queries.save_feedback(
            action="draft_reject", item_id=draft_id, chat_id=chat_id, reason="", title=snapshot_title,
        )
    except Exception as exc:
        logger.error(f"draft reject save_feedback error: {exc}")

    # Set FSM state so next text message is captured as reject reason
    await state.set_state(RejectReason.waiting_reason)
    await state.update_data(feedback_key=feedback_key)

    await query.answer("❌ Rejeitado")
    await _finalize_card(
        query,
        f"❌ *Recusado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC\n\n"
        f"💭 Por quê? (opcional — responda ou `pular`)",
    )


# ── Draft actions (approve, test_approve) — generic handler ──

@callback_router.callback_query(DraftAction.filter())
async def on_draft_action(query: CallbackQuery, callback_data: DraftAction):
    chat_id = query.message.chat.id
    action = callback_data.action
    draft_id = callback_data.draft_id

    if action == "approve":
        draft = drafts_get(draft_id)
        if not draft:
            await query.answer("❌ Draft não encontrado")
            await _finalize_card(query, "❌ *DRAFT EXPIRADO*\n\nRode o workflow novamente.")
            return
        if draft["status"] != "pending":
            await query.answer("⚠️ Já processado")
            await _finalize_card(query, f"⚠️ *Já processado* ({draft['status']})")
            return
        drafts_update(draft_id, status="approved")
        await query.answer("✅ Aprovado! Enviando...")
        await _finalize_card(
            query,
            f"✅ *Aprovado* em {datetime.now(timezone.utc).strftime('%H:%M')} UTC — envio em andamento",
        )
        from dispatch import process_approval_async
        asyncio.create_task(
            process_approval_async(chat_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
        )

    elif action == "test_approve":
        draft = drafts_get(draft_id)
        if not draft:
            await query.answer("❌ Draft não encontrado")
            await _finalize_card(query, "❌ *Draft não encontrado*")
            return
        await query.answer("🧪 Enviando teste para 1 contato...")
        await _finalize_card(
            query,
            f"🧪 *Teste em andamento* — {datetime.now(timezone.utc).strftime('%H:%M')} UTC",
        )
        from dispatch import process_test_send_async
        asyncio.create_task(
            process_test_send_async(chat_id, draft_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
        )


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


# ── Curation actions ──

@callback_router.callback_query(CurateAction.filter())
async def on_curate_action(query: CallbackQuery, callback_data: CurateAction, state: FSMContext):
    chat_id = query.message.chat.id
    item_id = callback_data.item_id
    action = callback_data.action

    if action == "archive":
        from execution.curation import redis_client
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            archived = await asyncio.to_thread(redis_client.archive, item_id, date, chat_id=chat_id)
        except Exception as exc:
            logger.error(f"curate_archive redis error: {exc}")
            await query.answer("⚠️ Redis indisponível, tenta de novo")
            return
        if archived is None:
            await query.answer("⚠️ Item expirou ou já processado")
            await _finalize_card(query, "⚠️ Item expirou ou já processado")
            return
        await query.answer("✅ Arquivado")
        await _finalize_card(
            query,
            f"✅ *Arquivado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )

    elif action == "reject":
        from execution.curation import redis_client
        snapshot_title = ""
        try:
            item = redis_client.get_staging(item_id)
            if item:
                snapshot_title = item.get("title") or ""
        except Exception:
            pass
        try:
            await asyncio.to_thread(redis_client.discard, item_id)
        except Exception as exc:
            logger.error(f"curate_reject redis error: {exc}")
            await query.answer("⚠️ Redis indisponível")
            return
        feedback_key = None
        try:
            feedback_key = redis_queries.save_feedback(
                action="curate_reject", item_id=item_id, chat_id=chat_id, reason="", title=snapshot_title,
            )
        except Exception as exc:
            logger.error(f"curate_reject save_feedback error: {exc}")

        # Set FSM state for reject reason collection
        await state.set_state(RejectReason.waiting_reason)
        await state.update_data(feedback_key=feedback_key)

        await query.answer("❌ Recusado")
        await _finalize_card(
            query,
            f"❌ *Recusado*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`\n\n"
            f"💭 Por quê? (opcional — responda ou `pular`)",
        )

    elif action == "pipeline":
        from execution.curation import redis_client
        try:
            item = await asyncio.to_thread(redis_client.get_staging, item_id)
        except Exception as exc:
            logger.error(f"curate_pipeline redis error: {exc}")
            await query.answer("⚠️ Redis indisponível")
            return
        if item is None:
            await query.answer("⚠️ Item expirou")
            await _finalize_card(query, "⚠️ Item expirou ou já processado")
            return
        try:
            redis_queries.mark_pipeline_processed(item_id, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        except Exception as exc:
            logger.warning(f"mark_pipeline_processed failed for {item_id}: {exc}")
        raw_text = (
            f"Title: {item.get('title', '')}\n"
            f"Date: {item.get('publishDate', '')}\n"
            f"Source: {item.get('source', '')}\n\n"
            f"{item.get('fullText', '')}"
        )
        await query.answer("🖋️ Enviando para o Writer...")
        bot = get_bot()
        progress = await bot.send_message(chat_id, f"🖋️ *Enviando para o Writer*\n🆔 `{item_id}`")
        await _finalize_card(
            query,
            f"🖋️ *Enviado para o Writer*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )
        asyncio.create_task(run_pipeline_and_archive(chat_id, raw_text, progress.message_id, item_id))

    elif action == "send_raw":
        from execution.curation import redis_client
        from dispatch import process_approval_async
        try:
            item = await asyncio.to_thread(redis_client.get_staging, item_id)
        except Exception as exc:
            logger.error(f"curate_send_raw redis error: {exc}")
            await query.answer("⚠️ Redis indisponível")
            return
        if item is None:
            await query.answer("⚠️ Item expirou")
            await _finalize_card(query, "⚠️ Item expirou ou já processado")
            return
        raw_text = item.get("fullText", "")
        title = item.get("title", "")
        if not raw_text:
            await query.answer("⚠️ Item sem texto")
            return
        # Archive the item
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            await asyncio.to_thread(redis_client.archive, item_id, date, chat_id=chat_id)
        except Exception as exc:
            logger.warning(f"archive after send_raw failed: {exc}")
        await query.answer("📲 Enviando para WhatsApp...")
        await _finalize_card(
            query,
            f"📲 *Enviado direto para WhatsApp*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC · 🆔 `{item_id}`",
        )
        # Build a simple message with title + text
        message = f"*{title}*\n\n{raw_text}" if title else raw_text
        asyncio.create_task(process_approval_async(chat_id, message))


# ── Broadcast confirm/cancel ──

@callback_router.callback_query(BroadcastConfirm.filter())
async def on_broadcast_confirm(query: CallbackQuery, callback_data: BroadcastConfirm):
    chat_id = query.message.chat.id

    if callback_data.action == "cancel":
        await query.answer("❌ Cancelado")
        await _finalize_card(query, "❌ *Envio cancelado*")
        return

    if callback_data.action == "send":
        draft_id = callback_data.draft_id
        draft = drafts_get(draft_id)
        if not draft:
            await query.answer("❌ Draft expirou")
            await _finalize_card(query, "❌ *Draft expirado*")
            return

        drafts_update(draft_id, status="approved")
        await query.answer("📲 Enviando...")
        await _finalize_card(
            query,
            f"📲 *Enviando para WhatsApp*\n🕒 {datetime.now(timezone.utc).strftime('%H:%M')} UTC",
        )
        from dispatch import process_approval_async
        asyncio.create_task(
            process_approval_async(chat_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
        )


# ── Nop callback ──

@callback_router.callback_query(lambda q: q.data in ("nop", "noop"))
async def on_nop(query: CallbackQuery):
    await query.answer("")
