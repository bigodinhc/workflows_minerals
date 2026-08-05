"""Tests for bot.routers.callbacks_history (banco de notícias por data)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from bot.callback_data import HistNav, HistOpen
from bot.routers.callbacks_history import on_hist_nav, on_hist_open


@pytest.mark.asyncio
async def test_hist_nav_edits_message_with_new_page(mock_callback_query, mocker):
    query = mock_callback_query(data="hist_nav:2026-04-14:news")
    query.message.edit_text = AsyncMock()
    fmt = mocker.patch(
        "bot.routers.callbacks_history.query_handlers.format_history_page",
        return_value=("body", {"inline_keyboard": []}),
    )

    await on_hist_nav(query, HistNav(date="2026-04-14", flt="news"))

    fmt.assert_called_once_with("2026-04-14", flt="news")
    query.message.edit_text.assert_awaited_once_with(
        "body", reply_markup={"inline_keyboard": []},
    )


@pytest.mark.asyncio
async def test_hist_open_posts_card_from_supabase(mock_callback_query, mocker, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://bot.example.com")
    query = mock_callback_query(data="hist_open:abc")
    item = {"id": "abc", "title": "T", "fullText": "body", "status": "archived"}
    # ordem: news_repo.get_by_id → item, telegram_poster.post_from_history → None
    to_thread = mocker.patch("asyncio.to_thread", new=AsyncMock(side_effect=[item, None]))

    await on_hist_open(query, HistOpen(item_id="abc"))

    assert to_thread.await_count == 2
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_hist_open_missing_item_answers_error(mock_callback_query, mocker):
    query = mock_callback_query(data="hist_open:gone")
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=None))

    await on_hist_open(query, HistOpen(item_id="gone"))

    query.answer.assert_awaited_with("⚠️ Notícia não encontrada no banco")
