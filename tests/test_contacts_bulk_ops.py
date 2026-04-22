"""Integration tests for the /list bulk activate/deactivate flow."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from webhook.bot.routers.callbacks_contacts import (
    on_bulk_prompt, on_bulk_confirm, on_bulk_cancel,
)
from webhook.bot.callback_data import (
    ContactBulk, ContactBulkConfirm, ContactBulkCancel,
)


@pytest.fixture
def fake_query():
    q = MagicMock()
    q.answer = AsyncMock()
    q.message = MagicMock()
    q.message.chat.id = 123
    q.message.message_id = 456
    q.message.edit_text = AsyncMock()
    return q


@pytest.mark.asyncio
async def test_bulk_prompt_shows_confirmation(fake_query):
    """First tap on [❌ Desativar todos] must show confirmation keyboard."""
    fake_repo = MagicMock()
    fake_repo.list_all.return_value = ([MagicMock() for _ in range(47)], 5)

    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo):
        await on_bulk_prompt(
            fake_query,
            ContactBulk(status="inativo", search=""),
        )

    fake_query.message.edit_text.assert_called_once()
    call = fake_query.message.edit_text.call_args
    text = call.args[0] if call.args else call.kwargs.get("text")
    assert "47" in str(text)
    assert "desativar" in str(text).lower()


@pytest.mark.asyncio
async def test_bulk_confirm_calls_bulk_set_status_and_reports(fake_query):
    fake_repo = MagicMock()
    fake_repo.bulk_set_status.return_value = 47

    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo), \
         patch("bot.routers.commands._render_list_view", new=AsyncMock()):
        await on_bulk_confirm(
            fake_query,
            ContactBulkConfirm(status="inativo", search=""),
        )

    fake_repo.bulk_set_status.assert_called_once_with("inativo", search=None)
    fake_query.answer.assert_awaited()
    toast = fake_query.answer.await_args.args[0]
    assert "47" in toast


@pytest.mark.asyncio
async def test_bulk_confirm_respects_search(fake_query):
    fake_repo = MagicMock()
    fake_repo.bulk_set_status.return_value = 3

    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo), \
         patch("bot.routers.commands._render_list_view", new=AsyncMock()):
        await on_bulk_confirm(
            fake_query,
            ContactBulkConfirm(status="ativo", search="joao"),
        )

    fake_repo.bulk_set_status.assert_called_once_with("ativo", search="joao")


@pytest.mark.asyncio
async def test_bulk_cancel_answers_and_rerenders_list(fake_query):
    with patch("bot.routers.commands._render_list_view",
               new=AsyncMock()) as rerender:
        await on_bulk_cancel(fake_query, ContactBulkCancel())

    fake_query.answer.assert_awaited_with("Cancelado")
    rerender.assert_awaited_once()
