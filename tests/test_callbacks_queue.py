"""Characterization tests — queue navigation callbacks."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.callback_data import QueuePage, QueueOpen
from bot.routers.callbacks_queue import on_queue_page, on_queue_open


@pytest.mark.asyncio
async def test_on_queue_page_happy_path_edits_message(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("queue body text", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_page(query, QueuePage(page=2))

    bot.edit_message_text.assert_awaited_once()
    kwargs = bot.edit_message_text.await_args.kwargs
    assert kwargs["chat_id"] == 100
    assert kwargs["message_id"] == 200


@pytest.mark.asyncio
async def test_on_queue_page_format_error_returns_silently(mock_callback_query, mocker):
    query = mock_callback_query()
    mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        side_effect=RuntimeError("boom"),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    # Must not raise
    await on_queue_page(query, QueuePage(page=1))

    bot.edit_message_text.assert_not_called()


@pytest.mark.asyncio
async def test_on_queue_open_happy_path_posts_for_curation(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    item = {"id": "item1", "title": "T", "fullText": "body"}
    mocker.patch("execution.curation.redis_client.get_staging", return_value=item)
    to_thread = mocker.patch("asyncio.to_thread", new=AsyncMock())

    await on_queue_open(query, QueueOpen(item_id="item1"))

    query.answer.assert_awaited_with("")
    to_thread.assert_awaited_once()  # post_for_curation scheduled via to_thread


@pytest.mark.asyncio
async def test_on_queue_open_item_expired_answers_warning(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch("execution.curation.redis_client.get_staging", return_value=None)

    await on_queue_open(query, QueueOpen(item_id="gone"))

    query.answer.assert_awaited_with("⚠️ Item expirou")


@pytest.mark.asyncio
async def test_on_queue_open_redis_error_answers_warning(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch(
        "execution.curation.redis_client.get_staging",
        side_effect=RuntimeError("redis down"),
    )

    await on_queue_open(query, QueueOpen(item_id="x"))

    query.answer.assert_awaited_with("⚠️ Redis indisponível")


@pytest.mark.asyncio
async def test_on_queue_mode_enter_activates_select_mode(mock_callback_query, mocker):
    from bot.callback_data import QueueModeToggle
    from bot.routers.callbacks_queue import on_queue_mode

    enter_mock = mocker.patch("webhook.queue_selection.enter_mode")
    mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42, message_id=99)
    await on_queue_mode(query, QueueModeToggle(action="enter"))

    enter_mock.assert_called_once_with(42)
    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_queue_mode_exit_clears_state(mock_callback_query, mocker):
    from bot.callback_data import QueueModeToggle
    from bot.routers.callbacks_queue import on_queue_mode

    exit_mock = mocker.patch("webhook.queue_selection.exit_mode")
    mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42)
    await on_queue_mode(query, QueueModeToggle(action="exit"))

    exit_mock.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_on_queue_page_uses_select_mode_when_active(mock_callback_query, mocker):
    from bot.callback_data import QueuePage
    from bot.routers.callbacks_queue import on_queue_page

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a"})
    format_mock = mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_page(mock_callback_query(chat_id=42), QueuePage(page=2))

    kwargs = format_mock.call_args.kwargs
    assert kwargs["mode"] == "select"
    assert kwargs["selected"] == {"a"}


@pytest.mark.asyncio
async def test_on_queue_page_uses_normal_mode_when_inactive(mock_callback_query, mocker):
    from bot.callback_data import QueuePage
    from bot.routers.callbacks_queue import on_queue_page

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)
    format_mock = mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_page(mock_callback_query(chat_id=42), QueuePage(page=1))

    kwargs = format_mock.call_args.kwargs
    assert kwargs["mode"] == "normal"


@pytest.mark.asyncio
async def test_on_queue_sel_toggle_adds_and_rerenders(mock_callback_query, mocker):
    from bot.callback_data import QueueSelToggle
    from bot.routers.callbacks_queue import on_queue_sel_toggle

    toggle_mock = mocker.patch("webhook.queue_selection.toggle", return_value=True)
    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"abc"})
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_sel_toggle(
        mock_callback_query(chat_id=42), QueueSelToggle(item_id="abc"),
    )

    toggle_mock.assert_called_once_with(42, "abc")
    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_queue_sel_toggle_not_in_select_mode_toasts(mock_callback_query, mocker):
    from bot.callback_data import QueueSelToggle
    from bot.routers.callbacks_queue import on_queue_sel_toggle

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=False)

    query = mock_callback_query(chat_id=42)
    await on_queue_sel_toggle(query, QueueSelToggle(item_id="abc"))

    query.answer.assert_awaited_with("Seleção expirou, entre no modo novamente")


@pytest.mark.asyncio
async def test_on_queue_sel_all_selects_every_staging_id(mock_callback_query, mocker):
    from bot.callback_data import QueueSelAll
    from bot.routers.callbacks_queue import on_queue_sel_all

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b"})
    mocker.patch(
        "redis_queries.list_staging",
        return_value=[{"id": "a"}, {"id": "b"}],
    )
    select_all_mock = mocker.patch("webhook.queue_selection.select_all")
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_sel_all(mock_callback_query(chat_id=42), QueueSelAll())

    select_all_mock.assert_called_once()
    args = select_all_mock.call_args.args
    assert args[0] == 42
    assert sorted(args[1]) == ["a", "b"]


@pytest.mark.asyncio
async def test_on_queue_sel_none_clears_selection(mock_callback_query, mocker):
    from bot.callback_data import QueueSelNone
    from bot.routers.callbacks_queue import on_queue_sel_none

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value=set())
    clear_mock = mocker.patch("webhook.queue_selection.clear")
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    await on_queue_sel_none(mock_callback_query(chat_id=42), QueueSelNone())

    clear_mock.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_on_queue_bulk_prompt_empty_selection_toasts(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkPrompt
    from bot.routers.callbacks_queue import on_queue_bulk_prompt

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value=set())

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_prompt(query, QueueBulkPrompt(action="archive"))

    query.answer.assert_awaited_with("Nada selecionado")


@pytest.mark.asyncio
async def test_on_queue_bulk_prompt_archive_shows_confirmation(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkPrompt
    from bot.routers.callbacks_queue import on_queue_bulk_prompt

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b", "c"})
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42, message_id=99)
    await on_queue_bulk_prompt(query, QueueBulkPrompt(action="archive"))

    bot.edit_message_text.assert_awaited_once()
    call = bot.edit_message_text.await_args
    assert "Arquivar 3 items?" in call.args[0]
    markup = call.kwargs["reply_markup"]
    texts = [b["text"] for row in markup["inline_keyboard"] for b in row]
    assert "✅ Sim" in texts
    assert "❌ Cancelar" in texts


@pytest.mark.asyncio
async def test_on_queue_bulk_prompt_discard_shows_confirmation(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkPrompt
    from bot.routers.callbacks_queue import on_queue_bulk_prompt

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a"})
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_prompt(query, QueueBulkPrompt(action="discard"))

    call = bot.edit_message_text.await_args
    assert "Descartar 1 item?" in call.args[0]


@pytest.mark.asyncio
async def test_on_queue_bulk_confirm_archive_executes_then_exits(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkConfirm
    from bot.routers.callbacks_queue import on_queue_bulk_confirm

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b"})
    exit_mock = mocker.patch("webhook.queue_selection.exit_mode")
    to_thread = mocker.patch(
        "asyncio.to_thread",
        new=AsyncMock(return_value={"archived": ["a", "b"], "failed": []}),
    )
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_confirm(query, QueueBulkConfirm(action="archive"))

    to_thread.assert_awaited_once()
    query.answer.assert_awaited_with("✅ 2 arquivados")
    exit_mock.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_on_queue_bulk_confirm_archive_partial_reports_both_counts(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkConfirm
    from bot.routers.callbacks_queue import on_queue_bulk_confirm

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b", "c"})
    mocker.patch("webhook.queue_selection.exit_mode")
    mocker.patch(
        "asyncio.to_thread",
        new=AsyncMock(return_value={"archived": ["a", "b"], "failed": ["c"]}),
    )
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=AsyncMock())

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_confirm(query, QueueBulkConfirm(action="archive"))

    query.answer.assert_awaited_with("✅ 2 arquivados, 1 falhou (expirado ou já removido)")


@pytest.mark.asyncio
async def test_on_queue_bulk_confirm_discard_executes(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkConfirm
    from bot.routers.callbacks_queue import on_queue_bulk_confirm

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a", "b"})
    mocker.patch("webhook.queue_selection.exit_mode")
    to_thread = mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=2))
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=AsyncMock())

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_confirm(query, QueueBulkConfirm(action="discard"))

    to_thread.assert_awaited_once()
    query.answer.assert_awaited_with("✅ 2 descartados")


@pytest.mark.asyncio
async def test_on_queue_bulk_confirm_empty_selection_toasts(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkConfirm
    from bot.routers.callbacks_queue import on_queue_bulk_confirm

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value=set())

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_confirm(query, QueueBulkConfirm(action="archive"))

    query.answer.assert_awaited_with("Seleção expirou, entre no modo novamente")


@pytest.mark.asyncio
async def test_on_queue_bulk_cancel_rerenders_select_mode(mock_callback_query, mocker):
    from bot.callback_data import QueueBulkCancel
    from bot.routers.callbacks_queue import on_queue_bulk_cancel

    mocker.patch("webhook.queue_selection.is_select_mode", return_value=True)
    mocker.patch("webhook.queue_selection.get_selection", return_value={"a"})
    mocker.patch(
        "bot.routers.callbacks_queue.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks_queue.get_bot", return_value=bot)

    query = mock_callback_query(chat_id=42)
    await on_queue_bulk_cancel(query, QueueBulkCancel())

    query.answer.assert_awaited_with("Cancelado")
    bot.edit_message_text.assert_awaited_once()
