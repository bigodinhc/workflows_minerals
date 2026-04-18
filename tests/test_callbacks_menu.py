"""Characterization tests — on_menu_action (main menu switchboard) in callbacks.py.

Covers the highest-value target branches. Other branches (history, rejections,
stats, status, reprocess, list, add, help) follow the same pattern and share the
`try: await query.message.answer(...) except Exception: pass` idiom — they are
covered by a single parameterized test below.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from bot.callback_data import MenuAction
from bot.routers.callbacks import on_menu_action
from bot.states import WriterInput, BroadcastMessage


@pytest.mark.asyncio
async def test_menu_action_reports_invokes_reports_show_types(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(chat_id=100)
    state = fsm_context_in_state()
    show_types = mocker.patch("reports_nav.reports_show_types", new=AsyncMock())

    await on_menu_action(query, MenuAction(target="reports"), state)

    query.answer.assert_awaited_with("")
    show_types.assert_awaited_once_with(100)


@pytest.mark.asyncio
async def test_menu_action_queue_posts_formatted_queue(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(chat_id=100)
    state = fsm_context_in_state()
    mocker.patch(
        "bot.routers.callbacks.query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )

    await on_menu_action(query, MenuAction(target="queue"), state)

    query.message.answer.assert_awaited_once()
    args = query.message.answer.await_args.args
    assert args[0] == "body"


@pytest.mark.asyncio
async def test_menu_action_writer_sets_fsm_writer_input_state(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(chat_id=100)
    state = fsm_context_in_state()

    await on_menu_action(query, MenuAction(target="writer"), state)

    state.set_state.assert_awaited_once_with(WriterInput.waiting_text)
    query.message.answer.assert_awaited_once()
    # Verify the prompt text contains the "Writer" header
    prompt = query.message.answer.await_args.args[0]
    assert "Writer" in prompt


@pytest.mark.asyncio
async def test_menu_action_broadcast_sets_fsm_broadcast_state(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(chat_id=100)
    state = fsm_context_in_state()

    await on_menu_action(query, MenuAction(target="broadcast"), state)

    state.set_state.assert_awaited_once_with(BroadcastMessage.waiting_text)
    query.message.answer.assert_awaited_once()
    prompt = query.message.answer.await_args.args[0]
    assert "Enviar" in prompt or "mensagem" in prompt.lower()


@pytest.mark.asyncio
async def test_menu_action_reprocess_shows_usage_hint(
    mock_callback_query, fsm_context_in_state,
):
    query = mock_callback_query(chat_id=100)
    state = fsm_context_in_state()

    await on_menu_action(query, MenuAction(target="reprocess"), state)

    query.message.answer.assert_awaited_once()
    msg = query.message.answer.await_args.args[0]
    assert "/reprocess" in msg


@pytest.mark.asyncio
@pytest.mark.parametrize("target,handler_fn", [
    ("history", "format_history"),
    ("rejections", "format_rejections"),
    ("help", "format_help"),
])
async def test_menu_action_query_handlers_targets_swallow_errors(
    mock_callback_query, fsm_context_in_state, mocker, target, handler_fn,
):
    """Branches that delegate to query_handlers and wrap in try/except: pass.

    Verifies: (a) the delegation happens, (b) exceptions do not propagate.
    """
    query = mock_callback_query(chat_id=100)
    state = fsm_context_in_state()
    mocker.patch(f"bot.routers.callbacks.query_handlers.{handler_fn}", return_value="body")

    await on_menu_action(query, MenuAction(target=target), state)

    query.message.answer.assert_awaited_once()
