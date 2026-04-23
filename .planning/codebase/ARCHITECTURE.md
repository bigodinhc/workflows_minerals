# Architecture

**Analysis Date:** 2026-04-22

## Pattern Overview

**Overall:** Multi-subsystem, event-driven automation platform joined through a shared observability spine, a Supabase Postgres/Storage backend, and a Redis runtime state layer.

The system is a polyrepo-in-a-monorepo: five independently deployed subsystems cooperate by writing to and reading from shared infrastructure (Supabase Postgres + Storage, Redis, Telegram channels, GitHub Actions). The author's own framing in `AGENT.md` describes this as a "3-layer architecture" — directive (`directives/`) → orchestration (LLM) → execution (`execution/`) — designed to push determinism into Python scripts while LLMs stay in the routing layer. On top of that 3-layer foundation, five deployed subsystems are coordinated by two cross-cutting backbones:

1. **Observability spine.** `execution.core.event_bus.EventBus` (Python) and `actors/*/src/lib/eventBus.js` (Node) fan out structured events to stdout, Supabase `event_log`, Sentry breadcrumbs, and two Telegram channels. Every event carries `workflow`, `run_id`, `trace_id`, `parent_run_id`.
2. **State-store spine.** Redis holds runtime state: workflow outcomes + streaks (`execution/core/state_store.py`), curation staging/archive + dedup + bot selection state (`execution/curation/redis_client.py` + `webhook/queue_selection.py`). Redis is now a **required** runtime dependency for the webhook and curation pipelines; it remains optional for cron scripts (their `state_store` silently no-ops when `REDIS_URL` is unset).

**Key Characteristics:**

- **Event-driven crons.** Every scheduled Python entry point is wrapped by `@with_event_bus(<workflow>)` (`execution/core/event_bus.py:335`), which emits `cron_started` / `cron_finished` / `cron_crashed` and records crashes to `state_store` for the watchdog.
- **Serverless scraping.** Expensive browser-bound scraping lives in Apify Node actors (`actors/`), invoked synchronously from cron scripts via `execution/integrations/apify_client.py`. Actors emit their own cron-lifecycle events to `event_log` using a sibling JS `EventBus` that mirrors the Python contract.
- **Trace propagation (Phase 4).** `execution/scripts/platts_ingestion.py` and `platts_reports.py` forward `trace_id` + `parent_run_id` from the Python EventBus into the Apify `run_input`; the actor's JS EventBus (`actors/platts-scrap-full-news/src/lib/eventBus.js`) carries them through every event it emits. A single `trace_id` therefore spans cron start → actor start → actor events → cron completion.
- **Split-lock idempotency for daily reports.** `morning_check.py` and `baltic_ingestion.py` use a two-key Redis pattern — `daily_report:inflight:{TYPE}:{date}` (20 min TTL, concurrency guard) + `daily_report:sent:{TYPE}:{date}` (48 h TTL, success marker). Early-exits before side effects leave both keys untouched so the next cron retries cleanly. See `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md`.
- **Stateless-at-rest subsystems.** Bot (Railway), actors (Apify), dashboard (Vercel) and crons (GH Actions) are all stateless. Durable state = Supabase Postgres + Storage; runtime state = Redis.
- **Shared Python package.** `execution/` is installed as a source-level package in both GH Actions runs (via root `requirements.txt`) and in the webhook Docker image (`Dockerfile:17` copies `execution/` into `/app/execution/`). Any `from execution.core import …` works in both deployments.
- **Immutability + repository pattern.** `ContactsRepo` (`execution/integrations/contacts_repo.py`) is the single source of truth for the WhatsApp broadcast list. Handlers and staging helpers return new dicts rather than mutating inputs.
- **parse_mode=None on bot edits.** The Telegram events-channel sink and `/tail`, `/status`, `/queue` handlers explicitly disable Markdown parsing to avoid underscores in workflow names tripping entity-parser errors.

## Subsystems

### 1. `execution/` — Cron Scripts + Python Library

Deterministic Python layer invoked by GitHub Actions and (indirectly) by the webhook. Two roles in one package.

**As a CLI layer (`execution/scripts/*.py`):**
- `morning_check.py` — Platts iron ore price snapshot → WhatsApp (08:30–10:00 BRT, `.github/workflows/morning_check.yml`). Split-lock idempotent.
- `baltic_ingestion.py` — Outlook Graph API → PDF → Claude extraction → IronMarket POST → WhatsApp (every 15 min, weekdays 09:00–11:45 BRT, `.github/workflows/baltic_ingestion.yml`). Split-lock idempotent.
- `send_daily_report.py` — LSEG SGX futures → WhatsApp (`.github/workflows/daily_report.yml`).
- `platts_ingestion.py` — Runs `platts-scrap-full-news` actor, dedups, routes items (3×/day, `.github/workflows/market_news.yml`).
- `platts_reports.py` — Runs `platts-scrap-reports` actor, stores PDFs in Supabase Storage (daily, `.github/workflows/platts_reports.yml`).
- `send_news.py` — Manual news dispatch (triggered from the bot via `webhook/workflow_trigger.py`).
- `watchdog_cron.py` — Every 5 min, compares `cron_parser` expectations against `state_store.get_status` and emits `cron_missed` (`.github/workflows/watchdog.yml`).
- `rebuild_dedup.py`, `manual_ingestion_json.py`, `inspect_platts.py`, `debug_apify.py` — operational utilities.

**As a shared library (imported by `webhook/`):**
- `execution.core.event_bus` — `EventBus`, `@with_event_bus`, stdout/Supabase/Sentry/main-chat/events-channel sinks, `get_current_bus()`.
- `execution.core.state_store` — Redis workflow-outcome store + alert-claim helpers (see §3).
- `execution.core.delivery_reporter` — WhatsApp send-result tracking + categorized error reporting with circuit breaker.
- `execution.core.progress_reporter` — Telegram live progress cards (one message edited throughout the run).
- `execution.core.cron_parser` — Reads `.github/workflows/*.yml` to compute expected run times for the watchdog.
- `execution.integrations.contacts_repo` — Supabase `contacts` table repository.
- `execution.integrations.{apify,claude,lseg,platts,supabase,telegram,uazapi,baltic}_client` — External API adapters.
- `execution.curation.{router,redis_client,id_gen,rationale_dispatcher,telegram_poster}` — Scraped-item classification and staging.
- `execution.agents.rationale_agent` — Anthropic Claude prompt runner.
- `execution.core.prompts.{writer,critique,adjuster,curator}` — Prompt templates.

### 2. `actors/` — Apify (Node.js) Scrapers

Each actor is an isolated Node 20 + Playwright package with its own `package.json`, `Dockerfile`, and `.actor/` manifest.

- `actors/platts-scrap-reports/` — Logs into Platts, navigates the reports grid, downloads PDFs, uploads to Supabase Storage, stores row metadata.
- `actors/platts-scrap-full-news/` — RMW, All Insights, Iron Ore Topic news scraping (reading pane + article fetch + image + tables).
- `actors/platts-scrap-price/`, `actors/platts-news-only/` — Legacy / supplementary actors.

Apify isolation prevents symlinking, so the JS `EventBus` is copy-pasted between `actors/platts-scrap-reports/src/lib/eventBus.js` and `actors/platts-scrap-full-news/src/lib/eventBus.js` — the header comment mandates both copies be updated in the same PR.

### 3. Curation / State-Store Layer (new)

Two Python modules coordinate durable curation state in Redis, plus a sibling bot-selection module in `webhook/`:

- **`execution/core/state_store.py`** — Workflow-outcome persistence for the watchdog/status UI.
  - Functions: `record_success`, `record_failure`, `record_empty`, `record_crash`, `get_status`, `get_all_status`, `try_claim_alert_key`, `check_sent_flag`, `set_sent_flag`, `release_inflight`.
  - Keys: `wf:last_run:<workflow>` (JSON), `wf:streak:<workflow>` (int), `wf:failures:<workflow>` (list, trimmed to 3), `wf:crash_dedup:<workflow>` (5 min NX), `daily_report:inflight:{TYPE}:{date}` (20 min), `daily_report:sent:{TYPE}:{date}` (48 h).
  - Non-raising contract. If `REDIS_URL` is unset or Redis unreachable, writes no-op and reads return None. Workflows are never broken by this module.
  - Auto-tags payloads with the active EventBus `run_id` via `_current_run_id()` for `/tail` correlation.

- **`execution/curation/redis_client.py`** — Curation keyspace.
  - Keys: `platts:staging:<id>` (JSON, 48 h), `platts:archive:<date>:<id>` (JSON, no TTL — consumed by a downstream project), `platts:seen` (sorted set, 30 d rolling), `platts:scraped:<date>` (set, 30 d), `platts:rationale:processed:<date>` (flag, 30 h).
  - Functions: `set_staging`, `get_staging`, `archive`, `discard`, `bulk_archive`, `bulk_discard`, `is_seen`, `mark_seen`, `staging_exists`, `mark_scraped`, `is_rationale_processed`, `set_rationale_processed`.
  - Raises on missing `REDIS_URL` — curation state is load-bearing; silent data loss is worse than crashing the ingestion run (contrast with `state_store`).
  - `archive` uses a Redis pipeline transaction so SET + DELETE cannot half-apply.
  - `bulk_archive` loops `archive()` per item independently — one expired or errored item does not abort the batch.

- **`webhook/queue_selection.py`** — Per-chat select-mode state for `/queue` bulk actions. Reuses `execution.curation.redis_client._get_client()`.
  - Keys: `bot:queue_mode:{chat_id}` (string `"select"`), `bot:queue_selected:{chat_id}` (set of item ids), `bot:queue_page:{chat_id}` (int).
  - All three share a 10 min TTL, refreshed on every mutation. `exit_mode` deletes all three.
  - Volatile by design — bot restarts discard it.

- **`webhook/redis_queries.py`** — Read-side feedback + pipeline keyspace.
  - Keys: `platts:pipeline:processed:<date>` (set, 2 d), `webhook:feedback:<ts>-<id>` (hash, 30 d), `webhook:feedback:index` (sorted set, 30 d).

### 4. `webhook/` — Aiogram Telegram Bot (Railway)

Long-running aiohttp app (`webhook/bot/main.py`) that:
- Hosts the Telegram webhook at `/webhook` via `SimpleRequestHandler`.
- Mounts aiohttp routes for GitHub Actions callbacks (`routes/api.py`), the draft-preview HTML (`routes/preview.py`), and the Telegram Mini-App API (`routes/mini_api.py` + `routes/mini_static.py`).
- Uses `RedisStorage` for FSM (contact add, reject-reason, news-draft flows).
- Imports the shared `execution.*` library at runtime; `Dockerfile` copies both `webhook/` and `execution/` into the image.

Handlers are organized into aiogram v3 `Router`s (§ Bot architecture below).

### 5. `dashboard/` — Next.js 16 App Router (Vercel)

`dashboard/app/` pages with `"use client"` + SWR for live workflow status:
- `page.tsx` — Workflow run list and trigger buttons.
- `contacts/page.tsx` — Supabase `contacts` table.
- `executions/page.tsx`, `news/page.tsx`, `workflows/page.tsx`.
- `components/` — Radix/shadcn UI primitives + `DeliveryReportView`.
- API routes under `app/api/` (workflows, contacts, logs) proxy to GH Actions + Supabase.

### 6. `supabase/` — Migrations + Edge Functions

- `supabase/migrations/20260418_event_log.sql` — `event_log(id, workflow, run_id, draft_id, level, label NOT NULL, detail, context jsonb, created_at)`.
- `supabase/migrations/20260419_event_log_rls.sql` — RLS; service-role writes, no anon reads.
- `supabase/migrations/20260422_contacts.sql` — Migration from Google Sheets → Supabase `contacts` table with `phone_uazapi` unique index + `status` check.

## State-Store Architecture

Two orthogonal Redis backends, both keyed by `REDIS_URL`:

**`execution.core.state_store` (observability store)**
- Consumers: `watchdog_cron.py`, `/status` and `/tail` bot commands, `@with_event_bus` crash path, `morning_check.py`, `baltic_ingestion.py` (split-lock).
- Contract: **non-raising**. Caller never handles exceptions.
- Circular-import guard: lazy-imports `event_bus.get_current_bus` inside `_current_run_id()` — `state_store` and `event_bus` reference each other at runtime only.
- Crash dedup: `record_crash` uses `SET NX EX 300s` on `wf:crash_dedup:<workflow>` so the same exception observed by both `@with_event_bus` and `progress_reporter.fail()` only increments the streak once.

**`execution.curation.redis_client` (curation store)**
- Consumers: `platts_ingestion.py`, `platts_reports.py`, `webhook/bot/routers/callbacks_queue.py`, `webhook/bot/routers/callbacks_curation.py`, `webhook/queue_selection.py`, `webhook/redis_queries.py`.
- Contract: **raising**. Missing REDIS_URL = hard failure. Data loss here means losing a staged item silently, which is worse than aborting the ingestion.

## Queue Bulk-Actions Flow (new)

Entry is `/queue` (`webhook/bot/routers/commands.py`) → `query_handlers.format_queue_page()` renders either the normal page or the select-mode page based on `queue_selection.is_select_mode(chat_id)`.

### ASCII state machine

```
    ┌──────────────────────────────────────────────────────────┐
    │                   /queue (chat command)                  │
    └────────────────────────────┬─────────────────────────────┘
                                 ▼
                      ┌─────────────────────┐
                      │     NORMAL MODE     │
                      │  (no Redis state)   │
                      │  ☑️ Modo seleção    │
                      │  [ item button ]×N  │◀───┐
                      │  ⬅ prev  next ➡    │     │
                      └──┬─────────────┬────┘     │
            queue_open:<id>           q_mode:enter│
                         │             │          │
                         ▼             ▼          │
                  curation card    enter_mode()   │
                                     writes       │
                                   bot:queue_*    │
                                     keys         │
                                       │          │
                                       ▼          │
                      ┌─────────────────────┐     │
                      │    SELECT MODE      │     │
                      │  N selected of M    │     │
                      │  ☐/☑️ item × N      │     │
                      │  ✅ Todos ❌ Nenhum │     │
                      │  📦 Arquivar N      │     │
                      │  🗑️ Descartar N     │     │
                      │  🔙 Sair            │     │
                      │  ⬅ prev  next ➡    │     │
                      └──┬───┬────────────┬─┘     │
                         │   │            │       │
             q_sel:<id>  │   │ q_all/     │q_mode:│
          toggle+rerender│   │ q_none     │exit   │
                         ▼   ▼            ▼       │
                   (stay in select mode)  exit_mode()
                         │                        │
                 q_bulk:archive                   │
                 q_bulk:discard                   │
                         │                        │
                         ▼                        │
                ┌─────────────────┐               │
                │ CONFIRM PROMPT  │               │
                │ Arquivar N?     │               │
                │ ✅ Sim ❌ Cancel│               │
                └──┬───────────┬──┘               │
                   │           │                  │
              q_bulkok     q_bulkno               │
                   │           │                  │
                   ▼           ▼                  │
            bulk_archive/   rerender              │
            bulk_discard    (stay select)─────────┤
                   │                              │
                   ▼                              │
            toast + exit_mode + rerender page 1 ──┘
```

### Callback → router mapping

All handlers in `webhook/bot/routers/callbacks_queue.py`, all gated by `RoleMiddleware(allowed_roles={"admin"})`:

| CallbackData (`webhook/bot/callback_data.py`) | Handler | Effect |
|---|---|---|
| `QueuePage(page=int)` | `on_queue_page` | Remembers page in select mode; re-renders |
| `QueueOpen(item_id=str)` | `on_queue_open` | Opens curation card via `telegram_poster.post_for_curation` |
| `QueueModeToggle(action='enter'|'exit')` | `on_queue_mode` | `queue_selection.enter_mode` / `exit_mode`; re-render page 1 |
| `QueueSelToggle(item_id=str)` | `on_queue_sel_toggle` | `queue_selection.toggle` (SADD/SREM atomic) |
| `QueueSelAll` | `on_queue_sel_all` | `select_all` with all 200 staging ids |
| `QueueSelNone` | `on_queue_sel_none` | `clear` (keeps mode key) |
| `QueueBulkPrompt(action='archive'|'discard')` | `on_queue_bulk_prompt` | Edits message to confirm prompt |
| `QueueBulkConfirm(action=...)` | `on_queue_bulk_confirm` | `bulk_archive` / `bulk_discard` via `asyncio.to_thread`; toast; auto-exit |
| `QueueBulkCancel` | `on_queue_bulk_cancel` | Re-render select mode (selection preserved) |

`_rerender()` always uses `edit_message_text` and swallows `TelegramBadRequest "message is not modified"` as a no-op.

## Observability Spine

### EventBus (Python: `execution/core/event_bus.py`)

Sinks, all never-raise:
- `_StdoutSink` — JSON per line to stdout (surfaces in GH Actions logs).
- `_SupabaseSink` — inserts rows into `event_log` via service-role key.
- `_SentrySink` — adds a breadcrumb per event; capture_exception on crash.
- `_MainChatSink` — sends a fresh Telegram message for `warn`/`error`/`cron_crashed`/`cron_missed` to the operator chat.
- `_EventsChannelSink` — one live card per run (send + subsequent edits) to the events channel. Skipped for `watchdog` (denylist in `_EVENTS_CHANNEL_DENYLIST`).

### EventBus (Node: `actors/*/src/lib/eventBus.js`)

Mirrors the Python contract. Emits to stdout + Supabase. Accepts `traceId` and `parentRunId` via actor `run_input`.

### Event schema (stored in `supabase/migrations/20260418_event_log.sql`)

```
{ ts, workflow, run_id, trace_id, parent_run_id, level, event, label, detail }
```

Event name taxonomy: `cron_started`, `cron_finished`, `cron_crashed`, `cron_missed`, `step`, `api_call`, plus custom labels. `label` is NOT NULL in Postgres; `event_bus.py` falls back to the event name when no explicit label is passed so lifecycle events persist (fix in the 2026-04-22 idempotency spec).

### Claim-ordering overhaul (2026-04-22)

Pre-fix, `morning_check` and `baltic_ingestion` claimed a single 48 h key before fetching data — any early-exit held the key and blocked retries for the rest of the day. Post-fix, both scripts use the split-lock pattern above; see `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md` for the timeline, rationale, and regression tests (`tests/test_morning_check_idempotency.py`, `tests/test_baltic_ingestion_idempotency.py`).

## Data Flow

```
┌──────────────┐  cron  ┌────────────┐  run_input{trace_id}  ┌──────────┐
│ GH Actions   │───────▶│ execution/ │──────────────────────▶│ Apify    │
│ (YAML cron)  │        │ scripts/   │                       │ actor JS │
└──────────────┘        └──────┬─────┘                       └────┬─────┘
                               │                                  │
                     ┌─────────┴──────────────┐                   │
                     ▼                        ▼                   ▼
              ┌──────────────┐         ┌──────────────┐   ┌──────────────┐
              │ Supabase     │◀────────│ Redis        │   │ Supabase     │
              │ event_log    │         │ staging+seen │   │ Storage      │
              │ contacts     │         │ wf:last_run  │   │ (PDFs)       │
              └──────┬───────┘         └──────┬───────┘   └──────────────┘
                     │                        │
                     │          ┌─────────────┴────────────┐
                     ▼          ▼                          ▼
              ┌──────────────────────┐            ┌──────────────────┐
              │ webhook/ (Railway)   │            │ dashboard/       │
              │ aiogram bot + Mini   │            │ (Vercel)         │
              │ App + aiohttp API    │            │ SWR + API routes │
              └────────┬─────────────┘            └──────────────────┘
                       │
                       ▼
              ┌──────────────────┐
              │ uazapi → WhatsApp│
              │ Telegram         │
              └──────────────────┘
```

## Delivery / Reporting Flow with Idempotency

`morning_check.py` and `baltic_ingestion.py` run Phase 0–5 of the split-lock pattern:

```
PHASE 0  check_sent_flag(sent_key)      → if set: exit, no side effect
PHASE 1  fetch source (Platts / Graph email)
PHASE 2  validate (non-empty, today's date, ≥ MIN_ITEMS)
             early-exit → no lock held, next cron retries
PHASE 3  try_claim_alert_key(inflight_key, 20min)
             if False: another run active, exit clean
PHASE 4  try:
           4a format message, DeliveryReporter.dispatch() → uazapi
           4b (baltic) IronMarket POST before WhatsApp (idempotent by variable_key)
           4c set_sent_flag(sent_key, 48h)   ← only on full success
         finally:
           release_inflight(inflight_key)
```

`DeliveryReporter` (`execution/core/delivery_reporter.py`) categorizes per-contact failures (`WHATSAPP_DISCONNECTED`, `RATE_LIMIT`, etc.), triggers a circuit breaker on 5 consecutive fatal failures in the same category, and emits a grouped summary to the Telegram main chat plus `event_log` via the surrounding EventBus. `ProgressReporter` renders a live card edited through the run.

## Bot Architecture

**Entry point:** `webhook/bot/main.py:main` — aiohttp app with `SimpleRequestHandler(dispatcher=dp, bot=bot)` registered at `/webhook`. Runs `python -m webhook.bot.main` (`railway.json`).

**Router registration (order matters — curation-specific filters first):**

```
onboarding_router         # /start, approval, subscription (public)
public_router             # other public commands
admin_router              # admin-only commands (RoleMiddleware)
shared_router             # /settings, /menu (admin + subscriber)
callbacks_curation_router # draft/curate/broadcast (specific filters first)
callbacks_reports_router  # report nav
callbacks_queue_router    # queue nav + bulk actions (new)
callbacks_menu_router     # main-menu switchboard
callbacks_contacts_router # contact admin
callbacks_workflows_router# workflow trigger + no-op
reply_kb_router           # reply keyboard text
message_router            # FSM + catch-all text
```

**Typed CallbackData** (`webhook/bot/callback_data.py`) replaces `callback_data.split(':', 1)` with aiogram v3 `CallbackData` factories — each class has a 2–8-char `prefix` (Telegram 64-byte payload limit) and declared field types. Seven new classes added for bulk actions: `QueueModeToggle`, `QueueSelToggle`, `QueueSelAll`, `QueueSelNone`, `QueueBulkPrompt`, `QueueBulkConfirm`, `QueueBulkCancel`.

**Query formatters** (`webhook/query_handlers.py`) are pure functions that consume `webhook.redis_queries` and return `(text, reply_markup_dict)`. They know nothing about aiohttp or Telegram transport. `format_queue_page(page, mode, selected)` branches on `mode` (`"normal"` vs `"select"`) to produce the correct keyboard. Callers in `callbacks_queue.py` pass the markup straight to `edit_message_text`.

**FSM storage:** `RedisStorage` (`webhook/bot/config.py:38`) persists states (`AddContact`, `NewsInput`, `AdjustDraft`, `RejectReason`, `BroadcastConfirm`) across bot restarts. FSM isolation per chat is covered by `tests/test_messages_fsm_isolation.py`.

## Dashboard Architecture

Next.js 16 App Router (`dashboard/app/`):
- `page.tsx` — workflow status + trigger (`/api/workflows` POST).
- `app/contacts/page.tsx`, `executions/page.tsx`, `news/page.tsx`, `workflows/page.tsx`.
- `components/` — shadcn-style Radix primitives (`button`, `card`, `table`, `sheet`, `dropdown-menu`, …) + `DeliveryReportView`.
- `lib/utils.ts` — `cn()` class merger.
- Data fetching: SWR (`useSWR("/api/…")`) with `refreshInterval: 10000` for workflow list.
- API routes (not shown in listing) proxy to GitHub Actions via Octokit and to Supabase via `@supabase/supabase-js`.

Styling: Tailwind v4 + `tw-animate-css` + custom terminal-green palette.

## Cross-Cutting Concerns

- **Repository pattern.** `ContactsRepo` (`execution/integrations/contacts_repo.py`) is the only read path for WhatsApp contacts. Tests in `tests/test_contacts_repo.py`, `test_contacts_repo_normalize.py`, `test_contacts_bulk_ops.py`.
- **Immutability.** `state_store._attach_run_id` returns a new dict rather than mutating (`state_store.py:61`). `redis_client.set_staging` copies the input before adding `stagedAt`.
- **parse_mode=None.** The events-channel sink, `/tail`, `/status`, `/queue` all pass `parse_mode=None` to avoid Markdown V1 entity errors on workflow names containing underscores.
- **Structured events everywhere.** Every cron emits `cron_started`/`cron_finished`/`cron_crashed`; every API call worth timing emits `api_call` with `duration_ms`.
- **Non-raising telemetry.** `EventBus`, `ProgressReporter`, `state_store` all swallow sink/Redis failures — workflows are never broken by observability.
- **Idempotency documented.** The split-lock pattern is the reference for any future daily-report workflow; see CONVENTIONS.md and `2026-04-22-idempotency-claim-ordering-fix-design.md`.

## Deployment Topology

| Subsystem | Host | Trigger | Runtime |
|---|---|---|---|
| `execution/scripts/*` | GitHub Actions | `.github/workflows/*.yml` cron + `workflow_dispatch` | Python 3.10/3.11 |
| `webhook/` | Railway | `Dockerfile` (Node-stage builds Mini-App, Python-stage runs bot); `railway.json` startCommand `python -m webhook.bot.main` | Python 3.11 + Node 20 build |
| `actors/platts-scrap-*` | Apify | invoked via `execution.integrations.apify_client` | Node 20 + Playwright |
| `dashboard/` | Vercel (inferred — `@vercel/…` SVG + DASHBOARD_BASE_URL default `workflows-minerals.vercel.app`) | on push | Next.js 16 / Node 20 |
| `supabase/` | Supabase Cloud | migrations applied manually per `supabase/migrations/README.md` | Postgres + Storage |
| Redis | Railway (shared with bot) | always-on | Redis 5.x-compatible |

**Secrets required:** `REDIS_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_EVENTS_CHANNEL_ID`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `UAZAPI_TOKEN`, `UAZAPI_URL`, `ANTHROPIC_API_KEY`, `AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET/TARGET_MAILBOX`, `IRONMARKET_API_KEY`, `SENTRY_DSN`, `APIFY_TOKEN`. Set per-workflow in `.github/workflows/*.yml` and on Railway.

---

*Architecture analysis: 2026-04-22*
