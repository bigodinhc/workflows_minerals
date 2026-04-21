# Observability Phase 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `event_bus` module + `@with_event_bus` decorator + Sentry auto-init + main-chat crash alerts across all 7 execution scripts. After this, every uncaught exception produces a Telegram alert + Sentry capture, regardless of where in the script lifecycle it occurs.

**Architecture:** New module `execution/core/event_bus.py` with never-raise `emit()` that fan-outs to 4 sinks (stdout, Supabase `event_log`, Sentry breadcrumb, main-chat Telegram for errors only). Decorator `@with_event_bus` wraps each script's `main()` to emit lifecycle events (`cron_started` / `cron_finished` / `cron_crashed`) and auto-init Sentry. Phase 1 does **not** include the events channel sink (Phase 2), watchdog (Phase 3), or `/tail` (Phase 4) — those are separate plans that ship after this one.

**Tech Stack:** Python 3.11, pytest, Supabase (`supabase-py`), `sentry_sdk`, existing `TelegramClient`.

**Spec reference:** `docs/superpowers/specs/2026-04-21-observability-unified-design.md` — Sections 1-3, 6 are the authoritative contract for Phase 1.

**Repo root:** `/Users/bigode/Dev/agentics_workflows/` (recently renamed from `Antigravity WF ` — the old trailing-space path is gone, new path has no spaces).

**Python runner note:** use `/usr/bin/python3` for test invocations (`python` alias not available in this repo's venv shebangs). Command: `/usr/bin/python3 -m pytest tests/test_event_bus.py -v`.

**Phase 1 ship criterion:**
1. All 7 execution scripts emit `cron_started` / `cron_finished` / `cron_crashed` events.
2. Sentry auto-initialized for all 7 (4 that don't today, 3 that already do — redundant but harmless).
3. Main chat receives a distinct alert message for every script crash (regardless of lifecycle stage).
4. Baseline tests still pass; 11 new tests green.
5. Supabase `event_log` table exists with the agreed schema and receives rows from manual smoke run.

---

## File Structure

**Files to create:**

| Path | Lines (approx) | Responsibility |
|---|---|---|
| `execution/core/event_bus.py` | ~300 | `EventBus` class, 4 sinks, `@with_event_bus` decorator |
| `tests/test_event_bus.py` | ~400 | 11 unit tests covering emit fan-out, sink isolation, decorator lifecycle |
| `supabase/migrations/20260421_event_log.sql` | ~30 | Table + indexes + CHECK constraint |

**Files to modify:**

| Path | Scope of change |
|---|---|
| `execution/core/progress_reporter.py` | Fix 2 — `fail()` pushes a distinct Telegram message in addition to editing the card |
| `tests/test_progress_reporter.py` (if exists) or add to `tests/test_delivery_reporter.py` | 1 new test: `test_progress_fail_edits_card_and_sends_new_message` |
| `execution/scripts/morning_check.py` | Add `@with_event_bus("morning_check")` decorator |
| `execution/scripts/send_daily_report.py` | Add `@with_event_bus("daily_report")` decorator |
| `execution/scripts/send_news.py` | Add `@with_event_bus("market_news")` decorator |
| `execution/scripts/rebuild_dedup.py` | Add `@with_event_bus("rebuild_dedup")` decorator |
| `execution/scripts/platts_reports.py` | Add `@with_event_bus("platts_reports")`; remove now-redundant `init_sentry(__name__)` call |
| `execution/scripts/platts_ingestion.py` | Add `@with_event_bus("platts_ingestion")`; remove redundant `init_sentry(__name__)` |
| `execution/scripts/baltic_ingestion.py` | Add `@with_event_bus("baltic_ingestion")`; remove redundant `init_sentry(__name__)` |

No other files touched. `delivery_reporter.py`, `state_store.py`, `cron_parser.py`, `status_builder.py`, `commands.py` stay as-is for Phase 1.

---

## Pre-flight

- [ ] **Step 0.1: Verify working tree + branch**

Run:
```bash
cd /Users/bigode/Dev/agentics_workflows && git status --short && git rev-parse --abbrev-ref HEAD
```

Expected: on `main` (or a feature branch). Uncommitted changes in `.next/`, `AGENT.md`, `docs/superpowers/specs/`, `docs/superpowers/plans/`, `tsconfig.tsbuildinfo`, `execution/scripts/inspect_platts.py` are fine (pre-existing noise from prior sessions). **No uncommitted changes to any of the files listed in "Files to modify" above** — if you find any, stop and investigate.

- [ ] **Step 0.2: Confirm spec file exists**

Run:
```bash
ls -la /Users/bigode/Dev/agentics_workflows/docs/superpowers/specs/2026-04-21-observability-unified-design.md
```

Expected: file exists. If missing, the spec wasn't committed — stop and recover.

- [ ] **Step 0.3: Baseline test count**

Run:
```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```

Expected: all tests pass. Record the exact count (at session start of prior work: 70 in `test_delivery_reporter.py` alone; full suite count may differ). Baseline is whatever passes now — each task in this plan must keep baseline green + add its own new tests.

- [ ] **Step 0.4: Confirm Supabase credentials available**

Run:
```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -c "import os; print('URL set:', bool(os.getenv('SUPABASE_URL'))); print('KEY set:', bool(os.getenv('SUPABASE_KEY')))"
```

Expected: both True **if your shell has the env loaded** (likely via `.env` or direnv). If both False, the tests will still pass (Supabase sink is optional + gracefully no-ops) but the Task 10 smoke test will be incomplete. If you have `.env`, `source .env` or use `export $(cat .env | xargs)` as appropriate.

- [ ] **Step 0.5: Confirm the event_log table status**

The spec flagged an open question: does `event_log` already exist? Check before applying the migration.

Run (against your Supabase project, via SQL editor or `psql`):
```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'event_log'
ORDER BY ordinal_position;
```

- If zero rows: table doesn't exist → Task 1 creates it from scratch.
- If rows exist: compare to the target schema below. Any missing columns (`trace_id`, `parent_run_id`, `pod`) need `ALTER TABLE ADD COLUMN ... NULL`. Adjust Task 1's migration to an `ALTER` instead of `CREATE` in that case.

---

## Task 1: Create `event_log` migration

**Files:**
- Create: `supabase/migrations/20260421_event_log.sql`

This task creates the table schema. No Python test — verification is by SQL inspection.

- [ ] **Step 1.1: Write the migration**

Create `supabase/migrations/20260421_event_log.sql`:

```sql
-- Phase 1 observability: event_log table
-- Referenced by execution/core/event_bus.py _SupabaseSink and webhook/bot/routers/commands.py /tail (Phase 4).

CREATE TABLE IF NOT EXISTS event_log (
  id            BIGSERIAL PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  workflow      TEXT NOT NULL,
  run_id        TEXT NOT NULL,
  trace_id      TEXT,
  parent_run_id TEXT,
  level         TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
  event         TEXT NOT NULL,
  label         TEXT,
  detail        JSONB,
  pod           TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_log_workflow_ts ON event_log (workflow, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_run_id      ON event_log (run_id);
CREATE INDEX IF NOT EXISTS idx_event_log_trace_id    ON event_log (trace_id);

-- TTL cleanup: rows older than 30 days deleted nightly.
-- If pg_cron is not enabled on this Supabase project, defer this to a manual cleanup
-- script (see Phase 4 followup). For now, leave as a comment for operator awareness:
-- SELECT cron.schedule('event_log_ttl', '0 3 * * *',
--   $$DELETE FROM event_log WHERE ts < NOW() - INTERVAL '30 days'$$);
```

**Alternative path (if table already exists with a different schema per Step 0.5):** write an `ALTER` migration instead. Example for adding missing columns:

```sql
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS trace_id TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS parent_run_id TEXT;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS pod TEXT;
CREATE INDEX IF NOT EXISTS idx_event_log_trace_id ON event_log (trace_id);
```

- [ ] **Step 1.2: Apply the migration**

If Supabase CLI is set up locally:
```bash
cd /Users/bigode/Dev/agentics_workflows && supabase db push
```

Otherwise, copy the SQL into the Supabase dashboard SQL editor and run it.

- [ ] **Step 1.3: Verify schema**

```sql
SELECT column_name, data_type, is_nullable FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'event_log' ORDER BY ordinal_position;
```

Expected: 11 columns matching the schema above.

- [ ] **Step 1.4: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add supabase/migrations/20260421_event_log.sql
git commit -m "$(cat <<'EOF'
feat(observability): add event_log table migration

Phase 1 of the unified observability spec. Table receives one row per emit()
from the forthcoming event_bus module — cron lifecycle events, step progress,
api_call results. Indexed on (workflow, ts desc), run_id, trace_id for
fast /tail queries in Phase 4. 30-day TTL via pg_cron planned as followup.

Spec: docs/superpowers/specs/2026-04-21-observability-unified-design.md
EOF
)"
```

Expected: commit succeeds.

---

## Task 2: Create `event_bus.py` core + `_StdoutSink`

**Files:**
- Create: `execution/core/event_bus.py`
- Create: `tests/test_event_bus.py`

This task ships the EventBus class with only the stdout sink wired. Subsequent tasks layer more sinks in.

- [ ] **Step 2.1: Write failing test for run_id + trace_id auto-generation**

Create `tests/test_event_bus.py`:

```python
"""Tests for execution.core.event_bus module."""
import json
import os
import pytest


def test_event_bus_generates_run_id_when_none_provided(monkeypatch):
    # Disable all optional sinks so the test is deterministic
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TRACE_ID", raising=False)
    monkeypatch.delenv("PARENT_RUN_ID", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="test_wf")
    assert bus.workflow == "test_wf"
    assert bus.run_id is not None and len(bus.run_id) >= 6
    assert bus.trace_id == bus.run_id  # defaults to run_id when no TRACE_ID env
    assert bus.parent_run_id is None


def test_event_bus_respects_provided_run_id_and_trace_id(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t", run_id="run_abc", trace_id="trace_xyz", parent_run_id="parent_q")
    assert bus.run_id == "run_abc"
    assert bus.trace_id == "trace_xyz"
    assert bus.parent_run_id == "parent_q"


def test_event_bus_inherits_trace_id_from_env(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TRACE_ID", "inherited_trace_42")

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    assert bus.trace_id == "inherited_trace_42"
    assert bus.run_id != "inherited_trace_42"  # run_id still auto-generated
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError: No module named 'execution.core.event_bus'` (3 errors).

- [ ] **Step 2.3: Create `event_bus.py` with EventBus stub**

Create `execution/core/event_bus.py`:

```python
"""
Event bus: single-point emitter for structured workflow events.

Fan-outs to multiple sinks (stdout, Supabase event_log, Sentry breadcrumbs,
main-chat Telegram for errors). Every sink is never-raise — failures are
logged to stderr/logger and swallowed so workflows are never broken by
telemetry.

Phase 1 (this module): stdout + Supabase + Sentry + main-chat sinks.
Phase 2 (later): _EventsChannelSink for firehose.
"""
import json
import logging
import os
import secrets
import sys
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VALID_LEVELS = frozenset({"info", "warn", "error"})


def _generate_run_id() -> str:
    """8-char hex, good enough for log grepping and far-from-collision."""
    return secrets.token_hex(4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventBus:
    """Emit structured events to multiple sinks. Never raises."""

    def __init__(
        self,
        workflow: str,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        parent_run_id: Optional[str] = None,
    ):
        self.workflow = workflow
        self.run_id = run_id or _generate_run_id()
        self.trace_id = trace_id or os.getenv("TRACE_ID") or self.run_id
        self.parent_run_id = parent_run_id or os.getenv("PARENT_RUN_ID")
        self._sinks = self._build_sinks()

    def _build_sinks(self) -> list:
        return [_StdoutSink()]

    def emit(
        self,
        event: str,
        label: str = "",
        detail: Optional[dict] = None,
        level: str = "info",
    ) -> None:
        """Fan-out to all sinks. Never raises."""
        if level not in _VALID_LEVELS:
            level = "info"
        event_dict = {
            "ts": _now_iso(),
            "workflow": self.workflow,
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "parent_run_id": self.parent_run_id,
            "level": level,
            "event": event,
            "label": label or None,
            "detail": detail or None,
        }
        for sink in self._sinks:
            try:
                sink.emit(event_dict)
            except Exception as exc:
                # Never let sink failure propagate
                logger.warning("event_bus sink %s failed: %s", type(sink).__name__, exc)


class _StdoutSink:
    """Always-on sink: one JSON line per event to stdout. Surfaces in GH Actions logs."""

    def emit(self, event_dict: dict) -> None:
        sys.stdout.write(json.dumps(event_dict, ensure_ascii=False) + "\n")
        sys.stdout.flush()
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py -v 2>&1 | tail -10
```

Expected: 3 passed.

- [ ] **Step 2.5: Add test for stdout sink output**

Append to `tests/test_event_bus.py`:

```python
def test_stdout_sink_writes_json_line_per_emit(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="wf_x", run_id="r1", trace_id="t1")
    bus.emit("cron_started")
    bus.emit("step", label="doing thing", detail={"n": 5}, level="info")
    bus.emit("cron_crashed", level="error")

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 3
    for line in out:
        parsed = json.loads(line)
        assert parsed["workflow"] == "wf_x"
        assert parsed["run_id"] == "r1"
        assert parsed["trace_id"] == "t1"
        assert parsed["event"] in ("cron_started", "step", "cron_crashed")
    assert json.loads(out[1])["label"] == "doing thing"
    assert json.loads(out[1])["detail"] == {"n": 5}
    assert json.loads(out[2])["level"] == "error"


def test_emit_coerces_invalid_level_to_info(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    bus.emit("step", level="debug")  # not in {info, warn, error}

    out = capsys.readouterr().out.strip()
    assert json.loads(out)["level"] == "info"
```

- [ ] **Step 2.6: Run to verify new tests pass**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py -v 2>&1 | tail -10
```

Expected: 5 passed.

- [ ] **Step 2.7: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add execution/core/event_bus.py tests/test_event_bus.py
git commit -m "$(cat <<'EOF'
feat(observability): add event_bus module with stdout sink

Phase 1 foundation: EventBus class with auto-generated run_id/trace_id,
never-raise emit() fan-out, and always-on _StdoutSink emitting one JSON
line per event (surfaces in GH Actions logs).

Spec: docs/superpowers/specs/2026-04-21-observability-unified-design.md §3
EOF
)"
```

Expected: commit succeeds.

---

## Task 3: Add `_SupabaseSink`

**Files:**
- Modify: `execution/core/event_bus.py`
- Modify: `tests/test_event_bus.py`

- [ ] **Step 3.1: Write failing test with a mock Supabase client**

Append to `tests/test_event_bus.py`:

```python
def test_supabase_sink_inserts_row_when_enabled(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "fake_key")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    inserted_rows = []

    class FakeSupabaseClient:
        def table(self, name):
            assert name == "event_log"
            return self

        def insert(self, row):
            inserted_rows.append(row)
            return self

        def execute(self):
            return None

    from execution.core import event_bus as eb

    monkeypatch.setattr(eb, "_get_supabase_client", lambda: FakeSupabaseClient())

    bus = eb.EventBus(workflow="wf_y", run_id="r2")
    bus.emit("cron_started")

    assert len(inserted_rows) == 1
    row = inserted_rows[0]
    assert row["workflow"] == "wf_y"
    assert row["run_id"] == "r2"
    assert row["event"] == "cron_started"
    assert row["level"] == "info"
    # The stdout payload includes 'ts' as ISO; Supabase row either uses its own default
    # or passes through. Either way, the 'ts' key presence is OK.


def test_supabase_sink_disabled_when_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    bus.emit("cron_started")  # should not raise, should not try Supabase

    # Verify stdout still fires
    assert "cron_started" in capsys.readouterr().out
```

- [ ] **Step 3.2: Run to verify it fails**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py::test_supabase_sink_inserts_row_when_enabled -v 2>&1 | tail -10
```

Expected: fail — `_get_supabase_client` attribute doesn't exist on module.

- [ ] **Step 3.3: Implement `_SupabaseSink` and wire into `_build_sinks`**

In `execution/core/event_bus.py`:

1. Add a module-level helper **before the `EventBus` class**:

```python
def _get_supabase_client():
    """Return a supabase-py Client, or None if credentials/library missing.
    Extracted to module scope so tests can monkeypatch."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        logger.warning("supabase client init failed: %s", exc)
        return None
```

2. Update `EventBus._build_sinks` to include `_SupabaseSink` when credentials are present:

```python
    def _build_sinks(self) -> list:
        sinks: list = [_StdoutSink()]
        supabase = _get_supabase_client()
        if supabase is not None:
            sinks.append(_SupabaseSink(supabase))
        return sinks
```

3. Add `_SupabaseSink` class **after `_StdoutSink`**:

```python
class _SupabaseSink:
    """Persists each event to the event_log table. Best-effort."""

    def __init__(self, client):
        self._client = client

    def emit(self, event_dict: dict) -> None:
        # Strip the 'ts' from the row; let Supabase use its NOW() default.
        row = {k: v for k, v in event_dict.items() if k != "ts"}
        self._client.table("event_log").insert(row).execute()
```

- [ ] **Step 3.4: Run new tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py -v 2>&1 | tail -15
```

Expected: 7 passed.

- [ ] **Step 3.5: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add execution/core/event_bus.py tests/test_event_bus.py
git commit -m "feat(observability): add supabase sink to event_bus"
```

---

## Task 4: Add `_SentrySink` (breadcrumbs only)

**Files:**
- Modify: `execution/core/event_bus.py`
- Modify: `tests/test_event_bus.py`

The Sentry sink adds a breadcrumb per emit. It does NOT call `capture_exception` — that's the decorator's job (Task 7). Breadcrumbs enrich any future captured exception with recent context.

- [ ] **Step 4.1: Write failing test**

Append to `tests/test_event_bus.py`:

```python
def test_sentry_sink_adds_breadcrumb_per_emit(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    breadcrumbs_added = []

    fake_sentry = type(sys)("sentry_sdk")
    fake_sentry.add_breadcrumb = lambda **kwargs: breadcrumbs_added.append(kwargs)
    fake_sentry.capture_exception = lambda exc: None

    import sys as _sys
    monkeypatch.setitem(_sys.modules, "sentry_sdk", fake_sentry)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="wf_z")
    bus.emit("step", label="working", detail={"n": 1}, level="info")
    bus.emit("cron_crashed", label="BOOM", level="error")

    assert len(breadcrumbs_added) == 2
    first = breadcrumbs_added[0]
    assert first["category"] == "wf_z"
    assert first["level"] == "info"
    assert first["message"] == "working"
    assert first["data"] == {"n": 1}

    second = breadcrumbs_added[1]
    assert second["level"] == "error"
    assert second["message"] == "BOOM"


def test_sentry_sink_graceful_when_sdk_missing(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # Force sentry_sdk import to fail
    import sys as _sys
    monkeypatch.setitem(_sys.modules, "sentry_sdk", None)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    bus.emit("step", label="no-sentry")  # must not raise
```

Note: the first test's `type(sys)("sentry_sdk")` idiom creates a fresh module object. The second test sets `sys.modules["sentry_sdk"] = None`, which makes `import sentry_sdk` raise ImportError on next import.

- [ ] **Step 4.2: Run to verify it fails**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py::test_sentry_sink_adds_breadcrumb_per_emit -v 2>&1 | tail -10
```

Expected: fail (Sentry sink not wired; breadcrumb never called).

- [ ] **Step 4.3: Implement `_SentrySink`**

In `execution/core/event_bus.py`:

1. Add `_SentrySink` class **after `_SupabaseSink`**:

```python
class _SentrySink:
    """Adds a Sentry breadcrumb per event for crash context. No capture here —
    capture_exception lives in the @with_event_bus decorator."""

    def emit(self, event_dict: dict) -> None:
        try:
            import sentry_sdk
        except Exception:
            return  # sentry_sdk absent or shimmed to None
        if sentry_sdk is None:
            return
        sentry_sdk.add_breadcrumb(
            category=event_dict.get("workflow") or "event_bus",
            level=event_dict.get("level", "info"),
            message=event_dict.get("label") or event_dict.get("event", ""),
            data=event_dict.get("detail") or {},
        )
```

2. Update `_build_sinks`:

```python
    def _build_sinks(self) -> list:
        sinks: list = [_StdoutSink()]
        supabase = _get_supabase_client()
        if supabase is not None:
            sinks.append(_SupabaseSink(supabase))
        sinks.append(_SentrySink())  # always-on; internally no-ops if sdk absent
        return sinks
```

- [ ] **Step 4.4: Run new tests to verify they pass**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py -v 2>&1 | tail -15
```

Expected: 9 passed.

- [ ] **Step 4.5: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add execution/core/event_bus.py tests/test_event_bus.py
git commit -m "feat(observability): add sentry breadcrumb sink to event_bus"
```

---

## Task 5: Add `_MainChatSink` (errors + crashes only)

**Files:**
- Modify: `execution/core/event_bus.py`
- Modify: `tests/test_event_bus.py`

The main-chat sink sends a Telegram message to the operator's primary chat. It fires only for:
- Any event with `level == "warn"` or `level == "error"`.
- Specific events: `cron_crashed`, `cron_missed`, regardless of level (belt-and-suspenders).

`info` events never hit the main chat — they'd drown it. The Phase 2 events-channel sink will show `info` events.

- [ ] **Step 5.1: Write failing tests**

Append to `tests/test_event_bus.py`:

```python
def test_main_chat_sink_sends_on_error(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 999

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    bus = eb.EventBus(workflow="morning_check")
    bus.emit("cron_crashed", label="TypeError: boom", level="error")

    assert len(sent_messages) == 1
    msg = sent_messages[0]
    assert msg["chat_id"] == "12345"
    assert "morning_check" in msg["text"].lower() or "MORNING CHECK" in msg["text"]
    assert "CRASH" in msg["text"] or "crash" in msg["text"].lower()
    assert "TypeError: boom" in msg["text"]


def test_main_chat_sink_skips_info_events(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    sent_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 1

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_build_telegram_client", lambda: FakeTelegramClient())

    bus = eb.EventBus(workflow="t")
    bus.emit("step", label="doing thing", level="info")
    bus.emit("cron_started")  # default level info

    assert sent_messages == []


def test_main_chat_sink_disabled_when_env_missing(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    from execution.core.event_bus import EventBus

    bus = EventBus(workflow="t")
    bus.emit("cron_crashed", level="error")  # would want to alert, but env missing

    # Stdout still fires
    assert "cron_crashed" in capsys.readouterr().out
```

- [ ] **Step 5.2: Run to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py::test_main_chat_sink_sends_on_error -v 2>&1 | tail -10
```

Expected: fail — `_build_telegram_client` missing.

- [ ] **Step 5.3: Implement `_MainChatSink`**

In `execution/core/event_bus.py`:

1. Add the telegram-client factory (keeps the lazy-import pattern from `delivery_reporter._build_telegram_client`):

```python
def _build_telegram_client():
    """Factory so tests can monkeypatch. Returns a TelegramClient or None on failure."""
    try:
        from execution.integrations.telegram_client import TelegramClient
        return TelegramClient()
    except Exception as exc:
        logger.warning("telegram client init failed: %s", exc)
        return None
```

2. Add `_MainChatSink` class **after `_SentrySink`**:

```python
_ALERT_EVENTS = frozenset({"cron_crashed", "cron_missed"})


class _MainChatSink:
    """Sends a distinct Telegram message to the operator's main chat for errors
    and specific alert events. Skips info-level so the primary chat stays clean."""

    def __init__(self, chat_id: str):
        self._chat_id = chat_id

    def _should_alert(self, event_dict: dict) -> bool:
        if event_dict.get("level") in ("warn", "error"):
            return True
        if event_dict.get("event") in _ALERT_EVENTS:
            return True
        return False

    def emit(self, event_dict: dict) -> None:
        if not self._should_alert(event_dict):
            return
        client = _build_telegram_client()
        if client is None:
            return
        text = self._format(event_dict)
        client.send_message(text=text, chat_id=self._chat_id)

    @staticmethod
    def _format(event_dict: dict) -> str:
        workflow = (event_dict.get("workflow") or "?").upper().replace("_", " ")
        event = event_dict.get("event", "")
        label = event_dict.get("label") or ""
        run_id = event_dict.get("run_id", "")
        if event == "cron_crashed":
            emoji = "🚨"
            title = f"{workflow} — CRASH"
        elif event == "cron_missed":
            emoji = "⏰"
            title = f"{workflow} — NÃO RODOU"
        else:
            emoji = "⚠️"
            title = f"{workflow} — {event}"
        lines = [f"{emoji} {title}"]
        if label:
            lines.append(label)
        if run_id:
            lines.append(f"run_id: {run_id}")
        return "\n".join(lines)
```

3. Update `_build_sinks`:

```python
    def _build_sinks(self) -> list:
        sinks: list = [_StdoutSink()]
        supabase = _get_supabase_client()
        if supabase is not None:
            sinks.append(_SupabaseSink(supabase))
        sinks.append(_SentrySink())
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if chat_id and token:
            sinks.append(_MainChatSink(chat_id=chat_id))
        return sinks
```

- [ ] **Step 5.4: Run all event_bus tests**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py -v 2>&1 | tail -15
```

Expected: 12 passed.

- [ ] **Step 5.5: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add execution/core/event_bus.py tests/test_event_bus.py
git commit -m "feat(observability): add main-chat sink for errors and crash alerts"
```

---

## Task 6: Never-raise robustness test

**Files:**
- Modify: `tests/test_event_bus.py`

Single test that proves a failing sink does NOT prevent other sinks from firing and does NOT propagate to the caller. This is the core "never-raise" contract.

- [ ] **Step 6.1: Write the test**

Append to `tests/test_event_bus.py`:

```python
def test_emit_continues_when_one_sink_raises(monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", "https://fake")
    monkeypatch.setenv("SUPABASE_KEY", "fake")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    class ExplodingSupabaseClient:
        def table(self, name):
            raise RuntimeError("supabase is down")

    from execution.core import event_bus as eb
    monkeypatch.setattr(eb, "_get_supabase_client", lambda: ExplodingSupabaseClient())

    bus = eb.EventBus(workflow="wf", run_id="rX")
    bus.emit("cron_started")  # must not raise
    # Stdout sink (runs before Supabase) still fires
    out = capsys.readouterr().out
    assert "cron_started" in out
    assert "rX" in out
```

- [ ] **Step 6.2: Run to verify it passes**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py::test_emit_continues_when_one_sink_raises -v 2>&1 | tail -5
```

Expected: pass (the try/except in `EventBus.emit` already implements this). If it fails, the try/except is broken — fix it in `event_bus.py`.

- [ ] **Step 6.3: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add tests/test_event_bus.py
git commit -m "test(observability): verify emit continues when one sink raises"
```

---

## Task 7: `@with_event_bus` decorator

**Files:**
- Modify: `execution/core/event_bus.py`
- Modify: `tests/test_event_bus.py`

The decorator wraps `main()`, emits lifecycle events, calls `init_sentry`, and captures uncaught exceptions to Sentry before re-raising.

- [ ] **Step 7.1: Write failing tests for happy path + crash path**

Append to `tests/test_event_bus.py`:

```python
def test_with_event_bus_emits_started_and_finished(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    from execution.core.event_bus import with_event_bus

    calls = []

    @with_event_bus("test_wf")
    def main():
        calls.append("inside main")
        return "ok"

    result = main()
    assert result == "ok"
    assert calls == ["inside main"]

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    events = [e["event"] for e in out_lines]
    assert events == ["cron_started", "cron_finished"]
    assert all(e["workflow"] == "test_wf" for e in out_lines)
    # Both lifecycle events share the same run_id
    assert out_lines[0]["run_id"] == out_lines[1]["run_id"]


def test_with_event_bus_catches_exception_and_re_raises(monkeypatch, capsys):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    captures = []
    fake_sentry = type(sys)("sentry_sdk")
    fake_sentry.add_breadcrumb = lambda **kw: None
    fake_sentry.capture_exception = lambda exc: captures.append(str(exc)[:50])

    import sys as _sys
    monkeypatch.setitem(_sys.modules, "sentry_sdk", fake_sentry)

    from execution.core.event_bus import with_event_bus

    @with_event_bus("test_wf")
    def broken_main():
        raise ValueError("synthetic boom")

    with pytest.raises(ValueError, match="synthetic boom"):
        broken_main()

    out_lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    events = [e["event"] for e in out_lines]
    assert events == ["cron_started", "cron_crashed"]
    crashed = out_lines[1]
    assert crashed["level"] == "error"
    assert "ValueError" in (crashed["label"] or "")
    assert "synthetic boom" in (crashed["label"] or "")

    # Sentry captured the exception
    assert len(captures) == 1
    assert "synthetic boom" in captures[0]


def test_with_event_bus_calls_init_sentry(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    calls = []
    from execution.core import sentry_init as si_module
    monkeypatch.setattr(si_module, "init_sentry", lambda name: calls.append(name) or True)

    from execution.core.event_bus import with_event_bus

    @with_event_bus("baltic_ingestion")
    def main():
        return None

    main()
    # init_sentry called once with the workflow-derived script name
    assert len(calls) == 1
    assert "baltic_ingestion" in calls[0]
```

- [ ] **Step 7.2: Run to verify they fail**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py -v -k "with_event_bus" 2>&1 | tail -10
```

Expected: 3 errors — `with_event_bus` doesn't exist.

- [ ] **Step 7.3: Implement the decorator**

Append to `execution/core/event_bus.py`:

```python
import functools


def with_event_bus(workflow: str):
    """Decorator that wraps a script's main() to emit lifecycle events and
    capture uncaught exceptions to Sentry.

    Usage:
        @with_event_bus("morning_check")
        def main():
            ...

    Emits cron_started on entry, cron_finished on clean exit, cron_crashed on
    exception. Calls init_sentry(workflow) as first action. Re-raises the
    original exception so GH Actions marks the run as failed.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Auto-init Sentry (idempotent; safe even if script already calls it)
            try:
                from execution.core.sentry_init import init_sentry
                init_sentry(f"cron.{workflow}")
            except Exception as exc:
                logger.warning("init_sentry failed in decorator: %s", exc)

            bus = EventBus(workflow=workflow)
            bus.emit("cron_started")
            try:
                result = func(*args, **kwargs)
            except BaseException as exc:
                bus.emit(
                    "cron_crashed",
                    label=f"{type(exc).__name__}: {str(exc)[:100]}",
                    detail={"exc_type": type(exc).__name__, "exc_str": str(exc)[:500]},
                    level="error",
                )
                # Capture WITH the last breadcrumbs already on the Sentry scope
                try:
                    import sentry_sdk
                    if sentry_sdk is not None:
                        sentry_sdk.capture_exception(exc)
                except Exception:
                    pass
                raise
            bus.emit("cron_finished")
            return result
        return wrapper
    return decorator
```

- [ ] **Step 7.4: Run tests**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/test_event_bus.py -v 2>&1 | tail -20
```

Expected: 16 passed (13 prior + 3 new for decorator).

- [ ] **Step 7.5: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add execution/core/event_bus.py tests/test_event_bus.py
git commit -m "$(cat <<'EOF'
feat(observability): add @with_event_bus decorator

Wraps script main() to emit cron_started/cron_finished/cron_crashed
lifecycle events. Auto-inits Sentry (fixes the 4 scripts currently missing
it) and captures any uncaught exception with last ~20 breadcrumbs as context.
Re-raises so GH Actions still marks the run failed.

Spec: docs/superpowers/specs/2026-04-21-observability-unified-design.md §6
EOF
)"
```

---

## Task 8: Fix 2 — `progress.fail()` pushes new message

**Files:**
- Modify: `execution/core/progress_reporter.py` (lines ~331-350, the `fail` method)
- Modify: `tests/test_progress_reporter.py` OR `tests/test_delivery_reporter.py` (pick whichever currently tests `progress_reporter`; if neither, create `tests/test_progress_reporter.py`)

Today `progress.fail(exc)` only EDITS the existing card. If the operator's chat had notifications muted or they scrolled past, the crash is silent. Fix: push a distinct new message as well, so Telegram notifies.

- [ ] **Step 8.1: Determine where the test goes**

Run:
```bash
cd /Users/bigode/Dev/agentics_workflows && ls tests/ | grep -i progress
```

If `tests/test_progress_reporter.py` exists, append there. If not, create it.

- [ ] **Step 8.2: Write the failing test**

Test code (add to whichever file you picked):

```python
def test_progress_fail_edits_card_and_sends_new_alert_message():
    """fail() must both edit the progress card AND push a distinct alert
    message so the operator gets a notification even if they scrolled past."""
    from execution.core.progress_reporter import ProgressReporter

    sent_messages = []
    edited_messages = []

    class FakeTelegramClient:
        def send_message(self, text, chat_id=None, **kwargs):
            sent_messages.append({"text": text, "chat_id": chat_id})
            return 123

        def edit_message_text(self, chat_id, message_id, new_text, **kwargs):
            edited_messages.append({"chat_id": chat_id, "message_id": message_id, "new_text": new_text})

    reporter = ProgressReporter(
        workflow="morning_check",
        chat_id="98765",
        telegram_client=FakeTelegramClient(),
    )
    reporter.start(phase_text="running")  # opens card, sets message_id
    assert len(sent_messages) == 1  # the initial card

    try:
        raise RuntimeError("deliberate crash")
    except RuntimeError as exc:
        reporter.fail(exc)

    # Card was edited with CRASH marker
    assert len(edited_messages) == 1
    assert "CRASH" in edited_messages[0]["new_text"]

    # AND a NEW message was sent (2 total sends now: initial card + alert)
    assert len(sent_messages) == 2
    alert = sent_messages[1]
    assert alert["chat_id"] == "98765"
    assert "CRASH" in alert["text"] or "crash" in alert["text"].lower()
    assert "morning_check" in alert["text"].lower()
    assert "deliberate crash" in alert["text"]
```

- [ ] **Step 8.3: Run to verify it fails**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest <your_test_file>::test_progress_fail_edits_card_and_sends_new_alert_message -v 2>&1 | tail -10
```

Expected: fail — `len(sent_messages) == 2` fails with value 1 (today `fail()` only edits, doesn't push).

- [ ] **Step 8.4: Edit `fail()` in progress_reporter.py**

Find the `fail` method (currently lines 331-350). Replace with:

```python
    def fail(self, exception: Exception) -> None:
        """Edit message with crash marker, push a distinct alert message,
        and record to state store. Called from outer try/except in script
        main(). Never raises."""
        exc_text = str(exception)[:200]

        # 1. Edit the existing card (as before)
        if not self._disabled and self._message_id is not None:
            text = self._header("🚨", f"CRASH: {exc_text}")
            try:
                client = self._get_client()
                client.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self._message_id,
                    new_text=text,
                )
            except Exception as e:
                print(f"[WARN] ProgressReporter.fail telegram edit failed: {e}")

        # 2. NEW: push a distinct alert message so the operator gets notified
        try:
            client = self._get_client()
            alert_text = f"🚨 CRASH {self.workflow}: {exc_text[:120]}"
            client.send_message(text=alert_text, chat_id=self.chat_id)
        except Exception as e:
            print(f"[WARN] ProgressReporter.fail alert send failed: {e}")

        # 3. Record crash to state store (as before)
        try:
            from execution.core import state_store
            state_store.record_crash(self.workflow, exc_text)
        except Exception as e:
            print(f"[WARN] ProgressReporter.fail state_store failed: {e}")
```

- [ ] **Step 8.5: Run test to verify it passes**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest <your_test_file>::test_progress_fail_edits_card_and_sends_new_alert_message -v 2>&1 | tail -10
```

Expected: pass.

- [ ] **Step 8.6: Run full test suite to confirm no regressions**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```

Expected: all pre-existing tests still pass; +1 new test from this task.

- [ ] **Step 8.7: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add execution/core/progress_reporter.py <your_test_file>
git commit -m "$(cat <<'EOF'
fix(observability): progress.fail() pushes a new alert message

Previously only edited the existing progress card with a CRASH marker —
if the operator scrolled past or had notifications silenced on the card,
the crash was silent. Now pushes a distinct alert message in addition to
the edit so Telegram triggers a fresh notification.
EOF
)"
```

---

## Task 9: Wrap all 7 execution scripts with `@with_event_bus`

**Files:**
- Modify: `execution/scripts/morning_check.py`
- Modify: `execution/scripts/send_daily_report.py`
- Modify: `execution/scripts/send_news.py`
- Modify: `execution/scripts/rebuild_dedup.py`
- Modify: `execution/scripts/platts_reports.py`
- Modify: `execution/scripts/platts_ingestion.py`
- Modify: `execution/scripts/baltic_ingestion.py`

No new tests — each script is minimal mechanical decoration. The decorator itself is tested in Task 7. End-to-end smoke in Task 10.

- [ ] **Step 9.1: Wrap `morning_check.py`**

Open `execution/scripts/morning_check.py`. Find the `def main():` line (likely near the bottom of the file, before an `if __name__ == "__main__":` block). Apply three edits:

1. Add an import at the top (group with other `execution.core` imports):
   ```python
   from execution.core.event_bus import with_event_bus
   ```

2. Decorate `main`:
   ```python
   @with_event_bus("morning_check")
   def main():
       # ... existing body unchanged
   ```

3. No other changes. The decorator does not change the function signature (still `def main():`, no `bus` arg — scripts don't emit custom events in Phase 1).

- [ ] **Step 9.2: Wrap `send_daily_report.py`**

Same pattern. Import + `@with_event_bus("daily_report")` (match the workflow name from `webhook/status_builder.ALL_WORKFLOWS`).

- [ ] **Step 9.3: Wrap `send_news.py`**

Import + `@with_event_bus("market_news")`.

- [ ] **Step 9.4: Wrap `rebuild_dedup.py`**

Import + `@with_event_bus("rebuild_dedup")`. (Not in ALL_WORKFLOWS today — that's fine, the decorator works regardless; ALL_WORKFLOWS matters for watchdog in Phase 3.)

- [ ] **Step 9.5: Wrap `platts_reports.py` + remove redundant init_sentry**

Apply the decorator as in Step 9.1. ALSO: find the existing `init_sentry(__name__)` call (around line 138) and remove it — the decorator now handles init. Keep the import line `from execution.core.sentry_init import init_sentry` for now (harmless; Task 10 will confirm it's unused via ruff/import-check if run).

Workflow name: `@with_event_bus("platts_reports")`.

- [ ] **Step 9.6: Wrap `platts_ingestion.py` + remove redundant init_sentry**

Same pattern. `@with_event_bus("platts_ingestion")`. Remove the `init_sentry(__name__)` call around line 202.

- [ ] **Step 9.7: Wrap `baltic_ingestion.py` + remove redundant init_sentry**

`@with_event_bus("baltic_ingestion")`. Remove the `init_sentry(__name__)` call around line 380.

- [ ] **Step 9.8: Dry-run each script's import**

For each wrapped script, verify it still imports without error:

```bash
cd /Users/bigode/Dev/agentics_workflows && for s in morning_check send_daily_report send_news rebuild_dedup platts_reports platts_ingestion baltic_ingestion; do
  /usr/bin/python3 -c "from execution.scripts import $s; print('$s OK')" || echo "$s FAILED"
done
```

Expected: `<name> OK` for all 7. If any fail, the decorator is likely being applied incorrectly — check the import line and `@` placement.

- [ ] **Step 9.9: Run full test suite to confirm no regressions**

```bash
cd /Users/bigode/Dev/agentics_workflows && /usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```

Expected: all tests still pass.

- [ ] **Step 9.10: Commit**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add execution/scripts/
git commit -m "$(cat <<'EOF'
feat(observability): wrap all execution scripts with @with_event_bus

morning_check, send_daily_report, send_news, rebuild_dedup, platts_reports,
platts_ingestion, baltic_ingestion now emit cron_started/cron_finished/
cron_crashed lifecycle events. Sentry auto-init via decorator covers the
4 scripts that weren't initialized before; the 3 scripts that had explicit
init_sentry calls drop them (decorator does it instead).

After this commit: every uncaught exception in any script produces a Telegram
alert in the main chat AND a Sentry capture with ~20 breadcrumbs of context,
regardless of whether the crash happened during import, config load, or
mid-execution.

Spec: docs/superpowers/specs/2026-04-21-observability-unified-design.md
EOF
)"
```

---

## Task 10: End-to-end manual validation

**Files:** none — verification only.

This task does not modify code; it validates Phase 1 actually delivers the ship criterion against real services (Supabase, Sentry, Telegram). Cannot be unit-tested; must be done by triggering real workflows.

- [ ] **Step 10.1: Trigger `morning_check` manually on staging**

Push your branch; trigger the workflow via GH Actions UI ("Run workflow" button on a staging branch / or the staging GH environment if separate).

Watch for:
- GH Actions stdout shows `{"event": "cron_started", ...}` line within first ~2s of run.
- Same run eventually shows `{"event": "cron_finished", ...}` (success) or `{"event": "cron_crashed", ...}` (failure).

If you don't have a staging env, skip to Step 10.3 (deliberate crash test) and rely on dev-machine manual script execution for the happy path.

- [ ] **Step 10.2: Verify Supabase event_log rows**

In Supabase SQL editor:
```sql
SELECT ts, workflow, event, level, label, run_id FROM event_log
WHERE workflow = 'morning_check'
ORDER BY ts DESC LIMIT 10;
```

Expected: at least 2 rows (`cron_started`, `cron_finished` OR `cron_crashed`) from the run you just triggered. `run_id` should match across both rows.

- [ ] **Step 10.3: Deliberate crash test**

Add a single line at the top of `morning_check.main()` on a throwaway branch:
```python
raise RuntimeError("observability smoke test — please delete")
```

Push + trigger the workflow. Verify:

1. **Main chat Telegram receives a message** matching:
   ```
   🚨 MORNING CHECK — CRASH
   RuntimeError: observability smoke test — please delete
   run_id: <8-hex-chars>
   ```

2. **Sentry dashboard** shows a new issue for `RuntimeError: observability smoke test`. Click it — verify the breadcrumbs pane shows `cron_started` as one of the recent breadcrumbs.

3. **Supabase** `event_log` has a `cron_crashed` row:
   ```sql
   SELECT * FROM event_log
   WHERE event = 'cron_crashed' AND workflow = 'morning_check'
   ORDER BY ts DESC LIMIT 1;
   ```

4. **GitHub Actions run** is marked failed (red X) — the decorator re-raises so the run doesn't green-wash.

Revert the `raise RuntimeError(...)` line, push again, verify the next run is green.

- [ ] **Step 10.4: Verify the 3 scripts that had manual init_sentry are still happy**

For each of `platts_reports`, `platts_ingestion`, `baltic_ingestion`: trigger their workflow manually (or locally invoke). Confirm:
- No "sentry init error" in stdout.
- Sentry dashboard shows events tagged with their workflow name.
- `event_log` has `cron_started`/`cron_finished` rows.

- [ ] **Step 10.5: Document the validation**

Add a short note to the followup doc (create if doesn't exist):

`docs/superpowers/followups/2026-04-21-observability-phase1-followups.md`:

```markdown
# Observability Phase 1 — Followups

**Shipped:** 2026-04-21 on `main` (commits <first>..<last>)

## Validation checklist (Task 10 of the plan)

- [ ] Supabase event_log rows confirmed for morning_check
- [ ] Deliberate-crash smoke test: Telegram alert received in main chat
- [ ] Sentry issue created with breadcrumbs
- [ ] All 7 scripts tested manually and green

## Known followups (out of Phase 1 scope)

- Phase 2: implement _EventsChannelSink + operator creates Telegram channel
- Phase 3: implement watchdog cron + .github/workflows/watchdog.yml
- Phase 4: implement /tail command + step/api_call instrumentation in scripts
- TTL on event_log (30d via pg_cron) — Supabase may not have pg_cron; defer to manual cleanup
- 3 scripts keep the import line `from execution.core.sentry_init import init_sentry` unused after removing the call — acceptable noise; a ruff/isort pass can drop it.

## Open questions resolved during implementation

- [ ] (fill in as discovered)
```

- [ ] **Step 10.6: Commit the followup doc**

```bash
cd /Users/bigode/Dev/agentics_workflows && git add docs/superpowers/followups/2026-04-21-observability-phase1-followups.md
git commit -m "docs(followups): observability phase 1 shipped — validation checklist"
```

---

## Post-flight: prepare for Phase 2

- [ ] **Step 11.1: Push to remote**

```bash
cd /Users/bigode/Dev/agentics_workflows && git push origin <your-branch>
```

- [ ] **Step 11.2: Confirm ship criterion is met**

Revisit the 5 criteria from the plan header:

1. All 7 execution scripts emit `cron_started`/`cron_finished`/`cron_crashed` — ✅ Task 9
2. Sentry auto-initialized for all 7 — ✅ Task 7 (via decorator) + Task 9
3. Main chat receives a distinct alert for every script crash — ✅ Task 5 (_MainChatSink) + Task 8 (progress.fail)
4. Baseline tests still pass; 11+ new tests green — ✅ Tasks 2-8
5. `event_log` table exists and receives rows — ✅ Task 1 + Task 10

- [ ] **Step 11.3: Note Phase 2 prerequisites**

Before starting Phase 2, the operator must:
1. Create a new Telegram channel (or group with topics enabled).
2. Add the bot as an admin of the channel.
3. Capture the channel's numeric ID (e.g., via `@userinfobot` or by forwarding a channel message to `@raw_data_bot`).
4. Add `TELEGRAM_EVENTS_CHANNEL_ID=<id>` to repo secrets and to `.env.example` (empty placeholder).

Phase 2 plan will reference this.

---

## Self-Review

**Spec coverage (Phase 1 only):**
- Spec §3 "event_bus API" → Tasks 2-5 (core + 3 sinks) ✓
- Spec §6.1 "Top-of-main sentinel via @with_event_bus" → Task 7 ✓
- Spec §6.2 "progress.fail() pushes new message" → Task 8 ✓
- Spec §6.3 "Sentry init in 4 missing scripts" → Task 7 (decorator) + Task 9 ✓
- Spec §6.4 "Sentry breadcrumbs from event_bus" → Task 4 ✓
- Spec Phase 1 ship criterion (1-5 above) → all tasks + Task 10 verification ✓

**Placeholder scan:** no TBDs. The `<your_test_file>` placeholder in Task 8 is intentional — Step 8.1 resolves it based on repo state. Step 10.1 says "skip to Step 10.3" — fine, valid fallback.

**Type consistency:**
- `_get_supabase_client()` defined in Task 3, referenced same-name in Task 5 (_build_sinks). ✓
- `_build_telegram_client()` defined in Task 5, NOT confused with the identically-named helper in `delivery_reporter.py` — both live in their own modules. ✓
- `with_event_bus(workflow: str)` signature consistent in Task 7 (definition) and Task 9 (usage). ✓
- `EventBus.emit(event, label, detail, level)` signature consistent across Tasks 2-7. ✓
- `_MainChatSink._should_alert` criteria (level in warn/error OR event in _ALERT_EVENTS) matches decorator's emit in Task 7 (level="error" for cron_crashed, which satisfies both guards). ✓

**One potential gotcha flagged:** the decorator calls `init_sentry(f"cron.{workflow}")` and also calls `capture_exception`. If `init_sentry` fails silently (logger warning, returns False), `capture_exception` still works if a prior Sentry init exists from somewhere else (e.g., webhook bot imported), or no-ops gracefully. Acceptable.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-21-observability-phase1-foundation-plan.md`.** Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration. Same pattern used for the P2/P3/P4 delivery_reporter cleanup earlier today.

**2. Inline Execution** — execute tasks in a single session using executing-plans, batch execution with checkpoints for review.

**Note for next session:** the current Claude session has a stale Bash CWD (directory was renamed to `agentics_workflows` mid-session). Start a fresh Claude session in the new path (`/Users/bigode/Dev/agentics_workflows/`) and the shell will resolve correctly. This plan's commands all use the new absolute path.

**Which approach?**
