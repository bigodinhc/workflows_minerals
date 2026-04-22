# Architecture

**Analysis Date:** 2026-04-22

## Pattern Overview

**Overall:** Layered + Repository Pattern

The system uses a **3-layer architecture** with clear separation of concerns:
1. **Handler Layer** (Telegram bot routers) — receives user input & callbacks
2. **Service Layer** (dispatch, query handlers) — orchestrates workflows
3. **Repository Layer** (ContactsRepo) — abstracts Supabase data access

This enables testing of business logic independently of external services (Telegram, Supabase, Uazapi). The recent refactor migrated contacts storage from Google Sheets to Supabase via the `ContactsRepo` interface pattern.

**Key Characteristics:**
- **Async-first**: Aiogram + aiohttp for concurrent operations
- **Dependency Injection**: Services accept client/repo injections for testability
- **Callback-driven UI**: Aiogram's CallbackData factories for type-safe Telegram button interactions
- **FSM (Finite State Machine)**: Redis-backed state management for multi-step workflows
- **Error Categorization**: Structured error types for WhatsApp delivery failures

## Layers

**Handler Layer (Telegram Bot Routers):**
- Purpose: Parse Telegram updates, enforce authorization, dispatch to services
- Location: `webhook/bot/routers/`
- Contains: Command handlers (`commands.py`), message FSM handlers (`messages.py`), callback query handlers (`callbacks_*.py`)
- Depends on: ContactsRepo, dispatch, contact_admin utilities
- Used by: Aiogram dispatcher (webhook entry point at `webhook/bot/main.py`)

**Service/Orchestration Layer:**
- Purpose: Business logic, multi-step workflows, external API coordination
- Location: `webhook/dispatch.py`, `webhook/contact_admin.py`, `execution/core/`
- Contains:
  - `dispatch.py` — WhatsApp sending (idempotency, Redis checks)
  - `contact_admin.py` — Input parsing, state management, UI rendering
  - `execution/core/delivery_reporter.py` — Aggregated delivery results & error categorization
  - `execution/core/progress_reporter.py` — Workflow event logging
- Depends on: ContactsRepo, Telegram API, Uazapi, Redis, Supabase
- Used by: Handlers, scripts, webhook routes

**Repository Layer (Data Access):**
- Purpose: Isolate Supabase client calls, normalize phone numbers, enforce business rules
- Location: `execution/integrations/contacts_repo.py`
- Contains: `ContactsRepo` class with methods: `list_active()`, `list_all()`, `get_by_phone()`, `add()`, `toggle()`, `bulk_set_status()`
- Depends on: supabase-py client, phonenumbers library
- Used by: Handlers, dispatch, scripts, dashboard API (`dashboard/app/api/contacts/route.ts`)

**Data Layer:**
- Purpose: Persist contact data, event logs
- Location: `supabase/migrations/20260422_contacts.sql`
- Contains: `contacts` table (id, name, phone_raw, phone_uazapi, status, created_at, updated_at)
- Depends on: Supabase PostgreSQL, Telegram Webhook API, Uazapi, Google Sheets (legacy, being phased out)

## Data Flow

**Add Contact Flow (/add command):**

1. User sends `/add` → `cmd_add()` in `webhook/bot/routers/commands.py` sets FSM state `AddContact.waiting_data`
2. User sends text → `on_add_contact_data()` in `webhook/bot/routers/messages.py`
3. Parse input via `contact_admin.parse_add_input(text)` → extract name, phone
4. Validate phone via `normalize_phone()` in ContactsRepo → E.164 format
5. Call `ContactsRepo.add(name, phone, send_welcome=_sync_send_welcome)`:
   - Duplicate pre-check via `get_by_phone()`
   - Call injected `send_welcome()` → wraps `dispatch.send_whatsapp()` for Uazapi
   - Insert into `contacts` table
   - Handle race conditions (unique constraint on phone_uazapi)
6. On success: Send confirmation toast with active count
7. On error: Send user-friendly message (InvalidPhoneError, ContactAlreadyExistsError, etc.)

**List/Toggle Contact Flow (/list, toggle callbacks):**

1. User sends `/list [search]` → `cmd_list()` fetches page 1
2. `_render_list_view()` calls `ContactsRepo.list_all(search=search, page=page, per_page=10)`
3. Build inline keyboard via `contact_admin.build_list_keyboard(contacts)` → Contact Bulkbuttns + pagination
4. User taps contact button → `ContactToggle` callback fires in `webhook/bot/routers/callbacks_contacts.py`
5. `on_contact_toggle()` calls `ContactsRepo.toggle(phone)` → flips status ativo ↔ inativo
6. Refresh list message with updated keyboard

**Bulk Operations (ContactBulk/ContactBulkConfirm/ContactBulkCancel):**

1. User taps "✅ Ativar todos" or "❌ Desativar todos" button
2. `on_bulk_prompt()` counts matching contacts, shows confirmation dialog with `ContactBulkConfirm` callback
3. User taps "✅ Sim" → `on_bulk_confirm()` calls `ContactsRepo.bulk_set_status(status, search=search)`
4. Update message with result count

**WhatsApp Broadcast Flow:**

1. User sends `/s` → shows main menu
2. User taps "📲 Enviar Msg" → FSM state `BroadcastMessage.waiting_text`
3. User sends message text → `on_broadcast_text()` creates draft, shows preview + confirm buttons
4. User taps "✅ Enviar" → `BroadcastConfirm` callback triggers dispatch
5. `dispatch.send_whatsapp()` called for each active contact:
   - Idempotency check via Redis (24h window: `whatsapp:sent:<hash>`)
   - Uazapi HTTP POST to `{UAZAPI_URL}/api/message/send`
   - Error categorization (WhatsApp disconnected, rate limit, invalid number, etc.)
6. `DeliveryReporter` aggregates results, sends Telegram summary

**Event Log Flow (Internal):**

1. Scripts/handlers call `progress_reporter.emit(workflow, run_id, event, label, detail)`
2. Inserts to `execution_log` table (workflow, run_id, ts, level, event, label, detail)
3. `/tail <workflow>` command queries and formats last 30 events for user

**State Management (FSM):**

- Redis backend via `aiogram.fsm.storage.redis.RedisStorage`
- Key pattern: `fsm:<chat_id>:<user_id>:<state_key>`
- Used for: AddContact flow, AdjustDraft, RejectReason, BroadcastMessage, WriterInput
- TTL: Auto-expiry per state (contact_admin uses explicit 5-min TTL for legacy state dict)

## Key Abstractions

**ContactsRepo Interface:**
- Purpose: Abstract Supabase from business logic; enable testing with fake clients
- Examples: `execution/integrations/contacts_repo.py`
- Pattern: Dependency injection (optional `client=` parameter in `__init__`); test passes `MagicMock()` client
- Methods expose contact operations (CRUD, search, bulk) with domain-specific exceptions

**CallbackData Factories:**
- Purpose: Type-safe Telegram button serialization/deserialization
- Examples: `webhook/bot/callback_data.py` — `ContactToggle`, `ContactBulk`, `ContactBulkConfirm`, `ContactBulkCancel`
- Pattern: Aiogram's `CallbackData` base class with `.pack()` / `.filter()` decorators
- Benefit: Eliminates string-parsing bugs; strongly-typed callback parameters in handlers

**DeliveryReporter:**
- Purpose: Structured error reporting with categorization (circuit breaker logic)
- Examples: `execution/core/delivery_reporter.py`
- Pattern: Enum-based error categories, per-category action hints, sample contact names
- Used by: Broadcast dispatch, script execution logging

**FSM States:**
- Purpose: Multi-step user flows with state persistence
- Examples: `webhook/bot/states.py` — `AddContact`, `AdjustDraft`, `RejectReason`, `BroadcastMessage`
- Pattern: Aiogram's `StatesGroup` + Redis storage; auto-cleared by handlers or TTL

## Entry Points

**Telegram Webhook:**
- Location: `webhook/bot/main.py` (`create_app()`, `main()`)
- Triggers: Telegram sends update via POST to `/webhook`
- Responsibilities:
  1. Setup Aiogram dispatcher with routers (commands, callbacks, messages)
  2. Register Aiogram webhook handler with aiohttp
  3. Mount additional aiohttp routes (API, preview, mini-app)
  4. On startup: set webhook URL with Telegram API
  5. On shutdown: delete webhook, close bot session

**CLI Scripts:**
- Location: `execution/scripts/` (send_daily_report.py, send_news.py, baltic_ingestion.py, etc.)
- Triggers: GitHub Actions cron jobs
- Responsibilities: Data ingestion, report generation, WhatsApp broadcast via `dispatch.send_whatsapp()`

**Admin Commands:**
- Location: `webhook/bot/routers/commands.py`
- Triggers: User types `/command` in Telegram
- Examples: `/add` (AddContact FSM), `/list` (fetch & render), `/status` (workflow health), `/tail` (event log)

**HTTP API Routes:**
- Location: `webhook/routes/api.py`, `webhook/routes/mini_api.py`
- Triggers: External services or frontend
- Examples: Report downloads, workflow status, mini-app auth

## Error Handling

**Strategy:** Multi-level validation with domain-specific exceptions

**Patterns:**

1. **Validation Layer** (`contact_admin.py`):
   - `parse_add_input()` — raises `ValueError` for bad format
   - Phone regex check — no unexpected characters
   - Length check — 10-15 digits

2. **Repository Layer** (`ContactsRepo`):
   - `normalize_phone()` — raises `InvalidPhoneError` for unparseable/invalid phones
   - `add()` — pre-check for duplicate, post-insert unique constraint race condition handling
   - Raises `ContactAlreadyExistsError` with existing contact details
   - Raises `ContactNotFoundError` on lookups that fail

3. **Handler Layer** (routers):
   - Catch domain exceptions → user-friendly Telegram messages
   - Log errors with `logger.error()` for ops visibility
   - Send error toast via `query.answer()`

4. **WhatsApp Sending** (`dispatch.send_whatsapp()`):
   - Categorize HTTP errors: WHATSAPP_DISCONNECTED, RATE_LIMIT, INVALID_NUMBER, UPSTREAM_5XX, AUTH, TIMEOUT, NETWORK, UNKNOWN
   - Circuit breaker: abort if 5 consecutive FATAL_CATEGORIES errors
   - Idempotency via Redis: prevent duplicate sends

## Cross-Cutting Concerns

**Logging:** 
- Framework: `logging` module (stdlib)
- Approach: Each module uses `logger = logging.getLogger(__name__)`
- Sentry integration: Optional via `SENTRY_DSN` env var (captures exceptions + breadcrumbs)
- Level: INFO for startup, WARNING for degradation, ERROR for operational issues

**Validation:** 
- Phone numbers: libphonenumber (phonenumbers library) for E.164 parsing
- Input parsing: Custom parsers in `contact_admin.py` + form validators in Aiogram filters
- Schema validation: (Not yet) — candidates: pydantic, zod (dashboard)

**Authentication:** 
- Telegram: Bot token in env var + webhook URL verification
- Admin checks: `RoleMiddleware` via `bot.users.is_admin(chat_id)`
- Supabase: Service role key (no RLS for internal scripts); RLS disabled in contacts table (service_role only)

**Async Coordination:**
- Framework: Aiogram + aiohttp
- Thread-bridging: `asyncio.to_thread()` for blocking repos/scripts (ContactsRepo, delivery_reporter)
- Background tasks: `create_background_task()` in `bot/main.py` for long-running ops
- Redis async: `redis_async.from_url()` for idempotency checks, FSM state

---

*Architecture analysis: 2026-04-22*
