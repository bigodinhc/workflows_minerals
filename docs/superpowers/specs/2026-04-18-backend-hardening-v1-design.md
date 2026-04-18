# Backend Hardening v1 — Design Spec

**Date:** 2026-04-18
**Status:** Draft — pending user review
**Scope:** Safety-net tests, router split, observability, reliability, live progress UX

---

## 1. Goal & Scope

### Problem

The Telegram/WhatsApp bot backend has three coupled fragilities:

1. **FSM handlers repeatedly broken by catch-all patterns.** Three commits in two months (`17135d8`, `a6214a0`, `2cab598`) fixed bugs where a catch-all text handler intercepted messages destined for FSM state handlers (`AdjustDraft`, `RejectReason`, `BroadcastMessage`). No test guards this bug class from returning.
2. **`callbacks.py` god file.** 601 lines mix five domains (curation approvals, report navigation, queue pagination, contact toggle, workflow triggers). Any edit has a high blast radius and no route-specific test coverage.
3. **Production is blind.** No Sentry, no metrics dashboard, no idempotency on WhatsApp sends (`send_whatsapp` duplicates on retry), silent `except Exception: pass` around `edit_message_text` calls, and long-running workflows (broadcast, scheduled crons) run "mudos" — the user has no live feedback about which phase is executing.

### Objective

A backend that is **safer to modify** and **observable in production**, without altering any external behavior of the bot.

### Scope (3 sequential phases)

- **Phase 1 — Safety net.** ~40 characterization tests covering production callback paths + FSM isolation tests for `messages.py`. Gate: Phase 2 does not start until Phase 1 is 100% green.
- **Phase 2 — Split.** Decompose `callbacks.py` into 5 domain routers (`_curation`, `_reports`, `_queue`, `_contacts`, `_workflows`). Migrate stray reply-keyboard handlers from `messages.py` to `reply_kb_router`. Clean up inline imports. Zero behavior change.
- **Phase 3 — Reliability + observability + live progress.** Idempotency on WhatsApp sends, Sentry in webhook + execution scripts, Prometheus `/metrics` endpoint, fix silent `except:pass` in edit calls, Postgres `event_log` table, extend `ProgressReporter` with three sinks (Telegram card + event_log + structured log) and debouncing, instrument the three blind cron scripts (`platts_ingestion`, `platts_reports`, `baltic_ingestion`).

### Non-goals (explicit)

- Hotfix of the hardcoded `IRONMARKET_API_KEY` (user explicitly deferred).
- CI workflow to run tests on push (item #4 from CONCERNS — separate follow-up).
- Rate limiting / auth hardening on Mini App endpoints (item #10 from CONCERNS).
- Any business-logic rewrite: 3-agent pipeline (Writer/Critique/Curator), curation router classification, Apify actor code, Sheets contact flow, scraper behavior.
- External log aggregator (Loki / Better Stack / Papertrail). The Postgres `event_log` covers the rastreability need.
- Refactor of `messages.py` beyond relocating the seven `on_reply_*` handlers.

### Overall acceptance criterion

After the three phases ship, the developer can:

1. Modify any callback without fear of silent regression (Phase 1 + Phase 2).
2. See a live progress card in Telegram for every long-running workflow, not just broadcast (Phase 3).
3. Receive a Sentry alert within ~30s of a new exception in production (Phase 3).
4. Query `SELECT * FROM event_log WHERE draft_id = '<id>' ORDER BY created_at` and see the full timeline of a draft (Phase 3).
5. View aggregate counters (`whatsapp_messages_total`, `telegram_edit_failures_total`) on a Prometheus dashboard (Phase 3).

---

## 2. Phase 1 — Safety Net (Characterization Tests)

### Objective

Create a test net that freezes **current** behavior of `callbacks.py` and guarantees FSM isolation in `messages.py`. The net must be 100% green before any line of code is moved in Phase 2.

### Strategy

Characterization tests, not TDD. Read what each handler does today → assert that in a test → in Phase 2, move the code; same assertions stay valid.

### Mocking approach (two layers)

**For callbacks (internal-logic focus):** pure mocks via `pytest-mock` + `unittest.mock.AsyncMock`.

```python
# tests/test_callbacks_curation.py (example)
@pytest.fixture
def mock_callback():
    cb = MagicMock(spec=CallbackQuery)
    cb.from_user.id = 12345
    cb.message = AsyncMock()
    cb.answer = AsyncMock()
    return cb

@pytest.mark.asyncio
async def test_approve_draft_triggers_process_approval(mock_callback, fake_redis, mocker):
    mocker.patch("webhook.bot.routers.callbacks_curation.bot", AsyncMock())
    spy = mocker.patch("webhook.bot.routers.callbacks_curation.process_approval_async")
    await on_draft_action(mock_callback, DraftAction(action="approve", draft_id="abc"))
    spy.assert_called_once()
```

**For FSM isolation (routing focus):** mini-integration — real `Dispatcher` + `AsyncMock(spec=Bot)` + fake `Update` objects in different FSM states (`AdjustDraft.waiting_feedback`, `BroadcastMessage.waiting_text`, no state). Exercises the real router registration order.

### Test file layout (mirrors the Phase 2 split)

```
tests/
├── conftest.py                           # + fixtures: mock_bot, mock_callback, fake_fsm_context
├── test_callbacks_curation.py            # ~10 tests: draft approve/reject/adjust/adjust-send
├── test_callbacks_reports.py             # ~8 tests: type → year → month → list → download
├── test_callbacks_queue.py               # ~5 tests: pagination next/prev/empty
├── test_callbacks_contacts.py            # ~4 tests: toggle admin, list
├── test_callbacks_workflows.py           # ~6 tests: trigger, re-run, status
└── test_messages_fsm_isolation.py        # ~7 tests: each FSM state in isolation, no catch-all
```

**Total:** ~40 tests. One file per callback domain, aligning 1-to-1 with Phase 2 routers.

### New fixtures in `conftest.py`

- `mock_bot` — `AsyncMock(spec=Bot)` with common methods (`send_message`, `edit_message_text`, `answer_callback_query`)
- `mock_callback_query(user_id, data)` — factory returning a configured `MagicMock(spec=CallbackQuery)`
- `mock_message(text, state)` — factory for FSM tests
- `fsm_context_in_state(state)` — helper producing a `FSMContext` mock pre-set to a given state

### Definition of done

- [ ] 40 tests green locally (`pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py`)
- [ ] 100% of action codes used in production (from `webhook/bot/callback_data.py`) have at least one test
- [ ] Each test idempotent (autouse fixture resets module-level caches)
- [ ] Full Phase 1 suite runs in ≤ 3s (no network, no real I/O)
- [ ] `tests/README.md` with one short section documenting the mock pattern

---

## 3. Phase 2 — Split + Router Consolidation

### Objective

No user-visible behavior change. Pure rearrangement to shrink blast radius of future edits. Phase 1 tests are the guard; a manual smoke test in the dev bot is the final gate (see Definition of done).

**Note on sub-task 3.2:** Moving the `on_reply_*` handlers from `message_router` to `reply_kb_router` changes which router processes those messages, but `main.py` already includes `reply_kb_router` before `message_router` and the handlers match on exact text, so the observable outcome is identical. The smoke test confirms this.

### 3.1 Split `callbacks.py` (601 lines → 5 files)

| New file | Handlers | Approx. lines |
|---|---|---|
| `callbacks_curation.py` | `on_draft_adjust`, `on_draft_reject`, `on_draft_action` (approve/ignore/whatsapp direct), `_finalize_card` | ~180 |
| `callbacks_reports.py` | `on_report_type`, `on_report_years`, `on_report_year`, `on_report_month`, `on_report_download`, `on_report_back` | ~140 |
| `callbacks_queue.py` | `on_menu_action` (queue nav/pagination subset) | ~80 |
| `callbacks_contacts.py` | Contact toggle/list callbacks | ~60 |
| `callbacks_workflows.py` | Workflow triggers + re-run | ~70 |

Each file exports a `Router()` named `callbacks_<domain>_router`.

**`main.py` update:**

```python
from bot.routers.callbacks_curation import callbacks_curation_router
from bot.routers.callbacks_reports import callbacks_reports_router
from bot.routers.callbacks_queue import callbacks_queue_router
from bot.routers.callbacks_contacts import callbacks_contacts_router
from bot.routers.callbacks_workflows import callbacks_workflows_router

# Order matters: specific filters before generic filters
dp.include_router(callbacks_curation_router)
dp.include_router(callbacks_reports_router)
dp.include_router(callbacks_queue_router)
dp.include_router(callbacks_contacts_router)
dp.include_router(callbacks_workflows_router)
```

Registration order preserves the current behavior: curation-specific (`DraftAction.filter(F.action == "adjust")`) before generic (`DraftAction.filter()`), before `MenuAction.filter()`, before others.

**`_helpers.py`:** stays shared. `_finalize_card` moves to `callbacks_curation.py` (only consumer).

### 3.2 Reply handler consolidation

`main.py` already imports `reply_kb_router` separate from `message_router`, so the external contract is ready. Step:

1. Check whether the seven `on_reply_*` handlers (`on_reply_reports`, `on_reply_queue`, `on_reply_workflows`, `on_reply_settings`, `on_reply_writer`, `on_reply_broadcast`, `on_reply_admin`) in `messages.py` currently decorate with `@message_router` or `@reply_kb_router`.
2. If mixed: change the seven decorators to `@reply_kb_router.message(...)`. No other code change.
3. If already correct: skip this sub-task.

Post-migration: `messages.py` contains only FSM state handlers (~130 lines).

### 3.3 Inline imports cleanup

In each of the five new files, hoist all `from reports_nav import ...`, `from workflow_trigger import ...`, `from bot.keyboards import ...` imports from function bodies to the module top. If any triggers a circular import, leave it inline with a comment documenting the cycle (`# cyclic: webhook.X imports us`). Estimate: zero cycles (the inlined modules are leaves).

### Migration plan (commit order)

1. Create 5 new files with empty `Router()` + docstring.
2. Move handlers one domain at a time — 5 commits (`refactor(bot): split callbacks.py — curation`, etc.).
3. After each move, run `pytest tests/test_callbacks_*.py`. Must pass green.
4. Delete `callbacks.py` (now empty) + update `main.py` imports. One commit.
5. Sub-task 3.2 (reply handlers): 1 commit.
6. Sub-task 3.3 (inline imports): 1 commit.

Total: ~7 small reviewable commits.

### Definition of done

- [ ] `callbacks.py` deleted; 5 new routers all < 200 lines
- [ ] `messages.py` is ~130 lines with only FSM state handlers; reply handlers all in `reply_kb_router`
- [ ] `pytest` 100% green (Phase 1 suite + pre-existing tests)
- [ ] Zero changes to `callback_data.py`, `states.py`, `keyboards.py` (not in scope)
- [ ] Manual smoke test in dev bot: queue opens, approve succeeds, reports navigation works end-to-end

---

## 4. Phase 3 — Reliability + Observability + Live Progress

### 4.1 Idempotency on WhatsApp send

Helper in `webhook/dispatch.py`:

```python
import hashlib

def _idempotency_key(phone: str, draft_id: str, message: str) -> str:
    digest = hashlib.sha1(f"{phone}|{draft_id}|{message}".encode()).hexdigest()
    return f"whatsapp:sent:{digest}"

async def send_whatsapp(phone: str, message: str, draft_id: str, redis) -> dict:
    key = _idempotency_key(phone, draft_id, message)
    # SET NX EX 86400 — atomic check-and-mark, 24h window
    if await redis.set(key, "1", ex=86400, nx=True) is None:
        logger.info("whatsapp_idempotency_hit",
                    extra={"phone_hash": _phone_hash(phone), "draft_id": draft_id})
        return {"status": "duplicate", "skipped": True}
    # ... existing UAZAPI call
```

- `draft_id` is required. For free-form broadcast (no draft), caller passes `draft_id=f"broadcast:{int(time.time())}"`. Because this key embeds the current timestamp, two free-form broadcasts of the same text at different moments are treated as distinct — that is intentional (free-form is a deliberate action, not a retry).
- TTL 24h balances retry legitimacy vs. duplicate-prevention window.
- The check-and-mark must happen before the UAZAPI call; failing the UAZAPI call after marking does not un-mark (the caller can generate a new `draft_id` to explicitly re-try).

### 4.2 Sentry

**Dependency:** `sentry-sdk[aiohttp]>=2.0.0` in `requirements.txt`.

**Init locations:**

```python
# webhook/bot/main.py — top of main()
import sentry_sdk
from sentry_sdk.integrations.aiohttp import AioHttpIntegration

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    environment=os.getenv("RAILWAY_ENVIRONMENT", "dev"),
    traces_sample_rate=0.1,
    integrations=[AioHttpIntegration()],
)
```

```python
# execution/core/sentry_init.py (new)
def init_sentry(script_name: str) -> None:
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        logger.warning("SENTRY_DSN not set; Sentry disabled for %s", script_name)
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("RAILWAY_ENVIRONMENT", "dev"),
        traces_sample_rate=0.1,
    )
    sentry_sdk.set_tag("script", script_name)
```

Each script in `execution/scripts/` calls `init_sentry(__name__)` at the top. If `SENTRY_DSN` is unset, `sentry_sdk.init()` is a no-op (safe in dev).

**New env var:** `SENTRY_DSN` — add to `.env.example` and Railway environment.

### 4.3 Prometheus `/metrics`

**Dependency:** `prometheus-client>=0.20.0`.

**New module `webhook/metrics.py`:**

```python
from prometheus_client import Counter, Histogram

whatsapp_sent = Counter(
    "whatsapp_messages_total", "WhatsApp sends", ["status"]  # success|failure|duplicate
)
whatsapp_duration = Histogram(
    "whatsapp_duration_seconds", "WhatsApp send latency"
)
edit_failures = Counter(
    "telegram_edit_failures_total", "edit_message failures", ["reason"]  # not_modified|bad_request|unexpected|flood
)
progress_card_edits = Counter(
    "progress_card_edits_total", "ProgressReporter Telegram card edits"
)
```

**Endpoint in `webhook/routes/api.py`:**

```python
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

@routes.get("/metrics")
async def metrics_endpoint(request: web.Request) -> web.Response:
    return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST)
```

Instrument `dispatch.py` (send flow) and the former silent-swallow points (see 4.4).

### 4.4 Fix silent `except: pass`

Known locations:

- `webhook/dispatch.py:104-105, 121-122, 133-134, 145-146` (edit progress messages)
- `webhook/bot/routers/callbacks_curation.py` — `_finalize_card` (post-split) lines ~53-58

Standardized replacement:

```python
from aiogram.exceptions import TelegramBadRequest

try:
    await bot.edit_message_text(...)
except TelegramBadRequest as e:
    msg = str(e).lower()
    if "message is not modified" in msg:
        edit_failures.labels(reason="not_modified").inc()
        # expected no-op; don't log
    else:
        edit_failures.labels(reason="bad_request").inc()
        logger.warning("edit_failed", extra={"chat_id": chat_id, "error": str(e)})
except Exception as e:
    edit_failures.labels(reason="unexpected").inc()
    logger.warning("edit_unexpected", exc_info=e)
```

### 4.5 Postgres `event_log` table

**Supabase migration:**

```sql
create table event_log (
  id bigserial primary key,
  workflow text not null,       -- "broadcast", "platts_ingestion", "curation", ...
  run_id text,                  -- groups steps of same execution
  draft_id text,                -- when applicable
  level text not null,          -- info | warning | error
  label text not null,          -- short phase label, e.g. "Writer generating"
  detail text,                  -- optional verbose detail
  context jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);
create index event_log_draft_idx on event_log (draft_id) where draft_id is not null;
create index event_log_workflow_time_idx on event_log (workflow, created_at desc);
create index event_log_run_idx on event_log (run_id) where run_id is not null;
```

Retention: manual truncate initially; later, scheduled `DELETE WHERE created_at < now() - interval '90 days'` (out of current scope).

### 4.6 ProgressReporter — three sinks + debounce

Extend existing `execution/core/progress_reporter.py` (217 lines today).

```python
class ProgressReporter:
    def __init__(self, bot, chat_id, workflow: str, run_id: str,
                 draft_id: Optional[str] = None, supabase_client=None):
        self.workflow = workflow
        self.run_id = run_id
        self.draft_id = draft_id
        self._supabase = supabase_client
        self._pending_card_state = []
        self._last_edit_at: Optional[float] = None
        self._flush_task: Optional[asyncio.Task] = None
        # ... existing state ...

    async def step(self, label: str, detail: str = "", level: str = "info") -> None:
        """Emit one progress step to all three sinks."""
        await self._emit_structured_log(level, label, detail)
        asyncio.create_task(self._persist_event_log(level, label, detail))  # fire-and-forget
        await self._update_telegram_card(label, detail, level)

    async def _emit_structured_log(self, level, label, detail):
        logger.log(
            _level_num(level),
            "progress.step",
            extra={"workflow": self.workflow, "run_id": self.run_id,
                   "draft_id": self.draft_id, "label": label, "detail": detail},
        )

    async def _persist_event_log(self, level, label, detail):
        if self._supabase is None:
            return
        try:
            self._supabase.table("event_log").insert({
                "workflow": self.workflow, "run_id": self.run_id,
                "draft_id": self.draft_id, "level": level,
                "label": label, "detail": detail,
            }).execute()
        except Exception as e:
            logger.warning("event_log_insert_failed", exc_info=e)

    async def _update_telegram_card(self, label, detail, level):
        self._pending_card_state.append({"label": label, "detail": detail, "level": level})
        now = time.monotonic()
        if self._last_edit_at and (now - self._last_edit_at) < 2.0:
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._delayed_flush())
            return
        await self._flush_now()

    async def _delayed_flush(self):
        await asyncio.sleep(2.0)
        await self._flush_now()

    async def _flush_now(self):
        # Build the card body from accumulated step history (self._pending_card_state)
        # Apply the same glyphs (✅/⏳/⬜/⚠️) described in the Card format below.
        # Call bot.edit_message_text with the error-handling pattern from section 4.4
        # (TelegramBadRequest "not modified" is silent; other errors log + increment counter).
        card_text = self._render_card()
        try:
            await self._bot.edit_message_text(
                chat_id=self._chat_id, message_id=self._message_id, text=card_text,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                edit_failures.labels(reason="bad_request").inc()
                logger.warning("progress_card_edit_failed", extra={"error": str(e)})
            else:
                edit_failures.labels(reason="not_modified").inc()
        except Exception as e:
            edit_failures.labels(reason="unexpected").inc()
            logger.warning("progress_card_edit_unexpected", exc_info=e)
        self._last_edit_at = time.monotonic()
        progress_card_edits.inc()

    async def finish(self, report=None, message=None):
        # Ensure final state is flushed regardless of debounce
        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
        await self._flush_now()
        # ... existing finish logic ...
```

**Card format** (user-facing):

```
📡 Broadcast #42
━━━━━━━━━━━━━━━━━━━━━━
✅ Contacts loaded (127)               [0.8s]
✅ Writer drafted                      [2.1s]
✅ Critique reviewed                   [1.4s]
⏳ Curator finalizing...
⬜ Send WhatsApp (0/127)
⬜ Persist audit
```

Glyphs: ✅ done, ⏳ running, ⬜ pending, ⚠️ error.

### 4.7 Instrument blind cron scripts

Add `ProgressReporter` calls in the three scripts that currently run silent:

- `execution/scripts/platts_ingestion.py` — steps: "Apify actor started" → "Dataset fetched (N items)" → "Dedup applied (M new)" → "Staged in Redis" → "Done"
- `execution/scripts/platts_reports.py` — "Actor started" → "PDF downloaded" → "Uploaded to Supabase" → "Telegram card sent" → "Done"
- `execution/scripts/baltic_ingestion.py` — "Email fetched" → "PDF extracted" → "Claude parsed" → "Postgres upsert" → "Done"

Each script instantiates one `ProgressReporter(workflow=__name__, run_id=uuid4(), …)` at start. No business-logic change.

### Definition of done (Phase 3)

- [ ] Broadcast retry within 24h does not duplicate WhatsApp sends (test with `fakeredis`)
- [ ] Sentry captures a test exception from a dev-only `/test-sentry` admin endpoint
- [ ] `GET /metrics` returns 200 with `whatsapp_messages_total` and `telegram_edit_failures_total`
- [ ] Zero `except Exception: pass` remain in `webhook/dispatch.py` and `webhook/bot/routers/callbacks_curation.py` (validated by grep)
- [ ] Migration `event_log` applied in Supabase (dev + prod)
- [ ] `ProgressReporter.step()` writes to all three sinks; telegram card never edits more than once per 2s (verified manually + by `progress_card_edits` counter)
- [ ] Three cron scripts show live progress cards in `TELEGRAM_CHAT_ID`
- [ ] SQL `SELECT * FROM event_log WHERE draft_id = '<real_id>' ORDER BY created_at` returns a complete timeline for a real broadcast

### Commit plan (~7 commits)

1. `feat(dispatch): idempotency key on WhatsApp send`
2. `feat(obs): Sentry init in webhook + execution scripts`
3. `feat(obs): Prometheus /metrics endpoint + counters`
4. `fix(dispatch): replace silent except:pass with TelegramBadRequest handling`
5. `feat(db): event_log migration + indexes`
6. `feat(core): ProgressReporter.step() with 3 sinks + debounce`
7. `feat(scripts): instrument platts/baltic ingestion with ProgressReporter`

---

## 5. Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Phase 2 split introduces silent regression | medium | high | Gate: Phase 2 does not start until Phase 1 is green. Manual smoke test after split. |
| Telegram rate-limit from card edits | medium | medium | 2-second debounce + `telegram_edit_failures_total{reason="flood"}` counter. Alert if >5/min. |
| `event_log` insert blocks request path | low | medium | Fire-and-forget `asyncio.create_task`; local try-catch with stdout fallback. |
| `SENTRY_DSN` missing in Railway prod | medium | low | `sentry_sdk.init()` is no-op without DSN; init helper logs a warning in prod. |
| `event_log` migration fails in prod | low | medium | Apply in dev first. Rollback is `drop table event_log cascade` — no dependent queries. |
| Circular import when hoisting inline imports | low | low | If encountered, leave inline with `# cyclic: webhook.X imports us` comment. Don't block. |
| Debounce swallows the final state if finish() races | low | medium | `finish()` cancels pending flush task and calls `_flush_now()` explicitly. |

---

## 6. Rollout

- **One PR per phase** (3 PRs total). Each is mergeable and useful on its own.
- **No feature flag.** Changes are additive (except the split, which is code motion). No remote kill switch needed.
- **Deploy order:** Phase 1 → merge → Phase 2 → merge → Phase 3 → merge.
- **Phase 3 sub-PRs (optional, if PR grows too large):**
  - PR 3a: idempotency + Sentry + `/metrics` + `except:pass` fix
  - PR 3b: `event_log` migration + extended `ProgressReporter`
  - PR 3c: instrument platts/baltic cron scripts

### External prerequisites

- Create a Sentry free-tier project and set `SENTRY_DSN` in `.env` + Railway.
- Supabase admin access to apply the `event_log` migration.
- No changes to existing secrets.

### Effort estimate (focused work, not calendar)

- Phase 1: ~1 day (40 tests + fixtures)
- Phase 2: ~1 day (split + manual validation + inline imports cleanup)
- Phase 3: ~2–3 days (four sub-components + instrumenting 3 scripts)

**Total:** ~4–5 focused working days.

---

## 7. Out-of-Band Notes

### Related items NOT in this spec but worth tracking

From the `.planning/codebase/CONCERNS.md` audit (2026-04-17), the following are real issues but deferred:

- **Hardcoded `IRONMARKET_API_KEY`** in `execution/scripts/baltic_ingestion.py:36` — user deferred.
- **No CI test gate** in GitHub Actions — next candidate follow-up.
- **Mini App rate limiting / init_data freshness** — separate hardening project.
- **Supabase pagination** on `reports_show_month_list()` (`webhook/reports_nav.py`) — follow-up.
- **Google Sheets blocking calls** in async context (`_get_contacts_sync`) — could be replaced with Redis-cached contacts in a later project.

---

*Spec author: brainstorming session 2026-04-18*
*Next step: `/superpowers:writing-plans` to produce implementation plans per phase.*
