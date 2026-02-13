# Codebase Structure

**Analysis Date:** 2026-02-13

## Directory Layout

```
project-root/
├── dashboard/               # Next.js frontend application
│   ├── app/                # App Router pages and API routes
│   ├── components/         # Reusable React components
│   ├── lib/                # Utility functions (cn, clsx/twMerge)
│   ├── public/             # Static assets
│   ├── package.json        # Frontend dependencies
│   ├── next.config.ts      # Next.js configuration
│   ├── tsconfig.json       # TypeScript configuration
│   └── .next/              # Build output (gitignored)
├── execution/              # Python workflow execution engine
│   ├── core/               # Core workflow infrastructure
│   ├── agents/             # LLM-based agents
│   ├── integrations/       # External service clients
│   └── scripts/            # Workflow implementation scripts
├── webhook/                # Flask Telegram bot server
│   └── app.py              # Main webhook application
├── directives/             # SOP documentation
│   ├── README.md           # How to write directives
│   └── _templates/         # Directive templates
├── data/                   # Persistent data storage (gitignored)
│   └── news_drafts.json    # News draft queue and history
├── .state/                 # Workflow state files (gitignored)
│   └── [workflow].json     # Per-workflow persistent state
├── .tmp/                   # Temporary files (gitignored)
│   ├── logs/               # Workflow execution logs
│   └── [other temp]/       # Transient data
├── .github/workflows/      # GitHub Actions automation
│   ├── morning_check.yml   # Platts price collection
│   ├── baltic_ingestion.yml # Baltic Exchange email monitoring
│   ├── daily_report.yml    # SGX futures data
│   └── rationale_news.yml  # News collection and AI processing
├── tests/                  # Test suite
├── .env                    # Environment variables (gitignored)
├── .env.example            # Environment template (committed)
├── .gitignore              # Git exclusions
└── Dockerfile              # Docker image for deployment
```

## Directory Purposes

**dashboard/**
- Purpose: Next.js frontend for workflow monitoring and control
- Contains: React components, Next.js pages, API routes, configuration
- Key files: `app/page.tsx` (main dashboard), `app/api/workflows/route.ts` (GitHub API proxy)

**dashboard/app/**
- Purpose: Next.js App Router structure
- Contains: Page components for routes, API route handlers
- Key files:
  - `page.tsx` - Root dashboard (workflow overview)
  - `layout.tsx` - Root layout with SideNav
  - `workflows/page.tsx` - Workflows page with details
  - `news/page.tsx` - News draft management
  - `executions/page.tsx` - Execution history
  - `contacts/page.tsx` - Contact list management

**dashboard/app/api/**
- Purpose: Next.js API routes acting as middleware to external services
- Contains: Request handlers for workflows, logs, news, contacts
- Key files:
  - `workflows/route.ts` - GET: fetch runs, POST: trigger execution
  - `logs/route.ts` - GET: retrieve job logs from GitHub
  - `news/route.ts` - GET: pending drafts, POST: approve/reject/send
  - `contacts/route.ts` - GET: contact list

**dashboard/components/**
- Purpose: Reusable UI components
- Contains: Radix UI primitives, layout components, theme-agnostic
- Key subdirectories:
  - `ui/` - Primitive components (Button, Card, Sheet, etc.)
  - `layout/` - Layout components (SideNav)

**dashboard/lib/**
- Purpose: Utility functions and helpers
- Contains: TypeScript utilities for styling, type definitions
- Key files: `utils.ts` - `cn()` function for Tailwind class merging

**execution/core/**
- Purpose: Core workflow infrastructure
- Contains: Runner, logger, state, retry modules
- Key files:
  - `runner.py` - Main workflow execution orchestrator
  - `logger.py` - Structured JSON logging to `.tmp/logs/`
  - `state.py` - Persistent state with StateManager and RunContext
  - `retry.py` - Exponential backoff retry decorator

**execution/integrations/**
- Purpose: External service clients
- Contains: Client classes for each external service
- Key files:
  - `platts_client.py` - Platts pricing API
  - `lseg_client.py` - Refinitiv LSEG futures API
  - `supabase_client.py` - Supabase database access
  - `telegram_client.py` - Telegram message sending
  - `sheets_client.py` - Google Sheets access
  - `claude_client.py` - Anthropic Claude API wrapper
  - `apify_client.py` - Web scraping via Apify
  - `baltic_client.py` - Baltic Exchange data
  - `uazapi_client.py` - Custom UAZAPI for WhatsApp dispatch

**execution/scripts/**
- Purpose: Implement workflow-specific business logic
- Contains: Standalone Python scripts executed by GitHub Actions
- Key files:
  - `morning_check.py` - Daily Platts iron ore price report
  - `baltic_ingestion.py` - Email-based Baltic Exchange BDI extraction
  - `daily_report.py` - SGX futures price report
  - `rationale_ingestion.py` - News collection and AI multi-agent processing
  - `send_news.py` - Telegram/WhatsApp dispatch
  - `send_daily_report.py` - Report delivery

**webhook/**
- Purpose: Flask application for Telegram bot with interactive approval
- Contains: Bot event handlers, AI agent orchestration
- Key files:
  - `app.py` - Main Flask application with /webhook and /message routes

**directives/**
- Purpose: SOP documentation for workflows
- Contains: Markdown files documenting each workflow
- Key files:
  - `README.md` - Format and guidelines for directives
  - `_templates/` - Template for new directives

**data/**
- Purpose: Persistent application data (gitignored)
- Contains: JSON files and text data
- Key files:
  - `news_drafts.json` - Queue of news drafts with status (pending/approved/rejected)

**.state/**
- Purpose: Workflow state persistence (gitignored)
- Contains: JSON files keyed by workflow name
- Example files:
  - `morning_check.json` - Last execution time, cursor positions
  - `baltic_ingestion.json` - Email parsing state

**.tmp/logs/**
- Purpose: Execution logs (gitignored)
- Contains: JSON log files organized by workflow
- Structure: `.tmp/logs/[workflow_name]/[run_id].json`

**.github/workflows/**
- Purpose: GitHub Actions automation
- Contains: YAML workflow definitions
- Key files:
  - `morning_check.yml` - Scheduled Platts data fetch (30,45 UTC on weekdays)
  - `baltic_ingestion.yml` - Email monitoring workflow
  - `daily_report.yml` - SGX futures workflow
  - `rationale_news.yml` - News AI processing workflow

## Key File Locations

**Entry Points:**
- `dashboard/app/page.tsx` - Main dashboard UI (root `/`)
- `dashboard/app/layout.tsx` - Root HTML structure with SideNav
- `webhook/app.py` - Flask application (deployed to Railway)
- `execution/scripts/morning_check.py` - Platts workflow entry
- `.github/workflows/morning_check.yml` - GitHub Actions trigger

**Configuration:**
- `dashboard/next.config.ts` - Next.js settings (turbopack config)
- `dashboard/tsconfig.json` - TypeScript compilation rules
- `dashboard/components.json` - Component library metadata
- `.env` - Environment variables (secrets, API keys)
- `.env.example` - Template for required env vars

**Core Logic:**
- `execution/core/runner.py` - Workflow execution lifecycle
- `execution/core/logger.py` - JSON logging infrastructure
- `execution/core/state.py` - State persistence
- `execution/integrations/` - All external service communication
- `execution/scripts/` - Workflow implementations

**Testing:**
- `tests/test_format.py` - Test suite (location for integration tests)

## Naming Conventions

**Files:**
- Components: PascalCase with `.tsx` extension (e.g., `SideNav.tsx`)
- Pages: lowercase with `.tsx` extension (e.g., `page.tsx`)
- API routes: `route.ts` in nested folder matching endpoint path
- Scripts: snake_case with `.py` extension (e.g., `morning_check.py`)
- Utilities: lowercase descriptive name (e.g., `utils.ts`)

**Directories:**
- Feature/route directories: lowercase (e.g., `workflows/`, `contacts/`)
- UI component directories: lowercase (e.g., `components/ui/`)
- API directories: match resource name (e.g., `app/api/workflows/`)
- Python packages: lowercase with underscores (e.g., `execution.integrations`)

**Variables & Functions:**
- React components: PascalCase (e.g., `WorkflowCard`, `SideNav`)
- Functions: camelCase (e.g., `formatDistanceToNow`, `handleTrigger`)
- Constants: UPPER_SNAKE_CASE (e.g., `WORKFLOWS`, `SHEET_ID`)
- Classes: PascalCase (e.g., `WorkflowLogger`, `StateManager`, `SupabaseClient`)

**Environment Variables:**
- API Keys: `[SERVICE]_[KEY_TYPE]` (e.g., `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`)
- URLs: `[SERVICE]_URL` (e.g., `SUPABASE_URL`, `UAZAPI_URL`)
- IDs: `[SERVICE]_[OBJECT]_ID` (e.g., `SHEET_ID`)
- Tokens: `[SERVICE]_TOKEN` (e.g., `TELEGRAM_BOT_TOKEN`)

## Where to Add New Code

**New Feature:**
- Primary code: `execution/scripts/[new_workflow].py` for logic
- Integration code: Add new client in `execution/integrations/[service]_client.py` if needed
- Tests: `tests/test_[new_workflow].py`
- GitHub Actions: `.github/workflows/[new_workflow].yml` for scheduling
- Dashboard: Add new page in `dashboard/app/[feature]/page.tsx` if UI needed

**New Component/Module:**
- React component: `dashboard/components/[category]/[ComponentName].tsx`
- Python module: `execution/[layer]/[module].py` (following layer structure)
- Integration client: `execution/integrations/[service]_client.py`

**Utilities:**
- Shared TypeScript helpers: `dashboard/lib/utils.ts`
- Shared Python helpers: `execution/core/[utility].py` if general, or add to relevant client
- Styling: Use Tailwind CSS classes in components; configure in `postcss.config.mjs` if needed

## Special Directories

**dashboard/.next/**
- Purpose: Next.js build artifacts
- Generated: Yes (build output from `npm run build`)
- Committed: No (in .gitignore)

**.state/**
- Purpose: Workflow state persistence for tracking execution progress
- Generated: Yes (created by `StateManager` on first workflow run)
- Committed: No (in .gitignore; contains runtime state)

**.tmp/logs/**
- Purpose: Execution logs for debugging and audit trail
- Generated: Yes (created by `WorkflowLogger` during script execution)
- Committed: No (in .gitignore; transient)

**data/**
- Purpose: Application data (news drafts, etc.)
- Generated: Yes (created by scripts and API routes)
- Committed: No (in .gitignore; contains sensitive/dynamic data)

**node_modules/ & .venv/**
- Purpose: Dependency installation directories
- Generated: Yes (`npm install` and `pip install`)
- Committed: No (in .gitignore)

---

*Structure analysis: 2026-02-13*
