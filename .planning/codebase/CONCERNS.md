# Codebase Concerns

**Analysis Date:** 2026-02-13

## Tech Debt

**Hardcoded Credentials in Source Code:**
- Issue: Plaintext password stored directly in Python script
- Files: `execution/scripts/rationale_ingestion.py` (line 73)
- Impact: Security vulnerability; credentials exposed in repository history
- Details:
  ```python
  "password": os.getenv("PLATTS_PASSWORD", "141204*MtM"), # Should use Env Var ideally
  ```
- Fix approach: Move all defaults to .env files, remove hardcoded values, implement secret rotation for exposed credentials

**Excessive Debug Logging in Production Code:**
- Issue: Debug print statements left scattered throughout source code
- Files: `execution/integrations/baltic_client.py` (17 print statements), `execution/integrations/sheets_client.py` (8 prints), `execution/integrations/uazapi_client.py` (2 prints), `execution/scripts/morning_check.py`
- Impact: Debug output pollutes logs, may leak sensitive information (URLs, IDs, partial tokens)
- Count: 49+ print statements across execution layer
- Fix approach: Replace all `print()` with `logger` calls from `WorkflowLogger` module; standardize logging levels (DEBUG, INFO, WARNING, ERROR)

**Overly Large Single Files:**
- Issue: `webhook/app.py` is 1044 lines - monolithic Flask application with mixed concerns
- Files: `webhook/app.py`
- Impact: Difficult to test, maintain, and reason about; tight coupling between routing, AI logic, external APIs
- Responsibilities mixed:
  - Telegram webhook handling (routing)
  - AI agent prompts (4 large prompts embedded)
  - Sheets API client calls
  - WhatsApp integration
  - Draft state management
  - Anthropic API calls
- Fix approach: Break into modules: `handlers/`, `agents/`, `integrations/`, `state_manager.py`; extract prompts to separate files

**Large Dashboard Components:**
- Issue: `dashboard/app/page.tsx` (294 lines) and `dashboard/app/workflows/page.tsx` (314 lines) contain business logic mixed with UI
- Files: `dashboard/app/page.tsx`, `dashboard/app/workflows/page.tsx`
- Impact: Component reusability reduced, state management distributed, testing difficult
- Details: Health calculations, workflow catalog definitions, API calls inlined in components
- Fix approach: Extract to custom hooks (`useWorkflowHealth`, `useWorkflowRuns`), separate data from presentation

**Unconfirmed Database Table Names:**
- Issue: Table name assumption without schema verification
- Files: `execution/integrations/supabase_client.py` (line 23)
- Impact: Runtime errors if table name doesn't exist; no migration tracking
- Fix approach: Add schema validation on client initialization; document actual table schema; add database migration files

**Global In-Memory State in Webhook:**
- Issue: Flask global dictionaries used for state management
- Files: `webhook/app.py` (lines 34-35)
- Details:
  ```python
  DRAFTS = {}         # draft_id → {message, status, original_text, uazapi_token, uazapi_url}
  ADJUST_STATE = {}   # chat_id → {draft_id, awaiting_feedback: True}
  ```
- Impact: Lost on process restart; no persistence; race conditions in multi-instance deployments
- Fix approach: Use database (Supabase or Redis) for state persistence; implement proper concurrency control

---

## Known Bugs

**Shell Injection Vulnerability in News Route:**
- Symptoms: API route executes Python script with user-controlled input
- Files: `dashboard/app/api/news/route.ts` (line 79)
- Details: Calling `exec()` to run `send_news.py` with draft message as argument
- Trigger: Approve draft with special characters (quotes, backticks, semicolons)
- Workaround: Currently uses file-based temp file approach (safer than inline args), but exec is still inherently risky
- Fix approach: Call Python subprocess directly via Node.js child_process module; use proper serialization/deserialization instead of shell

**Inconsistent Error Handling:**
- Symptoms: Some API endpoints return 500 with generic messages; others expose stack traces
- Files: `dashboard/app/api/logs/route.ts`, `dashboard/app/api/contacts/route.ts`
- Impact: Debugging harder; user feedback unclear
- Fix approach: Standardize error response shape; use error codes for programmatic handling; never expose stack traces to client

**Missing News Draft Validation:**
- Symptoms: Draft text sent directly without sanitization
- Files: `dashboard/app/api/news/route.ts`
- Impact: Could send malformed WhatsApp markup if draft editing doesn't validate
- Fix approach: Add text validation schema; enforce WhatsApp message format rules

---

## Security Considerations

**Unencrypted Environment Secrets:**
- Risk: GITHUB_TOKEN, UAZAPI_TOKEN, ANTHROPIC_API_KEY exposed via environment variables
- Files: `dashboard/app/api/workflows/route.ts` (requires GITHUB_TOKEN), `webhook/app.py` (requires UAZAPI_TOKEN, ANTHROPIC_API_KEY)
- Current mitigation: .env file (not committed), but no rotation policy
- Recommendations:
  - Implement secret rotation schedule (quarterly minimum)
  - Use secret management service (GitHub Secrets Environments, Railway Vault)
  - Add secret scanning in CI/CD pipeline
  - Audit logs for which processes accessed which secrets

**Broadened GitHub API Permissions:**
- Risk: GITHUB_TOKEN has full repo access (workflow dispatch)
- Files: `dashboard/app/api/workflows/route.ts`
- Impact: Compromised token could modify workflows, delete runs, access repo contents
- Recommendations: Create fine-grained personal access token with minimal scope (workflows only)

**Missing Input Validation:**
- Risk: News route accepts arbitrary text without sanitization
- Files: `dashboard/app/api/news/route.ts`
- Impact: Could inject WhatsApp/Telegram format exploits
- Fix: Add zod schema validation on all POST/PUT requests

**Azure Graph API Token Generation:**
- Risk: Client secrets stored in environment
- Files: `execution/integrations/baltic_client.py`
- Impact: Exposed secret could be used to access all mail in organization
- Recommendations: Use managed identity (if on Azure) or rotate client secret monthly; audit mailbox access logs

**No Rate Limiting:**
- Risk: API routes have no rate limiting or authentication
- Files: `dashboard/app/api/workflows/route.ts`, `dashboard/app/api/news/route.ts`, `dashboard/app/api/logs/route.ts`, `dashboard/app/api/contacts/route.ts`
- Impact: DOS vulnerability; anyone with URL can trigger workflows or access logs
- Fix: Add authentication middleware (OAuth, JWT, or API key); implement rate limiting per IP/user

---

## Performance Bottlenecks

**Synchronous PDF Download in Email Processing:**
- Problem: Baltic client downloads PDF files without timeout or size limits
- Files: `execution/integrations/baltic_client.py` (lines 84-172)
- Cause: Single-threaded requests.get() blocks entire workflow
- Impact: If PDF link is slow/unavailable, workflow hangs; no max-size check could cause memory exhaustion
- Improvement path:
  - Add request timeout (current: infinite)
  - Add max content-length check before download
  - Consider async requests (httpx) or background job queue

**N+1 Query in Platts Data Retrieval:**
- Problem: Fetching data for each product key individually
- Files: `execution/scripts/morning_check.py` (lines 201-230)
- Cause: Loop through symbols and fetch one-by-one from Platts
- Impact: Unnecessary API calls; slow report generation
- Improvement path: Batch requests if Platts API supports; cache results with TTL

**Unoptimized GitHub API Polling:**
- Problem: Dashboard fetches full workflow runs every 10 seconds
- Files: `dashboard/app/page.tsx` (line 27)
- Impact: 8640 GitHub API calls per day; potential rate limit hits
- Improvement path:
  - Increase polling interval (30+ seconds)
  - Cache results server-side
  - Use webhook instead of polling

**Large JSON Payloads:**
- Problem: No pagination or field selection in API responses
- Files: `dashboard/app/api/workflows/route.ts` (line 18: per_page: 100)
- Impact: Transfers unnecessary data (commit messages, author info not displayed)
- Fix: Use GraphQL to request only needed fields; implement pagination

---

## Fragile Areas

**AI Prompt Management:**
- Files: `webhook/app.py` (lines 46-200+ hardcoded prompts)
- Why fragile: Prompts define behavior of Writer, Critique, Curator agents; small text changes break output parsing
- Safe modification:
  - Change prompts in isolated environment first
  - Test with same input corpus before deploying
  - Version control prompts separately (separate file)
  - Add regression tests with expected output samples
- Test coverage: Only manual testing via Telegram

**Apify Integration:**
- Files: `execution/scripts/rationale_ingestion.py`, `execution/integrations/apify_client.py`
- Why fragile: Depends on external actor; if Apify changes output format, script breaks silently
- Safe modification:
  - Add data validation after Apify response
  - Log actual response structure
  - Add test with fixture data
- Test coverage: No unit tests

**State Persistence (Local JSON Files):**
- Files: `execution/core/state.py`, `dashboard/app/api/news/route.ts`
- Why fragile: JSON files on filesystem not atomic; concurrent writes corrupt data
- Safe modification:
  - Add file locking or database
  - Validate JSON on read (handle corrupt files)
  - Add backup/versioning
- Risk scenario: Two processes write state simultaneously → corrupted state file

**Azure Mailbox Access:**
- Files: `execution/integrations/baltic_client.py`
- Why fragile: Depends on Azure Graph API behavior; email parsing uses regex and heuristics
- Safe modification:
  - Add detailed error logging for each step
  - Test with sample emails before deploying
  - Document expected email format
- Test coverage: No unit tests; manual testing only

---

## Scaling Limits

**Single-Instance Webhook Deployment:**
- Current capacity: 1 Railway container (default)
- Limit: ~100 concurrent requests before timeout (gunicorn workers = 4 by default)
- Blocking operations: Telegram API (3-5 seconds), Anthropic API (5-10 seconds), WhatsApp send (2-3 seconds)
- Scaling path:
  - Add task queue (Celery/RabbitMQ) for async operations
  - Scale Railway container replicas to 3+
  - Move long-running AI tasks to background workers

**No Database for Distributed State:**
- Current capacity: In-memory only; lost on restart
- Limit: Works for <10 concurrent draft sessions
- Scaling path: Migrate DRAFTS/ADJUST_STATE to Supabase; adds latency but enables multi-instance

**Supabase Limits on News Drafts:**
- Current approach: JSON files on filesystem
- Limit: Scales to 1000+ files before filesystem becomes slow
- Scaling path: Move to Supabase RLS with user-scoped access; add pagination UI

**GitHub API Rate Limiting:**
- Current: 100 requests per dashboard refresh
- Limit: 60 requests/hour (unauthenticated) or 5000/hour (authenticated)
- Current token is authenticated, so capacity: ~1 refresh every 1 minute indefinitely
- Scaling path: Cache on server-side; use GraphQL to fetch only deltas; implement exponential backoff

---

## Dependencies at Risk

**python-dotenv (unmaintained):**
- Risk: Last update 2022; potential security vulnerabilities
- Impact: Could not load environment variables properly in edge cases
- Migration plan: Use `python-decouple` (actively maintained) or built-in `os.environ`

**spgci (0.0.70):**
- Risk: Pre-release version; API could change
- Impact: Platts data retrieval breaks
- Migration plan: Check if newer stable version exists; pin to tested version

**lseg-data (1.0.0):**
- Risk: Low version number; few downloads suggest low adoption
- Impact: API may be unstable
- Migration plan: Evaluate alternatives; add fallback data source

**msal (31.0+):**
- Risk: Azure authentication; if tokens break, email sync stops
- Current mitigation: Error handling in place
- Monitoring needed: Token expiration; Azure API changes

---

## Missing Critical Features

**No Workflow Failure Recovery:**
- Problem: If morning_check.py fails midway, no automatic retry
- Files: `.github/workflows/` (not visible in repo) coordinates this
- Blocks: Need manual intervention or cron retry
- Fix: Implement exponential backoff in retry.py; add circuit breaker for failing APIs

**No Draft Versioning:**
- Problem: Editing draft overwrites original; can't see what changed
- Files: `dashboard/app/api/news/route.ts`, `data/news_drafts.json`
- Impact: Can't audit approvals; accidental overwrite loses content
- Fix: Store edit history with timestamps; show diff view

**No Webhook Signature Validation:**
- Problem: Flask routes accept any POST request
- Files: `webhook/app.py`
- Impact: Anyone can send fake approval requests if URL is known
- Fix: Add Telegram signature validation (verify via token hash)

**No Audit Logging:**
- Problem: No record of who approved/rejected drafts or triggered workflows
- Impact: Cannot trace data back to person for compliance
- Fix: Log all mutations with timestamp, user context, IP address

---

## Test Coverage Gaps

**No Unit Tests for Core Integration Clients:**
- What's not tested: All API clients (Platts, LSEG, Supabase, Sheets, Telegram, Baltic, Apify, Claude)
- Files: `execution/integrations/`
- Risk: Refactoring breaks APIs silently
- Priority: **HIGH** - These are critical paths; integration test suite needed

**No E2E Tests for Workflows:**
- What's not tested: Full workflow execution (morning_check → format → send)
- Files: `execution/scripts/`, `execution/agents/`
- Risk: Changes to pricing format or filtering logic break production reports
- Priority: **HIGH** - This is main business logic

**No API Contract Tests:**
- What's not tested: Response formats from dashboard endpoints
- Files: `dashboard/app/api/`
- Risk: Frontend breaks on API schema changes
- Priority: **MEDIUM** - UI can break but fallback to error messages

**No Prompt/Agent Tests:**
- What's not tested: Writer, Critique, Curator agent outputs
- Files: `webhook/app.py` (prompts), `execution/agents/rationale_agent.py`
- Risk: Prompt tuning breaks silently; hard to regression test
- Priority: **MEDIUM** - Affects news quality but not system stability

**Only 1 Test File (Format Only):**
- Files: `tests/test_format.py` - only tests WhatsApp message formatting
- Coverage: <1% of codebase
- Fix approach: Add:
  - `tests/test_integrations/` for mocked API clients
  - `tests/test_workflows/` for end-to-end scenarios
  - `tests/test_agents/` for prompt outputs with fixtures
  - `tests/test_api.py` for dashboard endpoints

---

*Concerns audit: 2026-02-13*
