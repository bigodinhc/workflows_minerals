# Observability Phase 4 — `/tail` + step/api_call Instrumentation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the read-side (`/tail <workflow>` command) and fill the events channel with meaningful step/api_call events, so the operator can pull a detailed timeline of any recent run from Telegram and watch live progress beyond just `cron_started`/`cron_finished`.

**Architecture:** Introduce a `ContextVar`-backed `get_current_bus()` accessor in `event_bus.py` so the `@with_event_bus` decorator can publish the active bus to any code running inside `main()` (scripts, helpers, `state_store`) without plumbing it through call signatures. `state_store.record_*` functions pick up the active `run_id` from that accessor and persist it in `wf:last_run:{workflow}`. The `/tail` command reads `event_log` from Supabase, resolving a default `run_id` via `state_store.get_status()`. Scripts add `bus.emit("step", ...)` and `bus.emit("api_call", ...)` at major boundaries.

**Tech Stack:** Python 3.10+, aiogram 3, Supabase-py, contextvars (stdlib), fakeredis + pytest + unittest.mock for tests.

---

## Context (read before starting)

**What's already live (Phases 1-3, shipped 2026-04-21):**
- `execution/core/event_bus.py` — `EventBus` class, 5 sinks (`_StdoutSink`, `_SupabaseSink`, `_SentrySink`, `_MainChatSink`, `_EventsChannelSink`), `@with_event_bus` decorator, `atexit` flush.
- All 7 execution scripts decorated with `@with_event_bus("workflow_name")`.
- `event_log` table on Supabase with columns `ts, workflow, run_id, trace_id, parent_run_id, level, event, label, detail, pod`. Indexes on `(workflow, ts DESC)`, `run_id`, `trace_id`.
- `execution/scripts/watchdog_cron.py` + `.github/workflows/watchdog.yml` (every 5 min) emitting `cron_missed` when a cron doesn't run within a 15 min grace window.
- `state_store.try_claim_alert_key()` for idempotent SET NX alerts.
- `TELEGRAM_EVENTS_CHANNEL_ID` wired into all 6 workflow YAMLs.

**What's observably missing (per operator feedback):**
- Events channel only shows `cron_started`/`cron_finished`. No step detail. Quote: "o do grupo nao faz muito sentido, porque veja, ele so da cron started e finished, nao foi isso que combinamos, foi?"
- No `/tail` command — operator can't pull a timeline of a failing run from the phone.
- `state_store.record_*` does NOT persist `run_id`, so `/tail <workflow>` without explicit run_id has no way to resolve the most recent run.

**Constraints:**
- `main()` signatures of the 7 scripts MUST stay `def main():` (no kwargs injection). The decorator publishes the bus via `ContextVar`; scripts/helpers grab it with `get_current_bus()`.
- `get_current_bus()` returning `None` outside a decorated context is expected. All callers must handle it (scripts opt-in; state_store silently skips the `run_id` field).
- Never-raise contract continues. `bus.emit(...)` failing mid-script must not crash the script.
- Existing test suite must keep passing. The `fake_redis` fixture + `_send_streak_alert` stub are load-bearing (see `tests/test_state_store.py:21-25`).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `execution/core/event_bus.py` | Modify (~30 new lines) | Add `ContextVar` + `get_current_bus()` accessor; decorator sets/resets token |
| `execution/core/state_store.py` | Modify (~15 new lines) | `record_success/failure/empty/crash` persist `run_id` pulled from `get_current_bus()` |
| `webhook/bot/routers/commands.py` | Modify (~60 new lines) | `/tail <workflow> [run_id]` handler |
| `tests/test_event_bus.py` | Extend (~40 new lines) | Tests for `get_current_bus()` lifecycle |
| `tests/test_state_store.py` | Extend (~30 new lines) | Tests that record_* writes run_id when bus active |
| `tests/test_tail_command.py` | Create (~150 lines) | Unit tests for `/tail` handler |
| `execution/scripts/morning_check.py` | Modify (~6 new emits) | Step + api_call boundaries (pilot) |
| `execution/scripts/send_daily_report.py` | Modify (~5 new emits) | Step + api_call boundaries (pilot) |
| `execution/scripts/baltic_ingestion.py` | Modify (~4 new emits) | Step boundaries |
| `execution/scripts/platts_ingestion.py` | Modify (~4 new emits) | Step boundaries |
| `execution/scripts/platts_reports.py` | Modify (~3 new emits) | Step boundaries |
| `execution/scripts/send_news.py` | Modify (~3 new emits) | Step boundaries |
| `execution/scripts/rebuild_dedup.py` | Modify (~2 new emits) | Step boundaries |
| `docs/superpowers/followups/2026-04-21-observability-phase4-followups.md` | Create | Deferrals + known-issues tracking |

---

## Task 1: ContextVar + `get_current_bus()` accessor

**Why first:** Every downstream task (state_store, instrumentation) depends on this accessor existing. No behavior change on its own — safe to land alone.

**Files:**
- Modify: `execution/core/event_bus.py` (add import + module-level ContextVar + accessor + decorator edit)
- Test: `tests/test_event_bus.py` (append tests)

- [ ] **Step 1: Write failing tests for `get_current_bus()`**

Append to `tests/test_event_bus.py`:

```python
def test_get_current_bus_returns_none_outside_decorator():
    from execution.core.event_bus import get_current_bus
    assert get_current_bus() is None


def test_get_current_bus_returns_active_bus_inside_decorator(monkeypatch):
    from execution.core.event_bus import with_event_bus, get_current_bus
    seen = {}

    @with_event_bus("test_wf")
    def fake_main():
        seen["bus"] = get_current_bus()

    fake_main()
    assert seen["bus"] is not None
    assert seen["bus"].workflow == "test_wf"


def test_get_current_bus_resets_after_decorator_exits(monkeypatch):
    from execution.core.event_bus import with_event_bus, get_current_bus

    @with_event_bus("test_wf")
    def fake_main():
        assert get_current_bus() is not None

    fake_main()
    assert get_current_bus() is None


def test_get_current_bus_resets_even_when_decorator_raises():
    from execution.core.event_bus import with_event_bus, get_current_bus

    @with_event_bus("test_wf")
    def boom():
        raise RuntimeError("oops")

    with pytest.raises(RuntimeError):
        boom()
    assert get_current_bus() is None


def test_get_current_bus_isolated_across_nested_calls():
    """If a decorated function calls another decorated function, the inner
    bus is active during inner call; outer bus restored afterward."""
    from execution.core.event_bus import with_event_bus, get_current_bus
    trail = []

    @with_event_bus("inner")
    def inner():
        trail.append(("inner", get_current_bus().workflow))

    @with_event_bus("outer")
    def outer():
        trail.append(("outer-before", get_current_bus().workflow))
        inner()
        trail.append(("outer-after", get_current_bus().workflow))

    outer()
    assert trail == [
        ("outer-before", "outer"),
        ("inner", "inner"),
        ("outer-after", "outer"),
    ]
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `/usr/bin/python3 -m pytest tests/test_event_bus.py -v -k "current_bus"`
Expected: ImportError or AttributeError (`get_current_bus` doesn't exist yet).

- [ ] **Step 3: Implement ContextVar + accessor + decorator wiring**

Edit `execution/core/event_bus.py`.

Add import at top of file (next to existing `import functools`):

```python
from contextvars import ContextVar
```

Add module-level after the existing `_VALID_LEVELS` line and the `_generate_run_id` helpers, BEFORE the `EventBus` class:

```python
_active_bus: ContextVar[Optional["EventBus"]] = ContextVar("active_event_bus", default=None)


def get_current_bus() -> Optional["EventBus"]:
    """Return the EventBus active for the current @with_event_bus context,
    or None if called outside a decorated function.

    Scripts and helpers use this to emit step/api_call events without
    threading the bus through call signatures. state_store.record_* uses
    it to tag last-run state with the event_bus run_id for /tail.

    Callers must tolerate None (outside decorator, or in tests)."""
    return _active_bus.get()
```

Modify the decorator body in `with_event_bus` to set and reset the ContextVar:

```python
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
        token = _active_bus.set(bus)
        try:
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
                try:
                    from execution.core import state_store
                    state_store.record_crash(workflow, f"{type(exc).__name__}: {exc}")
                except Exception:
                    pass
                try:
                    import sentry_sdk
                    if sentry_sdk is not None:
                        sentry_sdk.capture_exception(exc)
                except Exception:
                    pass
                raise
            bus.emit("cron_finished")
            return result
        finally:
            _active_bus.reset(token)
    return wrapper
return decorator
```

(The `try/finally` around the whole body — not just the `try/except` for the user function — is what guarantees reset on exceptions AND on clean return.)

- [ ] **Step 4: Run tests to confirm they pass**

Run: `/usr/bin/python3 -m pytest tests/test_event_bus.py -v -k "current_bus"`
Expected: 5 passed.

- [ ] **Step 5: Run full event_bus test suite to confirm no regression**

Run: `/usr/bin/python3 -m pytest tests/test_event_bus.py -v`
Expected: all 28+ tests passing.

- [ ] **Step 6: Commit**

```bash
git add execution/core/event_bus.py tests/test_event_bus.py
git commit -m "feat(observability): add get_current_bus() ContextVar accessor

Publishes the active EventBus via ContextVar so scripts, helpers, and
state_store can emit step/api_call events without threading the bus
through call signatures. Handles nested decorators and exception paths
via try/finally reset."
```

---

## Task 2: `state_store` persists `run_id` from active bus

**Why second:** Needed before `/tail` can resolve a default run_id when no explicit one is passed. Zero change to `get_status()` — callers already receive any extra JSON fields via `json.loads`.

**Files:**
- Modify: `execution/core/state_store.py` (helper + edits to 4 record functions)
- Test: `tests/test_state_store.py` (append tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_state_store.py`:

```python
def test_record_success_persists_run_id_when_bus_active(fake_redis, monkeypatch):
    """When @with_event_bus decorator is active, record_success must tag
    the last_run payload with the event_bus run_id for /tail resolution."""
    from execution.core import state_store, event_bus

    bus = event_bus.EventBus(workflow="test")
    token = event_bus._active_bus.set(bus)
    try:
        state_store.record_success("test", {"total": 1, "success": 1, "failure": 0}, 100)
    finally:
        event_bus._active_bus.reset(token)

    raw = fake_redis.get("wf:last_run:test")
    data = json.loads(raw)
    assert data["run_id"] == bus.run_id


def test_record_failure_persists_run_id_when_bus_active(fake_redis, monkeypatch):
    from execution.core import state_store, event_bus

    bus = event_bus.EventBus(workflow="test")
    token = event_bus._active_bus.set(bus)
    try:
        state_store.record_failure("test", {"total": 1, "success": 0, "failure": 1}, 100)
    finally:
        event_bus._active_bus.reset(token)

    raw = fake_redis.get("wf:last_run:test")
    data = json.loads(raw)
    assert data["run_id"] == bus.run_id


def test_record_empty_persists_run_id_when_bus_active(fake_redis, monkeypatch):
    from execution.core import state_store, event_bus

    bus = event_bus.EventBus(workflow="test")
    token = event_bus._active_bus.set(bus)
    try:
        state_store.record_empty("test", "no data")
    finally:
        event_bus._active_bus.reset(token)

    raw = fake_redis.get("wf:last_run:test")
    data = json.loads(raw)
    assert data["run_id"] == bus.run_id


def test_record_crash_persists_run_id_when_bus_active(fake_redis, monkeypatch):
    from execution.core import state_store, event_bus

    bus = event_bus.EventBus(workflow="test")
    token = event_bus._active_bus.set(bus)
    try:
        state_store.record_crash("test", "boom")
    finally:
        event_bus._active_bus.reset(token)

    raw = fake_redis.get("wf:last_run:test")
    data = json.loads(raw)
    assert data["run_id"] == bus.run_id


def test_record_success_omits_run_id_when_no_bus(fake_redis):
    """Outside a decorator, record_success still works — just no run_id field."""
    from execution.core.state_store import record_success

    record_success("test", {"total": 1, "success": 1, "failure": 0}, 100)
    raw = fake_redis.get("wf:last_run:test")
    data = json.loads(raw)
    assert "run_id" not in data or data["run_id"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `/usr/bin/python3 -m pytest tests/test_state_store.py -v -k "run_id"`
Expected: FAIL — `run_id` not present in stored payload.

- [ ] **Step 3: Implement run_id persistence**

Edit `execution/core/state_store.py`.

Add helper near the top of the file (after `_now_iso`):

```python
def _current_run_id() -> Optional[str]:
    """Pull the active EventBus run_id, if any. None outside a decorator.

    Import is lazy to avoid event_bus importing state_store importing
    event_bus (circular). Callers treat None as 'no run_id to persist'."""
    try:
        from execution.core.event_bus import get_current_bus
    except Exception:
        return None
    bus = get_current_bus()
    return bus.run_id if bus is not None else None
```

Modify the 4 record functions to include `run_id` in the stored payload. Example for `record_success`:

```python
def record_success(workflow: str, summary: dict, duration_ms: int) -> None:
    """Record a successful run. Clears failure streak."""
    client = _get_client()
    if client is None:
        return
    try:
        payload = {
            "status": "success",
            "time_iso": _now_iso(),
            "summary": summary,
            "duration_ms": duration_ms,
        }
        run_id = _current_run_id()
        if run_id is not None:
            payload["run_id"] = run_id
        _write_last_run(client, workflow, payload)
        client.delete(f"wf:streak:{workflow}")
    except Exception as exc:
        logger.warning(f"state_store.record_success failed: {exc}")
```

Apply the same `run_id = _current_run_id(); if run_id is not None: payload["run_id"] = run_id` pattern to `record_failure`, `record_empty`, and `record_crash`. Do NOT add run_id to the `_push_failure` entry — that list's entries are `{time, reason}` only, and /tail resolves by run_id not by failure-list scan.

- [ ] **Step 4: Run tests to confirm they pass**

Run: `/usr/bin/python3 -m pytest tests/test_state_store.py -v`
Expected: all pass (new run_id tests + all existing).

- [ ] **Step 5: Commit**

```bash
git add execution/core/state_store.py tests/test_state_store.py
git commit -m "feat(observability): persist run_id in wf:last_run when bus is active

record_success/failure/empty/crash pull the active EventBus run_id from
get_current_bus() and store it in the last_run JSON payload. Unblocks
/tail <workflow> resolving the most recent run_id without explicit arg."
```

---

## Task 3: `/tail` command handler

**Why third:** Consumes the `run_id` persisted in Task 2. Tests can mock supabase + state_store independently.

**Files:**
- Modify: `webhook/bot/routers/commands.py` (add handler + helpers)
- Create: `tests/test_tail_command.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_tail_command.py`:

```python
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
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `/usr/bin/python3 -m pytest tests/test_tail_command.py -v`
Expected: ImportError (`cmd_tail` doesn't exist yet).

- [ ] **Step 3: Implement `/tail` handler**

Edit `webhook/bot/routers/commands.py`.

Add imports at top (alongside existing imports):

```python
from aiogram.filters import Command, CommandObject
from status_builder import ALL_WORKFLOWS
```

Add module-level Supabase client factory (near top of file, so tests can monkeypatch):

```python
def _get_supabase_client():
    """Return a supabase-py Client, or None if credentials/lib missing.
    Extracted so tests can monkeypatch."""
    import os
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        logger.warning(f"/tail: supabase init failed: {exc}")
        return None
```

Add the handler (in the `# ── Admin router ──` section, alongside `cmd_status`):

```python
@admin_router.message(Command("tail"))
async def cmd_tail(message: Message, command: CommandObject):
    args = (command.args or "").strip().split()
    if not args:
        await message.reply(_tail_help())
        return

    workflow = args[0]
    explicit_run_id = args[1] if len(args) > 1 else None

    if workflow not in ALL_WORKFLOWS:
        await message.reply(
            f"Workflow desconhecido: `{workflow}`.\n\n"
            f"Disponíveis: {', '.join(ALL_WORKFLOWS)}"
        )
        return

    run_id = explicit_run_id
    if run_id is None:
        from execution.core import state_store
        status = state_store.get_status(workflow)
        if status is None:
            await message.reply(f"Nenhum run recente de `{workflow}`.")
            return
        run_id = status.get("run_id")
        if run_id is None:
            await message.reply(
                f"Run mais recente de `{workflow}` sem run_id (legacy, anterior ao Phase 4).\n"
                f"Use `/tail {workflow} <run_id>` com um ID explícito."
            )
            return

    client = _get_supabase_client()
    if client is None:
        await message.reply("⚠️ Supabase indisponível — não consigo buscar eventos.")
        return

    try:
        events = (
            client.table("event_log")
            .select("ts, level, event, label, detail")
            .eq("workflow", workflow)
            .eq("run_id", run_id)
            .order("ts", desc=False)
            .limit(30)
            .execute()
        )
    except Exception as exc:
        logger.error(f"/tail event_log query failed: {exc}")
        await message.reply(f"⚠️ Erro ao consultar event_log: {str(exc)[:100]}")
        return

    rows = events.data or []
    if not rows:
        await message.reply(
            f"📜 `{workflow}.{run_id}` — sem eventos no event_log."
        )
        return

    await message.reply(_format_tail(workflow, run_id, rows))


def _tail_help() -> str:
    return (
        "📜 *Uso do /tail*\n\n"
        "`/tail <workflow>` — últimos 30 eventos do run mais recente\n"
        "`/tail <workflow> <run_id>` — últimos 30 eventos de um run específico\n\n"
        f"Workflows: {', '.join(ALL_WORKFLOWS)}"
    )


def _format_tail(workflow: str, run_id: str, rows: list) -> str:
    level_emoji = {"info": "ℹ️", "warn": "⚠️", "error": "🚨"}
    lines = [f"📜 `{workflow}.{run_id}` (últimos {len(rows)} eventos)\n"]
    for row in rows:
        ts = (row.get("ts") or "")
        hhmmss = ts[11:19] if len(ts) >= 19 else ts
        emoji = level_emoji.get(row.get("level", "info"), "•")
        event = row.get("event", "?")
        label = row.get("label") or ""
        line = f"{hhmmss} {emoji} {event}"
        if label:
            line += f" — {label[:80]}"
        lines.append(line)
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to confirm they pass**

Run: `/usr/bin/python3 -m pytest tests/test_tail_command.py -v`
Expected: 9 passed.

- [ ] **Step 5: Smoke-test import path**

Run: `/usr/bin/python3 -c "from webhook.bot.routers.commands import cmd_tail, _format_tail, _tail_help; print('ok')"`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add webhook/bot/routers/commands.py tests/test_tail_command.py
git commit -m "feat(observability): add /tail command for event_log timeline

/tail <workflow>            → last 30 events of most recent run
/tail <workflow> <run_id>   → last 30 events of a specific run
/tail                       → help + available workflows

Resolves default run_id via state_store.get_status(). Degrades gracefully
when Supabase is unavailable, run_id is missing (legacy runs), or the
event_log returns no rows."
```

---

## Task 4: Pilot instrumentation — `morning_check` + `send_daily_report`

**Why fourth:** Ship /tail command working end-to-end on two well-understood scripts before rolling to all 7. Confirms event volume, batching behavior in events channel, and output quality in /tail.

**Files:**
- Modify: `execution/scripts/morning_check.py`
- Modify: `execution/scripts/send_daily_report.py`

**Emit conventions (apply consistently here, reused in Task 5):**

| event | level | when to emit | label example | detail example |
|---|---|---|---|---|
| `step` | info | at the start of a logical phase (fetch, process, persist, notify) | `"Baixando dados Platts"` | `None` (or small summary dict) |
| `api_call` | info | immediately after a successful upstream call | `"platts.get_futures"` | `{"status": 200, "duration_ms": 340, "rows": 47}` |
| `api_call` | warn | upstream call failed but script recovered | `"lseg.fetch (fallback)"` | `{"status": 503, "duration_ms": 1200, "error": "timeout"}` |

Don't emit inside tight loops (>10 iterations). Emit loop summaries only (1 emit before, 1 after).

- [ ] **Step 1: Read morning_check to identify instrumentation points**

Run: `/usr/bin/python3 -c "import ast,sys;print(open('execution/scripts/morning_check.py').read())" | head -250`
Expected: read the file, identify 4-6 logical phases: (1) Redis connect, (2) Platts fetch, (3) LSEG fetch, (4) data merge/processing, (5) Supabase persist, (6) WhatsApp send.

- [ ] **Step 2: Add `get_current_bus()` import + step/api_call emits to morning_check**

Edit `execution/scripts/morning_check.py`. At the top of `main()` (after existing decorator), grab the bus:

```python
from execution.core.event_bus import get_current_bus

@with_event_bus("morning_check")
def main():
    bus = get_current_bus()
    # ... rest of main() stays the same, with emits interspersed
```

Add emit calls at each logical phase boundary. Example pattern:

```python
    bus.emit("step", label="Conectando ao Redis")
    redis_client = get_redis_client()

    bus.emit("step", label="Baixando dados Platts")
    import time
    t0 = time.time()
    platts_data = fetch_platts()
    bus.emit("api_call", label="platts.get",
             detail={"duration_ms": int((time.time() - t0) * 1000), "rows": len(platts_data)})
```

Add 4-6 step/api_call emits at natural phase boundaries. Each emit is 1-2 lines. Bus can be None only if `main()` is called outside the decorator (e.g., by a test) — add a tiny guard at the top:

```python
    bus = get_current_bus()
    if bus is None:
        # Degrade to a no-op bus for isolated test runs
        from execution.core.event_bus import EventBus
        bus = EventBus(workflow="morning_check")
```

Keep the guard out if every path already uses the decorator (check call sites).

- [ ] **Step 3: Add the same instrumentation to send_daily_report**

Edit `execution/scripts/send_daily_report.py`. Identify 3-5 phase boundaries (load contacts from Sheets, build message, call uazapi/delivery_reporter, persist state). Add `step` + `api_call` emits.

- [ ] **Step 4: Run full test suite — no regressions**

Run: `/usr/bin/python3 -m pytest tests/ -v --ignore=tests/test_e2e -x 2>&1 | tail -40`
Expected: all pass. A few tests may need `fake_redis` + `_active_bus` stubs; fix on the spot only if tests fail.

- [ ] **Step 5: Manual smoke test (document, don't execute in CI)**

Add a markdown note to the commit message:

```
Manual validation steps:
1. Trigger morning_check via GitHub Actions workflow_dispatch.
2. Observe events channel: expect ~5 info lines per phase, batched into
   1-3 messages over the run duration.
3. Run `/tail morning_check` in the bot → expect ~8-10 events including
   cron_started, step entries, api_call entries, cron_finished.
4. Verify GitHub Actions stdout contains corresponding JSON lines.
```

- [ ] **Step 6: Commit**

```bash
git add execution/scripts/morning_check.py execution/scripts/send_daily_report.py
git commit -m "feat(observability): instrument morning_check + send_daily_report with step/api_call

Adds bus.emit('step', ...) and bus.emit('api_call', ...) at major phase
boundaries (Redis connect, upstream fetch, Supabase persist, WhatsApp
send). Bus is pulled from get_current_bus() — no signature change to
main(). Pilot for remaining 5 scripts in follow-up commit."
```

---

## Task 5: Roll out instrumentation to remaining 5 scripts

**Why fifth:** After pilot validates event volume and /tail output, roll out the same pattern to the rest. Each script gets 2-4 emits. Keep it light — step+api_call at major boundaries only.

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py`
- Modify: `execution/scripts/platts_ingestion.py`
- Modify: `execution/scripts/platts_reports.py`
- Modify: `execution/scripts/send_news.py`
- Modify: `execution/scripts/rebuild_dedup.py`

- [ ] **Step 1: Instrument baltic_ingestion**

Edit `execution/scripts/baltic_ingestion.py`. Add `from execution.core.event_bus import get_current_bus`. Inside `main()`, grab `bus = get_current_bus()` and add 3-4 step/api_call emits at: (1) Apify run start, (2) Apify poll/complete, (3) Supabase write, (4) notify operator.

- [ ] **Step 2: Instrument platts_ingestion**

Edit `execution/scripts/platts_ingestion.py`. Add 3-4 emits at: (1) Apify trigger, (2) wait/poll completion, (3) parse + dedup, (4) Supabase persist.

- [ ] **Step 3: Instrument platts_reports**

Edit `execution/scripts/platts_reports.py`. Add 2-3 emits at: (1) fetch staging rows, (2) generate report via Anthropic, (3) persist/notify.

- [ ] **Step 4: Instrument send_news**

Edit `execution/scripts/send_news.py`. Add 2-3 emits at: (1) load queue, (2) send to WhatsApp via delivery_reporter, (3) archive.

- [ ] **Step 5: Instrument rebuild_dedup**

Edit `execution/scripts/rebuild_dedup.py`. Add 2 emits at: (1) scan Supabase rows, (2) write dedup cache to Redis.

- [ ] **Step 6: Run full test suite**

Run: `/usr/bin/python3 -m pytest tests/ -v --ignore=tests/test_e2e -x 2>&1 | tail -30`
Expected: all pass.

- [ ] **Step 7: Syntax-check all modified scripts**

Run: `/usr/bin/python3 -c "import ast; [ast.parse(open(p).read()) for p in ['execution/scripts/baltic_ingestion.py', 'execution/scripts/platts_ingestion.py', 'execution/scripts/platts_reports.py', 'execution/scripts/send_news.py', 'execution/scripts/rebuild_dedup.py']]; print('ok')"`
Expected: `ok`.

- [ ] **Step 8: Commit**

```bash
git add execution/scripts/baltic_ingestion.py execution/scripts/platts_ingestion.py execution/scripts/platts_reports.py execution/scripts/send_news.py execution/scripts/rebuild_dedup.py
git commit -m "feat(observability): instrument remaining 5 scripts with step/api_call

Extends pilot pattern from morning_check + send_daily_report to:
- baltic_ingestion (Apify run + Supabase persist)
- platts_ingestion (Apify trigger + parse + persist)
- platts_reports (fetch + Anthropic + notify)
- send_news (queue + uazapi + archive)
- rebuild_dedup (scan + write cache)

Events channel now shows meaningful phase detail; /tail returns 5-10+
events per run instead of just cron_started/finished."
```

---

## Task 6: Followups document

**Files:**
- Create: `docs/superpowers/followups/2026-04-21-observability-phase4-followups.md`

- [ ] **Step 1: Create followups doc**

Write `docs/superpowers/followups/2026-04-21-observability-phase4-followups.md`:

```markdown
# Observability Phase 4 Followups

**Shipped:** 2026-04-21 — `/tail` command + step/api_call instrumentation.

## Deferred from Phase 4 scope

### P1 — delivery_tick / delivery_summary bridge (events channel)
Spec §Rollout phase 4 mentioned optionally bridging `delivery_reporter` to emit `delivery_tick` per contact and `delivery_summary` at dispatch end. Skipped to keep Phase 4 tight. Revisit if operator wants per-contact detail in events channel.

### P2 — /tail level filter
`/tail <workflow> --level=warn` to filter events by level. YAGNI for MVP — revisit if /tail output is too noisy in practice.

### P3 — /tail pagination
Cap is currently 30 events. Long-running scripts may emit 50+ events. Add `/tail <workflow> --page=2` or truncate with "... X earlier events".

### P4 — trace_id propagation to Apify actors
Spec §Scope §item 8 mentions propagating `trace_id` via env var to Apify actors so their events link back. Not started; separate spec.

## Known limitations

### L1 — Legacy runs (pre-Phase 4) have no run_id
`/tail <workflow>` for a run that completed before this phase shipped will report "run mais recente ... sem run_id (legacy)". Operator must use `/tail <workflow> <explicit_run_id>` or wait for a new run.

### L2 — ContextVar doesn't propagate across threads/async without copy_context
If a script spawns a thread pool (none currently do) and calls `state_store.record_*` from the pool, `get_current_bus()` returns None. Not a problem today because all execution scripts are single-threaded synchronous.

## Verification checklist (post-deploy)

- [ ] `/tail morning_check` returns a formatted timeline with >3 events
- [ ] Events channel shows `step` and `api_call` lines (not just cron_started/finished)
- [ ] Trigger a script with `workflow_dispatch`, then run `/tail` — timeline matches what the script actually did
- [ ] Legacy run (if any exists) shows the "sem run_id" message, not a crash
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/followups/2026-04-21-observability-phase4-followups.md
git commit -m "docs(observability): add Phase 4 followups tracking"
```

---

## Self-review checklist (done by plan author)

- [x] Every spec Phase 4 requirement has a task (cmd `/tail` → Task 3; step/api_call → Tasks 4+5; state_store run_id → Task 2; get_current_bus → Task 1 prerequisite)
- [x] No placeholders — every step has concrete code or a specific command
- [x] Type consistency — `get_current_bus`, `_active_bus`, `_current_run_id`, `cmd_tail` signatures consistent across tasks
- [x] Tests first for Tasks 1-3 (TDD); Tasks 4-5 rely on existing decorator + bus contract already tested; smoke/syntax checks instead
- [x] Bite-sized steps (each 2-5 min)
- [x] Every task is independently shippable and reversible
- [x] Followup doc covers known limitations (L1 legacy runs, L2 threading)
- [x] Commit messages follow project convention (`feat(observability): ...`, short subject + body)

## Execution

Per user's request ("pode seguir com /writing-plans phase 4"), next step after plan approval:

- Use `superpowers:subagent-driven-development` with the dispatch flow (implementer → spec reviewer → code quality reviewer) per task.
- Work in a worktree: `.worktrees/obs-phase4/` off `main` (branch: `feature/observability-phase4`).
