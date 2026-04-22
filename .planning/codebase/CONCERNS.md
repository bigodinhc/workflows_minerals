# Codebase Concerns

**Analysis Date:** 2026-04-22

## Tech Debt

### Orphaned rationale_dispatcher module
- **Issue:** `execution/curation/rationale_dispatcher.py` marked as orphaned after Bot Navigation v1.1
- **Files:** `execution/curation/rationale_dispatcher.py:1-8` (TODO comment)
- **Impact:** Dead code path not called by router; maintains state files that may accumulate
- **Fix approach:** 
  1. Audit dependencies (check if any scripts manually invoke it)
  2. Remove file if confirmed unreachable
  3. If keeping as utility, extract to separate utilities package and add deprecation notice

### Google Sheets to Supabase migration incomplete
- **Issue:** Mini API contacts endpoints (`webhook/routes/mini_api.py`) still read from Google Sheets via `SheetsClient` while core bot uses Supabase `ContactsRepo`
- **Files:** 
  - `webhook/routes/mini_api.py:407-433` (get_contacts — uses SheetsClient)
  - `webhook/routes/mini_api.py:436-456` (toggle_contact — uses SheetsClient)
  - `webhook/routes/mini_api.py:492-502` (_fetch_contacts_active — uses SheetsClient)
- **Impact:** 
  - Mini app reads stale contact data (Sheets not synced with Supabase)
  - Dual writes: bot /add writes to Supabase, mini_api toggle writes to Sheets only
  - Contact list divergence between bot and Mini App frontend
- **Fix approach:**
  1. Replace `SheetsClient` calls with `ContactsRepo` in mini_api.py lines 407-502
  2. Update response format from Sheets columns to Contact dataclass fields
  3. Add integration test ensuring bot /add and mini_api toggle affect same contact
  4. Deprecate/remove sheets_client.py in next phase

### Unconfirmed Supabase table schema
- **Issue:** `execution/integrations/supabase_client.py:22` has TODO comment about table name confirmation
- **Files:** `execution/integrations/supabase_client.py:22` 
- **Impact:** Prices fetch assumes `sgx_prices` table but schema not verified; queries may fail silently
- **Fix approach:** 
  1. Create SQL migration for sgx_prices table with schema documentation
  2. Remove TODO comment after confirmation
  3. Add tests that verify table exists and has expected columns

## Migration-in-Progress Risks

### Data consistency gap: Mini API reads Sheets, bot writes Supabase
- **Symptoms:** A contact added via `/add` (bot → Supabase) won't appear in Mini App contact list until manual resync
- **Files:**
  - Read: `webhook/routes/mini_api.py:413-415` (SheetsClient.list_contacts)
  - Write: `webhook/bot/routers/messages.py:210-213` (ContactsRepo.add)
- **Trigger:** Add contact via bot, refresh Mini App UI
- **Workaround:** None — requires code fix
- **Priority:** HIGH — breaks end-user experience in Mini App

### Race condition: Dual write during migration
- **Symptoms:** Contact toggled in bot (Supabase) but old status visible in Mini App (still reading Sheets)
- **Files:**
  - Bot toggle: `webhook/bot/routers/callbacks_contacts.py:35` (repo.toggle)
  - Mini toggle: `webhook/routes/mini_api.py:442-445` (sheets.toggle_contact)
- **Trigger:** User toggles same contact via bot and Mini App in quick succession
- **Workaround:** Refresh Mini App after toggling in bot
- **Priority:** HIGH — can corrupt broadcast list

### Legacy test suite still mocks gspread
- **Files:** `tests/test_sheets_contact_ops.py` (full suite)
- **Impact:** Tests don't prevent regression to gspread; migration not enforced by CI
- **Fix approach:** 
  1. Migrate test_sheets_contact_ops.py to test_mini_api_contacts.py using ContactsRepo
  2. Delete test_sheets_contact_ops.py once verified
  3. Add integration test comparing bot /add with mini_api GET response

## Known Bugs

### Telegram message edit flooding
- **Description:** `dispatch.py` catches `TelegramBadRequest` for "flood" but continues processing; may lose progress updates
- **Files:** `webhook/dispatch.py:138-147`
- **Symptoms:** 
  - Flood control hits but broadcast continues without user updates
  - Metrics recorded but user sees blank screen
- **Trigger:** Rapid /approve on multiple news items
- **Workaround:** Wait and retry
- **Improvement:** Add exponential backoff before retrying edit; buffer messages instead of failing silently

### Supabase service_role key usage without scope restriction
- **Description:** All ContactsRepo writes use SUPABASE_KEY (likely service_role) with no column-level RLS
- **Files:** `execution/integrations/contacts_repo.py:108-112`
- **Impact:** Any code with env var can insert/update/delete all contacts without row-level scope
- **Security note:** Comments say "no policies, service_role bypasses RLS" but this is intentional per design
- **Recommendation:** Consider adding anon/user policies as defense-in-depth (see Security section)

## Security Considerations

### RLS policies disabled on contacts table (by design)
- **Risk:** service_role key compromise = full contacts table compromise
- **Files:** `supabase/migrations/20260422_contacts.sql:34-35`
- **Current mitigation:** 
  - SUPABASE_KEY env var protected (not in git via .gitignore)
  - service_role key not exposed to client code
  - Contacts only modified by trusted webhook endpoints
- **Recommendations:**
  1. Add audit logging to trigger when contacts.status changes
  2. Consider adding row-level policies for future subscriber-facing APIs (if any)
  3. Document in code why RLS is off (emergency access reason? scalability?)

### Telegram user input not strictly validated in /add flow
- **Risk:** Phone number bypass in contact_admin.parse_add_input allows some unexpected characters
- **Files:** `webhook/contact_admin.py:60-63`
- **Details:**
  - Allowed chars: `"+0123456789 -().@swhatpne"` (note typo: "swhatpne" instead of "whatsapp"?)
  - phonenumbers.parse will reject invalid formats but silent acceptance of `@` is odd
- **Fix approach:**
  1. Clarify intent of allowed_chars (remove suspicious chars like @)
  2. Document why certain punctuation allowed
  3. Add test for rejected/allowed patterns

### UAZAPI token transmitted in callback data (test endpoint)
- **Risk:** Token logged or cached in callback_data string
- **Files:** `webhook/dispatch.py:254-289` (process_test_send_async takes uazapi_token param)
- **Details:** Test send passes token via function param; if logged, token exposed
- **Current mitigation:** Token not logged in normal flow; test endpoint admin-only
- **Fix approach:**
  1. Audit logging statements for uazapi_token references
  2. Consider using session/store instead of passing token in params
  3. Add secret masking in logs: `UAZAPI_TOKEN: 'SET (xxxxx...)'` pattern already in use

### SUPABASE_KEY in environment not scoped
- **Risk:** All Supabase tables accessible with same key
- **Files:** `execution/integrations/contacts_repo.py:109`
- **Recommendation:** 
  - Use separate scoped API keys per service if using Supabase multi-tenant
  - Document key scoping policy
  - Consider JWT RLS in production (Supabase supports user JWT claims)

### Input validation gap: search parameter in /list
- **Risk:** SQL injection via ilike in search filters
- **Files:** 
  - `execution/integrations/contacts_repo.py:139` (list_all uses ilike)
  - `webhook/contact_admin.py:165-166` (search split from /list command)
- **Mitigation:** Supabase SDK parameterizes ilike; OWASP injection risk low
- **Note:** User input only trusted from TELEGRAM_CHAT_ID (admin only) so blast radius limited

## Performance Bottlenecks

### Sync ContactsRepo wrapped in asyncio.to_thread
- **Problem:** Every contact query blocks async task
- **Files:**
  - `webhook/dispatch.py:70-72` (get_contacts in broadcast loop)
  - `webhook/bot/routers/callbacks_contacts.py:35,76,117` (toggle and list)
  - `webhook/routes/mini_api.py:413-415` (list_contacts)
- **Impact:** Scales to ~1 DB call per broadcast iteration; blocks Telegram webhook
- **Improvement path:**
  1. Move to async Supabase client (supabase-py supports async in newer versions)
  2. Batch contact fetches to reduce call count
  3. Cache active contacts list in Redis with 5min TTL

### N+1 queries in build_list_keyboard
- **Problem:** Renders single page of contacts without full data pre-fetch
- **Files:** `webhook/contact_admin.py:141-200` (renders buttons but doesn't fetch all)
- **Details:** Pagination handled by ContactsRepo.list_all which fetches per-page, not the issue
- **Note:** This is actually efficient; not a blocker

### Redis lazy-initialized on every broadcast
- **Problem:** _get_redis_async() called per send_whatsapp; creates new connections
- **Files:** `webhook/dispatch.py:51-59` 
- **Impact:** Single global _redis_async_client mitigates; initial call slower
- **Note:** Actually fine; singleton pattern in place

## Fragile Areas

### Uazapi welcome message hardcoded without fallback
- **Problem:** /add sends welcome message before DB insert; no retry on uazapi down
- **Files:** `webhook/bot/routers/messages.py:206-212`
- **Trigger:** Uazapi unavailable but Telegram up
- **Symptoms:** Contact added to DB but no welcome sent; user confused
- **Safer approach:**
  1. Insert first with status "pending"
  2. Send welcome async with retry
  3. Mark as "ativo" only after confirmed send (or separate notification channel)

### Contact.toggle has no concurrency protection
- **Problem:** Two simultaneous toggles via bot and mini_api race
- **Files:** `webhook/bot/routers/callbacks_contacts.py:35` (repo.toggle)
- **Details:** Reads status, flips, writes back; no atomic CAS
- **Safety:** Supabase unique index prevents duplicate inserts but doesn't protect toggle race
- **Fix approach:** Use Supabase RLS with timestamp-based optimistic locking or CTE update

### Callback data parsing relies on aiogram's CallbackData
- **Problem:** If callback string mangled, handler raises unpredictable exceptions
- **Files:** `webhook/bot/callback_data.py:1-112` (factories)
- **Mitigation:** Aiogram validates factory unpacking; invalid data logged
- **Note:** Acceptable risk with admin-only routes

### Error handling swallows exceptions in dispatch
- **Problem:** send_whatsapp catches all exceptions; logs but doesn't distinguish errors
- **Files:** `webhook/dispatch.py:116-118` (generic "send error" log)
- **Impact:** Uazapi timeout vs. bad token both logged same way; hard to debug
- **Fix approach:**
  1. Catch aiohttp.ClientError separately from JSON/network errors
  2. Log error type and code in structured format
  3. Send different Telegram message to user per error category

## Observability Gaps

### Silent exception swallowing in _fetch_contacts_active
- **Problem:** Exception caught and logged but count returns 0 (default)
- **Files:** `webhook/routes/mini_api.py:492-502`
- **Impact:** Stats show 0 contacts if Sheets query fails; user doesn't know why
- **Fix:** Add metric or log alert threshold for repeated failures

### No structured logging in ContactsRepo
- **Problem:** Phone normalization errors logged via exception; no correlation with request
- **Files:** `execution/integrations/contacts_repo.py:42-75` (no logs)
- **Impact:** Debugging user "contact added but not working" requires app logs + DB query
- **Fix approach:**
  1. Add logger to ContactsRepo class
  2. Log phone input, normalized form, and query result
  3. Include request_id for tracing

### Missing metrics for contact operations
- **Problem:** No counters for add/toggle/list operations
- **Files:** `execution/integrations/contacts_repo.py` (full module)
- **Impact:** Can't detect abuse (repeated add attempts) or capacity planning
- **Fix approach:**
  1. Add prometheus counter for contacts_added_total, contacts_toggled_total
  2. Add histogram for contacts_list_duration_seconds
  3. Dashboard for /add success/failure rate

### Async task fire-and-forget with no error tracking
- **Problem:** `asyncio.create_task(process_news(...))` in messages.py line 270 not awaited
- **Files:** `webhook/bot/routers/messages.py:270`
- **Impact:** If process_news crashes, error only in task exception log; user never notified
- **Fix:** Use task group (TaskGroup in Python 3.11+) or explicit exception callback

### Silent failures in report generation
- **Problem:** Gallery of try/except blocks return empty list on error instead of propagating
- **Files:** 
  - `webhook/routes/mini_api.py:231-254` (staging/archive/rejected fetch)
  - Each catches Exception and returns []
- **Impact:** User sees empty news list; can't tell if Supabase down or no items
- **Fix:** Return error in response body, e.g., `{"items": [], "error": "Redis unavailable"}`

---

*Concerns audit: 2026-04-22*
