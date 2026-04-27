"""Callback handlers for the OneDrive PDF approval flow."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callback_data import OneDriveApprove, OneDriveConfirm, OneDriveDiscard
from bot.middlewares.auth import RoleMiddleware
from bot.users import format_user_label
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


async def _claim(redis_client, approval_id: str, from_user) -> tuple[str, dict]:
    """Atomic first-click lock.

    Returns one of:
      ("won", claimer_dict)        — this caller now owns the approval
      ("reentrant", claimer_dict)  — same user clicked again, still owns it
      ("lost", existing_claimer)   — another user already owns the approval

    The lock key is `approval:{uuid}:claimed_by`. TTL inherits the
    remaining TTL of `approval:{uuid}` so they expire together.
    """
    approval_ttl = await redis_client.ttl(f"approval:{approval_id}")
    if approval_ttl <= 0:
        approval_ttl = 48 * 3600  # safety fallback if TTL info is missing

    payload = {
        "chat_id": from_user.id,
        "label": format_user_label(from_user),
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }
    ok = await redis_client.set(
        f"approval:{approval_id}:claimed_by",
        json.dumps(payload),
        nx=True,
        ex=approval_ttl,
    )
    if ok:
        return ("won", payload)

    raw = await redis_client.get(f"approval:{approval_id}:claimed_by")
    existing = json.loads(raw) if raw else {}
    if existing.get("chat_id") == from_user.id:
        return ("reentrant", existing)
    return ("lost", existing)


async def _edit_others(
    bot,
    redis_client,
    approval_id: str,
    new_text: str,
    exclude_chat_id: int,
    bus,
) -> None:
    """Cascade-edit every recipient card EXCEPT the clicker's.

    Reads recipients from approval:{uuid}.recipients. Edits in parallel
    via asyncio.gather. Swallows TelegramBadRequest (message gone, bot
    blocked, message not modified) and emits cascade_edit_skipped.
    Logs and emits cascade_edit_failed for unexpected errors. Never raises.

    Uses parse_mode=None deliberately — see spec, Markdown safety section.
    """
    state = await _load_state(redis_client, approval_id)
    if not state:
        return
    recipients = state.get("recipients", []) or []
    targets = [r for r in recipients if r.get("chat_id") != exclude_chat_id]
    if not targets:
        return

    coros = [
        bot.edit_message_text(
            chat_id=r["chat_id"],
            message_id=r["message_id"],
            text=new_text,
            parse_mode=None,
            reply_markup=None,
        )
        for r in targets
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    for r, exc in zip(targets, results):
        if isinstance(exc, TelegramBadRequest):
            bus.emit("cascade_edit_skipped", level="info", detail={
                "target_chat_id": r["chat_id"],
                "reason": str(exc)[:120],
            })
        elif isinstance(exc, Exception):
            bus.emit("cascade_edit_failed", level="warn", detail={
                "target_chat_id": r["chat_id"],
                "error": str(exc)[:200],
                "exc_type": type(exc).__name__,
            })


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

    # Atomic first-click claim
    claim_status, claimer = await _claim(redis_client, callback_data.approval_id, query.from_user)

    if claim_status == "lost":
        bus.emit("approval_clashed", detail={
            "loser_chat_id": query.from_user.id,
            "winner_label": claimer.get("label"),
        })
        await query.answer(
            text=f"Já em decisão por {claimer.get('label', 'outro aprovador')}",
            show_alert=False,
        )
        return

    if claim_status == "won":
        bus.emit("approval_claimed", detail={
            "approval_id": callback_data.approval_id,
            "chat_id": claimer["chat_id"],
            "label": claimer["label"],
        })

    # Existing emission preserved
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

    # Cascade lock to other recipients (only if this was a fresh claim;
    # reentrant means cards are already locked from a prior click).
    if claim_status == "won":
        hhmm = datetime.now(timezone.utc).strftime("%H:%M")
        cascade_text = f"🔒 Sendo decidido por {claimer['label']} às {hhmm}"
        await _edit_others(
            bot=query.bot,
            redis_client=redis_client,
            approval_id=callback_data.approval_id,
            new_text=cascade_text,
            exclude_chat_id=query.from_user.id,
            bus=bus,
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

    # Reentrant claim — same user already owns this approval from on_approve.
    # If a different user somehow reaches on_confirm (shouldn't happen via UI
    # because cascade locked their card), reject defensively.
    bus = EventBus(workflow="onedrive_webhook", trace_id=state.get("trace_id"))
    claim_status, claimer = await _claim(redis_client, callback_data.approval_id, query.from_user)
    if claim_status == "lost":
        bus.emit("approval_clashed", detail={
            "loser_chat_id": query.from_user.id,
            "winner_label": claimer.get("label"),
        })
        await query.answer(
            text=f"Já em decisão por {claimer.get('label', 'outro aprovador')}",
            show_alert=False,
        )
        return

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
        # Cascade failure to other recipients
        cascade_text = (
            f"❌ Decidido por {claimer.get('label', '?')} → {label}\n"
            f"Falha no envio"
        )
        await _edit_others(
            bot=query.bot, redis_client=redis_client,
            approval_id=callback_data.approval_id, new_text=cascade_text,
            exclude_chat_id=query.from_user.id, bus=bus,
        )
        await redis_client.delete(f"approval:{callback_data.approval_id}")
        await redis_client.delete(f"approval:{callback_data.approval_id}:claimed_by")
        return

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

    if result["failed"] and not result["sent"] and result.get("errors"):
        first = result["errors"][0]
        summary += f"\n\n⚠️ Erro: `{first.get('error','')[:200]}`"

    await query.bot.edit_message_text(
        text=summary,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )

    # Cascade final result to other recipients (parse_mode=None — no Markdown)
    cascade_text = (
        f"✏️ Decidido por {claimer.get('label', '?')} → {label}\n"
        f"{icon} {result['sent']}/{total}"
    )
    if result["failed"]:
        cascade_text += f" · {result['failed']} falhas"
    await _edit_others(
        bot=query.bot, redis_client=redis_client,
        approval_id=callback_data.approval_id, new_text=cascade_text,
        exclude_chat_id=query.from_user.id, bus=bus,
    )

    await redis_client.delete(f"approval:{callback_data.approval_id}")
    await redis_client.delete(f"approval:{callback_data.approval_id}:claimed_by")


@callbacks_onedrive_router.callback_query(OneDriveDiscard.filter())
async def on_discard(query: CallbackQuery, callback_data: OneDriveDiscard):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    filename = state.get("filename", "(expirado)") if state else "(expirado)"

    bus = EventBus(workflow="onedrive_webhook", trace_id=(state or {}).get("trace_id"))

    if state:
        # Race-safe claim — discard is also a "decision" that locks the approval
        claim_status, claimer = await _claim(redis_client, callback_data.approval_id, query.from_user)
        if claim_status == "lost":
            bus.emit("approval_clashed", detail={
                "loser_chat_id": query.from_user.id,
                "winner_label": claimer.get("label"),
            })
            await query.answer(
                text=f"Já em decisão por {claimer.get('label', 'outro aprovador')}",
                show_alert=False,
            )
            return

        bus.emit("approval_discarded", detail={"approval_id": callback_data.approval_id})

        # Cascade discard message to other recipients (skip 🔒 — terminal in one step)
        hhmm = datetime.now(timezone.utc).strftime("%H:%M")
        cascade_text = f"❌ Descartado por {claimer['label']} às {hhmm}\n{filename}"
        await _edit_others(
            bot=query.bot, redis_client=redis_client,
            approval_id=callback_data.approval_id, new_text=cascade_text,
            exclude_chat_id=query.from_user.id, bus=bus,
        )

    await redis_client.delete(f"approval:{callback_data.approval_id}")
    await redis_client.delete(f"approval:{callback_data.approval_id}:claimed_by")

    await query.bot.edit_message_text(
        text=f"❌ Descartado às {datetime.now().strftime('%H:%M')}\n`{filename}`",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await query.answer()
