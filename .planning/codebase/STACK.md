# Technology Stack

**Analysis Date:** 2026-04-17

## Languages

**Primary:**
- Python 3.11 - Telegram webhook, execution scripts, integrations layer
- TypeScript 5.6 - Mini App (React) and Apify Actors (Node.js ESM)

**Secondary:**
- JavaScript (Node.js ES modules) - Apify Actors and Mini App build tools

## Runtime

**Environment:**
- Python 3.11 (production via `python:3.11-slim` Docker base)
- Node.js 20 (Apify Actors and Mini App frontend)

**Package Manager:**
- pip (Python) — `requirements.txt` at repo root and `webhook/requirements.txt`
- npm (Node.js) — lockfiles present for root, `webhook/mini-app/`, `dashboard/`, and each actor

## Frameworks

**Core Python:**
- Aiogram 3.4+ - Telegram bot framework (webhook-based, async)
- aiohttp 3.9+ - Web server for Telegram webhook handler and API routes
- aiohttp-jinja2 1.6+ - Template rendering for webhook routes

**Mini App Frontend:**
- React 19 - UI library
- Vite 6 - Fast build tool with esbuild
- Tailwind CSS 4 - Utility-first CSS
- TypeScript 5.6 - Type safety

**Dashboard:**
- Next.js 16.1.6 - Production React framework with App Router
- React 19.2.3
- Tailwind CSS 4.1.18 - Styling

**Apify Actors:**
- Crawlee 3.13.8 - Web scraping framework with Playwright integration
- Playwright 1.54.1 - Headless browser automation
- Apify SDK 3.4.2 - Apify platform integration

**Testing:**
- pytest 7.0+ - Python unit/integration testing
- Vitest 3.0+ - TypeScript/JavaScript testing (Mini App)
- @testing-library/react 16 - React component testing

## Key Dependencies

**Critical Python:**
- `aiogram` (3.4+) - Telegram bot framework — Load-bearing; all bot interactions depend on it
- `supabase-py` (2.0+) - Database and file storage client — Handles Platts report PDFs and metadata
- `redis` (5.0+) - Caching and state management — Critical for curation pipeline (staging/archive dedup)
- `anthropic` (0.40+) - Claude API for 3-agent news pipeline — Writer/Critique/Curator agents
- `apify-client` (1.0+) - Apify orchestration from Python — Triggers actors and retrieves datasets
- `aiohttp` (3.9+) - Async HTTP server for webhook handler — Direct dependency for web server
- `requests` (2.28+) - Synchronous HTTP client — Used by integration clients
- `gspread` (5.10+) - Google Sheets client — Curation approvals and contact list access
- `google-auth` (2.0+) - OAuth for Google APIs — Sheets and Drive authentication
- `pandas` (2.0+) - Data manipulation for report processing
- `pyyaml` (6.0+) - Configuration parsing
- `croniter` (2.0+) - Cron expression parsing for scheduled tasks

**Integration-Specific:**
- `spgci` (0.0.70) - Platts/S&P Global Commodities Intelligence API for price data
- `lseg-data` (1.0+) - LSEG (Refinitiv) data access for shipping indices
- `msal` (1.31+) - Microsoft Azure authentication for Baltic Exchange email ingestion

**Python Testing:**
- `pytest-mock` (3.10+) - Mocking for pytest
- `fakeredis` (2.20+) - In-memory Redis for testing (no external Redis needed in test env)
- `pytest-asyncio` (0.21+) - Async test support for aiogram handlers

**Node.js/Frontend:**
- `react` (19.0+) - React library for both Mini App and Dashboard
- `react-dom` (19.0+) - DOM rendering
- `@supabase/supabase-js` (2.49+) - Supabase client for JavaScript/TypeScript
- `swr` (2.4+) - Data fetching and caching (Mini App and Dashboard)
- `tailwindcss` (4+) - CSS utility framework
- `@vitejs/plugin-react` (4.3+) - Vite React integration
- `vitest` (3.0+) - Vitest test runner for components
- `jsdom` (26.0+) - DOM simulation for testing
- `typescript` (5.6+) - TypeScript compiler

**Apify Actor Dependencies:**
- `crawlee` (3.13.8) - Web scraping abstraction
- `playwright` (1.54.1) - Headless browser control
- `apify` (3.4.2) - Apify SDK for dataset/key-value storage
- `node-fetch` (3.3.2) - HTTP client (Actors)

## Configuration

**Environment Variables (see `.env.example`):**
- `TELEGRAM_BOT_TOKEN` - Telegram Bot API token
- `TELEGRAM_CHAT_ID` - Primary chat for workflow state notifications
- `REDIS_URL` - Redis connection string (production: Railway, dev: local/Docker)
- `ANTHROPIC_API_KEY` - Claude API key for 3-agent pipeline
- `APIFY_API_TOKEN` - Apify platform authentication
- `APIFY_PLATTS_ACTOR_ID` - Actor ID for Platts price/news scraping (default: `bigodeio05/platts-scrap-full-news`)
- `APIFY_PLATTS_REPORTS_ACTOR_ID` - Actor ID for Platts reports PDFs (default: `bigodeio05/platts-scrap-reports`)
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_KEY` - Supabase anon key (read-only)
- `SUPABASE_SERVICE_ROLE_KEY` - Supabase service role key (full access, used by reports actor)
- `GOOGLE_CREDENTIALS_JSON` - Base64-encoded Google service account JSON
- `UAZAPI_URL` - Uazapi base URL for WhatsApp sending (default: `https://mineralstrading.uazapi.com`)
- `UAZAPI_TOKEN` - Uazapi authentication token
- `LSEG_APP_KEY` - LSEG Platform app key
- `LSEG_USERNAME` - LSEG Platform username
- `LSEG_PASSWORD` - LSEG Platform password
- `AZURE_TENANT_ID` - Azure AD tenant for Baltic Exchange email
- `AZURE_CLIENT_ID` - Azure AD application ID
- `AZURE_CLIENT_SECRET` - Azure AD client secret
- `AZURE_TARGET_MAILBOX` - Target mailbox for Baltic Exchange email fetching
- `PORT` - aiohttp web server port (default: 8080)
- `TELEGRAM_WEBHOOK_URL` - Full webhook URL for Telegram Bot API registration
- `TELEGRAM_CHAT_ID_BALTIC` - Optional separate chat for Baltic ingestion

**File-Based Configuration:**
- `.env` - Runtime environment variables (not committed; see `.env.example`)
- `pyproject.toml` at `webhook/pyproject.toml` - Python project metadata and Railway build config
- `pytest.ini` - pytest configuration (testpaths: `tests/`, discovery patterns)

## Build & Development Tools

**Python Build:**
- pip for dependency management
- setuptools + wheel (via `pyproject.toml` build-backend)
- gunicorn - WSGI server (configured in Railway)

**Node.js/Frontend Build:**
- Vite 6.0 (Mini App) — Fast bundler with esbuild
- Next.js 16.1.6 (Dashboard) — Turbopack for bundling
- TypeScript compiler (`tsc`) — Type checking

**Linting/Formatting:**
- ESLint 9 (JavaScript/TypeScript) — Config: `.eslintrc.config.mjs` at dashboard and actor roots
- Prettier 3.5 (Actors) — Code formatting

**Testing:**
- pytest (Python) — Run: `pytest` in CI or locally
- Vitest (TypeScript) — Run: `npm test` or `npm run test:watch` in Mini App

## Docker & Containerization

**Dockerfile (Multi-Stage Build):**
- Stage 1: Node.js 20 slim — Build Mini App frontend with Vite
- Stage 2: Python 3.11 slim — Runtime with webhook bot
- Entrypoint: `python -m webhook.bot.main` → aiohttp web server on port 8080

**Container Details:**
- `EXPOSE 8080` - aiohttp server port
- `WORKDIR /app` - Application root
- Copies:
  - `webhook/` — Telegram bot and webhook handlers
  - `execution/` — Python scripts and integrations
  - `.github/workflows/` — Accessible for workflow logging
  - Built Mini App dist (`webhook/mini-app/dist/`) — Served by aiohttp

## Deployment

**Hosting Platform:**
- Railway — Production deployment (Node.js/Python multi-service)
- Configuration: `railway.json` specifies Dockerfile build and start command

**CI/CD Workflows (GitHub Actions):**
- `.github/workflows/market_news.yml` — Platts ingestion (3x/day: 9h, 12h, 15h BRT)
- `.github/workflows/platts_reports.yml` — Platts reports scraping (daily at 20h BRT)
- `.github/workflows/baltic_ingestion.yml` — Baltic Exchange report ingestion
- `.github/workflows/morning_check.yml` — Health check workflow
- `.github/workflows/daily_report.yml` — Daily summary report

**Cron Schedules (UTC):**
- Platts market news: 12, 15, 18 UTC (9h, 12h, 15h BRT weekdays)
- Platts reports: 23 UTC (20h BRT daily)
- Baltic: 06 UTC daily

## Platform-Specific Notes

**Python Environment:**
- No `.python-version` file detected; defaults to 3.11 per Dockerfile
- Virtual environment at `.venv/` (for local development)
- Dependencies frozen in `requirements.txt` (main) and `webhook/requirements.txt` (bot-specific)

**Node.js Versions:**
- Actors: Node 20 (ESM modules)
- Mini App: Node 20 (Vite dev server, build)
- Dashboard: Node 20 (Next.js build and runtime)

---

*Stack analysis: 2026-04-17*
