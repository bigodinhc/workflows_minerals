# Phase 1: Migration Flask → Aiogram 3

**Date:** 2026-04-16
**Status:** Approved
**Approach:** Clean rewrite (big bang) — new Aiogram 3 bot reusing extracted business logic modules

## Context

The SuperMustache Bot currently runs on Flask + raw requests to Telegram Bot API. It was recently refactored into focused modules:

- `telegram.py` — Bot API wrappers (will be REPLACED by Aiogram SDK)
- `pipeline.py` — Claude 3-agent chain (REUSED as-is, made async)
- `dispatch.py` — WhatsApp delivery (REUSED as-is, made async)
- `reports_nav.py` — Reports navigation (REUSED, adapted to Aiogram handlers)
- `status_builder.py` — /status message (REUSED as-is)
- `callback_router.py` — Callback dispatcher (REPLACED by Aiogram routers)
- `contact_admin.py` — Contact add flow (REUSED, adapted to FSM)
- `query_handlers.py` — Query formatters (REUSED as-is)
- `redis_queries.py` — Redis feedback (REUSED as-is)
- `workflow_trigger.py` — GitHub Actions trigger (REUSED, made async)
- `digest.py` — Message formatting (REUSED as-is)

## Goals

1. Replace Flask + raw requests with Aiogram 3 + aiohttp
2. Replace manual state dicts (ADJUST_STATE, REJECT_REASON_STATE) with Aiogram FSM + Redis storage
3. Replace threading.Thread with asyncio tasks
4. Replace manual callback string parsing with CallbackData factories
5. Keep all existing functionality working — zero feature regression
6. Keep aiohttp routes for GitHub Actions endpoints (/store-draft, /seen-articles, /health)
7. Deploy on Railway with webhook mode (same infrastructure)

## Non-Goals

- No new features in this phase (subscriptions, Telegram delivery, UX improvements — those are Phase 2)
- No framework change for the execution/ directory — only webhook/ changes
- No changes to GitHub Actions workflows
- No changes to the dashboard

## Architecture

### Before (Flask)

```
Railway (gunicorn)
  └── Flask app (app.py)
       ├── POST /webhook → telegram_webhook() → handle_callback() / command handlers
       ├── POST /store-draft → store_draft()
       ├── GET/POST /seen-articles
       ├── GET /health
       ├── GET /preview/<id>
       └── POST /admin/register-commands
```

### After (Aiogram 3 + aiohttp)

```
Railway (python -m bot.main)
  └── aiohttp app
       ├── Aiogram webhook handler (POST /webhook)
       │    ├── Router: commands (start, status, queue, history, etc.)
       │    ├── Router: callbacks (curation, reports, workflows, contacts, menu)
       │    ├── Router: messages (text input — FSM states)
       │    └── Middleware: auth, logging
       ├── POST /store-draft (aiohttp route — GitHub Actions)
       ├── GET/POST /seen-articles (aiohttp route — GitHub Actions)
       ├── GET /health (aiohttp route)
       ├── GET /preview/<id> (aiohttp route — Jinja2 template)
       └── POST /admin/register-commands (aiohttp route)
```

### Directory Structure

```
webhook/
  bot/
    __init__.py
    main.py              — Entry point: aiohttp app + Aiogram webhook setup
    config.py             — Environment variables, constants
    middlewares/
      __init__.py
      auth.py             — Admin authorization middleware
    routers/
      __init__.py
      commands.py         — /start, /status, /queue, /history, /stats, etc.
      callbacks.py        — All callback query handlers (curation, menu, reports, workflows)
      messages.py         — FSM text input handlers (adjust feedback, reject reason, add contact, news text)
    states.py             — FSM StatesGroup definitions
    callbacks.py          — CallbackData factory definitions
    keyboards.py          — Inline keyboard builders
  routes/
    __init__.py
    api.py                — aiohttp routes for /store-draft, /seen-articles, /health
    preview.py            — aiohttp route for /preview/<id> with Jinja2
  # Existing modules (reused)
  pipeline.py
  dispatch.py
  reports_nav.py
  status_builder.py
  contact_admin.py
  query_handlers.py
  redis_queries.py
  workflow_trigger.py
  digest.py
  templates/
    preview.html
```

### Deleted files (replaced)
- `app.py` — replaced by `bot/main.py` + `routes/api.py`
- `telegram.py` — replaced by Aiogram SDK (bot.send_message, etc.)
- `callback_router.py` — replaced by `bot/routers/callbacks.py`

## Key Design Decisions

### 1. FSM States

Replace in-memory dicts with Aiogram FSM:

```python
# bot/states.py
from aiogram.fsm.state import State, StatesGroup

class AdjustDraft(StatesGroup):
    waiting_feedback = State()  # replaces ADJUST_STATE

class RejectReason(StatesGroup):
    waiting_reason = State()  # replaces REJECT_REASON_STATE

class AddContact(StatesGroup):
    waiting_data = State()  # replaces contact_admin.ADD_STATE

class NewsInput(StatesGroup):
    processing = State()  # guards against double-submit while pipeline runs
```

FSM storage: `RedisStorage` using the existing `REDIS_URL` — state survives redeploys.

### 2. CallbackData Factories

Replace string parsing with typed factories:

```python
# bot/callbacks.py
from aiogram.filters.callback_data import CallbackData

class CurateAction(CallbackData, prefix="curate"):
    action: str  # archive, reject, pipeline
    item_id: str

class DraftAction(CallbackData, prefix="draft"):
    action: str  # approve, test_approve, adjust, reject
    draft_id: str

class MenuAction(CallbackData, prefix="menu"):
    target: str  # reports, queue, history, rejections, stats, status, etc.

class ReportType(CallbackData, prefix="rpt_type"):
    report_type: str

class ReportYear(CallbackData, prefix="rpt_year"):
    report_type: str
    year: int

class ReportMonth(CallbackData, prefix="rpt_month"):
    report_type: str
    year: int
    month: int

class ReportDownload(CallbackData, prefix="report_dl"):
    report_id: str

class ReportBack(CallbackData, prefix="rpt_back"):
    target: str  # types, type:<name>, years:<name>, year:<name>:<year>

class ReportYears(CallbackData, prefix="rpt_years"):
    report_type: str

class QueuePage(CallbackData, prefix="queue_page"):
    page: int

class QueueOpen(CallbackData, prefix="queue_open"):
    item_id: str

class ContactToggle(CallbackData, prefix="tgl"):
    phone: str

class ContactPage(CallbackData, prefix="pg"):
    page: int
    search: str = ""

class WorkflowRun(CallbackData, prefix="wf_run"):
    workflow_id: str

class WorkflowList(CallbackData, prefix="wf"):
    action: str  # list, back_menu
```

### 3. Auth Middleware

Replace repeated `if not contact_admin.is_authorized(chat_id)` with middleware:

```python
# bot/middlewares/auth.py
from aiogram import BaseMiddleware

class AdminAuthMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        chat_id = event.from_user.id if hasattr(event, 'from_user') else None
        if chat_id and not contact_admin.is_authorized(chat_id):
            return  # silently ignore unauthorized users
        return await handler(event, data)
```

Applied to admin-only routers. The `/start` command stays on a public router.

### 4. Async Adaptation

Modules that call external APIs need async versions:

- `pipeline.py` — `call_claude()` uses `anthropic.Anthropic` (sync). Replace with `anthropic.AsyncAnthropic`.
- `dispatch.py` — `send_whatsapp()`, `get_contacts()` use `requests`. Replace with `aiohttp.ClientSession`.
- `workflow_trigger.py` — uses `requests`. Replace with `aiohttp.ClientSession`.
- `reports_nav.py` — Supabase calls are sync. Wrap with `asyncio.to_thread()` initially, migrate to async later.

### 5. Background Tasks

Replace `threading.Thread(target=..., daemon=True).start()` with:

```python
asyncio.create_task(process_news(chat_id, text, progress_msg_id))
```

Use structured task tracking to avoid fire-and-forget leaks:

```python
# bot/main.py
_background_tasks: set[asyncio.Task] = set()

def create_background_task(coro):
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task
```

### 6. GitHub Actions Endpoints

These stay as plain aiohttp routes (not Aiogram handlers):

```python
# routes/api.py
from aiohttp import web

routes = web.RouteTableDef()

@routes.post("/store-draft")
async def store_draft(request: web.Request) -> web.Response:
    data = await request.json()
    # Same logic as current Flask route
    ...

@routes.get("/health")
async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", ...})
```

Mounted on the same aiohttp app that serves the Aiogram webhook.

### 7. Deployment

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY webhook/ ./webhook/
COPY execution/ ./execution/
COPY .github/ ./.github/
EXPOSE 8080
CMD ["python", "-m", "webhook.bot.main"]
```

```json
// railway.json
{
  "build": { "builder": "DOCKERFILE" },
  "deploy": {
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10,
    "startCommand": "python -m webhook.bot.main"
  }
}
```

No more gunicorn — aiohttp serves directly.

### 8. Dependencies

New in requirements.txt:
```
aiogram>=3.4.0,<4.0
aiohttp>=3.9.0
aiohttp-jinja2>=1.6  # for preview template
```

Removed:
```
flask
gunicorn
```

Kept:
```
anthropic>=0.40.0  # will use AsyncAnthropic
requests  # still used by execution/ scripts (not webhook)
redis>=5.0,<6.0  # Aiogram RedisStorage uses same connection
```

## Migration Strategy

### What gets rewritten
- `app.py` → `bot/main.py` + `routes/api.py` + `routes/preview.py`
- `telegram.py` → deleted (Aiogram SDK replaces it)
- `callback_router.py` → `bot/routers/callbacks.py` (using CallbackData factories)

### What gets adapted (async + Aiogram patterns)
- `pipeline.py` — sync→async (AsyncAnthropic)
- `dispatch.py` — sync→async (aiohttp.ClientSession)
- `workflow_trigger.py` — sync→async (aiohttp.ClientSession)
- `contact_admin.py` — state management → FSM
- `reports_nav.py` — sync Supabase → asyncio.to_thread() wrapper

### What stays unchanged
- `status_builder.py`
- `query_handlers.py`
- `redis_queries.py`
- `digest.py`
- `execution/` (entire directory)
- `.github/workflows/` (all GitHub Actions)
- `dashboard/` (Next.js frontend)
- `actors/` (Apify scrapers)

## Testing Strategy

- Port existing 295 tests to work with new module structure
- Add Aiogram-specific tests using `aiogram.testing` (MockBot, MockTelegram)
- Test FSM state transitions (adjust, reject reason, add contact)
- Test CallbackData serialization/deserialization
- Test aiohttp routes (store-draft, seen-articles, health)
- Integration test: webhook receives update → correct handler fires

## Rollback Plan

Keep the current Flask code in a `webhook_flask_backup/` branch. If migration fails, revert to Flask with `git checkout`.

## Success Criteria

1. All existing bot functionality works identically from user perspective
2. FSM state persists across Railway redeploys (Redis-backed)
3. No more threading.Thread — all async
4. All 295+ tests pass (adapted or rewritten)
5. GitHub Actions endpoints (/store-draft, /seen-articles) work unchanged
6. Deploy on Railway with webhook mode
7. Response time equal or better than Flask version
