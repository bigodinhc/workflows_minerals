"""Callback handlers for the OneDrive PDF approval flow."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callback_data import OneDriveApprove, OneDriveConfirm, OneDriveDiscard
from bot.middlewares.auth import RoleMiddleware
from execution.core.event_bus import EventBus
from execution.integrations.contacts_repo import ContactsRepo

from dispatch_document import dispatch_document, ALL_CODE


logger = logging.getLogger(__name__)

callbacks_onedrive_router = Router(name="callbacks_onedrive")
callbacks_onedrive_router.callback_query.middleware(
    RoleMiddleware(allowed_roles={"admin"})
)


def _redis():
    from redis.asyncio import Redis
    return Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


async def _load_state(redis_client, approval_id: str):
    raw = await redis_client.get(f"approval:{approval_id}")
    return json.loads(raw) if raw else None


async def _save_state(redis_client, approval_id: str, state: dict) -> None:
    await redis_client.set(
        f"approval:{approval_id}",
        json.dumps(state),
        keepttl=True,
    )


def _list_label(list_code: str, contacts_repo: ContactsRepo):
    if list_code == ALL_CODE:
        return "Todos", len(contacts_repo.list_active())
    for lst in contacts_repo.list_lists():
        if lst.code == list_code:
            return lst.label, lst.member_count
    return list_code, 0


@callbacks_onedrive_router.callback_query(OneDriveApprove.filter())
async def on_approve(query: CallbackQuery, callback_data: OneDriveApprove):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    if not state:
        await query.answer(text="⚠️ Aprovação expirada", show_alert=True)
        return

    bus = EventBus(workflow="onedrive_webhook", trace_id=state.get("trace_id"))
    bus.emit("approval_clicked", detail={
        "approval_id": callback_data.approval_id,
        "list_code": callback_data.list_code,
    })

    contacts_repo = ContactsRepo()
    label, count = _list_label(callback_data.list_code, contacts_repo)

    state = {**state, "status": "awaiting_confirm"}
    await _save_state(redis_client, callback_data.approval_id, state)

    text = (
        f"⚠️ *Confirmar envio?*\n\n"
        f"`{state['filename']}`\n"
        f"→ {label} ({count} contatos)"
    )
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Enviar",
        callback_data=OneDriveConfirm(
            approval_id=callback_data.approval_id,
            list_code=callback_data.list_code,
        ).pack(),
    )
    kb.button(
        text="◀ Voltar",
        callback_data=OneDriveDiscard(approval_id=callback_data.approval_id).pack(),
    )
    kb.adjust(2)

    await query.bot.edit_message_text(
        text=text,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )
    await query.answer()


@callbacks_onedrive_router.callback_query(OneDriveConfirm.filter())
async def on_confirm(query: CallbackQuery, callback_data: OneDriveConfirm):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    if not state:
        await query.answer(text="⚠️ Aprovação expirada", show_alert=True)
        return
    if state.get("status") == "dispatching":
        await query.answer(text="Já em andamento…", show_alert=True)
        return

    bus = EventBus(workflow="onedrive_webhook", trace_id=state.get("trace_id"))
    bus.emit("approval_approved", detail={
        "approval_id": callback_data.approval_id,
        "list_code": callback_data.list_code,
    })

    state = {**state, "status": "dispatching"}
    await _save_state(redis_client, callback_data.approval_id, state)

    contacts_repo = ContactsRepo()
    label, _ = _list_label(callback_data.list_code, contacts_repo)

    await query.bot.edit_message_text(
        text=f"📤 Enviando *{state['filename']}* → {label}…",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await query.answer()

    try:
        result = await dispatch_document(
            approval_id=callback_data.approval_id,
            list_code=callback_data.list_code,
        )
    except Exception as exc:
        logger.exception("dispatch_document failed")
        bus.emit("dispatch_failed", level="error", detail={
            "approval_id": callback_data.approval_id,
            "error": str(exc)[:200],
        })
        await query.bot.edit_message_text(
            text=f"❌ Falha no envio: {type(exc).__name__}: {str(exc)[:200]}",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            parse_mode=None,
        )
        return

    # (dispatch_completed is emitted inside dispatch_document; no duplicate emit here)

    total = result["sent"] + result["failed"] + result["skipped"]
    if result["failed"] and not result["sent"]:
        icon = "❌"
        header = "Falhou"
    elif result["failed"]:
        icon = "⚠️"
        header = "Parcial"
    else:
        icon = "✅"
        header = "Enviado"
    summary = (
        f"{icon} *{header}* — {state['filename']}\n"
        f"Lista: {label}\n"
        f"{result['sent']}/{total} sucesso"
    )
    if result["failed"]:
        summary += f" · {result['failed']} falhas"
    if result["skipped"]:
        summary += f" · {result['skipped']} já enviados antes"

    # Surface first error reason if everything failed
    if result["failed"] and not result["sent"] and result.get("errors"):
        first = result["errors"][0]
        summary += f"\n\n⚠️ Erro: `{first.get('error','')[:200]}`"

    await query.bot.edit_message_text(
        text=summary,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await redis_client.delete(f"approval:{callback_data.approval_id}")


@callbacks_onedrive_router.callback_query(OneDriveDiscard.filter())
async def on_discard(query: CallbackQuery, callback_data: OneDriveDiscard):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    filename = state.get("filename", "(expirado)") if state else "(expirado)"

    if state:
        bus = EventBus(workflow="onedrive_webhook", trace_id=state.get("trace_id"))
        bus.emit("approval_discarded", detail={"approval_id": callback_data.approval_id})

    await redis_client.delete(f"approval:{callback_data.approval_id}")

    await query.bot.edit_message_text(
        text=f"❌ Descartado às {datetime.now().strftime('%H:%M')}\n`{filename}`",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await query.answer()
