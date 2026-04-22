# Observability Phase 3 — Watchdog Followups

**Shipped:** 2026-04-21 on branch `feature/observability-phase3` (commits `b075af2..376e3b8`)
**Spec:** `docs/superpowers/specs/2026-04-21-observability-unified-design.md` §Watchdog
**Plan:** `docs/superpowers/plans/2026-04-21-observability-phase3-watchdog-plan.md`

## Commits on the branch

| SHA | Task | Summary |
|---|---|---|
| `b075af2` | 1 | `cron_parser.parse_previous_run` helper |
| `9e7c955` | 2 | `state_store.try_claim_alert_key` helper |
| `15376fc` | 3 | `execution/scripts/watchdog.py` + 5 integration tests |
| `376e3b8` | 4 | `.github/workflows/watchdog.yml` (runs every 5 min) |

**Test count:** 484 → **497 passed** (+13 new/bonus), **3 pre-existing failed** (down from 5 because installing `croniter` as part of Task 1 freed 2 previously-failing tests in `test_cron_parser.py`).

---

## Ship criterion — operator validation

- [ ] **Merge this branch to `main` and push** — watchdog starts running on its `*/5 * * * *` schedule automatically.
- [ ] **Trigger manually once** via the GH Actions "Watchdog" → "Run workflow" button; confirm the run finishes green (no `cron_missed` yet because everything is within grace).
- [ ] **Confirm Redis secret is set** in repo secrets (`REDIS_URL`) — without it the idempotency guard degrades to "alert anyway on every watchdog run", which would produce a duplicate alert every 5 min during a real miss.
- [ ] **First real miss in production**: expect a `⏰ WATCHDOG — NÃO RODOU` message in the main Telegram chat within 20 minutes (15 min grace + 5 min watchdog interval). Label line will carry the actual missing workflow name.
- [ ] **Watch for false positives in the first 1-2 weeks**: GH Actions cron runs can drift 5-15 minutes during peak load. Our 15 min grace absorbs that. If legitimate delays cross the grace threshold, bump `GRACE_MINUTES` in `execution/scripts/watchdog.py`.

---

## Known followups (out of Phase 3 scope)

### A. UX improvements

1. **Telegram alert title is generic: `⏰ WATCHDOG — NÃO RODOU`.** Because the bus's workflow is `"watchdog"`, `_MainChatSink._format` uses that in the title. The actual missing workflow is in the `label` ("morning_check não rodou") and in `detail.missed_workflow`. **Fix:** teach `_format` to read `event_dict.get("detail", {}).get("missed_workflow")` when `event == "cron_missed"` and substitute that into the title. Produces `⏰ MORNING CHECK — NÃO RODOU` instead. ~5-line change in `event_bus.py`. Captured for Phase 4 cleanup.

2. **Alert includes the `run_id` line from `_MainChatSink._format`.** For `cron_missed`, the `run_id` belongs to the watchdog's own run, not to the missing workflow (which by definition has none). This is OK but slightly misleading. Consider suppressing the `run_id` line when `event == "cron_missed"`, or renaming it to "watchdog_run_id" for clarity.

### B. Scope / correctness

3. **`rationale_news` has no YAML.** `ALL_WORKFLOWS` lists it but `.github/workflows/rationale_news.yml` does not exist. Watchdog's `parse_previous_run` returns `None` → workflow is silently skipped. Either remove `rationale_news` from `ALL_WORKFLOWS` or add the YAML. Not blocking because the skip is correct behavior.

4. **`platts_ingestion` / `platts_reports` / `rebuild_dedup` not in `ALL_WORKFLOWS`.** These 3 scripts ARE wrapped with `@with_event_bus` (Phase 1 Task 9), so they emit `cron_started`/`cron_finished`, but watchdog doesn't check whether they ran on schedule. If any of them has a recurring cron in its YAML, add to `ALL_WORKFLOWS` to benefit from watchdog coverage.

5. **Watchdog's own missed run is not detected.** If the watchdog GH workflow is disabled, paused, or has its own YAML deleted, nothing alerts. A "watchdog of the watchdog" via the Phase 2 events channel + human spot-checks covers this. Out of scope for Phase 3.

### C. Tuning

6. **`GRACE_MINUTES = 15` may be too tight or too loose.** GH Actions typically delays cron by 5-15 min during peak. We absorb 15 min. If we see legitimate false positives in the first couple of weeks, bump to 20 or 25. Target: < 1 false positive per month.

7. **Alert TTL is 24h.** One alert per unique `(workflow, previous_expected_iso)` pair within 24 hours. A workflow that misses across cron intervals (new `previous_expected` each time) fires a new alert per interval — which is correct semantics (the operator should know about each missed opportunity), not a bug.

8. **Per-run alert key shape:** `wf:watchdog_alerted:{workflow}:{previous_expected_iso}`. Under many concurrent watchdog runs (shouldn't happen since cron is 5 min but theoretically), multiple processes might race on the SET NX; that's exactly what NX guards against. No action.

### D. Operator runbook

9. **Where to look when an alert fires:**
   - **Telegram main chat** — first alert arrives here (via `_MainChatSink`).
   - **Supabase `event_log`** — query `SELECT * FROM event_log WHERE event='cron_missed' ORDER BY ts DESC LIMIT 20` for history.
   - **Sentry** — breadcrumb trail up to the `cron_missed` emit lives in the watchdog's Sentry event.
   - **GitHub Actions UI** — the Watchdog run shows all stdout including the JSON-line events.

10. **How to silence a false positive:** set `wf:watchdog_alerted:{workflow}:{previous_expected_iso}` in Redis with any value + TTL to prevent re-alerting on that specific miss. Useful if you KNOW the workflow is disabled on purpose (maintenance, etc.).

---

## Phase 4 prerequisites

Nothing extra. Phase 4 (`/tail` command + `step`/`api_call` instrumentation) builds entirely on Phase 1's `event_log` table, which is live. Phase 3 adds `cron_missed` as another event type that `/tail` will naturally surface via the same query.

---

## Open questions resolved during implementation

- **Does `croniter.get_prev(datetime)` preserve the UTC tzinfo when the base datetime is tz-aware?** Yes. Verified during Task 1 — no tzinfo-stripping quirks.
- **Does monkeypatching `status_builder.ALL_WORKFLOWS` stick across watchdog imports?** Yes — `watchdog.main()` accesses it via `status_builder.ALL_WORKFLOWS` attribute lookup at call time, not a snapshot. Each test's monkeypatch is respected.
- **Does `@with_event_bus("watchdog")` decorator wrap `main()` cleanly, emitting lifecycle events without interfering with the inner `bus = EventBus(workflow="watchdog")` creation?** Yes — the decorator creates its own bus for lifecycle, the inner bus created by `main()` is separate. They share the workflow name but have different `run_id`s. For `event_log` queries this is a minor quirk (decorator's `cron_started`/`cron_finished` don't share a `run_id` with the `cron_missed` emitted mid-run), but each event is independently correct. Tighten in Phase 4 if needed by passing the decorator's bus via closure.
- **What happens when Redis is unavailable in production?** `try_claim_alert_key` returns `True` (degrades permissive) — alert fires on every watchdog run until Redis recovers OR the underlying condition resolves. Acceptable: getting duplicate alerts is louder but recoverable; silent misses are not.
