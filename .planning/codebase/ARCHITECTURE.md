# Architecture

**Analysis Date:** 2026-02-13

## Pattern Overview

**Overall:** Three-tier hybrid architecture combining a Next.js dashboard frontend, Python-based execution engine orchestrated by GitHub Actions workflows, and external data integrations.

**Key Characteristics:**
- Event-driven workflow execution via GitHub Actions (scheduled and manual dispatch)
- Agent-based processing pipeline with multi-stage AI (Writer → Critic → Curator)
- Persistent state management with file-based JSON storage
- Real-time UI polling with GitHub API integration
- Webhook-based Telegram bot for interactive approval workflows

## Layers

**Presentation Layer (Frontend Dashboard):**
- Purpose: Web UI for monitoring workflows, viewing logs, triggering executions, and managing news drafts
- Location: `dashboard/`
- Contains: React/Next.js components, API routes, styling with Tailwind CSS
- Depends on: GitHub API (for workflow runs and logs), local file system (for draft data)
- Used by: End users for workflow management and monitoring

**API Routes Layer:**
- Purpose: Middleware connecting dashboard UI to external services (GitHub API, local filesystem, Python scripts)
- Location: `dashboard/app/api/`
- Contains: Next.js route handlers for workflows, logs, news drafts, contacts
- Depends on: GitHub Octokit SDK, Node.js filesystem, external subprocess calls
- Used by: Dashboard components via SWR data fetching

**Execution Engine (Core):**
- Purpose: Provides infrastructure for workflow execution tracking, logging, state management
- Location: `execution/core/`
- Contains: `runner.py`, `logger.py`, `state.py`, `retry.py` with workflow lifecycle management
- Depends on: Python standard library, custom logger/state modules
- Used by: Execution scripts and agents

**Integration Clients:**
- Purpose: Encapsulate external service communication (APIs, webhooks, databases)
- Location: `execution/integrations/`
- Contains: Clients for Platts, LSEG, Supabase, Telegram, Sheets, Claude, Apify, Baltic, UAZApi
- Depends on: External SDKs (anthropic, supabase, googleapis, etc.)
- Used by: Execution scripts for data fetching and sending

**Workflow Scripts (Execution):**
- Purpose: Implement specific business logic for data collection, processing, and delivery
- Location: `execution/scripts/`
- Contains: `morning_check.py`, `baltic_ingestion.py`, `daily_report.py`, `rationale_ingestion.py`, `send_daily_report.py`, `send_news.py`
- Depends on: Integration clients, core logger/state/retry modules
- Used by: GitHub Actions workflows

**Orchestration Layer:**
- Purpose: Define schedules and entry points for workflow execution
- Location: `.github/workflows/`
- Contains: YAML files for `morning_check.yml`, `baltic_ingestion.yml`, `daily_report.yml`, `rationale_news.yml`
- Depends on: GitHub Actions environment, Python environment setup, secrets/variables
- Used by: GitHub's scheduler and manual dispatches

**Webhook Service:**
- Purpose: Interactive Telegram bot for news draft approval and manual dispatch
- Location: `webhook/app.py`
- Contains: Flask app handling Telegram messages, AI agent orchestration (Writer/Critic/Curator), UAZAPI calls
- Depends on: Flask, Anthropic SDK, external APIs
- Used by: Telegram users for interactive approval workflow

**Directives Layer:**
- Purpose: Define standard operating procedures (SOPs) in markdown
- Location: `directives/`
- Contains: Markdown documentation of workflows, tools, inputs, outputs, edge cases
- Depends on: None (documentation only)
- Used by: AI agents for understanding workflow requirements

## Data Flow

**Scheduled Workflow Execution:**

1. GitHub Actions scheduler triggers at specified time (e.g., 08:30 BRT for morning check)
2. Workflow job checks out code and sets up Python environment
3. Execution script runs (e.g., `execution/scripts/morning_check.py`)
4. Script uses integration clients to fetch data (Platts prices, email, LSEG data)
5. Data is processed and formatted according to business rules
6. Script sends formatted messages via WhatsApp/Telegram through integration clients
7. Logs written to `dashboard/.tmp/logs/[workflow_name]/[run_id].json`
8. Execution completes

**Dashboard Monitoring:**

1. User loads dashboard at `dashboard/app/page.tsx`
2. Component calls `/api/workflows` endpoint via SWR with 10s refresh interval
3. API route fetches GitHub workflow runs from external `workflows_minerals` repo
4. Runs formatted and returned as JSON
5. Dashboard displays workflow status, health metrics, last run times
6. User can click "View Logs" to fetch `/api/logs?run_id=XXX`
7. API retrieves job logs from GitHub Actions API
8. Logs displayed in modal

**News Draft Workflow (Multi-Agent):**

1. Apify integration collects raw news articles
2. `rationale_ingestion.py` script processes articles through 3 AI agents:
   - Writer: Formats raw data into structured narrative
   - Critic: Reviews for accuracy and completeness
   - Curator: Final polish and approval readiness
3. Draft saved to `data/news_drafts.json` with `status: pending`
4. Dashboard displays pending drafts in `/news` route
5. User approves/rejects draft via `/api/news` endpoint
6. If approved: `send_news.py` triggers, sends to WhatsApp
7. Draft status updated in JSON

**Interactive Telegram Approval:**

1. User sends text to Telegram bot
2. Webhook app receives message in `/webhook` endpoint
3. App invokes 3 AI agents via Anthropic SDK (using multi-turn conversation)
4. Writer agent generates initial formatted message
5. Critic agent reviews and suggests improvements
6. Curator agent finalizes version
7. Bot sends preview message to Telegram with Approve/Reject buttons
8. User approves → bot calls UAZAPI to dispatch to WhatsApp contacts
9. User rejects → draft discarded, new conversation started

**State Management:**

- Persistent state stored in `.state/[workflow].json` (e.g., `.state/morning_check.json`)
- Each workflow maintains last-known state (cursor positions, last fetch times, etc.)
- State files loaded at script start, updated during execution, persisted to disk
- Enables resume-on-failure and duplicate prevention

## Key Abstractions

**WorkflowLogger:**
- Purpose: Structured JSON logging for workflow execution
- Examples: `execution/core/logger.py`
- Pattern: Decorator/initialization pattern with step tracking and file I/O
- Writes JSON to `.tmp/logs/[workflow]/[run_id].json`

**StateManager:**
- Purpose: Persistent key-value storage for workflow state
- Examples: `execution/core/state.py`
- Pattern: File-based JSON store with get/set/delete operations
- Enables state recovery and prevents duplicate processing

**RunContext:**
- Purpose: In-memory context for single workflow execution
- Examples: `execution/core/state.py`
- Pattern: Container for inputs, outputs, and execution metadata
- Passed through workflow execution for data threading

**IntegrationClient Pattern:**
- Purpose: Encapsulate external service communication
- Examples: `execution/integrations/supabase_client.py`, `execution/integrations/telegram_client.py`, etc.
- Pattern: Class-based client with method per operation, error handling, logging
- Each client manages auth, connection, and API-specific logic

**RetryPolicy:**
- Purpose: Exponential backoff retry logic for unreliable operations
- Examples: `execution/core/retry.py`
- Pattern: Decorator factory with configurable attempts, delays, exceptions
- Used for API calls prone to temporary failures

## Entry Points

**Web Dashboard:**
- Location: `dashboard/app/page.tsx` (root)
- Triggers: User loads `http://localhost:3000/`
- Responsibilities: Display workflow status, trigger execution, view logs

**GitHub Actions Workflows:**
- Location: `.github/workflows/*.yml`
- Triggers: Scheduled cron jobs or manual dispatch via GitHub UI
- Responsibilities: Environment setup, Python execution, secret injection

**Webhook Bot:**
- Location: `webhook/app.py`
- Triggers: Telegram message received
- Responsibilities: Parse message, invoke AI agents, handle user approval/rejection

**Manual Execution Scripts:**
- Location: `execution/scripts/*.py`
- Triggers: Direct Python invocation (e.g., `python execution/scripts/morning_check.py`)
- Responsibilities: Data collection, processing, delivery

## Error Handling

**Strategy:** Try-catch with logging, exponential backoff for transient failures, user-friendly error messages in UI

**Patterns:**
- Integration clients catch exceptions and log with context (file, API, operation)
- Execution scripts use `@retry_with_backoff` decorator for API calls
- Dashboard API routes return `NextResponse.json({ error: "..." }, { status: 500 })`
- Logger records all errors to JSON for post-mortem analysis

## Cross-Cutting Concerns

**Logging:** Centralized via `WorkflowLogger` writing JSON to `.tmp/logs/`, console output for visibility

**Validation:** Input validation in API routes before forwarding to Python (e.g., `run_id` type check in `/api/logs`)

**Authentication:** GitHub token in environment variables for Actions/API, Telegram token for bot, API keys in `.env`

**Rate Limiting:** Implicit via workflow schedules; explicit rate limits in external APIs (Platts, LSEG, Apify)

**Secrets Management:** All sensitive data in GitHub Secrets or `.env` file (excluded from git)

---

*Architecture analysis: 2026-02-13*
