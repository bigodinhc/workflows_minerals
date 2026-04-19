"""Callback handlers for curation domain: drafts, curation items, broadcast confirm.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.config import get_bot
from bot.callback_data import CurateAction, DraftAction, BroadcastConfirm
from bot.states import AdjustDraft, RejectReason
from bot.middlewares.auth import RoleMiddleware
from bot.routers._helpers import (
    drafts_get, drafts_contains, drafts_update,
    run_pipeline_and_archive,
)
import redis_queries
from dispatch import process_approval_async, process_test_send_async
from execution.curation import redis_client

logger = logging.getLogger(__name__)

callbacks_curation_router = Router(name="callbacks_curation")
callbacks_curation_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


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

@callbacks_curation_router.callback_query(DraftAction.filter(F.action == "adjust"))
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

@callbacks_curation_router.callback_query(DraftAction.filter(F.action == "reject"))
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

@callbacks_curation_router.callback_query(DraftAction.filter())
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
        asyncio.create_task(
            process_approval_async(chat_id, draft["message"], draft_id, draft.get("uazapi_token"), draft.get("uazapi_url"))
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
        asyncio.create_task(
            process_test_send_async(chat_id, draft_id, draft["message"], draft.get("uazapi_token"), draft.get("uazapi_url"))
        )


# ── Curation actions ──

@callbacks_curation_router.callback_query(CurateAction.filter())
async def on_curate_action(query: CallbackQuery, callback_data: CurateAction, state: FSMContext):
    chat_id = query.message.chat.id
    item_id = callback_data.item_id
    action = callback_data.action

    if action == "archive":
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
        asyncio.create_task(process_approval_async(chat_id, message, item_id))


# ── Broadcast confirm/cancel ──

@callbacks_curation_router.callback_query(BroadcastConfirm.filter())
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
        asyncio.create_task(
            process_approval_async(chat_id, draft["message"], draft_id, draft.get("uazapi_token"), draft.get("uazapi_url"))
        )
