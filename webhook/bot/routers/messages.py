"""FSM text input handlers.

Handles text messages when the user is in a specific FSM state
(adjust feedback, reject reason, add contact, free-form news text).
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from bot.config import ANTHROPIC_API_KEY, SHEET_ID
from bot.states import AdjustDraft, RejectReason, AddContact
from bot.middlewares.auth import RoleMiddleware
from bot.routers._helpers import process_news, process_adjustment
import contact_admin
import redis_queries
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)

# ── Reply keyboard text handler (admin + subscriber) ──

reply_kb_router = Router(name="reply_keyboard")
reply_kb_router.message.middleware(RoleMiddleware(allowed_roles={"admin", "subscriber"}))


@reply_kb_router.message(F.text == "📊 Reports")
async def on_reply_reports(message: Message):
    from reports_nav import reports_show_types
    await reports_show_types(message.chat.id)


@reply_kb_router.message(F.text == "📰 Fila")
async def on_reply_queue(message: Message):
    import query_handlers
    try:
        body, markup = query_handlers.format_queue_page(page=1)
    except Exception:
        await message.answer("❌ Erro ao consultar staging.")
        return
    await message.answer(body, reply_markup=markup)


@reply_kb_router.message(F.text == "⚡ Workflows")
async def on_reply_workflows(message: Message):
    from workflow_trigger import render_workflow_list
    wf_text, wf_markup = await render_workflow_list()
    await message.answer(wf_text, reply_markup=wf_markup)


@reply_kb_router.message(F.text.contains("Settings"))
async def on_reply_settings(message: Message):
    from bot.routers.settings import show_subscription_panel
    await show_subscription_panel(message.chat.id)


message_router = Router(name="messages")
message_router.message.middleware(RoleMiddleware(allowed_roles={"admin"}))


@message_router.message(AdjustDraft.waiting_feedback, F.text)
async def on_adjust_feedback(message: Message, state: FSMContext):
    data = await state.get_data()
    draft_id = data.get("draft_id")
    await state.clear()
    if not draft_id:
        await message.answer("❌ Nenhum draft em ajuste.")
        return
    logger.info(f"Received adjustment feedback for {draft_id}")
    asyncio.create_task(process_adjustment(message.chat.id, draft_id, message.text))


@message_router.message(RejectReason.waiting_reason, F.text)
async def on_reject_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    feedback_key = data.get("feedback_key")
    await state.clear()

    stripped = (message.text or "").strip()
    if stripped.lower() in {"pular", "skip"}:
        await message.answer("✅ Ok, sem razão registrada.")
        return

    if feedback_key:
        try:
            redis_queries.update_feedback_reason(feedback_key, stripped)
        except Exception as exc:
            logger.error(f"update_feedback_reason error: {exc}")
    await message.answer("✅ Razão registrada.")


@message_router.message(AddContact.waiting_data, F.text)
async def on_add_contact_data(message: Message, state: FSMContext):
    text = message.text or ""

    # /cancel while in add flow
    if text.strip().startswith("/"):
        await state.clear()
        return

    try:
        name, phone = contact_admin.parse_add_input(text)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return  # keep state so user can retry

    try:
        sheets = SheetsClient()
        await asyncio.to_thread(sheets.add_contact, SHEET_ID, name, phone)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        await state.clear()
        return
    except Exception as e:
        logger.error(f"add_contact failed: {e}")
        await message.answer("❌ Erro ao gravar na planilha. Tente novamente.")
        await state.clear()
        return

    try:
        sheets = SheetsClient()
        all_contacts, _ = await asyncio.to_thread(
            sheets.list_contacts, SHEET_ID, page=1, per_page=10_000,
        )
        active = sum(1 for c in all_contacts if str(c.get("ButtonPayload", "")).strip() == "Big")
    except Exception:
        active = "?"

    await message.answer(f"✅ {name} adicionado\nTotal ativos: {active}")
    await state.clear()


# ── Free-form news text (no FSM state — catch-all for text) ──

@message_router.message(F.text)
async def on_news_text(message: Message):
    """Process free-form text through the 3-agent pipeline."""
    if not ANTHROPIC_API_KEY:
        await message.answer("❌ ANTHROPIC\\_API\\_KEY não configurada no servidor.")
        return

    chat_id = message.chat.id
    text = message.text or ""
    logger.info(f"New news text from chat {chat_id} ({len(text)} chars)")

    progress = await message.answer("⏳ Processando sua notícia com 3 agentes IA...")
    asyncio.create_task(process_news(chat_id, text, progress.message_id))
