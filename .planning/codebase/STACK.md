# Technology Stack

**Analysis Date:** 2026-04-22

Multi-language agentic workflows platform: Python execution layer (GitHub-Actions crons + Railway-hosted Telegram bot), Node.js/TypeScript Next.js dashboard, JavaScript Apify actors, and Supabase (Postgres + Storage) as backend. Redis (Upstash) is now a load-bearing dependency for workflow state, curation staging, and bot queue selection mode.

## Languages

**Primary:**
- Python (crons + bot + mini_api + agents) — pinned to **3.10** in GitHub Actions (`.github/workflows/*.yml`), **3.11-slim** in the Railway Docker image (`Dockerfile:10`). `webhook/pyproject.toml` declares `requires-python = ">=3.9"` but runtime targets are 3.10/3.11.
- TypeScript — dashboard (`dashboard/tsconfig.json`, `target: ES2017`, `strict: true`, `module: esnext`) and Telegram Mini App (`webhook/mini-app/tsconfig.json`).
- JavaScript (ES modules, `"type": "module"`) — all four Apify actors under `actors/*/src/main.js`.

**Secondary:**
- SQL — Supabase migrations split across `supabase/migrations/` (canonical) and `execution/supabase/migrations/` (event_log idempotent schema).
- Jinja2 HTML — `webhook/templates/preview.html`.

## Runtime

**Python:**
- GitHub Actions jobs pin `python-version: '3.10'` via `actions/setup-python@v4` (see `.github/workflows/baltic_ingestion.yml:30`, `morning_check.yml:38`, `daily_report.yml:28`, `market_news.yml:32`, `platts_reports.yml:30`, `watchdog.yml:18`).
- Railway webhook image runs `python:3.11-slim` (`Dockerfile:10`).
- Async stack: `asyncio` + `aiohttp` (aiogram webhook server under `aiohttp.web`). Sync code still dominates the cron scripts (`requests`-based clients).

**Node.js:**
- Dockerfile stage 1 (Mini App build) uses `node:20-slim` (`Dockerfile:2`).
- Apify actors: Apify-provided base image (not pinned in this repo; each actor has its own `Dockerfile`).
- Dashboard: Node ≥ 18 expected for Next 16; Vercel auto-provisions.

**Next.js:**
- `next@16.1.6`, `react@19.2.3`, `react-dom@19.2.3` — React 19 era (`dashboard/package.json:20-24`).
- Turbopack enabled for root (`dashboard/next.config.ts`).

**Docker base images:**
- Webhook: `node:20-slim` (frontend build) → `python:3.11-slim` (runtime) — multi-stage build at `/Dockerfile`.
- Actors: Apify base with Playwright browsers installed via `postinstall: npx crawlee install-playwright-browsers`.

## Package Managers

**Python:**
- `pip` via `requirements.txt` in both CI and Docker.
- **Local dev convention on this machine: use `uv` instead of `pip`** — system pip is broken here (Python 3.14 pyexpat dylib issue). This is per-machine, not enforced in CI.
- **Two requirements.txt files — keep them in sync:**
  - `/requirements.txt` — used by all GitHub Actions cron jobs (superset; includes `pandas`, `lseg-data`, `spgci`, `msal`, `anthropic`, `apify-client`, test libs).
  - `/webhook/requirements.txt` — used by Railway Docker build for the bot (lean subset: no test libs, no pandas, no msal, no spgci).
  - When adding a runtime dep that the bot needs at startup, add it to BOTH files. When adding a cron-only dep (like a new domain SDK), root only.
- No lockfile (no `requirements.lock` / `pip-compile` output); version ranges are `>=x,<y` style. Reproducibility relies on the resolver picking within the declared bounds.

**Node.js:**
- npm for all four Node trees (lockfiles present): `/package-lock.json`, `/dashboard/package-lock.json`, `/webhook/mini-app/package-lock.json`, `/actors/platts-scrap-reports/package-lock.json`, `/actors/platts-scrap-full-news/package-lock.json`.
- Root `/package.json` has a single dep: `apify-client@^2.22.0` (used by local scripts, not actor runtime).
- `platts-scrap-price` and `platts-news-only` actors carry `package.json` without a committed lockfile — recreated per build on Apify.

## Frameworks

**Telegram bot (`webhook/`):**
- `aiogram >=3.4.0,<4.0` — v3 routers + dispatcher + FSM (`webhook/bot/main.py:17`, `webhook/bot/config.py:8`).
- `aiohttp >=3.9.0,<4.0` — HTTP server hosting the aiogram webhook (`SimpleRequestHandler` at `webhook/bot/main.py:17`).
- `aiohttp-jinja2 >=1.6,<2.0` — preview template rendering (`webhook/templates/preview.html`).
- `aiogram.fsm.storage.redis.RedisStorage` — FSM state persisted to Redis (`webhook/bot/config.py:11,38`).

**Dashboard (`dashboard/`):**
- Next.js 16 App Router (`dashboard/app/**`), API routes under `dashboard/app/api/{contacts,delivery-report,logs,news,workflows}/route.ts`.
- `@supabase/supabase-js@^2.104.0` for Postgres reads.
- `swr@^2.4.0` for client-side data fetching.
- Tailwind CSS 4 (`tailwindcss@^4.1.18`, `@tailwindcss/postcss@^4`, PostCSS-only config at `dashboard/postcss.config.mjs`).
- `radix-ui@^1.4.3`, `lucide-react@^0.563.0`, `framer-motion@^12.31.0`, `class-variance-authority`, `clsx`, `tailwind-merge`.
- `octokit@^5.0.5` (GitHub API client for workflow_dispatch from dashboard).
- `googleapis@^171.2.0` is present in `dashboard/package.json:18` but has **no source-code usage** in `dashboard/app|lib|components` — carry-over from the Google Sheets era, candidate for removal.
- `@fontsource/jetbrains-mono@^5.2.8`, `date-fns@^4.1.0`.

**Mini App (`webhook/mini-app/`):**
- React 19 + Vite 6 + Vitest 3 (`webhook/mini-app/package.json`).
- Built in Dockerfile stage 1, output copied to `webhook/mini-app/dist/` and served by `webhook/routes/mini_static.py`.

**Apify actors (`actors/*/`):**
- `apify@^3.4.2`, `crawlee@^3.13.8`, `playwright@1.54.1` across all four actors.
- `platts-scrap-reports@0.2.0` and `platts-news-only` pull in `@supabase/supabase-js` (`^2.49.0` / `^2.104.0`) + `node-fetch@^3.3.2` (reports) for PDF upload to Storage and event_log inserts.
- Each actor carries its own copy of `src/lib/eventBus.js` — mirror of the Python `EventBus` (see `actors/platts-scrap-full-news/src/lib/eventBus.js:5-11`, `actors/platts-scrap-reports/src/lib/eventBus.js`). Apify package isolation prevents symlinking; changes must be mirrored across both copies in the same PR.

**Testing frameworks:**
- `pytest >=7.0.0` + `pytest-mock >=3.10.0` + `pytest-asyncio >=0.21,<1.0` (`requirements.txt:19-24`).
- `fakeredis >=2.20,<3.0` — drop-in for `redis` in tests (`tests/test_curation_redis_client.py`, `tests/test_dispatch_idempotency.py` uses both sync and `fakeredis.aioredis`).
- Vitest for actor tests (`platts-scrap-reports`, `platts-scrap-full-news`) and Mini App (`vitest@^3.0.0` + jsdom + testing-library).
- pytest config: `/pytest.ini` — `testpaths = tests`, `addopts = -v --tb=short --ignore=tests/archive`, `norecursedirs` excludes `tests/archive`, `.venv`, `node_modules`.

**Build/dev tooling:**
- Actors: `eslint@^9.29.0` + `@apify/eslint-config@^1.0.0` + `eslint-config-prettier@^10.1.5` + `prettier@^3.5.3`.
- Dashboard: `eslint@^9` + `eslint-config-next@16.1.6` via flat config (`dashboard/eslint.config.mjs` using `defineConfig` from `eslint/config`).
- Mini App: TypeScript 5 + Vite 6.
- No `ruff` / `black` / `mypy` configured for Python — style is not tooling-enforced. No pre-commit hooks configured.

## Key Dependencies

**HTTP & clients:**
- `requests >=2.28.0` — sync HTTP (clients, IronMarket POST in `baltic_ingestion.py:187`).
- `aiohttp >=3.9.0,<4.0` — async HTTP + webhook server.
- `apify-client >=1.0.0` (Python SDK, used by `execution/integrations/apify_client.py:2`) and `apify-client@^2.22.0` (root Node SDK).

**Validation / parsing:**
- `phonenumbers >=8.13,<9.0` — contact phone normalization (`execution/integrations/contacts_repo.py:17`).
- `pyyaml >=6.0,<7.0` — parsed by `execution/core/cron_parser.py` against `.github/workflows/*.yml` to recover cron expressions for watchdog expected-next-run calculation.
- `croniter >=2.0,<3.0` — expected-next-run computation for watchdog.
- No Pydantic / zod; `dataclasses` + manual validation are used instead (see `execution/integrations/contacts_repo.py`).

**Database / KV:**
- `supabase >=2.0.0,<3.0` (Python `supabase-py`) — all Python-side Supabase I/O (`execution/integrations/supabase_client.py:2`, `execution/core/event_bus.py:54`).
- `@supabase/supabase-js@^2.104.0` — dashboard + two actors.
- **`redis >=5.0,<6.0` (Python `redis-py` — the unified client that absorbed aioredis)** — canonical Redis client across the platform. Sync API used everywhere; `redis.asyncio` not used in production (only tests via `fakeredis.aioredis`). Patterns:
  - `execution/curation/redis_client.py:42-51` — **raises on missing/unreachable** (curation staging/archive is load-bearing; silent loss is worse than a crash). `redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3, decode_responses=True)`.
  - `execution/core/state_store.py:30-41` — **never raises**: no-ops silently if `REDIS_URL` unset or Redis unreachable (observability sinks must never break workflows). Adds `_client.ping()` on first connect.
  - `webhook/redis_queries.py:34-41` — bot read-side helpers (staging list, stats, feedback). Mirrors the curation constructor.
  - `webhook/queue_selection.py:16` — new bot queue select-mode state; reuses `execution.curation.redis_client._get_client()` so bulk archive/discard ops share the curation connection.
  - `webhook/bot/config.py:11,38` — aiogram FSM state via `RedisStorage.from_url(REDIS_URL)`.
- `fakeredis >=2.20,<3.0` — test-only (sync `fakeredis.FakeRedis` and async `fakeredis.aioredis.FakeRedis`).

**Observability:**
- `sentry-sdk[aiohttp] >=2.0.0,<3.0.0` — initialized per-script via `execution/core/sentry_init.py:init_sentry()` when `SENTRY_DSN` is set (`traces_sample_rate=0.1`, `environment=RAILWAY_ENVIRONMENT or "dev"`). Also auto-init'd by the `@with_event_bus` decorator (`execution/core/event_bus.py:354`).
- `prometheus-client >=0.20.0,<1.0.0` — webhook `/metrics` endpoint (`webhook/metrics.py:9`, `webhook/routes/api.py:18`).
- `structlog >=20.0.0` declared in `/requirements.txt:15` — **present but unused in `execution/` source** (the `WorkflowLogger` in `execution/core/logger.py` is hand-rolled, not structlog). Candidate for removal.

**Domain clients:**
- `spgci >=0.0.70` — S&P Global Commodity Insights Python SDK (`execution/integrations/platts_client.py:9`; used by the legacy morning_check path via `SPGCI_USERNAME`/`SPGCI_PASSWORD`).
- `lseg-data >=1.0.0` — Refinitiv LSEG Data Library (`execution/integrations/lseg_client.py:6`, daily SGX report).
- `msal >=1.31.0` — Microsoft Graph OAuth client-credentials flow for Baltic email ingestion (`execution/integrations/baltic_client.py:4,17`).
- `anthropic >=0.40.0` — Claude SDK for PDF extraction and the rationale agent (`execution/integrations/claude_client.py:5`, `execution/agents/rationale_agent.py`).
- `pandas >=2.0.0` — used by `lseg_client.py` and `platts_client.py` for tabular transforms. Deliberately not in `/webhook/requirements.txt` (bot never touches it).

## Configuration

**Environment variables (canonical, from `.github/workflows/*.yml` + `webhook/bot/config.py`):**
- **Supabase:** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (preferred), `SUPABASE_KEY` (legacy fallback still read in `event_bus._get_supabase_client`).
- **Redis (Upstash):** `REDIS_URL` — single URL for state_store, curation, bot FSM, bot queue selection, and actor PDF dedup.
- **Telegram:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (operator main chat), `TELEGRAM_EVENTS_CHANNEL_ID` (firehose), `TELEGRAM_WEBHOOK_URL`.
- **Apify:** `APIFY_API_TOKEN`, `APIFY_PLATTS_ACTOR_ID` (default `bigodeio05/platts-scrap-full-news`), `APIFY_PLATTS_REPORTS_ACTOR_ID` (default `bigodeio05/platts-scrap-reports`).
- **Platts / scraping:** `PLATTS_USERNAME`, `PLATTS_PASSWORD`.
- **LSEG:** `LSEG_APP_KEY`, `LSEG_USERNAME`, `LSEG_PASSWORD`.
- **S&P Platts legacy API:** `SPGCI_USERNAME`, `SPGCI_PASSWORD`.
- **Microsoft Graph (Baltic):** `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TARGET_MAILBOX`.
- **WhatsApp (UazAPI):** `UAZAPI_URL` (default `https://mineralstrading.uazapi.com`), `UAZAPI_TOKEN`.
- **IronMarket:** `IRONMARKET_API_KEY` (plus a hardcoded fallback in `execution/scripts/baltic_ingestion.py:41` — existing debt).
- **LLM:** `ANTHROPIC_API_KEY`.
- **Observability:** `SENTRY_DSN`, `RAILWAY_ENVIRONMENT` (Sentry env tag), `TRACE_ID` / `PARENT_RUN_ID` (consumed by `EventBus.__init__` for Phase 4 trace propagation).
- **Dashboard:** `DASHBOARD_BASE_URL` (default `https://workflows-minerals.vercel.app`, used in streak alerts), `GITHUB_TOKEN` (for Octokit calls from Vercel).

**Config files:**
- Root: `/pytest.ini`, `/Dockerfile`, `/railway.json` (Railway deploy — `DOCKERFILE` builder, `startCommand: python -m webhook.bot.main`, `restartPolicyMaxRetries: 10`).
- Dashboard: `/dashboard/next.config.ts`, `/dashboard/tsconfig.json`, `/dashboard/eslint.config.mjs`, `/dashboard/postcss.config.mjs`, `/dashboard/components.json`.
- Webhook: `/webhook/pyproject.toml` (metadata only — `name = "telegram-webhook"`, `version = "1.0.0"`, `requires-python = ">=3.9"`, includes a `[tool.railway]` stub that is unused by the current `railway.json`).
- Supabase: no `supabase/config.toml` in git (CLI state lives in untracked `supabase/.temp/`, ignored per commit `347b937`).

## Platform Requirements

**Development:**
- Python 3.10 or 3.11 (install with `uv pip install -r requirements.txt` on this machine).
- Node 20+ (actors + Mini App) / Node 18+ (dashboard).
- Redis instance or `fakeredis` for unit tests (`pytest -q` runs fully offline against `fakeredis`).
- Supabase project + service role key for integration work against `event_log` / `contacts` / `platts_reports`.

**Production:**
- **GitHub Actions** — runs all six cron workflows under `ubuntu-latest` (Python 3.10).
- **Railway** — hosts the Telegram webhook bot (Dockerfile build, `python -m webhook.bot.main`, `PORT=8080`, `restartPolicyMaxRetries: 10`).
- **Apify** — hosts all four scraper actors (`bigodeio05/platts-*`).
- **Vercel** — hosts the Next.js dashboard (`workflows-minerals.vercel.app`).
- **Upstash Redis** — single `REDIS_URL` serves state_store, curation, bot FSM, bot queue selection, and actor dedup. Confirmed Upstash per `docs/superpowers/specs/2026-04-14-redis-state-and-admin-ux-design.md:22`.
- **Supabase** — Postgres + Storage bucket `platts-reports` + service-role key for all write paths.

---

*Stack analysis: 2026-04-22*
