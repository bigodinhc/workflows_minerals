# Observability Phase 4 Followups

**Shipped:** 2026-04-21 — `/tail` command + step/api_call instrumentation.

**Branch:** `feature/observability-phase4` (6 commits). Parent: `main@aafe707`.

## Deferred from Phase 4 scope

### P1 — delivery_tick / delivery_summary bridge (events channel)
Spec §Rollout phase 4 mentioned optionally bridging `delivery_reporter` to emit `delivery_tick` per contact and `delivery_summary` at dispatch end. Skipped to keep Phase 4 tight. Revisit if operator wants per-contact detail in events channel.

### P2 — /tail level filter
`/tail <workflow> --level=warn` to filter events by level. YAGNI for MVP — revisit if /tail output is too noisy in practice.

### P3 — /tail pagination
Cap is currently `_TAIL_LIMIT = 30` events. Long-running scripts may emit 30+ events. Add `/tail <workflow> --page=2` or prefix output with "... X earlier events".

### P4 — trace_id propagation to Apify actors
Spec §Scope §item 8 mentions propagating `trace_id` via env var to Apify actors so their events link back. Not started; separate spec.

## Known limitations

### L1 — Legacy runs (pre-Phase 4) have no run_id
`/tail <workflow>` for a run that completed before this phase shipped will report "run mais recente ... sem run_id (legacy, anterior ao Phase 4)". Operator must use `/tail <workflow> <explicit_run_id>` or wait for a new run.

### L2 — ContextVar doesn't propagate across threads/new event loops without copy_context
Phase 4 relies on `asyncio.run(...)` propagating the active `EventBus` from the `@with_event_bus` decorator into the async helper (works because asyncio snapshots the current context when creating the main task). If a future script spawns a **thread pool** (none today) and calls `state_store.record_*` from the pool, `get_current_bus()` returns None. Not a problem today; revisit when concurrency model changes.

## Pre-existing issues surfaced during review (out of Phase 4 scope)

### PE1 — `baltic_ingestion.py` dry-run does not short-circuit before IronMarket HTTP POST
File: `execution/scripts/baltic_ingestion.py` (~line 320–327).

The `if args.dry_run: print(json.dumps(data, indent=2))` block does NOT `return` — it falls through to `await asyncio.to_thread(ingest_to_ironmarket, data)` which is a **live HTTP POST** to the Railway-hosted IronMarket endpoint. The second dry-run guard (after `format_whatsapp_message`) exits only after IronMarket has already been called. Spec forbade control-flow changes in Task 5, so the instrumentation was added as-is; the Phase 4 `bus.emit("api_call", label="ironmarket.ingest", ...)` faithfully reflects the wrong behavior.

**Fix** (out of scope for Phase 4):
```python
if args.dry_run:
    print(json.dumps(data, indent=2))
    await reporter.finish()
    return   # exit before IronMarket
```

### PE2 — 3 pre-existing failing tests in `tests/test_query_handlers.py`
Unrelated to Phase 4:
- `test_queue_single_page_titles_in_buttons`
- `test_queue_button_uses_rationale_icon`
- `test_queue_paginated`

Introduced by commit `d2ca0fc` (2026-04-17). Not caused by Phase 4 changes and not touched here.

## Verification checklist (post-deploy)

- [ ] `/tail morning_check` returns a formatted timeline with >3 events
- [ ] Events channel shows `step` and `api_call` lines (not just cron_started/finished)
- [ ] Trigger a script via `workflow_dispatch`, then run `/tail` — timeline matches what the script actually did
- [ ] Legacy run (if any exists) shows the "sem run_id" message, not a crash
- [ ] `api_call` events show tight `duration_ms` (the metric captures only the upstream call, not client construction)
