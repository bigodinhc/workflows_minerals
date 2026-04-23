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

from bot.config import ANTHROPIC_API_KEY
from bot.states import AdjustDraft, RejectReason, AddContact, BroadcastMessage, ReprocessItem, WriterInput
from bot.middlewares.auth import RoleMiddleware
from bot.routers._helpers import process_news, process_adjustment
import contact_admin
import redis_queries
from execution.integrations.contacts_repo import (
    ContactsRepo, InvalidPhoneError, ContactAlreadyExistsError,
)

logger = logging.getLogger(__name__)

WELCOME_MESSAGE = (
    "Você foi adicionado à lista de informações de mercado "
    "da Minerals Trading."
)

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


@reply_kb_router.message(F.text == "🖋️ Writer")
async def on_reply_writer(message: Message, state: FSMContext):
    from bot.users import is_admin
    if not is_admin(message.chat.id):
        return
    await state.set_state(WriterInput.waiting_text)
    await message.answer(
        "🖋️ *Writer — 3 agentes IA*\n\n"
        "Cole ou digite o texto que sera processado por:\n"
        "1\\. Writer — redige\n"
        "2\\. Reviewer — revisa\n"
        "3\\. Finalizer — formata\n\n"
        "Use `/cancel` para cancelar.",
    )


@reply_kb_router.message(F.text == "📲 Enviar Msg")
async def on_reply_broadcast(message: Message, state: FSMContext):
    from bot.users import is_admin
    if not is_admin(message.chat.id):
        return
    await state.set_state(BroadcastMessage.waiting_text)
    await message.answer(
        "📲 *Enviar mensagem direta*\n\n"
        "Digite o texto que sera enviado para todos os contatos WhatsApp.\n\n"
        "Use `/cancel` para cancelar.",
    )


@reply_kb_router.message(F.text == "🥸 Admin")
async def on_reply_admin(message: Message):
    from bot.keyboards import build_main_menu_keyboard
    from bot.users import is_admin
    if not is_admin(message.chat.id):
        return
    await message.answer("🥸 *SuperMustache BOT*", reply_markup=build_main_menu_keyboard())


message_router = Router(name="messages")
message_router.message.middleware(RoleMiddleware(allowed_roles={"admin"}))


@message_router.message(BroadcastMessage.waiting_text, F.text)
async def on_broadcast_text(message: Message, state: FSMContext):
    import time as _time
    from bot.callback_data import BroadcastConfirm
    from bot.routers._helpers import drafts_set
    text = (message.text or "").strip()
    if not text:
        await message.answer("❌ Texto vazio. Tente novamente.")
        return

    await state.clear()

    draft_id = f"broadcast_{int(_time.time())}"
    drafts_set(draft_id, {
        "message": text,
        "status": "pending",
        "original_text": text,
        "uazapi_token": None,
        "uazapi_url": None,
    })

    preview = text[:3500] if len(text) > 3500 else text
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Enviar para WhatsApp", "callback_data": BroadcastConfirm(action="send", draft_id=draft_id).pack()},
                {"text": "❌ Cancelar", "callback_data": BroadcastConfirm(action="cancel", draft_id=draft_id).pack()},
            ],
        ],
    }
    await message.answer(
        f"📲 *PREVIEW*\n\n{preview}\n\n"
        f"────────────────────\n"
        f"_{len(text)} caracteres_",
        reply_markup=keyboard,
    )


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

    from webhook.dispatch import send_whatsapp  # local import to avoid cycles

    async def _send_welcome(phone_uazapi: str) -> None:
        result = await send_whatsapp(
            phone_uazapi, WELCOME_MESSAGE, draft_id="welcome",
        )
        if not result.get("ok") and result.get("status") != "duplicate":
            raise RuntimeError(
                f"uazapi send not ok (status={result.get('http_status')})"
            )

    def _sync_send_welcome(phone_uazapi: str) -> None:
        asyncio.run(_send_welcome(phone_uazapi))

    try:
        repo = ContactsRepo()
        contact = await asyncio.to_thread(
            repo.add, name, phone, send_welcome=_sync_send_welcome,
        )
    except InvalidPhoneError as e:
        await message.answer(
            f"❌ Telefone inválido: {e}\n"
            f"Inclua o DDI (ex: 55 Brasil, 1 EUA).",
        )
        await state.clear()
        return
    except ContactAlreadyExistsError as e:
        await message.answer(
            f"❌ Já existe: {e.existing.name} ({e.existing.status})",
        )
        await state.clear()
        return
    except RuntimeError as e:
        await message.answer(
            f"❌ Não consegui enviar mensagem de boas-vindas — "
            f"o número pode não ter WhatsApp.\n\nDetalhe: {str(e)[:200]}",
        )
        await state.clear()
        return
    except Exception as e:
        logger.error(f"add_contact failed: {e}")
        await message.answer("❌ Erro ao adicionar contato. Tente novamente.")
        await state.clear()
        return

    # Success — fetch active count for confirmation.
    try:
        active_contacts = await asyncio.to_thread(repo.list_active)
        active = len(active_contacts)
    except Exception:
        active = "?"

    await message.answer(
        f"✅ {contact.name} adicionado ({contact.phone_uazapi})\n"
        f"Total ativos: {active}",
    )
    await state.clear()


# ── Reprocess input (via "🔁 Reprocessar" button) ──

@message_router.message(ReprocessItem.waiting_id, F.text)
async def on_reprocess_id(message: Message, state: FSMContext):
    """Consume the item_id sent after pressing 🔁 Reprocessar."""
    text = (message.text or "").strip()

    if text.startswith("/"):
        await state.clear()
        return

    if not text:
        await message.answer("❌ Envie o `item_id` ou /cancel.")
        return

    await state.clear()
    from bot.routers.commands import _reprocess_item
    await _reprocess_item(message.chat.id, text)


# ── Writer input (via "🖋️ Writer" button) ──

@message_router.message(WriterInput.waiting_text, F.text)
async def on_writer_text(message: Message, state: FSMContext):
    """Process text through 3-agent pipeline (Writer → Reviewer → Finalizer)."""
    await state.clear()

    if not ANTHROPIC_API_KEY:
        await message.answer("❌ ANTHROPIC\\_API\\_KEY não configurada no servidor.")
        return

    chat_id = message.chat.id
    text = message.text or ""
    logger.info(f"Writer input from chat {chat_id} ({len(text)} chars)")

    progress = await message.answer("⏳ Processando com 3 agentes IA...")
    asyncio.create_task(process_news(chat_id, text, progress.message_id))
