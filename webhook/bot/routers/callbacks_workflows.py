"""Callback handlers for workflow triggers + nop.

Extracted from webhook/bot/routers/callbacks.py during Phase 2 router split.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import WorkflowRun, WorkflowList
from bot.config import get_bot
from bot.keyboards import build_main_menu_keyboard
from bot.middlewares.auth import RoleMiddleware

logger = logging.getLogger(__name__)

callbacks_workflows_router = Router(name="callbacks_workflows")
callbacks_workflows_router.callback_query.middleware(RoleMiddleware(allowed_roles={"admin"}))


# ── Workflow actions ──

@callbacks_workflows_router.callback_query(WorkflowRun.filter())
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


@callbacks_workflows_router.callback_query(WorkflowList.filter())
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

@callbacks_workflows_router.callback_query(lambda q: q.data in ("nop", "noop"))
async def on_nop(query: CallbackQuery):
    await query.answer("")
