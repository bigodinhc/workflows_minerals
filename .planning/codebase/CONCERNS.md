# Codebase Concerns

**Analysis Date:** 2026-04-17

## Security

### Hardcoded API Key (CRITICAL)

**Issue:** IronMarket API key exposed in source code with comment suggesting awareness of security risk.

**File:** `execution/scripts/baltic_ingestion.py:36`

```python
IRONMARKET_API_KEY = "ironmkt_WUbuYLe4m06GTiYos_fVwvBfNa2l8GWoJtE9K8MJFCY" # Keeping hardcoded as requested, or load from env
```

**Impact:** If repository is exposed (git clone, archive leak), IronMarket API credentials are compromised. Could allow unauthorized API calls costing resources.

**Fix approach:** Move to environment variable with fallback. Use `os.getenv("IRONMARKET_API_KEY", IRONMARKET_API_KEY_DEFAULT)` and document required env vars in README.

---

### Unvalidated User Input in Telegram Handlers

**Issue:** User-provided text (especially in broadcast/news) flows directly to WhatsApp with minimal validation beyond truncation.

**Files:** 
- `webhook/bot/routers/messages.py:93-130` (BroadcastMessage handler)
- `webhook/dispatch.py:65-75` (send_whatsapp function)

**Evidence:** No input sanitization, XSS-like risk if WhatsApp client interprets HTML/special chars. Markdown escaping exists in `query_handlers.py` but not uniformly applied to all user inputs.

**Impact:** Malicious users could inject commands or formatting that breaks WhatsApp display or bypasses intended formatting.

**Fix approach:** 
1. Centralize input validation for all user-provided text
2. Use `_escape_md()` consistently before sending to WhatsApp
3. Add length validation beyond truncation
4. Test edge cases (emoji, RTL text, special unicode)

---

### Missing Rate Limiting on Webhook Endpoints

**Issue:** No rate limiting on `/api/mini/*` endpoints or webhook callbacks. An attacker could spam requests.

**Files:** 
- `webhook/routes/mini_api.py` (all routes)
- `webhook/routes/api.py` (approval/test endpoints)

**Impact:** Bot could be disabled via request flooding. No protection against distributed abuse.

**Fix approach:** Add aiohttp-based rate limiter (e.g., `aiolimiter` package) per IP/user_id with exponential backoff. Configurable via env var.

---

### Weak Auth on /api/mini Routes (Authorization Only, No Rate Limiting)

**Issue:** Auth via Telegram initData validates signature but no secondary checks exist. Replay attacks or token forgery not explicitly defended against.

**File:** `webhook/routes/mini_auth.py:19-36`

```python
data = safe_parse_webapp_init_data(TELEGRAM_BOT_TOKEN, init_data)
```

**Impact:** Relies entirely on aiogram's `safe_parse_webapp_init_data()`. If signature validation is weak, unauthorized access possible. No timestamp validation visible.

**Fix approach:** 
1. Verify Telegram's recommended `query_id` uniqueness + timestamp freshness check
2. Add blacklist for revoked tokens (not currently done)
3. Add secondary check for user role freshness (cache with TTL)

---

## Technical Debt & Fragile Areas

### Catch-All Message Handler Recently Fixed But Pattern Remains Risky

**Issue:** Recent commits (2cab598, a6214a0, 17135d8) show repeated bugs with catch-all text handlers intercepting FSM state messages.

**File:** `webhook/bot/routers/messages.py` (entire message_router)

**Evidence:** Git history shows:
- 17135d8: "catch-all news handler intercepting text + draft not found"
- a6214a0: "use StateFilter(None) on catch-all to prevent 3-agent activation"
- 2cab598: "remove catch-all text handler, add explicit Writer button"

**Impact:** FSM states can be silently intercepted. Users typing in AdjustDraft state or RejectReason state could have messages misrouted.

**Safe modification:** 
1. Register specific FSM handlers BEFORE any F.text handlers
2. Use explicit `StateFilter` on all FSM transitions
3. Add integration test for FSM state isolation (TextInputStateTest)
4. Consider router registration order as documented in code (line 90 comment)

---

### God File: callbacks.py (601 lines)

**Issue:** Single router with 601 lines handling all callback logic (reports, queues, contacts, workflows, approvals).

**File:** `webhook/bot/routers/callbacks.py`

**Impact:** Hard to modify, test, or review. Small change risks side effects. Multiple error handling patterns.

**Fix approach:** Split into domain routers:
- `callbacks_curation.py` — draft adjust/reject/approve
- `callbacks_reports.py` — report navigation
- `callbacks_queue.py` — queue pagination
- `callbacks_contacts.py` — contact toggle/admin
- `callbacks_workflows.py` — workflow triggers

Each <200 lines, easier to test.

---

### Inline Imports Shadow Module Scope

**Issue:** Many functions import modules inside function bodies, creating hidden dependencies and making tracing harder.

**Files:**
- `webhook/bot/routers/commands.py` — `from reports_nav import ...` inside functions
- `webhook/bot/routers/callbacks.py` — `from reports_nav import ...`, `from workflow_trigger import ...`
- `webhook/dispatch.py` — `from bot.keyboards import ...` inside functions

**Impact:** Circular import risks, harder to trace dependencies. Makes static analysis difficult.

**Fix approach:** Move all imports to module top. Use `.` relative imports. Document cyclic dependencies in module docstring.

---

### Silent Exception Swallowing in edit_message_text Calls

**Issue:** Many handlers catch all exceptions on edit calls and silently pass.

**Files:**
- `webhook/dispatch.py:104-105, 121-122, 145-146`
- `webhook/bot/routers/callbacks.py:53-58` (in _finalize_card)

**Evidence:**
```python
try:
    await bot.edit_message_text(...)
except Exception:
    pass  # ignore "message not modified" — don't crash delivery
```

**Impact:** Messages not being edited silently fail. User sees stale state. Hard to debug in production.

**Fix approach:** 
1. Catch specific exceptions: `aiogram.exceptions.TelegramBadRequest` with "message is not modified" substring
2. Log other exceptions with level WARNING
3. Add counter metric for edit failures
4. Return success/failure flag from helpers

---

### Trailing-Space Path Issue (Known from Memory)

**Issue:** Supabase storage paths may have trailing spaces causing mismatches on retrieval.

**Files:** `webhook/reports_nav.py` (noted in memory as "trailing-space path")

**Impact:** Reports may not be downloadable or retrievable if path keys don't match.

**Fix approach:** 
1. Strip all path keys on write and read: `path.strip()`
2. Add migration script to fix existing paths
3. Add validation test for path normalization

---

## Performance Bottlenecks

### Blocking Sync Google Sheets Calls in Async Context

**Issue:** `_get_contacts_sync()` makes blocking Google Sheets API calls in webhook handler, bridged with `asyncio.to_thread()` but still synchronous.

**File:** `webhook/dispatch.py:25-47`

```python
def _get_contacts_sync():
    """Fetch WhatsApp contacts from Google Sheets (sync)."""
    # ... max 3 retries with sleep()
```

**Impact:** If Google Sheets API is slow (2-3s per request), thread pool fills up. Multiple simultaneous requests can exhaust thread pool.

**Fix approach:** 
1. Implement async Google Sheets client using `aiohttp` directly instead of gspread
2. Or: cache contacts in Redis with TTL=300s to reduce API calls
3. Add timeout to thread: `asyncio.wait_for(asyncio.to_thread(...), timeout=10)`

---

### N+1 Query Pattern in Mini API (News Endpoint)

**Issue:** `list_staging()` and `list_archive_recent()` fetch all items from Redis, then filter in Python. For large queues, this loads entire dataset into memory.

**Files:** 
- `webhook/routes/mini_api.py:160-175` (news endpoint)
- `webhook/redis_queries.py:78-86` (list_staging implementation)

**Evidence:** `pipe.execute()` returns all keys, then Python does filtering:
```python
items = [pipe.execute()]  # All items loaded
return [_staging_to_news_item(i) for i in staging]  # Python filter
```

**Impact:** For 10k+ items in queue, allocates large in-memory list. Slow pagination.

**Fix approach:** 
1. Use Redis Lua scripting to filter on server side
2. Or: implement cursor-based pagination with SCAN
3. Set Redis key limit: max 500 items in staging, archive excess to Postgres

---

### Missing TTL on Redis Curation State

**Issue:** Most Redis keys in curation layer lack explicit TTL. Long-lived entries accumulate.

**File:** `execution/curation/redis_client.py:56`

```python
client.expire(key, _SEEN_TTL_SECONDS)  # Only on SEEN items
```

**Impact:** Other keyspaces (staging, archive, feedback) grow unbounded. Redis memory usage climbs.

**Fix approach:** 
1. Set TTL on all keys at creation: `client.setex(key, ttl_seconds, value)`
2. Configure TTLs per keyspace:
   - staging: 7 days
   - archive: 30 days
   - feedback: 60 days
   - seen: 1 day (current)
3. Add Redis memory monitoring/alerts

---

### Synchronous Platts Scraping May Block Event Loop

**Issue:** `platts_ingestion.py` and `baltic_ingestion.py` run as scheduled jobs with synchronous API calls.

**Files:** `execution/scripts/platts_ingestion.py`, `execution/scripts/baltic_ingestion.py`

**Impact:** If scraping takes 5+ minutes, blocks other events. No async/await pattern.

**Fix approach:** Convert to async with `aiohttp` for all HTTP calls. Use `asyncio.gather()` to parallelize requests.

---

## Reliability

### No Idempotency Token on WhatsApp Send (Webhook Handler)

**Issue:** `send_whatsapp()` has no idempotency check. If webhook is called twice, message sends twice.

**File:** `webhook/dispatch.py:65-77`

**Impact:** Users may receive duplicate WhatsApp messages. Confusing UX.

**Fix approach:** 
1. Generate idempotency key per send: hash(phone + timestamp + message_hash)
2. Store in Redis with TTL=3600s
3. Check key before calling UAZAPI: if key exists, return cached response
4. Use key in UAZAPI call if API supports it (check docs)

---

### Retry Logic Missing on GitHub API Calls

**Issue:** `_fetch_github_runs()` makes single request with generic exception handler, no retry.

**File:** `webhook/routes/mini_api.py:58-70`

**Evidence:**
```python
except Exception as exc:
    logger.error("GitHub API error: %s", exc)
return {"workflow_runs": []}  # Silent failure
```

**Impact:** Temporary GitHub outage returns empty runs list, UI shows "no runs". User has no visibility into transient failure vs real empty state.

**Fix approach:** 
1. Use `@retry_with_backoff` decorator from `execution.core.retry` (already exists)
2. Add exponential backoff: 1s, 2s, 4s max
3. Log retry attempts
4. Return cached last-known-good state if all retries fail

---

### Missing Error Callback for Telegram Messages

**Issue:** Telegram message sends are fire-and-forget. No confirmation they reached Telegram server.

**Files:**
- `webhook/dispatch.py:128-138` (progress message edits)
- `webhook/bot/routers/callbacks.py:214+` (keyboard edits)

**Impact:** Users see stale/incorrect buttons/text if message edit fails. No logging of Telegram API failures.

**Fix approach:** 
1. Capture result of all `bot.send_message()` and `bot.edit_message_text()` calls
2. Log HTTP status on failure
3. Add fallback: if edit fails, send new message with same content
4. Consider using aiogram's built-in error handler middleware

---

### Silently Dropped Errors in Approval Processing

**Issue:** Multiple try/except blocks in `process_approval_async()` silence errors, continuing with incomplete data.

**File:** `webhook/dispatch.py:123-125, 133-134`

**Evidence:**
```python
try:
    await bot.edit_message_text(...)
except Exception:
    pass  # Continue to next
```

**Impact:** If editing progress message fails, subsequent edits are skipped silently. User sees 0/N completed, then suddenly ✔️ without intermediate updates.

**Fix approach:** 
1. Log each exception at WARNING level
2. Add circuit breaker: if 3 consecutive edits fail, fall back to single final message
3. Track success/failure metrics

---

## Maintainability

### Inconsistent Error Handling Across Modules

**Issue:** Different patterns for handling the same error types:
- Some use `logger.error(...); raise`
- Some use `logger.warning(...); return None`
- Some use bare `except Exception: pass`

**Files:** `webhook/dispatch.py`, `webhook/routes/mini_api.py`, `webhook/bot/routers/callbacks.py`

**Impact:** Hard to predict behavior. Some failures log, others don't. Inconsistent observability.

**Fix approach:** Define error policy module `webhook/error_policy.py`:
```python
POLICY = {
    "github_api": {"retry": 3, "log_level": "warning", "fallback": "cached"},
    "telegram": {"retry": 0, "log_level": "error", "fallback": "new_message"},
    "sheets": {"retry": 3, "log_level": "warning", "fallback": "cached"},
}
```

Apply consistently via decorators.

---

### No Observability for Mini App Frontend Errors

**Issue:** Webhook mini-app frontend catches errors but only shows user toast. No error reporting to backend.

**File:** `webhook/mini-app/src/lib/api.ts:1-13`

```typescript
if (!response.ok) {
  throw new Error(`API ${response.status}: ${response.statusText}`);
}
```

**Impact:** Silent failures in prod. If API returns 500, only user sees error, no server logs it. No error metrics.

**Fix approach:** 
1. Add error reporting endpoint: `POST /api/mini/logs`
2. Send: {timestamp, pathname, error, stack, status}
3. Store in centralized logging service (consider Sentry)
4. Add frontend performance metrics (load time, first paint, API latency)

---

### Duplicate Logic: _escape_md() in Multiple Files

**Issue:** `_escape_md()` defined in both `webhook/query_handlers.py` and `execution/curation/telegram_poster.py`.

**Files:**
- `webhook/query_handlers.py:14-20`
- `execution/curation/telegram_poster.py` (imported, not duplicated)

**Impact:** If escape rules change, must update in multiple places. Risk of inconsistency.

**Fix approach:** Move to shared module `execution/core/markdown.py`, export as public utility.

---

### No Test Coverage for Critical Paths

**Issue:** Large files like `callbacks.py` (601 lines) have minimal test coverage visible in git.

**Files:** `tests/test_query_handlers.py` (258 lines) tests query handlers but not callbacks.py directly.

**Impact:** Changes to callback logic risk regressions. Especially risky for FSM state transitions and error handling.

**Fix approach:** Add callback router tests:
```python
tests/test_callback_draft_actions.py  # 100+ lines
tests/test_callback_reports.py        # 80+ lines
tests/test_callback_queues.py         # 60+ lines
```

Target 80%+ coverage on callbacks.py and messages.py.

---

## Scaling Limits

### Redis Key Expiration Not Enforced Uniformly

**Issue:** Only `execution/curation/redis_client.py` sets TTLs via `expire()`. Other keyspaces accumulate.

**Files:** `execution/curation/redis_client.py:56`, `webhook/redis_queries.py` (no TTL calls)

**Impact:** With 10k+ curated items/day, Redis grows unbounded. At 1MB/day, fills 100GB in 274 years. But with heavy usage (100 items/hour), fills in weeks.

**Fix approach:** 
1. Enforce TTLs at data entry: `client.setex(key, 30*86400, value)` on all new keys
2. Add Redis memory monitor: alert if >80% capacity
3. Implement eviction policy: `redis.conf` maxmemory-policy=allkeys-lru
4. Set Redis max memory in Railway deployment

---

### Supabase Queries Not Paginated on Large Reports

**Issue:** `reports_show_month_list()` fetches all reports for month without pagination.

**File:** `webhook/reports_nav.py:145-160`

```python
reports = sb.table("platts_reports").select(...).eq("report_type", ...).execute()
```

**Impact:** If month has 10k reports, fetches all at once. Slow response, memory spike.

**Fix approach:** 
1. Add pagination: `offset(page * 50).limit(50)`
2. Or: use `count` to show "123 reports" and lazy-load on scroll
3. Add index on (report_type, date_key) in Postgres

---

## Observability

### No Central Error Tracking (Sentry/Similar)

**Issue:** Errors logged to stdout only. No aggregation, alerting, or error trending.

**Files:** All Python modules use `logging` but logs go to console only.

**Impact:** In production, errors are lost in pod logs. Can't detect patterns (e.g., "X fails 10x/day"). No PagerDuty/Slack alerts.

**Fix approach:** Integrate Sentry:
1. `pip install sentry-sdk`
2. Initialize in `webhook/app.py` and `execution/scripts/*.py`
3. Set tags: workflow name, chat_id, draft_id
4. Configure alerts for error count spike
5. Link to GitHub issues via breadcrumbs

---

### Missing Metrics on WhatsApp Delivery

**Issue:** `process_approval_async()` logs success/failure text but no metrics (Prometheus counters).

**File:** `webhook/dispatch.py:127`

```python
logger.info(f"Approval complete: {report.success_count} sent, {report.failure_count} failed")
```

**Impact:** No dashboard visibility. Can't see delivery rate trends or correlate with Uazapi outages.

**Fix approach:** 
1. Add `prometheus_client` counters:
   - `whatsapp_messages_sent_total{status=success/failure}`
   - `whatsapp_delivery_time_seconds`
2. Expose `/metrics` endpoint
3. Scrape into monitoring (Railway supports Prometheus)

---

### Insufficient Logging in Mini App Backend Routes

**Issue:** `webhook/routes/mini_api.py` has only 8 logger calls across 552 lines.

**Impact:** Request flow unclear. Slow queries hard to diagnose.

**Fix approach:** Add structured logging at entry/exit:
```python
@routes.get("/api/mini/news")
async def get_news(request: web.Request) -> web.Response:
    logger.info("GET /api/mini/news", extra={"page": page, "limit": limit})
    try:
        ...
        logger.info("GET /api/mini/news SUCCESS", extra={"count": len(items)})
    except Exception as e:
        logger.error("GET /api/mini/news FAILED", exc_info=e)
        raise
```

---

## Dependencies at Risk

### Python Version Mismatch Between Modules

**Issue:** `webhook/pyproject.toml` requires Python >=3.9, but some scripts may assume >=3.10 (use of type hints like `dict[str, Any]`).

**Files:** `webhook/pyproject.toml:6` vs `execution/scripts/*.py` (type hints usage)

**Impact:** Code may fail on Python 3.9 deployments. Unclear minimum version.

**Fix approach:** 
1. Specify `python_requires = ">=3.10"` in pyproject.toml
2. Use `from __future__ import annotations` at top of all files with modern type hints
3. Test CI matrix: Python 3.10, 3.11, 3.12

---

### Outdated spgci Version

**Issue:** `requirements.txt` pins `spgci>=0.0.70` (old version, no upper bound).

**Impact:** Unpredictable behavior if maintainer releases breaking changes. No security constraints.

**Fix approach:** 
1. Check latest version: `pip index versions spgci`
2. Pin range: `spgci>=0.0.70,<1.0.0`
3. Add version check in CI: fail if any dependency is >1y old

---

### anthropic Package Version Constraint Loose

**Issue:** `requirements.txt` specifies `anthropic>=0.40.0` with no upper bound.

**Impact:** If anthropic releases 1.0 with breaking API changes, production breaks silently.

**Fix approach:** Pin to tested version range: `anthropic>=0.40.0,<1.0.0`.

---

## Fragile Areas Requiring Careful Modification

### FSM State Handlers (Airframe-Level Fragility)

**Area:** Entire FSM router system in `webhook/bot/routers/messages.py`

**Why Fragile:** 
1. Callback routing must respect FSM state boundaries
2. Filter order matters (specific states BEFORE generic F.text)
3. Recent history of catch-all intercepting FSM states (3 commits)

**Safe Modification:**
1. Add router registration order test:
   ```python
   def test_fsm_isolation():
       # Simulate StateFilter(AdjustDraft.waiting_feedback, F.text)
       # Verify F.text handler doesn't trigger
   ```
2. Use type hints for state: `@message_router.message(AdjustDraft.waiting_feedback, F.text)` (currently done)
3. Never add bare `F.text` handler without StateFilter

**Test Coverage:** Currently minimal. Need dedicated test file.

---

### Redis Transaction Pipeline (Data Consistency)

**Area:** `execution/curation/redis_client.py:115-125` (SET + DELETE in pipeline)

**Why Fragile:** 
1. Two-step operation: if pipeline fails mid-operation, state is inconsistent
2. No rollback mechanism
3. Memory leak if delete fails

**Safe Modification:**
1. Use Lua script for atomic operation:
   ```python
   script = """
   redis.call('SET', KEYS[1], ARGV[1])
   redis.call('LPUSH', KEYS[2], ARGV[2])
   return 1
   """
   client.eval(script, 2, key1, key2, val1, val2)
   ```
2. Add transaction-level error logging
3. Test rollback scenarios

---

### Supabase Storage Download URL Signing

**Area:** `webhook/reports_nav.py:_get_signed_url()` (assumed to exist in codebase)

**Why Fragile:** URL expiry timing is critical. URL generation must match Supabase's signing algorithm.

**Safe Modification:**
1. Test URL expiry with actual file: fetch immediately, then after 1h
2. Log signed URL generation errors with request details (not URL)
3. Add unit test with mock Supabase client
4. Verify token format matches Supabase docs quarterly

---

## Missing Critical Features / Limitations

### No Notification System for Failed Workflows

**Issue:** Workflows fail silently unless user manually checks `/status` command. No push notification or email alert.

**Impact:** Issues go unnoticed for hours. Baltic report missing, market data stale.

**Fix approach:** 
1. Add alert rule in state_store: if failure streak >= 2, send alert
2. Configure Telegram or Slack notifications
3. Track last-alert-sent to avoid spam

---

### No Audit Log for Approvals/Rejections

**Issue:** Draft approvals and rejections happen in Telegram, no permanent record of who approved what.

**Files:** `webhook/bot/routers/callbacks.py` (approval flow)

**Impact:** Can't trace decision history. Compliance risk.

**Fix approach:** Add audit table in Postgres:
```sql
CREATE TABLE draft_audits (
  id SERIAL PRIMARY KEY,
  draft_id TEXT,
  user_id INT,
  action TEXT,
  timestamp TIMESTAMPTZ DEFAULT NOW()
);
```

Insert on approve/reject/adjust.

---

### No Rate Limiting Per User

**Issue:** User can spam approval requests, broadcast requests without throttle.

**Impact:** Telegram bot API rate limits (30 msg/sec per bot) could be exceeded, causing 429 responses.

**Fix approach:** 
1. Track user action counts in Redis: `user:123:approvals:minute`
2. Reject if count > 10 per minute with backoff message
3. Log rate limit violations

---

## Test Coverage Gaps

### Callback Router Not Tested (601-line File)

**What's Not Tested:** 
- Draft adjust/reject/approve flow
- Report navigation (type → year → month → list)
- Queue pagination
- Contact toggle
- Workflow triggers
- All error handlers (except Exception: pass clauses)

**Files:** `webhook/bot/routers/callbacks.py`, no corresponding test file.

**Risk:** High. Frequent changes to callback logic, no test protection.

**Priority:** High. Add `tests/test_callback_router.py` with:
- 10 tests for draft actions
- 8 tests for report navigation
- 5 tests for queue pagination
- 4 tests for contact ops
- 6 tests for error handling

Target: 80% coverage.

---

### Mini API Routes Lightly Tested

**What's Not Tested:** 
- News endpoint pagination
- Workflows endpoint health calc
- Contacts endpoint search
- Error paths (GitHub API down, Supabase timeout)
- Auth validation (init data signature)

**Files:** `webhook/routes/mini_api.py` (552 lines), minimal corresponding tests.

**Risk:** Medium. Frontend depends on API contracts. Breaking changes undetected.

**Priority:** Medium. Add `tests/test_mini_api_routes.py` with mocked GitHub/Supabase.

---

### Dispatch Module Missing Integration Tests

**What's Not Tested:**
- Full WhatsApp send flow with progress updates
- Google Sheets contact fetch with retries
- Error handling (Sheets timeout, Uazapi 500)
- Progress message edit failures
- Concurrent sends (thread pool behavior)

**Files:** `webhook/dispatch.py` (211 lines), no corresponding integration test.

**Risk:** Medium. Critical path for broadcast approvals.

**Priority:** Medium. Add `tests/test_dispatch_integration.py` with mock aiohttp + Google Sheets.

---

*Concerns audit: 2026-04-17*
