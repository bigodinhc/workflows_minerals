"""Tests for the /tail command handler in webhook/bot/routers/commands.py."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def fake_supabase_events():
    """Returns a factory that builds a FakeSupabase client whose event_log
    returns a given list of rows."""
    def _build(rows):
        mock_chain = MagicMock()
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.limit.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=rows)

        mock_client = MagicMock()
        mock_client.table.return_value = mock_chain
        return mock_client, mock_chain
    return _build


@pytest.mark.asyncio
async def test_tail_without_args_shows_help(monkeypatch):
    from bot.routers.commands import cmd_tail

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = None

    await cmd_tail(message, command)

    message.reply.assert_called_once()
    help_text = message.reply.call_args[0][0]
    assert "/tail" in help_text
    assert "morning_check" in help_text  # lists available workflows


@pytest.mark.asyncio
async def test_tail_unknown_workflow_shows_error(monkeypatch):
    from bot.routers.commands import cmd_tail

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = "not_a_workflow"

    await cmd_tail(message, command)

    reply = message.reply.call_args[0][0]
    assert "desconhecido" in reply.lower() or "unknown" in reply.lower()
    assert "morning_check" in reply  # available workflows are listed in the error


@pytest.mark.asyncio
async def test_tail_resolves_default_run_id_from_state_store(monkeypatch, fake_supabase_events):
    """When no run_id is passed, /tail <workflow> must pull run_id from
    state_store.get_status(workflow)['run_id'] and query event_log."""
    from bot.routers import commands

    monkeypatch.setattr(
        "execution.core.state_store.get_status",
        lambda wf: {"status": "success", "run_id": "abc12345"},
    )
    mock_client, mock_chain = fake_supabase_events(rows=[
        {"ts": "2026-04-21T09:00:00+00:00", "level": "info", "event": "cron_started", "label": None, "detail": None},
        {"ts": "2026-04-21T09:00:05+00:00", "level": "info", "event": "step", "label": "Baixando dados", "detail": None},
        {"ts": "2026-04-21T09:02:00+00:00", "level": "info", "event": "cron_finished", "label": None, "detail": None},
    ])
    monkeypatch.setattr(commands, "_get_supabase_client", lambda: mock_client)

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = "morning_check"

    await commands.cmd_tail(message, command)

    # Assert supabase was queried with the right workflow + run_id
    mock_chain.eq.assert_any_call("workflow", "morning_check")
    mock_chain.eq.assert_any_call("run_id", "abc12345")
    mock_chain.limit.assert_called_once_with(30)
    mock_chain.order.assert_called_once_with("ts", desc=False)

    reply = message.reply.call_args[0][0]
    assert "morning_check" in reply
    assert "abc12345" in reply
    assert "cron_started" in reply
    assert "Baixando dados" in reply
    assert "cron_finished" in reply


@pytest.mark.asyncio
async def test_tail_with_explicit_run_id(monkeypatch, fake_supabase_events):
    from bot.routers import commands

    # state_store should NOT be consulted when run_id is explicit
    monkeypatch.setattr(
        "execution.core.state_store.get_status",
        lambda wf: pytest.fail("should not consult state_store when run_id given"),
    )
    mock_client, mock_chain = fake_supabase_events(rows=[
        {"ts": "2026-04-21T08:00:00+00:00", "level": "info", "event": "cron_started", "label": None, "detail": None},
    ])
    monkeypatch.setattr(commands, "_get_supabase_client", lambda: mock_client)

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = "morning_check r8f3abc12"

    await commands.cmd_tail(message, command)

    mock_chain.eq.assert_any_call("run_id", "r8f3abc12")
    reply = message.reply.call_args[0][0]
    assert "r8f3abc12" in reply


@pytest.mark.asyncio
async def test_tail_no_run_id_in_state_store(monkeypatch):
    """Legacy runs (pre-Phase 4) won't have run_id in last_run payload.
    Must report gracefully, not crash."""
    from bot.routers import commands

    monkeypatch.setattr(
        "execution.core.state_store.get_status",
        lambda wf: {"status": "success", "time_iso": "..."},  # no run_id key
    )

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = "morning_check"

    await commands.cmd_tail(message, command)

    reply = message.reply.call_args[0][0]
    assert "run_id" in reply.lower() or "legacy" in reply.lower()


@pytest.mark.asyncio
async def test_tail_no_status_for_workflow(monkeypatch):
    """No last_run entry at all for workflow."""
    from bot.routers import commands

    monkeypatch.setattr("execution.core.state_store.get_status", lambda wf: None)

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = "morning_check"

    await commands.cmd_tail(message, command)

    reply = message.reply.call_args[0][0]
    assert "nenhum" in reply.lower() or "no recent" in reply.lower()


@pytest.mark.asyncio
async def test_tail_empty_event_log(monkeypatch, fake_supabase_events):
    """run_id resolves, but event_log has no matching rows (shouldn't happen,
    but defensive)."""
    from bot.routers import commands

    monkeypatch.setattr(
        "execution.core.state_store.get_status",
        lambda wf: {"run_id": "abc12345"},
    )
    mock_client, _ = fake_supabase_events(rows=[])
    monkeypatch.setattr(commands, "_get_supabase_client", lambda: mock_client)

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = "morning_check"

    await commands.cmd_tail(message, command)

    reply = message.reply.call_args[0][0]
    assert "sem eventos" in reply.lower() or "no events" in reply.lower()


@pytest.mark.asyncio
async def test_tail_supabase_unavailable_reports_gracefully(monkeypatch):
    from bot.routers import commands

    monkeypatch.setattr(
        "execution.core.state_store.get_status",
        lambda wf: {"run_id": "abc12345"},
    )
    monkeypatch.setattr(commands, "_get_supabase_client", lambda: None)

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = "morning_check"

    await commands.cmd_tail(message, command)

    reply = message.reply.call_args[0][0]
    assert "supabase" in reply.lower() or "indispon" in reply.lower() or "unavailable" in reply.lower()


@pytest.mark.asyncio
async def test_tail_formats_events_with_timestamps_and_emojis(monkeypatch, fake_supabase_events):
    """Output format: HH:MM:SS <emoji> event_name — label."""
    from bot.routers import commands

    monkeypatch.setattr(
        "execution.core.state_store.get_status",
        lambda wf: {"run_id": "abc12345"},
    )
    mock_client, _ = fake_supabase_events(rows=[
        {"ts": "2026-04-21T09:00:02+00:00", "level": "info", "event": "cron_started", "label": None, "detail": None},
        {"ts": "2026-04-21T09:00:05+00:00", "level": "info", "event": "step", "label": "Baixando Platts", "detail": None},
        {"ts": "2026-04-21T09:00:08+00:00", "level": "error", "event": "cron_crashed", "label": "RuntimeError: boom", "detail": None},
    ])
    monkeypatch.setattr(commands, "_get_supabase_client", lambda: mock_client)

    message = MagicMock()
    message.reply = AsyncMock()
    command = MagicMock()
    command.args = "morning_check"

    await commands.cmd_tail(message, command)

    reply = message.reply.call_args[0][0]
    assert "09:00:02" in reply
    assert "09:00:05" in reply
    assert "09:00:08" in reply
    assert "Baixando Platts" in reply
    assert "RuntimeError" in reply
    # Error level renders with 🚨 or similar
    assert "🚨" in reply or "error" in reply.lower()
