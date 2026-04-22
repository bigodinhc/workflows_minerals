# Codebase Structure

**Analysis Date:** 2026-04-22

## Directory Layout

```
/Users/bigode/Dev/agentics_workflows/
├── webhook/                    # Telegram bot + HTTP API server
│   ├── bot/                    # Aiogram bot application
│   │   ├── main.py            # aiohttp app entry point, webhook setup
│   │   ├── config.py          # Bot/Dispatcher/Storage singletons, env vars
│   │   ├── callback_data.py    # CallbackData factories (typed button data)
│   │   ├── states.py          # FSM state groups (AddContact, etc.)
│   │   ├── users.py           # User/chat_id auth checks
│   │   ├── keyboards.py       # UI builders (inline, reply keyboards)
│   │   ├── delivery.py        # Telegram message delivery/edit helpers
│   │   ├── middlewares/       # Auth, error handling
│   │   │   └── auth.py        # RoleMiddleware for admin/subscriber checks
│   │   └── routers/           # Message handlers organized by feature
│   │       ├── commands.py    # /add, /list, /status, /tail, etc.
│   │       ├── messages.py    # FSM text input handlers (AddContact.waiting_data, etc.)
│   │       ├── onboarding.py  # /start, approval, subscription FSM
│   │       ├── callbacks_contacts.py  # Toggle, pagination, bulk operations
│   │       ├── callbacks_curation.py  # Draft approval, rejection, pipeline
│   │       ├── callbacks_reports.py   # Report type/year/month/download navigation
│   │       ├── callbacks_queue.py     # Staging queue navigation
│   │       ├── callbacks_menu.py      # Main menu switchboard
│   │       ├── callbacks_workflows.py # Workflow trigger + nop callbacks
│   │       ├── settings.py    # Subscription panel
│   │       └── _helpers.py    # Shared handler utilities
│   ├── dispatch.py            # WhatsApp sending (idempotency, Uazapi)
│   ├── contact_admin.py       # Contact parsing, state, keyboard building
│   ├── query_handlers.py      # Query/report builders (history, stats, queue)
│   ├── workflow_trigger.py    # Workflow manual trigger UI
│   ├── status_builder.py      # /status command response formatting
│   ├── reports_nav.py         # Report navigation tree
│   ├── digest.py              # Message digestion/curation logic
│   ├── pipeline.py            # Message pipeline stages
│   ├── metrics.py             # Error/success counters
│   ├── redis_queries.py       # Redis query helpers (feedback, staging)
│   ├── routes/                # aiohttp HTTP endpoints
│   │   ├── api.py            # Main API routes (reports, webhooks)
│   │   ├── mini_api.py       # Mini-app API routes
│   │   ├── mini_auth.py      # Mini-app auth
│   │   ├── mini_static.py    # Mini-app static files
│   │   └── preview.py        # Draft preview templates
│   ├── templates/             # Jinja2 templates for preview
│   └── mini-app/              # Frontend mini-app (embedded in Telegram)
│
├── execution/                 # Core business logic, scripts, integrations
│   ├── core/                  # Shared utilities
│   │   ├── state_store.py    # Persistent workflow state (JSON in .state/)
│   │   ├── state.py          # Runtime state model
│   │   ├── delivery_reporter.py  # WhatsApp delivery aggregation + error categorization
│   │   ├── progress_reporter.py  # Event emission to event_log table
│   │   ├── event_bus.py      # Event emission/subscription
│   │   ├── logger.py         # Structured logging
│   │   ├── retry.py          # Exponential backoff retry logic
│   │   ├── runner.py         # Workflow executor
│   │   ├── cron_parser.py    # Cron expression parsing
│   │   ├── sentry_init.py    # Error tracking setup
│   │   └── prompts/          # LLM prompt templates
│   │       ├── writer.py     # Content writing prompt
│   │       ├── curator.py    # Content curation prompt
│   │       ├── adjuster.py   # Draft adjustment prompt
│   │       └── critique.py   # Critique/review prompt
│   ├── integrations/         # External service clients
│   │   ├── contacts_repo.py  # Supabase contacts table (formerly sheets_client.py)
│   │   ├── supabase_client.py # Supabase initialization
│   │   ├── sheets_client.py  # Google Sheets (legacy, being phased out)
│   │   ├── telegram_client.py # Telegram API wrapper
│   │   ├── uazapi_client.py  # WhatsApp Uazapi wrapper
│   │   ├── claude_client.py  # Anthropic API wrapper
│   │   ├── apify_client.py   # Apify (web scraping) client
│   │   ├── baltic_client.py  # Baltic exchange data client
│   │   ├── lseg_client.py    # LSEG (Refinitiv) client
│   │   ├── platts_client.py  # S&P Platts client
│   │   └── __init__.py       # Shared initialization
│   ├── curation/             # Content curation workflow
│   │   ├── rationale_dispatcher.py # Router for curator agent
│   │   ├── router.py         # Routing logic
│   │   ├── id_gen.py         # Unique ID generation
│   │   ├── redis_client.py   # Redis integration for curation
│   │   └── telegram_poster.py # Telegram message posting
│   ├── agents/               # AI agents (LLM-backed workers)
│   │   └── rationale_agent.py # Curator agent
│   ├── supabase/             # Supabase client initialization
│   │   └── __init__.py
│   ├── scripts/              # CLI/cron executable scripts
│   │   ├── send_daily_report.py   # Morning report broadcast
│   │   ├── send_news.py           # News broadcast (cron)
│   │   ├── morning_check.py       # Daily health check
│   │   ├── baltic_ingestion.py    # Baltic exchange ingest
│   │   ├── platts_ingestion.py    # S&P Platts ingest
│   │   ├── platts_reports.py      # Platts reports download
│   │   ├── rebuild_dedup.py       # Rebuild deduplication index
│   │   ├── watchdog_cron.py       # Health check daemon
│   │   ├── manual_ingestion_json.py # Manual data import
│   │   ├── debug_apify.py         # Debug apify scraper
│   │   ├── inspect_platts.py      # Inspect Platts data
│   │   └── __init__.py
│   └── __init__.py
│
├── supabase/                 # Database migrations + config
│   ├── migrations/           # SQL migration files
│   │   ├── 20260422_contacts.sql     # Contacts table + indexes, triggers, RLS
│   │   ├── 20260419_event_log_rls.sql # Event log RLS policies
│   │   └── 20260418_event_log.sql     # Event log table
│   └── config.json          # Supabase project config
│
├── dashboard/                # Next.js admin dashboard (TypeScript)
│   ├── app/                  # Next.js app router
│   │   ├── api/
│   │   │   ├── contacts/route.ts  # Contacts API (parallel to Python repo)
│   │   │   └── ...
│   │   └── page.tsx          # Dashboard homepage
│   ├── components/           # React components
│   ├── lib/                  # Utilities (Supabase client, etc.)
│   ├── public/               # Static assets
│   └── package.json
│
├── tests/                    # Pytest test suite
│   ├── test_contacts_repo.py # ContactsRepo unit tests (with fake clients)
│   ├── test_callbacks_contacts.py # Callback handler tests
│   ├── test_contact_admin.py # Input parsing, state, keyboard tests
│   ├── test_bot_states.py    # FSM state tests
│   ├── test_bot_delivery.py  # Message delivery tests
│   ├── test_dispatch_idempotency.py # Idempotency key logic
│   ├── test_contacts_bulk_ops.py # Bulk operation tests
│   ├── test_callbacks_reports.py # Report navigation tests
│   ├── test_migration_contacts_from_sheets.py # Migration verification
│   ├── test_delivery_reporter.py # Error categorization tests
│   ├── test_query_handlers.py # Report query logic
│   ├── test_bot_callback_data.py # CallbackData serialization
│   ├── test_progress_reporter.py # Event logging
│   └── ... (54 total test files)
│
├── scripts/                  # Utility scripts (not imported in app)
│   ├── migrate_contacts_from_sheets.py # One-time: sheets → supabase
│   └── ...
│
├── actors/                   # Apify actor definitions (web scraping)
│   ├── platts-scrap-price/   # Platts price scraper
│   ├── platts-scrap-reports/ # Platts reports scraper
│   ├── platts-scrap-full-news/ # Full news scraper
│   └── ...
│
├── directives/               # Prompt templates (reusable)
│   ├── _templates/           # Template library
│   └── ...
│
├── data/                     # Static data, configuration
│   └── ...
│
├── docs/                     # Documentation
│   ├── superpowers/          # Feature documentation
│   └── ...
│
├── .planning/codebase/       # GSD codebase analysis (this directory)
│   ├── ARCHITECTURE.md       # Architecture & data flow
│   ├── STRUCTURE.md          # This file
│   ├── CONVENTIONS.md        # Coding style & patterns
│   ├── TESTING.md            # Testing approach
│   ├── STACK.md              # Technology stack
│   ├── INTEGRATIONS.md       # External services
│   └── CONCERNS.md           # Technical debt & issues
│
├── .github/                  # GitHub Actions workflows
│   └── workflows/            # CI/CD pipelines
│
├── .env                      # Environment variables (secrets)
├── .env.example              # Example env vars (safe template)
├── .gitignore
├── requirements.txt          # Python dependencies
├── package.json              # JavaScript dependencies
├── package-lock.json         # JavaScript lockfile
├── pytest.ini                # Pytest configuration
├── Dockerfile                # Container image definition
├── railway.json              # Railway.app deployment config
├── AGENT.md                  # Agent/workflow documentation
└── README.md
```

## Directory Purposes

**`webhook/`:**
- Purpose: HTTP entry point for Telegram webhook + supplementary HTTP API
- Contains: Aiogram bot routers, dispatcher setup, aiohttp route handlers
- Key files: `bot/main.py` (entry point), `dispatch.py` (broadcast), `contact_admin.py` (parsing)

**`webhook/bot/`:**
- Purpose: Telegram bot application (Aiogram framework)
- Contains: Message handlers, callback handlers, FSM state definitions, keyboard builders
- Key files: `config.py` (singletons), `routers/commands.py` (command dispatch), `routers/messages.py` (FSM inputs), `routers/callbacks_contacts.py` (contact operations)

**`webhook/routes/`:**
- Purpose: HTTP API endpoints (non-Telegram)
- Contains: Report downloads, workflow status, mini-app authentication
- Key files: `api.py` (main routes), `mini_api.py` (embedded app)

**`execution/`:**
- Purpose: Core business logic, external service integration, CLI scripts
- Contains: Workflow orchestration, contact repository, service clients
- Key files: `integrations/contacts_repo.py` (Supabase abstraction), `core/delivery_reporter.py` (error handling), `scripts/` (cron jobs)

**`execution/integrations/`:**
- Purpose: Wrap external API clients with domain logic
- Contains: ContactsRepo, Telegram, Uazapi, Claude, Supabase clients
- Note: Each client is independently testable; repos accept optional `client=` for dependency injection

**`execution/core/`:**
- Purpose: Reusable utilities for workflow execution
- Contains: State management, logging, retry logic, LLM prompts, event emission
- Key files: `delivery_reporter.py` (WhatsApp error categorization), `progress_reporter.py` (event logging), `state_store.py` (persistent state)

**`execution/scripts/`:**
- Purpose: Standalone CLI/cron-invoked executables
- Contains: Data ingestion (Baltic, Platts), report generation, health checks
- Invoked by: GitHub Actions scheduled jobs
- Entry pattern: `if __name__ == "__main__": main()`

**`supabase/migrations/`:**
- Purpose: Database schema definitions (SQL)
- Contains: Version-stamped migration files
- Naming: `YYYYMMDD_description.sql`
- Key file: `20260422_contacts.sql` (active contact list, replacing Google Sheets)

**`dashboard/`:**
- Purpose: Admin UI (Next.js, TypeScript, React)
- Contains: Web interface for contact management, report viewing, workflow triggers
- Key file: `app/api/contacts/route.ts` (parallel TypeScript implementation of Python ContactsRepo)
- Note: Mirrors Python contact operations; decoupled but synchronized

**`tests/`:**
- Purpose: Automated test suite (Pytest)
- Contains: Unit tests, integration tests, mock clients
- Naming: `test_*.py` (Pytest discovery pattern)
- Key file: `test_contacts_repo.py` (FakeQuery mock for Supabase client testing)
- Key file: `test_callbacks_contacts.py` (handler mocking + dispatcher testing)

## Key File Locations

**Entry Points:**

| File | Triggers | Purpose |
|------|----------|---------|
| `webhook/bot/main.py` | HTTP POST to `/webhook` | Aiogram webhook + aiohttp app bootstrap |
| `execution/scripts/send_daily_report.py` | GitHub Actions cron | Daily report broadcast |
| `execution/scripts/send_news.py` | GitHub Actions cron | News article broadcast |
| `webhook/routes/api.py` | HTTP requests to `/api/...` | Admin API (reports, webhooks) |

**Configuration:**

| File | Purpose |
|------|---------|
| `webhook/bot/config.py` | Bot/Dispatcher/Storage singletons, env var resolution |
| `supabase/config.json` | Supabase project settings |
| `.env` | Environment variables (secrets, auth tokens) |
| `pytest.ini` | Test runner configuration |
| `railway.json` | Railway.app deployment manifest |

**Core Logic:**

| File | Purpose |
|------|---------|
| `execution/integrations/contacts_repo.py` | Contact CRUD, phone normalization, business rules |
| `webhook/dispatch.py` | WhatsApp message sending, idempotency, error handling |
| `execution/core/delivery_reporter.py` | Delivery result aggregation, error categorization |
| `webhook/contact_admin.py` | Input parsing, state management, UI rendering |
| `webhook/bot/routers/commands.py` | `/add`, `/list`, `/status`, `/tail` command handlers |
| `webhook/bot/routers/messages.py` | FSM text input handlers (contact add, adjust, reject) |
| `webhook/bot/routers/callbacks_contacts.py` | Toggle, bulk, pagination button handlers |

**Testing:**

| File | Purpose |
|------|---------|
| `tests/test_contacts_repo.py` | ContactsRepo with FakeQuery mock |
| `tests/test_callbacks_contacts.py` | Handler + callback dispatcher testing |
| `tests/test_contact_admin.py` | Input parsing + keyboard building |
| `tests/test_dispatch_idempotency.py` | Redis idempotency key logic |
| `tests/test_contacts_bulk_ops.py` | Bulk status changes |

**Database:**

| File | Purpose |
|------|---------|
| `supabase/migrations/20260422_contacts.sql` | Contacts table schema (replaces Google Sheets) |
| `supabase/migrations/20260419_event_log_rls.sql` | Event log RLS policies |
| `supabase/migrations/20260418_event_log.sql` | Event log schema |

## Naming Conventions

**Files:**

- **Python modules**: `snake_case.py` (e.g., `contact_admin.py`, `contacts_repo.py`)
- **Test files**: `test_<module_name>.py` (e.g., `test_contacts_repo.py`)
- **Routers**: `<feature>_<handler_type>.py` (e.g., `callbacks_contacts.py`, `routers/commands.py`)
- **Migration files**: `YYYYMMDD_<description>.sql` (e.g., `20260422_contacts.sql`)
- **TypeScript**: `kebab-case.ts` for routes, `PascalCase.tsx` for React components

**Directories:**

- **Feature grouping**: By functional domain (e.g., `routers/`, `integrations/`, `scripts/`)
- **Layer separation**: `core/` (utilities), `integrations/` (API clients), `scripts/` (CLI)
- **Tests colocated**: `tests/` directory (NOT scattered in source)

**Classes/Exports:**

- **Data classes**: `PascalCase` (e.g., `Contact`, `DeliveryResult`, `ContactsRepo`)
- **Functions**: `snake_case` (e.g., `normalize_phone()`, `build_list_keyboard()`)
- **Constants**: `UPPER_SNAKE_CASE` (e.g., `WEBHOOK_PATH`, `WELCOME_MESSAGE`)
- **Exceptions**: `PascalCase` ending in `Error` (e.g., `InvalidPhoneError`, `ContactNotFoundError`)

**CallbackData Classes:**

- **Pattern**: `PascalCase` with domain prefix (e.g., `ContactToggle`, `ContactBulk`, `ContactBulkConfirm`)
- **Prefix**: `CallbackData(prefix="...")` (e.g., `prefix="tgl"`, `prefix="bulk"`)
- **Filter method**: Automatically generated by Aiogram (e.g., `ContactToggle.filter()`)

## Where to Add New Code

**New Feature (Contact-like entity):**

1. **Data layer**: Add migration in `supabase/migrations/YYYYMMDD_<table>.sql`
2. **Repository**: Create `execution/integrations/<entity>_repo.py` (extends ContactsRepo pattern)
3. **Handler**: Create `webhook/bot/routers/<entity>_admin.py` or extend existing router
4. **CallbackData**: Add to `webhook/bot/callback_data.py` (factories for button types)
5. **Tests**: Create `tests/test_<entity>_repo.py` + `tests/test_<entity>_admin.py`
6. **Scripts**: Add endpoints to `execution/scripts/` if needed

**New Command:**

1. **Handler**: Add to `webhook/bot/routers/commands.py` (admin_router or public_router)
2. **Tests**: Add to `tests/test_bot_callback_data.py` or create `tests/test_<command>.py`
3. **State (if multi-step)**: Add to `webhook/bot/states.py`
4. **Callback handling (if buttons)**: Add to `webhook/bot/routers/callbacks_*.py`

**New Service Integration (API client):**

1. **Client**: Create `execution/integrations/<service>_client.py`
2. **Interface**: Define domain-specific methods (not raw API calls)
3. **Tests**: Create `tests/test_<service>_client.py` with mock HTTP
4. **Usage**: Import in scripts or routers; pass as dependency

**New Script (cron job):**

1. **File**: Create in `execution/scripts/<workflow_name>.py`
2. **Entry point**: Implement `main()` function
3. **State**: Use `execution.core.state_store` for persistence
4. **Logging**: Use `execution.core.logger` for Sentry integration
5. **Invocation**: Add GitHub Actions job in `.github/workflows/`

**Utilities/Helpers:**

- **Shared parsing/validation**: `webhook/contact_admin.py` (for contact-specific) or new utility module
- **Shared business logic**: `execution/core/` (for workflow-agnostic logic)
- **Shared UI builders**: `webhook/bot/keyboards.py` (inline/reply keyboards)

## Special Directories

**`.state/`:**
- Purpose: Persistent JSON state for long-running workflows (GitHub Actions between jobs)
- Generated: Yes (auto-created by `state_store.py`)
- Committed: No (git-ignored, rebuild on each run)
- Pattern: `<workflow_name>.json` (e.g., `send_daily_report.json`)

**`.tmp/logs/`:**
- Purpose: Local development log archives
- Generated: Yes
- Committed: No (git-ignored)

**`supabase/.temp/`:**
- Purpose: Supabase CLI temporary files
- Generated: Yes
- Committed: No (git-ignored)

**`dashboard/.next/`:**
- Purpose: Next.js build output (client + server bundles)
- Generated: Yes (on `npm run build`)
- Committed: No (git-ignored)

**`__pycache__/`, `.pytest_cache/`:**
- Purpose: Python bytecode cache, Pytest caches
- Generated: Yes
- Committed: No (git-ignored)

## Public vs Internal Boundaries

**Public/Exported APIs:**

- `execution.integrations.contacts_repo.ContactsRepo` — Service boundary (all scripts/handlers depend on it)
- `execution.integrations.contacts_repo.Contact` — Data model (serializable, hashable)
- `webhook.dispatch.send_whatsapp()` — Broadcast sending (handlers, scripts use it)
- `execution.core.delivery_reporter.DeliveryReporter` — Error handling abstraction
- `webhook.bot.routers.*` — Handler routers (included by dispatcher)

**Internal/Private APIs:**

- `webhook.contact_admin._parse_ts()` — Helper (underscore prefix signals internal)
- `execution.integrations.contacts_repo._row_to_contact()` — Conversion helper
- `webhook.dispatch._redis_sync_client`, `_redis_async_client` — Module singletons (not exported)
- `webhook.bot.config._bot`, `_dp`, `_storage` — Lazy singletons (accessed via get_*() functions)

---

*Structure analysis: 2026-04-22*
