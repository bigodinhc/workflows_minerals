# Phase 3: Telegram Mini App

**Date:** 2026-04-16
**Status:** Approved
**Depends on:** Phase 1 (Aiogram 3) and Phase 2 (UX + subscriptions) completed

## Context

The SuperMustache Bot runs on Aiogram 3 with professional UX, subscriptions, and Telegram delivery. Phase 3 adds a Telegram Mini App — a web application embedded inside Telegram that complements the bot with a rich visual dashboard.

The existing Next.js dashboard (Vercel) continues for desktop use. The Mini App targets mobile — the primary way users interact with Telegram.

## Goals

1. Dashboard inside Telegram accessible via bot menu button
2. Visual style: Blum/exchange-inspired — glassmorphism, sparklines, teal accent palette, ambient glow
3. 5 screens: Home, Workflows, News, Reports, Contacts
4. Navigation: 3 bottom tabs (Home, Workflows, News) + "Mais" menu (Reports, Contatos, Settings)
5. Mobile-first, dark mode, Telegram theme sync via themeParams
6. Authentication via Telegram initData (zero login)

## Non-Goals

- Replacing the Next.js dashboard (it stays for desktop)
- TON/crypto integration
- Offline mode
- Full feature parity with dashboard on day one — focus on most-used features
- Payment/monetization

## Visual Design System

### Palette

```
Background:       #09090b (near black)
Card background:   rgba(28,28,31,0.7) with backdrop-filter: blur(10px)
Primary accent:    #14b8a6 (teal-500)
Accent light:      #5eead4 (teal-300)
Success:           #4ade80 (green-400)
Error:             #f87171 (red-400)
Warning:           #facc15 (yellow-400)
Text primary:      #fafafa
Text secondary:    #71717a
Text muted:        #52525b
Border:            rgba(255,255,255,0.04)
Status border:     status color at 8-15% opacity
```

### Design Tokens

- Border radius: 14px (cards), 20px (hero), 12px (icons), 20px (chips)
- Glassmorphism: `background: rgba(28,28,31,0.7); backdrop-filter: blur(10px)`
- Ambient glow: radial-gradient with accent at 4-6% opacity behind hero
- Status dots: 8px with box-shadow glow at 30% opacity
- Sparklines: SVG polyline, 60x12px, stroke-width 1.5, rounded caps
- Touch targets: minimum 44x44px
- Font sizes: 8-9px labels, 10px secondary, 12-13px body, 18px headings, 36px hero stat

## Screens

### 1. Home (default tab)

**Hero card** (glassmorphism, gradient border):
- "SYSTEM HEALTH" label in teal
- Large health percentage (e.g., "92%")
- Mini ring chart SVG showing X/5 workflows OK
- Stats row: Runs today | Contatos | News count
- Decorative orbs (radial gradients) for depth

**Workflows section** below hero:
- "WORKFLOWS" label + "Ver todos →" link
- One card per workflow showing:
  - Icon (📊📈⚓📰📄) in gradient-tinted square
  - Name + last run time
  - Sparkline SVG (last 5 runs trend)
  - Health % + status dot (OK/Fail/Run)
  - Status-colored border (green/red/yellow at low opacity)

### 2. Workflows (tab)

**Card expandível** pattern:

**Collapsed state:**
- Icon + name + description
- Health % badge
- Last run time

**Expanded state** (toque para abrir):
- Full card header (darker background)
- "Últimas 5 execuções" section:
  - Date/time + result per run (success with duration, failure with reason)
- Action buttons:
  - "▶ Executar agora" (primary, blue/teal)
  - "🔗 GitHub" (secondary, gray)
- Trigger uses Telegram `showPopup` for confirmation
- Haptic feedback on trigger (success/error distinct)

### 3. News (tab)

**Filter chips** at top:
- Todos (active by default, teal accent)
- Pendentes
- Arquivados
- Recusados

**Lista compacta:**
- Status dot with glow (green=archived, yellow=pending, red=rejected)
- Title in 1 line (ellipsis overflow)
- Time on the right
- Rows grouped in a continuous glass card with 2px gaps
- First row: top rounded corners. Last row: bottom rounded corners.
- ~8 items visible per screen
- Toque opens detail view (full text, source, date, actions)
- Infinite scroll pagination (load more on scroll)

### 4. Reports (via "Mais" menu)

**Navigation hierarchy:**
1. Report type selection (Market Reports / Research Reports)
2. Latest 10 reports OR browse by year
3. Year → Month → Report list
4. Tap to download PDF

Reuses existing reports_nav.py logic via API. Visual style matches the glassmorphism cards.

### 5. Contatos (via "Mais" menu)

**Contact list:**
- Name + phone
- Active/inactive toggle (teal dot = active)
- Search bar at top
- Pagination

Reuses existing contact management logic via API.

### Menu "Mais"

Opens from 4th tab button. Shows list of sections:

- 📊 Reports — "PDFs Platts — Market & Research"
- 👥 Contatos — "15 ativos · gerenciar lista"
- ⚙️ Settings — "Notificações e preferências"

Each item is a glassmorphism row with icon, title, and subtitle.

### Tab Bar

- 3 main tabs + "Mais" overflow
- Active tab: accent color label + indicator line (2px teal bar above)
- Inactive: 40% opacity emoji, muted text
- Glass background: `rgba(9,9,11,0.95)` with `backdrop-filter: blur(20px)`
- Border: `rgba(255,255,255,0.04)` top

## Tech Stack

### Frontend
- **React 19 + Vite** — fast builds, same React as existing dashboard
- **@telegram-apps/sdk-react** — themeParams, MainButton, BackButton, hapticFeedback, showPopup
- **Tailwind CSS** — utility classes matching design tokens
- **SWR** — cache-first data fetching with background revalidation

### Backend API
- aiohttp routes on the same Railway server (added in Phase 1)
- All endpoints validate Telegram `initData` header
- Prefix: `/api/mini/`

### Deployment
- **Option A (MVP):** Build Vite at Docker build time, serve static from aiohttp
- Single deploy, no CORS, shared auth
- Move to Vercel later if performance requires CDN

## API Endpoints

All validate `initData` via `aiogram.utils.web_app.check_webapp_signature`.

### GET /api/mini/workflows
```json
{
  "workflows": [
    {
      "id": "morning_check.yml",
      "name": "MORNING CHECK",
      "description": "Preços Platts",
      "icon": "📊",
      "last_run": {
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-04-16T08:30:00Z",
        "duration_seconds": 45
      },
      "health_pct": 90,
      "recent_runs": [
        {"conclusion": "success", "created_at": "..."},
        {"conclusion": "success", "created_at": "..."},
        {"conclusion": "failure", "created_at": "..."},
        {"conclusion": "success", "created_at": "..."},
        {"conclusion": "success", "created_at": "..."}
      ]
    }
  ]
}
```

### GET /api/mini/workflows/{id}/runs?limit=5
```json
{
  "runs": [
    {
      "id": 12345,
      "status": "completed",
      "conclusion": "failure",
      "created_at": "2026-04-16T09:30:00Z",
      "duration_seconds": null,
      "error": "LSEG timeout",
      "html_url": "https://github.com/..."
    }
  ]
}
```

### POST /api/mini/trigger
```json
// Request
{ "workflow_id": "morning_check.yml" }
// Response
{ "ok": true }
```

### GET /api/mini/news?status=all&page=1&limit=20
```json
{
  "items": [
    {
      "id": "platts_abc123",
      "title": "Iron ore prices surge on renewed China stimulus hopes",
      "source": "Platts",
      "source_feed": "allInsights",
      "date": "2026-04-16T08:45:00Z",
      "status": "archived",
      "preview_url": "/preview/platts_abc123"
    }
  ],
  "total": 156,
  "page": 1
}
```

### GET /api/mini/news/{id}
Full article detail (title, fullText, tables, source, date, status).

### GET /api/mini/reports?type=Market%20Reports&year=2026&month=4
```json
{
  "reports": [
    {
      "id": "uuid",
      "report_name": "Iron Ore Monthly",
      "date_key": "2026-04-15",
      "download_url": "/api/mini/reports/uuid/download"
    }
  ]
}
```

### GET /api/mini/reports/{id}/download
Returns signed Supabase storage URL (redirect or JSON with URL).

### GET /api/mini/contacts?search=&page=1
```json
{
  "contacts": [
    {"name": "João", "phone": "5511999...", "active": true}
  ],
  "total": 15,
  "page": 1
}
```

### POST /api/mini/contacts/{phone}/toggle
Toggle active/inactive. Returns new status.

### GET /api/mini/stats
```json
{
  "health_pct": 92,
  "workflows_ok": 4,
  "workflows_total": 5,
  "runs_today": 47,
  "contacts_active": 14,
  "news_today": 156
}
```

## Authentication

```python
# routes/mini_api.py
from aiogram.utils.web_app import check_webapp_signature, parse_webapp_init_data

async def validate_init_data(request: web.Request) -> dict:
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not check_webapp_signature(BOT_TOKEN, init_data):
        raise web.HTTPUnauthorized(text="Invalid initData")
    return parse_webapp_init_data(init_data)
```

Frontend sends `initData` in every request header. No login screen needed.

## Telegram Native Interactions

### themeParams
```javascript
import { useThemeParams } from '@telegram-apps/sdk-react';
// Sync Telegram theme colors to CSS variables
// Fallback to our teal design tokens when not available
```

### MainButton
- Workflows expanded card: "Executar agora" as MainButton
- Reports detail: "Download PDF" as MainButton

### BackButton
- Navigation stack: tab screens → detail views
- BackButton pops detail views
- Tab switches don't push to stack

### hapticFeedback
- Button taps: `impactOccurred('light')`
- Workflow trigger success: `notificationOccurred('success')`
- Workflow trigger error: `notificationOccurred('error')`
- Toggle contact: `impactOccurred('medium')`

### showPopup
- Confirm before triggering workflow: "Executar MORNING CHECK agora?"

## Directory Structure

```
webhook/
  mini-app/
    index.html
    vite.config.ts
    package.json
    tailwind.config.ts
    src/
      main.tsx
      App.tsx
      hooks/
        useTelegram.ts          # themeParams, MainButton, BackButton, hapticFeedback
        useApi.ts               # SWR fetcher with initData auth header
      pages/
        Home.tsx                # Hero card + workflow overview
        Workflows.tsx           # Expandable workflow cards
        News.tsx                # Compact list with filters
        NewsDetail.tsx          # Full article view
        Reports.tsx             # Type → Year → Month → List navigation
        Contacts.tsx            # Contact list with toggle
        More.tsx                # "Mais" menu (Reports, Contatos, Settings)
        Settings.tsx            # Subscription management (reuses Phase 2 logic)
      components/
        TabBar.tsx              # Bottom 3+1 navigation
        HeroCard.tsx            # Glassmorphism stats hero
        RingChart.tsx           # Mini SVG donut
        Sparkline.tsx           # Mini SVG trend line
        WorkflowCard.tsx        # Expandable workflow card
        NewsRow.tsx             # Compact news row with status dot
        FilterChips.tsx         # Status filter pills
        GlassCard.tsx           # Reusable glassmorphism container
        MenuRow.tsx             # "Mais" menu item
        StatusDot.tsx           # Colored dot with glow
        Skeleton.tsx            # Loading skeleton
      lib/
        api.ts                  # API client: base URL + initData header
        theme.ts                # Design tokens + themeParams sync
        types.ts                # TypeScript interfaces for API responses
    dist/                       # Built assets (served by aiohttp)
  routes/
    mini_api.py                 # All /api/mini/* endpoints
    mini_static.py              # Serve Vite dist/ static files
```

## BotFather Configuration

After deployment:
1. Set Menu Button via BotFather → `/setmenubutton` with Mini App URL
2. Or: inline keyboard button in bot: `{"text": "📊 Dashboard", "web_app": {"url": "https://web-production-0d909.up.railway.app/mini/"}}`

## Performance Targets

- Initial load: under 2 seconds
- SWR stale-while-revalidate for instant perceived load on revisit
- Skeleton screens during loading (no blank/white screens)
- Vite code splitting per page (lazy loaded tabs)
- Lighthouse mobile score: target >90

## Testing Strategy

- Unit tests for API endpoints (mock GitHub API, mock Redis, mock Supabase)
- initData validation tests (valid/invalid/expired signatures)
- Frontend component tests with React Testing Library
- Sparkline and RingChart render tests
- E2E: manual testing in Telegram (Mini App environment is hard to automate)
- Performance: Lighthouse audit on built output

## Success Criteria

1. Mini App opens from bot menu button in under 2 seconds
2. Dark mode with glassmorphism visual — matches approved mockups
3. Home shows system health hero + workflow sparklines
4. Workflows expand to show history + trigger button with haptic feedback
5. News feed loads 156+ items with smooth scroll and filters
6. Reports browse/download works
7. Contacts toggle works
8. Authentication via initData — no login screen
9. Responsive on all phone sizes
