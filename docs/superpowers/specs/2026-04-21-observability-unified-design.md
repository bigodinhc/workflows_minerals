# Observability Unified — Design Spec

**Date:** 2026-04-21
**Status:** Approved — ready for implementation planning
**Owner:** bigodinhc

## Goal

Give the operator **live visibility into every workflow run on Telegram**, without drowning the primary chat. Close four concrete blind spots: (1) crons that silently fail to start, (2) crashes before progress_reporter initializes, (3) upstream API errors (Platts/LSEG/Apify) that never reach Sentry, (4) the missing read-side for the `event_log` (Supabase table already being written to, never queried). Introduce a single event bus so every emitter funnels through one contract.

## Motivation

Today's state:
- `progress_reporter.py` edits one Telegram card per workflow as it runs. Clean but per-workflow; you only see a workflow once it's started.
- `delivery_reporter.py` summarizes WhatsApp delivery at dispatch end (categorized, actionable).
- `state_store.py` fires a streak alert after 3 consecutive failures.
- `/status` command shows last-run per workflow.

Blind spots:
- Cron scheduled for 09:00 doesn't run at all (GH Actions queue backup, runner failure) → operator notices only when they manually check `/status` later in the day.
- Script crashes during import or config load (before `progress.start()`) → no Telegram message, no Sentry capture (4 of 7 scripts don't init Sentry).
- `progress.fail()` edits the existing card with a crash marker but doesn't push a new message → if operator scrolled past, miss is silent.
- Apify/Platts/LSEG failures during upstream fetch → captured in `WorkflowLogger` (ephemeral, gone in 90 days with the GH Actions run).
- No correlation between Apify run → Supabase row → GH Actions run → WhatsApp send. Debugging a bad price takes hours of jumping between systems.
- `event_log` table (written to by `progress_reporter.py` async path) has zero readers.

Desired state (after this spec):
- Primary chat stays clean (cards + real alerts, as today).
- Dedicated "events" Telegram channel shows a live firehose of every workflow event — operator opens it only when they want to see detail.
- Watchdog cron independently alerts if a scheduled workflow doesn't produce a run within 15 min of its expected start.
- Every uncaught script exception produces a Telegram alert + Sentry capture, regardless of where in the script lifecycle it occurs.
- `/tail <workflow>` command returns the last 30 events of the most recent run from `event_log` — operator queries on demand.
- Every event carries a `trace_id` that survives across Apify → Supabase → GH Actions → WhatsApp.

## Scope

**In scope:**
1. New module `execution/core/event_bus.py` with 4 sinks (stdout log, Supabase `event_log`, main-chat Telegram, events-channel Telegram). Also sets Sentry breadcrumbs.
2. New `event_log` Supabase table (if not already present with required fields) + indexes + TTL.
3. New watchdog GH Action `.github/workflows/watchdog.yml` running every 5 min.
4. New `/tail` command handler in `webhook/bot/routers/commands.py`.
5. `@with_event_bus` decorator for script `main()` functions — emits cron_started / cron_finished / cron_crashed, ensures alert on crash even before `progress.start()`.
6. `progress.fail()` pushes a new Telegram message in addition to editing the card.
7. Sentry `init_sentry(__name__)` added to the 4 scripts missing it.
8. `trace_id` generated at top of script `main()` and propagated via env var to any Apify actor runs triggered from the script. Apify actors emit events with the inherited `trace_id`.
9. Phased rollout plan (4 phases) so each phase ships value independently.

**Out of scope (explicit non-goals):**
- Grafana / Prometheus time-series dashboards. The user's stated need is live feed, not trending. Ship if/when trending becomes a felt need.
- Email / SMS / PagerDuty escalation. Telegram is sufficient for single-operator. Revisit when team grows.
- Centralized log shipping (Datadog, Loki, CloudWatch). Supabase `event_log` is the persistent sink; GH Actions run logs remain ephemeral but survive 90 days.
- Dashboard Next.js live feed (WebSocket / Supabase realtime). Keep current dashboard as-is; rely on `/tail` + events channel for live visibility. Can be added later without breaking this spec.
- Retry logic. The `execution/core/retry.py` decorator is unused in production; adding retries to Platts/LSEG/Apify calls is a separate concern (different YAGNI decision).
- Migrating `parse_mode="Markdown"` to `MarkdownV2` or HTML. Known open followup (P6 from categorized-alerts followups); separate spec when the next Telegram formatting touch-up lands.

## Architecture

### High-level flow

```
   scripts/webhook/actors              event_bus.emit(event, label, detail, level)
             │                                      │
             └──────────────────────────────────────┘
                                                    │
                         ┌──────────────┬───────────┼────────────┬────────────────┐
                         ▼              ▼           ▼            ▼                ▼
                    ┌────────┐    ┌──────────┐  ┌────────┐   ┌──────────┐    ┌─────────┐
                    │ stdout │    │ Supabase │  │ Telegram│   │ Telegram │    │ Sentry  │
                    │ JSON   │    │event_log │  │ card    │   │ events   │    │breadcrumb
                    │ line   │    │(persist) │  │(main    │   │ channel  │    │(context │
                    │        │    │          │  │ chat,   │   │(firehose)│    │ for     │
                    │        │    │          │  │ existing│   │          │    │ future  │
                    │        │    │          │  │ flow)   │   │          │    │ crash)  │
                    └────────┘    └──────────┘  └────────┘   └──────────┘    └─────────┘
                                     │
                                     │ read-side
                                     ▼
                              /tail command
                              (webhook/bot)
```

### Design principles

1. **Never-raise.** `event_bus.emit()` never lets a sink failure propagate. Each sink is wrapped in try/except that logs-and-moves-on.
2. **Single contract, multiple backends.** `emit()` is the one function everyone calls. Swapping Telegram for Slack later = change one file.
3. **Throttle at the sink, not the caller.** Callers emit freely. Telegram events-channel sink batches `info` events in 1-second windows to avoid Telegram's ~30 msg/sec limit. `warn`/`error` go immediately.
4. **Correlation propagation is structural.** Every emit carries `workflow`, `run_id`, `trace_id` automatically (pulled from the `EventBus` instance). Caller only supplies `event`, `label`, `detail`.
5. **Graceful degradation.** If Supabase is down, stdout + Sentry still work. If Telegram is down, Supabase still receives the event. No single failure blinds everything.

### Component boundaries

| Component | File | Responsibility | Depends on |
|---|---|---|---|
| **EventBus** | `execution/core/event_bus.py` (new, ~250 lines) | Fan-out to sinks with throttling + never-raise | `supabase-py`, `aiogram` / `TelegramClient`, `sentry_sdk` (all optional) |
| **TelegramEventsSink** | `execution/core/event_bus.py` (same file, `_TelegramEventsSink` class) | Formats + sends to events channel with 1s batching | `TelegramClient` |
| **`@with_event_bus` decorator** | `execution/core/event_bus.py` | Wraps `main()`; emits cron_started/finished/crashed; sentinel alert on crash | `EventBus`, `state_store` |
| **Watchdog script** | `execution/scripts/watchdog.py` (new, ~100 lines) | Reads state_store + cron schedule; emits cron_missed | `state_store`, `cron_parser`, `event_bus` |
| **Watchdog workflow** | `.github/workflows/watchdog.yml` (new) | Runs watchdog script every 5 min | GH Actions cron |
| **/tail command** | `webhook/bot/routers/commands.py` (edit, +40 lines) | Reads last N events from `event_log` for a run_id | `supabase-py` |
| **Supabase migration** | `supabase/migrations/YYYYMMDD_event_log.sql` (new, if needed) | Table schema + indexes + TTL | Supabase CLI |

All existing files (`delivery_reporter.py`, `progress_reporter.py`, `state_store.py`) are edited only where explicitly required by this spec — no drive-by refactoring.

## Data Model

### `event_log` table

```sql
CREATE TABLE IF NOT EXISTS event_log (
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  workflow    TEXT NOT NULL,
  run_id      TEXT NOT NULL,
  trace_id    TEXT,              -- optional: cross-system correlation id
  parent_run_id TEXT,            -- optional: for Apify actor runs triggered by a GH cron
  level       TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
  event       TEXT NOT NULL,     -- enum-like: see "Event vocabulary" below
  label       TEXT,              -- human-readable ("Baixando Platts", "send.uazapi")
  detail      JSONB,             -- structured payload (duration_ms, status_code, rows, etc.)
  pod         TEXT               -- optional: hostname / environment marker
);

CREATE INDEX IF NOT EXISTS idx_event_log_workflow_ts ON event_log (workflow, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_run_id      ON event_log (run_id);
CREATE INDEX IF NOT EXISTS idx_event_log_trace_id    ON event_log (trace_id);

-- TTL: 30 days. Implemented via pg_cron or manual Supabase scheduled function.
-- (If pg_cron is unavailable, omit and handle in a monthly cleanup script.)
```

**Backward compat check:** verify whether `event_log` already exists (written by `progress_reporter.py` async path). If yes, confirm columns cover this schema or write an `ALTER TABLE` migration. If the existing schema has `draft_id` but lacks `trace_id`/`parent_run_id`, add the missing columns as nullable.

### Event vocabulary

A fixed set of `event` values (enum-like, but stored as TEXT for extensibility):

| event | emitted by | meaning |
|---|---|---|
| `cron_started` | `@with_event_bus` decorator | script main() entered |
| `cron_finished` | decorator (success path) | main() returned without exception |
| `cron_crashed` | decorator (exception handler) | uncaught exception in main() |
| `cron_missed` | watchdog script | expected run didn't produce state by deadline |
| `step` | scripts (manual) | human-readable progress step ("Baixando Platts") |
| `api_call` | scripts (manual) | external API call completed ("platts.get", status/duration) |
| `delivery_tick` | `delivery_reporter` bridge | per-contact delivery event (optional; emitted only if events channel is active) |
| `delivery_summary` | `delivery_reporter` bridge | end-of-dispatch summary event (optional) |
| `breaker_tripped` | `delivery_reporter` | circuit breaker fired |

Adding a new `event` value does NOT require a migration — TEXT column handles it. New consumers (e.g., future dashboard) simply match on known values and ignore unknowns.

### ID conventions

- `run_id` — short UUID (8 char hex). Generated at top of script `main()` via the decorator. Same ID flows through `WorkflowLogger`, `progress_reporter`, `state_store`, `event_log`.
- `trace_id` — optional full UUID. Present when the workflow is part of a larger pipeline (e.g., Apify actor triggered by cron → inherits the cron's `trace_id`). Propagated via env var `TRACE_ID` when invoking child processes.
- `parent_run_id` — present when an Apify actor or sub-process inherits from a parent. Lets you reconstruct the tree: "this Apify run was triggered by morning_check run r8f3a".

## Component: `event_bus`

### API

```python
# execution/core/event_bus.py

class EventBus:
    """Fan-out event emitter. Never raises; sink failures are logged and swallowed."""

    def __init__(
        self,
        workflow: str,
        run_id: str | None = None,       # auto-generated if None
        trace_id: str | None = None,     # auto-generated if None
        parent_run_id: str | None = None,
    ):
        ...

    def emit(
        self,
        event: str,                      # from the event vocabulary
        label: str = "",
        detail: dict | None = None,
        level: str = "info",             # 'info' | 'warn' | 'error'
    ) -> None:
        """Fan-out to all enabled sinks. Never raises."""
        ...

    def child(self, sub_workflow: str) -> "EventBus":
        """Create a child bus with same trace_id, new run_id, parent_run_id set to self.run_id."""
        ...

    @property
    def run_id(self) -> str: ...
    @property
    def trace_id(self) -> str: ...
```

### Usage example

```python
from execution.core.event_bus import EventBus, with_event_bus

@with_event_bus(workflow="morning_check")
def main(bus: EventBus) -> None:
    bus.emit("step", label="Conectando ao Redis")
    redis_client = get_redis()

    bus.emit("step", label="Baixando dados Platts")
    t0 = time.time()
    data = platts_client.get_futures()
    bus.emit("api_call", label="platts.get",
             detail={"status": 200, "duration_ms": int((time.time()-t0)*1000), "rows": len(data)})

    bus.emit("step", label="Enviando WhatsApp")
    # ... existing delivery_reporter call, which will also emit events via a bridge
```

The decorator handles `cron_started` at entry, `cron_finished` at clean exit, `cron_crashed` on uncaught exception (+ pushes alert to main chat + `state_store.record_crash` + Sentry capture).

### Sinks

All sinks are private classes inside `event_bus.py`:

1. **`_StdoutSink`** — always enabled. Writes one JSON line per event to stdout (surfaces in GH Actions logs).
2. **`_SupabaseSink`** — enabled if Supabase client is available. Inserts row to `event_log`. On failure, logs warning.
3. **`_MainChatSink`** — enabled if `TELEGRAM_CHAT_ID` env var set AND event level is `warn` or `error` OR event type is `cron_crashed` / `cron_missed`. Sends distinct message (not edit).
4. **`_EventsChannelSink`** — enabled if `TELEGRAM_EVENTS_CHANNEL_ID` env var set. Batches `info` events in 1-second windows; `warn`/`error` flushed immediately. Uses same `TelegramClient` infrastructure.
5. **`_SentrySink`** — enabled if `sentry_sdk` initialized. Adds breadcrumb for every emit (so future crashes have last-20-events context). Calls `capture_exception` only when an actual exception is attached (via `detail["exc_info"]`).

Each sink exposes `is_enabled() -> bool` and `emit(event_dict) -> None`. `EventBus.emit` iterates sinks, calling each inside a try/except. No sink failure ever reaches the caller.

### Throttling (events channel only)

```python
class _EventsChannelSink:
    _BATCH_WINDOW_SECONDS = 1.0
    _MAX_MESSAGES_PER_SECOND = 25  # Telegram limit ~30; leave headroom

    def emit(self, event_dict: dict) -> None:
        if event_dict["level"] in ("warn", "error"):
            self._send_now(event_dict)
            return
        # Info: buffer and flush at window boundary
        self._buffer.append(event_dict)
        self._schedule_flush_if_needed()
```

Flush renders up to 20 events as a single multi-line Telegram message (keeps one message per ~1s window regardless of event volume).

### Error pre-propagation (cron_crashed)

When the `@with_event_bus` decorator catches an uncaught exception:

```python
def wrapper(*args, **kwargs):
    bus = EventBus(workflow=workflow_name)
    bus.emit("cron_started")
    try:
        return func(bus, *args, **kwargs)
    except BaseException as exc:
        bus.emit("cron_crashed", label=f"{type(exc).__name__}: {str(exc)[:100]}",
                 detail={"exc_type": type(exc).__name__, "exc_str": str(exc)[:500]},
                 level="error")
        # This single emit cascades to: stdout, event_log, main-chat alert, Sentry breadcrumb + capture
        raise  # Re-raise so GH Actions marks the run failed
```

The main-chat alert message uses a distinct format (🚨 emoji, workflow name, exception type, truncated message):

```
🚨 MORNING CHECK — CRASH
TypeError: unhashable type: 'dict'
run_id: r8f3a
[Ver dashboard](...)
```

## Component: Watchdog

### Detection logic

```python
# execution/scripts/watchdog.py
from datetime import datetime, timedelta, timezone
from execution.core import state_store, cron_parser
from execution.core.event_bus import EventBus
from webhook.status_builder import ALL_WORKFLOWS

GRACE_MINUTES = 15

def main() -> None:
    bus = EventBus(workflow="watchdog")
    now = datetime.now(timezone.utc)

    for workflow in ALL_WORKFLOWS:
        expected = cron_parser.parse_next_run(workflow)  # returns next scheduled run
        previous_expected = _previous_scheduled_run(workflow, now)
        if previous_expected is None:
            continue  # first-ever run; nothing to check

        deadline = previous_expected + timedelta(minutes=GRACE_MINUTES)
        if now < deadline:
            continue  # still within grace window

        last = state_store.get_status(workflow)
        if last is not None and _parse_iso(last["time_iso"]) >= previous_expected:
            continue  # run happened (possibly late, but happened)

        # Missed run. Alert (idempotently via a new public state_store helper).
        alert_key = f"wf:watchdog_alerted:{workflow}:{previous_expected.isoformat()}"
        if not state_store.try_claim_alert_key(alert_key, ttl_seconds=86400):
            continue  # already alerted for this miss

        bus.emit(
            "cron_missed",
            label=f"{workflow} não rodou",
            detail={
                "expected_iso": previous_expected.isoformat(),
                "deadline_iso": deadline.isoformat(),
                "last_run_iso": last["time_iso"] if last else None,
            },
            level="error",
        )
```

### Idempotency

The `wf:watchdog_alerted:{workflow}:{expected_iso}` Redis key (SET NX, 24h TTL) ensures only one alert per missed occurrence. Restarting the watchdog or having overlapping runs does not duplicate. Needs a new public helper in `state_store.py`:

```python
def try_claim_alert_key(key: str, ttl_seconds: int) -> bool:
    """Atomic SET NX with TTL. Returns True if claim succeeded (caller should alert),
    False if key already existed (caller should skip — someone else alerted)."""
    client = _get_client()
    if client is None:
        return True  # degrade: when Redis unavailable, alert anyway rather than suppress
    try:
        result = client.set(key, "1", nx=True, ex=ttl_seconds)
        return result is not None
    except Exception as exc:
        logger.warning(f"try_claim_alert_key failed: {exc}")
        return True
```

### Previous-run helper

`cron_parser.parse_next_run()` returns the NEXT run. Computing the PREVIOUS expected run requires walking backward from `next_run` by one cron interval. If the parser doesn't expose this, add a helper `cron_parser.parse_previous_run(workflow, now)`.

### Workflow YAML

```yaml
# .github/workflows/watchdog.yml
name: Watchdog
on:
  schedule:
    - cron: '*/5 * * * *'  # every 5 min
jobs:
  watchdog:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - env:
          REDIS_URL: ${{ secrets.REDIS_URL }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          TELEGRAM_EVENTS_CHANNEL_ID: ${{ secrets.TELEGRAM_EVENTS_CHANNEL_ID }}
          SENTRY_DSN: ${{ secrets.SENTRY_DSN }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
        run: python -m execution.scripts.watchdog
```

## Component: `/tail` command

### Usage

```
/tail morning_check            → last 30 events of the most recent morning_check run
/tail morning_check r8f3a      → last 30 events of a specific run_id
/tail                          → help text listing available workflows
```

### Implementation (new handler in `webhook/bot/routers/commands.py`)

```python
@admin_router.message(Command("tail"))
async def tail_handler(message: Message, command: CommandObject):
    args = (command.args or "").strip().split()
    if not args:
        await message.reply(_tail_help())
        return
    workflow = args[0]
    run_id = args[1] if len(args) > 1 else None

    if workflow not in ALL_WORKFLOWS:
        await message.reply(f"Workflow desconhecido. Disponíveis: {', '.join(ALL_WORKFLOWS)}")
        return

    if run_id is None:
        status = state_store.get_status(workflow)
        if status is None:
            await message.reply(f"Nenhum run recente de {workflow}.")
            return
        run_id = status.get("run_id")
        if run_id is None:
            await message.reply(f"Run mais recente de {workflow} sem run_id rastreável (legacy).")
            return

    events = (
        supabase.table("event_log")
        .select("ts, level, event, label, detail")
        .eq("workflow", workflow)
        .eq("run_id", run_id)
        .order("ts", desc=False)
        .limit(30)
        .execute()
    )

    await message.reply(_format_tail(workflow, run_id, events.data))
```

### Output format

```
📜 morning_check.r8f3a (últimos 30 eventos)

09:00:02 ℹ️ cron_started
09:00:05 ℹ️ step — Baixando dados Platts
09:00:08 ℹ️ api_call — platts.get status=200 dur=340ms
09:00:09 ✅ step — 47 rows parsed
09:00:12 ℹ️ step — Enviando WhatsApp
09:00:14 ℹ️ delivery_tick — 1/100 Ana
...
09:02:30 ✅ cron_finished — ok=95 fail=5
```

### `state_store` update needed

`state_store.record_success/failure/empty/crash` must also persist `run_id` in the `wf:last_run:{workflow}` payload so `/tail <workflow>` (without explicit run_id) can resolve it. One-line addition to each record function.

## Error gap fixes

All 4 land in Phase 1 so operator gets end-to-end crash visibility immediately.

### Fix 1: Top-of-main sentinel via `@with_event_bus`

Every script's `main()` is wrapped:

```python
# execution/scripts/morning_check.py (before)
def main():
    progress = ProgressReporter(...)
    progress.start()
    # ... rest of script

# after
from execution.core.event_bus import with_event_bus

@with_event_bus(workflow="morning_check")
def main(bus):
    progress = ProgressReporter(...)
    progress.start()
    # ... rest of script (no other changes required)
```

The decorator:
1. Creates `EventBus(workflow="morning_check")` → generates `run_id` and `trace_id`.
2. Calls `bus.emit("cron_started")`.
3. Invokes `main(bus)`.
4. On success → `bus.emit("cron_finished", detail={...})`.
5. On exception → `bus.emit("cron_crashed", level="error", detail={...})` → cascades to main-chat alert + Sentry + state_store + stdout.
6. Re-raises so GH Actions marks run failed.

Covers crashes before `progress.start()` (import errors, missing env vars, Redis connection failure).

### Fix 2: `progress.fail()` pushes new message

In `execution/core/progress_reporter.py`, extend `fail()`:

```python
def fail(self, exception: Exception) -> None:
    """..."""
    exc_text = str(exception)[:200]

    # (existing) Edit card
    if not self._disabled and self._message_id is not None:
        text = self._header("🚨", f"CRASH: {exc_text}")
        try:
            self._get_client().edit_message_text(...)
        except Exception as e:
            print(f"[WARN] ProgressReporter.fail telegram edit failed: {e}")

    # (NEW) Push distinct alert message so operator sees a notification
    try:
        alert_text = f"🚨 CRASH {self.workflow}: {exc_text[:120]}"
        self._get_client().send_message(text=alert_text, chat_id=self.chat_id)
    except Exception as e:
        print(f"[WARN] ProgressReporter.fail alert send failed: {e}")

    # (existing) state_store
    try:
        from execution.core import state_store
        state_store.record_crash(self.workflow, exc_text)
    except Exception as e:
        print(f"[WARN] ProgressReporter.fail state_store failed: {e}")
```

### Fix 3: Sentry init in 4 missing scripts

One line at top of `main()` in:
- `execution/scripts/morning_check.py`
- `execution/scripts/send_daily_report.py`
- `execution/scripts/send_news.py`
- `execution/scripts/rebuild_dedup.py`

```python
from execution.core.sentry_init import init_sentry

def main(bus):
    init_sentry(__name__)
    # ... rest
```

(Or fold into the decorator — call `init_sentry(script_name)` inside `with_event_bus` before emitting `cron_started`. Cleaner: no per-script edit needed beyond the decorator.)

### Fix 4: Sentry breadcrumbs from event_bus

`_SentrySink.emit` adds a breadcrumb for every event (purely for crash context enrichment, no capture):

```python
def emit(self, event_dict: dict) -> None:
    try:
        import sentry_sdk
        sentry_sdk.add_breadcrumb(
            category=event_dict["workflow"],
            level=event_dict["level"],
            message=event_dict.get("label") or event_dict["event"],
            data=event_dict.get("detail") or {},
        )
    except Exception:
        pass
```

The actual `capture_exception` call lives in the **`@with_event_bus` decorator**, not the sink — inside its `except BaseException` block, before re-raising. At that point `sys.exc_info()` is still valid and the last ~20 breadcrumbs are already buffered on the Sentry scope:

```python
except BaseException as exc:
    bus.emit("cron_crashed", level="error", ...)   # adds final breadcrumb
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)           # captures WITH breadcrumbs as context
    except Exception:
        pass
    raise
```

Result in Sentry: each crash issue shows a trail like "platts.get OK → lseg.fetch timeout retry=1 → lseg.fetch timeout retry=2 → CRASH".

## Testing strategy

### Unit tests (`tests/test_event_bus.py`, new)

- `test_emit_never_raises_when_all_sinks_disabled` — all env vars unset; emit is silent no-op.
- `test_emit_writes_stdout_json` — captures stdout via capsys, asserts one valid JSON line per emit.
- `test_emit_inserts_supabase_row` — mock Supabase client, assert insert called with expected payload.
- `test_emit_batches_info_events_to_events_channel` — mock TelegramClient, emit 5 info events within 100ms, assert exactly 1 send_message call after 1s window.
- `test_emit_sends_warn_immediately_to_events_channel` — emit 1 warn, assert immediate send.
- `test_emit_sends_error_to_main_chat` — emit level="error", assert main chat send.
- `test_emit_adds_sentry_breadcrumb` — mock sentry_sdk, assert add_breadcrumb called.
- `test_emit_continues_when_sink_raises` — configure one sink to raise, assert other sinks still fire.
- `test_with_event_bus_decorator_emits_cron_started_and_finished` — wrap a pass-through function, assert lifecycle events.
- `test_with_event_bus_decorator_catches_exception_and_re_raises` — wrap a raising function, assert cron_crashed emitted + original exception re-raised.
- `test_child_bus_inherits_trace_id` — create parent + child, assert trace_id equal, run_id different, parent_run_id set.

### Integration test (`tests/test_watchdog.py`, new)

- Mock `state_store` to return `last_run.time_iso` = yesterday, `cron_parser` to return `previous_expected` = 30 min ago. Run watchdog, assert `cron_missed` emitted.
- Same setup, but `last_run.time_iso` = 10 min ago (> previous_expected). Assert NO emit (run happened, just late).
- Idempotency: call watchdog twice in a row with same miss condition. Assert only 1 alert via Redis SET NX guard.

### Smoke test (`tests/test_tail_command.py`, new)

- Insert 5 events for a fake run_id into mocked event_log.
- Call `/tail <workflow>`. Assert reply contains 5 lines in time order.

### Manual validation checklist (pre-rollout)

1. Deploy Phase 1 to staging branch. Trigger `morning_check` manually. Verify:
   - Sentry dashboard shows "sentry_initialized" log line for morning_check.
   - `cron_started` / `cron_finished` rows appear in `event_log`.
   - GH Actions stdout has `{"event": "cron_started", ...}` line.
2. Deliberately break a script (add `raise RuntimeError("test")` at top of `main`). Verify:
   - Main chat receives `🚨 MORNING CHECK — CRASH ... RuntimeError: test`.
   - Sentry shows captured exception with `cron_crashed` breadcrumbs.
   - `state_store.record_crash` called (streak counter increments).
3. Phase 2: set `TELEGRAM_EVENTS_CHANNEL_ID`. Trigger workflow. Verify events channel receives batched `info` events + immediate `warn`/`error`.
4. Phase 3: disable a cron entirely. After 15 min, verify watchdog `cron_missed` alert in main chat.
5. Phase 4: run `/tail morning_check` in bot. Verify last 30 events returned in time order.

## Rollout phases

Each phase is atomically deployable. A phase landing does NOT require later phases to be complete.

### Phase 1 — Foundation (day 1-2, highest value per effort)

- Write `execution/core/event_bus.py` (stubs for all sinks, only Stdout + Supabase + Sentry active).
- Write `tests/test_event_bus.py` (unit tests).
- Apply `supabase/migrations/YYYYMMDD_event_log.sql` if table doesn't exist or schema mismatch.
- Add `@with_event_bus` decorator.
- Wrap `main()` of all 7 execution scripts with decorator.
- Add Sentry init via decorator (Fix 3+4 merged).
- Extend `progress.fail()` to push new message (Fix 2).

**Ship criterion:** all execution scripts emit `cron_started`/`cron_finished`/`cron_crashed`. Sentry captures all uncaught exceptions. Main chat gets alert for every crash (regardless of script lifecycle stage).

### Phase 2 — Events channel sink (day 3)

- Implement `_EventsChannelSink` with 1-second batching.
- Add `TELEGRAM_EVENTS_CHANNEL_ID` env var to all workflow YAMLs.
- Operator creates Telegram channel, adds bot as admin, captures channel ID, sets secret.

**Ship criterion:** operator opens events channel, triggers a workflow manually, sees live firehose.

### Phase 3 — Watchdog (day 4)

- Write `execution/scripts/watchdog.py` (+ `cron_parser.parse_previous_run` helper if needed).
- Write `tests/test_watchdog.py` (integration).
- Write `.github/workflows/watchdog.yml`.
- Set required secrets on the repo.

**Ship criterion:** operator can disable a cron and receive a `cron_missed` alert in main chat within 20 minutes (15 min grace + 5 min watchdog interval).

### Phase 4 — /tail + step instrumentation (day 5-6)

- Add `/tail` command handler in `webhook/bot/routers/commands.py`.
- Update `state_store.record_*` to persist `run_id`.
- Add `bus.emit("step", ...)` and `bus.emit("api_call", ...)` calls to key places in existing scripts (Platts fetch, LSEG fetch, Supabase writes, major loop boundaries).
- Optional: add `delivery_tick` / `delivery_summary` bridge in `delivery_reporter` so events channel also sees per-contact sends.

**Ship criterion:** `/tail morning_check` returns a detailed event timeline; events channel shows step-level detail, not just cron lifecycle.

### Total estimate

~5-6 working days for one engineer. Can be compressed to 3 days if error-fix sentinel (Fix 1-4) is done first and watchdog is deferred.

## Dependencies and prerequisites

- **Supabase credentials** with write access to `event_log` (existing service role key likely sufficient).
- **Telegram channel created + bot added as admin + channel ID captured** — operator action, blocks Phase 2 ship.
- **Redis reachable** from watchdog runner (same credentials as existing scripts).
- **`sentry_sdk` already in `requirements.txt`** — verify.
- **`cron_parser.parse_previous_run()` helper** — may need to add if current parser only returns next_run.

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Events channel hits Telegram rate limit under bursty load | Medium | Firehose stops mid-run; operator misses detail | 1s batching window + 25 msg/sec cap in `_EventsChannelSink`; drop oldest info events if buffer > 50 |
| Supabase `event_log` fills up (writes from every emit) | Low | Cost/performance creep | TTL 30 days via pg_cron or manual cleanup script; add alert on table size |
| Watchdog false positive (cron ran but Redis write failed) | Low | Spurious `cron_missed` alert | Watchdog only alerts if Redis READ also returns no recent entry; double-check via stdout log inspection would require different sink (out of scope) |
| `@with_event_bus` decorator breaks existing scripts | Medium | Scripts fail to start | Phase 1 tests include wrap-a-passthrough-function. Decorator is minimally invasive (injects `bus` kwarg). Rollback = remove decorator, script works as before. |
| `progress.fail()` new message floods chat on repeated retries | Low | Annoyance | No retry logic exists today; only one crash per run; acceptable |
| Operator sets wrong TELEGRAM_EVENTS_CHANNEL_ID → messages go to wrong chat | Low | Privacy leak | Bot requires admin permissions in channel; wrong ID likely returns "chat not found" failure, sink degrades gracefully |

## Open questions (deferred to implementation)

These do NOT block the spec but should be answered during Phase 1 implementation:

1. Does `event_log` table already exist (written by `progress_reporter.py` async path)? If yes, what's the current schema? (Check via Supabase UI or `\d event_log` in SQL editor.)
2. Does `cron_parser` already expose `parse_previous_run()`, or do we need to add it?
3. Should Apify actors emit events too (via direct `event_log` inserts, since they don't have Python SDK access to `event_bus`)? Out of scope now; revisit after Phase 4.
4. Should `/tail` support level filter (`/tail morning_check --level=warn`)? YAGNI for MVP; add if/when operator asks.

## Success criteria

After all 4 phases ship, the operator should be able to:

1. Run `/status` and see last-run state of all workflows (unchanged).
2. Open the events channel and scroll through the live firehose of what's happening across all workflows, right now.
3. Receive a main-chat alert within 20 min when any scheduled cron doesn't run.
4. Receive a main-chat alert for every script crash, regardless of whether it happened during import, config load, or after `progress.start()`.
5. Run `/tail morning_check` and see the detailed event timeline of the most recent run.
6. Click a Sentry issue and see the last ~20 events as breadcrumbs, making root-cause analysis 5-10× faster than reading raw GH Actions logs.

If all 6 are true, the spec's goal is met.
