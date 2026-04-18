"""Characterization tests — workflow trigger callbacks + nop."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.callback_data import WorkflowRun, WorkflowList
from bot.routers.callbacks import on_workflow_run, on_workflow_list, on_nop


@pytest.mark.asyncio
async def test_workflow_run_happy_path_edits_and_tracks(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("workflow_trigger._workflow_name_by_id", return_value="daily_report")
    mocker.patch("workflow_trigger.trigger_workflow", new=AsyncMock(return_value=(True, None)))
    mocker.patch("workflow_trigger.find_triggered_run", new=AsyncMock(return_value="run_42"))
    mocker.patch("workflow_trigger.poll_and_update", new=AsyncMock())
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)
    mocker.patch("asyncio.create_task")

    await on_workflow_run(query, WorkflowRun(workflow_id="wf_daily"))

    query.answer.assert_awaited_with("Disparando daily_report...")
    # At minimum one edit: "🚀 *Disparando daily_report...*"
    assert bot.edit_message_text.await_count >= 1


@pytest.mark.asyncio
async def test_workflow_run_trigger_failure_shows_error_with_retry(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("workflow_trigger._workflow_name_by_id", return_value="failing_wf")
    mocker.patch(
        "workflow_trigger.trigger_workflow",
        new=AsyncMock(return_value=(False, "api rate limit")),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)

    await on_workflow_run(query, WorkflowRun(workflow_id="fail_wf"))

    # Error message edit is issued — text is positional arg[0]
    edits = [c.args[0] for c in bot.edit_message_text.await_args_list]
    assert any("erro ao disparar" in e for e in edits)


@pytest.mark.asyncio
async def test_workflow_run_no_run_id_shows_warning(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch("workflow_trigger._workflow_name_by_id", return_value="wf")
    mocker.patch("workflow_trigger.trigger_workflow", new=AsyncMock(return_value=(True, None)))
    mocker.patch("workflow_trigger.find_triggered_run", new=AsyncMock(return_value=None))
    mocker.patch("workflow_trigger.poll_and_update", new=AsyncMock())
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)

    # Capture and immediately await the task so the _track() coroutine runs inline
    import asyncio
    orig_create_task = asyncio.create_task
    captured_tasks: list = []

    def _capture(coro):
        t = orig_create_task(coro)
        captured_tasks.append(t)
        return t

    mocker.patch("asyncio.create_task", side_effect=_capture)

    await on_workflow_run(query, WorkflowRun(workflow_id="wf"))
    for t in captured_tasks:
        await t

    edits = [c.args[0] for c in bot.edit_message_text.await_args_list]
    assert any("nao encontrei o run" in e for e in edits)


@pytest.mark.asyncio
async def test_workflow_list_action_list_renders(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100, message_id=200)
    mocker.patch(
        "workflow_trigger.render_workflow_list",
        new=AsyncMock(return_value=("workflows text", {"inline_keyboard": []})),
    )
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    mocker.patch("bot.routers.callbacks.get_bot", return_value=bot)

    await on_workflow_list(query, WorkflowList(action="list"))

    bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_workflow_list_back_menu_reopens_main_menu(mock_callback_query, mocker):
    query = mock_callback_query(chat_id=100)
    mocker.patch("bot.routers.callbacks.get_bot", return_value=AsyncMock())
    mocker.patch("bot.routers.callbacks.build_main_menu_keyboard", return_value={"kb": 1})

    await on_workflow_list(query, WorkflowList(action="back_menu"))

    query.message.answer.assert_awaited()


@pytest.mark.asyncio
async def test_nop_callback_answers_empty(mock_callback_query):
    query = mock_callback_query(data="nop")

    await on_nop(query)

    query.answer.assert_awaited_with("")
