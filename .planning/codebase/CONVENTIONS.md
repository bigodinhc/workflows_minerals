# Coding Conventions

**Analysis Date:** 2026-04-22

Multi-language monorepo: Python (cron scripts + Telegram webhook), TypeScript/React (Next.js dashboard), JavaScript ESM (Apify actors). Conventions vary per subsystem but share a common observability/immutability discipline.

## Naming Patterns

**Python files (`execution/`, `webhook/`, `tests/`):**
- Modules: `snake_case.py` — `redis_client.py`, `queue_selection.py`, `state_store.py`, `event_bus.py`
- Tests: `test_<subject>.py` in `tests/` (flat, one file per module-under-test)
- Scripts (cron entry points): `execution/scripts/<workflow>.py` (e.g. `morning_check.py`, `baltic_ingestion.py`, `platts_reports.py`)

**Python identifiers:**
- Functions / variables: `snake_case` (`is_select_mode`, `record_failure`, `bulk_archive`)
- Constants: `UPPER_SNAKE_CASE`, prefixed `_` when module-private (`_TTL_SECONDS`, `_STAGING_TTL_SECONDS`, `_STREAK_THRESHOLD`, `_EVENTS_CHANNEL_DENYLIST`)
- Classes: `PascalCase` (`EventBus`, `PlattsClient`, `QueueBulkConfirm`)
- Private helpers: leading underscore (`_get_client`, `_mode_key`, `_now_iso`)

**JavaScript actors (`actors/*/src/**/*.js`):**
- Files: `camelCase.js` (`eventBus.js`, `main.js`) and lowercase for folders
- Tests: `<subject>.test.js` (e.g. `actors/platts-scrap-reports/tests/eventBus.test.js`)
- Identifiers: `camelCase` for vars/functions, `PascalCase` for classes (`EventBus`), `UPPER_SNAKE_CASE` for module constants
- Actor names (directory): `kebab-case` (`platts-scrap-reports`, `platts-news-only`)

**TypeScript / React (`dashboard/`):**
- Components: `PascalCase.tsx`
- Non-component modules: `camelCase.ts`
- Hooks: `useX` prefix
- App router conventions from Next.js 16 (see `dashboard/app/`)

## File Organization

**Many small focused files** (user global rule, enforced):
- Python modules typical 150-400 lines; hard ceiling ~800
- `webhook/bot/routers/` splits by domain: `callbacks_queue.py`, `callbacks_contacts.py`, `callbacks_curation.py`, `callbacks_menu.py`, `callbacks_reports.py`, `callbacks_workflows.py`, `commands.py`, `messages.py`, `onboarding.py`, `settings.py`
- `execution/` splits by role: `core/` (infra — EventBus, state_store, logger, delivery_reporter), `curation/` (Redis staging, routing, posting), `integrations/` (external SDK clients), `scripts/` (cron entry points), `agents/` (Claude-backed LLM calls)

**Feature-based, not type-based:** A feature lives across `execution/scripts/<name>.py` + `execution/integrations/<client>.py` + `tests/test_<name>.py` rather than a single "controllers/" or "services/" bucket.

## Module Boundaries

**Import rules:**
- `execution.core.*` — no dependencies on other `execution.*` subpackages; pure infra
- `execution.integrations.*` — wraps one external SDK per module, imported by `scripts/` and `curation/`
- `execution.curation.*` — may import `execution.integrations.*` and `execution.core.*`
- `execution.scripts.*` — top of the graph; imports everything else
- `webhook.bot.*` — aiogram-specific; imports `webhook.queue_selection`, `webhook.redis_queries`, `webhook.query_handlers`, and `execution.curation.*` for shared Redis keyspace
- Tests add both repo root and `webhook/` to `sys.path` in `tests/conftest.py` (mirrors the Railway Dockerfile which copies `webhook/` into `/app/`)

**Import ordering** (loose PEP 8, no `isort` enforced):
1. `from __future__ import annotations` when mixing `|` unions / forward refs on Py 3.9
2. Stdlib (`import asyncio`, `import os`, `from datetime import ...`)
3. Third-party (`import pytest`, `from aiogram import Router`)
4. First-party (`from execution.core.event_bus import ...`, `from bot.callback_data import ...`)

## Error Handling

**Structured `try/except` with narrow scope + logger:**
```python
try:
    item = curation_redis.get_staging(callback_data.item_id)
except Exception as exc:
    logger.error(f"queue_open redis error: {exc}")
    await query.answer("⚠️ Redis indisponível")
    return
```
See `webhook/bot/routers/callbacks_queue.py:91-111`.

**Never-raise on telemetry:** `EventBus` sinks (stdout, Supabase, Telegram) swallow failures and log to `logger.warning` — workflows must never be broken by observability (`execution/core/event_bus.py:1-11`).

**Graceful Redis degradation in bot handlers:** `_current_mode()` in `callbacks_queue.py:35-43` falls back to `('normal', set())` on any Redis error instead of 500ing.

**EventBus emits `cron_crashed` on top-level failures** — the `@with_event_bus` decorator in `execution/core/event_bus.py` catches, emits `cron_crashed` with `level="error"`, then re-raises.

**Bulk-op error-recovery pattern** (from `bulk_archive` + `test_callbacks_queue.py`, commits 7d5f337 / 9ca394c):
- Per-item `try/except continue` so one bad id cannot abort the batch
- Return `{"archived": [...], "failed": [...]}` with input ordering preserved
- Caller surfaces both counts in the Telegram toast with singular/plural agreement:
  `"✅ 2 arquivados, 1 falhou (expirado ou já removido)"` vs `"✅ 1 arquivado"`
- Implementation: `execution/curation/redis_client.py:120-139`
- Handler: `webhook/bot/routers/callbacks_queue.py:224-270`

**Aiogram `TelegramBadRequest`:** Always caught on `edit_message_text` (the common cause is "message is not modified" on rapid taps — safe to log+ignore). See `callbacks_queue.py:70-73, 219-221`.

## Logging & Observability

**Never use `print()` in production code.** Two channels:

1. **Structured events via `EventBus`** (`execution/core/event_bus.py`)
   - `bus.emit(event, label=..., detail={...}, level="info"|"warn"|"error")`
   - Fans out to stdout (one JSON line per emit), Supabase `event_log`, Sentry breadcrumbs, and `_MainChatSink` for warn/error
   - Reserved events: `cron_started`, `cron_crashed`, `cron_missed`, `step`, `api_call`
   - `watchdog` workflow is in `_EVENTS_CHANNEL_DENYLIST` (firehose suppression)
2. **Standard `logging.getLogger(__name__)`** for bot routers and helpers (`logger.error`, `logger.warning`) — flows to stderr/Sentry, not the event log

**Trace propagation:**
- `EventBus.trace_id` defaults to `run_id` (new root trace) or inherits from `TRACE_ID` env var (`event_bus.py:98`)
- `parent_run_id` inherits from `PARENT_RUN_ID` env
- Cron → Apify actor: scripts inject both into `run_input`:
  ```python
  if current is not None:
      run_input["trace_id"] = current.trace_id
      run_input["parent_run_id"] = current.run_id
  ```
  Covered by `tests/test_platts_ingestion_trace.py` and `tests/test_platts_reports_trace.py`
- Apify actor's JS `EventBus` mirrors the Python shape (`actors/platts-scrap-reports/src/lib/eventBus.js`) and echoes `apify_run_id` into every event `detail` so cross-process correlation works end-to-end

**Claim-ordering for idempotency** (commit 43aa332, spec `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md`):
- **Split-lock pattern:** `check_sent_flag()` -> validate data -> `try_claim_alert_key()` -> send -> `set_sent_flag()` / `release_inflight()`
- NEVER claim the 48h key before validation — early-exits (empty/incomplete data) would otherwise block retries for the whole day
- Regression guard: `tests/test_morning_check_idempotency.py`, `tests/test_baltic_ingestion_idempotency.py` spy on call order across every exit path

## Bot Callback Conventions (REQUIRED)

**Typed `CallbackData` factories** (after fix 7d40506) — all `webhook/bot/routers/*` callbacks go through `webhook/bot/callback_data.py`.

**Authoring a new callback:**
```python
# webhook/bot/callback_data.py
class QueuePage(CallbackData, prefix="queue_page"):
    page: int
```

**Serialising (keyboard builder):**
```python
button.callback_data = QueuePage(page=2).pack()  # -> "queue_page:2"
```

**Registering the handler (router):**
```python
@callbacks_queue_router.callback_query(QueuePage.filter())
async def on_queue_page(query: CallbackQuery, callback_data: QueuePage):
    chat_id = query.message.chat.id
    ...  # use callback_data.page directly — aiogram unpacked it
```

**Prefixes in use** (see full list in `webhook/bot/callback_data.py`):
`curate`, `draft`, `menu`, `rpt_type`, `rpt_year`, `rpt_month`, `report_dl`, `rpt_back`, `rpt_years`, `queue_page`, `queue_open`, `tgl`, `pg`, `wf_run`, `wf`, `user_approve`, `sub_toggle`, `sub_done`, `onboard`, `bcast`, `bulk`, `bulkok`, `bulkno`, `q_mode`, `q_sel`, `q_all`, `q_none`, `q_bulk`, `q_bulkok`, `q_bulkno`

**Never do** manual `callback_data.split(":", 1)` parsing in new code — migrate to a factory. Coverage in `tests/test_bot_callback_data.py` (pack -> unpack roundtrip for every factory).

## Queue Selection State

Convention for per-chat UI selection state (`webhook/queue_selection.py`):
- **Set-based selection** — `bot:queue_selected:{chat_id}` is a Redis SET (SADD / SREM / SMEMBERS), atomic toggle via `SADD` return value
- **Page-local toggle** — `get_page()` persists the user's current page; `on_queue_sel_toggle` must pass `page=get_page(chat_id)` to `format_queue_page`, NEVER reset to 1 (regression test `test_on_queue_sel_toggle_preserves_current_page`)
- **10-minute TTL** refreshed on every mutation via transactional pipelines
- **`enter_mode` / `exit_mode` bracket the session** — `exit_mode` deletes all three keys atomically
- **Per-chat isolation** — every helper takes `chat_id` as first arg, namespaced key
- **Order-determinism for bulk ops** — `sorted(selected)` before passing to `bulk_archive` so results are reproducible

## Immutability (User Global Rule)

**Never mutate inputs.** Always spread/replace:
```python
# WRONG
item["archivedBy"] = chat_id
return item

# CORRECT — prefer a new dict; if mutating a freshly-decoded Redis copy
# is truly local, document it.
return {**item, "archivedBy": chat_id, "archivedAt": _now_iso()}
```
Applies equally to JS/TS — spread syntax (`{...obj, key: val}`) over assignment.

## Configuration & Env

**Conventions:**
- `.env` (git-ignored) in repo root for local dev; `.env.example` is the checked-in template
- Never commit `credentials.json`, `token.json`, `.env`, `*.env`
- Access env via `os.getenv("VAR", default)` — fail loudly for load-bearing keys, soft-default for optional (Sentry, Telegram events channel)
- `REDIS_URL` is REQUIRED by `execution/curation/redis_client.py:45` (raises `RuntimeError` if unset) because losing a staged item would be worse than crashing
- `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` are the canonical Supabase creds; legacy `SUPABASE_KEY` is a fallback in `event_bus._get_supabase_client`
- GitHub Actions pass secrets via `secrets.*` and non-secret vars via `vars.*` (see `.github/workflows/morning_check.yml`)
- Two requirements files (MEMORY.md note): root `requirements.txt` powers GitHub Actions crons; `webhook/requirements.txt` powers the Railway Docker build — keep both in sync when adding a dep used by both sides

## Lint & Format

**Python:**
- No `ruff.toml` / `pyproject.toml` lint config, no `.pre-commit-config.yaml` — style is enforced by convention + review
- `webhook/pyproject.toml` only declares the project for Railway's setuptools build

**Actors (JS ESM):**
- ESLint via `@apify/eslint-config` composed with `eslint-config-prettier`
  - Config: `actors/platts-scrap-reports/eslint.config.mjs` (flat config)
  - Scripts: `npm run lint`, `npm run lint:fix`
- Prettier: `printWidth: 120, tabWidth: 4, singleQuote: true` (`.prettierrc` in each actor)
  - Scripts: `npm run format`, `npm run format:check`

**Dashboard (Next.js):**
- ESLint flat config at `dashboard/eslint.config.mjs` composing `eslint-config-next/core-web-vitals` + `eslint-config-next/typescript`
- Scripts: `npm run lint`
- No Prettier config — formatting relies on editor/ESLint defaults

## Function & Module Design

- **Small functions** (<50 lines); extract helpers (`_confirm_markup`, `_current_mode`, `_rerender`) when a handler grows
- **Single responsibility per module** — `redis_client.py` owns Redis keys, `queue_selection.py` owns select-mode state, `callbacks_queue.py` owns routing/rendering
- **Factory over inheritance** — `CallbackData` subclasses, `_build_telegram_client` factory so tests can monkeypatch
- **`from __future__ import annotations`** on all new Python modules to unlock `|` unions on Py 3.9 (Railway pins 3.9)

## Git Workflow

**Conventional commits** (user global rule, consistently followed — see `git log`):
- `feat(scope):`, `fix(scope):`, `refactor(scope):`, `chore(scope):`, `docs(scope):`, `test(scope):`, `perf(scope):`, `ci(scope):`
- Scopes are subsystem names: `queue`, `bot`, `curation`, `morning_check`, `baltic`, `event_bus`, `state_store`, `tail`, `contacts`
- Examples: `feat(queue): add bulk prompt/confirm/cancel handlers`, `fix(morning_check): split-lock idempotency`, `refactor(queue): bulk-op error recovery + singular/plural polish`
- Attribution disabled globally via `~/.claude/settings.json` per user rules

## Supabase Migrations

- Location: `supabase/migrations/`
- Naming: `YYYYMMDD_<purpose>.sql` (e.g. `20260418_event_log.sql`, `20260419_event_log_rls.sql`, `20260422_contacts.sql`)
- `supabase/.temp/` is git-ignored (CLI cache)

## Aiogram Conventions

- **`parse_mode=ParseMode.MARKDOWN`** is the bot default (`webhook/bot/config.py:47`)
- **`parse_mode=None`** is REQUIRED for any message containing raw workflow names (underscores), user-provided text, or JSON blobs — see `webhook/bot/routers/commands.py` `/tail` handler (fix commit a714646). Rule: if you can't guarantee Markdown-safety, opt out.
- **FSM isolation**: per-user state; tests in `tests/test_messages_fsm_isolation.py` verify handlers do not leak state across users
- **Middlewares** live in `webhook/bot/middlewares/`; `RoleMiddleware(allowed_roles={"admin"})` gates admin-only callback routers

## Docs-as-Code

Active spec/plan/followup structure under `docs/superpowers/`:
- `specs/YYYY-MM-DD-<feature>-design.md` — design doc authored BEFORE implementation
- `plans/YYYY-MM-DD-<feature>-plan.md` — task breakdown consumed by executor
- `followups/YYYY-MM-DD-<feature>-followups.md` — post-implementation loose ends

Every non-trivial change lands a spec + plan (see commits `5c2329b docs(plan): /queue bulk actions`, `27c268f docs(spec): /queue bulk archive+discard design`, `60ae15e docs: document split-lock idempotency convention`). This convention is load-bearing for future Claude sessions — consult `docs/superpowers/specs/` before redesigning a subsystem.

## Code Quality Checklist (per user global rules)

Before marking work complete:
- [ ] Immutable patterns (no mutation)
- [ ] Functions <50 lines, files <800 lines
- [ ] `logger.error` / EventBus instead of `print` / `console.log`
- [ ] No hardcoded secrets, all env via `os.getenv`
- [ ] Input validation on external boundaries
- [ ] Comprehensive `try/except` with user-friendly messages
- [ ] 80%+ test coverage target (see `TESTING.md`)

---

*Convention analysis: 2026-04-22*
