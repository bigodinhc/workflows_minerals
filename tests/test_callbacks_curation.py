"""Characterization tests for webhook/bot/routers/callbacks.py — curation domain.

Tests freeze CURRENT behavior (2026-04-18). If a test fails after Phase 2 split,
the split regressed behavior — not the test's fault.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.callback_data import DraftAction, CurateAction, BroadcastConfirm
from bot.states import AdjustDraft, RejectReason
from bot.routers.callbacks_curation import (
    on_draft_adjust, on_draft_reject, on_draft_action,
    on_curate_action, on_broadcast_confirm,
)


# ─── on_draft_adjust ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_adjust_happy_path_sets_fsm_and_notifies(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="draft:adjust:abc123")
    state = fsm_context_in_state()
    mocker.patch("bot.routers.callbacks_curation.drafts_get",
                 return_value={"message": "hi", "status": "pending"})
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())

    await on_draft_adjust(query, DraftAction(action="adjust", draft_id="abc123"), state)

    state.set_state.assert_awaited_once_with(AdjustDraft.waiting_feedback)
    state.update_data.assert_awaited_once_with(draft_id="abc123")
    query.answer.assert_awaited_with("✏️ Modo ajuste")
    query.message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_draft_adjust_draft_missing_answers_error_and_does_not_set_state(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="draft:adjust:missing")
    state = fsm_context_in_state()
    mocker.patch("bot.routers.callbacks_curation.drafts_get", return_value=None)
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())

    await on_draft_adjust(query, DraftAction(action="adjust", draft_id="missing"), state)

    query.answer.assert_awaited_with("❌ Draft não encontrado")
    state.set_state.assert_not_called()


# ─── on_draft_reject ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_reject_happy_path_sets_reject_state_and_saves_feedback(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="draft:reject:xyz")
    state = fsm_context_in_state()
    mocker.patch(
        "bot.routers.callbacks_curation.drafts_get",
        return_value={"message": "📊 Iron ore up\n*MINERALS TRADING*", "status": "pending"},
    )
    mocker.patch("bot.routers.callbacks_curation.drafts_contains", return_value=True)
    mocker.patch("bot.routers.callbacks_curation.drafts_update")
    save_feedback = mocker.patch(
        "bot.routers.callbacks_curation.redis_queries.save_feedback", return_value="fbk_1",
    )
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())

    await on_draft_reject(query, DraftAction(action="reject", draft_id="xyz"), state)

    save_feedback.assert_called_once()
    kwargs = save_feedback.call_args.kwargs
    assert kwargs["action"] == "draft_reject"
    assert kwargs["item_id"] == "xyz"
    state.set_state.assert_awaited_once_with(RejectReason.waiting_reason)
    state.update_data.assert_awaited_once_with(feedback_key="fbk_1")
    query.answer.assert_awaited_with("❌ Rejeitado")


@pytest.mark.asyncio
async def test_draft_reject_missing_draft_still_sets_state_with_id_fallback_title(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="draft:reject:def456")
    state = fsm_context_in_state()
    mocker.patch("bot.routers.callbacks_curation.drafts_get", return_value=None)
    mocker.patch("bot.routers.callbacks_curation.drafts_contains", return_value=False)
    save_feedback = mocker.patch(
        "bot.routers.callbacks_curation.redis_queries.save_feedback", return_value="fbk_2",
    )
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())

    await on_draft_reject(query, DraftAction(action="reject", draft_id="def456"), state)

    assert save_feedback.call_args.kwargs["title"].startswith("Draft def456")
    state.set_state.assert_awaited_once_with(RejectReason.waiting_reason)


# ─── on_draft_action — approve branch ────────────────────────────────────────

@pytest.mark.asyncio
async def test_draft_action_approve_happy_path_dispatches_send(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="draft:approve:approved1")
    mocker.patch(
        "bot.routers.callbacks_curation.drafts_get",
        return_value={"message": "hi", "status": "pending",
                      "uazapi_token": None, "uazapi_url": None},
    )
    drafts_update = mocker.patch("bot.routers.callbacks_curation.drafts_update")
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())
    mocker.patch("bot.routers.callbacks_curation.process_approval_async", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_draft_action(query, DraftAction(action="approve", draft_id="approved1"))

    drafts_update.assert_called_once_with("approved1", status="approved")
    query.answer.assert_awaited_with("✅ Aprovado! Enviando...")
    create_task.assert_called_once()


@pytest.mark.asyncio
async def test_draft_action_approve_already_processed_short_circuits(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="draft:approve:dup1")
    mocker.patch(
        "bot.routers.callbacks_curation.drafts_get",
        return_value={"message": "hi", "status": "approved"},
    )
    drafts_update = mocker.patch("bot.routers.callbacks_curation.drafts_update")
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_draft_action(query, DraftAction(action="approve", draft_id="dup1"))

    query.answer.assert_awaited_with("⚠️ Já processado")
    drafts_update.assert_not_called()
    create_task.assert_not_called()


@pytest.mark.asyncio
async def test_draft_action_approve_missing_draft_answers_expired(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="draft:approve:gone")
    mocker.patch("bot.routers.callbacks_curation.drafts_get", return_value=None)
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())
    drafts_update = mocker.patch("bot.routers.callbacks_curation.drafts_update")
    create_task = mocker.patch("asyncio.create_task")

    await on_draft_action(query, DraftAction(action="approve", draft_id="gone"))

    query.answer.assert_awaited_with("❌ Draft não encontrado")
    drafts_update.assert_not_called()
    create_task.assert_not_called()


# ─── on_draft_action — test_approve branch ───────────────────────────────────

@pytest.mark.asyncio
async def test_draft_action_test_approve_dispatches_test_send(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="draft:test_approve:t1")
    mocker.patch(
        "bot.routers.callbacks_curation.drafts_get",
        return_value={"message": "hi", "status": "pending"},
    )
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())
    mocker.patch("bot.routers.callbacks_curation.process_test_send_async", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_draft_action(query, DraftAction(action="test_approve", draft_id="t1"))

    query.answer.assert_awaited_with("🧪 Enviando teste para 1 contato...")
    create_task.assert_called_once()


# ─── on_curate_action — archive branch ──────────────────────────────────────

@pytest.mark.asyncio
async def test_curate_action_archive_happy_path_finalizes_success(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="curate:archive:item_arch")
    state = fsm_context_in_state()
    # New behavior: archive writes status to Supabase (set_status -> True) then
    # discards from the Redis staging queue. Both run via asyncio.to_thread.
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=True))
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())

    await on_curate_action(query, CurateAction(action="archive", item_id="item_arch"), state)

    query.answer.assert_awaited_with("✅ Arquivado")


@pytest.mark.asyncio
async def test_curate_action_archive_not_found_short_circuits(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="curate:archive:item_gone")
    state = fsm_context_in_state()
    # New behavior: set_status returns False (item not found in Supabase) ->
    # handler short-circuits with "não encontrado no banco".
    mocker.patch("asyncio.to_thread", new=AsyncMock(return_value=False))
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())

    await on_curate_action(query, CurateAction(action="archive", item_id="item_gone"), state)

    query.answer.assert_awaited_with("⚠️ Item não encontrado no banco")


# ─── on_curate_action — pipeline branch ──────────────────────────────────────

@pytest.mark.asyncio
async def test_curate_action_pipeline_happy_path_schedules_run_pipeline(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="curate:pipeline:item1")
    state = fsm_context_in_state()
    # pipeline branch uses asyncio.to_thread(redis_client.get_staging, item_id)
    # so we patch asyncio.to_thread to return the item directly
    mocker.patch(
        "asyncio.to_thread",
        new=AsyncMock(return_value={"title": "T", "fullText": "body", "publishDate": "2026-04-18",
                                    "source": "Platts"}),
    )
    mocker.patch("bot.routers.callbacks_curation.redis_queries.mark_pipeline_processed")
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=mocker.MagicMock(message_id=99))
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=bot)
    create_task = mocker.patch("asyncio.create_task")

    await on_curate_action(query, CurateAction(action="pipeline", item_id="item1"), state)

    query.answer.assert_awaited_with("🖋️ Enviando para o Writer...")
    create_task.assert_called_once()


# ─── on_curate_action — send_raw branch ──────────────────────────────────────

@pytest.mark.asyncio
async def test_curate_action_send_raw_archives_and_dispatches(
    mock_callback_query, fsm_context_in_state, mocker,
):
    query = mock_callback_query(data="curate:send_raw:item2")
    state = fsm_context_in_state()
    # send_raw branch via asyncio.to_thread, in order:
    #   1) get_staging -> item
    #   2) news_repo.set_status (archived)
    #   3) redis_client.discard
    item = {"title": "Hdr", "fullText": "Body text"}
    to_thread = mocker.patch(
        "asyncio.to_thread",
        new=AsyncMock(side_effect=[item, True, 1]),
    )
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())
    mocker.patch("bot.routers.callbacks_curation.process_approval_async", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_curate_action(query, CurateAction(action="send_raw", item_id="item2"), state)

    # get_staging + set_status + discard = 3 to_thread calls
    assert to_thread.await_count == 3
    query.answer.assert_awaited_with("📲 Enviando para WhatsApp...")
    create_task.assert_called_once()


# ─── on_broadcast_confirm — send + cancel branches ───────────────────────────

@pytest.mark.asyncio
async def test_broadcast_confirm_send_happy_path_dispatches(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="bcast:send:bcast_1")
    mocker.patch(
        "bot.routers.callbacks_curation.drafts_get",
        return_value={"message": "direct text", "uazapi_token": None, "uazapi_url": None},
    )
    drafts_update = mocker.patch("bot.routers.callbacks_curation.drafts_update")
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())
    mocker.patch("bot.routers.callbacks_curation.process_approval_async", new=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_broadcast_confirm(query, BroadcastConfirm(action="send", draft_id="bcast_1"))

    drafts_update.assert_called_once_with("bcast_1", status="approved")
    query.answer.assert_awaited_with("📲 Enviando...")
    create_task.assert_called_once()


@pytest.mark.asyncio
async def test_broadcast_confirm_cancel_finalizes_without_dispatch(
    mock_callback_query, mocker,
):
    query = mock_callback_query(data="bcast:cancel:bcast_1")
    mocker.patch("bot.routers.callbacks_curation.get_bot", return_value=AsyncMock())
    create_task = mocker.patch("asyncio.create_task")

    await on_broadcast_confirm(query, BroadcastConfirm(action="cancel", draft_id="bcast_1"))

    query.answer.assert_awaited_with("❌ Cancelado")
    create_task.assert_not_called()


# ─── on_curate_action — archive Supabase write (Task 3.1) ────────────────────

@pytest.mark.asyncio
async def test_curate_archive_writes_supabase_then_discards_redis(mock_callback_query):
    from bot.callback_data import CurateAction
    import bot.routers.callbacks_curation as cc

    query = mock_callback_query(data="curate:archive:abc123")
    cb = CurateAction(action="archive", item_id="abc123")
    state = MagicMock()
    state.set_state = AsyncMock(); state.update_data = AsyncMock()

    with patch.object(cc.news_repo, "set_status", return_value=True) as m_status, \
         patch.object(cc.redis_client, "discard") as m_discard, \
         patch.object(cc, "_finalize_card", new=AsyncMock()):
        await cc.on_curate_action(query, cb, state)

    m_status.assert_called_once()
    assert m_status.call_args[0][1] == "archived"
    m_discard.assert_called_once_with("abc123")


@pytest.mark.asyncio
async def test_curate_archive_aborts_when_supabase_fails(mock_callback_query):
    from bot.callback_data import CurateAction
    import bot.routers.callbacks_curation as cc

    query = mock_callback_query(data="curate:archive:abc123")
    cb = CurateAction(action="archive", item_id="abc123")
    state = MagicMock()

    with patch.object(cc.news_repo, "set_status", side_effect=RuntimeError("down")), \
         patch.object(cc.redis_client, "discard") as m_discard, \
         patch.object(cc, "_finalize_card", new=AsyncMock()):
        await cc.on_curate_action(query, cb, state)

    m_discard.assert_not_called()
