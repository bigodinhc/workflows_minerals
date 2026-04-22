# Technology Stack

**Analysis Date:** 2026-04-22

## Languages

**Primary:**
- Python 3.12.9 - Main execution layer, bot handlers, integrations
- Python 3.11 - Docker base image for webhook/bot server
- TypeScript 5 - Dashboard frontend (Next.js)
- JavaScript - Mini app frontend (React), Apify actors

**Secondary:**
- SQL - Supabase migrations and stored functions
- YAML - GitHub Actions workflow definitions

## Runtime

**Environment:**
- Python 3.11-slim (Docker production) / 3.12.9 (local development)
- Node.js 20-slim (Docker, for mini-app frontend build)
- Next.js 16.1.6 (frontend server)

**Package Manager:**
- pip (Python) with requirements.txt files
- npm (Node.js dependencies)
- No uv.lock detected; project uses pip only

## Frameworks

**Core:**
- aiogram 3.4.0-<4.0 - Telegram bot framework with FSM, webhook support
- aiohttp 3.9.0-<4.0 - Async HTTP server and client for webhook, dispatch
- aiohttp-jinja2 1.6-<2.0 - Jinja2 template rendering for webhook routes
- Next.js 16.1.6 - Dashboard frontend (React 19.2.3)

**Testing:**
- pytest 7.0.0+ - Python unit tests
- pytest-mock 3.10.0+ - Mocking library
- pytest-asyncio 0.21-<1.0 - Async test support

**Build/Dev:**
- Dockerfile with multi-stage build (Node frontend → Python runtime)
- GitHub Actions - CI/CD orchestration
- gunicorn (in production deployments)
- ESLint 9 - TypeScript linting

## Key Dependencies

**Critical:**
- supabase 2.0.0-<3.0 - Supabase Python client (contacts, event_log tables)
- phonenumbers 8.13-<9.0 - Phone number parsing/validation (E.164 normalization for WhatsApp)
- anthropic 0.40.0+ - Claude API integration (async + sync)
- aiohttp (async HTTP) + requests (sync HTTP) - External API calls
- redis 5.0-<6.0 - Async/sync cache for idempotency, session storage
- redis (via aiogram.fsm.storage) - Distributed FSM state storage

**Infrastructure & Observability:**
- sentry-sdk[aiohttp] 2.0.0-<3.0.0 - Error tracking (optional, configured via SENTRY_DSN)
- prometheus-client 0.20.0-<1.0.0 - Metrics collection
- structlog 20.0.0+ - Structured logging
- pyyaml 6.0-<7.0 - YAML config parsing

**Data Processing:**
- pandas 2.0.0+ - Data manipulation (LSEG futures, price data)
- lseg-data 1.0.0 - LSEG (Refinitiv) data platform SDK
- spgci 0.0.70 - S&P Global Platts Commodity Insights API
- apify-client 1.0.0+ - Apify web scraping orchestration

**External APIs & Auth:**
- google-auth 2.0.0+ - Google OAuth2 authentication
- google-api-python-client 2.0.0+ - Google Sheets/Slides API (used in legacy code, partially replaced by Supabase)
- gspread 5.10.0+ - Google Sheets client (legacy, being migrated to Supabase)
- msal 1.31.0 - Microsoft authentication library (potential future OAuth)

**Frontend (Dashboard):**
- googleapis 171.2.0 - Google APIs for TypeScript dashboard
- octokit 5.0.5 - GitHub API client for workflow management
- radix-ui 1.4.3 - Headless UI components
- lucide-react 0.563.0 - Icon library
- framer-motion 12.31.0 - Animation library
- swr 2.4.0 - SWR data fetching hooks
- tailwindcss 4.1.18 - Utility CSS framework
- clsx 2.1.1 - Conditional CSS class management

**Testing & Development:**
- pytest 7.0.0+ - Testing framework
- pytest-mock 3.10.0+ - Mocking utilities
- pytest-asyncio 0.21-<1.0 - Async test support
- fakeredis 2.20-<3.0 - Redis mock for tests
- croniter 2.0-<3.0 - Cron expression parsing

## Configuration

**Environment:**
- `.env` file (not committed) - Runtime secrets and configuration
- `.env.example` - Template documenting required variables
- Supabase credentials: `SUPABASE_URL`, `SUPABASE_KEY`
- Telegram bot: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Uazapi/WhatsApp: `UAZAPI_TOKEN`, `UAZAPI_URL` (defaults to https://mineralstrading.uazapi.com)
- Redis: `REDIS_URL`
- LSEG Platform: `LSEG_APP_KEY`, `LSEG_USERNAME`, `LSEG_PASSWORD`
- Apify: `APIFY_API_TOKEN`
- Anthropic: `ANTHROPIC_API_KEY`
- Sentry: `SENTRY_DSN` (optional)
- GitHub Actions: `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`
- Port: `PORT` (default 8080 for webhook/bot server)

**Build:**
- `webhook/pyproject.toml` - Webhook package metadata (Railway deployment config)
- `webhook/requirements.txt` - Webhook dependencies (pinned versions)
- `root/requirements.txt` - Execution/integration dependencies
- `dashboard/next.config.ts` - Next.js configuration
- `dashboard/package.json` - Frontend dependencies
- `Dockerfile` - Multi-stage production build (Node frontend + Python runtime)

## Platform Requirements

**Development:**
- Python 3.11+ (3.12.9 tested)
- Node.js 20+ (for dashboard/mini-app build)
- Redis instance (local or remote via `REDIS_URL`)
- Supabase project (PostgreSQL-backed)
- Git (for GitHub Actions workflows)

**Production:**
- Docker/Kubernetes runtime (image built from `Dockerfile`)
- Railway platform (indicated by `[tool.railway]` in `webhook/pyproject.toml`)
- GitHub Actions (for scheduled workflows: daily_report, morning_check, platts_reports, baltic_ingestion, market_news)
- Redis cluster/instance (async FSM storage, idempotency cache)
- Supabase hosted PostgreSQL (tables: contacts, event_log, sgx_prices)

---

*Stack analysis: 2026-04-22*
