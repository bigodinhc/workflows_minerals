# Technology Stack

**Analysis Date:** 2026-02-13

## Languages

**Primary:**
- TypeScript 5.x - Used for Next.js dashboard frontend and API routes
- Python 3.10 - Used for webhook server and execution scripts

**Secondary:**
- JavaScript (ES2017) - Next.js build output and utilities
- SQL - Via Supabase/database queries (in execution scripts)

## Runtime

**Environment:**
- Node.js (for dashboard and main package)
- Python 3.10 (for webhook and execution)
- Turbopack (build bundler in Next.js)

**Package Manager:**
- npm - Used for dashboard dependencies
- npm - Used for root package (apify-client only)
- pip - Used for Python dependencies

## Frameworks

**Frontend:**
- Next.js 16.1.6 - Full-stack React framework with App Router
- React 19.2.3 - UI library
- Tailwind CSS 4.1.18 - Utility-first CSS framework
- Radix UI 1.4.3 - Headless component library for accessibility

**Backend/APIs:**
- Flask 2.0+ - Python web framework for webhook server
- Node.js/Next.js API Routes - TypeScript endpoint handlers

**Data Fetching:**
- SWR 2.4.0 - React hooks library for data fetching and caching (dashboard)
- Google APIs Client 171.2.0 - For Sheets/Drive integration
- Octokit 5.0.5 - GitHub REST API client

**CLI/Execution:**
- Apify Client 2.22.0 - For Apify actor execution and monitoring

## Key Dependencies

**Critical:**
- anthropic 0.40+ - Claude API client (webhook and executions)
- gspread 5.10+ - Google Sheets library (Python)
- google-auth 2.0+ - Authentication for Google services
- requests 2.28+ - HTTP client for external APIs

**Infrastructure:**
- gunicorn 21.0+ - WSGI server for Flask (webhook deployment)
- googleapis 171.2.0 - Google Sheets/Drive/Gmail APIs (Node.js)

**Utilities:**
- python-dotenv 1.0+ - Environment variable management
- pandas 2.0+ - Data manipulation (execution scripts)
- structlog 20.0+ - Structured logging
- date-fns 4.1.0 - Date utilities (dashboard)
- framer-motion 12.31.0 - Animation library (dashboard)
- lucide-react 0.563.0 - Icon library (dashboard)

## Configuration

**Environment:**
- `.env` file - Local configuration with secrets
- `.env.example` - Template for required variables
- Environment variables loaded via `python-dotenv` (Python) and Next.js default support (Node.js)

**Key configs required:**
- `GOOGLE_CREDENTIALS_JSON` - Base64-encoded Google service account key (JSON)
- `ANTHROPIC_API_KEY` - Claude API key
- `TELEGRAM_BOT_TOKEN` - Telegram bot token
- `GITHUB_TOKEN` - GitHub personal access token for Octokit
- `UAZAPI_TOKEN` - WhatsApp API token
- `UAZAPI_URL` - WhatsApp API endpoint
- `APIFY_API_TOKEN` - Apify.com API token
- `PORT` - Server port (default 8080 for webhook)

**Build:**
- `tsconfig.json` (`./dashboard/tsconfig.json`) - TypeScript compiler options with ES2017 target
- `next.config.ts` (`./dashboard/next.config.ts`) - Next.js configuration with Turbopack enabled
- `.eslintrc` (via eslint-config-next) - Linting configuration via Next.js default
- Tailwind CSS config implicit (v4 doesn't require explicit config file)

## Platform Requirements

**Development:**
- Node.js (latest or 18+) for dashboard
- Python 3.10 for webhook and execution
- npm for package management
- Git (workflows in GitHub Actions)

**Production:**
- Railway.app deployment (configured via `railway.json`)
- Docker (Dockerfile for containerization)
- Python 3.10-slim base image
- Gunicorn WSGI server for Flask webhook

---

*Stack analysis: 2026-02-13*
