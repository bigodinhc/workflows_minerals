# Architecture

**Analysis Date:** 2026-04-17

## Pattern Overview

**Overall:** Modular monorepo (Python + TypeScript) with event-driven microservice-like boundaries.

**Key Characteristics:**
- **Backend:** Python execution layer (curation, agent orchestration) + aiohttp webhook server (Telegram bot + Mini App API)
- **Frontend:** React/TypeScript Mini App (Telegram Web App) + Next.js Dashboard (React)
- **Scrapers:** Apify Actor orchestration (JavaScript/Node.js) running in cloud, output → Supabase Storage + Postgres
- **Message Flow:** Webhook ingestion (Apify callbacks, GitHub Actions) → Redis staging → Curation classification → Telegram bot approval → WhatsApp broadcast
- **Persistence:** Supabase (Postgres tables + Storage buckets), Redis (FSM state, staging queues, dedup caches), Google Sheets (contact admin)

## Layers

**Apify Scraper Actors:**
- Purpose: Cloud-based web scrapers that extract Platts/TSI data (prices, news, reports)
- Location: `/actors/`
- Contains: JavaScript/Node.js actors (platts-scrap-price, platts-scrap-full-news, platts-scrap-reports, etc.)
- Depends on: Apify platform, Supabase Storage/Postgres, Telegram API (for notifications)
- Used by: Execution scripts via ApifyClient; output stored in Supabase

**Execution Layer (Python):**
- Purpose: Core business logic, scheduling, AI agent orchestration
- Location: `/execution/`
- Contains:
  - `scripts/` — Cron-triggered ingestion workflows (platts_ingestion.py, morning_check.py, send_daily_report.py)
  - `curation/` — Classification/routing of scraped data (router.py, id_gen.py, redis_client.py, telegram_poster.py)
  - `agents/` — Claude-based AI for market analysis (rationale_agent.py: 3-phase extraction → synthesis → localization)
  - `integrations/` — External API clients (supabase_client.py, sheets_client.py, apify_client.py, telegram_client.py, claude_client.py, etc.)
  - `core/` — State management, logging, utilities (state_store.py, progress_reporter.py, delivery_reporter.py, logger.py)
- Depends on: Supabase, Redis, Apify, Claude API, Google Sheets API, LSEG (Platts), Telegram API
- Used by: Webhook handlers, scheduled jobs

**Webhook Server (aiohttp + Aiogram):**
- Purpose: HTTP server handling Telegram webhook updates, Mini App API, and internal routes
- Location: `/webhook/bot/` (Telegram bot) + `/webhook/routes/` (API endpoints)
- Contains:
  - `bot/main.py` — Entry point (creates aiohttp app, registers routers)
  - `bot/config.py` — Environment/singletons (Bot, Dispatcher, Redis storage)
  - `bot/routers/` — Message handlers (commands, callbacks, messages, FSM states)
  - `bot/middlewares/` — Auth middleware (role-based access control)
  - `routes/` — HTTP endpoints (api.py for store-draft/seen-articles, mini_api.py for Mini App, preview.py for draft preview)
- Depends on: Aiogram (Telegram), Redis (FSM state), Execution layer, Supabase, Google Sheets
- Used by: Telegram users, Mini App frontend, GitHub Actions

**Telegram Mini App Frontend (React/TypeScript):**
- Purpose: In-Telegram web interface for browsing workflows, news, reports, contacts
- Location: `/webhook/mini-app/src/`
- Contains:
  - `App.tsx` — Main router and tab navigation
  - `pages/` — Workflows, News, NewsDetail, Reports, Contacts, More, Home
  - `components/` — Reusable UI (TabBar, Card, Skeleton, Buttons)
  - `hooks/` — Custom hooks (useNavigation, useAuth, etc.)
  - `lib/` — API client, utilities
- Depends on: Telegram Web App SDK (telegram.d.ts), aiohttp Mini App API (/api/mini/*)
- Used by: Telegram Mini App SDK

**Dashboard Frontend (Next.js/React):**
- Purpose: Admin dashboard for monitoring, delivery reports, contacts management, news curation
- Location: `/dashboard/`
- Contains:
  - `app/api/` — Server-side API routes (contacts, delivery-report, logs, news, workflows)
  - `app/` — Page routes (news, workflows, executions, contacts)
  - `components/` — UI sections (dashboard, delivery, layout, ui)
  - `lib/` — Utilities and API clients
- Depends on: Execution layer (for logs/state), Supabase, Google Sheets
- Used by: Admin web browser

## Data Flow

**Core Workflow: Scrape → Classify → Approve → Broadcast**

1. **Data Ingestion (Apify Actors)**
   - Actors scrape Platts/TSI websites on schedule
   - Output → Supabase Storage (PDF reports) + Postgres (structured data: news, prices)
   - Actor sends callback notification to webhook or logs to Telegram

2. **Execution Script Trigger (platts_ingestion.py)**
   - Cron or manual trigger via GitHub Actions
   - Fetches dataset from Apify via ApifyClient
   - Flattens nested actor output into article list

3. **Curation Classification (router.py)**
   - Each item classified as "rationale" (RMW Rationale/Lump tab) or "news" (other)
   - Generated unique ID via hash(title)
   - Dedup check: skip if already seen or in staging
   - Staged in Redis with `platts:staging:{id}` and marked seen/scraped

4. **Telegram Queue (/queue command)**
   - Bot retrieves staging items from Redis (type: "news" or "rationale")
   - Displays paginated inline buttons for admin review
   - Admin can approve, reject, adjust, or send to AI

5. **Approval + AI Processing**
   - **News:** Direct approval → posted to Telegram channel
   - **Rationale:** Sent to Claude agent (3-phase: analyst → synthesis → localizer) → structured briefing generated
   - Approved items moved from staging → Telegram channel/WhatsApp broadcast

6. **Broadcast**
   - Approved messages posted to Telegram channel (subscribers notified)
   - WhatsApp endpoint receives same message, posts to WhatsApp group (via WhatsApp Cloud API webhook)
   - Delivery tracked in Postgres (delivery_reports table)

7. **Mini App (Real-Time Feed)**
   - Mini App fetches latest news from `/api/mini/news` (reads from Supabase/Redis)
   - Displays workflows (GitHub Actions runs), reports (Supabase Storage), contacts (Google Sheets)

**State Management:**
- **Redis:** FSM state (user conversation context), staging queue, dedup cache (seen articles), workflow state
- **Supabase Postgres:** Persistent records (news articles, reports metadata, delivery logs, contacts)
- **Google Sheets:** Contact list (admin managed)
- **Local memory (webhook):** Draft approvals in-flight

## Key Abstractions

**Curation Router:**
- Purpose: Classify and stage scraped items with dedup
- Examples: `execution/curation/router.py`, `execution/curation/id_gen.py`
- Pattern: Functional, stateless classification. Persists state to Redis.

**RationaleAgent (Claude 3-Phase):**
- Purpose: Extract market intelligence from raw reports
- Examples: `execution/agents/rationale_agent.py`
- Pattern: Sequential AI prompting (analyst → synthesis → localizer)

**Telegram Bot Handlers:**
- Purpose: Handle user commands, callbacks, and FSM flows
- Examples: `webhook/bot/routers/callbacks.py`, `webhook/bot/routers/messages.py`
- Pattern: Aiogram routers with middleware, CallbackData objects for type-safe button actions

**Mini App API:**
- Purpose: REST endpoints for in-app data fetching
- Examples: `webhook/routes/mini_api.py` (/api/mini/news, /api/mini/workflows, etc.)
- Pattern: aiohttp handlers with init_data validation (Telegram SDK)

**External Integrations (Client Classes):**
- Purpose: Encapsulate API calls to external services
- Examples: `execution/integrations/apify_client.py`, `execution/integrations/sheets_client.py`, `execution/integrations/claude_client.py`
- Pattern: Single responsibility, error handling, logging

## Entry Points

**Telegram Webhook:**
- Location: `webhook/bot/main.py:main()`
- Triggers: POST /webhook (Telegram updates) via aiohttp
- Responsibilities: Route Telegram messages to handlers, manage FSM state (Redis)

**Execution Scripts (Cron Jobs):**
- Location: `execution/scripts/*.py` (platts_ingestion.py, morning_check.py, send_daily_report.py, etc.)
- Triggers: Railway/GitHub Actions scheduled or manual
- Responsibilities: Fetch data, classify, queue for approval, dispatch to Telegram/WhatsApp

**GitHub Actions Workflows:**
- Location: `.github/workflows/`
- Triggers: Scheduled crons, manual dispatch
- Responsibilities: Invoke execution scripts, trigger Apify actors, post drafts to webhook

**Apify Actor Main:**
- Location: `actors/platts-scrap-*/src/main.js`
- Triggers: Manual or scheduled via Apify platform
- Responsibilities: Scrape data, save to Supabase, notify webhook

**Next.js Dashboard:**
- Location: `dashboard/` with `dashboard/app/page.tsx` as root
- Triggers: Admin browser navigation
- Responsibilities: Display workflows, news, contacts, delivery reports

**Mini App Frontend:**
- Location: `webhook/mini-app/src/main.tsx` (compiled to dist/, served by webhook)
- Triggers: User taps "Open Mini App" in Telegram
- Responsibilities: Display workflows, news feed, reports, contacts in-Telegram

## Error Handling

**Strategy:** Comprehensive try-catch with structured logging and fallback user messaging.

**Patterns:**
- **Aiogram handlers:** Catch exceptions, log with WorkflowLogger, reply with user-friendly error message
- **Execution scripts:** Catch at top level, log traceback, exit with non-zero code (Railway detects failure)
- **External API calls:** Retry logic (exponential backoff), log each attempt, raise after max retries
- **State transitions:** FSM errors logged; invalid states return error message without state change

## Cross-Cutting Concerns

**Logging:** 
- Framework: Python `structlog` + custom `WorkflowLogger` (colored console output, file logs to `/.tmp/logs/`)
- Pattern: Each module logs entry/exit of workflows, errors, and key state changes

**Validation:**
- **User input:** FSM states constrain expected message types; invalid input returns error
- **External data:** Defensive parsing (isinstance checks) for Apify actor payloads; malformed items skipped with warning
- **Auth:** RoleMiddleware on Telegram handlers (admin vs subscriber vs public); Mini App validates init_data signature

**Authentication:**
- **Telegram:** Bot token in environment; webhook signature validation (Telegram sends X-Telegram-Init-Data header)
- **Mini App:** Telegram SDK init_data (signed by Telegram, validated server-side in `validate_init_data()`)
- **Admin routes:** Middleware checks user role against Postgres user_roles table

---

*Architecture analysis: 2026-04-17*
