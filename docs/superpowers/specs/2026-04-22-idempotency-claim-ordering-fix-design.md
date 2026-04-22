# Daily-Report Split-Lock Idempotency + Event_log Label Fix

**Date:** 2026-04-22
**Status:** Draft — awaiting user review
**Owner:** @bigodinhc
**Related:** PR #1 (`df15d9aa` — Sheets → Supabase migration), `execution/scripts/baltic_ingestion.py`, `execution/scripts/morning_check.py`, `supabase/migrations/20260418_event_log.sql`

## Problem

Today (2026-04-22) the Baltic Exchange daily report was **not delivered**: no `FREIGHT_C3_BALTIC` ingested into IronMarket, no WhatsApp broadcast. Investigation showed the same regression also affects `morning_check`, and both come from the same commit.

### Root cause

Commit `df15d9aa` (2026-04-22 09:06 BRT) replaced the legacy Google-Sheets idempotency pattern:

```
Phase 1 — CHECK (passive read of control sheet)
…processing…
Phase N — MARK (write to control sheet, only after successful send)
```

with a single atomic Redis operation:

```python
claimed = state_store.try_claim_alert_key(claim_key, 48 * 3600)
if not claimed: return
```

`try_claim_alert_key` is `SET NX EX` — it both *reads* (check) and *writes* (mark) in one step. Because the legacy `mark_daily_status` call (post-send) was deleted without a replacement, the "mark" effectively moved from post-success to pre-processing.

The consequence: any early-exit between the claim and the actual broadcast (no email yet, email is from yesterday, no PDF, low-confidence extraction, crash during processing) leaves the key held for 48h. The first cron run of the day "wastes" the day if the email hasn't arrived yet.

### Today's timeline (Baltic)

| UTC | Duration | Behavior |
|---|---|---|
| 12:19 | 1m1s | Claimed key; Graph API returned yesterday's email (today's not yet released); rejected at `email_dt != today_dt` check; **key held** |
| 12:49 | 52s | `"Report already sent today. Exiting."` |
| 13:32 | 55s | same skip |
| 14:17 | 54s | same skip |
| 14:58 | 55s | same skip |

Same bug present in `morning_check.py:213-216`, with the extra irony that the script has explicit "will retry later" branches (lines 233, 245) for empty/incomplete data — but the claim-above-them blocks the retry for 48h.

### Secondary bug (discovered during investigation)

`supabase/migrations/20260418_event_log.sql:11` declares `label text not null`, but `@with_event_bus` emits `cron_started` / `cron_finished` events without a `label` (the event name itself is the identifier). `execution/core/event_bus.py:137` converts empty `label` to `None`, which Postgres rejects:

```
null value in column "label" of relation "event_log" violates not-null constraint
```

Effect: `cron_started` / `cron_finished` events do **not** persist to Supabase for any workflow today. `/tail` cannot show lifecycle events — observability is silently broken.

## Goals

1. Delivery of daily reports is not blocked by early-exit conditions that occurred before any side effect.
2. Transient failures during processing (network blips, upstream 5xx, PDF malformed on one attempt) do not waste the day — the next cron retries cleanly.
3. Two concurrent crons cannot both broadcast the same report.
4. After a fully-successful broadcast, subsequent crons the same day do nothing.
5. Regression of this specific ordering bug is caught by automated tests.
6. `cron_started` / `cron_finished` events persist to `event_log`.
7. The design intent is documented so future refactors don't re-regress.

## Non-goals

- Per-contact delivery deduplication (tracking which phone numbers already received, skipping them on retry). Deferred — see Known Limitations. Revisit if mid-broadcast crashes become a real operational problem.
- `IRONMARKET_API_KEY` hardcoded fallback in `baltic_ingestion.py:41`. Separate concern, separate spec.
- Idempotency review of `send_news.py`, `send_daily_report.py`, `platts_ingestion.py`. None use the `daily_report:sent:*` key pattern; out of scope.
- Renaming `state_store.try_claim_alert_key`. Its semantic ("atomically claim a Redis key with TTL") is accurate for both the existing watchdog-alert-dedup use case and the new in-flight-lock use case. Docstring update only.

## Design

### Two keys per daily-report workflow

Replace the single-lock pattern (one key with 48h TTL claimed pre-processing) with a split-lock pattern:

| Key | TTL | Role | When written | When cleared |
|---|---|---|---|---|
| `daily_report:inflight:{REPORT_TYPE}:{date}` | 20min | Concurrency guard — only one cron processes the report at a time | Acquired (SET NX EX) after data validation, before side effects | Released (DEL) in `finally` block on any exit; auto-expires 20min after crash |
| `daily_report:sent:{REPORT_TYPE}:{date}` | 48h | Idempotency commit — "this report was delivered today, don't re-send" | SET unconditionally, only after successful full broadcast | Expires naturally 48h later |

Separation of concerns:

- **Early-exits before data validation** → no key touched. Next cron retries.
- **Crashes mid-processing** (PDF, Claude, IronMarket, partial WhatsApp) → in-flight lock auto-expires in 20min. Sent flag never written. Next cron retries. ← **This is what Path A (current) gets wrong.**
- **Full success** → sent flag set. Subsequent crons today exit at Phase 0.
- **Two crons start while a previous one is still processing** → second one fails to acquire in-flight lock at Phase 3, exits cleanly.

### New pipeline flow (both scripts)

```
PHASE 0  — read sent flag. If set: exit "already delivered today" (no side effect)
PHASE 1  — fetch source data (email, Platts rows)
PHASE 2  — validate data is fresh/complete
             early-exit here: no lock, no flag. Next cron retries.
PHASE 3  — acquire in-flight lock (SET NX EX 20min)
             if another cron holds it: exit cleanly, no error
PHASE 4  — try:
             4a: processing (PDF download, Claude extraction, data shaping)
             4b: side effects (IronMarket POST, WhatsApp broadcast via DeliveryReporter)
             4c: set sent flag (SET EX 48h) — only reached on full success
           finally:
             release in-flight lock (DEL)
```

**Side-effect ordering note** (4b): IronMarket POST first, then WhatsApp broadcast. IronMarket POST is idempotent by `variable_key`. WhatsApp is not. A crash between IronMarket and WhatsApp means IronMarket has today's value but nobody got the message → retry will re-POST IronMarket (harmless, same `variable_key`) and broadcast to everyone cleanly. Ordering preserved from current code.

### Baltic concrete changes

Current `baltic_ingestion.py:225-236` (claim first):

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
    msg = await asyncio.to_thread(baltic.find_latest_email)
    …
    # today-validation
    # PDF download
    # Claude extraction
    # IronMarket POST
    # WhatsApp broadcast
    # (no post-success marker — assumed the pre-claim covered it)
```

New structure:

```python
sent_key = f"daily_report:sent:{REPORT_TYPE}:{today_str}"
inflight_key = f"daily_report:inflight:{REPORT_TYPE}:{today_str}"

try:
    # PHASE 0: read sent flag (no side effect)
    if not args.dry_run and await asyncio.to_thread(state_store.check_sent_flag, sent_key):
        logger.info("Report already delivered today. Exiting.")
        await reporter.step("Skipped", "already delivered today", level="info")
        await reporter.finish()
        return

    # PHASE 1-2: fetch email + today-validation (existing logic unchanged)
    msg = await asyncio.to_thread(baltic.find_latest_email)
    if not msg: ...return
    # email_dt != today_dt check ...return

    # PHASE 3: acquire in-flight lock
    if not args.dry_run:
        acquired = await asyncio.to_thread(
            state_store.try_claim_alert_key, inflight_key, 20 * 60
        )
        if not acquired:
            logger.info("Another run is processing this report. Exiting.")
            await reporter.step("Skipped", "another run in progress", level="info")
            await reporter.finish()
            return

    inflight_held = not args.dry_run

    try:
        # PHASE 4a-b: PDF, Claude, IronMarket, WhatsApp (existing logic)
        ...

        # PHASE 4c: commit success
        if not args.dry_run:
            await asyncio.to_thread(state_store.set_sent_flag, sent_key, 48 * 3600)

        await reporter.finish(report=report, message=message)

    finally:
        if inflight_held:
            await asyncio.to_thread(state_store.release_inflight, inflight_key)
```

### morning_check concrete changes

Same structure. Current `morning_check.py:211-246` claims the key first and then has two "retry later" branches that don't actually retry. New flow:

```python
sent_key = f"daily_report:sent:{REPORT_TYPE}:{date_str}"
inflight_key = f"daily_report:inflight:{REPORT_TYPE}:{date_str}"

try:
    # PHASE 0: read sent flag
    if not args.dry_run and state_store.check_sent_flag(sent_key):
        progress.finish_empty("already delivered today")
        return

    # PHASE 1-2: fetch Platts + row-count validation (existing logic)
    report_items = platts.get_report_data(...)
    if not report_items:
        progress.finish_empty("sem dados do Platts ainda")
        sys.exit(0)
    if len(report_items) < MIN_ITEMS_EXPECTED:
        progress.finish_empty(f"dados incompletos ({len(report_items)}/{TOTAL_SYMBOLS})")
        sys.exit(0)

    # PHASE 3: acquire in-flight lock
    if not args.dry_run:
        if not state_store.try_claim_alert_key(inflight_key, 20 * 60):
            progress.finish_empty("another run in progress")
            return

    inflight_held = not args.dry_run
    try:
        # PHASE 4a-b: format message, send via DeliveryReporter
        ...

        # PHASE 4c: commit success
        if not args.dry_run:
            state_store.set_sent_flag(sent_key, 48 * 3600)
    finally:
        if inflight_held:
            state_store.release_inflight(inflight_key)
```

The two pre-existing "will retry later" branches now *are* truthful: no lock, no flag, next cron gets a clean shot.

### `state_store` additions

Three new helpers, all non-raising (match the existing module contract):

```python
def check_sent_flag(key: str) -> bool:
    """Read-only check. Returns True if the key exists.

    Permissive on Redis failure: returns False so the workflow proceeds.
    If the workflow succeeds, a new sent flag will be written; if it fails,
    Redis is probably still down and the next run will hit the same path.

    Used by daily-report workflows at Phase 0 to short-circuit when today's
    delivery already happened."""

def set_sent_flag(key: str, ttl_seconds: int) -> None:
    """Unconditional SET EX (overwrite if present). Call only after the
    guarded operation has fully succeeded. Non-raising."""

def release_inflight(key: str) -> None:
    """DEL. Non-raising. Idempotent: harmless if the key already expired.

    The in-flight lock also has a TTL as a safety net against crashes that
    skip the finally block, but the normal happy-path release is explicit."""
```

The existing `try_claim_alert_key` is unchanged. Its docstring gains a sentence clarifying both use cases:

> Use for short-TTL mutex locks (in-flight work guards, daily-report concurrency) or for alert suppression (watchdog missing-cron notifications). When used as a sent-flag, prefer `set_sent_flag` — this function's atomicity is wasted when you only want to mark success.

### Event_log label fix

Single-line change in `execution/core/event_bus.py:137`:

```python
# Before
"label": label or None,

# After
"label": label or event,
```

Rationale:

- Semantically correct — when no explicit label is passed (lifecycle events `cron_started` / `cron_finished`), the event name itself is the most informative label.
- Satisfies the `NOT NULL` constraint in `event_log.label` without weakening the schema.
- No migration needed.
- Events that pass explicit labels (`step`, `api_call`, `error`) are unaffected: `label or event` short-circuits on the truthy `label`.

**Verification:** after deploy, `/tail baltic_ingestion` should show `cron_started` and `cron_finished` rows. Currently these rows are absent because every insert throws the NOT NULL violation (visible in every run log today).

### Regression tests

Two new test files, table-driven:

- `tests/test_baltic_ingestion_idempotency.py`
- `tests/test_morning_check_idempotency.py`

Plus expanded coverage for the new state_store helpers in `tests/test_state_store.py` (create if missing).

**Pattern per script test file:** patch the three state_store helpers as spies (`check_sent_flag`, `try_claim_alert_key`, `set_sent_flag`, `release_inflight`); patch upstream fetchers with fixtures; assert the expected call sequence.

**Scenarios for Baltic:**

| # | Scenario | Fetchers return | `check_sent_flag` | `try_claim_alert_key` (inflight) | Processing runs | `set_sent_flag` | `release_inflight` |
|---|---|---|---|---|---|---|---|
| 1 | Sent flag already set | — | True | ❌ | ❌ | ❌ | ❌ |
| 2 | No email in last 24h | `find_latest_email` → None | False | ❌ | ❌ | ❌ | ❌ |
| 3 | Email from yesterday | msg with yesterday's date | False | ❌ | ❌ | ❌ | ❌ |
| 4 | Concurrent run holds lock | today's msg | False | ❌ (returns False) | ❌ | ❌ | ❌ |
| 5 | PDF missing | today's msg, `get_pdf_attachment` → (None, None) | False | ✅ | partial | ❌ | ✅ |
| 6 | Claude low-confidence | today's msg + PDF, extraction_confidence=low | False | ✅ | partial (raises) | ❌ | ✅ |
| 7 | Full success | today's msg + PDF + valid extraction | False | ✅ | ✅ | ✅ | ✅ |

**Scenarios for morning_check:** same shape, with:
- Empty `platts.get_report_data` → no lock (scenario 2 equivalent)
- `< MIN_ITEMS_EXPECTED` rows → no lock (scenario 3 equivalent)
- Full row set → acquire lock, set flag, release lock

Tests run with `dry_run=False` (production path where locks are active). Integrations are patched at module boundary so no real network calls occur. Use `pytest-asyncio` (already in the suite).

**New state_store tests** (`test_state_store.py`):
- `check_sent_flag` returns True when key exists, False when absent, False when Redis unavailable
- `set_sent_flag` writes with correct TTL
- `release_inflight` deletes key; is idempotent (no error on missing key)
- All three functions are non-raising on Redis errors

### Convention documentation

Add to `.planning/codebase/CONVENTIONS.md`:

> ### Idempotency — daily reports (split-lock pattern)
>
> Daily-report workflows use **two** Redis keys, not one:
>
> - `daily_report:inflight:{REPORT_TYPE}:{date}` — 15min TTL. Acquired via `try_claim_alert_key` after data validation; released in `finally` on exit. Prevents two crons from broadcasting the same report in parallel. Auto-expires after a crash.
> - `daily_report:sent:{REPORT_TYPE}:{date}` — 48h TTL. Written via `set_sent_flag` **only** after the full broadcast (IronMarket POST + WhatsApp dispatch) succeeds. Checked via `check_sent_flag` at the start of every run.
>
> **Claim ordering rule.** Early-exits that precede any side effect (source data missing, stale, or incomplete) must not touch either key. Early-exits that occur after Phase 3 (lock acquired) release the lock but do **not** set the sent flag — the next cron retries cleanly.
>
> **Anti-pattern (the bug of 2026-04-22):** using a single long-TTL `SET NX EX` key as both the concurrency guard and the sent flag. A pre-processing early-exit then holds the key for the full TTL, blocking all retries on the same day. The Sheets→Supabase migration (`df15d9aa`) regressed this by compacting the legacy `check_daily_status` + `mark_daily_status` pair into one atomic call; the split-lock pattern above is the correct replacement.
>
> **When to deviate.** If future operational experience shows mid-broadcast crashes happen often enough that duplicate WhatsApp messages become a real complaint, add per-contact dedup (Redis set of delivered phone numbers under a 48h key). Not needed today.

## Known limitations

### Mid-broadcast crash can cause WhatsApp duplicates on retry

If `delivery_reporter.dispatch()` sends to N of K contacts and then the process crashes (uazapi gateway dies, runner killed, OOM), the in-flight lock auto-expires in ≤15min and the next cron retries the full broadcast — including the N contacts who already received the message.

**Why accepted:** narrow failure window (seconds to minutes during the broadcast loop, out of a ~day); rare in practice; easily detected (delivery_reporter emits `delivery_summary` + per-contact status to the event bus); mitigatable at the operator level by deleting the in-flight key or letting the sent flag be set manually if partial delivery is acceptable.

**Escape hatch:** add per-contact dedup. `DeliveryReporter` already tracks per-contact results — one additional Redis set `daily_report:delivered:{REPORT_TYPE}:{date}` that the reporter checks before each send and adds to after success would eliminate the risk at ~20 lines of code. Not in scope for this spec.

### In-flight lock auto-expire can be racy

TTL is set to 20min. Yesterday's `baltic_ingestion` runs took 17-18min (uazapi was slow). If a broadcast ever exceeds 20min, a second cron starting after expiry would see the lock gone and start a parallel broadcast.

**Mitigation:** tune TTL upward if broadcast duration grows. Any growth is visible in `event_log` via `/tail`; surface it in CONCERNS.md review if observed. Alternative long-term: switch to a Redis key pattern where the lock value is the holder's `run_id` and the finally block only deletes if the value still matches — prevents the rare case of a slow run releasing a lock the new run just acquired. Not needed today.

## Files changed

| File | Change | Size |
|---|---|---|
| `execution/core/state_store.py` | Add `check_sent_flag`, `set_sent_flag`, `release_inflight`. Update `try_claim_alert_key` docstring. | ~40 lines |
| `execution/scripts/baltic_ingestion.py` | Refactor claim block to split-lock pattern (Phase 0–4 structure). | ~30 lines net |
| `execution/scripts/morning_check.py` | Same refactor. | ~25 lines net |
| `execution/core/event_bus.py:137` | `label or None` → `label or event` | 1 line |
| `tests/test_baltic_ingestion_idempotency.py` | New file, 7 scenarios. | ~200 lines |
| `tests/test_morning_check_idempotency.py` | New file, 5 scenarios. | ~150 lines |
| `tests/test_state_store.py` | Add (or extend) coverage for the 3 new helpers. | ~80 lines |
| `.planning/codebase/CONVENTIONS.md` | Add "Idempotency — daily reports (split-lock pattern)" section. | ~30 lines |

Total: 8 files, ~550 lines net. Mostly tests and docs.

## Rollout

1. **Merge** the fix.
2. **Unblock today's Baltic report** (required for today's cron window has already closed):
   - Delete Redis key `daily_report:sent:BALTIC_REPORT:2026-04-22` (legacy single-key name under old code) OR verify no new `daily_report:inflight:*` or `daily_report:sent:*` key for today exists if merged before next cron.
   - `gh workflow run baltic_ingestion.yml`.
   - Confirm via `/tail baltic_ingestion` that the full run completed (`cron_started` → phase steps → `cron_finished`).
   - Check that the `cron_started` / `cron_finished` rows appear in Supabase `event_log` — confirms event_log label fix landed.
3. **Check morning_check** for the same state. If today's morning report also didn't deliver, same unblock procedure (delete legacy key, `gh workflow run morning_check.yml`).
4. **Post-deploy verification** over the next 2-3 days:
   - All daily reports deliver on first-attempt cron run when source data is ready.
   - No double-sends (check delivery_summary in event_log).
   - `/tail` shows lifecycle events for every run.

## Open questions

*(none — all trade-offs resolved in pre-spec conversation)*
