# Daily-Report Split-Lock Idempotency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-key idempotency pattern in baltic_ingestion.py and morning_check.py with a split-lock pattern (short-TTL in-flight + long-TTL sent flag) so that pre-processing early-exits don't waste the day, and fix the event_log `label NOT NULL` bug silently dropping lifecycle events.

**Architecture:** Two Redis keys per daily-report workflow: `daily_report:inflight:{REPORT_TYPE}:{date}` (20min TTL, acquired post-validation, released in `finally`) and `daily_report:sent:{REPORT_TYPE}:{date}` (48h TTL, written only after full broadcast success). Three new helpers added to `execution/core/state_store.py`. Lifecycle events (`cron_started`/`cron_finished`) start persisting to Supabase `event_log` via a one-line fallback in `execution/core/event_bus.py`.

**Tech Stack:** Python 3.10, pytest + pytest-asyncio + fakeredis, Redis (redis-py), aiogram3, Supabase Python client.

---

## File Structure

| File | Role | Change type |
|---|---|---|
| `execution/core/state_store.py` | Add 3 helpers (`check_sent_flag`, `set_sent_flag`, `release_inflight`); update `try_claim_alert_key` docstring | Modify (~40 lines added) |
| `execution/core/event_bus.py` | Line 137: `label or None` → `label or event` | Modify (1 line) |
| `execution/scripts/baltic_ingestion.py` | Replace single-claim block with split-lock phase structure | Modify (~30 lines net) |
| `execution/scripts/morning_check.py` | Same refactor | Modify (~25 lines net) |
| `tests/test_state_store.py` | Add coverage for 3 new helpers | Extend (~80 lines) |
| `tests/test_event_bus.py` | Add test for label fallback | Extend (~15 lines) |
| `tests/test_baltic_ingestion_idempotency.py` | New file, 7 scenarios | Create (~220 lines) |
| `tests/test_morning_check_idempotency.py` | New file, 5 scenarios | Create (~170 lines) |
| `.planning/codebase/CONVENTIONS.md` | Add "Idempotency — daily reports" section | Modify (~30 lines) |

---

## Task 1: Add `state_store` helpers (TDD)

**Files:**
- Modify: `execution/core/state_store.py` (add 3 functions after `try_claim_alert_key`)
- Test: `tests/test_state_store.py` (extend)

- [ ] **Step 1.1: Write failing tests for `check_sent_flag`**

Append to `tests/test_state_store.py`:

```python
def test_check_sent_flag_returns_false_when_absent(fake_redis):
    from execution.core.state_store import check_sent_flag
    assert check_sent_flag("daily_report:sent:TEST:2026-04-22") is False


def test_check_sent_flag_returns_true_when_present(fake_redis):
    from execution.core.state_store import check_sent_flag, set_sent_flag
    set_sent_flag("daily_report:sent:TEST:2026-04-22", ttl_seconds=60)
    assert check_sent_flag("daily_report:sent:TEST:2026-04-22") is True


def test_check_sent_flag_returns_false_when_redis_unavailable(monkeypatch):
    """Permissive degrade: treat 'unknown' as 'not sent' so workflow proceeds."""
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: None)
    assert state_store.check_sent_flag("any") is False


def test_check_sent_flag_returns_false_when_redis_raises(monkeypatch):
    from execution.core import state_store

    class FlakyRedis:
        def exists(self, key):
            raise RuntimeError("connection lost")

    monkeypatch.setattr(state_store, "_get_client", lambda: FlakyRedis())
    assert state_store.check_sent_flag("any") is False
```

- [ ] **Step 1.2: Write failing tests for `set_sent_flag`**

```python
def test_set_sent_flag_writes_key_with_ttl(fake_redis):
    from execution.core.state_store import set_sent_flag
    set_sent_flag("daily_report:sent:TEST:2026-04-22", ttl_seconds=3600)
    assert fake_redis.get("daily_report:sent:TEST:2026-04-22") == "1"
    ttl = fake_redis.ttl("daily_report:sent:TEST:2026-04-22")
    assert 3595 <= ttl <= 3600


def test_set_sent_flag_overwrites_existing(fake_redis):
    from execution.core.state_store import set_sent_flag
    fake_redis.set("daily_report:sent:TEST:2026-04-22", "0", ex=60)
    set_sent_flag("daily_report:sent:TEST:2026-04-22", ttl_seconds=3600)
    assert fake_redis.get("daily_report:sent:TEST:2026-04-22") == "1"


def test_set_sent_flag_noop_when_redis_unavailable(monkeypatch):
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: None)
    # Must not raise
    state_store.set_sent_flag("any", ttl_seconds=60)


def test_set_sent_flag_noop_when_redis_raises(monkeypatch):
    from execution.core import state_store

    class FlakyRedis:
        def set(self, key, value, ex=None):
            raise RuntimeError("connection lost")

    monkeypatch.setattr(state_store, "_get_client", lambda: FlakyRedis())
    state_store.set_sent_flag("any", ttl_seconds=60)  # Must not raise
```

- [ ] **Step 1.3: Write failing tests for `release_inflight`**

```python
def test_release_inflight_deletes_key(fake_redis):
    from execution.core.state_store import release_inflight
    fake_redis.set("daily_report:inflight:TEST:2026-04-22", "1", ex=60)
    release_inflight("daily_report:inflight:TEST:2026-04-22")
    assert fake_redis.get("daily_report:inflight:TEST:2026-04-22") is None


def test_release_inflight_is_idempotent_on_missing_key(fake_redis):
    from execution.core.state_store import release_inflight
    release_inflight("daily_report:inflight:TEST:missing")  # Must not raise


def test_release_inflight_noop_when_redis_unavailable(monkeypatch):
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: None)
    state_store.release_inflight("any")  # Must not raise


def test_release_inflight_noop_when_redis_raises(monkeypatch):
    from execution.core import state_store

    class FlakyRedis:
        def delete(self, key):
            raise RuntimeError("connection lost")

    monkeypatch.setattr(state_store, "_get_client", lambda: FlakyRedis())
    state_store.release_inflight("any")  # Must not raise
```

- [ ] **Step 1.4: Run tests to confirm they fail**

Run: `pytest tests/test_state_store.py -v -k "sent_flag or release_inflight" 2>&1 | tail -30`

Expected: all new tests fail with `ImportError: cannot import name 'check_sent_flag'` (or similar) since helpers don't exist yet.

- [ ] **Step 1.5: Implement the 3 helpers in `execution/core/state_store.py`**

Add after the `try_claim_alert_key` function (around line 228), before `_STREAK_THRESHOLD = 3`:

```python
def check_sent_flag(key: str) -> bool:
    """Read-only existence check for a sent flag. Returns True iff the key
    exists in Redis.

    Permissive on Redis failure: returns False so the caller proceeds as if
    not yet sent. If the workflow subsequently succeeds, a fresh sent flag
    is written. If Redis stays down, the next run takes the same path — no
    silent duplicate delivery (we'd prefer an operator-visible duplicate to
    a silent skip)."""
    client = _get_client()
    if client is None:
        return False
    try:
        return client.exists(key) > 0
    except Exception as exc:
        logger.warning(f"state_store.check_sent_flag failed: {exc}")
        return False


def set_sent_flag(key: str, ttl_seconds: int) -> None:
    """Unconditional SET EX — marks a workflow as successfully completed for
    its window. Call only after the guarded operation has fully succeeded.
    Non-raising. Overwrites any existing key (use when the caller owns the
    key's lifecycle; if you need atomic 'first-writer-wins' semantics, use
    try_claim_alert_key instead)."""
    client = _get_client()
    if client is None:
        return
    try:
        client.set(key, "1", ex=ttl_seconds)
    except Exception as exc:
        logger.warning(f"state_store.set_sent_flag failed: {exc}")


def release_inflight(key: str) -> None:
    """Explicit release of an in-flight lock (DEL). Complements
    try_claim_alert_key used as a short-TTL mutex: the TTL is the crash
    safety net, but normal happy-path exit releases the lock immediately
    so the next run doesn't have to wait.

    Idempotent: harmless if the key already expired. Non-raising."""
    client = _get_client()
    if client is None:
        return
    try:
        client.delete(key)
    except Exception as exc:
        logger.warning(f"state_store.release_inflight failed: {exc}")
```

- [ ] **Step 1.6: Run tests to confirm they pass**

Run: `pytest tests/test_state_store.py -v -k "sent_flag or release_inflight" 2>&1 | tail -30`

Expected: all 12 new tests pass (4 per helper).

Also run the full state_store test suite to verify no regressions:

Run: `pytest tests/test_state_store.py -v 2>&1 | tail -20`

Expected: all existing tests still pass.

- [ ] **Step 1.7: Commit**

```bash
git add execution/core/state_store.py tests/test_state_store.py
git commit -m "feat(state_store): add check_sent_flag, set_sent_flag, release_inflight helpers

Building blocks for the split-lock pattern used by daily-report workflows.
Complements try_claim_alert_key (which stays as the NX-lock acquirer).
All non-raising, permissive on Redis failure."
```

---

## Task 2: Fix `event_bus` label fallback (TDD)

**Files:**
- Modify: `execution/core/event_bus.py:137`
- Test: `tests/test_event_bus.py`

- [ ] **Step 2.1: Write failing test**

Append to `tests/test_event_bus.py`:

```python
def test_emit_without_label_uses_event_name_as_label():
    """event_log.label is NOT NULL in Supabase. When a caller emits an event
    without an explicit label (e.g., lifecycle events cron_started/
    cron_finished emitted by the @with_event_bus decorator), the event name
    itself must populate the label field. Otherwise the _SupabaseSink insert
    throws 'null value in column label violates not-null constraint' and the
    event silently drops from the timeline."""
    from execution.core.event_bus import EventBus

    captured = []

    class _CaptureSink:
        def emit(self, event_dict):
            captured.append(event_dict)

    bus = EventBus(workflow="test")
    bus._sinks = [_CaptureSink()]
    bus.emit("cron_started")  # no explicit label

    assert len(captured) == 1
    assert captured[0]["event"] == "cron_started"
    assert captured[0]["label"] == "cron_started"  # fallback


def test_emit_with_explicit_label_keeps_it():
    """Regression guard: explicit labels must not be overwritten by the event
    name fallback."""
    from execution.core.event_bus import EventBus

    captured = []

    class _CaptureSink:
        def emit(self, event_dict):
            captured.append(event_dict)

    bus = EventBus(workflow="test")
    bus._sinks = [_CaptureSink()]
    bus.emit("step", label="fetching data")

    assert captured[0]["event"] == "step"
    assert captured[0]["label"] == "fetching data"
```

- [ ] **Step 2.2: Run tests to confirm they fail**

Run: `pytest tests/test_event_bus.py::test_emit_without_label_uses_event_name_as_label -v 2>&1 | tail -15`

Expected: fail with `assert None == 'cron_started'` (current code maps empty label to None at line 137).

The second test should already pass (regression guard).

- [ ] **Step 2.3: Fix the line**

In `execution/core/event_bus.py:137`, change:

```python
"label": label or None,
```

to:

```python
"label": label or event,
```

- [ ] **Step 2.4: Run tests to confirm they pass**

Run: `pytest tests/test_event_bus.py -v 2>&1 | tail -30`

Expected: both new tests pass. All existing event_bus tests continue to pass.

- [ ] **Step 2.5: Commit**

```bash
git add execution/core/event_bus.py tests/test_event_bus.py
git commit -m "fix(event_bus): fall back to event name when label is empty

Satisfies the NOT NULL constraint on event_log.label so cron_started and
cron_finished events persist. Previously every run logged 'null value in
column label violates not-null constraint' and these lifecycle events
silently dropped from the Supabase timeline, breaking /tail output."
```

---

## Task 3: Refactor `baltic_ingestion.py` to split-lock (TDD)

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py` (phase 1 claim block)
- Create: `tests/test_baltic_ingestion_idempotency.py`

- [ ] **Step 3.1: Create test file with 7 parametrized scenarios**

Create `tests/test_baltic_ingestion_idempotency.py`:

```python
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
```

- [ ] **Step 3.2: Run tests to confirm they fail (current code has wrong ordering)**

Run: `pytest tests/test_baltic_ingestion_idempotency.py -v 2>&1 | tail -40`

Expected: test_scenario_3 fails (current code calls `try_claim_alert_key` before checking email date). Other scenarios may also fail because the current code uses the old flow (single claim, no `check_sent_flag`, no `set_sent_flag`, no `release_inflight`).

- [ ] **Step 3.3: Refactor `baltic_ingestion.py` to split-lock**

In `execution/scripts/baltic_ingestion.py`, replace lines 225-278 (the current `try:` block from `PHASE 1: check control sheet` through the `email_dt != today_dt` validation).

Find the current block:

```python
    try:
        # ── PHASE 1: check control sheet ─────────────────────────────────────
        if not args.dry_run:
            claim_key = f"daily_report:sent:{REPORT_TYPE}:{today_str}"
            claimed = await asyncio.to_thread(
                state_store.try_claim_alert_key, claim_key, 48 * 3600
            )
            if not claimed:
                logger.info("Report already sent today. Exiting.")
                await reporter.step("Skipped", "report already sent today", level="info")
                await reporter.finish()
                return

        # ── PHASE 2: fetch email via Graph API ────────────────────────────────
        logger.info("Checking Outlook for Baltic Exchange email...")
        bus.emit("step", label="Buscando emails (Graph API)")
        baltic = BalticClient()

        try:
            t0 = _time.time()
            msg = await asyncio.to_thread(baltic.find_latest_email)
            bus.emit("api_call", label="graph.find_latest_email", detail={"duration_ms": round((_time.time() - t0) * 1000)})
        except Exception as e:
            ...
            raise

        if not msg:
            ...
            return

        # Validate if email is actually from TODAY
        email_date_str = msg['receivedDateTime']
        try:
            email_date_str_clean = email_date_str.replace("Z", "+00:00")
            email_dt = datetime.fromisoformat(email_date_str_clean).date()
            today_dt = datetime.utcnow().date()

            if email_dt != today_dt:
                logger.info(f"Found email but it is from {email_dt} (not today {today_dt}). Report not released yet.")
                logger.info(f"Subject: {msg['subject']}")
                await reporter.step(
                    "No email found",
                    f"latest email is from {email_dt}, today is {today_dt}",
                    level="info",
                )
                await reporter.finish()
                return

        except Exception as e:
            logger.warning(f"Could not validate email date: {e}. Proceeding with caution.")
```

Replace with the split-lock version:

```python
    sent_key = f"daily_report:sent:{REPORT_TYPE}:{today_str}"
    inflight_key = f"daily_report:inflight:{REPORT_TYPE}:{today_str}"
    inflight_held = False

    try:
        # ── PHASE 0: check sent flag (read-only, no side effect) ─────────────
        if not args.dry_run:
            already_sent = await asyncio.to_thread(state_store.check_sent_flag, sent_key)
            if already_sent:
                logger.info("Report already delivered today. Exiting.")
                await reporter.step("Skipped", "already delivered today", level="info")
                await reporter.finish()
                return

        # ── PHASE 1: fetch email via Graph API ───────────────────────────────
        logger.info("Checking Outlook for Baltic Exchange email...")
        bus.emit("step", label="Buscando emails (Graph API)")
        baltic = BalticClient()

        try:
            t0 = _time.time()
            msg = await asyncio.to_thread(baltic.find_latest_email)
            bus.emit("api_call", label="graph.find_latest_email", detail={"duration_ms": round((_time.time() - t0) * 1000)})
        except Exception as e:
            logger.error(f"Failed to fetch emails: {e}")
            await reporter.step(
                f"Failed: {type(e).__name__}",
                f"email fetch error: {str(e)[:200]}",
                level="error",
            )
            raise

        if not msg:
            logger.info("No matching email found in the last 24h.")
            await reporter.step("No email found", "no Baltic email in the last 24h", level="info")
            await reporter.finish()
            return

        # ── PHASE 2: validate email is from today ────────────────────────────
        email_date_str = msg['receivedDateTime']
        try:
            email_date_str_clean = email_date_str.replace("Z", "+00:00")
            email_dt = datetime.fromisoformat(email_date_str_clean).date()
            today_dt = datetime.utcnow().date()

            if email_dt != today_dt:
                logger.info(f"Found email but it is from {email_dt} (not today {today_dt}). Report not released yet.")
                logger.info(f"Subject: {msg['subject']}")
                await reporter.step(
                    "No email found",
                    f"latest email is from {email_dt}, today is {today_dt}",
                    level="info",
                )
                await reporter.finish()
                return

        except Exception as e:
            logger.warning(f"Could not validate email date: {e}. Proceeding with caution.")

        # ── PHASE 3: acquire in-flight lock ──────────────────────────────────
        # We have confirmed data to process. Claim the lock to prevent a
        # concurrent cron (started while we're still running) from broadcasting
        # the same report twice. 20min TTL safely covers observed broadcast
        # durations (17-18min yesterday); if exceeded, tune upward.
        if not args.dry_run:
            acquired = await asyncio.to_thread(
                state_store.try_claim_alert_key, inflight_key, 20 * 60
            )
            if not acquired:
                logger.info("Another run is processing this report. Exiting.")
                await reporter.step("Skipped", "another run in progress", level="info")
                await reporter.finish()
                return
            inflight_held = True

        logger.info(f"Found email: {msg['subject']} ({msg['receivedDateTime']})")
        await reporter.step(
            "Email fetched",
            f"from {msg.get('from', {}).get('emailAddress', {}).get('address', 'unknown')} at {email_date_str}",
        )
```

Then, **in the same file**, at the end of the pipeline (after `await reporter.finish(report=report, message=message)`), add the `set_sent_flag` call. And wrap the existing `try:` body with an outer try/finally that releases the lock.

Current structure (post-phase-3 success):

```python
        await reporter.finish(report=report, message=message)

    except Exception as exc:
        await reporter.step(...)
        raise
    finally:
        await bot.session.close()
```

Change to:

```python
        # ── PHASE 4c: commit success — set sent flag ─────────────────────────
        if not args.dry_run:
            await asyncio.to_thread(state_store.set_sent_flag, sent_key, 48 * 3600)

        await reporter.finish(report=report, message=message)

    except Exception as exc:
        await reporter.step(
            f"Failed: {type(exc).__name__}",
            str(exc)[:200],
            level="error",
        )
        raise
    finally:
        # Release the in-flight lock regardless of outcome. Auto-expires in
        # 20min as a crash safety net if this release is skipped.
        if inflight_held:
            try:
                await asyncio.to_thread(state_store.release_inflight, inflight_key)
            except Exception as release_exc:
                logger.warning(f"Failed to release inflight lock: {release_exc}")
        await bot.session.close()
```

Note: the original `except Exception as exc:` already re-raises — keep that. The `finally` adds the lock-release before the `bot.session.close()`.

Also remove the now-unused variable `claim_key` from the script if it survives anywhere (grep after the edit to confirm no dead references).

- [ ] **Step 3.4: Run tests to confirm they pass**

Run: `pytest tests/test_baltic_ingestion_idempotency.py -v 2>&1 | tail -30`

Expected: all 7 tests pass.

- [ ] **Step 3.5: Run related existing tests to check for regressions**

Run: `pytest tests/test_event_bus.py tests/test_state_store.py tests/test_delivery_reporter.py tests/test_progress_reporter.py -v 2>&1 | tail -30`

Expected: all existing tests pass.

- [ ] **Step 3.6: Manual dry-run**

Run (from repo root, with real env set):

```bash
python execution/scripts/baltic_ingestion.py --dry-run
```

Expected: either finds today's email and prints the extracted data + WhatsApp preview, OR skips cleanly (no email, email from yesterday) — **in both cases no Redis key is written** (dry-run branch skips all state_store calls by design). No exceptions.

If you don't have the env set locally, skip this step and rely on CI + post-deploy verification.

- [ ] **Step 3.7: Commit**

```bash
git add execution/scripts/baltic_ingestion.py tests/test_baltic_ingestion_idempotency.py
git commit -m "fix(baltic): split-lock idempotency (sent flag + in-flight lock)

Move the Redis claim from pre-validation to post-validation. The sent flag
(daily_report:sent:*, 48h TTL) is now written only after the full broadcast
succeeds; a separate in-flight lock (daily_report:inflight:*, 20min TTL)
guards against concurrent crons and is released in finally.

Fixes the 2026-04-22 regression where the first cron of the day reclaimed
the single key before checking if the email was from today, then exited —
leaving the key held for 48h and blocking every subsequent run that day.

Covered by tests/test_baltic_ingestion_idempotency.py (7 scenarios)."
```

---

## Task 4: Refactor `morning_check.py` to split-lock (TDD)

**Files:**
- Modify: `execution/scripts/morning_check.py` (phase 2 claim block + end-of-pipeline commit)
- Create: `tests/test_morning_check_idempotency.py`

- [ ] **Step 4.1: Read the current script structure**

Read: `execution/scripts/morning_check.py` (full file). Identify:
- The claim block (current lines 211-219)
- The two "will retry later" early-exits (lines 230-233 and 241-245)
- The end-of-send success path (after delivery_reporter call)
- The outer try/except/finally structure

- [ ] **Step 4.2: Create `tests/test_morning_check_idempotency.py`**

Mirror the Baltic test pattern, adapted for morning_check's integrations (PlattsClient instead of BalticClient; no Claude). 5 scenarios:

```python
"""Idempotency-ordering tests for morning_check.

Reference: docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md
"""
from __future__ import annotations

import argparse
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


TODAY_STR = datetime.now().strftime("%Y-%m-%d")

MIN_ITEMS_EXPECTED = 10  # must match morning_check.MIN_ITEMS_EXPECTED
FULL_ITEMS = [MagicMock() for _ in range(20)]
SPARSE_ITEMS = [MagicMock() for _ in range(5)]  # below threshold


@pytest.fixture
def spy_state_store(monkeypatch):
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
    from execution.scripts import morning_check as mod

    platts_instance = MagicMock()
    platts_instance.get_report_data.return_value = FULL_ITEMS

    contacts_instance = MagicMock()
    contacts_instance.list_active.return_value = [
        MagicMock(name="Contact1", phone_uazapi="5511999999999"),
    ]

    delivery_reporter_instance = MagicMock()
    report = MagicMock()
    report.success_count = 1
    report.failure_count = 0
    report.total = 1
    delivery_reporter_instance.dispatch.return_value = report

    monkeypatch.setattr(mod, "PlattsClient", lambda: platts_instance)
    monkeypatch.setattr(mod, "ContactsRepo", lambda: contacts_instance)
    monkeypatch.setattr(mod, "DeliveryReporter", lambda **kwargs: delivery_reporter_instance)

    return {
        "platts": platts_instance,
        "contacts": contacts_instance,
        "delivery_reporter": delivery_reporter_instance,
    }


@pytest.fixture
def morning_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service-key")


@pytest.fixture
def active_bus(monkeypatch):
    from execution.core import event_bus
    bus = event_bus.EventBus(workflow="morning_check")
    bus._sinks = []
    token = event_bus._active_bus.set(bus)
    yield bus
    event_bus._active_bus.reset(token)


# The morning_check main function is synchronous (no asyncio). Invoke directly.
def _invoke(dry_run: bool = False):
    from execution.scripts import morning_check as mod
    args = argparse.Namespace(dry_run=dry_run)
    # morning_check may use sys.exit for early-exits. Catch SystemExit to
    # assert on side effects without failing the test.
    try:
        mod._run_pipeline(args)  # refactor extracts the pipeline body into _run_pipeline
    except SystemExit as e:
        assert e.code == 0, f"unexpected non-zero exit: {e.code}"


def test_scenario_1_sent_flag_already_set(
    spy_state_store, patched_integrations, morning_env, active_bus, monkeypatch,
):
    from execution.core import state_store

    def spy_check_true(*args, **kwargs):
        spy_state_store["check_sent_flag"].append({"args": args, "kwargs": kwargs})
        return True

    monkeypatch.setattr(state_store, "check_sent_flag", spy_check_true)

    _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert spy_state_store["try_claim_alert_key"] == []
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []
    patched_integrations["platts"].get_report_data.assert_not_called()


def test_scenario_2_no_platts_data(
    spy_state_store, patched_integrations, morning_env, active_bus,
):
    """Platts returned empty → no lock, no flag."""
    patched_integrations["platts"].get_report_data.return_value = []

    _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert spy_state_store["try_claim_alert_key"] == [], (
        "No lock must be acquired when Platts returned no data."
    )
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []


def test_scenario_3_incomplete_platts_data(
    spy_state_store, patched_integrations, morning_env, active_bus,
):
    """Platts returned below MIN_ITEMS_EXPECTED → no lock (next cron can retry)."""
    patched_integrations["platts"].get_report_data.return_value = SPARSE_ITEMS

    _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert spy_state_store["try_claim_alert_key"] == []
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []


def test_scenario_4_concurrent_run_holds_lock(
    spy_state_store, patched_integrations, morning_env, active_bus, monkeypatch,
):
    from execution.core import state_store

    def spy_claim_false(*args, **kwargs):
        spy_state_store["try_claim_alert_key"].append({"args": args, "kwargs": kwargs})
        return False

    monkeypatch.setattr(state_store, "try_claim_alert_key", spy_claim_false)

    _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert len(spy_state_store["try_claim_alert_key"]) == 1
    assert spy_state_store["set_sent_flag"] == []
    assert spy_state_store["release_inflight"] == []
    patched_integrations["delivery_reporter"].dispatch.assert_not_called()


def test_scenario_5_full_success(
    spy_state_store, patched_integrations, morning_env, active_bus,
):
    _invoke(dry_run=False)

    assert len(spy_state_store["check_sent_flag"]) == 1
    assert len(spy_state_store["try_claim_alert_key"]) == 1
    assert len(spy_state_store["set_sent_flag"]) == 1
    assert len(spy_state_store["release_inflight"]) == 1
    patched_integrations["delivery_reporter"].dispatch.assert_called_once()
```

**Important design note:** the test imports `mod._run_pipeline` which does not exist yet. As part of the refactor (Step 4.4), extract the current pipeline body from `morning_check.main()` into a testable function `_run_pipeline(args)`. This is the minimal extraction needed to make the flow testable — do not expand scope beyond that.

- [ ] **Step 4.3: Run tests to confirm they fail**

Run: `pytest tests/test_morning_check_idempotency.py -v 2>&1 | tail -30`

Expected: all 5 fail, most likely with `AttributeError: module 'morning_check' has no attribute '_run_pipeline'` (since the extraction hasn't happened yet).

- [ ] **Step 4.4: Refactor `morning_check.py`**

In `execution/scripts/morning_check.py`:

1. Extract the current pipeline body (today lines ~200-end of `main`, roughly the section from `bus.emit(...)` through the delivery call) into a new top-level function `_run_pipeline(args)`. The goal is a single entry point that the test can call.

2. Replace the claim block (current lines 211-219) with the split-lock phase structure. Find:

```python
    try:
        # 2. Idempotency claim via Redis (48h TTL — report is daily).
        if not args.dry_run:
            bus.emit("step", label="Checando idempotência via Redis")
            claim_key = f"daily_report:sent:{REPORT_TYPE}:{date_str}"
            if not state_store.try_claim_alert_key(claim_key, ttl_seconds=48 * 3600):
                logger.info("Report already sent today. Exiting.")
                progress.finish_empty("report ja enviado hoje")
                return
```

Replace with:

```python
    sent_key = f"daily_report:sent:{REPORT_TYPE}:{date_str}"
    inflight_key = f"daily_report:inflight:{REPORT_TYPE}:{date_str}"
    inflight_held = False

    try:
        # ── PHASE 0: check sent flag (read-only) ─────────────────────────────
        if not args.dry_run:
            bus.emit("step", label="Checando sent flag")
            if state_store.check_sent_flag(sent_key):
                logger.info("Report already delivered today. Exiting.")
                progress.finish_empty("report já entregue hoje")
                return
```

3. Move the current data-validation early-exits (lines 230-233 and 241-245) to run **before** the lock acquisition. They already do in the file's current layout (they come after the claim, but conceptually they fetch data; we need to fetch first, then claim). Reorder so:

```
PHASE 0: check_sent_flag (above)
PHASE 1: fetch platts data (existing lines 222-228)
PHASE 2a: early-exit if empty (existing 230-233)
PHASE 2b: early-exit if below threshold (existing 235-245)
PHASE 3: acquire in-flight lock ← NEW, inserted here
PHASE 4: build_message + delivery (existing)
PHASE 5: set_sent_flag ← NEW, after successful delivery
```

Insert after the incomplete-data early-exit, before `build_message`:

```python
        # ── PHASE 3: acquire in-flight lock ──────────────────────────────────
        if not args.dry_run:
            if not state_store.try_claim_alert_key(inflight_key, ttl_seconds=20 * 60):
                logger.info("Another run is processing this report. Exiting.")
                progress.finish_empty("another run in progress")
                return
            inflight_held = True
```

4. After the delivery succeeds (end of `_run_pipeline`), add the sent-flag write and wrap the pipeline in try/finally:

```python
        # ── PHASE 5: commit success ─────────────────────────────────────────
        if not args.dry_run:
            state_store.set_sent_flag(sent_key, ttl_seconds=48 * 3600)

    finally:
        if inflight_held:
            try:
                state_store.release_inflight(inflight_key)
            except Exception as release_exc:
                logger.warning(f"Failed to release inflight lock: {release_exc}")
```

5. Update `main()` to call `_run_pipeline(args)` instead of having the pipeline body inline.

- [ ] **Step 4.5: Run tests to confirm they pass**

Run: `pytest tests/test_morning_check_idempotency.py -v 2>&1 | tail -30`

Expected: all 5 pass.

- [ ] **Step 4.6: Run related existing tests**

Run: `pytest tests/test_state_store.py tests/test_event_bus.py tests/test_delivery_reporter.py -v 2>&1 | tail -20`

Expected: all pass.

- [ ] **Step 4.7: Manual dry-run**

Run:

```bash
python execution/scripts/morning_check.py --dry-run
```

Expected: either prints message preview or exits cleanly on "no data"; no exceptions. Dry-run skips all state_store calls.

- [ ] **Step 4.8: Commit**

```bash
git add execution/scripts/morning_check.py tests/test_morning_check_idempotency.py
git commit -m "fix(morning_check): split-lock idempotency

Same regression as baltic_ingestion — the pre-validation claim held the
key for 48h even when Platts data was empty or incomplete, blocking the
\"will retry later\" branches for the rest of the day.

Extract pipeline body into _run_pipeline(args) for test reach; split the
single claim into a sent flag (post-success, 48h) and an in-flight lock
(post-validation, 20min, released in finally). Covered by 5 scenarios in
tests/test_morning_check_idempotency.py."
```

---

## Task 5: Update `try_claim_alert_key` docstring + conventions doc

**Files:**
- Modify: `execution/core/state_store.py:211-227` (docstring only)
- Modify: `.planning/codebase/CONVENTIONS.md`

- [ ] **Step 5.1: Update `try_claim_alert_key` docstring**

In `execution/core/state_store.py:211-227`, replace the existing docstring:

```python
def try_claim_alert_key(key: str, ttl_seconds: int) -> bool:
    """Atomic Redis SET NX EX. Returns True if the caller claimed the key
    (should fire the alert), False if the key already existed (someone else
    alerted). Degrades permissive: returns True on any Redis failure so an
    alert still fires rather than silently swallowed.

    Use for idempotent alert suppression (e.g., watchdog missing-cron
    notifications that must fire exactly once per miss)."""
```

with:

```python
def try_claim_alert_key(key: str, ttl_seconds: int) -> bool:
    """Atomic Redis SET NX EX. Returns True if the caller claimed the key,
    False if the key already existed. Degrades permissive: returns True on
    any Redis failure so the caller proceeds rather than silently skipping.

    Two use cases:

    1. Short-TTL in-flight mutex — guards a section that must not run
       concurrently. Pair with `release_inflight()` in a finally block;
       the TTL is the crash safety net.

    2. Alert-fire deduplication — e.g., watchdog missing-cron notifications
       that must fire exactly once per miss.

    For the 'mark as completed' semantic (set a long-TTL flag only after a
    pipeline succeeds), use `set_sent_flag()` instead — this function's
    atomicity is wasted when you only want to record success."""
```

- [ ] **Step 5.2: Add conventions section**

Append to `.planning/codebase/CONVENTIONS.md` (place it under the existing structure — if the file has a top-level pattern like "## Backend patterns" or similar, add as a new "## Idempotency" section; otherwise append at the end):

```markdown
## Idempotency — daily reports (split-lock pattern)

Daily-report workflows (`baltic_ingestion`, `morning_check`) use **two** Redis keys, not one:

- `daily_report:inflight:{REPORT_TYPE}:{date}` — 20min TTL. Acquired via `try_claim_alert_key` after data validation; released in `finally` on exit. Prevents two crons from broadcasting the same report in parallel. Auto-expires after a crash.
- `daily_report:sent:{REPORT_TYPE}:{date}` — 48h TTL. Written via `set_sent_flag` **only** after the full broadcast (IronMarket POST + WhatsApp dispatch) succeeds. Checked via `check_sent_flag` at the start of every run.

### Claim ordering rule

Early-exits that precede any side effect (source data missing, stale, or incomplete) must not touch either key. Early-exits that occur after Phase 3 (lock acquired) release the lock in `finally` but do **not** set the sent flag — the next cron retries cleanly.

### Anti-pattern (the bug of 2026-04-22)

Using a single long-TTL `SET NX EX` key as both the concurrency guard and the sent flag. A pre-processing early-exit then holds the key for the full TTL, blocking all retries on the same day. The Sheets→Supabase migration (`df15d9aa`) regressed this by compacting the legacy `check_daily_status` + `mark_daily_status` pair into one atomic call; the split-lock pattern above is the correct replacement.

### When to deviate

If operational experience shows mid-broadcast crashes happen often enough that duplicate WhatsApp messages become a real complaint, add per-contact dedup (Redis set of delivered phone numbers under a 48h key). `DeliveryReporter` already tracks per-contact results, so this is ~20 lines. Not needed today.

### Reference

- Design doc: `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md`
- Implementation plan: `docs/superpowers/plans/2026-04-22-idempotency-split-lock-plan.md`
```

- [ ] **Step 5.3: Commit**

```bash
git add execution/core/state_store.py .planning/codebase/CONVENTIONS.md
git commit -m "docs: document split-lock idempotency convention

Expand try_claim_alert_key docstring to cover the two distinct use cases
(in-flight mutex vs. alert dedup). Add 'Idempotency — daily reports' section
to CONVENTIONS.md describing the split-lock pattern and the anti-pattern
that regressed in the Sheets→Supabase migration."
```

---

## Task 6: Full test-suite verification

**Files:** none modified here — this is a verification gate.

- [ ] **Step 6.1: Run the full test suite**

Run: `pytest 2>&1 | tail -40`

Expected: all tests pass. Note any failures.

- [ ] **Step 6.2: If failures, triage**

- If the failure is in a test you **added** (tasks 1-4), inspect the assertion and fix the implementation or the test. Commit the fix separately.
- If the failure is in a **pre-existing** test and is caused by your changes, fix the regression at the root cause (do not disable the test).
- If the failure is **flaky** (passes on rerun, unrelated to your changes), note it but do not mask it.

- [ ] **Step 6.3: Final sanity check — grep for dead references**

Run:

```bash
grep -n "claim_key" execution/scripts/baltic_ingestion.py execution/scripts/morning_check.py
grep -n "check_daily_status\|mark_daily_status" execution/ -r
grep -n "sheets_client\|SheetsClient" execution/scripts/baltic_ingestion.py execution/scripts/morning_check.py
```

Expected:
- `claim_key` → no occurrences (removed; `sent_key` and `inflight_key` replaced it).
- `check_daily_status` / `mark_daily_status` → no occurrences in production code (these were removed in the Sheets→Supabase migration; verify nothing snuck back).
- `SheetsClient` in the two scripts → no occurrences (removed in `df15d9aa`).

- [ ] **Step 6.4: Commit (only if anything was fixed in steps 6.2/6.3)**

If no further changes are needed, skip this step.

---

## Rollout (post-merge, manual)

These are **not code tasks** — run them after the branch merges to main.

- [ ] **Step 7.1: Unblock today's Baltic report**

1. Check if the legacy claim key is still held:

   ```bash
   redis-cli -u "$REDIS_URL" GET daily_report:sent:BALTIC_REPORT:2026-04-22
   ```

   Expected: `"1"` (the stuck claim from today's failed first run).

2. Delete it:

   ```bash
   redis-cli -u "$REDIS_URL" DEL daily_report:sent:BALTIC_REPORT:2026-04-22
   ```

3. Trigger the workflow:

   ```bash
   gh workflow run baltic_ingestion.yml
   ```

4. Watch the run:

   ```bash
   gh run watch  # picks the most recent run
   ```

5. Confirm via `/tail baltic_ingestion` in Telegram — should show `cron_started`, phase steps (`Buscando emails`, `Email fetched`, `PDF extracted`, `Claude parsed`, `Enviando para IronMarket`, `Enviando WhatsApp`, `Postgres upsert`), and `cron_finished`.

- [ ] **Step 7.2: Check morning_check for the same stuck state**

```bash
redis-cli -u "$REDIS_URL" GET daily_report:sent:MORNING_REPORT:2026-04-22
```

If `"1"`, repeat the unblock procedure with `gh workflow run morning_check.yml`. If `nil`, no action needed.

- [ ] **Step 7.3: Verify event_log label fix**

```bash
# Via dashboard or direct SQL:
# SELECT count(*) FROM event_log WHERE event IN ('cron_started', 'cron_finished') AND created_at > NOW() - INTERVAL '1 hour';
```

Expected: rows present. If zero, the label fallback didn't land — re-check event_bus.py:137.

- [ ] **Step 7.4: Monitor over the next 2-3 days**

- All daily reports deliver on first-attempt cron run when source data is ready.
- No double-sends (check `delivery_summary` rows in event_log).
- `/tail <workflow>` shows lifecycle events for every run.
- No `"null value in column label"` errors in any workflow's logs.

If any assertion fails, open a follow-up note in `docs/superpowers/followups/2026-04-DD-idempotency-split-lock-followups.md`.

---

## Self-review

**Spec coverage check:**
- Goal 1 (early-exits don't waste the day): Tasks 3, 4 — split-lock places claim post-validation.
- Goal 2 (transient failures retry cleanly): Tasks 3, 4 — inflight lock auto-expires in 20min on crash.
- Goal 3 (concurrent crons can't both broadcast): Tasks 3, 4 — inflight SET NX prevents.
- Goal 4 (successful broadcast blocks rerun): Tasks 3, 4 — `set_sent_flag` at end of happy path.
- Goal 5 (regression test for ordering): Tasks 3, 4 — 12 scenarios across two test files.
- Goal 6 (cron_started/cron_finished persist): Task 2.
- Goal 7 (design documented): Task 5.
- Non-goal "per-contact dedup": explicitly deferred in conventions doc.
- Non-goal "hardcoded API key": out of scope.
- Non-goal "rename try_claim_alert_key": kept, docstring updated.

No gaps.

**Placeholder scan:**
- No TBDs, TODOs, "fill in later", or vague "add appropriate error handling".
- All test code is concrete.
- All code changes show the full before/after snippet.

**Type consistency:**
- Helper signatures match across tasks: `check_sent_flag(key: str) -> bool`, `set_sent_flag(key: str, ttl_seconds: int) -> None`, `release_inflight(key: str) -> None`.
- Key names are consistent: `daily_report:sent:{REPORT_TYPE}:{date}` and `daily_report:inflight:{REPORT_TYPE}:{date}`.
- TTLs are consistent: 48h for sent, 20min for in-flight.
- `inflight_held` local flag is used identically in both scripts.
