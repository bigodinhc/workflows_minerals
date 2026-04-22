# External Integrations

**Analysis Date:** 2026-04-22

## APIs & External Services

**WhatsApp / Messaging:**
- Uazapi (WhatsApp Business API) - Send broadcast messages to contacts
  - SDK/Client: `execution/integrations/uazapi_client.py`, `webhook/dispatch.py`
  - Auth: `UAZAPI_TOKEN` env var, `UAZAPI_URL` (defaults to https://mineralstrading.uazapi.com)
  - Flow: Phone numbers normalized to E.164 format (digits only, no +) via `phonenumbers` library
  - Idempotency: Redis-backed deduplication (24-hour window per phone+draft_id+message)
  - Status codes: 200 = sent, 4xx = validation error, 5xx = server error (retried via backoff)

**Financial Data:**
- LSEG Platform (Refinitiv) - Real-time commodity pricing (SGX Iron Ore futures)
  - SDK/Client: `execution/integrations/lseg_client.py`
  - Auth: `LSEG_APP_KEY`, `LSEG_USERNAME`, `LSEG_PASSWORD` env vars
  - Config: Temporary JSON config file (`.tmp/lseg-config.json`) created at runtime
  - RICs: `SZZF[MonthCode][YearCode]` (e.g., SZZFF6 for Feb 2026)
  - Data: TRDPRC_1, SETTLE, NETCHNG_1, PCTCHNG, EXPIR_DATE fields

- Platts Commodity Insights - Commodity reports and market data
  - SDK/Client: `execution/integrations/platts_client.py`
  - Auth: API credentials via environment (check existing implementation)
  - Used by: `execution/scripts/platts_reports.py`, `execution/scripts/platts_ingestion.py`

**Web Scraping & Automation:**
- Apify Platform - Actor-based web scraping and automation
  - SDK/Client: `execution/integrations/apify_client.py`
  - Auth: `APIFY_API_TOKEN` env var
  - Flow: Trigger actor → wait for completion → fetch dataset results
  - Dataset storage: Apify cloud storage with optional Supabase integration
  - Actors in project: `actors/platts-*.js` (news, full scrape, price, reports)

**AI & Content Generation:**
- Anthropic Claude API - Content generation, extraction, analysis
  - SDK/Client: `execution/integrations/claude_client.py`, `webhook/pipeline.py`
  - Auth: `ANTHROPIC_API_KEY` env var
  - Implementation: Both sync (Anthropic) and async (AsyncAnthropic) clients
  - Used by: Content extraction, data processing, news curation
  - Error handling: APIConnectionError, AuthenticationError, rate limit handling

## Data Storage

**Databases:**
- Supabase (PostgreSQL-backed)
  - Tables:
    - `contacts` - WhatsApp broadcast list (2026-04-22 migration from Google Sheets)
      - Columns: id (UUID), name, phone_raw, phone_uazapi, status (ativo|inativo), created_at, updated_at
      - Unique index: `contacts_phone_uazapi_uidx`
      - Trigger: Auto-updates `updated_at` on modification
      - RLS: Enabled, no policies (service_role only access)
    - `event_log` - Workflow observability timeline
      - Columns: id (bigserial), workflow, run_id, draft_id, level (info|warning|error), label, detail, context (JSONB), created_at
      - Indexes: draft_id, workflow+created_at, run_id
    - `sgx_prices` (assumed) - Historical SGX Iron Ore pricing (source: LSEG, via supabase_client.py)
  - Connection: `SUPABASE_URL`, `SUPABASE_KEY` env vars
  - Client: `supabase-py` (v2.0.0+)
  - Query style: PostgREST API via Python client (select, insert, update, etc.)
  - Repository pattern: `execution/integrations/contacts_repo.py` abstracts table operations

**File Storage:**
- Supabase Storage (if used by Apify for dataset uploads)
- Local filesystem: `.state/`, `.tmp/` directories for temporary data
- No explicit S3/cloud storage detected (Supabase storage would be primary)

**Caching:**
- Redis - Session, FSM state, idempotency cache
  - Connection: `REDIS_URL` env var (async + sync clients)
  - FSM Storage: `aiogram.fsm.storage.redis.RedisStorage` for Telegram bot state
  - Idempotency: Keys like `whatsapp:sent:<sha1>` with 24-hour TTL
  - Used by: `webhook/dispatch.py`, `webhook/bot/config.py`, `execution/curation/`

## Authentication & Identity

**Auth Providers:**
- Telegram Bot Token - `TELEGRAM_BOT_TOKEN` env var (managed by BotFather)
- Google OAuth2 - Legacy Google Sheets access (being replaced by Supabase)
  - Flow: `GOOGLE_CREDENTIALS_JSON` or `GOOGLE_CREDENTIALS_PATH` + `GOOGLE_TOKEN_PATH`
  - Scopes: sheets.readonly, slides (configurable)
  - Used by: Dashboard `app/api/contacts/route.ts` for Sheets API (legacy)
- Supabase Service Role Key - `SUPABASE_KEY` (programmatic access, bypasses RLS)
- GitHub Token - `GITHUB_TOKEN` for workflow status / action triggers

**Session Management:**
- FSM Storage via Redis (Aiogram) - Telegram user state machine (conversation context)
- No user login system detected; bot is admin-only with `TELEGRAM_CHAT_ID` whitelist

## Monitoring & Observability

**Error Tracking:**
- Sentry - Optional error tracking
  - Configuration: `SENTRY_DSN` env var (if set, enables collection)
  - Integration: `sentry_sdk[aiohttp]` in `webhook/bot/main.py`
  - Status: Currently empty DSN, monitoring disabled by default

**Logs:**
- Structured logging: `structlog` for JSON-formatted output
- Standard logger: Python `logging` module with `WorkflowLogger` wrapper
- Location: `execution/core/logger.py`
- Event bus: `execution/core/event_bus.py` for workflow event tracking (async context manager)

**Metrics:**
- Prometheus - Client library (`prometheus-client`) for custom metrics
- Location: `webhook/metrics.py` (specific metrics tracked)
- Consumption: Prometheus scraper expected to poll `/metrics` endpoint (if exposed)

## CI/CD & Deployment

**Hosting:**
- Railway platform - Primary deployment target for webhook/bot
  - Dockerfile build: Multi-stage (Node frontend compile → Python runtime)
  - Entry point: `python -m webhook.bot.main`
  - Port: `$PORT` env var (default 8080)
  - Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`

**CI Pipeline:**
- GitHub Actions - Scheduled workflow orchestration
  - Workflows: `daily_report.yml`, `morning_check.yml`, `platts_reports.yml`, `baltic_ingestion.yml`, `market_news.yml`, `watchdog.yml`
  - Triggers: Cron schedules (times in UTC, business hours BRT)
  - Environment: `ubuntu-latest`, Python 3.10 setup
  - Secrets: GitHub Actions secret store (SUPABASE_URL, SUPABASE_KEY, etc.)
  - Build/Test: `pip install -r requirements.txt`, then execute script

**Webhook Deployment:**
- Telegram webhook mode - aiohttp server + aiogram SimpleRequestHandler
  - URL: `TELEGRAM_WEBHOOK_URL` env var (must be HTTPS, publicly routable)
  - Path: `/webhook` (hardcoded in `webhook/bot/config.py`)
  - Method: Aiogram's `setup_application()` configures webhook route

## Environment Configuration

**Required env vars:**
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_KEY` - Supabase service role key (public-like, not a secret for read-only queries)
- `TELEGRAM_BOT_TOKEN` - Telegram bot token from BotFather
- `TELEGRAM_CHAT_ID` - Admin Telegram chat/user ID for notifications
- `TELEGRAM_WEBHOOK_URL` - Public HTTPS URL for Telegram callbacks (e.g., https://your-domain.com/webhook)
- `REDIS_URL` - Redis connection string (e.g., redis://user:pass@host:port)
- `UAZAPI_TOKEN` - Uazapi authentication token
- `UAZAPI_URL` - Uazapi base URL (defaults to https://mineralstrading.uazapi.com)
- `ANTHROPIC_API_KEY` - Claude API key
- `APIFY_API_TOKEN` - Apify platform API token
- `LSEG_APP_KEY`, `LSEG_USERNAME`, `LSEG_PASSWORD` - LSEG Platform credentials
- `SENTRY_DSN` - Optional; empty = disabled
- `PORT` - Webhook server port (default 8080)

**Secrets location:**
- GitHub Actions: Settings → Secrets and variables → Actions
- Railway: Project → Variables (encrypted at rest)
- Local dev: `.env` file (Git-ignored, never committed)
- Docker: Environment variables passed at runtime

## Webhooks & Callbacks

**Incoming:**
- Telegram webhook - POST `/webhook` receives `telegram.Update` JSON
  - Handler: `webhook/bot/routers/` modules (commands, messages, callbacks)
  - Callback types: Button presses (approve, reject, adjust, test-send), message handling, state transitions

**Outgoing:**
- Telegram sendMessage, editMessageText, answerCallbackQuery - via `TelegramClient`
- Uazapi /send/text - WhatsApp message dispatch (async via `dispatch.send_whatsapp()`)
- Google Sheets API - Legacy (being phased out)
- Apify - Actor status polling (blocking wait via `actor.call()`)
- Supabase PostgREST - CRUD operations on contacts, event_log tables

## Legacy Integrations (Being Migrated)

**Google Sheets:**
- File: `execution/integrations/sheets_client.py`
- Status: Deprecated; contacts functionality migrated to Supabase (2026-04-22)
- Legacy Sheet ID: `1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0` (hardcoded in `webhook/bot/config.py`)
- Consumer: Dashboard still reads via Google API (will switch to Supabase)
- Remaining: May still be used for non-contact data; check `execution/scripts/` for usage

**Remnants:**
- `gspread` library still in requirements.txt (kept for backward compatibility)
- Google API credentials parsing still functional but not primary path

---

*Integration audit: 2026-04-22*
