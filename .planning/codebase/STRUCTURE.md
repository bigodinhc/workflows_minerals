# Codebase Structure

**Analysis Date:** 2026-04-17

## Directory Layout

```
Antigravity WF/
├── .github/
│   └── workflows/          # GitHub Actions CI/CD (morning_check, daily_report, platts_reports, etc.)
├── .planning/              # GSD planning documents
│   └── codebase/           # Architecture analysis (ARCHITECTURE.md, STRUCTURE.md, etc.)
├── .state/                 # Persistent state files (dedup caches, workflow status)
├── .superpowers/           # Superpowers agent brainstorms
├── .worktrees/             # Git worktrees for feature branches (phase1-aiogram, phase2-ux)
├── .tmp/                   # Runtime logs and temporary files
├── .venv/                  # Python virtual environment
├── actors/                 # Apify web scrapers (JavaScript/Node.js)
│   ├── platts-scrap-price/         # Price scraper
│   ├── platts-scrap-full-news/     # News + reports scraper
│   ├── platts-scrap-reports/       # PDF reports (with Supabase upload)
│   ├── platts-news-only/           # News-only variant
│   └── .investigation/             # Research actors
├── dashboard/              # Next.js admin dashboard
│   ├── app/                # Next.js routes
│   │   ├── api/            # Server-side API routes (contacts, news, workflows)
│   │   ├── news/           # News list/detail pages
│   │   ├── workflows/      # Workflow/execution view
│   │   ├── contacts/       # Contact management
│   │   └── layout.tsx      # Root layout
│   ├── components/         # React components (dashboard, delivery, layout, ui)
│   ├── lib/                # Utilities, API clients
│   ├── public/             # Static assets
│   └── package.json        # Dependencies
├── data/                   # Data files (reference data, manual inputs)
├── directives/             # Prompts and instruction templates
│   └── _templates/         # Prompt templates for Claude
├── docs/
│   └── superpowers/        # Phase plans and design specs
│       ├── plans/          # Implementation plans (2026-04-*)
│       └── specs/          # Design specifications
├── execution/              # Python backend (business logic, AI, integrations)
│   ├── agents/             # Claude-based AI agents
│   │   └── rationale_agent.py    # 3-phase market analysis pipeline
│   ├── core/               # Core utilities
│   │   ├── logger.py       # Structured logging (WorkflowLogger)
│   │   ├── state.py        # State machine enums
│   │   ├── state_store.py  # Persistent state (Postgres)
│   │   ├── progress_reporter.py   # Workflow progress tracking
│   │   ├── delivery_reporter.py   # Broadcast delivery tracking
│   │   ├── cron_parser.py  # Cron expression parsing
│   │   └── prompts/        # Claude prompt templates
│   ├── curation/           # Data classification and routing
│   │   ├── router.py       # Classify items (news vs rationale), stage in Redis
│   │   ├── id_gen.py       # Generate unique IDs (hash-based)
│   │   ├── redis_client.py # Redis operations (staging, dedup, seen)
│   │   ├── rationale_dispatcher.py # Route rationale to AI agent
│   │   └── telegram_poster.py      # Post to Telegram channel
│   ├── integrations/       # External service clients
│   │   ├── apify_client.py        # Apify actor runner
│   │   ├── supabase_client.py     # Supabase Postgres/Storage
│   │   ├── sheets_client.py       # Google Sheets (contacts)
│   │   ├── telegram_client.py     # Telegram Bot API
│   │   ├── claude_client.py       # Anthropic Claude API
│   │   ├── platts_client.py       # LSEG Platts API
│   │   ├── lseg_client.py         # LSEG data
│   │   ├── baltic_client.py       # Baltic Exchange API
│   │   └── uazapi_client.py       # UAZ Minerals API
│   └── scripts/            # Cron-triggered workflows
│       ├── platts_ingestion.py     # Main scrape → classify → stage workflow
│       ├── morning_check.py        # Daily market summary
│       ├── send_daily_report.py    # Generate and broadcast daily report
│       ├── send_news.py            # Send news alerts
│       ├── baltic_ingestion.py     # Baltic Exchange data
│       ├── platts_reports.py       # Report PDF scraping
│       ├── rebuild_dedup.py        # Rebuild dedup cache
│       └── inspect_platts.py       # Debug script
├── tests/                  # Python pytest suite
│   ├── test_*.py           # Unit and integration tests
│   ├── conftest.py         # Pytest fixtures and configuration
│   └── (36 test files)     # Comprehensive coverage
├── webhook/                # aiohttp + Aiogram webhook server
│   ├── bot/                # Telegram bot handlers and config
│   │   ├── main.py         # Entry point (creates aiohttp app, registers routers)
│   │   ├── config.py       # Environment vars, Bot/Dispatcher singletons
│   │   ├── callback_data.py # CallbackData objects for type-safe buttons
│   │   ├── states.py       # FSM states for user conversations
│   │   ├── keyboards.py    # Inline/reply keyboard builders
│   │   ├── routers/        # Message handlers
│   │   │   ├── commands.py         # /start, /help, /menu, /settings
│   │   │   ├── onboarding.py       # /start flow, approval, subscription
│   │   │   ├── callbacks.py        # All callback_query handlers (queue, approve, reject, etc.)
│   │   │   ├── messages.py         # Text message handlers (FSM, catch-all)
│   │   │   ├── settings.py         # /settings flow
│   │   │   ├── reply_kb_router.py  # Reply keyboard text handlers
│   │   │   └── _helpers.py         # Shared utilities (drafts_get, process_adjustment)
│   │   └── middlewares/    # Aiogram middleware
│   │       └── auth.py     # RoleMiddleware (admin/subscriber/public)
│   ├── routes/             # aiohttp HTTP routes
│   │   ├── api.py          # Store drafts, seen articles, health checks, admin operations
│   │   ├── mini_api.py     # Mini App REST API (/api/mini/news, /api/mini/workflows, etc.)
│   │   ├── mini_auth.py    # Telegram init_data validation
│   │   ├── mini_static.py  # Serve mini-app dist/ frontend
│   │   └── preview.py      # Draft preview endpoint
│   ├── mini-app/           # React/TypeScript Mini App (Telegram Web App)
│   │   ├── src/
│   │   │   ├── App.tsx     # Main router and tab navigation
│   │   │   ├── pages/      # Page components (Home, News, NewsDetail, Workflows, Reports, Contacts, More)
│   │   │   ├── components/ # Reusable UI (TabBar, Card, Skeleton, Button, etc.)
│   │   │   ├── hooks/      # Custom hooks (useNavigation, useAuth, useLocalStorage)
│   │   │   ├── lib/        # API client, utilities (formatDate, classnames, etc.)
│   │   │   ├── index.css   # Global styles
│   │   │   ├── main.tsx    # React DOM render
│   │   │   ├── telegram.d.ts # Telegram Web App SDK types
│   │   │   └── test-setup.ts # Jest setup
│   │   ├── dist/           # Built frontend (generated by npm run build)
│   │   ├── package.json    # Dependencies (Vite, React, TypeScript, Tailwind)
│   │   └── vite.config.ts  # Vite build configuration
│   ├── templates/          # Jinja2 templates (draft preview HTML)
│   └── requirements.txt    # Python dependencies (aiohttp, aiogram, etc.)
├── node_modules/           # npm packages (root: apify-client only)
├── .env                    # Environment variables (secrets, API keys) — gitignored
├── .env.example            # Template for .env
├── .gitignore              # Git ignore rules
├── Dockerfile              # Multi-stage build (Node + Python)
├── package.json            # Root npm package (apify-client)
├── package-lock.json       # npm lockfile
├── pytest.ini              # Pytest configuration
├── requirements.txt        # Root Python dependencies
└── railway.json            # Railway deployment config

```

## Directory Purposes

**actors/:**
- Purpose: Apify cloud scrapers (JavaScript/Node.js)
- Contains: Actor.json (metadata), src/main.js (entry), src/auth/, src/download/, src/filters/, src/grid/, src/notify/, src/persist/, src/util/
- Key files: `actors/platts-scrap-reports/src/main.js` (Platts report PDF scraper)

**dashboard/:**
- Purpose: Next.js admin dashboard (React)
- Contains: TypeScript pages, components, API routes
- Key files: `dashboard/app/page.tsx` (root), `dashboard/app/api/*/route.ts` (server routes)

**execution/:**
- Purpose: Python business logic, AI orchestration, integrations
- Contains: Agents, curation logic, external API clients, scripts, core utilities
- Key files: `execution/scripts/platts_ingestion.py` (main ingestion), `execution/agents/rationale_agent.py` (3-phase AI)

**webhook/:**
- Purpose: aiohttp webhook server (Telegram bot + Mini App API)
- Contains: Aiogram handlers, HTTP routes, React Mini App, templates
- Key files: `webhook/bot/main.py` (entry), `webhook/routes/mini_api.py` (API), `webhook/mini-app/src/App.tsx` (React root)

**tests/:**
- Purpose: Python pytest test suite
- Contains: Unit tests, integration tests, fixtures
- Key files: `tests/conftest.py` (fixtures), `tests/test_*.py` (test modules)

**docs/superpowers/:**
- Purpose: Phase plans and design specifications
- Contains: `plans/` (implementation plans), `specs/` (design docs)
- Key files: `docs/superpowers/plans/2026-04-*.md` (phase implementation plans)

**.planning/codebase/:**
- Purpose: Architecture analysis documents (generated by gsd:map-codebase)
- Contains: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, CONCERNS.md, STACK.md, INTEGRATIONS.md

## Key File Locations

**Entry Points:**
- `webhook/bot/main.py` — Telegram webhook server (aiohttp + Aiogram)
- `execution/scripts/platts_ingestion.py` — Scrape → classify → stage workflow (cron-triggered)
- `actors/platts-scrap-reports/src/main.js` — Apify actor main entry
- `dashboard/app/page.tsx` — Next.js dashboard root
- `webhook/mini-app/src/main.tsx` — React Mini App entry

**Configuration:**
- `webhook/bot/config.py` — Environment vars, Bot/Dispatcher singletons, constants
- `.env` — Secrets (TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, REDIS_URL, etc.) — gitignored
- `Dockerfile` — Multi-stage Docker build
- `railway.json` — Railway deployment manifest

**Core Logic:**
- `execution/curation/router.py` — Item classification and staging
- `execution/agents/rationale_agent.py` — Claude 3-phase AI analysis
- `webhook/bot/routers/callbacks.py` — All Telegram button callbacks
- `webhook/routes/mini_api.py` — Mini App REST API endpoints

**State Management:**
- `execution/curation/redis_client.py` — Redis operations (staging, dedup, seen)
- `execution/core/state_store.py` — Postgres state persistence
- `webhook/bot/config.py` — FSM storage (RedisStorage)

**Testing:**
- `tests/conftest.py` — Pytest fixtures (fake Redis, mocks)
- `pytest.ini` — Test configuration

## Naming Conventions

**Files:**
- Python: `snake_case.py` (e.g., `platts_ingestion.py`, `rationale_agent.py`)
- TypeScript/JavaScript: `camelCase.ts`, `camelCase.tsx`, `UPPERCASE.json` (e.g., `App.tsx`, `callback_data.py`)
- Directories: `kebab-case/` for actors (e.g., `platts-scrap-reports/`), `snake_case/` for Python (e.g., `execution/`)

**Directories:**
- Feature modules: `snake_case/` (e.g., `curation/`, `agents/`)
- Feature packages: `kebab-case/` for public-facing (e.g., `platts-scrap-reports/`)
- Classes: `PascalCase` in Python (e.g., `RationaleAgent`, `WorkflowLogger`)
- Functions/variables: `snake_case` in Python, `camelCase` in TypeScript

**Types:**
- Python dataclasses/enums: `PascalCase` (e.g., `State`, `RoleMiddleware`)
- TypeScript interfaces: `PascalCase` with `I` prefix optional (e.g., `WorkflowRun`, `NewsItem`)
- Telegram CallbackData: `PascalCase` (e.g., `CurateAction`, `DraftAction`)

## Where to Add New Code

**New Feature (Telegram Command):**
- Handler: `webhook/bot/routers/*.py` (or new router file)
- Callback data: `webhook/bot/callback_data.py`
- FSM state: `webhook/bot/states.py` (if stateful)
- Keyboard builder: `webhook/bot/keyboards.py` (if button-heavy)
- Tests: `tests/test_*.py` (matching handler name)

**New Execution Script:**
- Location: `execution/scripts/*.py`
- Pattern: Parse args, init logger, wrap main logic in try-catch, log results
- Register cron in `.github/workflows/` or `railway.json`
- Tests: `tests/test_*.py`

**New Integration (External API):**
- Location: `execution/integrations/*.py`
- Pattern: Single class (e.g., `NewServiceClient`), async/sync methods, comprehensive error handling
- Add to `execution/integrations/__init__.py` exports
- Tests: `tests/test_*.py`

**New Mini App Page:**
- Component: `webhook/mini-app/src/pages/NewPage.tsx`
- Add to `App.tsx` routing (lazy import)
- API endpoint: `webhook/routes/mini_api.py` (/api/mini/new-feature)
- Tests: `webhook/mini-app/src/__tests__/*.test.ts` (if unit tests exist)

**New Dashboard Page:**
- Route: `dashboard/app/new-feature/page.tsx` (or layout structure)
- API: `dashboard/app/api/new-feature/route.ts` (if needed)
- Components: `dashboard/components/new-feature/*.tsx`

**Utilities:**
- Shared Python: `execution/core/*.py`
- Shared TypeScript: `webhook/mini-app/src/lib/*.ts` or `dashboard/lib/`
- Prompts: `execution/core/prompts/` or `directives/_templates/`

## Special Directories

**`.state/`:**
- Purpose: Persistent state files (dedup caches, workflow metadata)
- Generated: Yes (by execution scripts)
- Committed: No (gitignored)

**.tmp/logs/:**
- Purpose: Runtime logs organized by module (WorkflowLogger output)
- Generated: Yes (by execution scripts and webhook)
- Committed: No (gitignored)

**.worktrees/:**
- Purpose: Git worktrees for parallel feature branches
- Generated: Yes (git worktree add)
- Committed: No (gitignored)

**actors/.investigation/:**
- Purpose: Research actors and proof-of-concepts
- Generated: No
- Committed: Yes (reference)

**directives/_templates/:**
- Purpose: Claude prompt templates (jinja2 format)
- Generated: No
- Committed: Yes (reference for agents)

**docs/superpowers/:**
- Purpose: Phase implementation plans and design specs
- Generated: Yes (by planning agent)
- Committed: Yes (docs)

**.planning/codebase/:**
- Purpose: Architecture analysis documents
- Generated: Yes (by gsd:map-codebase)
- Committed: Yes (docs)

## Test Location Convention

Tests are **co-located** at root `tests/` directory:
- `tests/test_curation_router.py` — Tests for `execution/curation/router.py`
- `tests/test_rationale_agent.py` — Tests for `execution/agents/rationale_agent.py`
- `tests/conftest.py` — Shared fixtures (FakeRedis, mock Supabase, etc.)

Pattern: `tests/test_{module_name}_{class_or_function}.py`

Run tests:
```bash
pytest                  # All tests
pytest -v --cov        # With coverage
pytest tests/test_routing.py::test_function_name  # Single test
```

---

*Structure analysis: 2026-04-17*
