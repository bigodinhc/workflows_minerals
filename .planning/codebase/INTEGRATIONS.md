# External Integrations

**Analysis Date:** 2026-02-13

## APIs & External Services

**AI & Language Processing:**
- Claude API (Anthropic) - Used for news processing via 3-agent pipeline
  - SDK/Client: `anthropic` Python package v0.40+
  - Model: `claude-sonnet-4-20250514`
  - Auth: `ANTHROPIC_API_KEY` environment variable
  - Used in: `./webhook/app.py` (call_claude, run_3_agents functions)

**Messaging:**
- Telegram Bot API - Workflow and news approval system
  - SDK/Client: Native HTTP requests to `api.telegram.org`
  - Auth: `TELEGRAM_BOT_TOKEN` environment variable
  - Endpoints: answerCallbackQuery, sendMessage, editMessage
  - Used in: `./webhook/app.py` (telegram_api, send_telegram_message functions)

- WhatsApp (via Uazapi) - Message dispatch to contacts
  - SDK/Client: HTTP requests with token header
  - Auth: `UAZAPI_TOKEN` environment variable
  - URL: `UAZAPI_URL` (default: `https://mineralstrading.uazapi.com`)
  - Function: `./webhook/app.py` send_whatsapp()
  - Headers: `{"token": UAZAPI_TOKEN}`

**Workflow & Automation:**
- Apify - Actor execution and monitoring
  - SDK/Client: `apify-client` npm package v2.22.0
  - Auth: `APIFY_API_TOKEN` environment variable
  - Used in: Root `package.json` dependencies
  - Purpose: Web scraping and data extraction actors

- GitHub Actions - Workflow management and triggering
  - SDK/Client: `octokit` npm package v5.0.5
  - Auth: `GITHUB_TOKEN` environment variable
  - Repository: `bigodinhc/workflows_minerals`
  - Operations: Fetch workflow runs, trigger dispatches, fetch logs
  - Files: `./dashboard/app/api/workflows/route.ts`, `./dashboard/app/api/logs/route.ts`

## Data Storage

**Databases:**
- Supabase - Mentioned in requirements but not actively used in code
  - Package: `supabase>=2.0.0` in `requirements.txt`
  - Connection: Via environment variable configuration
  - Client: Python supabase library

**Google Workspace Services:**
- Google Sheets
  - Connection: `GOOGLE_CREDENTIALS_JSON` (service account)
  - Client: `googleapis` npm package v171.2.0 and `gspread` Python v5.10+
  - Scope: `https://www.googleapis.com/auth/spreadsheets.readonly`
  - Sheet ID: `1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0`
  - Used in: `./dashboard/app/api/contacts/route.ts` (GET contacts)
  - Used in: `./webhook/app.py` get_contacts() function

- Google Drive - Implicit via googleapis for document access
  - Auth: Same service account as Sheets
  - Client: `googleapis` npm package

- Gmail - Implicit via googleapis (potential for email notifications)
  - Auth: Same service account

**File Storage:**
- Local filesystem only - No external blob storage configured

**Caching:**
- In-memory dictionary - Flask app uses `DRAFTS` and `ADJUST_STATE` dicts
- SWR client-side caching - Dashboard uses SWR for HTTP caching with `refreshInterval` set to 5-10 seconds
- No dedicated caching service (Redis, Memcached)

## Authentication & Identity

**Auth Provider:**
- Custom service account-based authentication
  - Google: Service account with private key
  - GitHub: Personal access token
  - Telegram: Bot token
  - Anthropic: API key
  - WhatsApp (Uazapi): Token header
  - Apify: API token

**Implementation:**
- Environment variable-based secrets
- No OAuth/OpenID Connect flows
- Service accounts for backend-to-backend communication
- Single user/bot per service (no user identity management)

## Monitoring & Observability

**Error Tracking:**
- None detected - No Sentry, Rollbar, or DataDog integration

**Logs:**
- Python logging module - Flask app uses `logging.basicConfig(level=logging.INFO)`
- Console output via Next.js built-in logging
- No centralized logging (Datadog, Splunk, etc.)
- File: `./webhook/app.py` uses logger.info() and logger.error()

## CI/CD & Deployment

**Hosting:**
- Railway.app - Production deployment platform
  - Config: `./railway.json` with Dockerfile builder
  - Deployment: Automatic from git push
  - Restart policy: ON_FAILURE with max 10 retries
  - Command: `gunicorn app:app --bind 0.0.0.0:8080 --timeout 120`

**CI Pipeline:**
- GitHub Actions - Workflow orchestration
  - Workflows: `./github/workflows/` directory
  - Recent workflows: morning_check.yml, rationale_news.yml, daily_report.yml
  - Triggered: Manual, scheduled, or event-based

## Environment Configuration

**Required env vars:**
- `GOOGLE_CREDENTIALS_JSON` - Google service account (base64 or raw JSON)
- `ANTHROPIC_API_KEY` - Claude API key from Anthropic
- `TELEGRAM_BOT_TOKEN` - Telegram bot token from @BotFather
- `GITHUB_TOKEN` - GitHub personal access token with repo access
- `UAZAPI_TOKEN` - WhatsApp API token
- `APIFY_API_TOKEN` - Apify.com API token
- `PORT` - Server port (default 8080)

**Optional env vars:**
- `UAZAPI_URL` - WhatsApp API base URL (default: https://mineralstrading.uazapi.com)
- `DEBUG` - Debug mode (true/false)
- `AZURE_TENANT_ID` - Microsoft Graph (Outlook) - not currently used
- `AZURE_CLIENT_ID` - Microsoft Graph (Outlook) - not currently used
- `AZURE_CLIENT_SECRET` - Microsoft Graph (Outlook) - not currently used
- `AZURE_TARGET_MAILBOX` - Microsoft Graph (Outlook) - not currently used

**Secrets location:**
- `.env` file (local development - NOT committed)
- Railway.app environment variables (production)
- GitHub Actions secrets (for CI workflows)

## Webhooks & Callbacks

**Incoming:**
- `POST /webhook` - Main webhook endpoint in `./webhook/app.py`
  - Receives: Telegram updates, news dispatch requests, approval callbacks
  - Handlers: Multiple if-blocks checking update type
  - State: Uses in-memory `DRAFTS` dict to track draft lifecycle

- `POST /store-draft` - Store draft to Telegram in `./webhook/app.py`
  - Purpose: Cache draft message for approval/adjustment workflow

- `GET /health` - Health check endpoint in `./webhook/app.py`
  - Purpose: Railway deployment health checks

- `GET /test-ai` - Debug endpoint for testing AI agents in `./webhook/app.py`
  - Purpose: Development testing of Claude pipeline

**Outgoing:**
- GitHub API calls - Octokit requests to `api.github.com`
  - Endpoints: GET /repos/{owner}/{repo}/actions/runs, POST dispatches
  - Used in: `./dashboard/app/api/workflows/route.ts`

- Google Sheets API calls - Googleapis requests
  - Endpoints: spreadsheets.values.get() for reading contacts
  - Used in: `./dashboard/app/api/contacts/route.ts`

- Telegram Bot API calls - requests to `api.telegram.org`
  - Methods: sendMessage, editMessage, answerCallbackQuery
  - Used in: `./webhook/app.py`

- Anthropic Claude API calls - requests to `api.anthropic.com`
  - Endpoint: messages.create
  - Model: claude-sonnet-4-20250514
  - Used in: `./webhook/app.py` call_claude()

- WhatsApp API calls (Uazapi) - HTTP POST to `UAZAPI_URL`
  - Endpoint: Message sending
  - Used in: `./webhook/app.py` send_whatsapp()

---

*Integration audit: 2026-02-13*
