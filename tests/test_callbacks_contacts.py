"""Characterization tests — contact admin callbacks."""
from __future__ import annotations

from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.callback_data import ContactToggle, ContactPage
from bot.routers.callbacks_contacts import on_contact_toggle, on_contact_page
from execution.integrations.contacts_repo import Contact, ContactNotFoundError

_NOW = datetime.now(timezone.utc)


def _contact(name: str, status: str) -> Contact:
    return Contact(
        id="test-id",
        name=name,
        phone_raw="5511999",
        phone_uazapi="5511999",
        status=status,
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.asyncio
async def test_contact_toggle_activate_shows_ativado_toast(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    returned_contact = _contact("João", "ativo")
    mocker.patch(
        "bot.routers.callbacks_contacts.ContactsRepo",
        return_value=MagicMock(),
    )
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=returned_contact))
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())

    await on_contact_toggle(query, ContactToggle(phone="+5511999"))

    query.answer.assert_awaited_with("✅ João ativado")
    render.assert_awaited_once()


@pytest.mark.asyncio
async def test_contact_toggle_deactivate_shows_desativado_toast(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    returned_contact = _contact("Maria", "inativo")
    mocker.patch(
        "bot.routers.callbacks_contacts.ContactsRepo",
        return_value=MagicMock(),
    )
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=returned_contact))
    mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())

    await on_contact_toggle(query, ContactToggle(phone="+5511888"))

    query.answer.assert_awaited_with("❌ Maria desativado")


@pytest.mark.asyncio
async def test_contact_toggle_not_found_shows_short_error(mock_callback_query, mocker):
    """ContactNotFoundError from repo.toggle → handler emits ❌ toast (truncated)."""
    query = mock_callback_query(chat_id=100)
    mocker.patch(
        "bot.routers.callbacks_contacts.ContactsRepo",
        return_value=MagicMock(),
    )
    mocker.patch(
        "asyncio.to_thread",
        new=AsyncMock(side_effect=ContactNotFoundError("no contact with phone bad")),
    )

    await on_contact_toggle(query, ContactToggle(phone="bad"))

    args = query.answer.await_args[0]
    assert args[0].startswith("❌")
    assert "no contact with phone bad" in args[0]


@pytest.mark.asyncio
async def test_contact_page_renders_with_search_param(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())

    await on_contact_page(query, ContactPage(page=2, search="joão"))

    query.answer.assert_awaited_with("")
    render.assert_awaited_once_with(100, page=2, search="joão", message_id=200, filter="t")


@pytest.mark.asyncio
async def test_contact_page_preserves_filter_when_paginating(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())

    await on_contact_page(query, ContactPage(page=3, search="", flt="mr"))

    render.assert_awaited_once_with(100, page=3, search=None, message_id=200, filter="mr")


@pytest.mark.asyncio
async def test_contact_filter_chip_re_renders_with_chosen_filter(mock_callback_query, mocker):
    from bot.callback_data import ContactFilter
    from bot.routers.callbacks_contacts import on_contact_filter

    query = mock_callback_query(chat_id=100, message_id=200)
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())

    await on_contact_filter(query, ContactFilter(value="a"))

    query.answer.assert_awaited_with("")
    render.assert_awaited_once_with(100, page=1, search=None, message_id=200, filter="a")


@pytest.mark.asyncio
async def test_contact_toggle_preserves_filter_on_re_render(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    returned_contact = _contact("João", "ativo")
    mocker.patch(
        "bot.routers.callbacks_contacts.ContactsRepo",
        return_value=MagicMock(),
    )
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=returned_contact))
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())

    await on_contact_toggle(query, ContactToggle(phone="+5511999", flt="i"))

    render.assert_awaited_once_with(
        100, page=1, search=None, message_id=200, filter="i",
    )
