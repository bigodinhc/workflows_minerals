# Observability: trace_id Propagation to Apify Actors — Design Spec

**Date:** 2026-04-22
**Status:** Approved — ready for implementation planning
**Owner:** bigodinhc
**Related:** `docs/superpowers/specs/2026-04-21-observability-unified-design.md` (P4 followup from Phase 4)

## Goal

Propagate the active `trace_id` from Python cron scripts to the Apify actors they trigger, so the operator can reconstruct the full execution tree (cron → actor → supabase rows) from a single SQL query against `event_log`.

## Motivation

Today the `@with_event_bus` decorator generates a `trace_id` per Python cron run and writes it to `event_log`. When that cron triggers an Apify actor (remote JS execution in Apify Cloud), the actor has no visibility into the parent's `trace_id` and emits nothing to `event_log` at all. Effect: when a row lands in Supabase from a Platts scrape, there is no structural link back to the GitHub Actions cron that initiated the chain. Debugging "why did this weird row appear at 09:07?" requires jumping between Apify Console logs, GH Actions logs, and Supabase manually.

This spec closes that gap by (1) passing `trace_id` + `parent_run_id` through the existing `run_input` dict and (2) adding a minimal `EventBus` JS implementation inside each in-scope actor that emits `cron_started`/`cron_finished`/`cron_crashed` to `event_log` with the inherited identifiers.

## Scope

**In scope (per Q1/Q2 discussion):**
1. Two production actors:
   - `bigodeio05/platts-scrap-full-news` (triggered by `platts_ingestion.py`)
   - `bigodeio05/platts-scrap-reports` (triggered by `platts_reports.py`)
2. Lifecycle events only: `cron_started`, `cron_finished`, `cron_crashed`. No `step` / `api_call` inside actors.
3. Python side: inject `trace_id` + `parent_run_id` into the `run_input` dict passed to `ApifyClient.run_actor`.
4. Actor side: new `src/lib/eventBus.js` (~50 lines) + `main.js` wrap with try/catch + 3 emits.
5. Tests: Python (assert `run_input` shape) + JS (vitest for `EventBus` class).

**Out of scope (explicit non-goals):**
- `step` / `api_call` instrumentation inside actors (P4.1 followup if needed)
- Retry / batching in the actor's `event_log` insert (best-effort)
- `/tail --trace=<id>` filter (can be added later without breaking this spec)
- `inspect_platts.py` instrumentation (CLI, not a cron, no consumer of trace_id)
- Legacy actors `platts-news-only`, `platts-scrap-price` (not triggered in production)

## Architecture

### Data flow

```
GitHub Actions cron
 └─> Python script (@with_event_bus decorator active)
      ├─> EventBus Python emits events to event_log
      │   (workflow=<script_name>, trace_id=<bus.trace_id>, parent_run_id=null)
      └─> ApifyClient.run_actor(run_input={
              ...business_fields,
              trace_id: bus.trace_id,       ← NEW, injected
              parent_run_id: bus.run_id,    ← NEW, injected
          })
           └─> Apify Cloud (remote JS execution)
                ├─> EventBus JS emits cron_started to event_log
                │   (workflow=<actor_slug>, trace_id=<inherited>, parent_run_id=<cron.run_id>)
                ├─> ...existing actor logic, untouched...
                ├─> EventBus JS emits cron_finished (or cron_crashed in catch)
                └─> Returns dataset_id to Python script
```

### Component boundaries

| Component | File | Responsibility |
|---|---|---|
| Python: run_input injection | `execution/scripts/platts_ingestion.py`, `execution/scripts/platts_reports.py` | Add `trace_id` + `parent_run_id` to the dict passed to `ApifyClient.run_actor`, sourced from `get_current_bus()` |
| Actor: EventBus JS | `actors/platts-scrap-full-news/src/lib/eventBus.js`, `actors/platts-scrap-reports/src/lib/eventBus.js` | Same logic, two independent copies (Apify package isolation, Q5b=1) |
| Actor: main wrap | `actors/<name>/src/main.js` | Construct `EventBus`, emit lifecycle events, wrap existing body in try/catch |
| Tests Python | `tests/test_platts_ingestion_trace.py`, `tests/test_platts_reports_trace.py` | Assert `run_input` carries trace_id + parent_run_id when bus is active |
| Tests JS | `actors/<name>/tests/eventBus.test.js` | vitest (already in actor package.json) unit tests for EventBus |

## Wire Format

### Python side (run_input additions)

`ApifyClient.run_actor(actor_id, run_input, ...)` accepts an arbitrary dict. Add two fields alongside existing business fields. Keys use snake_case to match the `event_log` column names and to be visually distinct from the actor's camelCase business fields.

```python
# Inside the decorated main() or _run_with_progress async helper:
bus = get_current_bus()

run_input = {
    # ...existing business fields (reportTypes, telegramChatId, etc.)
}
if bus is not None:
    run_input["trace_id"] = bus.trace_id
    run_input["parent_run_id"] = bus.run_id

dataset_id = client.run_actor(ACTOR_ID, run_input, ...)
```

When no bus is active (e.g., CLI smoke test), the fields are omitted entirely (not `None`) so the actor's fallback path kicks in.

### Actor side (input parsing)

```js
const input = (await Actor.getInput()) ?? {};
const {
    // ...existing business fields destructured above...
    trace_id: inheritedTraceId,   // may be undefined
    parent_run_id: parentRunId,   // may be undefined
} = input;
```

## EventBus JS Library

### API

```js
export class EventBus {
    constructor({ workflow, traceId, parentRunId }) { ... }
    async emit(event, { label, detail, level } = {}) { ... }
    // Read-only properties
    get runId() { return this._runId; }
    get traceId() { return this._traceId; }
}
```

### Construction logic (trace hierarchy)

- `workflow`: required. Snake_case actor identifier (e.g., `"platts_scrap_full_news"`).
- `traceId`: optional. If provided, inherited from parent. If absent, defaults to the new `runId` (actor becomes a new root trace — handles manual Apify Console runs per Q5a=1).
- `parentRunId`: optional. If absent, stored as `null`.
- `runId`: always fresh-generated via `crypto.randomBytes(4).toString('hex')` (8-char hex, matches Python's `secrets.token_hex(4)`).

### Emit contract

- Writes one JSON line to stdout (mirrors Python `_StdoutSink` — surfaces in Apify run logs).
- Inserts one row into `event_log` via `@supabase/supabase-js` (already an actor dependency).
- Never raises. Supabase absence (env vars missing) → stdout-only. Supabase insert failure → `console.warn(...)` + swallowed.

### Reference implementation

```js
// actors/<name>/src/lib/eventBus.js
import { createClient } from '@supabase/supabase-js';
import crypto from 'crypto';

const VALID_LEVELS = new Set(['info', 'warn', 'error']);

function generateRunId() {
    return crypto.randomBytes(4).toString('hex');
}

function initSupabase() {
    const url = process.env.SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
    if (!url || !key) return null;
    try {
        return createClient(url, key);
    } catch (err) {
        console.warn('EventBus supabase init failed:', err.message);
        return null;
    }
}

export class EventBus {
    constructor({ workflow, traceId, parentRunId }) {
        if (!workflow) throw new Error('EventBus: workflow is required');
        this._workflow = workflow;
        this._runId = generateRunId();
        this._traceId = traceId ?? this._runId;
        this._parentRunId = parentRunId ?? null;
        this._supabase = initSupabase();
    }

    get runId() { return this._runId; }
    get traceId() { return this._traceId; }

    async emit(event, { label = null, detail = null, level = 'info' } = {}) {
        if (!VALID_LEVELS.has(level)) level = 'info';
        const row = {
            workflow: this._workflow,
            run_id: this._runId,
            trace_id: this._traceId,
            parent_run_id: this._parentRunId,
            level,
            event,
            label,
            detail,
        };
        // stdout sink (always)
        console.log(JSON.stringify({ ts: new Date().toISOString(), ...row }));
        // supabase sink (best-effort)
        if (this._supabase) {
            try {
                await this._supabase.from('event_log').insert(row);
            } catch (err) {
                console.warn('EventBus supabase insert failed:', err.message);
            }
        }
    }
}
```

## Actor main.js Wrapping Pattern

Each actor applies the same wrap. Preserve 100% of existing actor logic between `cron_started` and `cron_finished` — only add structural try/catch + emits.

```js
// actors/platts-scrap-reports/src/main.js (shape only; business fields omitted)
import { Actor } from 'apify';
import { EventBus } from './lib/eventBus.js';
// ...other existing imports...

await Actor.init();

const input = (await Actor.getInput()) ?? {};
const {
    // ...existing destructuring...
    trace_id: inheritedTraceId,
    parent_run_id: parentRunId,
} = input;

const bus = new EventBus({
    workflow: 'platts_scrap_reports',
    traceId: inheritedTraceId,
    parentRunId: parentRunId,
});

await bus.emit('cron_started', {
    detail: { apify_run_id: Actor.config.actorRunId },
});

try {
    // ===== EXISTING ACTOR LOGIC UNTOUCHED =====
    // login, scrape, download, upload, notify, etc.
    // ==========================================

    await bus.emit('cron_finished', {
        detail: {
            // summary counts matching Python convention (total/success/failure when applicable)
            downloaded: downloadedCount,
            errors: errorsCount,
        },
    });
    await Actor.exit();
} catch (err) {
    await bus.emit('cron_crashed', {
        label: `${err.name}: ${String(err.message || '').slice(0, 100)}`,
        detail: {
            exc_type: err.name,
            exc_str: String(err).slice(0, 500),
        },
        level: 'error',
    });
    await Actor.fail(err.message || String(err));
}
```

Note: `Actor.fail` still produces the Apify "failed" run status, so Apify Console remains accurate. The crash event fires BEFORE `Actor.fail` so it persists even if `Actor.fail` teardown is destructive.

## Testing Strategy

### Python unit tests (new, 2 files)

```python
# tests/test_platts_ingestion_trace.py
def test_run_input_includes_trace_ids_when_bus_active(fake_redis, monkeypatch):
    """When @with_event_bus is active, run_input carries trace_id + parent_run_id."""
    from execution.core import event_bus
    captured = {}

    class FakeApifyClient:
        def run_actor(self, actor_id, run_input, **kw):
            captured.update(run_input)
            return "dataset_fake"

    monkeypatch.setattr("execution.integrations.apify_client.ApifyClient", lambda: FakeApifyClient())
    # ... monkeypatch supabase/sheets/etc as needed ...

    bus = event_bus.EventBus(workflow="platts_ingestion")
    token = event_bus._active_bus.set(bus)
    try:
        from execution.scripts import platts_ingestion
        # Invoke the helper that builds and fires run_input
        # (may require a small refactor to extract the run_input construction into
        # a testable function; see followup note below)
        ...
    finally:
        event_bus._active_bus.reset(token)

    assert captured["trace_id"] == bus.trace_id
    assert captured["parent_run_id"] == bus.run_id


def test_run_input_omits_trace_ids_when_no_bus(...):
    """Outside the decorator, run_input has no trace_id/parent_run_id keys."""
    ...
    assert "trace_id" not in captured
    assert "parent_run_id" not in captured
```

Identical structure for `tests/test_platts_reports_trace.py`.

**Testability note:** the current `_run_with_progress` async helpers construct `run_input` inline. Plan may need a small extraction into a pure `_build_run_input(args, bus, ...)` helper so tests can invoke it without running the whole actor pipeline. Judgement call for the implementer during planning.

### JS unit tests (new, 2 files — identical)

```js
// actors/<name>/tests/eventBus.test.js
import { describe, it, expect, vi } from 'vitest';
import { EventBus } from '../src/lib/eventBus.js';

describe('EventBus', () => {
    it('inherits trace_id from constructor arg', () => {
        const bus = new EventBus({ workflow: 'test', traceId: 'abc12345', parentRunId: 'xyz' });
        expect(bus.traceId).toBe('abc12345');
    });

    it('generates new trace_id when none provided (root trace)', () => {
        const bus = new EventBus({ workflow: 'test' });
        expect(bus.traceId).toBe(bus.runId);
    });

    it('generates 8-char hex runId', () => {
        const bus = new EventBus({ workflow: 'test' });
        expect(bus.runId).toMatch(/^[0-9a-f]{8}$/);
    });

    it('parentRunId defaults to null', () => {
        const bus = new EventBus({ workflow: 'test' });
        expect(bus['_parentRunId']).toBeNull();
    });

    it('throws when workflow is missing', () => {
        expect(() => new EventBus({})).toThrow();
    });

    it('emit writes to supabase when available', async () => {
        const insertMock = vi.fn().mockResolvedValue({});
        const bus = new EventBus({ workflow: 'test' });
        bus._supabase = { from: () => ({ insert: insertMock }) };
        await bus.emit('cron_started');
        expect(insertMock).toHaveBeenCalledWith(expect.objectContaining({
            event: 'cron_started', workflow: 'test',
        }));
    });

    it('emit is never-raise when supabase throws', async () => {
        const bus = new EventBus({ workflow: 'test' });
        bus._supabase = { from: () => ({ insert: () => Promise.reject(new Error('boom')) }) };
        await expect(bus.emit('cron_started')).resolves.not.toThrow();
    });

    it('coerces invalid level to info', async () => {
        const insertMock = vi.fn().mockResolvedValue({});
        const bus = new EventBus({ workflow: 'test' });
        bus._supabase = { from: () => ({ insert: insertMock }) };
        await bus.emit('cron_started', { level: 'critical' });
        expect(insertMock).toHaveBeenCalledWith(expect.objectContaining({ level: 'info' }));
    });
});
```

### Manual validation (post-deploy)

1. Trigger `platts_reports.yml` workflow via GitHub workflow_dispatch.
2. In Telegram, run `/tail platts_reports` → capture the displayed `trace_id` from any event row (via SQL if needed).
3. Run in Supabase SQL editor:
   ```sql
   SELECT ts, workflow, event, level, run_id, parent_run_id
   FROM event_log
   WHERE trace_id = '<captured_trace_id>'
   ORDER BY ts;
   ```
4. Expect ~6 rows: 3 from Python (`workflow='platts_reports'`) + 3 from actor (`workflow='platts_scrap_reports'`, `parent_run_id` matches the Python cron's `run_id`).
5. Run `/tail platts_scrap_reports` in the bot — actor-only timeline returns.
6. Manually start the actor in Apify Console WITHOUT `trace_id` in input → verify a new root trace is created, `parent_run_id IS NULL`, emits still succeed.

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Actor `event_log` insert fails (credentials, network) | Low | 1 run without trace row | Never-raise; logs to stderr; actor run still succeeds |
| Schema mismatch (actor sends field event_log doesn't accept) | Very low | Insert rejected | Same schema as Python; vitest locks shape at unit level |
| Actor deployed WITHOUT Python mirror deploy (or vice versa) | Medium | Brief period of orphan root traces | Actor fallback (`traceId ?? runId`) still emits — just not correlated with cron; no silent failure |
| Apify rate limit on Supabase | Negligible | Slow insert | 3 inserts per actor run × ~3 runs/day = ~10 inserts/day per actor |
| `Actor.fail` running before `bus.emit('cron_crashed')` finishes flushing | Low | Missing crash event | `await` on emit BEFORE `Actor.fail`; stdout log is captured regardless |

## Dependencies / Prerequisites

- `@supabase/supabase-js` already in both actor's `package.json` ✓
- `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` already available as env vars in Apify Cloud for both actors ✓
- `event_log` table already has `trace_id` + `parent_run_id` columns (landed in 2026-04-21 migration) ✓
- `vitest` already in both actor's devDependencies ✓
- No Python dependency changes required

## Success Criteria

After this spec ships:

1. `SELECT * FROM event_log WHERE trace_id = <X> ORDER BY ts` returns Python + actor rows chronologically interleaved for any cron-triggered actor run.
2. Actor runs initiated manually from Apify Console produce `event_log` rows with a fresh root trace (`parent_run_id IS NULL`).
3. Deliberate actor crash produces a `cron_crashed` event persisted to `event_log` with `level=error`.
4. `/tail platts_scrap_reports` returns the actor-only timeline for the most recent actor run.
5. All Python + JS tests green in CI.
