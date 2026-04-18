# External Integrations

**Analysis Date:** 2026-04-17

## Telegram Bot API

**Purpose:** Automated workflow orchestration for news curation, approvals, report navigation, and broadcast messaging.

**Implementation:**
- Framework: Aiogram 3.4+ (async webhook-based)
- Webhook Handler: `webhook/bot/main.py` — Receives updates from Telegram Bot API
- Routes: `webhook/bot/routers/` — Commands, callbacks, message handlers, settings
- Auth: Token in `TELEGRAM_BOT_TOKEN` env var
- Webhook Registration: POST `/admin/register-commands` endpoint registers commands with Telegram

**Key Features:**
- Inline keyboards for approval workflows (`UserApproval`, `BroadcastConfirm` callback data)
- FSM (Finite State Machine) for multi-step interactions (`AddContact`, `RejectReason`, `BroadcastMessage` states)
- RedisStorage for persistent conversation state (via `webhook/bot/config.py:get_storage()`)
- Parse mode: Markdown (configured in `DefaultBotProperties`)

**Environment Variables:**
- `TELEGRAM_BOT_TOKEN` - Bot token
- `TELEGRAM_CHAT_ID` - Primary workflow chat ID
- `TELEGRAM_CHAT_ID_BALTIC` - Optional separate chat for Baltic ingestion
- `TELEGRAM_WEBHOOK_URL` - Full webhook URL for Telegram endpoint registration

**Related Files:**
- `webhook/bot/config.py` — Bot, dispatcher, and storage initialization
- `webhook/bot/routers/` — All handler implementations
- `webhook/contact_admin.py` — Message formatting for admin notifications
- `webhook/routes/api.py:@routes.post("/admin/register-commands")` — Command registration

---

## Claude AI (Anthropic)

**Purpose:** 3-agent pipeline for iron ore market news processing (Writer → Critique → Curator) and structured data extraction.

**Implementation:**

### News Pipeline (Async):
- Location: `webhook/pipeline.py`
- Function: `call_claude(system_prompt, user_prompt)` — Async wrapper using `AsyncAnthropic`
- Model: `claude-sonnet-4-6`
- Used by: `webhook/bot/routers/callbacks.py` (approve/adjust) and `webhook/dispatch.py` (broadcast)
- Workers:
  - **Writer** — Draft WhatsApp message from raw content
  - **Critique** — Feedback on tone, accuracy, compliance
  - **Curator** — Final version for broadcast (incorporates feedback)

### Apify Actor PDF Extraction:
- Location: `execution/integrations/claude_client.py:ClaudeClient.extract_data_from_pdf()`
- Model: `claude-sonnet-4-6`
- Document Type: PDF with base64 encoding
- Purpose: Extract financial data (routes, indices, prices) from Baltic Exchange reports
- Prompt: Custom system prompt for financial data extraction with JSON schema validation

**Environment Variables:**
- `ANTHROPIC_API_KEY` - Claude API key

**Related Files:**
- `webhook/pipeline.py` — Main pipeline orchestration
- `execution/integrations/claude_client.py` — ClaudeClient wrapper for PDF extraction
- `webhook/bot/routers/messages.py:AdjustDraft` handler — Calls Claude for message adjustment
- `webhook/dispatch.py:WhatsAppDispatcher` — Calls pipeline for broadcast

---

## Supabase (PostgreSQL + File Storage)

**Purpose:** Persistent storage for Platts reports PDFs, metadata, navigation state, and database backend.

**Implementation:**

### Database (PostgreSQL):
- Tables (inferred from queries):
  - Reports metadata (report date, filename, download URL, etc.)
  - User preferences and settings
- Location: Queries in `webhook/reports_nav.py` and `execution/integrations/supabase_client.py`
- Client: `supabase>=2.0.0` Python SDK

### File Storage:
- Bucket: Used for storing downloaded Platts reports PDFs
- Upload: `webhook/reports_nav.py:upload_report_to_storage()` — Stores PDFs with metadata
- Download: Navigation endpoints retrieve signed download URLs

**Environment Variables:**
- `SUPABASE_URL` - Project URL
- `SUPABASE_KEY` - Anon key (read-only client)
- `SUPABASE_SERVICE_ROLE_KEY` - Service role key for Apify actor uploads (full permissions)

**Related Files:**
- `webhook/reports_nav.py` — Report navigation, storage uploads, and metadata queries
- `execution/integrations/supabase_client.py` — Supabase client wrapper (basic initialization)
- `actors/platts-scrap-reports/src/storage/` — Actor-side Supabase integration (JavaScript)

---

## Redis

**Purpose:** Curation pipeline state management, staging/archive dedup, FSM storage for Telegram conversations, and session management.

**Implementation:**

### Keyspaces:
- `platts:staging:<id>` — JSON string, TTL 48h — Staged items awaiting approval
- `platts:archive:<date>:<id>` — JSON string, no TTL — Approved/archived items (consumed by other workflows)
- `platts:seen` — Sorted Set, score=epoch, rolling 30d — Dedup by article hash
- `platts:scraped:<date>` — Set, TTL 30d — Daily telemetry of scraped article IDs
- `platts:rationale:processed:<date>` — String flag, TTL 30h — 1x/day gate for Anthropic processing

### FSM Storage:
- Location: `webhook/bot/config.py:get_storage()` creates `RedisStorage.from_url(REDIS_URL)`
- Purpose: Persists conversation states across restarts (callbacks, multi-step forms)

**Environment Variables:**
- `REDIS_URL` - Full connection string (e.g., `redis://user:pass@host:port/db`)

**Related Files:**
- `execution/curation/redis_client.py` — Core curation state operations
- `webhook/redis_queries.py` — Read-side queries for UI (list_staging, list_archive, list_feedback, stats_for_date)
- `webhook/bot/config.py:get_storage()` — FSM storage initialization
- `webhook/bot/routers/callbacks.py` — Interaction with curation Redis keyspaces
- Tests: `tests/curation/test_redis_client.py` — Uses fakeredis for isolation

---

## Apify Actors

**Purpose:** Distributed web scraping of commodity pricing and research reports with headless browser automation.

**Implementation:**

### Apify SDK Integration:
- Client: `execution/integrations/apify_client.py:ApifyClient`
- Method: Async `run_actor(actor_id, run_input, memory_mbytes, timeout_secs)` → returns dataset_id
- Results: Downloaded via Apify dataset API into local JSON for processing

### Actors (Locations in `actors/` directory):

1. **`platts-scrap-full-news`** (Node.js Crawlee/Playwright)
   - Scrapes Platts Connect portal (news + prices)
   - Extracts: Flash news, Top News, Latest, News Insights, RMW (Rotating Market Watch)
   - Called by: `execution/scripts/platts_ingestion.py`
   - Actor ID env var: `APIFY_PLATTS_ACTOR_ID` (default: `bigodeio05/platts-scrap-full-news`)
   - Frequency: 3x/day (9h, 12h, 15h BRT) via GitHub Actions

2. **`platts-scrap-reports`** (Node.js Crawlee/Playwright + Supabase)
   - Scrapes Platts Market & Research Reports grids
   - Downloads PDFs → uploads to Supabase Storage + sends to Telegram
   - Dependencies: Playwright, Crawlee, @supabase/supabase-js
   - Called by: `execution/scripts/platts_reports.py`
   - Actor ID env var: `APIFY_PLATTS_REPORTS_ACTOR_ID` (default: `bigodeio05/platts-scrap-reports`)
   - Frequency: Daily at 20h BRT via GitHub Actions
   - Dedup: Redis keyspace `platts:report:seen:<slug>:<date>` (TTL 90d)

3. **`platts-scrap-price`** (Node.js Crawlee/Playwright)
   - Scrapes Platts price data
   - Extracts commodity prices with historical comparisons

4. **`platts-news-only`** (Node.js Crawlee/Playwright)
   - News-only variant of full scraper

**Environment Variables:**
- `APIFY_API_TOKEN` - Platform authentication token
- `APIFY_PLATTS_ACTOR_ID` - Actor ID for price/news (default provided)
- `APIFY_PLATTS_REPORTS_ACTOR_ID` - Actor ID for reports PDFs (default provided)

**Related Files:**
- `execution/integrations/apify_client.py` — ApifyClient class for orchestration
- `execution/scripts/platts_ingestion.py` — Triggers `platts-scrap-full-news` actor and processes output
- `execution/scripts/platts_reports.py` — Triggers `platts-scrap-reports` actor
- `execution/scripts/inspect_platts.py` — Debug script to inspect actor dataset output

---

## Google Sheets API

**Purpose:** Curation approvals workflow and WhatsApp contact list management.

**Implementation:**
- Client: `gspread>=5.10.0` + `google-auth>=2.0.0`
- Wrapper: `execution/integrations/sheets_client.py:SheetsClient`
- Sheet ID: Hardcoded in `webhook/bot/config.py:SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"`
- Auth: Service account credentials via `GOOGLE_CREDENTIALS_JSON` env var (base64-encoded JSON)

**Features:**
- SCOPES: `https://www.googleapis.com/auth/spreadsheets`, `https://www.googleapis.com/auth/drive`
- Used for: Contact list (WhatsApp recipients), approval workflow logging, decision tracking
- Async wrapper: `await asyncio.to_thread(sheets_client.method_name())` in bot handlers

**Environment Variables:**
- `GOOGLE_CREDENTIALS_JSON` - Base64-encoded service account JSON
- `GOOGLE_CREDENTIALS_PATH` - Fallback local path for development (default: `credentials.json`)

**Related Files:**
- `execution/integrations/sheets_client.py` — SheetsClient with append/read operations
- `webhook/bot/routers/callbacks.py` — Records approvals to Sheets
- `webhook/dispatch.py:_fetch_whatsapp_contacts()` — Reads contact list from Sheets

---

## Uazapi (WhatsApp Cloud API)

**Purpose:** Send approved messages to WhatsApp contacts (broadcast and direct send).

**Implementation:**
- Client: Direct HTTP requests (no SDK)
- Endpoint: `os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")`
- Authentication: Bearer token in `UAZAPI_TOKEN` env var
- Protocol: Async HTTP POST to `/messages` or `/send` endpoint (exact path depends on API version)

**Features:**
- Sends formatted messages to contact phone numbers
- Progress reporting via Telegram callback updates (`DeliveryReporter`)
- Error handling with retry logic
- Input: Phone number (cleaned of `whatsapp:` prefix), message text, media (if applicable)

**Environment Variables:**
- `UAZAPI_URL` - API base URL
- `UAZAPI_TOKEN` - Bearer token for authentication

**Related Files:**
- `webhook/dispatch.py:send_whatsapp()` — Core send function
- `webhook/dispatch.py:process_whatsapp_dispatch()` — Batch sending with progress tracking
- `webhook/bot/routers/callbacks.py` — Triggers WhatsApp broadcast from approval callbacks

---

## S&P Global Platts (Commodity Intelligence)

**Purpose:** Real-time commodity pricing data (iron ore, freight indices).

**Implementation:**
- SDK: `spgci>=0.0.70` (S&P Global Commodities Intelligence)
- Wrapper: `execution/integrations/platts_client.py:PlattsClient`
- Auth: Implicit via network credentials or API key (configured at platform level)

**Data Accessed:**
- Iron ore fines prices (62%, 65% Fe CFR China)
- Specialty products (pellets, lump ore)
- VIU differentials (alumina, phosphorus, silica)
- Rotating Market Watch (RMW) summaries

**Related Files:**
- `execution/integrations/platts_client.py` — PlattsClient with detailed symbol mappings and query methods

---

## LSEG (Refinitiv) Data Platform

**Purpose:** Shipping indices (Baltic Exchange rates) and maritime data.

**Implementation:**
- SDK: `lseg-data>=1.0.0`
- Wrapper: `execution/integrations/lseg_client.py:LSEGClient`
- Auth: App key, username, password via env vars
- Config: Temporary JSON config file created at runtime (`.tmp/lseg-config.json`)

**Environment Variables:**
- `LSEG_APP_KEY` - Application key
- `LSEG_USERNAME` - Username
- `LSEG_PASSWORD` - Password

**Related Files:**
- `execution/integrations/lseg_client.py` — LSEGClient for session management and queries

---

## Baltic Exchange (Email Ingestion via Azure)

**Purpose:** Automated ingestion of Baltic Exchange daily reports via email.

**Implementation:**
- Auth Provider: Microsoft Azure AD (MSAL)
- SDK: `msal>=1.31.0`
- Protocol: Microsoft Graph API (O365 mailbox access)
- Wrapper: `execution/integrations/baltic_client.py:BalticClient`

**Features:**
- Queries mailbox for emails from `DailyReports@midship.com` with keyword filters
- Retrieves attachment bytes for PDF processing
- Time window: Last 24 hours

**Environment Variables:**
- `AZURE_TENANT_ID` - Azure AD tenant ID
- `AZURE_CLIENT_ID` - Registered application client ID
- `AZURE_CLIENT_SECRET` - Application secret
- `AZURE_TARGET_MAILBOX` - Target mailbox email or ID

**Related Files:**
- `execution/integrations/baltic_client.py` — BalticClient for email retrieval
- `execution/scripts/baltic_ingestion.py` — Script to fetch, extract, and store Baltic data

---

## GitHub Actions

**Purpose:** Scheduled workflows for data ingestion pipelines and health checks.

**Workflows (`.github/workflows/`):**

1. **`market_news.yml`** — Platts market news ingestion
   - Trigger: Cron 0 12, 15, 18 UTC (9h, 12h, 15h BRT on weekdays)
   - Steps: Checkout → Setup Python → Install dependencies → Run `platts_ingestion.py`
   - Outputs: Dataset → Dedup → Rationale AI or Telegram curation

2. **`platts_reports.yml`** — Platts reports PDF scraping
   - Trigger: Cron 0 23 UTC (20h BRT daily)
   - Steps: Checkout → Run Apify actor → Upload to Supabase → Send to Telegram

3. **`baltic_ingestion.yml`** — Baltic Exchange report ingestion
   - Trigger: Cron 0 06 UTC daily
   - Steps: Fetch email → Extract PDF → Claude extraction → Store results

4. **`morning_check.yml`** — Health check
   - Trigger: Cron daily
   - Steps: Ping health endpoints, verify service connectivity

5. **`daily_report.yml`** — Summary report generation
   - Trigger: Cron daily
   - Steps: Aggregate daily stats → Send to Telegram

**Environment Secrets (GitHub Settings):**
- All env vars listed above stored as GitHub Actions secrets (accessed via `${{ secrets.VAR_NAME }}`)

**Related Files:**
- `.github/workflows/*.yml` — Workflow definitions

---

## Webhooks & Incoming Integrations

**Telegram Bot Webhook:**
- Endpoint: `/webhook` (configured in `webhook/bot/main.py`)
- Path: `webhook/bot/handlers.py` → Router
- Setup: POST `/admin/register-commands` to register webhook URL with Telegram

**HTTP API Endpoints (aiohttp routes in `webhook/routes/api.py`):**
- `GET /health` — Health check (no auth)
- `GET /test-ai` — Anthropic connectivity test
- `POST /admin/register-commands` — Register Telegram commands
- `POST /store-draft` — GitHub Actions → store draft for approval
- `GET/POST /seen-articles` — GitHub Actions → dedup tracking

---

## Cron Schedules & Observability

**Execution Logs:**
- Stored in: `.state/` directory (local; not committed)
- Structured Logging: `execution/core/logger.py:WorkflowLogger` with JSON output
- Failures: Reported to Telegram chat (`TELEGRAM_CHAT_ID`) via `execution/core/state_store.py`

**State Recovery:**
- Redis: Persistent staging/archive state (survives restarts)
- Sheets: Approval history (human audit trail)

---

## Data Flow Summary

```
Platts Portal
    ↓ [Apify Actor: platts-scrap-full-news]
Apify Dataset
    ↓ [execution/scripts/platts_ingestion.py]
Redis staging + Seen dedup
    ↓ [Manual approval OR Rationale AI]
Telegram curation workflow (approve/adjust/reject)
    ↓ [3-agent pipeline: Writer → Critique → Curator]
Redis archive + Google Sheets audit
    ↓ [Broadcast dispatch]
WhatsApp (Uazapi) + Telegram
```

---

*Integration audit: 2026-04-17*
