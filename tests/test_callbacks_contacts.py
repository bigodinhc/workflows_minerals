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


# ── Integration: end-to-end _render_list_view with fake Supabase ──

@pytest.mark.asyncio
async def test_render_list_view_with_filter_ativos_only_sends_active_contacts(mocker):
    """Tapping ✅ Ativos must forward status='ativo' to repo and render only active."""
    from bot.routers.commands import _render_list_view

    # Fake repo: records kwargs on list_all, returns different rows per call.
    recorded = []

    def fake_list_all(search=None, page=1, per_page=10, status=None, list_code=None):
        recorded.append({
            "search": search, "page": page, "per_page": per_page,
            "status": status, "list_code": list_code,
        })
        if status == "ativo":
            rows = [_contact("A1", "ativo"), _contact("A2", "ativo")]
        elif status == "inativo":
            rows = [_contact("I1", "inativo")]
        else:
            rows = [_contact("A1", "ativo"), _contact("A2", "ativo"),
                    _contact("I1", "inativo")]
        return rows, 1

    repo_mock = MagicMock()
    repo_mock.list_all = fake_list_all
    mocker.patch("bot.routers.commands.ContactsRepo", return_value=repo_mock)

    bot_mock = AsyncMock()
    bot_mock.send_message = AsyncMock()
    bot_mock.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.commands.get_bot", return_value=bot_mock)

    await _render_list_view(chat_id=100, page=1, search=None, filter="a")

    # Every list_all call must carry status='ativo', list_code=None.
    assert recorded, "repo.list_all should have been called"
    for call in recorded:
        assert call["status"] == "ativo", f"wrong status passed: {call}"
        assert call["list_code"] is None
    # The keyboard built for the sent message must reflect filter='a'
    # (chip row shows bullets around "Ativos").
    bot_mock.send_message.assert_awaited_once()
    kwargs = bot_mock.send_message.await_args.kwargs
    kb = kwargs["reply_markup"]
    chip_row = kb["inline_keyboard"][0]
    ativos_chip = next(b for b in chip_row if b["callback_data"] == "cf:a")
    assert "•" in ativos_chip["text"]  # active indicator


@pytest.mark.asyncio
async def test_render_list_view_with_filter_minerals_restricts_to_list_members(mocker):
    from bot.routers.commands import _render_list_view

    recorded = []

    def fake_list_all(search=None, page=1, per_page=10, status=None, list_code=None):
        recorded.append({"status": status, "list_code": list_code})
        return [_contact("M1", "ativo")], 1

    repo_mock = MagicMock()
    repo_mock.list_all = fake_list_all
    mocker.patch("bot.routers.commands.ContactsRepo", return_value=repo_mock)
    bot_mock = AsyncMock()
    mocker.patch("bot.routers.commands.get_bot", return_value=bot_mock)

    await _render_list_view(chat_id=100, page=1, search=None, filter="mr")

    for call in recorded:
        assert call["list_code"] == "minerals_report"
        assert call["status"] is None
