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
pattern. Note: they also need `@supabase/supabase-js` + `vitest` added to
their `package.json` (neither had those deps; `platts-scrap-full-news`
needed them too — added in Task 3).

### P4.4 — shared EventBus JS package
Current state: 2 byte-identical copies of `eventBus.js`, kept in sync
manually (comment at top of each file reminds editors). If this grows
to 4+ actors OR the bus gains non-trivial logic (retry, batching), extract
to an npm package or Apify shared-storage. Not worth it for 2 static copies.

### P4.5 — JS EventBus env-var fallback for TRACE_ID / PARENT_RUN_ID
The Python EventBus constructor has `traceId = traceId or os.getenv("TRACE_ID")`
as a fallback. The JS copy doesn't read env vars — only the explicit constructor
arg. Flagged in Task 1 review as a "known deferral". Not a bug today (actors
always get trace_id from `run_input`), but a future footgun if a non-ApifyClient
caller forgets to pass them.

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

### L4 — PostgREST errors observability
Fixed in Task 1 code-review follow-up (commit `38feb7c`): supabase-js v2
resolves `insert()` with `{data, error}` for RLS/constraint failures
instead of throwing. The original catch block only covered network errors.
Now destructures `{error}` and logs to `console.warn` — PostgREST failures
are observable in stderr. Still drop the row silently (that's intentional;
event bus is best-effort, never-raise), but no longer silently succeed at
the JS level.

### L5 — Non-Error throws
Fixed in Task 2 code-review follow-up (commit `7478687`): if a JS dependency
throws a plain string (`throw 'oops'`), the catch block's `err.name` is
undefined. Now uses `err?.name ?? 'UnknownError'` + `err?.message ?? String(err ?? '')`
so labels are always meaningful.

### L6 — Pre-run Actor.fail / Actor.exit orphaned cron_started
Fixed in Task 2 (`failWithEvent` helper for 3 pre-run `Actor.fail()` guards
in `platts-scrap-reports`) and Task 4 (inline emit before `Actor.exit()` in
the 1 pre-run credentials guard in `platts-scrap-full-news`). Any future
pre-run validation guard that exits must follow the same pattern, else
the `cron_started` event will be orphaned in `event_log`.

## Verification checklist (post-deploy, copy-paste ready)

- [ ] `apify push` succeeded for both actors
- [ ] Next cron run produces rows in `event_log` from both Python + actor
- [ ] `trace_id` column is the same value across Python and actor rows of one run
- [ ] `parent_run_id` in actor rows matches the Python row's `run_id`
- [ ] `/tail platts_scrap_reports` returns a non-empty timeline
- [ ] Manual Apify Console run (no trace_id input) still writes `event_log` rows
- [ ] A deliberate actor error (e.g., `throw new Error('test')` injected temporarily) produces a `cron_crashed` event in `event_log`
- [ ] `platts-scrap-full-news` actor tests pass in Apify Cloud build (the `package.json` now has `vitest` script)
