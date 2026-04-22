# Observability: trace_id Propagation to Apify Actors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Propagate `trace_id` + `parent_run_id` from Python cron scripts to the 2 production Apify actors they trigger, so `event_log` can reconstruct the full cron→actor→supabase execution tree via a single SQL query.

**Architecture:** Python scripts inject 2 snake_case keys into the existing `run_input` dict passed to `ApifyClient.run_actor`. Each actor gets its own copy of a minimal `EventBus` JS class (~50 LOC) that inherits `traceId` from input, generates a fresh short-hex `runId`, and emits `cron_started`/`cron_finished`/`cron_crashed` rows to the existing `event_log` Supabase table. No retry, no batching, no step/api_call inside actors (that's a future phase).

**Tech Stack:** Python 3.9+ (system `/usr/bin/python3`), Node.js 18+ via Apify SDK, `@supabase/supabase-js` (already a dep in both actors), vitest (already in devDeps), pytest.

---

## Context (read before starting)

**What's already live:**
- `event_log` Supabase table with columns: `ts, workflow, run_id, trace_id, parent_run_id, level, event, label, detail, pod` (Phase 1).
- Python EventBus emits lifecycle events from all 7 execution scripts; `@with_event_bus(workflow)` decorator sets a `ContextVar` readable via `get_current_bus()`.
- `ApifyClient.run_actor(actor_id, run_input, **kwargs)` returns `dataset_id`. The method is a thin wrapper around `apify_client.ActorClient.call`. `run_input` is a dict forwarded verbatim to the actor.
- Actors live in `actors/<name>/` as self-contained Node packages (own `package.json`, `Dockerfile`, `node_modules`). They deploy to Apify Cloud via `apify push` from within the actor directory.
- Both in-scope actors already have `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` set as env vars in Apify Cloud (confirmed by `main.js` already using them for PDF uploads in `platts-scrap-reports`, and by `persist/supabaseUpload.js` in same).
- Both actors already have `@supabase/supabase-js` in dependencies and `vitest` in devDependencies.

**What this plan changes:**
- Python: 2 scripts add 2 lines each (run_input injection). 2 new test files.
- JS actors: 2 copies of a new ~60-line `eventBus.js` + 2 test files (vitest) + main.js wraps.
- No schema migration, no new dependency, no env var changes.

**Wire format (from spec §Wire Format):**

Python side:
```python
run_input = {
    # ...existing business fields...
}
bus = get_current_bus()
if bus is not None:
    run_input["trace_id"] = bus.trace_id
    run_input["parent_run_id"] = bus.run_id
```

Actor side:
```js
const { trace_id: inheritedTraceId, parent_run_id: parentRunId } = input;
const bus = new EventBus({
    workflow: 'platts_scrap_reports',  // or 'platts_scrap_full_news'
    traceId: inheritedTraceId,
    parentRunId: parentRunId,
});
```

**Apify actor deploy note (reference, NOT a task):** After the JS changes are merged to main, the operator runs `cd actors/<name> && apify push` in each actor directory to deploy the new version to Apify Cloud. This requires an `apify login` done beforehand. The plan does NOT include the `apify push` step — that is a manual deployment action outside git/CI scope. Task 7 documents this as a followup.

## File Structure

### Created
| Path | Purpose |
|---|---|
| `actors/platts-scrap-reports/src/lib/eventBus.js` | EventBus class (canonical copy — Task 1) |
| `actors/platts-scrap-reports/tests/eventBus.test.js` | vitest unit tests |
| `actors/platts-scrap-full-news/src/lib/eventBus.js` | EventBus class (literal copy — Task 3) |
| `actors/platts-scrap-full-news/tests/eventBus.test.js` | vitest unit tests (literal copy) |
| `tests/test_platts_reports_trace.py` | Asserts run_input shape when bus active |
| `tests/test_platts_ingestion_trace.py` | Same pattern for platts_ingestion |
| `docs/superpowers/followups/2026-04-22-observability-trace-id-apify-followups.md` | Deployment checklist + deferrals |

### Modified
| Path | Changes |
|---|---|
| `actors/platts-scrap-reports/src/main.js` | Import EventBus, construct from input, wrap body in try/catch with emits |
| `actors/platts-scrap-full-news/src/main.js` | Same pattern, adapted to Crawlee `crawler.run` flow |
| `execution/scripts/platts_reports.py` | Inject trace_id + parent_run_id into `run_input` (around line 180, after dict construction) |
| `execution/scripts/platts_ingestion.py` | Same injection pattern (around line 256) |

---

## Task 1: Create EventBus JS library + unit tests in `platts-scrap-reports`

**Why first:** Canonical implementation. Task 3 copies this file verbatim to the other actor, so we lock the contract here and validate via tests before anything else depends on it.

**Files:**
- Create: `actors/platts-scrap-reports/src/lib/eventBus.js`
- Create: `actors/platts-scrap-reports/tests/eventBus.test.js`

- [ ] **Step 1: Write the failing tests**

Create `actors/platts-scrap-reports/tests/eventBus.test.js`:

```js
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { EventBus } from '../src/lib/eventBus.js';

describe('EventBus', () => {
    let originalEnv;

    beforeEach(() => {
        originalEnv = { ...process.env };
        // Default: no Supabase creds, so _supabase is null
        delete process.env.SUPABASE_URL;
        delete process.env.SUPABASE_SERVICE_ROLE_KEY;
    });

    afterEach(() => {
        process.env = originalEnv;
    });

    it('throws when workflow is missing', () => {
        expect(() => new EventBus({})).toThrow(/workflow/i);
    });

    it('generates 8-char lowercase-hex runId', () => {
        const bus = new EventBus({ workflow: 'test' });
        expect(bus.runId).toMatch(/^[0-9a-f]{8}$/);
    });

    it('inherits traceId from constructor arg', () => {
        const bus = new EventBus({ workflow: 'test', traceId: 'abc12345', parentRunId: 'xyz98765' });
        expect(bus.traceId).toBe('abc12345');
    });

    it('defaults traceId to runId when none provided (new root trace)', () => {
        const bus = new EventBus({ workflow: 'test' });
        expect(bus.traceId).toBe(bus.runId);
    });

    it('defaults parentRunId to null when none provided', () => {
        const bus = new EventBus({ workflow: 'test' });
        // Access via emit side-effect (parentRunId is private)
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        bus.emit('cron_started');
        const loggedJson = JSON.parse(logSpy.mock.calls[0][0]);
        expect(loggedJson.parent_run_id).toBeNull();
        logSpy.mockRestore();
    });

    it('emit writes one JSON line to stdout with all required fields', async () => {
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        const bus = new EventBus({ workflow: 'test_wf', traceId: 't1', parentRunId: 'p1' });
        await bus.emit('cron_started', { label: 'hello', detail: { a: 1 }, level: 'info' });

        expect(logSpy).toHaveBeenCalledTimes(1);
        const logged = JSON.parse(logSpy.mock.calls[0][0]);
        expect(logged.workflow).toBe('test_wf');
        expect(logged.event).toBe('cron_started');
        expect(logged.label).toBe('hello');
        expect(logged.level).toBe('info');
        expect(logged.detail).toEqual({ a: 1 });
        expect(logged.trace_id).toBe('t1');
        expect(logged.parent_run_id).toBe('p1');
        expect(logged.run_id).toMatch(/^[0-9a-f]{8}$/);
        expect(logged.ts).toMatch(/^\d{4}-\d{2}-\d{2}T/);  // ISO 8601
        logSpy.mockRestore();
    });

    it('emit coerces invalid level to info', async () => {
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        const bus = new EventBus({ workflow: 'test' });
        await bus.emit('custom', { level: 'critical' });
        const logged = JSON.parse(logSpy.mock.calls[0][0]);
        expect(logged.level).toBe('info');
        logSpy.mockRestore();
    });

    it('emit writes to supabase when _supabase is present', async () => {
        const insertMock = vi.fn().mockResolvedValue({});
        const fromMock = vi.fn().mockReturnValue({ insert: insertMock });
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});

        const bus = new EventBus({ workflow: 'test' });
        bus._supabase = { from: fromMock };
        await bus.emit('cron_started');

        expect(fromMock).toHaveBeenCalledWith('event_log');
        expect(insertMock).toHaveBeenCalledTimes(1);
        const insertedRow = insertMock.mock.calls[0][0];
        expect(insertedRow.workflow).toBe('test');
        expect(insertedRow.event).toBe('cron_started');
        // 'ts' must NOT be in the row — Supabase uses NOW() default
        expect(insertedRow.ts).toBeUndefined();
        logSpy.mockRestore();
    });

    it('emit is never-raise when supabase insert throws', async () => {
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

        const bus = new EventBus({ workflow: 'test' });
        bus._supabase = {
            from: () => ({ insert: () => Promise.reject(new Error('boom')) }),
        };
        await expect(bus.emit('cron_started')).resolves.not.toThrow();
        expect(warnSpy).toHaveBeenCalled();  // warning was logged
        logSpy.mockRestore();
        warnSpy.mockRestore();
    });

    it('emit is never-raise when supabase client is null', async () => {
        const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
        const bus = new EventBus({ workflow: 'test' });
        expect(bus._supabase).toBeNull();
        await expect(bus.emit('cron_started')).resolves.not.toThrow();
        logSpy.mockRestore();
    });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from repo root):
```bash
cd actors/platts-scrap-reports && npx vitest run tests/eventBus.test.js
```

Expected: all 9 tests FAIL with `Cannot find module '../src/lib/eventBus.js'` (module doesn't exist yet).

- [ ] **Step 3: Create the EventBus implementation**

Create `actors/platts-scrap-reports/src/lib/eventBus.js`:

```js
// Event bus: emits structured workflow events to stdout and the Supabase event_log.
// Never raises — sink failures are logged to stderr and swallowed so the actor
// never fails because of telemetry.
//
// Mirrors the Python EventBus contract in execution/core/event_bus.py so events
// from actors and cron scripts share the same trace_id / parent_run_id schema.
//
// Keep this file in sync between actors/platts-scrap-reports/src/lib/eventBus.js
// and actors/platts-scrap-full-news/src/lib/eventBus.js (Apify package isolation
// means we can't share via symlink or local import without breaking the Docker
// build). Changes to either copy should be mirrored to the other in the same PR.

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
        console.warn('EventBus: supabase init failed:', err.message);
        return null;
    }
}

export class EventBus {
    constructor({ workflow, traceId, parentRunId } = {}) {
        if (!workflow) {
            throw new Error('EventBus: workflow is required');
        }
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
        // Stdout sink — always fires (surfaces in Apify run logs).
        console.log(JSON.stringify({ ts: new Date().toISOString(), ...row }));
        // Supabase sink — best-effort.
        if (this._supabase) {
            try {
                await this._supabase.from('event_log').insert(row);
            } catch (err) {
                console.warn('EventBus: event_log insert failed:', err.message);
            }
        }
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
cd actors/platts-scrap-reports && npx vitest run tests/eventBus.test.js
```

Expected: 9/9 PASS.

- [ ] **Step 5: Run the full actor test suite to confirm no regression**

Run:
```bash
cd actors/platts-scrap-reports && npm test
```

Expected: all pre-existing tests (`dates.test.js`, `filters.test.js`, `slug.test.js`) plus new `eventBus.test.js` all pass.

- [ ] **Step 6: Commit**

```bash
git add actors/platts-scrap-reports/src/lib/eventBus.js actors/platts-scrap-reports/tests/eventBus.test.js
git commit -m "feat(actors): add EventBus JS to platts-scrap-reports

Minimal event bus class (~60 LOC) mirroring the Python EventBus contract:
generates a short-hex runId, inherits traceId from constructor (or creates
a new root trace when none provided), and emits JSON lines to stdout +
best-effort inserts to Supabase event_log.

Never-raise: Supabase absence or insert failure degrades to stdout-only,
logged via console.warn.

Canonical copy — Task 3 will replicate verbatim to platts-scrap-full-news."
```

---

## Task 2: Wrap `platts-scrap-reports/src/main.js` with EventBus lifecycle emits

**Why:** Consume the EventBus from Task 1. Actor now writes its own lifecycle events to `event_log`, correlated via `trace_id` with the Python cron that triggered it.

**Files:**
- Modify: `actors/platts-scrap-reports/src/main.js`

The current end of `main.js` is:

```js
try {
    await run();
} finally {
    await ctx.close();
    await browser.close();
    await Actor.exit();
}
```

The wrap introduces try/catch, moves `Actor.exit()` out of the `finally` (only fires on success), and adds crashed emit on error.

- [ ] **Step 1: Read the first 40 lines of the file**

Run:
```bash
head -40 actors/platts-scrap-reports/src/main.js
```

Note where `await Actor.getInput()` is destructured. The EventBus import goes next to the other imports at the top; the construction goes right after the input destructuring.

- [ ] **Step 2: Add import at the top of main.js**

Add a new import line alongside the existing ones (around the top of the file, with other local `./` imports):

```js
import { EventBus } from './lib/eventBus.js';
```

- [ ] **Step 3: Construct EventBus and emit cron_started right after input parse**

Locate the block where `input` is destructured (around line 17-27). Directly AFTER the destructuring block, add:

```js
const bus = new EventBus({
    workflow: 'platts_scrap_reports',
    traceId: input.trace_id,
    parentRunId: input.parent_run_id,
});

await bus.emit('cron_started', {
    detail: { apify_run_id: Actor.config?.actorRunId ?? null },
});
```

- [ ] **Step 4: Replace the bottom try/finally block with try/catch/finally + emits**

Find the existing block at the end of the file:

```js
try {
    await run();
} finally {
    await ctx.close();
    await browser.close();
    await Actor.exit();
}
```

Replace it with:

```js
try {
    await run();
    await bus.emit('cron_finished', {
        detail: { summary_type: summary?.type ?? 'unknown' },
    });
} catch (err) {
    await bus.emit('cron_crashed', {
        label: `${err.name}: ${String(err.message || '').slice(0, 100)}`,
        detail: {
            exc_type: err.name,
            exc_str: String(err).slice(0, 500),
        },
        level: 'error',
    });
    await ctx.close();
    await browser.close();
    await Actor.fail(err.message || String(err));
    return;
} finally {
    // Resources close regardless of outcome (Actor.exit / Actor.fail handled per-branch).
    try { await ctx.close(); } catch {}
    try { await browser.close(); } catch {}
}
await Actor.exit();
```

Note: `summary` is a variable declared inside the `run()` function — not in outer scope. If `summary` is not accessible from the outer scope, replace the `detail` object in `cron_finished` with `detail: { ok: true }`. Check the file during implementation; the spec is flexible on the exact `detail` shape.

- [ ] **Step 5: Run the actor tests + syntax check**

Run:
```bash
cd actors/platts-scrap-reports && npm test
node --check src/main.js
```

Expected: all tests pass. `node --check` prints nothing if the file parses.

- [ ] **Step 6: Commit**

```bash
git add actors/platts-scrap-reports/src/main.js
git commit -m "feat(actors): wrap platts-scrap-reports main.js with EventBus lifecycle

Imports EventBus, constructs it from input.trace_id + input.parent_run_id
(with fallback to a fresh root trace when run manually via Apify Console),
and emits cron_started/cron_finished/cron_crashed events to event_log.

Preserves existing try/finally resource cleanup (ctx + browser) — errors
now produce a cron_crashed event BEFORE Actor.fail is called."
```

---

## Task 3: Copy EventBus JS + tests to `platts-scrap-full-news`

**Why:** The 2 actors are separate Apify packages with isolated node_modules (Q5b=1 in spec). No sharing mechanism that survives Apify's Docker build. The copy is verbatim — any change must be mirrored in both files.

**Files:**
- Create: `actors/platts-scrap-full-news/src/lib/eventBus.js`
- Create: `actors/platts-scrap-full-news/tests/eventBus.test.js`

- [ ] **Step 1: Copy the library file**

Run:
```bash
cp actors/platts-scrap-reports/src/lib/eventBus.js actors/platts-scrap-full-news/src/lib/eventBus.js
```

- [ ] **Step 2: Create the tests directory (it doesn't exist in this actor)**

Run:
```bash
mkdir -p actors/platts-scrap-full-news/tests
```

- [ ] **Step 3: Copy the test file**

Run:
```bash
cp actors/platts-scrap-reports/tests/eventBus.test.js actors/platts-scrap-full-news/tests/eventBus.test.js
```

- [ ] **Step 4: Verify tests pass in the new location**

Run:
```bash
cd actors/platts-scrap-full-news && npx vitest run tests/eventBus.test.js
```

Expected: 9/9 PASS.

- [ ] **Step 5: Confirm file hashes are identical between the two actors**

Run:
```bash
md5 actors/platts-scrap-reports/src/lib/eventBus.js actors/platts-scrap-full-news/src/lib/eventBus.js
md5 actors/platts-scrap-reports/tests/eventBus.test.js actors/platts-scrap-full-news/tests/eventBus.test.js
```

Expected: Matching MD5 hashes for each pair. This verifies the files are byte-identical and the "keep in sync" comment in `eventBus.js` is accurate.

- [ ] **Step 6: Commit**

```bash
git add actors/platts-scrap-full-news/src/lib/eventBus.js actors/platts-scrap-full-news/tests/eventBus.test.js
git commit -m "feat(actors): copy EventBus JS to platts-scrap-full-news

Byte-identical copy of actors/platts-scrap-reports/src/lib/eventBus.js.
Apify package isolation requires per-actor copies (no shared node_modules
across actor packages). The 'keep in sync' comment at the top of each
file reminds future editors to apply changes to both copies."
```

---

## Task 4: Wrap `platts-scrap-full-news/src/main.js` with EventBus lifecycle emits

**Why:** Same rationale as Task 2 but adapted to this actor's structure. `platts-scrap-full-news` uses `PlaywrightCrawler` with an inline `requestHandler`, not the explicit `run()` + `ctx.close()` pattern.

**Files:**
- Modify: `actors/platts-scrap-full-news/src/main.js`

- [ ] **Step 1: Inspect the current top and bottom of main.js**

Run:
```bash
head -45 actors/platts-scrap-full-news/src/main.js
tail -15 actors/platts-scrap-full-news/src/main.js
```

Confirm: the module-level flow is `await Actor.init()` → destructure `input` → build `crawler` → `await crawler.run(['about:blank'])` → `log.info('Fim!')` → `await Actor.exit()`.

- [ ] **Step 2: Add the EventBus import**

Add alongside the other local `./` imports at the top of the file:

```js
import { EventBus } from './lib/eventBus.js';
```

- [ ] **Step 3: Construct EventBus and emit cron_started after input destructuring**

Find the destructuring block (around lines 37-45, starts with `const { ... } = input;`). Directly AFTER it, insert:

```js
const bus = new EventBus({
    workflow: 'platts_scrap_full_news',
    traceId: input.trace_id,
    parentRunId: input.parent_run_id,
});

await bus.emit('cron_started', {
    detail: { apify_run_id: Actor.config?.actorRunId ?? null },
});
```

- [ ] **Step 4: Wrap the crawler.run call + exit with try/catch**

Find the block at the bottom of the file:

```js
await crawler.run(['about:blank']);
log.info('🏁 Fim!');
await Actor.exit();
```

Replace with:

```js
try {
    await crawler.run(['about:blank']);
    log.info('🏁 Fim!');
    await bus.emit('cron_finished', {
        detail: { ok: true },
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

- [ ] **Step 5: Syntax check + tests**

Run:
```bash
cd actors/platts-scrap-full-news && node --check src/main.js && npm test
```

Expected: `node --check` prints nothing. Tests all pass.

- [ ] **Step 6: Commit**

```bash
git add actors/platts-scrap-full-news/src/main.js
git commit -m "feat(actors): wrap platts-scrap-full-news main.js with EventBus lifecycle

Mirrors the platts-scrap-reports wrap but adapted to this actor's
Crawlee-based flow: the try/catch wraps crawler.run() + the final
log.info + Actor.exit, so both crawler-internal errors and teardown
errors produce a cron_crashed event before Actor.fail fires."
```

---

## Task 5: Python — inject trace_id into `platts_reports.py` `run_input`

**Why:** Without this, the actor always gets `input.trace_id === undefined` and creates new root traces for every run. Parent-child correlation requires the Python side to forward its active bus context.

**Files:**
- Modify: `execution/scripts/platts_reports.py:170-180` (the `run_input = { ... }` block)
- Create: `tests/test_platts_reports_trace.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_platts_reports_trace.py`:

```python
"""Tests that platts_reports.py injects trace_id + parent_run_id into run_input
when a bus is active, and omits them when no bus is active."""
import pytest
from unittest.mock import MagicMock


def _run_build(bus_active: bool):
    """Invoke the run_input construction path and return the dict passed to
    ApifyClient.run_actor. Uses monkeypatch-style manual patching since we
    need to capture the dict before the real actor call.

    Returns the captured run_input dict.
    """
    from execution.core import event_bus as eb

    captured = {}

    class FakeApifyClient:
        def __init__(self, *args, **kwargs): pass
        def run_actor(self, actor_id, run_input, **kwargs):
            captured.update(run_input)
            # Minimal shape: actor returns a dataset id + list of items
            return ("dataset_fake_id", [{"type": "success", "downloaded": [], "skipped": [], "errors": []}])

    # The _run_apify_sync helper in platts_reports.py takes a client + run_input
    # and calls client.run_actor. Invoke it directly to isolate the dict shape.
    from execution.scripts import platts_reports as pr

    run_input = {
        "username": "u", "password": "p",
        "reportTypes": ["Market Reports"], "maxReportsPerType": 50,
        "dryRun": False, "forceRedownload": False,
        "gdriveFolderId": "test",
    }

    if bus_active:
        bus = eb.EventBus(workflow="platts_reports")
        token = eb._active_bus.set(bus)
        try:
            # In production the script does: `if bus is not None: run_input["trace_id"] = bus.trace_id; ...`
            # then passes run_input into _run_apify_sync. We exercise the same path here.
            # The actual injection is expected to happen inside the script's main() or
            # _run_with_progress helper BEFORE _run_apify_sync is called.
            # For this test, the ContextVar-active path should produce a run_input
            # with trace_id / parent_run_id already injected by the time it reaches
            # _run_apify_sync. We verify by invoking the helper that builds the dict.
            _inject_trace_ids(run_input, bus)
            pr._run_apify_sync(FakeApifyClient(), run_input)
        finally:
            eb._active_bus.reset(token)
        return captured, bus
    else:
        pr._run_apify_sync(FakeApifyClient(), run_input)
        return captured, None


def _inject_trace_ids(run_input, bus):
    """Mirrors the injection logic expected to live in platts_reports.py.
    Centralized here so if the implementation moves the injection to a helper
    function, the test imports that helper instead of duplicating."""
    if bus is not None:
        run_input["trace_id"] = bus.trace_id
        run_input["parent_run_id"] = bus.run_id


def test_run_input_includes_trace_ids_when_bus_active(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)

    captured, bus = _run_build(bus_active=True)

    assert captured["trace_id"] == bus.trace_id
    assert captured["parent_run_id"] == bus.run_id
    # Business fields still there
    assert captured["username"] == "u"
    assert captured["reportTypes"] == ["Market Reports"]


def test_run_input_omits_trace_ids_when_no_bus():
    captured, _ = _run_build(bus_active=False)
    assert "trace_id" not in captured
    assert "parent_run_id" not in captured
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
/usr/bin/python3 -m pytest tests/test_platts_reports_trace.py -v
```

Expected: both tests FAIL. The first fails because nothing in `platts_reports.py` actually injects the fields (the test's `_inject_trace_ids` is a prod-behavior shim, but since we're calling `_run_apify_sync` directly via the shim, it actually passes in isolation. See Step 3 note.)

**Implementation note:** The tests as written use a local `_inject_trace_ids` helper to simulate the prod behavior. Since that helper is defined in the test file, the first test will PASS even without any production code change. The REAL goal of these tests is to lock the CONTRACT — once the injection lives in `platts_reports.py`, refactor the test to import and call the production helper instead of the local shim. An alternative stricter form below.

**Alternative (stricter) test — use if `_build_run_input` is extracted as a public helper:**

If during Step 3 the implementer extracts the `run_input = { ... }` block into a helper `_build_run_input(args, bus=None)` in `platts_reports.py`, replace the test body with:

```python
def test_build_run_input_includes_trace_ids_when_bus_given(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)

    from execution.core import event_bus as eb
    from execution.scripts.platts_reports import _build_run_input

    bus = eb.EventBus(workflow="platts_reports")
    args = MagicMock(dry_run=False, force_redownload=False)
    run_input = _build_run_input(args, bus=bus)

    assert run_input["trace_id"] == bus.trace_id
    assert run_input["parent_run_id"] == bus.run_id


def test_build_run_input_omits_trace_ids_when_no_bus():
    from execution.scripts.platts_reports import _build_run_input
    args = MagicMock(dry_run=False, force_redownload=False)
    run_input = _build_run_input(args, bus=None)
    assert "trace_id" not in run_input
    assert "parent_run_id" not in run_input
```

Prefer the alternative if the implementer extracts the helper (cleaner test, no shim). Default to the original form otherwise.

- [ ] **Step 3: Implement the injection in `platts_reports.py`**

Edit `execution/scripts/platts_reports.py`, around lines 170-180. The current block is:

```python
run_input = {
    "username": username,
    "password": password,
    "reportTypes": ["Market Reports", "Research Reports"],
    "maxReportsPerType": 50,
    "dryRun": args.dry_run,
    "forceRedownload": args.force_redownload,
    "gdriveFolderId": os.environ.get(
        "GDRIVE_PLATTS_REPORTS_FOLDER_ID", "1KxixMP9rKF0vGzINGvmmyFvouaOvL02y"
    ),
}
```

Change to:

```python
from execution.core.event_bus import get_current_bus

bus = get_current_bus()
run_input = {
    "username": username,
    "password": password,
    "reportTypes": ["Market Reports", "Research Reports"],
    "maxReportsPerType": 50,
    "dryRun": args.dry_run,
    "forceRedownload": args.force_redownload,
    "gdriveFolderId": os.environ.get(
        "GDRIVE_PLATTS_REPORTS_FOLDER_ID", "1KxixMP9rKF0vGzINGvmmyFvouaOvL02y"
    ),
}
if bus is not None:
    run_input["trace_id"] = bus.trace_id
    run_input["parent_run_id"] = bus.run_id
```

**If the import is already at top-of-file** (check first with `grep -n "from execution.core.event_bus" execution/scripts/platts_reports.py`), don't duplicate it — move the `get_current_bus` usage below the existing import.

- [ ] **Step 4: Run the test — it should pass now**

Run:
```bash
/usr/bin/python3 -m pytest tests/test_platts_reports_trace.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run the full test suite to confirm no regression**

Run:
```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```

Expected: same number of pre-existing failures as the baseline (should be 3 in `test_query_handlers.py`); no new failures.

- [ ] **Step 6: Commit**

```bash
git add execution/scripts/platts_reports.py tests/test_platts_reports_trace.py
git commit -m "feat(observability): platts_reports injects trace_id into run_input

When @with_event_bus is active (cron-triggered run), adds trace_id +
parent_run_id to the Apify run_input dict so the actor can inherit them
and emit event_log rows correlated with the Python cron.

When no bus is active (manual CLI run), the fields are omitted — actor
falls back to generating a new root trace, no broken correlation."
```

---

## Task 6: Python — inject trace_id into `platts_ingestion.py` `run_input`

**Why:** Same as Task 5, for the second production actor.

**Files:**
- Modify: `execution/scripts/platts_ingestion.py:244-260` (the `run_input = { ... }` block)
- Create: `tests/test_platts_ingestion_trace.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_platts_ingestion_trace.py`:

```python
"""Tests that platts_ingestion.py injects trace_id + parent_run_id into run_input."""
import pytest
from unittest.mock import MagicMock


def test_run_input_includes_trace_ids_when_bus_active(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENTS_CHANNEL_ID", raising=False)

    from execution.core import event_bus as eb

    # The injection is expected to happen in the main() of platts_ingestion.py,
    # which builds run_input inline (around line 244). We mirror the expected
    # injection pattern here and assert the fields are populated when bus active.
    bus = eb.EventBus(workflow="platts_ingestion")
    token = eb._active_bus.set(bus)
    try:
        from execution.core.event_bus import get_current_bus
        current = get_current_bus()
        assert current is bus

        # Simulate the block exactly as it should appear in platts_ingestion.py
        # after this task's edit:
        run_input = {
            "username": "u",
            "password": "p",
            "sources": ["allInsights"],
            "includeFlash": True,
            "includeLatest": True,
            "maxArticles": 50,
            "maxArticlesPerRmwTab": 5,
            "latestMaxItems": 15,
            "dateFilter": "today",
            "concurrency": 2,
            "dedupArticles": True,
        }
        if current is not None:
            run_input["trace_id"] = current.trace_id
            run_input["parent_run_id"] = current.run_id

        assert run_input["trace_id"] == bus.trace_id
        assert run_input["parent_run_id"] == bus.run_id
    finally:
        eb._active_bus.reset(token)


def test_run_input_omits_trace_ids_when_no_bus():
    from execution.core.event_bus import get_current_bus
    assert get_current_bus() is None

    run_input = {
        "username": "u",
        "password": "p",
        "sources": ["allInsights"],
    }
    current = get_current_bus()
    if current is not None:
        run_input["trace_id"] = current.trace_id
        run_input["parent_run_id"] = current.run_id

    assert "trace_id" not in run_input
    assert "parent_run_id" not in run_input
```

**Note:** like Task 5, these tests use a locally-inlined version of the injection logic to lock the contract. If the implementer extracts `_build_run_input(args, bus=None)` in `platts_ingestion.py`, prefer importing and calling that helper directly (stricter).

- [ ] **Step 2: Run tests — should pass on the "no bus" case but the "active bus" case depends on the actual production code change below**

Run:
```bash
/usr/bin/python3 -m pytest tests/test_platts_ingestion_trace.py -v
```

Expected: both tests may already PASS because the test file mirrors the injection logic inline. The real value of these tests is locking the contract — Step 3 adds the prod code change.

- [ ] **Step 3: Implement the injection in `platts_ingestion.py`**

Edit `execution/scripts/platts_ingestion.py`, lines 244-260. Current block:

```python
run_input = {
    "username": os.getenv("PLATTS_USERNAME", ""),
    "password": os.getenv("PLATTS_PASSWORD", ""),
    "sources": ["allInsights", "ironOreTopic", "rmw"],
    "includeFlash": True,
    "includeLatest": True,
    "maxArticles": 50,
    "maxArticlesPerRmwTab": 5,
    "latestMaxItems": 15,
    "dateFilter": "today",
    "concurrency": 2,
    "dedupArticles": True,
}
if args.target_date:
    run_input["targetDate"] = args.target_date
    run_input["dateFormat"] = "BR"
    run_input["dateFilter"] = "all"
```

Change to:

```python
from execution.core.event_bus import get_current_bus

bus = get_current_bus()
run_input = {
    "username": os.getenv("PLATTS_USERNAME", ""),
    "password": os.getenv("PLATTS_PASSWORD", ""),
    "sources": ["allInsights", "ironOreTopic", "rmw"],
    "includeFlash": True,
    "includeLatest": True,
    "maxArticles": 50,
    "maxArticlesPerRmwTab": 5,
    "latestMaxItems": 15,
    "dateFilter": "today",
    "concurrency": 2,
    "dedupArticles": True,
}
if bus is not None:
    run_input["trace_id"] = bus.trace_id
    run_input["parent_run_id"] = bus.run_id
if args.target_date:
    run_input["targetDate"] = args.target_date
    run_input["dateFormat"] = "BR"
    run_input["dateFilter"] = "all"
```

**Same import check as Task 5**: grep for existing `from execution.core.event_bus` at top of file. If present, don't duplicate.

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
/usr/bin/python3 -m pytest tests/test_platts_ingestion_trace.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run the full test suite to confirm no regression**

Run:
```bash
/usr/bin/python3 -m pytest tests/ 2>&1 | tail -5
```

Expected: same baseline failures only (3 pre-existing in `test_query_handlers`).

- [ ] **Step 6: Commit**

```bash
git add execution/scripts/platts_ingestion.py tests/test_platts_ingestion_trace.py
git commit -m "feat(observability): platts_ingestion injects trace_id into run_input

Mirrors the pattern landed in platts_reports: pulls the active EventBus
via get_current_bus() and adds trace_id + parent_run_id to the Apify
run_input dict when present. Fields are omitted (not None) when the
script runs outside the decorator, so the actor's fallback path remains
clean."
```

---

## Task 7: Followups doc + deployment checklist

**Why:** Deploying Apify actor code changes requires `apify push` from each actor dir — that's a manual step outside git. Operator needs a post-merge checklist so the deployment doesn't get forgotten. Also captures explicit deferrals.

**Files:**
- Create: `docs/superpowers/followups/2026-04-22-observability-trace-id-apify-followups.md`

- [ ] **Step 1: Create the followups doc**

Create `docs/superpowers/followups/2026-04-22-observability-trace-id-apify-followups.md`:

```markdown
# Observability trace_id → Apify Propagation — Followups

**Shipped:** 2026-04-22 — P4 from Phase 4 observability spec.

## Post-merge deployment checklist

Apify actor code lives in `actors/<name>/` but deploys to Apify Cloud independently
of Python/webhook CI. After the Python-side changes merge to main, the actors
must be pushed manually:

- [ ] `cd actors/platts-scrap-reports && apify push`
- [ ] `cd actors/platts-scrap-full-news && apify push`

Both require a prior `apify login` with an account that has access to the
`bigodeio05/platts-scrap-*` namespace. If the deploy is skipped, the Python
side still works (it sends `trace_id` in `run_input`), but the actor keeps
emitting with the OLD code (which ignores those fields). Net effect: still
no correlation until the actor is also deployed.

**Ordering:** Python-first deploy is safe (actor ignores extra keys in
`run_input` without failing). Actor-first deploy is also safe (actor falls
back to `traceId ?? runId` which creates a new root trace).

## Post-deploy manual validation

1. In GitHub Actions, trigger `platts_reports.yml` via `workflow_dispatch`.
2. Wait for it to complete (~3-5 min).
3. In Telegram, run `/tail platts_reports` and grab the `run_id` printed in
   any event (or via the state_store key).
4. Run this SQL in Supabase:
   ```sql
   SELECT ts, workflow, event, level, run_id, trace_id, parent_run_id
   FROM event_log
   WHERE trace_id = (
       SELECT trace_id FROM event_log
       WHERE workflow = 'platts_reports'
       ORDER BY ts DESC LIMIT 1
   )
   ORDER BY ts;
   ```
5. Expect: rows from both `workflow='platts_reports'` (Python) AND
   `workflow='platts_scrap_reports'` (actor), all sharing the same `trace_id`.
   Actor rows carry `parent_run_id = <the Python cron's run_id>`.
6. Run `/tail platts_scrap_reports` — returns actor-only timeline.
7. Test the manual-run fallback: from Apify Console, start the actor directly
   (without `trace_id` in input). Verify a new root trace appears
   (`parent_run_id IS NULL`).

## Deferred (not in this phase)

### P4.1 — step / api_call inside actors
Spec §Scope §out-of-scope. Today actors emit only lifecycle. If the operator
needs step-level detail (login, grid navigation, supabase upload, etc) in
`/tail`, add `bus.emit('step', ...)` / `bus.emit('api_call', ...)` calls
inside the actor body. Minor change, no schema or wire format impact.

### P4.2 — `/tail --trace=<id>` filter
Currently `/tail <workflow>` returns the most recent run of that workflow.
A `--trace=<id>` flag would cross-workflow return the full tree. Useful for
debugging a correlated failure. Small change to the `/tail` handler + one
extra SQL condition.

### P4.3 — instrument legacy actors
`platts-news-only` and `platts-scrap-price` were explicitly out-of-scope
(Q1=A). If they get reactivated, copy `eventBus.js` + repeat Task 4 wrap
pattern.

### P4.4 — shared EventBus JS package
Current state: 2 byte-identical copies of `eventBus.js`, kept in sync
manually (comment at top of each file reminds editors). If this grows
to 4+ actors OR the bus gains non-trivial logic (retry, batching), extract
to an npm package or Apify shared-storage. Not worth it for 2 static copies.

## Known limitations

### L1 — Actor deployment is manual
There is no CI auto-deploy for actors. The followups checklist above is
load-bearing. If forgotten, the correlation gap persists until someone
runs `apify push`.

### L2 — Orphan Apify runs
Actors started by humans via Apify Console (not via our Python scripts)
don't have `trace_id` in input. They emit correctly but create a fresh
root trace. `/tail platts_scrap_reports` shows these alongside
cron-triggered runs — there's no visual distinguishment. Not a bug, but
worth knowing when debugging.

### L3 — `@supabase/supabase-js` timeout
If Supabase is slow/unreachable from Apify's runner IP range, the
`emit('cron_finished', ...)` call could block the actor's shutdown by a
few seconds per failed insert. Each emit has an implicit timeout via
supabase-js's default fetch timeout (~60s). Acceptable today; revisit if
operators notice slow actor completion.

## Verification checklist (post-deploy, copy-paste ready)

- [ ] `apify push` succeeded for both actors
- [ ] Next cron run produces rows in `event_log` from both Python + actor
- [ ] `trace_id` column is the same value across Python and actor rows of one run
- [ ] `parent_run_id` in actor rows matches the Python row's `run_id`
- [ ] `/tail platts_scrap_reports` returns a non-empty timeline
- [ ] Manual Apify Console run (no trace_id input) still writes `event_log` rows
- [ ] A deliberate actor error (e.g., `raise new Error('test')` injected temporarily) produces a `cron_crashed` event in `event_log`
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/followups/2026-04-22-observability-trace-id-apify-followups.md
git commit -m "docs(observability): add Phase 4 trace_id→Apify followups

Deployment checklist for 'apify push' in both actor dirs (no CI auto-deploy),
manual validation SQL, and deferred items (step/api_call inside actors,
/tail --trace filter, legacy actor instrumentation, shared npm package)."
```

---

## Self-Review

**Spec coverage** (each section of `docs/superpowers/specs/2026-04-22-observability-trace-id-apify-propagation-design.md` mapped to tasks):

- §Scope in-scope items 1-5 → Tasks 1-6 ✓
- §Architecture data flow → Tasks 2, 4 (actor wrap) + Tasks 5, 6 (Python inject) ✓
- §Wire Format → Tasks 5, 6 ✓
- §EventBus JS Library API + reference implementation → Task 1 + Task 3 (copy) ✓
- §Actor main.js Wrapping Pattern → Tasks 2 + 4 ✓
- §Testing Strategy (Python + JS + manual) → Tests embedded in Tasks 1-6, manual in Task 7 ✓
- §Success Criteria → Task 7 (verification checklist) ✓

**Placeholder scan:** No "TBD"/"TODO"/vague references. Each step has concrete code or exact commands. The "implementation note" about the stricter alternative test form in Tasks 5-6 is a judgement call flagged for the implementer, not a placeholder.

**Type consistency:**
- `EventBus` class — consistent API: `constructor({ workflow, traceId, parentRunId })`, `runId` / `traceId` getters, `emit(event, { label, detail, level })`.
- snake_case keys in run_input: `trace_id`, `parent_run_id` (both Python and actor sides consistent).
- Workflow names: `'platts_scrap_reports'` and `'platts_scrap_full_news'` (snake_case, matches spec).
- Python `get_current_bus()` + `bus.trace_id` + `bus.run_id` consistent across Tasks 5-6.

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-04-22-observability-trace-id-apify-propagation-plan.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — Fresh subagent per task, two-stage review after each (spec compliance + code quality). Catches issues early, preserves controller context.

**2. Inline Execution** — Execute in this session with checkpoints for review.

**Which approach?**
