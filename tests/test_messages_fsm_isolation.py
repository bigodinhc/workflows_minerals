"""Characterization tests — FSM isolation in webhook/bot/routers/messages.py.

Guards the bug class fixed in commits 2cab598, a6214a0, 17135d8: a catch-all
F.text handler must NOT exist on message_router. Each FSM state handler must
fire only in its state.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from aiogram.filters import StateFilter
from bot.routers.messages import (
    message_router, reply_kb_router,
    on_broadcast_text, on_adjust_feedback, on_reject_reason,
    on_add_contact_data, on_writer_text,
    on_reply_reports, on_reply_queue,
)
from bot.states import (
    AdjustDraft, RejectReason, AddContact, BroadcastMessage, WriterInput,
)


# ─── No catch-all on message_router ──────────────────────────────────────────

def test_message_router_has_no_catchall_text_handler_without_state_filter():
    """Regression guard: every observer on message_router must have a state filter.

    Introduced after commits 2cab598, a6214a0, 17135d8 — a catch-all F.text handler
    would intercept FSM state messages. This test fails if one is added back.
    """
    for handler in message_router.message.handlers:
        filters = handler.filters or []
        has_state_filter = any(
            isinstance(f.callback, StateFilter) or
            getattr(f.callback, "__class__", None).__name__ == "StateFilter" or
            "state" in repr(f).lower() or
            hasattr(f, "states") or hasattr(f.callback, "states")
            for f in filters
        )
        if not has_state_filter:
            func = handler.callback
            pytest.fail(
                f"message_router has a catchall F.text handler: {func.__name__}. "
                "Every handler on message_router MUST have an explicit StateFilter or "
                "StatesGroup member as its first positional filter. "
                "If this handler does not need state, register it on reply_kb_router instead."
            )


# ─── FSM state handlers: happy path in their state ───────────────────────────

@pytest.mark.asyncio
async def test_broadcast_text_handler_creates_draft_and_shows_preview(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="Alô WhatsApp")
    state = fsm_context_in_state(state=BroadcastMessage.waiting_text)
    mocker.patch("bot.routers._helpers.drafts_set")
    mocker.patch("time.time", return_value=1700000000)

    await on_broadcast_text(msg, state)

    state.clear.assert_awaited_once()
    msg.answer.assert_awaited()  # preview message sent
    args, kwargs = msg.answer.await_args
    assert "PREVIEW" in args[0]


@pytest.mark.asyncio
async def test_adjust_feedback_handler_schedules_process_adjustment(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="adicione um parágrafo")
    state = fsm_context_in_state(
        state=AdjustDraft.waiting_feedback, data={"draft_id": "abc"},
    )
    mocker.patch("bot.routers.messages.process_adjustment", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_adjust_feedback(msg, state)

    state.clear.assert_awaited_once()
    create_task.assert_called_once()


@pytest.mark.asyncio
async def test_reject_reason_skip_keyword_shortcircuits(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="pular")
    state = fsm_context_in_state(
        state=RejectReason.waiting_reason, data={"feedback_key": "fbk_1"},
    )
    update = mocker.patch("bot.routers.messages.redis_queries.update_feedback_reason")

    await on_reject_reason(msg, state)

    msg.answer.assert_awaited_with("✅ Ok, sem razão registrada.")
    update.assert_not_called()


@pytest.mark.asyncio
async def test_add_contact_data_happy_path_writes_to_sheet(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="João 11999998888")
    state = fsm_context_in_state(state=AddContact.waiting_data)
    mocker.patch(
        "bot.routers.messages.contact_admin.parse_add_input",
        return_value=("João", "11999998888"),
    )
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=([], 0)))
    mocker.patch("bot.routers.messages.SheetsClient")

    await on_add_contact_data(msg, state)

    state.clear.assert_awaited()
    # final confirmation message sent
    final_call = msg.answer.await_args_list[-1]
    assert "adicionado" in final_call.args[0]


@pytest.mark.asyncio
async def test_writer_text_handler_schedules_process_news(
    mock_message, fsm_context_in_state, mocker,
):
    msg = mock_message(text="Iron ore up 2%")
    state = fsm_context_in_state(state=WriterInput.waiting_text)
    progress_msg = mocker.MagicMock()
    progress_msg.message_id = 55
    msg.answer = AsyncMock(return_value=progress_msg)
    mocker.patch("bot.routers.messages.ANTHROPIC_API_KEY", "test-key")
    mocker.patch("bot.routers.messages.process_news", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_writer_text(msg, state)

    state.clear.assert_awaited_once()
    create_task.assert_called_once()


# ─── Reply keyboard router — separate from message_router ───────────────────

@pytest.mark.asyncio
async def test_reply_kb_reports_invokes_show_types(mock_message, mocker):
    msg = mock_message(text="📊 Reports")
    show = mocker.patch("reports_nav.reports_show_types", new=AsyncMock())

    await on_reply_reports(msg)

    show.assert_awaited_once_with(msg.chat.id)


@pytest.mark.asyncio
async def test_reply_kb_queue_posts_formatted_queue(mock_message, mocker):
    msg = mock_message(text="📰 Fila")
    mocker.patch(
        "query_handlers.format_queue_page",
        return_value=("body", {"inline_keyboard": []}),
    )

    await on_reply_queue(msg)

    msg.answer.assert_awaited_once()
    body_arg = msg.answer.await_args.args[0]
    assert body_arg == "body"
