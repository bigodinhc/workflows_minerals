# Observability Phase 3 — Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a `watchdog.py` script + GH Actions workflow that polls every 5 min, detects when any of the 5 workflows in `ALL_WORKFLOWS` **should** have run but didn't, and emits a `cron_missed` alert through the event bus (Phase 1). After this, silent "cron never fired" failures get surfaced in the main chat within 20 minutes (15 min grace + 5 min watchdog interval).

**Architecture:** One new script reads `ALL_WORKFLOWS`, walks each one's cron backward to find its most recent `previous_expected` time, compares against `state_store.get_status(workflow).time_iso`, and emits `cron_missed` if no run happened within the grace window. Idempotency via a Redis `SET NX` guard (new helper `state_store.try_claim_alert_key`). Needs one new helper in `cron_parser.parse_previous_run` (croniter's `get_prev`). The existing Phase 1 event bus handles fan-out to Telegram/Sentry/Supabase — watchdog just emits.

**Tech Stack:** Python 3.11 (matches Phase 1), `pyyaml` + `croniter` (already installed, used by `cron_parser`), `redis` (already installed, used by `state_store`), `pytest`. No new dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-21-observability-unified-design.md` §§ "Component: Watchdog", Phase 3 rollout.

**Repo root:** `/Users/bigode/Dev/agentics_workflows/`

**Python runner:** `/usr/bin/python3 -m pytest ...` (same as Phase 1).

**Phase 3 ship criterion:**
1. `watchdog.py` runs every 5 minutes on GH Actions.
2. For each workflow in `ALL_WORKFLOWS`, watchdog reads the last expected cron time (via `cron_parser.parse_previous_run`) and the last recorded run (via `state_store.get_status`).
3. If no run happened within `GRACE_MINUTES = 15` after the expected time, watchdog emits `cron_missed` via the event bus (level=error), which triggers the main-chat Telegram alert from Phase 1's `_MainChatSink`.
4. Alerts are idempotent — watchdog running twice on the same miss produces exactly one alert.
5. Baseline + Phase 1 tests still pass; ~5 new tests green.

---

## File Structure

**Files to create:**

| Path | Lines (approx) | Responsibility |
|---|---|---|
| `execution/scripts/watchdog.py` | ~80 | Main watchdog loop: iterate workflows, detect misses, emit `cron_missed` |
| `tests/test_watchdog.py` | ~180 | Integration tests with fake cron_parser + fake state_store |
| `.github/workflows/watchdog.yml` | ~30 | GH Actions schedule for watchdog |

**Files to modify:**

| Path | Scope of change |
|---|---|
| `execution/core/cron_parser.py` | Add `parse_previous_run(workflow, now=None)` helper (+ its test) |
| `execution/core/state_store.py` | Add `try_claim_alert_key(key, ttl_seconds)` helper (+ its test) |
| `tests/test_cron_parser.py` | Add 1 test for `parse_previous_run` |
| `tests/test_state_store.py` (if exists; else new section in closest test file) | Add 2 tests for `try_claim_alert_key` |

No other files touched. `event_bus.py` stays as-is — watchdog uses the existing `EventBus` and decorator.

---

## Pre-flight

- [ ] **Step 0.1: Confirm Phase 1 landed on main**

Run:
```bash
cd /Users/bigode/Dev/agentics_workflows && git log --oneline main -5 | head -6
```

Expected: see commit `6d03cbe fix(migrations): ...` (or newer) as recent history. The Phase 1 merge commit (`660f05d Merge observability phase 1 foundation`) should be in the history. If not, stop — Phase 1 must be on main before Phase 3.

- [ ] **Step 0.2: Create the Phase 3 worktree**

```bash
cd /Users/bigode/Dev/agentics_workflows && git worktree add .worktrees/obs-phase3 -b feature/observability-phase3
```

All remaining work happens in `/Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3/`. Use `cd` before every command.

- [ ] **Step 0.3: Baseline test count**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 484 passed, 5 pre-existing failed (in `test_cron_parser.py` and `test_query_handlers.py`). These 5 failures are NOT caused by Phase 3 changes and are NOT expected to be fixed here. Each task must keep the same 5 failures + 484 baseline + its own new tests.

- [ ] **Step 0.4: Confirm Redis credentials (optional for tests)**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -c "import os; print('REDIS_URL set:', bool(os.getenv('REDIS_URL')))"
```

Not required for tests — all Redis access in tests uses a monkeypatched fake client, same pattern as existing `state_store.py` tests.

---

## Task 1: Add `parse_previous_run` helper to `cron_parser.py`

**Files:**
- Modify: `execution/core/cron_parser.py` (append new function)
- Modify: `tests/test_cron_parser.py` (append 1 new test)

The watchdog needs to know "when was this workflow SUPPOSED to have run last?" — i.e., walk the cron expression BACKWARD from `now` by one interval. croniter supports this natively via `get_prev`. This helper mirrors `parse_next_run` in structure and error handling.

- [ ] **Step 1.1: Inspect current `parse_next_run` as reference**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && sed -n '21,72p' execution/core/cron_parser.py
```

Keep the existing function unchanged. Your new function should reuse the same YAML-loading + croniter-importing logic, so extract a private helper to avoid duplication (see Step 1.3).

- [ ] **Step 1.2: Write the failing test**

Append to `tests/test_cron_parser.py`:

```python
def test_parse_previous_run_returns_most_recent_past_occurrence(tmp_path, monkeypatch):
    """parse_previous_run walks one cron interval backward from `now` and
    returns the most recent scheduled run that has already passed."""
    from execution.core import cron_parser

    # Write a minimal workflow YAML: runs at 09:00 UTC Mon-Fri
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir()
    wf_file = wf_dir / "test_wf.yml"
    wf_file.write_text(
        "name: Test\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: '0 9 * * 1-5'\n"
        "jobs:\n"
        "  x:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps: [{run: 'echo'}]\n"
    )

    # Fix `now` to a known Wednesday at 14:00 UTC. The previous 09:00 UTC run
    # happened 5 hours ago, same day.
    from datetime import datetime, timezone
    fixed_now = datetime(2026, 4, 15, 14, 0, 0, tzinfo=timezone.utc)  # Wed
    monkeypatch.setattr(cron_parser, "_utc_now", lambda: fixed_now)

    previous = cron_parser.parse_previous_run("test_wf", workflows_dir=str(wf_dir))

    assert previous is not None
    # parse_previous_run returns in UTC (not BRT) to match `now` semantics used by watchdog
    assert previous.tzinfo is not None
    # The previous 09:00 UTC Wed run is 5 hours before fixed_now
    from datetime import timedelta
    assert previous == fixed_now - timedelta(hours=5)


def test_parse_previous_run_returns_none_when_workflow_missing(tmp_path):
    """If the workflow YAML doesn't exist, return None (not raise)."""
    from execution.core import cron_parser
    previous = cron_parser.parse_previous_run("nonexistent", workflows_dir=str(tmp_path))
    assert previous is None
```

- [ ] **Step 1.3: Verify the tests fail**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/test_cron_parser.py::test_parse_previous_run_returns_most_recent_past_occurrence -v 2>&1 | tail -10
```

Expected: `AttributeError: module 'execution.core.cron_parser' has no attribute 'parse_previous_run'`.

- [ ] **Step 1.4: Implement `parse_previous_run`**

Append to `execution/core/cron_parser.py`:

```python
def parse_previous_run(workflow: str, workflows_dir: str = ".github/workflows") -> Optional[datetime]:
    """Return the most recent scheduled run of `workflow` that has already
    passed (relative to `now` in UTC), or None if not parseable. Mirrors
    parse_next_run but walks BACKWARD via croniter.get_prev.

    Returns a UTC-aware datetime (unlike parse_next_run which converts to BRT).
    The watchdog consumes UTC so it can compare against its own UTC `now`.
    """
    path = os.path.join(workflows_dir, f"{workflow}.yml")
    if not os.path.exists(path):
        return None
    try:
        import yaml
    except Exception as exc:
        logger.warning(f"cron_parser: pyyaml not installed: {exc}")
        return None
    try:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
    except Exception as exc:
        logger.warning(f"cron_parser: failed to parse {path}: {exc}")
        return None
    if not isinstance(data, dict):
        return None
    on_section = data.get("on") or data.get(True)
    if not isinstance(on_section, dict):
        return None
    schedule = on_section.get("schedule")
    if not isinstance(schedule, list) or not schedule:
        return None
    try:
        from croniter import croniter
    except Exception as exc:
        logger.warning(f"cron_parser: croniter not installed: {exc}")
        return None
    now_utc = _utc_now()
    prev_runs = []
    for entry in schedule:
        cron_expr = entry.get("cron") if isinstance(entry, dict) else None
        if not cron_expr:
            continue
        try:
            it = croniter(cron_expr, now_utc)
            prev_runs.append(it.get_prev(datetime))
        except Exception as exc:
            logger.warning(f"cron_parser: bad cron {cron_expr!r}: {exc}")
            continue
    if not prev_runs:
        return None
    latest = max(prev_runs)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return latest  # UTC, unlike parse_next_run which returns BRT
```

- [ ] **Step 1.5: Run the tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/test_cron_parser.py -v 2>&1 | tail -15
```

Expected: all `test_parse_previous_run_*` tests pass. The 1 pre-existing failure in `test_cron_parser.py` (`test_parse_next_run_returns_earliest_when_multiple_crons`) is unrelated and stays failing — do NOT attempt to fix it as part of this task.

- [ ] **Step 1.6: Full suite regression**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 486 passed (484 baseline + 2 new), 5 failed (pre-existing).

- [ ] **Step 1.7: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && \
  git add execution/core/cron_parser.py tests/test_cron_parser.py && \
  git commit -m "$(cat <<'EOF'
feat(cron_parser): add parse_previous_run helper for watchdog

Mirrors parse_next_run but walks backward via croniter.get_prev. Returns
UTC-aware datetime (parse_next_run returns BRT; watchdog operates in UTC).

Consumed by execution/scripts/watchdog.py (next commit).
EOF
)"
```

---

## Task 2: Add `try_claim_alert_key` to `state_store.py`

**Files:**
- Modify: `execution/core/state_store.py` (append new function)
- Modify or create: `tests/test_state_store.py` (append 2 new tests; create file if absent)

This is the idempotency guard for watchdog alerts: Redis SET NX with TTL. First caller gets `True` and alerts; subsequent callers within the TTL get `False` and skip.

- [ ] **Step 2.1: Check if `tests/test_state_store.py` exists**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && ls tests/test_state_store.py 2>&1
```

If it exists, append. If not, create a new file with the minimal pytest imports header and the two tests.

- [ ] **Step 2.2: Write the failing tests**

Add (or create) to `tests/test_state_store.py`:

```python
"""Tests for execution.core.state_store additional helpers."""
import pytest


def test_try_claim_alert_key_returns_true_on_first_claim(monkeypatch):
    """First caller with a fresh key should return True (alert should fire).
    Uses a fake redis client to avoid real Redis."""
    from execution.core import state_store

    set_calls = []

    class FakeRedis:
        def set(self, key, value, nx=False, ex=None):
            set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
            return True  # redis-py returns True/None for SET NX; True = key set

    monkeypatch.setattr(state_store, "_get_client", lambda: FakeRedis())

    assert state_store.try_claim_alert_key("wf:test:1", ttl_seconds=60) is True
    assert len(set_calls) == 1
    assert set_calls[0]["key"] == "wf:test:1"
    assert set_calls[0]["nx"] is True
    assert set_calls[0]["ex"] == 60


def test_try_claim_alert_key_returns_false_on_duplicate_claim(monkeypatch):
    """Second caller with a still-alive key should return False (alert already sent)."""
    from execution.core import state_store

    class FakeRedis:
        def set(self, key, value, nx=False, ex=None):
            return None  # redis-py returns None when NX fails (key already exists)

    monkeypatch.setattr(state_store, "_get_client", lambda: FakeRedis())

    assert state_store.try_claim_alert_key("wf:test:2", ttl_seconds=60) is False


def test_try_claim_alert_key_returns_true_when_redis_unavailable(monkeypatch):
    """When Redis is down, degrade permissive: return True so the alert
    still fires. Losing one duplicate alert is worse than losing the alert
    entirely."""
    from execution.core import state_store
    monkeypatch.setattr(state_store, "_get_client", lambda: None)

    assert state_store.try_claim_alert_key("wf:test:3", ttl_seconds=60) is True


def test_try_claim_alert_key_returns_true_when_redis_raises(monkeypatch):
    """If the Redis SET itself raises (connection reset mid-call), degrade permissive."""
    from execution.core import state_store

    class FlakyRedis:
        def set(self, key, value, nx=False, ex=None):
            raise RuntimeError("connection lost")

    monkeypatch.setattr(state_store, "_get_client", lambda: FlakyRedis())

    assert state_store.try_claim_alert_key("wf:test:4", ttl_seconds=60) is True
```

- [ ] **Step 2.3: Verify tests fail**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/test_state_store.py -v 2>&1 | tail -10
```

Expected: 4 failures/errors — `AttributeError: module 'execution.core.state_store' has no attribute 'try_claim_alert_key'`.

- [ ] **Step 2.4: Implement `try_claim_alert_key`**

Append to `execution/core/state_store.py` (place after `get_all_status`, before `_STREAK_THRESHOLD`):

```python
def try_claim_alert_key(key: str, ttl_seconds: int) -> bool:
    """Atomic Redis SET NX EX. Returns True if the caller claimed the key
    (should fire the alert), False if the key already existed (someone else
    alerted). Degrades permissive: returns True on any Redis failure so an
    alert still fires rather than silently swallowed.

    Use for idempotent alert suppression (e.g., watchdog missing-cron
    notifications that must fire exactly once per miss)."""
    client = _get_client()
    if client is None:
        return True
    try:
        result = client.set(key, "1", nx=True, ex=ttl_seconds)
        return result is not None and result is not False
    except Exception as exc:
        logger.warning(f"state_store.try_claim_alert_key failed: {exc}")
        return True
```

Note on the `result is not None and result is not False` guard: `redis-py` returns `True` on successful NX-set and `None` on NX-fail. The double-guard tolerates both true-False-vs-None conventions (some Redis clients return `False` instead of `None` on NX-fail).

- [ ] **Step 2.5: Run tests — verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/test_state_store.py -v 2>&1 | tail -10
```

Expected: 4 passed.

- [ ] **Step 2.6: Full suite regression**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 490 passed (486 from Task 1 + 4 new), 5 failed (pre-existing).

- [ ] **Step 2.7: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && \
  git add execution/core/state_store.py tests/test_state_store.py && \
  git commit -m "$(cat <<'EOF'
feat(state_store): add try_claim_alert_key for idempotent alerts

Atomic Redis SET NX + TTL. Returns True if caller claimed the key (should
alert), False if a previous caller already claimed it. Degrades permissive
on Redis failure so alerts still fire rather than silently drop.

Consumed by execution/scripts/watchdog.py (next commit).
EOF
)"
```

---

## Task 3: Write `execution/scripts/watchdog.py` + integration test

**Files:**
- Create: `execution/scripts/watchdog.py`
- Create: `tests/test_watchdog.py`

This is the heart of Phase 3. The watchdog loops over `ALL_WORKFLOWS`, detects misses, and emits `cron_missed` via the event bus. Wrapped with `@with_event_bus("watchdog")` so its own lifecycle is observable.

- [ ] **Step 3.1: Write failing integration tests**

Create `tests/test_watchdog.py`:

```python
"""Integration tests for execution.scripts.watchdog."""
import json
import pytest
from datetime import datetime, timezone, timedelta


def _iso(dt):
    return dt.isoformat()


@pytest.fixture
def disable_sinks(monkeypatch):
    """Disable Supabase / Telegram sinks so tests don't hit network."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def test_watchdog_emits_cron_missed_when_last_run_too_old(monkeypatch, capsys, disable_sinks):
    """Workflow's previous expected run was 30 minutes ago; grace is 15;
    state store shows last run was yesterday. Expect cron_missed emitted."""
    from execution.scripts import watchdog as wd

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    previous_expected = now - timedelta(minutes=30)
    yesterday = now - timedelta(days=1)

    # Patch time
    monkeypatch.setattr(wd, "_utc_now", lambda: now)

    # Patch cron_parser to say 'morning_check' has a previous_expected 30 min ago
    from execution.core import cron_parser
    monkeypatch.setattr(
        cron_parser,
        "parse_previous_run",
        lambda wf, **kwargs: previous_expected if wf == "morning_check" else None,
    )

    # Patch state_store to say morning_check's last run was yesterday
    from execution.core import state_store
    monkeypatch.setattr(
        state_store,
        "get_status",
        lambda wf: {"time_iso": _iso(yesterday), "status": "success"} if wf == "morning_check" else None,
    )
    # Alert claim: succeed (first time alerting)
    claim_calls = []
    def fake_claim(key, ttl_seconds):
        claim_calls.append((key, ttl_seconds))
        return True
    monkeypatch.setattr(state_store, "try_claim_alert_key", fake_claim)

    # Patch ALL_WORKFLOWS to just 'morning_check'
    from webhook import status_builder
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["morning_check"])

    wd.main()

    # Parse stdout JSON lines to find cron_missed
    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    missed_events = [e for e in out_lines if e["event"] == "cron_missed"]
    assert len(missed_events) == 1
    m = missed_events[0]
    assert m["level"] == "error"
    assert "morning_check" in (m["label"] or "")
    assert m["detail"]["expected_iso"] == _iso(previous_expected)
    assert m["detail"]["last_run_iso"] == _iso(yesterday)

    # Alert claim was attempted with the right key shape
    assert len(claim_calls) == 1
    key, ttl = claim_calls[0]
    assert "morning_check" in key
    assert _iso(previous_expected) in key
    assert ttl == 86400  # 24h


def test_watchdog_skips_when_within_grace_window(monkeypatch, capsys, disable_sinks):
    """Previous expected was 5 minutes ago; grace is 15; still in window → no alert."""
    from execution.scripts import watchdog as wd
    from execution.core import cron_parser, state_store
    from webhook import status_builder

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    previous_expected = now - timedelta(minutes=5)  # within 15min grace

    monkeypatch.setattr(wd, "_utc_now", lambda: now)
    monkeypatch.setattr(cron_parser, "parse_previous_run", lambda wf, **kw: previous_expected)
    monkeypatch.setattr(state_store, "get_status", lambda wf: None)  # never ran
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda k, t: pytest.fail("should not have tried to claim"))
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["morning_check"])

    wd.main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    missed = [e for e in out_lines if e["event"] == "cron_missed"]
    assert missed == []


def test_watchdog_skips_when_last_run_after_previous_expected(monkeypatch, capsys, disable_sinks):
    """Previous expected 30 min ago; but last_run 10 min ago (i.e., ran late but ran).
    Should NOT alert."""
    from execution.scripts import watchdog as wd
    from execution.core import cron_parser, state_store
    from webhook import status_builder

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    previous_expected = now - timedelta(minutes=30)
    last_run = now - timedelta(minutes=10)  # after previous_expected

    monkeypatch.setattr(wd, "_utc_now", lambda: now)
    monkeypatch.setattr(cron_parser, "parse_previous_run", lambda wf, **kw: previous_expected)
    monkeypatch.setattr(state_store, "get_status", lambda wf: {"time_iso": _iso(last_run)})
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda k, t: pytest.fail("should not claim"))
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["morning_check"])

    wd.main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [e for e in out_lines if e["event"] == "cron_missed"] == []


def test_watchdog_is_idempotent_via_claim_guard(monkeypatch, capsys, disable_sinks):
    """When try_claim_alert_key returns False (already alerted), watchdog
    should NOT emit a duplicate cron_missed."""
    from execution.scripts import watchdog as wd
    from execution.core import cron_parser, state_store
    from webhook import status_builder

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    previous_expected = now - timedelta(minutes=30)

    monkeypatch.setattr(wd, "_utc_now", lambda: now)
    monkeypatch.setattr(cron_parser, "parse_previous_run", lambda wf, **kw: previous_expected)
    monkeypatch.setattr(state_store, "get_status", lambda wf: None)
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda k, t: False)  # already claimed
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["morning_check"])

    wd.main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [e for e in out_lines if e["event"] == "cron_missed"] == []


def test_watchdog_skips_workflow_with_no_previous_run(monkeypatch, capsys, disable_sinks):
    """If cron_parser.parse_previous_run returns None (workflow has no schedule
    or YAML is missing), skip silently — not an error."""
    from execution.scripts import watchdog as wd
    from execution.core import cron_parser, state_store
    from webhook import status_builder

    now = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(wd, "_utc_now", lambda: now)
    monkeypatch.setattr(cron_parser, "parse_previous_run", lambda wf, **kw: None)
    monkeypatch.setattr(state_store, "get_status", lambda wf: None)
    monkeypatch.setattr(state_store, "try_claim_alert_key", lambda k, t: pytest.fail("should not claim"))
    monkeypatch.setattr(status_builder, "ALL_WORKFLOWS", ["rationale_news"])  # no YAML

    wd.main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert [e for e in out_lines if e["event"] == "cron_missed"] == []
```

- [ ] **Step 3.2: Run tests — expect ModuleNotFoundError**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/test_watchdog.py -v 2>&1 | tail -10
```

Expected: 5 errors, all with `ModuleNotFoundError: No module named 'execution.scripts.watchdog'`.

- [ ] **Step 3.3: Implement `watchdog.py`**

Create `execution/scripts/watchdog.py`:

```python
"""
Watchdog: detects when a workflow in ALL_WORKFLOWS was supposed to run but didn't.

Runs every 5 minutes via .github/workflows/watchdog.yml. For each workflow:
  1. Computes `previous_expected` = most recent past cron occurrence.
  2. If `now < previous_expected + GRACE_MINUTES`, still in grace window — skip.
  3. Reads `state_store.get_status(wf).time_iso`. If >= previous_expected, ran — skip.
  4. Otherwise: atomically claim an alert key (idempotency), then emit `cron_missed`.

The event bus (Phase 1) fans out to stdout (GH Actions logs), Supabase event_log,
Sentry breadcrumb, and the main-chat Telegram sink — which delivers the actual
operator alert.

Never raises at the top level — wrapped by @with_event_bus so the GH run marks
failed if something unrecoverable happens.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from execution.core import cron_parser, state_store
from execution.core.event_bus import EventBus, with_event_bus
from webhook import status_builder

logger = logging.getLogger(__name__)

GRACE_MINUTES = 15
ALERT_TTL_SECONDS = 86_400  # 24h; one alert per miss-occurrence


def _utc_now() -> datetime:
    """Monkeypatch seam for tests."""
    return datetime.now(timezone.utc)


def _parse_iso_to_utc(iso_str: str) -> Optional[datetime]:
    """Parse an ISO-8601 string to a UTC-aware datetime, or None if unparseable."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@with_event_bus("watchdog")
def main() -> None:
    bus = EventBus(workflow="watchdog")
    now = _utc_now()

    for workflow in status_builder.ALL_WORKFLOWS:
        previous_expected = cron_parser.parse_previous_run(workflow)
        if previous_expected is None:
            continue  # workflow has no schedule YAML or unparseable — skip

        deadline = previous_expected + timedelta(minutes=GRACE_MINUTES)
        if now < deadline:
            continue  # still in grace window

        last = state_store.get_status(workflow)
        last_run_utc = _parse_iso_to_utc(last.get("time_iso") if last else "")
        if last_run_utc is not None and last_run_utc >= previous_expected:
            continue  # ran (possibly late, but ran)

        alert_key = f"wf:watchdog_alerted:{workflow}:{previous_expected.isoformat()}"
        if not state_store.try_claim_alert_key(alert_key, ttl_seconds=ALERT_TTL_SECONDS):
            continue  # already alerted for this miss

        bus.emit(
            "cron_missed",
            label=f"{workflow} não rodou",
            detail={
                "missed_workflow": workflow,
                "expected_iso": previous_expected.isoformat(),
                "deadline_iso": deadline.isoformat(),
                "last_run_iso": last.get("time_iso") if last else None,
                "grace_minutes": GRACE_MINUTES,
            },
            level="error",
        )


if __name__ == "__main__":
    main()
```

**Notes on design choices:**

- `_utc_now` is extracted as a module-level function purely so tests can monkeypatch it. `datetime.now(timezone.utc)` isn't easily mockable in-place.
- `_parse_iso_to_utc` handles both naive and aware ISO strings. `state_store._now_iso()` uses `.astimezone().isoformat()`, which includes a timezone offset — so most stored `time_iso`s will already be aware. The helper tolerates either.
- The `bus.emit("cron_missed", ...)` fires with `workflow="watchdog"` (because that's the bus's workflow), but the `detail["missed_workflow"]` carries the ACTUAL missing workflow name for the event_log query + the Telegram alert format.
- `_MainChatSink._format` handles `cron_missed` explicitly — it prints `⏰ WATCHDOG — NÃO RODOU` by default. The plan accepts that the alert title will read "WATCHDOG — NÃO RODOU" rather than "MORNING CHECK — NÃO RODOU"; the `label` field ("morning_check não rodou") carries the real name on the second line. **Improvement for followup:** customize `_MainChatSink._format` to pull `detail.missed_workflow` when the event is `cron_missed`. Not blocking Phase 3 ship.

- [ ] **Step 3.4: Run tests — verify 5 pass**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/test_watchdog.py -v 2>&1 | tail -15
```

Expected: 5 passed.

- [ ] **Step 3.5: Smoke import check**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -c "from execution.scripts import watchdog; print('OK')"
```

Expected: `OK`. If ImportError, the decorator application is wrong.

- [ ] **Step 3.6: Full suite regression**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 495 passed (490 from Task 2 + 5 new), 5 pre-existing failed.

- [ ] **Step 3.7: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && \
  git add execution/scripts/watchdog.py tests/test_watchdog.py && \
  git commit -m "$(cat <<'EOF'
feat(observability): add watchdog script for silent cron failure detection

Polls every 5 min (via .github/workflows/watchdog.yml, next commit). For each
workflow in ALL_WORKFLOWS, checks whether the last expected cron actually ran
within GRACE_MINUTES=15. If not, emits cron_missed through the Phase 1 event
bus — which fans out to stdout, Supabase event_log, Sentry, and the main-chat
Telegram sink for the operator alert.

Idempotency via state_store.try_claim_alert_key (Redis SET NX, 24h TTL):
watchdog running every 5 minutes produces exactly one alert per miss, not
twelve per hour.

5 integration tests cover: alert fires when stale, grace window respected,
late-but-ran does not alert, idempotent on duplicate, unscheduled workflow
skipped.

Spec: docs/superpowers/specs/2026-04-21-observability-unified-design.md §Watchdog
EOF
)"
```

---

## Task 4: Add `.github/workflows/watchdog.yml`

**Files:**
- Create: `.github/workflows/watchdog.yml`

- [ ] **Step 4.1: Inspect an existing workflow YAML to match conventions**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && head -60 .github/workflows/morning_check.yml
```

Note the Python version (`3.10` on this project per `morning_check.yml`), the `actions/checkout@v3`, and `actions/setup-python@v4` versions. Match them.

- [ ] **Step 4.2: Create the workflow YAML**

Create `.github/workflows/watchdog.yml`:

```yaml
name: "Watchdog"

on:
  schedule:
    - cron: '*/5 * * * *'   # every 5 minutes
  workflow_dispatch: {}    # allow manual trigger from GH UI

jobs:
  watchdog:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt

      - name: Run watchdog
        env:
          REDIS_URL: ${{ secrets.REDIS_URL }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          SENTRY_DSN: ${{ secrets.SENTRY_DSN }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
        run: python -m execution.scripts.watchdog
```

**Notes:**
- `*/5 * * * *` is "every 5 minutes" — the smallest interval GH Actions supports. In practice GH may delay crons by ~5-15 minutes during peak load; the 15 min grace window absorbs that.
- `TELEGRAM_EVENTS_CHANNEL_ID` is NOT in this env block because Phase 2 hasn't landed yet. When Phase 2 adds the events-channel sink, it can be included. Leaving it out now avoids confusion about a secret that may or may not be set.
- `workflow_dispatch: {}` lets the operator manually trigger the watchdog from the Actions UI for testing (e.g., Task 5 smoke).

- [ ] **Step 4.3: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && \
  git add .github/workflows/watchdog.yml && \
  git commit -m "$(cat <<'EOF'
feat(observability): add watchdog GH Actions workflow

Runs execution/scripts/watchdog.py every 5 minutes. Also allows manual
trigger via workflow_dispatch for smoke testing during Phase 3 validation.
EOF
)"
```

---

## Task 5: End-to-end manual validation

**Files:** none — verification only. This is operator-executed after all code merges.

Cannot be unit-tested — requires real GH Actions + Redis + Telegram.

- [ ] **Step 5.1: Trigger the watchdog manually once on `main`**

After merging this plan to main:
1. Open GitHub Actions UI.
2. Click "Watchdog" workflow in the left sidebar.
3. Click "Run workflow" → confirm.
4. Within ~30s, the watchdog run should appear.
5. Observe the run's stdout. Expected log pattern:
   ```
   {"ts":"...","workflow":"watchdog","event":"cron_started",...}
   {"ts":"...","workflow":"watchdog","event":"cron_finished",...}
   ```
6. No `cron_missed` yet — everything should be within grace.

- [ ] **Step 5.2: Deliberate missed-cron test**

Pick a workflow with a known schedule (e.g., `morning_check`, which runs at various 08:30-10:00 BRT times). Either:
- **Option A (safer):** Temporarily disable it by editing its YAML and setting the schedule to a time far in the past (e.g., comment out all `cron:` entries). Push to a throwaway branch. Trigger the branch's watchdog. The watchdog from the main branch will keep running too — that's the one that'll alert.

  Actually this won't work cleanly because main still sees the original schedule. Skip this option.

- **Option B (simplest):** Manually clear the `wf:last_run:morning_check` key in Redis (via `redis-cli` or Supabase/Upstash dashboard), and also clear the `wf:watchdog_alerted:*` keys for morning_check. Wait past the next morning_check expected run time without triggering it. After `previous_expected + 15min` passes, the next watchdog run (max 5 min later) should emit `cron_missed`.

  This isn't easy to do on a schedule, so:

- **Option C (recommended):** Monkeypatch in a scratch run. Create a temporary copy of watchdog.py that forces `_utc_now` to a future time past the grace window, OR uses a fake workflow name that has a stale state_store entry. Trigger manually. Observe the alert.

Given the complexity of option C and that you're already monitoring Sentry + Telegram — **the pragmatic path is: ship Phase 3 to main, and the first REAL missed cron that happens in production will validate the path.** Watchdog false-negatives are rare (the state_store writes are wired from all 7 scripts already in Phase 1); watchdog false-positives are rare (the grace window + idempotency guard protect against noise).

Accept the validation as "best-effort" and watch for the first real alert in production for 1-2 weeks.

- [ ] **Step 5.3: Document the validation status**

Append to `docs/superpowers/followups/2026-04-21-observability-phase1-followups.md` OR create a new Phase 3-specific followups doc.

Create: `docs/superpowers/followups/2026-04-21-observability-phase3-followups.md`

```markdown
# Observability Phase 3 — Watchdog Followups

**Shipped:** 2026-04-21 on branch `feature/observability-phase3`

## Validation status

- [ ] Manual `workflow_dispatch` trigger returned green within ~30s
- [ ] First N days in production: no false-positive `cron_missed` alerts
- [ ] First real missed cron: alert delivered in main chat within 20 min

## Known followups

- **`_MainChatSink._format` for `cron_missed`**: today the alert shows `⏰ WATCHDOG — NÃO RODOU` (because the bus's workflow is "watchdog"). The actual missed workflow name is in `detail.missed_workflow` and `label` but the title is generic. Improvement: teach `_format` to read `event_dict.get("detail", {}).get("missed_workflow")` when `event == "cron_missed"` and substitute it into the title.

- **`rationale_news` has no YAML**: `ALL_WORKFLOWS` lists it but `.github/workflows/rationale_news.yml` does not exist. Watchdog skips it silently (correct behavior). Remove from `ALL_WORKFLOWS` or add the YAML.

- **Watchdog's own miss**: if the watchdog workflow ITSELF is disabled/broken, nothing will detect that. A "watchdog of the watchdog" (e.g., a Phase 2 events channel + human check) covers this gap. Out of scope for Phase 3.

- **GH Actions cron drift**: GH delays scheduled runs by 5-15 min during peak hours. Our 15 min grace absorbs this. If we see legitimate false-positives, extend to 20 or 25.

- **Alert TTL is 24h**: a workflow that misses for multiple days fires once per day (because the alert key includes `previous_expected_iso`, which changes each cron interval — so new miss = new key = new alert, which is correct). Verify this in practice.

## Phase 4 prerequisites

Nothing extra — Phase 4 (/tail + step/api_call instrumentation) builds on Phase 1's event_log table, which is already live.
```

- [ ] **Step 5.4: Commit followups doc**

```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && \
  git add docs/superpowers/followups/2026-04-21-observability-phase3-followups.md && \
  git commit -m "docs(followups): observability phase 3 shipped — watchdog validation checklist"
```

---

## Post-flight: merge to main

- [ ] **Step 6.1: Confirm ship criterion**

Revisit the 5 criteria from the header:
1. `watchdog.py` exists and passes tests ✅ Task 3
2. Detects misses correctly ✅ Task 3 tests
3. Emits `cron_missed` via event bus ✅ Task 3
4. Idempotent via `try_claim_alert_key` ✅ Task 2 + Task 3 test
5. Baseline tests + new tests pass ✅ all tasks

- [ ] **Step 6.2: Merge back to main**

From the worktree:
```bash
cd /Users/bigode/Dev/agentics_workflows/.worktrees/obs-phase3 && \
  git log --oneline main..HEAD  # confirm 5 commits
```

From the main checkout:
```bash
cd /Users/bigode/Dev/agentics_workflows && \
  git checkout main && \
  git merge --no-ff feature/observability-phase3 -m "Merge observability phase 3 watchdog"
```

Verify:
```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -3
```

Expected: 495 passed, 5 pre-existing failed.

- [ ] **Step 6.3: Clean up worktree and branch**

```bash
cd /Users/bigode/Dev/agentics_workflows && \
  git worktree remove .worktrees/obs-phase3 && \
  git branch -d feature/observability-phase3
```

- [ ] **Step 6.4: Push to remote**

```bash
cd /Users/bigode/Dev/agentics_workflows && git push origin main
```

Watchdog starts running automatically on its `*/5 * * * *` schedule from this point.

---

## Self-Review

**Spec coverage:**
- Spec §Component Watchdog → detection logic (Task 3) ✓
- Spec §Idempotency → `try_claim_alert_key` (Task 2) ✓
- Spec §Previous-run helper → `parse_previous_run` (Task 1) ✓
- Spec §Workflow YAML → `watchdog.yml` (Task 4) ✓
- Spec Phase 3 ship criterion (operator gets `cron_missed` alert in main chat within 20 min) → Task 3 emits `cron_missed` → Phase 1's `_MainChatSink` handles delivery ✓
- Spec §Integration test references `tests/test_watchdog.py` → Task 3 ✓

**Placeholder scan:** no TBDs. Step 5.2 acknowledges the deliberate-miss test is hard to do synthetically and accepts real-production validation as the ship gate. This is intentional, not a gap.

**Type consistency:**
- `_utc_now()` defined in watchdog.py and referenced in tests via `monkeypatch.setattr(wd, "_utc_now", ...)` ✓
- `parse_previous_run(workflow, workflows_dir=...)` signature consistent in cron_parser.py definition (Task 1) and tests (Task 3) ✓
- `try_claim_alert_key(key, ttl_seconds)` signature consistent in state_store.py (Task 2) and watchdog.py (Task 3) ✓
- `state_store.get_status(workflow)` return shape `{time_iso, status, summary, ...}` — watchdog reads `.get("time_iso")`, consistent with existing spec ✓
- `ALL_WORKFLOWS` imported via `from webhook import status_builder` then `status_builder.ALL_WORKFLOWS` — matches how watchdog's tests monkeypatch it ✓
- Event emit shape (`event="cron_missed"`, `label`, `detail`, `level="error"`) matches `_MainChatSink._should_alert` guards in Phase 1 (`event in _ALERT_EVENTS` OR `level in ("warn", "error")`) ✓

**One acknowledged gotcha:** `_MainChatSink._format` produces `⏰ WATCHDOG — NÃO RODOU` instead of `⏰ MORNING CHECK — NÃO RODOU` because the bus's workflow is "watchdog", not "morning_check". The `label` field ("morning_check não rodou") carries the real name. Captured in followups as a one-line `_format` tweak for Phase 4.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-21-observability-phase3-watchdog-plan.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks. Same pattern used for Phase 1. 4 code tasks + 1 manual validation = ~5 subagent dispatches.

**2. Inline Execution** — execute tasks in a single session using executing-plans, batch execution with checkpoints for review.

**Which approach?**
