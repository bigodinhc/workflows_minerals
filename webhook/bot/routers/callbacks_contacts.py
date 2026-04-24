"""Callback handlers for contact admin (toggle, bulk activate/deactivate)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import (
    ContactToggle, ContactPage, ContactFilter,
    ContactBulk, ContactBulkConfirm, ContactBulkCancel,
)
from bot.middlewares.auth import RoleMiddleware
from execution.integrations.contacts_repo import (
    ContactsRepo, ContactNotFoundError,
)

logger = logging.getLogger(__name__)

callbacks_contacts_router = Router(name="callbacks_contacts")
callbacks_contacts_router.callback_query.middleware(
    RoleMiddleware(allowed_roles={"admin"})
)


# ── Toggle ──

@callbacks_contacts_router.callback_query(ContactToggle.filter())
async def on_contact_toggle(query: CallbackQuery, callback_data: ContactToggle):
    # Local import avoids circular dep with commands.py.
    from bot.routers.commands import _render_list_view
    try:
        repo = ContactsRepo()
        contact = await asyncio.to_thread(repo.toggle, callback_data.phone)
    except ContactNotFoundError as e:
        await query.answer(f"❌ {str(e)[:100]}")
        return
    except Exception as e:
        logger.error(f"toggle_contact failed: {e}")
        await query.answer("❌ Erro")
        return

    toast = (
        f"✅ {contact.name} ativado" if contact.is_active()
        else f"❌ {contact.name} desativado"
    )
    await query.answer(toast)
    await _render_list_view(
        query.message.chat.id, page=1, search=None,
        message_id=query.message.message_id, filter=callback_data.flt,
    )


# ── Pagination ──

@callbacks_contacts_router.callback_query(ContactPage.filter())
async def on_contact_page(query: CallbackQuery, callback_data: ContactPage):
    from bot.routers.commands import _render_list_view
    await query.answer("")
    search = callback_data.search if callback_data.search else None
    await _render_list_view(
        query.message.chat.id, page=callback_data.page,
        search=search, message_id=query.message.message_id,
        filter=callback_data.flt,
    )


# ── Filter chip tap ──

@callbacks_contacts_router.callback_query(ContactFilter.filter())
async def on_contact_filter(query: CallbackQuery, callback_data: ContactFilter):
    from bot.routers.commands import _render_list_view
    await query.answer("")
    await _render_list_view(
        query.message.chat.id, page=1, search=None,
        message_id=query.message.message_id, filter=callback_data.value,
    )


# ── Bulk: first tap (show confirmation) ──

@callbacks_contacts_router.callback_query(ContactBulk.filter())
async def on_bulk_prompt(query: CallbackQuery, callback_data: ContactBulk):
    """First tap — count how many contacts match and show confirmation."""
    search = callback_data.search if callback_data.search else None
    flt = callback_data.flt
    repo_kwargs = _flt_to_repo_kwargs(flt)
    try:
        repo = ContactsRepo()
        all_rows, _ = await asyncio.to_thread(
            repo.list_all, search=search, page=1, per_page=10_000, **repo_kwargs,
        )
    except Exception as e:
        logger.error(f"bulk count failed: {e}")
        await query.answer("❌ Erro")
        return

    count = len(all_rows)
    verb = "ativar" if callback_data.status == "ativo" else "desativar"
    scope_parts = []
    if search:
        scope_parts.append(f'busca: "{search}"')
    if flt and flt != "t":
        from contact_admin import FILTER_LABELS
        scope_parts.append(f"filtro: {FILTER_LABELS.get(flt, flt)}")
    scope = f" ({', '.join(scope_parts)})" if scope_parts else ""
    prompt = f"Confirma {verb} {count} contatos{scope}?"

    confirm_kb = {
        "inline_keyboard": [[
            {
                "text": "✅ Sim",
                "callback_data": ContactBulkConfirm(
                    status=callback_data.status,
                    search=callback_data.search,
                    flt=flt,
                ).pack(),
            },
            {
                "text": "❌ Cancelar",
                "callback_data": ContactBulkCancel().pack(),
            },
        ]]
    }

    await query.answer("")
    await query.message.edit_text(prompt, reply_markup=confirm_kb)


# ── Bulk: second tap (execute) ──

@callbacks_contacts_router.callback_query(ContactBulkConfirm.filter())
async def on_bulk_confirm(query: CallbackQuery, callback_data: ContactBulkConfirm):
    from bot.routers.commands import _render_list_view
    search = callback_data.search if callback_data.search else None
    try:
        repo = ContactsRepo()
        count = await asyncio.to_thread(
            repo.bulk_set_status, callback_data.status, search=search,
        )
    except Exception as e:
        logger.error(f"bulk_set_status failed: {e}")
        await query.answer("❌ Erro")
        return

    verb = "ativados" if callback_data.status == "ativo" else "desativados"
    await query.answer(f"✅ {count} contatos {verb}")
    await _render_list_view(
        query.message.chat.id, page=1, search=search,
        message_id=query.message.message_id, filter=callback_data.flt,
    )


# ── Bulk: cancel ──

@callbacks_contacts_router.callback_query(ContactBulkCancel.filter())
async def on_bulk_cancel(query: CallbackQuery, callback_data: ContactBulkCancel):
    from bot.routers.commands import _render_list_view
    await query.answer("Cancelado")
    await _render_list_view(
        query.message.chat.id, page=1, search=None,
        message_id=query.message.message_id,
    )


def _flt_to_repo_kwargs(flt: str) -> dict:
    """Translate short filter code → ContactsRepo.list_all kwargs."""
    from bot.routers.commands import _FILTER_TO_REPO
    return _FILTER_TO_REPO.get(flt, _FILTER_TO_REPO["t"])
