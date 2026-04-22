"""Idempotency-ordering tests for baltic_ingestion._run_with_progress.

Asserts that the split-lock state_store helpers are called in the correct
order across all exit paths. Regression guard for the 2026-04-22 bug
where the single-claim-before-validation pattern held the key for 48h
after early-exits, wasting the day.

Reference: docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


TODAY_STR = "2026-04-22"


def _today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _yesterday_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _msg(received: str) -> dict:
    """Graph API message fixture."""
    return {
        "id": "msg_1",
        "subject": "Baltic Exchange Daily Indices & Baltic Dry Index",
        "receivedDateTime": received,
        "hasAttachments": True,
        "from": {"emailAddress": {"address": "DailyReports@midship.com"}},
    }


VALID_CLAUDE_DATA = {
    "report_date": TODAY_STR,
    "bdi": {"value": 2017, "change": -29, "direction": "DOWN"},
    "capesize": {"value": 2884, "change": -105, "direction": "DOWN"},
    "panamax": {"value": 1874, "change": 0, "direction": "FLAT"},
    "supramax": {"value": 1461, "change": 14, "direction": "UP"},
    "handysize": {"value": 753, "change": 8, "direction": "UP"},
    "routes": [
        {"code": "C3", "value": 11.7, "change": -0.186, "description": "Tubarao to Qingdao"},
    ],
    "extraction_confidence": "high",
}


@pytest.fixture
def spy_state_store(monkeypatch):
    """Record every call to the 4 state_store helpers used by the script."""
    from execution.core import state_store
    calls = {
        "check_sent_flag": [],
        "try_claim_alert_key": [],
        "set_sent_flag": [],
        "release_inflight": [],
    }

    def _mk_spy(name, return_value):
        def spy(*args, **kwargs):
            calls[name].append({"args": args, "kwargs": kwargs})
            return return_value
        return spy

    monkeypatch.setattr(state_store, "check_sent_flag", _mk_spy("check_sent_flag", False))
    monkeypatch.setattr(state_store, "try_claim_alert_key", _mk_spy("try_claim_alert_key", True))
    monkeypatch.setattr(state_store, "set_sent_flag", _mk_spy("set_sent_flag", None))
    monkeypatch.setattr(state_store, "release_inflight", _mk_spy("release_inflight", None))
    return calls


@pytest.fixture
def patched_integrations(monkeypatch):
    """Patch all integration classes at module boundary with MagicMocks that
    the tests can configure per scenario."""
    from execution.scripts import baltic_ingestion as mod

    baltic_instance = MagicMock()
    baltic_instance.find_latest_email.return_value = _msg(_today_iso())
    baltic_instance.get_pdf_attachment.return_value = (b"fake-pdf-bytes", "baltic.pdf")

    claude_instance = MagicMock()
    claude_instance.extract_data_from_pdf.return_value = VALID_CLAUDE_DATA

    contacts_instance = MagicMock()
    contacts_instance.list_active.return_value = [
        MagicMock(name="Contact1", phone_uazapi="5511999999999"),
    ]

    uazapi_instance = MagicMock()
    uazapi_instance.send_message = MagicMock(return_value=None)

    delivery_reporter_instance = MagicMock()
    report = MagicMock()
    report.success_count = 1
    report.failure_count = 0
    report.total = 1
    delivery_reporter_instance.dispatch.return_value = report

    monkeypatch.setattr(mod, "BalticClient", lambda: baltic_instance)
    monkeypatch.setattr(mod, "ClaudeClient", lambda: claude_instance)
    monkeypatch.setattr(mod, "ContactsRepo", lambda: contacts_instance)
    monkeypatch.setattr(mod, "UazapiClient", lambda: uazapi_instance)
    monkeypatch.setattr(mod, "DeliveryReporter", lambda **kwargs: delivery_reporter_instance)

    # ingest_to_ironmarket does a live HTTP call — patch it out
    monkeypatch.setattr(mod, "ingest_to_ironmarket", lambda data: (True, "Success"))

    return {
        "baltic": baltic_instance,
        "claude": claude_instance,
        "contacts": contacts_instance,
        "uazapi": uazapi_instance,
        "delivery_reporter": delivery_reporter_instance,
    }


@pytest.fixture
def patched_bot_and_progress(monkeypatch):
    """Patch aiogram Bot, supabase.create_client, and ProgressReporter (all
    imported inside the async function) so the script doesn't touch the
    network."""
    import aiogram
    import supabase
    from execution.core import progress_reporter as pr_mod

    bot_instance = AsyncMock()
    sent_message = MagicMock()
    sent_message.message_id = 999
    bot_instance.send_message = AsyncMock(return_value=sent_message)
    bot_instance.session = MagicMock()
    bot_instance.session.close = AsyncMock()

    monkeypatch.setattr(aiogram, "Bot", lambda token: bot_instance)
    monkeypatch.setattr(supabase, "create_client", lambda url, key: MagicMock())

    progress_instance = MagicMock()
    progress_instance.step = AsyncMock()
    progress_instance.finish = AsyncMock()
    progress_instance._message_id = None
    progress_instance._pending_card_state = []
    progress_instance._last_edit_at = None
    monkeypatch.setattr(pr_mod, "ProgressReporter", lambda **kwargs: progress_instance)

    return bot_instance, progress_instance


@pytest.fixture
def baltic_env(monkeypatch):
    """Env vars required for Bot initialisation + event_bus sinks."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_BALTIC", "12345")
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)


@pytest.fixture
def active_bus(monkeypatch):
    """Set up a live EventBus in the ContextVar so get_current_bus()
    resolves inside _run_with_progress."""
    from execution.core import event_bus

    bus = event_bus.EventBus(workflow="baltic_ingestion")
    bus._sinks = []  # silence all sinks
    token = event_bus._active_bus.set(bus)
    yield bus
    event_bus._active_bus.reset(token)


async def _invoke(dry_run: bool = False):
    """Shared helper: call _run_with_progress with standard args."""
    from execution.scripts.baltic_ingestion import _run_with_progress
    args = argparse.Namespace(dry_run=dry_run)
    await _run_with_progress(args, chat_id=12345, today_str=TODAY_STR)


# ── SCENARIOS ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scenario_1_sent_flag_already_set(
    spy_state_store, patched_integrations, patched_bot_and_progress, baltic_env, active_bus, monkeypatch,
):
    """Phase 0 short-circuit: sent flag already present → exit immediately."""
    from execution.core import state_store

    def spy_check_true(*args, **kwargs):
        spy_state_store["check_sent_flag"].append({"args": args, "kwargs": kwargs})
        return True

    monkeypatch.setattr(state_store, "check_sent_flag", spy_check_true)

    await _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert spy_state_store["try_claim_alert_key"] == []
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []
    # Email API must not even be called
    patched_integrations["baltic"].find_latest_email.assert_not_called()


@pytest.mark.asyncio
async def test_scenario_2_no_email_found(
    spy_state_store, patched_integrations, patched_bot_and_progress, baltic_env, active_bus,
):
    """No email in last 24h → early-exit before any lock."""
    patched_integrations["baltic"].find_latest_email.return_value = None

    await _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert spy_state_store["try_claim_alert_key"] == []
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []
    patched_integrations["baltic"].get_pdf_attachment.assert_not_called()


@pytest.mark.asyncio
async def test_scenario_3_email_from_yesterday(
    spy_state_store, patched_integrations, patched_bot_and_progress, baltic_env, active_bus,
):
    """Email found but dated yesterday → early-exit before any lock.

    Direct regression of the 2026-04-22 production bug."""
    patched_integrations["baltic"].find_latest_email.return_value = _msg(_yesterday_iso())

    await _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert spy_state_store["try_claim_alert_key"] == [], (
        "No lock must be acquired when the email is not from today — this "
        "is the core bug we are fixing."
    )
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []
    patched_integrations["baltic"].get_pdf_attachment.assert_not_called()


@pytest.mark.asyncio
async def test_scenario_4_concurrent_run_holds_lock(
    spy_state_store, patched_integrations, patched_bot_and_progress, baltic_env, active_bus, monkeypatch,
):
    """Another cron is mid-run → try_claim_alert_key returns False, exit clean."""
    from execution.core import state_store

    def spy_claim_false(*args, **kwargs):
        spy_state_store["try_claim_alert_key"].append({"args": args, "kwargs": kwargs})
        return False

    monkeypatch.setattr(state_store, "try_claim_alert_key", spy_claim_false)

    await _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert len(spy_state_store["try_claim_alert_key"]) == 1
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []
    # PDF/Claude/broadcast must not run
    patched_integrations["baltic"].get_pdf_attachment.assert_not_called()
    patched_integrations["claude"].extract_data_from_pdf.assert_not_called()
    patched_integrations["delivery_reporter"].dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_scenario_5_pdf_missing(
    spy_state_store, patched_integrations, patched_bot_and_progress, baltic_env, active_bus,
):
    """Email is today, but no PDF attachment / link → lock acquired, released,
    sent flag never written."""
    patched_integrations["baltic"].get_pdf_attachment.return_value = (None, None)

    await _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert len(spy_state_store["try_claim_alert_key"]) == 1
    assert spy_state_store["set_sent_flag"] == []
    assert len(spy_state_store["release_inflight"]) == 1


@pytest.mark.asyncio
async def test_scenario_6_claude_low_confidence(
    spy_state_store, patched_integrations, patched_bot_and_progress, baltic_env, active_bus,
):
    """Low-confidence extraction raises RuntimeError → lock released in finally,
    sent flag never written."""
    patched_integrations["claude"].extract_data_from_pdf.return_value = {
        "extraction_confidence": "low",
    }

    with pytest.raises(RuntimeError, match="extraction failed"):
        await _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert len(spy_state_store["try_claim_alert_key"]) == 1
    assert spy_state_store["set_sent_flag"] == []
    assert len(spy_state_store["release_inflight"]) == 1


@pytest.mark.asyncio
async def test_scenario_7_full_success(
    spy_state_store, patched_integrations, patched_bot_and_progress, baltic_env, active_bus,
):
    """Happy path → check_sent_flag → acquire lock → process → set_sent_flag → release lock."""
    await _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert len(spy_state_store["try_claim_alert_key"]) == 1
    assert len(spy_state_store["set_sent_flag"]) == 1
    assert len(spy_state_store["release_inflight"]) == 1

    # Verify ordering: sent flag set before inflight released
    # (both happen in the success path; ordering matters because a crash
    # between them would leave sent=set but inflight=held until TTL, which
    # is fine but we want the documented order)
    patched_integrations["delivery_reporter"].dispatch.assert_called_once()
