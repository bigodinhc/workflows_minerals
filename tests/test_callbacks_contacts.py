"""Characterization tests — contact admin callbacks."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from bot.callback_data import ContactToggle, ContactPage
from bot.routers.callbacks_contacts import on_contact_toggle, on_contact_page


@pytest.mark.asyncio
async def test_contact_toggle_activate_shows_ativado_toast(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    # to_thread(sheets.toggle_contact, SHEET_ID, phone) → (name, new_status)
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=("João", "Big")))
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())
    mocker.patch("bot.routers.callbacks_contacts.SheetsClient")

    await on_contact_toggle(query, ContactToggle(phone="+5511999"))

    query.answer.assert_awaited_with("✅ João ativado")
    render.assert_awaited_once()


@pytest.mark.asyncio
async def test_contact_toggle_deactivate_shows_desativado_toast(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=("Maria", "")))
    mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())
    mocker.patch("bot.routers.callbacks_contacts.SheetsClient")

    await on_contact_toggle(query, ContactToggle(phone="+5511888"))

    query.answer.assert_awaited_with("❌ Maria desativado")


@pytest.mark.asyncio
async def test_contact_toggle_value_error_shows_short_error(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch("asyncio.to_thread", new=AsyncMock(side_effect=ValueError("invalid phone")))
    mocker.patch("bot.routers.callbacks_contacts.SheetsClient")

    await on_contact_toggle(query, ContactToggle(phone="bad"))

    query.answer.assert_awaited_with("❌ invalid phone")


@pytest.mark.asyncio
async def test_contact_page_renders_with_search_param(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    render = mocker.patch("bot.routers.commands._render_list_view", new=AsyncMock())

    await on_contact_page(query, ContactPage(page=2, search="joão"))

    query.answer.assert_awaited_with("")
    render.assert_awaited_once_with(100, page=2, search="joão", message_id=200)
