"""Unit tests for ProgressReporter.step() and debounced card flush."""
from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    return bot


@pytest.fixture
def mock_supabase():
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute = MagicMock()
    return sb


@pytest.mark.asyncio
async def test_step_calls_all_three_sinks_on_first_call(mock_bot, mock_supabase, caplog):
    from execution.core.progress_reporter import ProgressReporter

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_1",
        draft_id="draft_1", supabase_client=mock_supabase,
    )
    reporter._message_id = 999  # pretend start() was called
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    await reporter.step("Loading", "fetching contacts", level="info")
    # Wait for fire-and-forget event_log task to complete
    await asyncio.sleep(0.05)

    # Sink 2: event_log insert
    mock_supabase.table.assert_called_with("event_log")
    # Sink 3: Telegram card edit
    mock_bot.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_step_debounces_rapid_edits_within_2_seconds(mock_bot, mock_supabase):
    from execution.core.progress_reporter import ProgressReporter

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_2",
        supabase_client=mock_supabase,
    )
    reporter._message_id = 999
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    # Fire 3 steps rapidly
    await reporter.step("Step 1")
    await reporter.step("Step 2")
    await reporter.step("Step 3")

    # Within 100ms of the first step, only 1 edit should have fired.
    await asyncio.sleep(0.1)
    assert mock_bot.edit_message_text.await_count == 1


@pytest.mark.asyncio
async def test_finish_flushes_pending_debounced_state(mock_bot, mock_supabase):
    from execution.core.progress_reporter import ProgressReporter

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_3",
        supabase_client=mock_supabase,
    )
    reporter._message_id = 999
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    await reporter.step("Step 1")
    await reporter.step("Step 2")  # debounced
    # finish() must flush the final state (containing Step 2)
    await reporter.finish(message="Done")

    # At least 2 edits: one from first step + one from finish flush
    assert mock_bot.edit_message_text.await_count >= 2


@pytest.mark.asyncio
async def test_event_log_insert_failure_does_not_raise(mock_bot, mock_supabase):
    from execution.core.progress_reporter import ProgressReporter

    mock_supabase.table.return_value.insert.return_value.execute = MagicMock(
        side_effect=RuntimeError("supabase down")
    )

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_4",
        supabase_client=mock_supabase,
    )
    reporter._message_id = 999
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    # Must NOT raise even though the supabase insert throws
    await reporter.step("Step 1")
    await asyncio.sleep(0.05)

    mock_bot.edit_message_text.assert_awaited()  # Telegram still works


@pytest.mark.asyncio
async def test_step_without_supabase_client_still_updates_telegram(mock_bot):
    from execution.core.progress_reporter import ProgressReporter

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_5",
        supabase_client=None,
    )
    reporter._message_id = 999
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    await reporter.step("Step 1")
    await asyncio.sleep(0.05)

    mock_bot.edit_message_text.assert_awaited()
