# Observability Phase 1 — Foundation Followups

**Shipped:** 2026-04-21 on branch `feature/observability-phase1` (commits `e686128..6abf648`)
**Spec:** `docs/superpowers/specs/2026-04-21-observability-unified-design.md` §§1–3, 6
**Plan:** `docs/superpowers/plans/2026-04-21-observability-phase1-foundation-plan.md`

## Commits on the branch

| SHA | Task | Summary |
|---|---|---|
| `e686128` | 1 | Add `event_log` table migration |
| `3e64c12` | 2 | Add `event_bus` module with stdout sink |
| `e176a8a` | 3 | Add Supabase sink to event_bus |
| `154e99c` | 4 | Add Sentry breadcrumb sink to event_bus |
| `dbb682e` | 5 | Add main-chat sink for errors and crash alerts |
| `af7e1a6` | 6 | Verify emit continues when one sink raises (never-raise test) |
| `cede58e` | 7 | Add `@with_event_bus` decorator |
| `f41d8b5` | 8 | `progress.fail()` pushes a new alert message |
| `6abf648` | 9 | Wrap all 7 execution scripts with `@with_event_bus` |

**Test count:** baseline 467 → 484 passed (+17 new tests for Phase 1), 5 pre-existing failures in `test_cron_parser.py` and `test_query_handlers.py` untouched.

---

## Ship criterion — operator validation checklist (Task 10)

These steps require access to Supabase / Sentry / Telegram. Mark each as you confirm it.

- [ ] **Step 10.1 — Apply the SQL migration to Supabase**
  - Open Supabase dashboard → SQL editor.
  - Paste the contents of `supabase/migrations/20260421_event_log.sql` and run.
  - Verify: `SELECT column_name, data_type FROM information_schema.columns WHERE table_name='event_log' ORDER BY ordinal_position;` returns the 11 columns (id, ts, workflow, run_id, trace_id, parent_run_id, level, event, label, detail, pod).
- [ ] **Step 10.2 — Trigger `morning_check` and verify `event_log` rows**
  ```sql
  SELECT ts, workflow, event, level, label, run_id FROM event_log
  WHERE workflow = 'morning_check' ORDER BY ts DESC LIMIT 10;
  ```
  Expected: at least `cron_started` and (`cron_finished` or `cron_crashed`) with matching `run_id`.
- [ ] **Step 10.3 — Deliberate crash smoke test**
  - On a throwaway branch, add `raise RuntimeError("observability smoke test")` at the top of `morning_check.main()`.
  - Push & trigger the workflow.
  - Verify all of: (1) main chat receives `🚨 MORNING CHECK — CRASH` Telegram message with `run_id` line; (2) Sentry dashboard shows a new issue with `cron_started` as a recent breadcrumb; (3) `event_log` has a `cron_crashed` row; (4) GH Actions marks the run as failed.
  - Revert the throwaway branch.
- [ ] **Step 10.4 — Verify the 3 scripts that had manual `init_sentry`**
  - Trigger `platts_reports`, `platts_ingestion`, `baltic_ingestion` manually (or let them run on their next schedule).
  - Each should show Sentry events tagged with its workflow name + `event_log` rows for `cron_started`/`cron_finished`.
- [ ] **Step 10.5 — Merge the branch to main**
  - `git checkout main && git merge --no-ff feature/observability-phase1 && git push`
  - Confirm production cron runs on the next scheduled firing.

---

## Known followups (out of Phase 1 scope)

These are recorded for the Phase 2–4 plans and post-ship cleanup:

### A. Reviewer-flagged improvements (to event_bus.py)

1. **`_MainChatSink` instantiates `TelegramClient` on every `emit()`.** Fine for Phase 1 (`cron_crashed` fires at most once per run) but wasteful if Phase 2 starts emitting higher-frequency alerts. Move the client into `_MainChatSink.__init__` so it's built once — matches `_SupabaseSink`'s pattern. *(Medium priority — becomes relevant in Phase 2.)*
2. **Level normalization silently coerces `"ERROR"` → `"info"`.** `_VALID_LEVELS = frozenset({"info", "warn", "error"})` is case-sensitive; any uppercase input gets dropped to `"info"` with no warning. Add `level = str(level).lower()` before the membership check. *(Low priority, low blast radius.)*
3. **Sink-failure log message lacks event context.** When `_StdoutSink` (or any sink) raises, `EventBus.emit` logs `"event_bus sink <Name> failed: <exc>"` without the event name that caused it. Add `event_dict.get("event")` to the warning format to make production debugging tractable. *(Low priority.)*
4. **No test for `*args/**kwargs` passthrough in `@with_event_bus`.** `functools.wraps` ensures the signature is preserved, but we never explicitly verify that a decorated `main(foo, bar=1)` actually receives them. One-line test. *(Low priority.)*
5. **`detail` with non-JSON-serializable values is silently dropped.** If a caller emits `detail={"ts": datetime.now()}`, `_StdoutSink.emit` raises `TypeError`, the fan-out guard swallows it, and the event is lost without a readable trace. Add `default=str` to `json.dumps` in `_StdoutSink`. *(Low priority until it bites us.)*
6. **`Any` is imported from `typing` but unused.** Leftover from the sink-Protocol discussion. Remove or use it (e.g., define `class _Sink(Protocol): def emit(self, event_dict: dict[str, Any]) -> None: ...`). *(Cosmetic.)*
7. **`NullHandler` not registered on `logger`.** If a future root-logger config hides WARNING-level output, our sink-failure warnings become invisible. Library-best-practice one-liner. *(Cosmetic until someone configures logging aggressively.)*

### B. Behavior notes (for operator awareness, not code changes)

- **PII in `exc_str[:500]`.** The decorator writes up to 500 chars of the exception string into the `cron_crashed` detail payload and into `_MainChatSink`'s Telegram message. If an exception text includes secrets or user data, it will leak into those channels. Low risk for our current scripts (they operate on market data, not user-provided text), but worth remembering. Mention in the runbook.
- **Redundant alerts on progress-wrapped scripts.** After Task 9, a crash in a script using `ProgressReporter` AND `@with_event_bus` sends TWO Telegram messages to the main chat: one from `progress.fail()` (Task 8) and one from `_MainChatSink` (Task 5). This is intentional belt-and-suspenders — if one path breaks, the other still alerts. If the noise is unacceptable later, we can dedupe by having one layer suppress on presence of the other, but for Phase 1 the redundancy is a feature.
- **`progress.fail()` sends an alert even when `_disabled=True`.** Existing progress-reporter tests still pass because they use `MagicMock`, but the alert-push block is not gated on `self._disabled`. If an operator relied on `_disabled` to fully silence Telegram from a ProgressReporter, they'll be surprised. Low real-world impact (disabled reporters are rare outside tests).

### C. Pre-existing issues surfaced during implementation

- **`baltic_ingestion.py` fails to import locally** due to missing `msal` dependency (Microsoft Graph client). Pre-existing — confirmed on `main` without any of our changes. Production environment presumably has `msal` installed via the GH Actions `requirements.txt`; our dev venv doesn't. Not blocking for Phase 1 ship, but add `msal` to dev-install docs or a `requirements-dev.txt`.
- **3 scripts retain `from execution.core.sentry_init import init_sentry` import line** after we removed the call. Harmless unused import. A ruff/isort pass will clean it up automatically in a future cleanup commit.

### D. Deferred to later phases

- **Phase 2** — `_EventsChannelSink` for firehose of `info`-level events to a dedicated Telegram channel. Requires: operator creates the channel, adds bot as admin, captures numeric ID, and sets `TELEGRAM_EVENTS_CHANNEL_ID` in repo secrets + `.env.example`.
- **Phase 3** — Watchdog cron (`.github/workflows/watchdog.yml`) that emits `cron_missed` when an expected workflow hasn't checked in within its grace window. Requires Phase 1's `event_log` table (ship criterion 5 above).
- **Phase 4** — `/tail` Telegram command for on-demand queries against `event_log`, plus step/`api_call` instrumentation in the 7 scripts (callers will call `bus.emit("step", ...)` directly instead of relying solely on the decorator).
- **TTL on `event_log`** — 30-day nightly cleanup via `pg_cron`. Supabase may not have `pg_cron` enabled on our project; deferred to a manual cleanup workflow or a Phase 4 script.

---

## Phase 2 prerequisites

Before opening the Phase 2 plan, the operator should:

1. Create a new Telegram channel (or use a group with topics enabled).
2. Add the bot as an admin with `send_messages` permission.
3. Capture the channel's numeric ID (via `@userinfobot` or by forwarding a channel message to `@raw_data_bot`).
4. Add `TELEGRAM_EVENTS_CHANNEL_ID=<id>` to repo secrets, and an empty placeholder in `.env.example` for documentation.

With those in place, Phase 2's `_EventsChannelSink` can be merged behind a simple env gate (identical pattern to `_MainChatSink`).

---

## Open questions resolved during implementation

- **Did `event_log` already exist?** No — the table was created fresh via the migration file in Task 1. The `ALTER TABLE` fallback path from the plan's pre-flight was not needed.
- **Does `sentry-sdk` need to be installed in the venv for tests to pass?** No — all Sentry tests use `monkeypatch.setitem(sys.modules, "sentry_sdk", fake)` to inject a fake module. The real SDK is only imported at runtime inside sink emit methods, protected by try/except.
- **Does `supabase-py` need to be installed?** Present in the venv (`supabase-py 2.28.0`), but tests don't exercise it — `_get_supabase_client` is monkeypatched to return a fake client.
- **Does `TelegramClient.send_message` accept the kwargs we're using?** Yes — signature is `def send_message(self, text, chat_id=None, reply_markup=None, parse_mode="Markdown")`, which accepts `text=` and `chat_id=` as kwargs.
- **Can the decorator handle async `main()`?** No, and we don't need it to — all 7 scripts in this repo use synchronous `def main():`. If an async script is added later, we'll need a sibling `async_with_event_bus` decorator (or widen the existing one to check for coroutines).
