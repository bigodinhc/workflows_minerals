"""Characterization tests — queue navigation callbacks."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.callback_data import QueuePage, QueueOpen
from bot.routers.callbacks_queue import on_queue_page, on_queue_open


@pytest.mark.asyncio
async def test_on_queue_page_happy_path_edits_message(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
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
