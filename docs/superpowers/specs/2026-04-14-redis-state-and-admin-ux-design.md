# Redis-Backed State + Admin UX Improvements — Design Spec

**Date:** 2026-04-14
**Status:** Approved design, pending implementation plan

## Goal

Introduce a small Redis-backed state store that persists workflow run results across the 5 scheduled workflows, and use it to deliver three admin-facing UX improvements:

1. Consecutive-failure alerts on Telegram (threshold 3)
2. A `/status` command that returns a one-screen snapshot of every workflow
3. A visible toast confirmation on the `/list` contact toggle

## Motivation

Today the system has no cross-run persistent state outside Google Sheets, so the admin has:

- No alerting when a workflow fails silently multiple times in a row (crashes stop at `sys.exit(1)` with no Telegram message).
- No quick way to check "is everything okay today?" without opening the dashboard.
- No feedback when tapping a toggle button in `/list` — a 2–3s Sheets API latency leaves the admin uncertain whether the tap registered.

Redis (Upstash free tier) solves the persistence gap cleanly: atomic `INCR` for streak counters, native TTL for future needs (drafts, dedup), and sub-ms reads for a snappy `/status` command.

## Scope

**In scope:**

- New `execution/core/state_store.py` module wrapping `redis` with no-op fallback when `REDIS_URL` is unset.
- Integration into `ProgressReporter` so every workflow run's outcome is recorded.
- New `ProgressReporter.fail(exception)` method for crash visibility (also fixes P0#6 from the project review).
- Minimal wiring of `market_news_ingestion.py` and `rationale_ingestion.py` so `/status` can show their state.
- `/status` command handler in `webhook/app.py`.
- Toast confirmation on the `/list` toggle callback.

**Out of scope:**

- Persisting `DRAFTS`, `SEEN_ARTICLES`, `ADJUST_STATE` in Redis (separate improvement, P1#7 in the review).
- Dashboard changes.
- New ops dashboards or metrics.
- Idempotency changes to `daily_report` (confirmed intentional: 6x/day by design, fresh futures data each run).

## Architecture

```
┌─────────────── Cron Workflows (GH Actions Python) ───────────────┐
│                                                                  │
│  ProgressReporter.finish(report)                                 │
│    ├─► (existing) Telegram edit message                          │
│    └─► state_store.record_success|record_failure(workflow, ...)  │
│                                                                  │
│  ProgressReporter.finish_empty(reason)                           │
│    ├─► (existing) Telegram edit with ℹ️                          │
│    └─► state_store.record_empty(workflow, reason)                │
│                                                                  │
│  ProgressReporter.fail(exc)   ← new, called from outer try/except│
│    ├─► Telegram edit with 🚨 CRASH                               │
│    └─► state_store.record_crash(workflow, exc_text)              │
│                                                                  │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                               ▼
                      ┌─────────────────┐
                      │     Redis       │
                      │   (Upstash)     │
                      │                 │
                      │ wf:last_run:*   │
                      │ wf:streak:*     │
                      │ wf:failures:*   │
                      └────────┬────────┘
                               │
┌──────────────────────────────┴───────────────────────────────────┐
│                      Flask Webhook                                │
│                                                                   │
│  /status command handler                                          │
│    ├─► state_store.get_status(all 5 workflows) — parallel-ish     │
│    ├─► Parse .github/workflows/*.yml for next cron (croniter)     │
│    └─► Format + TelegramClient.send_message                       │
│                                                                   │
│  tgl:<phone> callback (existing, modified)                        │
│    └─► answer_callback_query(toast: "✅ Name ativado")            │
└───────────────────────────────────────────────────────────────────┘
```

Streak-alert side-effect: when `state_store.record_failure` or `record_crash` pushes the streak to >= 3, the store itself sends a distinct Telegram alert message (not an edit of the workflow's own message) so it's visually separate in the chat.

## State Store (`execution/core/state_store.py`)

**Dependency:** `redis==5.x` (Python client, sync mode).

**Public API (functions, not a class — stateless except for cached client):**

```python
def record_success(workflow: str, summary: dict, duration_ms: int) -> None
def record_failure(workflow: str, summary: dict, duration_ms: int) -> None
def record_empty(workflow: str, reason: str) -> None
def record_crash(workflow: str, exc_text: str) -> None
def get_status(workflow: str) -> Optional[dict]
def get_all_status(workflows: list[str]) -> dict[str, Optional[dict]]
```

**Redis keys:**

| Key | Type | TTL | Written by | Read by |
|---|---|---|---|---|
| `wf:last_run:{workflow}` | JSON string | none | `record_*` | `get_status` |
| `wf:streak:{workflow}` | integer | none | `record_failure/crash` (INCR), `record_success` (DEL) | `get_status` |
| `wf:failures:{workflow}` | list (LPUSH, LTRIM to 3) | none | `record_failure/crash` | streak-alert formatter |

**`wf:last_run` JSON schema:**

```json
{
  "status": "success|failure|empty|crash",
  "time_iso": "2026-04-14T08:30:12-03:00",
  "summary": { "total": 100, "success": 100, "failure": 0 },
  "duration_ms": 240000,
  "reason": "dados incompletos (5/26)"  // only for empty
}
```

**Configuration:**

- `REDIS_URL` env var, format `rediss://default:<password>@<host>:<port>`
- If unset or empty: all functions become no-ops silently. No connection attempted. `get_status` returns `None`.
- Connection timeout: 3s. On timeout or connection error, logs warning and returns no-op for that call (does not cache the failure — next call retries).

**Streak alert side-effect inside `record_failure` / `record_crash`:**

1. `INCR wf:streak:{workflow}` → new_value
2. `LPUSH wf:failures:{workflow}` with `{time, reason}` then `LTRIM 0 2` (keep last 3)
3. If `new_value >= 3`, call `_send_streak_alert(workflow)`:
   - Read last 3 failures from the list
   - Format a separate Telegram message (new `send_message`, not edit):
     ```
     🚨 ALERTA: DAILY_REPORT falhou 3x seguidas

     Ultimas falhas:
     • 08:00 — crash (LSEG connection timeout)
     • 09:00 — crash (LSEG connection timeout)
     • 10:00 — 100% delivery failure

     [Ver dashboard](https://...)
     ```
   - Uses same `TelegramClient` singleton (lazy-imported) and same `TELEGRAM_CHAT_ID`

**Failure mode invariant:** state_store never raises. Redis or Telegram errors log warnings and return. Workflows are never broken by this module.

## ProgressReporter Integration

Modify `execution/core/progress_reporter.py`:

### `finish(report, message=None)` — tail additions

After the successful Telegram edit (existing code), add:

```python
summary = {
    "total": report.total,
    "success": report.success_count,
    "failure": report.failure_count,
}
duration_ms = int((report.finished_at - report.started_at).total_seconds() * 1000)

if report.success_count > 0:
    state_store.record_success(self.workflow, summary, duration_ms)
else:
    state_store.record_failure(self.workflow, summary, duration_ms)
```

(Wrapped in `try/except` so state_store errors never break workflow.)

### `finish_empty(reason)` — tail addition

```python
try:
    state_store.record_empty(self.workflow, reason)
except Exception as exc:
    print(f"[WARN] state_store.record_empty failed: {exc}")
```

### `fail(exception)` — new method

```python
def fail(self, exception: Exception) -> None:
    """Edit message with crash marker and record to state store.
    Called from outer try/except in script main(). Never raises."""
    exc_text = str(exception)[:200]
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
    try:
        state_store.record_crash(self.workflow, exc_text)
    except Exception as e:
        print(f"[WARN] state_store.record_crash failed: {e}")
```

### Scripts — outer try/except

Each of the 4 currently-wired scripts (`morning_check`, `send_daily_report`, `baltic_ingestion`, `send_news`) and the 2 ingestion scripts (`market_news_ingestion`, `rationale_ingestion`) wraps `main()`'s body in:

```python
try:
    # ... existing body ...
except Exception as exc:
    if progress is not None:
        progress.fail(exc)
    raise
```

For `market_news_ingestion.py` and `rationale_ingestion.py` (no `ProgressReporter` today), wire only the minimal result recording:

- Near top of `main()`:
  ```python
  WORKFLOW_NAME = "market_news"  # or "rationale_news"
  ```
- Before each non-crash exit path, call `state_store.record_empty(WORKFLOW_NAME, reason)` or `record_success(WORKFLOW_NAME, {...})` depending on outcome.
- Outer try/except calls `state_store.record_crash(WORKFLOW_NAME, str(exc)[:200])` before re-raising.

This does NOT add ProgressReporter to these scripts — they still emit their own approval-request Telegram message as today. The state store just tracks the run outcome.

## `/status` Command

**Handler location:** `webhook/app.py`, in the existing message-routing function.

**Behavior:**

1. On incoming message with text `/status`:
2. `if not contact_admin.is_authorized(chat_id): return 401-equivalent silent reject`
3. Call `state_store.get_all_status(ALL_WORKFLOWS)` where
   ```python
   ALL_WORKFLOWS = ["morning_check", "daily_report", "baltic_ingestion",
                    "market_news", "rationale_news"]
   ```
4. For each workflow, parse next scheduled run:
   - New helper `_parse_next_run(workflow_name)` that reads `.github/workflows/{workflow_name}.yml`, extracts `on.schedule[].cron`, parses with `croniter`, returns next UTC datetime, converts to BRT (America/Sao_Paulo).
   - If multiple cron schedules (e.g. daily_report has 6), returns the earliest upcoming.
   - If no schedule found or parse fails: returns `None`.
5. Build one line per workflow (format below).
6. Send via `TelegramClient.send_message` with Markdown parse mode.

**Line format per workflow:**

| State | Format |
|---|---|
| streak >= 3 | `{workflow}: 🚨 {streak} falhas seguidas` |
| status=success | `{workflow}: ✅ {HH:MM} BRT ({ok}/{total}, {Nm})` |
| status=failure | `{workflow}: ❌ {HH:MM} BRT (0/{total} enviadas)` |
| status=crash | `{workflow}: 💥 {HH:MM} BRT (crash: {reason_short})` |
| status=empty | `{workflow}: ℹ️ {HH:MM} BRT ({reason})` |
| no data in Redis | `{workflow}: ⏳ proximo {HH:MM} BRT` |

Workflow label displayed as-is (e.g., `morning_check`). Duration shown as `4m` or `45s` for readability.

**Full response template:**

```markdown
📊 STATUS ({DD/MM HH:MM} BRT)

morning_check:    ✅ 08:30 BRT (100/100, 4m)
daily_report:     ✅ 09:00 BRT (100/100, 3m)
baltic_ingestion: ⏳ proximo 16:00 BRT
market_news:      ℹ️ 06:00 BRT (approval pendente)
rationale_news:   🚨 3 falhas seguidas

[Dashboard](https://workflows-minerals.vercel.app/)
```

**Edge cases:**

- `state_store` returns `None` for all (Redis down / not configured): respond `⚠️ Store de estado indisponivel. Abra o dashboard pra ver historico.`
- Column alignment: use Python f-string left-pad to the longest workflow name length (17 chars as of today).
- All workflows without Redis data: `/status` still works, just shows "⏳ proximo" for everyone.

## `/list` Toggle Confirmation

**Change in `webhook/app.py` callback handler for `tgl:<phone>`:**

After the successful `toggle_contact(...)` call and before re-rendering the list, call:

```python
new_status_text = "ativado" if toggled_to_big else "desativado"
telegram_client.answer_callback_query(
    callback_query_id,
    text=f"✅ {contact_name} {new_status_text}",
    show_alert=False,
)
```

On exception from `toggle_contact`:

```python
telegram_client.answer_callback_query(
    callback_query_id,
    text=f"❌ Erro: {str(exc)[:100]}",
    show_alert=False,
)
return  # do not re-render; preserve previous state
```

**Extend `TelegramClient.answer_callback_query`:**

Current signature `answer_callback_query(callback_query_id, text="Processado!")`. Add optional `show_alert: bool = False` parameter, forwarded to the Telegram API call.

## Testing

`tests/test_state_store.py` — ~12 unit tests using `fakeredis`:
- `record_success` writes `wf:last_run` and deletes `wf:streak`
- `record_failure` increments `wf:streak` and pushes to `wf:failures`
- `record_failure` at streak=3 triggers `_send_streak_alert` (injectable callback for test)
- `record_empty` does not touch streak or failures list
- `record_crash` behaves like `record_failure` but with status="crash"
- `get_status` returns parsed dict
- `get_all_status` returns dict keyed by workflow
- `REDIS_URL` unset: all functions are silent no-ops
- Redis connection error: functions return `None` without raising
- Streak reset: after 2 failures, a success sets streak=0 and future failure starts at 1

`tests/test_progress_reporter.py` — append ~5 tests:
- `finish(report)` with success_count>0 calls `record_success`
- `finish(report)` with 100% failure calls `record_failure`
- `finish_empty(reason)` calls `record_empty`
- `fail(exc)` edits message with 🚨 CRASH and calls `record_crash`
- `state_store` errors in any of the above do not propagate (verify via mock raising)

`tests/test_webhook_status.py` — new, ~7 tests:
- `/status` from unauthorized chat returns silent reject (does not call state_store)
- `/status` formats success line correctly
- `/status` formats streak >= 3 line correctly
- `/status` with no Redis data shows "⏳ proximo" with parsed cron
- `/status` when state_store returns all None for all workflows shows fallback message
- `/status` with empty status shows ℹ️ + reason
- Next-run parsing handles multiple cron schedules and returns earliest

`tests/test_webhook_toggle_confirm.py` — new, ~3 tests:
- Successful toggle calls `answer_callback_query` with name + "ativado"/"desativado"
- Failed toggle calls `answer_callback_query` with "❌ Erro" text
- `show_alert=False` is passed (toast, not modal)

`tests/test_telegram_client.py` — new, ~2 tests (client didn't have tests before):
- `answer_callback_query` with `show_alert=True` forwards param
- Default `show_alert=False`

## Dependencies

Add to `requirements.txt`:

```
redis>=5.0,<6.0
croniter>=2.0,<3.0
fakeredis>=2.20,<3.0
```

(`fakeredis` in prod requirements rather than dev-only because the repo has no split today and it's tiny.)

## Configuration

New env var required:

- `REDIS_URL` — provided by Upstash when you create the DB. Set in:
  - Railway (webhook service)
  - GitHub Actions secrets (all 5 workflow yml files need `REDIS_URL: ${{ secrets.REDIS_URL }}` in the env block)

If `REDIS_URL` is absent, the system degrades gracefully: state store is no-op, streak alerts never fire, `/status` shows fallback message, but all existing functionality keeps working.

## Rollout

1. Land `state_store.py` + tests. Verify fakeredis tests pass.
2. Sign up for Upstash Redis, create DB, copy URL to GitHub + Railway.
3. Land `ProgressReporter` integration + crash handler + `/status` + toggle toast in a single PR. Tests pass.
4. Observe next scheduled run of each workflow. Verify Redis gets populated. Call `/status` to see.
5. No feature flag — behavior is additive (no existing UX changes).

## Non-Goals

- No Redis-based distributed locking.
- No replay of missed streak alerts after outage.
- No admin command to reset streak counters manually (if needed later, a one-liner `/reset-streak <workflow>` can be added).
- No migration of existing Sheets "Controle" tab into Redis — that tab continues to serve its original purpose (human-readable daily-send ledger).
