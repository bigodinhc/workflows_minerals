# Phase 3: Telegram Mini App (Dashboard Inside Telegram)

**Date:** 2026-04-16
**Status:** Approved
**Depends on:** Phase 2 (UX + subscriptions + delivery) completed

## Context

After Phase 1 (Aiogram 3) and Phase 2 (professional UX + subscriptions + Telegram delivery), the bot has a solid foundation. Phase 3 adds a Telegram Mini App — a web application embedded inside Telegram that replaces or complements the existing Next.js dashboard.

The current dashboard (Next.js, deployed on Vercel) provides:
- Workflow execution history with health percentages
- Workflow trigger buttons
- Delivery report viewer
- News feed (ingested items)
- Contact management

A Mini App brings this experience INSIDE Telegram — no need to open a separate browser tab. The user taps a button in the bot and the dashboard opens as a native-feeling panel.

## Goals

1. Build a Telegram Mini App that replicates core dashboard functionality
2. Native Telegram look-and-feel (themeParams, dark/light mode sync)
3. Accessible via bot menu button and inline keyboard
4. Mobile-first design (the primary use case is viewing from phone)
5. Real-time data display (workflow status, delivery reports)

## Non-Goals

- Full feature parity with Next.js dashboard on day one — start with most-used features
- Replacing the Next.js dashboard (it continues existing for desktop use)
- TON/crypto integration
- User authentication beyond Telegram's built-in initData validation
- Offline mode (nice-to-have later)

## Features — MVP Scope

### 1. Workflow Dashboard

Main screen showing all 5 workflows:
- Current status (running/success/failure)
- Last run time
- Health percentage (last 10 runs)
- Trigger button per workflow

This is the most-used dashboard feature — checking if workflows ran successfully.

### 2. Delivery Reports

View recent delivery reports:
- Date, workflow name, success/failure counts
- Tap to expand details (per-contact delivery status)

### 3. News Feed

Scrollable feed of ingested Platts news:
- Title, date, source
- Tap to read full text
- Status badges (archived, rejected, pending)

### Navigation

Bottom tab bar (native Mini App pattern):

```
┌─────┬──────┬──────┐
│  ⚡  │  📊  │  📰  │
│ Home │ Feed │ News │
└─────┴──────┴──────┘
```

- **Home** — Workflow dashboard (default)
- **Feed** — Delivery reports
- **News** — Ingested news

## Tech Stack

### Frontend
- **React 19 + Vite** — same React version as existing dashboard for component reuse
- **@telegram-apps/sdk-react** — official Telegram Mini App SDK for React
- **Tailwind CSS** — same as existing dashboard
- **SWR** — same data fetching library as existing dashboard

### Backend
- Reuse the same API endpoints from the Aiogram webhook's aiohttp server
- Add Mini App-specific API routes under `/api/mini/`
- Validate Telegram `initData` for authentication

### Deployment
- Build as static assets (Vite build)
- Serve from the same Railway aiohttp server (or separate Vercel deploy)
- Register via BotFather as Mini App URL

## Architecture

```
┌────────────────────────────────────────────────────┐
│ Telegram Client                                    │
│                                                    │
│  ┌──────────────────────────────────────────────┐  │
│  │ Mini App (WebView)                           │  │
│  │                                              │  │
│  │  React + Vite + @telegram-apps/sdk-react     │  │
│  │  ├── WorkflowDashboard (home)                │  │
│  │  ├── DeliveryFeed                            │  │
│  │  └── NewsFeed                                │  │
│  │                                              │  │
│  │  themeParams → CSS variables (auto dark/light)│  │
│  │  MainButton → primary action per screen      │  │
│  │  hapticFeedback → button presses             │  │
│  └──────────────────────────────────────────────┘  │
│         │ API calls (authenticated via initData)    │
│         ▼                                          │
│  ┌──────────────────────────────────────────────┐  │
│  │ Webhook Server (aiohttp)                     │  │
│  │  /api/mini/workflows   → workflow status     │  │
│  │  /api/mini/deliveries  → delivery reports    │  │
│  │  /api/mini/news        → ingested news       │  │
│  │  /api/mini/trigger     → trigger workflow    │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────┘
```

## Design Principles (from research document)

### 1. Native Look & Feel

```javascript
// Sync with Telegram theme automatically
import { useThemeParams } from '@telegram-apps/sdk-react';

const { bg_color, text_color, hint_color, button_color } = useThemeParams();

// Apply as CSS variables
document.documentElement.style.setProperty('--tg-bg', bg_color);
document.documentElement.style.setProperty('--tg-text', text_color);
```

No hardcoded colors. The app looks correct in both dark and light mode without any manual theming.

### 2. Mobile-First

- Touch targets minimum 44x44px
- No hover states (mobile doesn't have hover)
- Relative units (rem) for font sizes
- Full-width cards and buttons
- Swipe gestures for navigation (optional enhancement)

### 3. Performance

- Loading time under 2 seconds
- Skeleton screens while data loads (no blank screens)
- SWR for cache-first data fetching with background revalidation
- Minimal bundle size — only import what's needed

### 4. Telegram-Native Interactions

- **MainButton** — "Trigger Workflow" on workflow detail screen
- **BackButton** — native back navigation between screens
- **hapticFeedback** — light vibration on button taps, success/error on workflow trigger
- **showPopup** — confirmation before triggering workflows

## API Endpoints (new)

All endpoints validate Telegram `initData` header for authentication.

### GET /api/mini/workflows
Returns workflow status (reuses GitHub API logic from workflow_trigger.py):
```json
{
  "workflows": [
    {
      "id": "morning_check.yml",
      "name": "MORNING CHECK",
      "description": "Precos Platts",
      "last_run": {
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-04-16T08:30:00Z",
        "duration_seconds": 45
      },
      "health_pct": 90
    }
  ]
}
```

### POST /api/mini/trigger
Trigger a workflow (reuses trigger_workflow from workflow_trigger.py):
```json
// Request
{ "workflow_id": "morning_check.yml" }

// Response
{ "ok": true, "message": "Workflow triggered" }
```

### GET /api/mini/deliveries
Returns recent delivery reports:
```json
{
  "deliveries": [
    {
      "date": "2026-04-16",
      "workflow": "morning_check",
      "total": 15,
      "success": 14,
      "failure": 1,
      "details_url": "/api/mini/deliveries/2026-04-16/morning_check"
    }
  ]
}
```

### GET /api/mini/news?page=1&limit=20
Returns ingested news items:
```json
{
  "items": [
    {
      "id": "platts_abc123",
      "title": "Iron ore prices surge on China demand",
      "source": "Platts",
      "date": "2026-04-16",
      "status": "archived",
      "preview_url": "/preview/platts_abc123"
    }
  ],
  "total": 156,
  "page": 1
}
```

## Authentication

Telegram Mini Apps pass `initData` — a signed string that proves the user opened the app from Telegram. Validate server-side:

```python
# routes/mini_api.py
from aiogram.utils.web_app import check_webapp_signature, parse_webapp_init_data

async def validate_init_data(request: web.Request) -> dict:
    """Extract and validate Telegram initData from request header."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not check_webapp_signature(BOT_TOKEN, init_data):
        raise web.HTTPUnauthorized(text="Invalid initData")
    return parse_webapp_init_data(init_data)
```

This gives us the user's `chat_id` without any login flow.

## Directory Structure

```
webhook/
  mini-app/                    # Vite React project
    index.html
    vite.config.ts
    package.json
    src/
      main.tsx
      App.tsx
      hooks/
        useTelegram.ts         # themeParams, MainButton, hapticFeedback
      pages/
        WorkflowDashboard.tsx  # Home tab
        DeliveryFeed.tsx       # Feed tab
        NewsFeed.tsx           # News tab
      components/
        WorkflowCard.tsx       # Reusable workflow status card
        DeliveryCard.tsx
        NewsItem.tsx
        TabBar.tsx             # Bottom navigation
        Skeleton.tsx           # Loading skeleton
      lib/
        api.ts                 # API client with initData auth
        theme.ts               # themeParams → CSS variables
    dist/                      # Built static assets (served by aiohttp)
  routes/
    mini_api.py                # API endpoints for Mini App
    mini_static.py             # Serve built static assets
```

## BotFather Configuration

After building:
1. `/setmenubutton` — set Mini App URL as the bot's menu button
2. Or: add "Open Dashboard" inline keyboard button that opens the Mini App via `web_app` parameter

```python
# In keyboards.py
{"text": "📊 Dashboard", "web_app": {"url": f"{WEBHOOK_URL}/mini/"}}
```

## Deployment

**Option A: Same Railway server (simpler)**
- Build Vite assets at Docker build time
- Serve `/mini/*` static files from aiohttp
- API endpoints at `/api/mini/*` on same server
- Pros: single deploy, shared auth
- Cons: larger Docker image

**Option B: Separate Vercel deploy (scalable)**
- Mini App frontend on Vercel (like existing dashboard)
- API calls to Railway webhook server
- Pros: CDN, fast static hosting
- Cons: CORS config, two deploys

Recommendation: **Option A** for MVP. Simpler, single deploy, no CORS. Move to Option B if performance requires it.

## Reuse from Existing Dashboard

Components and patterns from `dashboard/` that can be adapted:

- `WorkflowCard` component structure and health calculation logic
- SWR data fetching patterns
- Tailwind utility classes and design tokens
- API route patterns (already fetching from GitHub API)

The Mini App won't share code directly (different build), but the patterns and logic transfer.

## Testing Strategy

- Unit tests for API endpoints (mock GitHub API, mock Redis)
- initData validation tests (valid signature, invalid signature, expired)
- Frontend: component tests with React Testing Library
- E2E: manual testing in Telegram (Mini App environment is hard to automate)
- Performance: Lighthouse audit for mobile (target >90 score)

## Success Criteria

1. Mini App opens from bot menu button in under 2 seconds
2. Dark/light mode syncs automatically with Telegram theme
3. Workflow status displays correctly with health indicators
4. Trigger workflow button works with haptic feedback + confirmation
5. Delivery reports and news feed load and paginate smoothly
6. Authentication works via initData (no login screen)
7. Mobile-first — looks good on phone (primary use case)
