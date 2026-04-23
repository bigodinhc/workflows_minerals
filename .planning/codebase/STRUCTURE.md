# Codebase Structure

**Analysis Date:** 2026-04-22

## Directory Layout

```
agentics_workflows/
├── AGENT.md                  # Mirrored CLAUDE.md/AGENTS.md/GEMINI.md — author's 3-layer arch note
├── Dockerfile                # Multi-stage: Node-stage builds Mini-App, Python-stage runs bot
├── railway.json              # Railway deploy config (startCommand: python -m webhook.bot.main)
├── requirements.txt          # Root Python deps — used by GH Actions runs
├── package.json              # Root Node deps (apify-client for actor invocation)
├── pytest.ini                # pytest discovery + asyncio mode
│
├── .github/
│   └── workflows/            # GH Actions cron + workflow_dispatch YAML
│       ├── baltic_ingestion.yml
│       ├── daily_report.yml
│       ├── market_news.yml
│       ├── morning_check.yml
│       ├── platts_reports.yml
│       └── watchdog.yml
│
├── execution/                # Python library + cron scripts (Layer 3)
│   ├── __init__.py
│   ├── agents/               # Claude-backed prompt runners
│   │   └── rationale_agent.py
│   ├── core/                 # Shared primitives
│   │   ├── __init__.py
│   │   ├── agents_progress.py
│   │   ├── cron_parser.py          # Reads .github/workflows/*.yml for watchdog
│   │   ├── delivery_reporter.py    # WhatsApp send tracking + circuit breaker
│   │   ├── event_bus.py            # EventBus + @with_event_bus + sinks
│   │   ├── logger.py               # WorkflowLogger
│   │   ├── progress_reporter.py    # Live Telegram progress card
│   │   ├── prompts/
│   │   │   ├── adjuster.py
│   │   │   ├── critique.py
│   │   │   ├── curator.py
│   │   │   └── writer.py
│   │   ├── retry.py                # retry_with_backoff
│   │   ├── runner.py               # Sub-workflow runner (directive chaining)
│   │   ├── sentry_init.py          # init_sentry(workflow)
│   │   ├── state.py                # File-backed StateManager
│   │   └── state_store.py          # Redis runtime state (new helpers for split-lock)
│   ├── curation/             # Platts item classification + Redis keyspace (new layer)
│   │   ├── __init__.py
│   │   ├── id_gen.py               # generate_id() — stable content hash
│   │   ├── rationale_dispatcher.py # Stages rationale items for AI drafting
│   │   ├── redis_client.py         # Curation Redis client: staging/archive/seen/bulk ops
│   │   ├── router.py               # classify() + route_items()
│   │   └── telegram_poster.py      # post_for_curation card + _escape_md
│   ├── integrations/         # External API clients (repository pattern)
│   │   ├── apify_client.py         # Run actor + forward trace_id
│   │   ├── baltic_client.py        # Outlook Graph API
│   │   ├── claude_client.py        # Anthropic PDF extraction
│   │   ├── contacts_repo.py        # Supabase contacts table
│   │   ├── lseg_client.py          # SGX futures
│   │   ├── platts_client.py        # S&P Global Commodity Insights
│   │   ├── supabase_client.py
│   │   ├── telegram_client.py
│   │   └── uazapi_client.py        # WhatsApp gateway
│   ├── scripts/              # Cron entry points (CLI layer)
│   │   ├── baltic_ingestion.py     # Split-lock idempotent (new)
│   │   ├── debug_apify.py
│   │   ├── inspect_platts.py
│   │   ├── manual_ingestion_json.py
│   │   ├── morning_check.py        # Split-lock idempotent (new)
│   │   ├── platts_ingestion.py
│   │   ├── platts_reports.py
│   │   ├── rebuild_dedup.py
│   │   ├── send_daily_report.py
│   │   ├── send_news.py
│   │   └── watchdog_cron.py
│   └── supabase/             # Legacy migrations dir (stub — new migrations live under supabase/)
│
├── webhook/                  # aiogram v3 Telegram bot + Mini-App server (Railway)
│   ├── requirements.txt      # Webhook-specific deps (used by Railway Docker build)
│   ├── pyproject.toml
│   ├── bot/                  # aiogram bot package (routers + middlewares + helpers)
│   │   ├── __init__.py
│   │   ├── callback_data.py        # Typed CallbackData factories (new: 7 Queue* classes)
│   │   ├── config.py               # Env vars, Bot/Dispatcher/RedisStorage singletons
│   │   ├── delivery.py             # Bot-side delivery helpers
│   │   ├── keyboards.py            # build_main_menu_keyboard, ...
│   │   ├── main.py                 # aiohttp + Aiogram webhook entry point
│   │   ├── middlewares/
│   │   │   ├── __init__.py
│   │   │   └── auth.py             # RoleMiddleware (admin/subscriber gating)
│   │   ├── routers/                # aiogram Router modules (registered in main.py)
│   │   │   ├── __init__.py
│   │   │   ├── _helpers.py         # drafts_*, run_pipeline_and_archive
│   │   │   ├── callbacks_contacts.py
│   │   │   ├── callbacks_curation.py
│   │   │   ├── callbacks_menu.py
│   │   │   ├── callbacks_queue.py  # /queue nav + bulk actions (new, 9 handlers)
│   │   │   ├── callbacks_reports.py
│   │   │   ├── callbacks_workflows.py
│   │   │   ├── commands.py         # /status, /tail, /queue, /history, /stats, /help
│   │   │   ├── messages.py         # FSM + catch-all text
│   │   │   ├── onboarding.py       # /start + approval + subscription
│   │   │   └── settings.py         # /settings
│   │   ├── states.py               # FSM: AddContact, NewsInput, AdjustDraft, RejectReason
│   │   └── users.py                # User registry helpers
│   ├── contact_admin.py      # Contacts bulk-op logic (consumed by bot + routes)
│   ├── digest.py             # Markdown digest builder (news/rationale)
│   ├── dispatch.py           # Draft approval + test-send pipeline
│   ├── metrics.py            # prometheus_client counters
│   ├── pipeline.py           # Draft → WhatsApp broadcast pipeline
│   ├── query_handlers.py     # /queue, /history, /stats formatters (new: select-mode branch)
│   ├── queue_selection.py    # Per-chat select-mode Redis state (new)
│   ├── redis_queries.py      # Read-side curation queries + feedback keyspace
│   ├── reports_nav.py        # /reports navigation
│   ├── status_builder.py     # /status message builder + ALL_WORKFLOWS
│   ├── workflow_trigger.py   # GH Actions dispatch_workflow helper
│   ├── routes/               # aiohttp HTTP routes (non-Telegram)
│   │   ├── __init__.py
│   │   ├── api.py                  # /store-draft, /seen-articles, /health, /metrics
│   │   ├── mini_api.py             # Telegram Mini-App API
│   │   ├── mini_auth.py            # Mini-App auth
│   │   ├── mini_static.py          # Mini-App static file serving
│   │   └── preview.py              # Draft preview HTML (Jinja2)
│   ├── templates/
│   │   └── preview.html
│   └── mini-app/             # Vite + React 19 + Tailwind Mini-App (built by Dockerfile)
│       ├── package.json
│       ├── tsconfig.json
│       └── index.html
│
├── actors/                   # Apify Node.js scrapers (one Docker image per actor)
│   ├── platts-news-only/     # Legacy news-only actor
│   │   └── src/{main.js, routes.js}
│   ├── platts-scrap-full-news/
│   │   ├── .actor/{actor,input_schema,dataset_schema,output_schema}.json
│   │   ├── Dockerfile
│   │   ├── package.json
│   │   ├── src/
│   │   │   ├── auth/login.js
│   │   │   ├── extract/{articlePage,images,readingPane,tables}.js
│   │   │   ├── lib/eventBus.js     # JS EventBus (mirror of Python contract)
│   │   │   ├── main.js             # Actor entry — accepts trace_id, parent_run_id
│   │   │   ├── routes.js
│   │   │   ├── sources/{allInsights,ironOreTopic,rmw}.js
│   │   │   └── util/{dates,debug,semaphore}.js
│   │   └── tests/eventBus.test.js
│   ├── platts-scrap-price/
│   └── platts-scrap-reports/
│       ├── .actor/...
│       ├── src/
│       │   ├── auth/login.js
│       │   ├── download/capturePdf.js
│       │   ├── filters/applyFilters.js
│       │   ├── grid/{extractRows,navigateGrid}.js
│       │   ├── lib/eventBus.js     # Keep in sync with full-news copy
│       │   ├── main.js
│       │   ├── notify/telegramSummary.js
│       │   ├── persist/{supabaseClient,supabaseUpload}.js
│       │   └── util/{dates,slug}.js
│       └── tests/{dates,eventBus,filters,slug}.test.js
│
├── dashboard/                # Next.js 16 App Router (Vercel)
│   ├── package.json
│   ├── next.config.ts
│   ├── tsconfig.json
│   ├── components.json       # shadcn registry config
│   ├── postcss.config.mjs
│   ├── eslint.config.mjs
│   ├── app/
│   │   ├── layout.tsx
│   │   ├── page.tsx                # Workflow status + triggers (SWR)
│   │   ├── globals.css
│   │   ├── contacts/page.tsx       # Supabase contacts table
│   │   ├── executions/page.tsx
│   │   ├── news/page.tsx
│   │   └── workflows/page.tsx
│   ├── components/
│   │   ├── delivery/DeliveryReportView.tsx
│   │   ├── layout/SideNav.tsx
│   │   └── ui/                      # shadcn/Radix primitives
│   │       ├── avatar.tsx, badge.tsx, button.tsx, card.tsx,
│   │       ├── dropdown-menu.tsx, hover-card.tsx, input.tsx,
│   │       ├── scroll-area.tsx, separator.tsx, sheet.tsx,
│   │       ├── skeleton.tsx, table.tsx, textarea.tsx, tooltip.tsx
│   ├── lib/utils.ts          # cn() class merger
│   └── public/               # Static SVG assets
│
├── supabase/
│   └── migrations/           # Applied manually per supabase/migrations/README.md
│       ├── 20260418_event_log.sql      # Phase 3 observability table
│       ├── 20260419_event_log_rls.sql  # Service-role-only writes
│       └── 20260422_contacts.sql       # Sheets → Supabase migration
│
├── directives/               # Layer 1 — SOPs for LLM orchestration
│   ├── README.md
│   └── _templates/workflow_template.md
│
├── docs/
│   └── superpowers/          # Design docs + plans + retrospective followups
│       ├── specs/            # Design specs (one per feature)
│       │   ├── 2026-04-22-bot-queue-bulk-actions-design.md
│       │   ├── 2026-04-22-idempotency-claim-ordering-fix-design.md
│       │   ├── 2026-04-22-contacts-supabase-migration-design.md
│       │   ├── 2026-04-22-observability-trace-id-apify-propagation-design.md
│       │   ├── 2026-04-21-observability-unified-design.md
│       │   └── ... (38 more)
│       ├── plans/            # Implementation plans (phase breakdowns)
│       │   ├── 2026-04-22-bot-queue-bulk-actions-plan.md
│       │   ├── 2026-04-22-idempotency-split-lock-plan.md
│       │   ├── 2026-04-22-contacts-supabase-migration-plan.md
│       │   └── ... (30 more)
│       └── followups/        # Post-merge retrospectives
│           ├── 2026-04-22-observability-trace-id-apify-followups.md
│           └── 2026-04-21-observability-phase{1..4}-followups.md
│
├── tests/                    # pytest suite (74 test files)
│   └── (see Tests enumeration below)
│
├── .planning/
│   └── codebase/             # THIS DIRECTORY — codebase maps
│
├── .state/                   # Persistent state between runs (gitignored)
├── .tmp/                     # Ephemeral intermediates + .tmp/logs/
│   └── logs/                 # .tmp/logs/<workflow>/<run_id>.json
├── .claude/                  # Claude Code settings + slash commands
├── .superpowers/             # Local brainstorm / agent state (gitignored)
└── scripts/
    └── archive/              # Archived one-off scripts
```

## Directory Purposes

### Core Python Layer (`execution/`)

- **`execution/core/`** — Pure primitives with no subsystem-specific knowledge. `event_bus.py`, `state_store.py`, `delivery_reporter.py`, `progress_reporter.py`, `logger.py`, `cron_parser.py`, `retry.py`, `sentry_init.py` are the shared backbone imported by every script and by the webhook.
- **`execution/curation/`** (new layer) — Platts item classification and Redis curation keyspace. `redis_client.py` owns the `platts:*` namespace; `router.py` classifies items; `id_gen.py` produces stable hashes; `rationale_dispatcher.py` + `telegram_poster.py` push items into downstream pipelines.
- **`execution/integrations/`** — Thin adapters to every external API. One module per provider; `contacts_repo.py` is the single read-path for WhatsApp contacts.
- **`execution/scripts/`** — Cron entry points. Each is `@with_event_bus`-wrapped and exits success on "no data yet" so GH doesn't mark the run failed.
- **`execution/agents/`** — Claude-backed content runners (rationale drafting).
- **`execution/core/prompts/`** — Prompt templates for the draft pipeline (writer → critique → adjuster → curator).

### Bot Layer (`webhook/`)

- **`webhook/bot/`** — aiogram v3 package. `main.py` is the aiohttp entry point; `config.py` owns singletons; `callback_data.py` has typed factories; routers are feature-scoped.
- **`webhook/bot/routers/`** — One router per feature domain. `callbacks_queue.py` (new) owns all `/queue` navigation + bulk-action callbacks. All queue routes gated by `RoleMiddleware(allowed_roles={"admin"})`.
- **`webhook/bot/middlewares/`** — `auth.py:RoleMiddleware` reads `users.py` and short-circuits unauthorized callers.
- **`webhook/routes/`** — Plain aiohttp routes unrelated to Telegram: GH Actions callbacks (`api.py`), draft preview HTML (`preview.py`), Mini-App API + static (`mini_api.py`, `mini_static.py`, `mini_auth.py`).
- **`webhook/*.py` (top-level)** — Transport-agnostic helpers consumed by routers: `query_handlers.py` (formatters), `queue_selection.py` (select-mode Redis state, new), `redis_queries.py` (read-side queries), `dispatch.py` (approval pipeline), `status_builder.py` (/status), `reports_nav.py` (/reports), `workflow_trigger.py` (GH dispatch), `contact_admin.py`, `digest.py`, `pipeline.py`, `metrics.py`.
- **`webhook/mini-app/`** — Vite + React 19 + Tailwind v4 Mini-App. Built by stage 1 of the Dockerfile; served by `routes/mini_static.py`.

### Scraper Layer (`actors/`)

Four independent Apify Node packages. Each has its own `package.json`, `Dockerfile`, and `.actor/` manifest. `src/lib/eventBus.js` is duplicated between `platts-scrap-reports` and `platts-scrap-full-news`; the duplicates must stay in sync (header comment enforces).

### Dashboard Layer (`dashboard/`)

Standard Next.js 16 App Router. One file per route under `app/*/page.tsx`. Components split into feature folders (`delivery/`, `layout/`) + `ui/` for shadcn primitives. API routes live under `app/api/*` (inferred from `page.tsx` calls to `/api/workflows`, `/api/contacts`).

### Database Layer (`supabase/`)

Migrations only. No schema introspection or generated types. Applied manually per `supabase/migrations/README.md`. Three migrations currently: `event_log`, `event_log_rls`, `contacts`.

### Docs Layer (`docs/superpowers/`)

Three doc categories:
- `specs/` — design docs, one per feature, dated.
- `plans/` — implementation plans (phase breakdowns), dated.
- `followups/` — post-merge retrospectives.

## Key File Locations

### Entry Points

- `webhook/bot/main.py` — aiohttp + Aiogram webhook (Railway startCommand).
- `execution/scripts/*.py` — GH Actions cron entry points (one script per workflow YAML).
- `actors/*/src/main.js` — Apify actor entry points.
- `dashboard/app/page.tsx` — Next.js root page.
- `dashboard/app/layout.tsx` — App Router layout.

### Configuration

- `.env` (gitignored) — local env vars.
- `Dockerfile` — webhook image (Railway).
- `railway.json` — Railway config.
- `requirements.txt` — root Python deps (GH Actions).
- `webhook/requirements.txt` — bot-only Python deps (Railway).
- `dashboard/package.json` — Node deps for Vercel.
- `.github/workflows/*.yml` — cron schedules + secrets.
- `supabase/migrations/*.sql` — schema.
- `pytest.ini` — test discovery.
- `dashboard/tsconfig.json`, `next.config.ts`, `postcss.config.mjs`, `eslint.config.mjs`.

### Core Logic

- `execution/core/event_bus.py` — observability fan-out.
- `execution/core/state_store.py` — workflow-outcome + idempotency Redis store.
- `execution/core/delivery_reporter.py` — WhatsApp send tracking + circuit breaker.
- `execution/curation/redis_client.py` — curation staging/archive keyspace.
- `webhook/queue_selection.py` — select-mode Redis state.
- `webhook/bot/routers/callbacks_queue.py` — bulk-action handlers.
- `webhook/query_handlers.py` — `/queue` rendering.

### Testing

- `tests/conftest.py` — fixtures (fakeredis, supabase mocks).
- `tests/*.py` — pytest files co-named with the module under test.
- `actors/*/tests/*.test.js` — actor unit tests (Node).

## Naming Conventions

**Python files:** `snake_case.py` (e.g., `event_bus.py`, `state_store.py`, `callbacks_queue.py`).

**Python modules:** snake_case, organized by feature (`curation/`, `integrations/`) not by type.

**Test files:** `test_<module>.py` mirroring the source module (`test_state_store.py`, `test_callbacks_queue.py`, `test_queue_selection.py`).

**JS files (actors):** `camelCase.js` (e.g., `eventBus.js`, `capturePdf.js`, `applyFilters.js`).

**TypeScript files (dashboard):** `PascalCase.tsx` for components, `camelCase.ts` for utilities, `kebab-case.tsx` for shadcn primitives (`dropdown-menu.tsx`).

**SQL migrations:** `YYYYMMDD_<short_name>.sql` — ISO date prefix for natural ordering.

**Design docs:** `YYYY-MM-DD-<kebab-name>-design.md` in `docs/superpowers/specs/`.

**Plans:** `YYYY-MM-DD-<kebab-name>-plan.md` in `docs/superpowers/plans/`.

**Directives:** descriptive kebab-case (e.g., `scrape_website.md`) in `directives/`.

## Where to Add New Code

### New Cron Workflow
- Python script: `execution/scripts/<name>.py` wrapped with `@with_event_bus("<name>")`.
- GH Actions YAML: `.github/workflows/<name>.yml` with `REDIS_URL`, `SUPABASE_*`, `TELEGRAM_*`, `SENTRY_DSN` envs.
- Add `<name>` to `ALL_WORKFLOWS` in `webhook/status_builder.py` and to `_TAIL_KNOWN_WORKFLOWS` in `webhook/bot/routers/commands.py` for `/tail` support.

### New Bot Command
- Handler: add `@admin_router.message(Command("<name>"))` in `webhook/bot/routers/commands.py` (or create a feature-scoped router in `webhook/bot/routers/` and register it in `webhook/bot/main.py`).
- Formatter: pure function in `webhook/query_handlers.py` returning `(text, reply_markup)`.

### New Bot Callback Button
- Typed CallbackData: add class to `webhook/bot/callback_data.py` with a short `prefix` (≤8 chars, Telegram 64-byte budget).
- Handler: router in `webhook/bot/routers/callbacks_<domain>.py` using `@router.callback_query(<Class>.filter())`.

### New Redis Keyspace
- For workflow state: add helpers to `execution/core/state_store.py` (non-raising contract).
- For curation state: add helpers to `execution/curation/redis_client.py` (raising contract, use pipeline transactions for multi-key ops).
- For bot runtime state: new module under `webhook/` that imports `execution.curation.redis_client._get_client`.

### New Apify Actor
- New directory `actors/<name>/` with its own `package.json` + `Dockerfile` + `.actor/` manifest.
- Copy `src/lib/eventBus.js` from an existing actor (keep in sync).
- Entry point at `actors/<name>/src/main.js`, accept `trace_id` + `parent_run_id` from `Actor.getInput()`.
- Invoke from `execution/integrations/apify_client.py`.

### New Dashboard Page
- Next.js route: `dashboard/app/<name>/page.tsx` with `"use client"` + `useSWR`.
- API route (if needed): `dashboard/app/api/<name>/route.ts`.
- Shared UI: reuse `dashboard/components/ui/*`; feature components under `dashboard/components/<domain>/`.

### New Supabase Migration
- `supabase/migrations/YYYYMMDD_<name>.sql` using `create … if not exists`.
- Apply via Supabase SQL editor or CLI; log in `supabase/migrations/README.md` applied-migrations table.

### New Test
- Python: `tests/test_<module>.py` matching the source module. Use `fakeredis` + `pytest-asyncio` fixtures from `conftest.py`.
- Actor: `actors/<name>/tests/<feature>.test.js` using whatever test runner the actor's `package.json` declares.

## Special Directories

- **`.state/`** — Persistent state between cron runs (gitignored, not in Redis).
- **`.tmp/logs/`** — Structured JSON logs per `.tmp/logs/<workflow>/<run_id>.json`. 7-day retention (per AGENT.md).
- **`.tmp/`** — All intermediates (regenerable). Never commit.
- **`.worktrees/`** — Git worktree directories for parallel branches (gitignored).
- **`.planning/codebase/`** — THIS directory. Codebase maps consumed by other Claude agents.
- **`.superpowers/brainstorm/`** — Transient brainstorm HTML outputs from Claude agent runs (gitignored).
- **`.claude/`** — Claude Code workspace settings + slash-command definitions.

## Tests Enumeration

### Observability + state
- `test_event_bus.py` — Sink fan-out, claim-ordering guarantees.
- `test_state_store.py` — `check_sent_flag`, `set_sent_flag`, `release_inflight`, streaks, crash dedup.
- `test_watchdog.py` — Missing-cron detection.
- `test_progress_reporter.py`, `test_progress_reporter_sinks.py`.
- `test_agents_progress.py`.
- `test_cron_parser.py`.

### Idempotency (new)
- `test_morning_check_idempotency.py` — Phase 0–5 split-lock scenarios.
- `test_baltic_ingestion_idempotency.py` — Phase 0–5 split-lock scenarios.
- `test_dispatch_idempotency.py`.

### Curation Redis (new)
- `test_curation_redis_client.py` — staging/archive/bulk ops.
- `test_curation_router.py` — classify/route.
- `test_curation_id_gen.py`.
- `test_curation_telegram_poster.py`.
- `test_rebuild_dedup.py`.

### Bot callbacks + FSM
- `test_callbacks_queue.py` — Bulk actions + pagination + mode toggle (new).
- `test_callbacks_curation.py` — Draft/curate/broadcast.
- `test_callbacks_contacts.py` — Contact admin.
- `test_callbacks_menu.py`, `test_callbacks_reports.py`, `test_callbacks_workflows.py`.
- `test_bot_callback_data.py` — Typed CallbackData (de)serialization.
- `test_bot_middlewares.py` — RoleMiddleware.
- `test_bot_delivery.py`, `test_bot_states.py`, `test_bot_users.py`.
- `test_messages_fsm_isolation.py`.
- `test_reject_reason_flow.py`.

### Queue + query handlers
- `test_queue_selection.py` — Select-mode Redis state (new).
- `test_query_handlers.py` — `/queue` normal + select rendering, `/history`, `/stats`, `/rejections`.
- `test_redis_queries.py` — Feedback + pipeline keyspace.

### Contacts
- `test_contacts_repo.py`, `test_contacts_repo_normalize.py`, `test_contacts_bulk_ops.py`.
- `test_contact_admin.py`.
- `archive/test_migrate_contacts_from_sheets.py` (archived).

### Mini-App
- `test_mini_auth.py`, `test_mini_contacts.py`, `test_mini_news.py`, `test_mini_reports.py`, `test_mini_stats.py`, `test_mini_workflows.py`.

### Traces (Phase 4)
- `test_platts_ingestion_trace.py`, `test_platts_reports_trace.py`.

### Misc
- `test_tail_command.py`, `test_webhook_status.py`, `test_workflow_trigger.py`.
- `test_delivery_reporter.py`.
- `test_digest.py`.
- `test_metrics_endpoint.py`.
- `test_prompts.py`.

### Actor JS tests (run in each actor package)
- `actors/platts-scrap-full-news/tests/eventBus.test.js`.
- `actors/platts-scrap-reports/tests/{dates,eventBus,filters,slug}.test.js`.

---

*Structure analysis: 2026-04-22*
