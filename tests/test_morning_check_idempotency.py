"""Idempotency-ordering tests for morning_check._run_pipeline.

Asserts that the split-lock state_store helpers are called in the correct
order across all exit paths. Regression guard for the 2026-04-22 bug
where the single-claim-before-validation pattern held the key for 48h
after early-exits (empty/incomplete Platts data), blocking "will retry
later" branches for the rest of the day.
"""
from __future__ import annotations

import argparse
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub heavy dependencies that are not installed in the test environment.
# platts_client imports pandas + spgci at module level; we inject lightweight
# MagicMock stand-ins before morning_check is first imported so the import
# chain resolves without errors.
# ---------------------------------------------------------------------------
_STUBS = ["pandas", "spgci"]
for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()  # type: ignore[assignment]


# ── FIXTURES ─────────────────────────────────────────────────────────────────

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


def _make_report_items(count: int = 20) -> list:
    """Return a list of MagicMock report items (above threshold by default)."""
    return [MagicMock() for _ in range(count)]


@pytest.fixture
def patched_integrations(monkeypatch):
    """Patch all integration classes at module boundary with MagicMocks that
    the tests can configure per scenario."""
    from execution.scripts import morning_check as mod

    platts_instance = MagicMock()
    platts_instance.get_report_data.return_value = _make_report_items(20)

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

    progress_instance = MagicMock()
    progress_instance.start = MagicMock()
    progress_instance.update = MagicMock()
    progress_instance.finish = MagicMock()
    progress_instance.finish_empty = MagicMock()
    progress_instance.fail = MagicMock()
    progress_instance.on_dispatch_tick = MagicMock()

    monkeypatch.setattr(mod, "PlattsClient", lambda: platts_instance)
    monkeypatch.setattr(mod, "ContactsRepo", lambda: contacts_instance)
    monkeypatch.setattr(mod, "UazapiClient", lambda: uazapi_instance)
    monkeypatch.setattr(mod, "DeliveryReporter", lambda **kwargs: delivery_reporter_instance)
    monkeypatch.setattr(mod, "ProgressReporter", lambda **kwargs: progress_instance)

    # asyncio.run(progress.finish(...)) — patch asyncio.run to be a no-op
    import asyncio
    monkeypatch.setattr(asyncio, "run", lambda coro: None)

    return {
        "platts": platts_instance,
        "contacts": contacts_instance,
        "uazapi": uazapi_instance,
        "delivery_reporter": delivery_reporter_instance,
        "progress": progress_instance,
    }


@pytest.fixture
def morning_env(monkeypatch):
    """Env vars required for initialisation + event_bus sinks."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)


@pytest.fixture
def active_bus(monkeypatch):
    """Set up a live EventBus in the ContextVar so get_current_bus()
    resolves inside _run_pipeline."""
    from execution.core import event_bus

    bus = event_bus.EventBus(workflow="morning_check")
    bus._sinks = []  # silence all sinks
    token = event_bus._active_bus.set(bus)
    yield bus
    event_bus._active_bus.reset(token)


def _invoke(dry_run: bool = False):
    """Shared helper: call _run_pipeline with standard args."""
    from execution.scripts import morning_check as mod
    args = argparse.Namespace(dry_run=dry_run, date=None)
    mod._run_pipeline(args)


# ── SCENARIOS ─────────────────────────────────────────────────────────────────

def test_scenario_1_sent_flag_already_set(
    patched_integrations, morning_env, active_bus, monkeypatch,
):
    """Phase 0 short-circuit: sent flag already present → exit immediately.
    Platts data is never fetched."""
    from execution.core import state_store

    check_calls = []
    other_calls = []

    def spy_check_true(*args, **kwargs):
        check_calls.append(args)
        return True

    monkeypatch.setattr(state_store, "check_sent_flag", spy_check_true)
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda *a, **k: other_calls.append(("try_claim", a)) or True)
    monkeypatch.setattr(state_store, "set_sent_flag", lambda *a, **k: other_calls.append(("set_sent", a)))
    monkeypatch.setattr(state_store, "release_inflight", lambda *a, **k: other_calls.append(("release", a)))

    _invoke(dry_run=False)

    assert len(check_calls) == 1
    assert other_calls == []
    patched_integrations["platts"].get_report_data.assert_not_called()


def test_scenario_2_no_platts_data(
    spy_state_store, patched_integrations, morning_env, active_bus,
):
    """No data from Platts → sys.exit(0) at Phase 2a, before lock is acquired.

    Direct regression: the old code would have claimed inflight for 48h here,
    killing all "will retry later" runs for the day."""
    patched_integrations["platts"].get_report_data.return_value = []

    with pytest.raises(SystemExit) as exc_info:
        _invoke(dry_run=False)
    assert exc_info.value.code == 0

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert spy_state_store["try_claim_alert_key"] == [], (
        "No lock must be acquired when Platts returns empty data — "
        "this is the core bug we are fixing."
    )
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []


def test_scenario_3_incomplete_platts_data(
    spy_state_store, patched_integrations, morning_env, active_bus,
):
    """Incomplete data from Platts (below MIN_ITEMS_EXPECTED=10) → sys.exit(0)
    at Phase 2b, before lock is acquired.

    Direct regression of the 2026-04-22 production bug."""
    patched_integrations["platts"].get_report_data.return_value = _make_report_items(5)

    with pytest.raises(SystemExit) as exc_info:
        _invoke(dry_run=False)
    assert exc_info.value.code == 0

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert spy_state_store["try_claim_alert_key"] == [], (
        "No lock must be acquired when Platts data is incomplete — "
        "this is the core bug we are fixing."
    )
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []


def test_scenario_4_concurrent_run_holds_lock(
    patched_integrations, morning_env, active_bus, monkeypatch,
):
    """Another cron run is mid-flight → try_claim_alert_key returns False,
    exit cleanly without dispatching."""
    from execution.core import state_store

    check_calls = []
    claim_calls = []
    other_calls = []

    monkeypatch.setattr(state_store, "check_sent_flag", lambda *a, **k: check_calls.append(a) or False)

    def spy_claim_false(*args, **kwargs):
        claim_calls.append(args)
        return False

    monkeypatch.setattr(state_store, "try_claim_alert_key", spy_claim_false)
    monkeypatch.setattr(state_store, "set_sent_flag", lambda *a, **k: other_calls.append(("set_sent", a)))
    monkeypatch.setattr(state_store, "release_inflight", lambda *a, **k: other_calls.append(("release", a)))

    _invoke(dry_run=False)

    assert len(check_calls) == 1
    assert len(claim_calls) == 1
    assert other_calls == []  # no set_sent_flag, no release_inflight
    patched_integrations["delivery_reporter"].dispatch.assert_not_called()


def test_scenario_5_full_success(
    patched_integrations, morning_env, active_bus, monkeypatch,
):
    """Happy path → check_sent_flag → acquire lock → send → set_sent_flag → release lock.

    Also verifies the critical ordering invariant: set_sent_flag MUST complete
    before release_inflight, so that even if a crash between them keeps the
    inflight lock held until TTL, the sent flag is already committed and the
    next run correctly short-circuits at Phase 0."""
    from execution.core import state_store

    call_order = []

    def spy_check(*args, **kwargs):
        call_order.append("check_sent_flag")
        return False

    def spy_claim(*args, **kwargs):
        call_order.append("try_claim_alert_key")
        return True

    def spy_set(*args, **kwargs):
        call_order.append("set_sent_flag")

    def spy_release(*args, **kwargs):
        call_order.append("release_inflight")

    monkeypatch.setattr(state_store, "check_sent_flag", spy_check)
    monkeypatch.setattr(state_store, "try_claim_alert_key", spy_claim)
    monkeypatch.setattr(state_store, "set_sent_flag", spy_set)
    monkeypatch.setattr(state_store, "release_inflight", spy_release)

    _invoke(dry_run=False)

    # All four helpers called exactly once
    assert call_order.count("check_sent_flag") == 1
    assert call_order.count("try_claim_alert_key") == 1
    assert call_order.count("set_sent_flag") == 1
    assert call_order.count("release_inflight") == 1

    # Critical ordering: check → acquire → set → release
    assert call_order.index("check_sent_flag") < call_order.index("try_claim_alert_key")
    assert call_order.index("try_claim_alert_key") < call_order.index("set_sent_flag")
    assert call_order.index("set_sent_flag") < call_order.index("release_inflight")

    patched_integrations["delivery_reporter"].dispatch.assert_called_once()
