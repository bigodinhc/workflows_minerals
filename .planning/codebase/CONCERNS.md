# Codebase Concerns

**Analysis Date:** 2026-04-22

Scope: multi-subsystem platform — Python execution layer (`execution/`), Node.js Apify actors (`actors/`), Next.js dashboard (`dashboard/`), aiogram webhook/bot (`webhook/`), Supabase backend (`supabase/`). Severity key: **[CRITICAL]** / **[HIGH]** / **[MEDIUM]** / **[LOW]**.

Refresh notes (vs. previous map): Redis is now load-bearing across three distinct keyspaces (curation, state_store, bot queue-selection). Split-lock idempotency rework landed for `baltic_ingestion` + `morning_check` (commits `43aa332`, `60ae15e`, `35629d4`, `022dad4`). `/queue` bulk-actions shipped (commits `e9901c3` → `38f59f3`). Phase 4 trace_id propagation to Apify landed, but actor deploys are manual. Sheets→Supabase migration for contacts completed (commit `cdf354d`).

---

## 1. Security Concerns

### [CRITICAL] `IRONMARKET_API_KEY` hardcoded in `baltic_ingestion.py`
- **What:** `execution/scripts/baltic_ingestion.py:41` has `IRONMARKET_API_KEY = "ironmkt_WUbuYLe4m06GTiYos_fVwvBfNa2l8GWoJtE9K8MJFCY"` with an inline comment `# Keeping hardcoded as requested, or load from env`. This secret is in git history and will be in every snapshot forever.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/scripts/baltic_ingestion.py:41`
- **Why it matters:** Anyone who pulls the repo (including future contractors, leaked backups, public forks) has permanent write access to the IronMarket price ingestion endpoint. Referenced as out-of-scope in `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md:68`, meaning the team knows but deferred. With this map update, it is now the single worst security issue in the repo.
- **Effort:** small — move to `os.environ["IRONMARKET_API_KEY"]`, rotate the key on IronMarket side, purge from git history via `git filter-repo`.
- **Priority:** **[CRITICAL]**

### [HIGH] `SUPABASE_SERVICE_ROLE_KEY` used in Next.js API routes — safe today, fragile
- **What:** `dashboard/app/api/contacts/route.ts:9` reads `process.env.SUPABASE_SERVICE_ROLE_KEY` inside a Next.js route handler. In the App Router this code runs server-side only, so the key is not shipped to the browser. But the `NEXT_PUBLIC_` vs non-prefixed distinction is easy to fumble — if anyone ever inlines the key into a Client Component, it leaks wholesale. Verified: `grep NEXT_PUBLIC_SUPABASE dashboard/app` returns zero matches today; all client pages (`"use client"` in `page.tsx` etc.) go through `/api/*` route handlers, never direct Supabase.
- **Files:** `/Users/bigode/Dev/agentics_workflows/dashboard/app/api/contacts/route.ts:8-20`; same pattern in `dashboard/app/api/{news,workflows,delivery-report,logs}/route.ts`.
- **Why it matters:** Service-role key bypasses RLS. Leak = full DB read/write.
- **Effort:** medium — add top-of-file comment asserting server-only; longer term, issue an anon-key + RLS SELECT policy for dashboard read paths and reserve service-role for writes.
- **Priority:** **[HIGH]**

### [HIGH] Telegram webhook has no secret-token verification
- **What:** `webhook/bot/main.py` calls `bot.set_webhook(webhook_url)` without the optional `secret_token` parameter. Aiogram's `SimpleRequestHandler` therefore accepts any POST to `/webhook`. `grep -rn 'webhook_secret\|TELEGRAM_WEBHOOK_SECRET\|secret_token' webhook --include='*.py'` returns zero matches.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/bot/main.py`, `/Users/bigode/Dev/agentics_workflows/webhook/bot/config.py:23` (`WEBHOOK_PATH = "/webhook"`)
- **Why it matters:** Anyone who discovers the Railway URL can replay forged Telegram updates. Mitigated partially by `RoleMiddleware` (webhook/bot/middlewares/auth.py:25) looking up `from_user.id` in Supabase `contacts` table — a forged update with a known admin `chat_id` would still pass middleware, because Telegram's `from_user.id` is attacker-controlled when the webhook has no secret verification.
- **Effort:** small — generate secret, pass it to `bot.set_webhook(secret_token=…)` and `SimpleRequestHandler(secret_token=…)`.
- **Priority:** **[HIGH]**

### [HIGH] `REDIS_URL` handling — correct today, single point of failure
- **What:** `REDIS_URL` is read in 5+ modules (`webhook/dispatch.py:44`, `webhook/redis_queries.py:35`, `webhook/bot/config.py:19`, `execution/curation/redis_client.py:43`, `execution/core/state_store.py:26`). Each module lazy-caches its own client. `.env` is gitignored (confirmed: `.gitignore:2`) and `.env.example` does not list `REDIS_URL` — so new devs must discover the requirement from grep. `.env` file exists on disk (3556 bytes); not tracked.
- **Files:** all 5 above + `.env.example` (missing `REDIS_URL=` line).
- **Why it matters:** 5 cached clients = 5 stale connections if Redis is restarted and a client gets a broken socket. Each client sets `socket_connect_timeout=3, socket_timeout=3` so failures surface quickly, but there is no shared retry/reconnect layer. `.env.example` omission means a fresh clone + `uv run pytest` will work (fakeredis) but a fresh clone + production invocation will fail cryptically on `RuntimeError: REDIS_URL env var not set`.
- **Effort:** small — (a) add `REDIS_URL=redis://localhost:6379/0  # required; use Railway managed Redis in prod` to `.env.example`. (b) medium effort: centralize the lazy-client in a single `execution/core/redis_pool.py` to avoid the 5-copy drift risk.
- **Priority:** **[HIGH]**

### [HIGH] No per-user authorization inside `/queue` bulk-op handlers beyond router-level middleware
- **What:** `webhook/bot/routers/callbacks_queue.py:32` gates the router on `RoleMiddleware(allowed_roles={"admin"})` — so only admins reach the handlers. That is fine. BUT: `queue_selection` state is keyed purely by `chat_id` with no ownership check. If two admins share the bot (today there is one, per `TELEGRAM_CHAT_ID` env), they would trample each other's selection. More critically: a malicious actor who compromises one admin's account can issue bulk `discard` over *all* staging items in one callback — there is no confirmation that the selected items belong to a specific user/scope.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/bot/routers/callbacks_queue.py:224-270`, `/Users/bigode/Dev/agentics_workflows/webhook/queue_selection.py:22-31` (keys are `bot:queue_{mode,selected,page}:{chat_id}`).
- **Why it matters:** The selection model is fundamentally per-chat, not per-user-role. Two admins in a group chat would see each other's checkboxes. Since today there is one admin, this is theoretical — but the design locks in a pattern that will bite once the bot onboards additional admins.
- **Effort:** medium — key by `(chat_id, from_user.id)`, require `from_user.id` on every handler, enforce parity between prompt and confirm (user who confirmed must be user who prompted).
- **Priority:** **[HIGH]** (design debt; no production impact today with single admin)

### [MEDIUM] `/metrics` endpoint is unauthenticated
- **What:** `webhook/routes/api.py` exposes a Prometheus endpoint with an explicit justification comment. Counters today are aggregate — but `edit_failures` labels and any future histogram with per-user labels would leak.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/routes/api.py`, `/Users/bigode/Dev/agentics_workflows/webhook/metrics.py`
- **Effort:** small — basic-auth via `METRICS_USER/METRICS_PASS` env pair.
- **Priority:** **[MEDIUM]**

### [MEDIUM] `/health` leaks short prefixes of sensitive tokens
- **What:** `webhook/routes/api.py:33+` returns `anthropic_key_prefix` and similar. Prefixes are not secrets in isolation but both Anthropic and UazAPI use deterministic key formats; first 10 chars shrink brute-force space.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/routes/api.py`
- **Effort:** small — replace with `bool(TOKEN)`.
- **Priority:** **[MEDIUM]**

### [MEDIUM] No rate limiting on HTTP routes
- **What:** `/store-draft`, `/seen-articles`, `/test-ai`, `/admin/register-commands`, and all `/api/mini/*` routes have no rate limit middleware. `/test-ai` triggers a paid Anthropic API call per hit.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/routes/api.py`, `/Users/bigode/Dev/agentics_workflows/webhook/routes/mini_api.py` (545 lines)
- **Effort:** small.
- **Priority:** **[MEDIUM]**

### [LOW] SQL-injection surface is effectively nil
- **What:** All DB access goes through supabase-py / supabase-js query builders. `grep` finds zero raw SQL strings outside `supabase/migrations/`.
- **Priority:** **[LOW]** (watch item for future `.rpc()` usage)

### [LOW] XSS surface in dashboard is effectively nil
- **What:** `grep -rn 'dangerouslySetInnerHTML\|innerHTML' dashboard --include='*.ts' --include='*.tsx'` returns zero hits outside `node_modules`. All rendering uses JSX default escaping.
- **Priority:** **[LOW]**

### [LOW] `.gitignore` patterns are un-anchored; future credential names could slip through
- **What:** `.gitignore:3-4` lists `credentials.json` / `token.json` unanchored. A file named `service-account.json` placed by a new dev would not match.
- **Files:** `/Users/bigode/Dev/agentics_workflows/.gitignore`
- **Effort:** small — add `*credentials*.json`, `*-sa.json`, `service-account*.json`.
- **Priority:** **[LOW]**

---

## 2. Technical Debt

### [HIGH] Two `requirements.txt` files must be manually kept in sync
- **What:** Root `/Users/bigode/Dev/agentics_workflows/requirements.txt` (GH Actions crons, 30 lines) and `/Users/bigode/Dev/agentics_workflows/webhook/requirements.txt` (Railway bot Docker build, 12 lines) are separate. Current drift (via `diff`): root has `apify-client`, `pytest`, `pytest-mock`, `fakeredis`, `pytest-asyncio`, `pandas`, `lseg-data`, `spgci`, `structlog`, `msal`, `python-dotenv` that webhook lacks; webhook adds `aiohttp-jinja2`. Version pinning agrees where it overlaps (`aiogram>=3.4.0,<4.0`, `redis>=5.0,<6.0`, `supabase>=2.0.0,<3.0`).
- **Files:** `/Users/bigode/Dev/agentics_workflows/requirements.txt`, `/Users/bigode/Dev/agentics_workflows/webhook/requirements.txt`
- **Why it matters:** Bot Docker image is smaller (no pandas/lseg/spgci) but phases that added new deps (phonenumbers, sentry) had to be updated in both. User's MEMORY.md flags this as a recurring trap. Import errors surface only on Railway rebuild.
- **Effort:** medium — migrate to `pyproject.toml` with `[project.optional-dependencies] webhook = [...], execution = [...]`, or adopt uv workspace.
- **Priority:** **[HIGH]**

### [HIGH] Hardcoded IronMarket URL + orphan `ingest_to_ironmarket` dry-run bypass
- **What:** `execution/scripts/baltic_ingestion.py:40` has `IRONMARKET_URL = "https://merry-adaptation-production.up.railway.app/ingest/price"` hardcoded. Additionally, Phase 4 followups `docs/superpowers/followups/2026-04-21-observability-phase4-followups.md:PE1` flag that `baltic_ingestion.py` dry-run does not short-circuit before the IronMarket HTTP POST — the `if args.dry_run: print(...)` block falls through to a live HTTP call.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/scripts/baltic_ingestion.py:40`
- **Why it matters:** (a) URL change requires code edit, not env. (b) `--dry-run` is a footgun because it still posts to prod IronMarket.
- **Effort:** small — move URL to env, add early `return` after dry-run print.
- **Priority:** **[HIGH]**

### [MEDIUM] Orphan module: `execution/curation/rationale_dispatcher.py`
- **What:** 119-line module documented as orphan via top-of-file TODO comment: `"TODO (v1.1+): Este módulo ficou ÓRFÃO após Bot Navigation v1.1…"`. Not imported by anything in `execution/` or `webhook/` (verified `grep -rn rationale_dispatcher --include='*.py'` returns only self-reference).
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/curation/rationale_dispatcher.py:3-9`
- **Effort:** small — delete, reference the removal in CHANGELOG.
- **Priority:** **[MEDIUM]**

### [MEDIUM] Dead script: `execution/scripts/debug_apify.py`
- **What:** 34-line ad-hoc script with hardcoded `DATASET_ID = "U8cZtEYLn5VirmWxQ"`. Not imported, not scheduled, not in any GH Action. Reads `APIFY_API_TOKEN` env and prints first item of a fixed dataset. Last touched Feb 5.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/scripts/debug_apify.py`
- **Effort:** small — move to `scripts/adhoc/` or delete.
- **Priority:** **[MEDIUM]**

### [MEDIUM] Duplicated EventBus JS across two Apify actors
- **What:** Byte-identical copies of `eventBus.js` in `/Users/bigode/Dev/agentics_workflows/actors/platts-scrap-reports/src/lib/eventBus.js` and `/Users/bigode/Dev/agentics_workflows/actors/platts-scrap-full-news/src/lib/eventBus.js`. Apify package isolation (Docker-per-actor) prevents symlink sharing. Deferred item P4.4 in `docs/superpowers/followups/2026-04-22-observability-trace-id-apify-followups.md:70-74`.
- **Files:** both paths above.
- **Effort:** medium — extract to npm package or Apify shared-storage; not worth it for 2 static copies per the followup, so keep as "watch item".
- **Priority:** **[MEDIUM]**

### [MEDIUM] Two Redis client factories in `webhook/`
- **What:** `webhook/redis_queries.py:30` (`_get_client`) and `webhook/dispatch.py:40,52` (`_get_redis_sync`, `_get_redis_async`) each lazy-instantiate their own singleton. `execution/curation/redis_client.py:26` has a third one. `execution/core/state_store.py:19` has a fourth. `webhook/queue_selection.py` reuses `execution.curation.redis_client._get_client` (good). Four modules own their own Redis client lifecycle.
- **Files:** all 4 above.
- **Why it matters:** Connection leaks on hot-reload; no single place to add telemetry hooks (e.g., slow-command logging).
- **Effort:** medium — extract to `execution/core/redis_pool.py`.
- **Priority:** **[MEDIUM]**

### [MEDIUM] Queue-selection TTL only protects against abandoned sessions, not process restart
- **What:** `webhook/queue_selection.py:18` sets `_TTL_SECONDS = 10 * 60`. Keys are `bot:queue_{mode,selected,page}:{chat_id}`. Selection state survives webhook restart within the 10-min TTL, but any orphan state (user entered select mode, webhook restarted, user never confirmed/cancelled) lingers for up to 10 min and gets re-applied when the user issues a new callback. `enter_mode` deletes the selection key but `page` is reset to 1 explicitly. Test coverage in `tests/test_queue_selection.py` does not exercise the "stale state after restart" scenario.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/queue_selection.py:40-51`
- **Why it matters:** Low operational impact (10 min is short), but the semantic "is the user still in select mode?" is determined by Redis key presence, not by a session ID tied to the current process.
- **Effort:** small — document the limitation; add a stale-state test.
- **Priority:** **[MEDIUM]**

### [LOW] In-source TODO / HACK comments (4 non-generated)
- **What:** `grep -rn 'TODO\|FIXME\|HACK\|XXX' --include='*.py' --include='*.ts' --include='*.tsx' --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=.worktrees --exclude-dir=.next` finds:
  - `execution/integrations/supabase_client.py:22` — `# TODO: CONFIRM TABLE NAME WITH USER`
  - `execution/curation/rationale_dispatcher.py:3` — orphan module marker (see above)
  - `execution/agents/rationale_agent.py:88` — `$XXX.XX/dmt` (prompt template placeholder, not a code TODO)
  - `dashboard/app/news/page.tsx:46` — `confirm('...TODOS os contatos?...')` (Portuguese UI text, not a TODO)
- **Effort:** small — resolve the Supabase one (rename + delete comment); rename `rationale_dispatcher.py`.
- **Priority:** **[LOW]**

### [LOW] `.env` layout accumulating undocumented vars
- **What:** `.env.example` (19 lines) is mostly commented-out placeholders. Actual required envs (found via grep): `REDIS_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_CHAT_ID_BALTIC`, `TELEGRAM_WEBHOOK_URL`, `TELEGRAM_EVENTS_CHANNEL_ID`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `UAZAPI_TOKEN`, `UAZAPI_URL`, `ANTHROPIC_API_KEY`, `APIFY_API_TOKEN`, `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`, `PLATTS_USERNAME`, `PLATTS_PASSWORD`, `IRONMARKET_API_KEY`, `DASHBOARD_BASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (for Graph API). Fewer than a third appear in `.env.example`.
- **Files:** `/Users/bigode/Dev/agentics_workflows/.env.example`
- **Effort:** small — fold the real list into `.env.example` with placeholder values.
- **Priority:** **[LOW]**

---

## 3. Operational & Reliability

### [HIGH] Redis downtime has asymmetric impact across modules
- **What:** Modules handle Redis failure differently:
  - `execution/core/state_store.py:19-41` — silent no-op on failure (writes return None, reads return None). Permissive degrade, documented: `"Workflows must never be broken by this module"`.
  - `execution/curation/redis_client.py:26-52` — raises `RuntimeError` on missing `REDIS_URL` and uses 3s connect timeout. Documented intent: `"curation state (staging/archive) is load-bearing: losing a staged item silently would be worse than crashing the ingestion run"`.
  - `webhook/queue_selection.py:40-51` — no try/except; a Redis error propagates as an unhandled exception to the callback handler. `callbacks_queue._current_mode` wraps `is_select_mode` in try/except (lines 35-43), but `on_queue_sel_all` (line 147) does not wrap `is_select_mode`, only `list_staging`. Mismatch between the two fallback philosophies.
  - `webhook/dispatch.py:62-98` — idempotency check wrapped in try/except; Redis failure logs but does NOT block the send (comment: `"Redis down? Don't block sends — but log loudly."`). Permissive.
  - `webhook/redis_queries.py` — raises on unset REDIS_URL, no fallback on connection error. `/queue` command crashes webhook handler.
- **Files:** all 5 above.
- **Why it matters:** During a Redis outage: daily-report crons keep running (state_store silent no-op), but they lose dedup protection — sheets-era would have double-sent. `/queue` command crashes the webhook. Staging ingestion crashes (curation_redis raises). Webhook idempotency degrades to "send always, pray Redis recovers".
- **Effort:** medium — document the contract per-module (already partially done via docstrings); audit webhook handlers for consistent error reporting.
- **Priority:** **[HIGH]**

### [HIGH] Redis observability is minimal
- **What:** `state_store.py` logs warnings on `_get_client` failure and on each `.record_*` exception. `curation/redis_client.py` does NOT log — it raises. `redis_queries.py` does not log. No Prometheus counter for `redis_connection_failures`. No per-command latency histogram.
- **Files:** `execution/core/state_store.py:39`, `execution/curation/redis_client.py`, `webhook/redis_queries.py`.
- **Why it matters:** When Redis is flaky, operators see workflow symptoms (missed reports, bot callback failures) but cannot directly observe which module's Redis call failed.
- **Effort:** medium — add `redis_errors_total{module=…}` counter to `webhook/metrics.py`; wire `state_store`, `curation_redis`, `redis_queries` to increment on exception.
- **Priority:** **[HIGH]**

### [HIGH] In-flight lock auto-expire can be racy under slow broadcasts
- **What:** `_INFLIGHT_LOCK_TTL_SEC = 20 * 60` in `execution/scripts/baltic_ingestion.py:44`. Yesterday's runs took 17–18 min (documented in `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md:344`). If broadcast ever exceeds 20 min, a second cron starting after expiry would see the lock gone and start a parallel broadcast.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/scripts/baltic_ingestion.py:44`, `/Users/bigode/Dev/agentics_workflows/docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md:343-347`
- **Why it matters:** Double WhatsApp broadcast to the full contact list. Probability low but non-zero.
- **Effort:** small (tune TTL) / medium (holder-run-id pattern from the spec's "long-term" suggestion).
- **Priority:** **[HIGH]** (because consequence is user-visible duplicate messages)

### [HIGH] No dead-letter queue for failed WhatsApp sends
- **What:** `execution/core/delivery_reporter.py` tracks per-contact results, but a crash mid-broadcast leaves the sent-flag unset → next cron replays the entire contact list (spec `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md:335-341` accepts this as "Known limitation"). WhatsApp idempotency key (`webhook/dispatch.py:62-65`) is `sha1(phone|draft_id|message)` with 24h TTL — so cron reruns that re-hash same inputs will dedup. But the dedup is keyed on message *content*; if the report has any timestamp or dynamic field, dedup misses.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/dispatch.py:62-98`, `/Users/bigode/Dev/agentics_workflows/execution/core/delivery_reporter.py`
- **Why it matters:** Mid-broadcast crash → duplicate messages to N contacts. Spec documents "escape hatch: per-contact dedup Redis set, ~20 lines, not in scope today."
- **Effort:** small — spec's escape hatch is ready to implement.
- **Priority:** **[HIGH]**

### [MEDIUM] `cron_crashed` emission depends on decorator ordering
- **What:** `@with_event_bus` in `execution/core/event_bus.py:335-395` emits `cron_started` on entry and either `cron_finished` or `cron_crashed` on exit. If a script exits via `sys.exit(0)` or `sys.exit(1)` (see `execution/scripts/morning_check.py`) inside the decorator body, `SystemExit` is caught and emitted correctly — verified by reading `event_bus.py:360-388`. However, pre-run guards that fire before `@with_event_bus` kicks in (e.g., module-level env-var asserts) would crash silently. Not common today.
- **Files:** `execution/core/event_bus.py:335-395`
- **Priority:** **[MEDIUM]**

### [MEDIUM] No rate limiting / circuit breaker on UazAPI calls
- **What:** `execution/integrations/uazapi_client.py:18` has `@retry_with_backoff(max_attempts=3, base_delay=2.0)` on `send_message`. Per-contact retry with 2s/4s/8s backoff on 500s; no handling for 429 specifically. `execution/core/delivery_reporter.py:158` detects `status == 429 or ("rate" in reason_lower)` and classifies as rate-limited, but does NOT back off — it just logs the failure and moves to the next contact. A 429 burst during broadcast would silently skip recipients.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/integrations/uazapi_client.py:18`, `/Users/bigode/Dev/agentics_workflows/execution/core/delivery_reporter.py:158`
- **Effort:** medium — add exponential sleep on 429 response inside retry decorator.
- **Priority:** **[MEDIUM]**

### [MEDIUM] Apify actor deploys are manual; CI drift risk
- **What:** `docs/superpowers/followups/2026-04-22-observability-trace-id-apify-followups.md:5-23` flags this. Actors live in `actors/<name>/` but deploy independently via `apify push`. If forgotten, actor keeps running old code that ignores `trace_id` input → correlation gap until someone pushes.
- **Files:** `actors/platts-scrap-reports`, `actors/platts-scrap-full-news`; deploy gate: `apify login` with `bigodeio05/platts-scrap-*` namespace.
- **Effort:** medium — add GH Action step that runs `apify push` when `actors/**/src/**` changes.
- **Priority:** **[MEDIUM]**

### [LOW] Railway health checks not verified
- **What:** `Dockerfile` exists (8.2 kB) but no explicit `HEALTHCHECK` directive was found. `/health` endpoint exists on the webhook side; Railway's default TCP health check would suffice, but explicit HEALTHCHECK is missing.
- **Files:** `/Users/bigode/Dev/agentics_workflows/Dockerfile`
- **Effort:** small.
- **Priority:** **[LOW]**

### [LOW] `.state/` dir tracked + contains stale JSON
- **What:** `.gitignore:10` says `.state/*.json`, and the dir is empty on disk — good. But directory `.state/` itself exists (empty) and is not gitignored. Residue from earlier manual runs that wrote `.state/<workflow>.json` before Redis migration.
- **Priority:** **[LOW]**

---

## 4. Developer Experience

### [HIGH] No root `README.md`
- **What:** `find . -maxdepth 1 -name 'README*'` returns nothing. Only `dashboard/README.md` (Next.js default). New contributors have to infer setup from `AGENT.md` (8 kB) and `.planning/codebase/*` (good secondary docs). `.env.example` is minimal.
- **Files:** none.
- **Effort:** medium — write a README with: 1-paragraph scope, architecture diagram reference, `uv sync`, `REDIS_URL=redis://localhost:6379 uv run pytest`, deploy targets (GH Actions crons vs. Railway webhook vs. Apify actors vs. Vercel dashboard).
- **Priority:** **[HIGH]**

### [HIGH] System pip is broken on this machine; use `uv`
- **What:** User MEMORY.md note: `pyexpat` dylib issue from Python 3.14. Workflows that assume `pip install` in docs will fail. Plans reference `uv run pytest` already (see `docs/superpowers/plans/2026-04-22-bot-queue-bulk-actions-plan.md` throughout), but `Dockerfile` and `requirements.txt` use `pip`.
- **Files:** `/Users/bigode/Dev/agentics_workflows/Dockerfile`, `/Users/bigode/Dev/agentics_workflows/requirements.txt`.
- **Effort:** small — document in README; optionally migrate Dockerfile to `uv pip install`.
- **Priority:** **[HIGH]** (contributor-blocker on this workstation)

### [MEDIUM] Two requirements.txt need sync (duplicated from Tech Debt section for DX visibility)
- See §2 for details. The DX pain is specifically: a dev adds a new import in `execution/`, tests pass locally via root `requirements.txt`, Railway build fails.
- **Priority:** **[MEDIUM]**

### [MEDIUM] Local Redis required for dev
- **What:** `tests/test_workflow_trigger.py:18` sets `monkeypatch.setenv("REDIS_URL", "redis://localhost:6379")` suggesting tests assume local Redis. But most tests (`test_state_store.py`, `test_queue_selection.py`, `test_curation_redis_client.py`) use `fakeredis` via conftest fixture → no local Redis needed. A manual `python execution/scripts/morning_check.py --dry-run` DOES require real Redis on `REDIS_URL`.
- **Files:** `/Users/bigode/Dev/agentics_workflows/tests/conftest.py`, `/Users/bigode/Dev/agentics_workflows/tests/test_workflow_trigger.py:18`
- **Effort:** small — document "for script dry-runs, start `redis-server` locally or point REDIS_URL at a dev instance".
- **Priority:** **[MEDIUM]**

### [MEDIUM] Test coverage blind spots
- **What:** Test suite is 55 files. Inspection-level blind spots:
  - `webhook/dispatch.py` async flows — `test_dispatch_idempotency.py` exists but only covers the SET NX key. No test for 429 retry path or partial-success broadcast.
  - `execution/scripts/debug_apify.py` — untested (also dead, see §2).
  - Redis connection-failure paths in `webhook/queue_selection.py` — no test stubs a failing `redis.exceptions.ConnectionError` on `smembers` / `sadd`.
  - Dashboard `/api/*` routes — zero automated tests on the Next.js side.
- **Files:** `/Users/bigode/Dev/agentics_workflows/tests/` (55 files), `/Users/bigode/Dev/agentics_workflows/dashboard/` (no test dir).
- **Effort:** medium per gap.
- **Priority:** **[MEDIUM]**

### [LOW] Worktrees still present from stale migrations
- **What:** `.worktrees/phase1-aiogram` and `.worktrees/phase2-ux` on disk (gitignored via `.gitignore:42`). Local branches `feature/phase1-aiogram3-migration`, `feature/phase2-professional-ux`, `phase1-safety-net`, `phase2-router-split`, `phase3-observability` exist. Stale relative to main.
- **Effort:** small — `git worktree remove`, `git branch -d`.
- **Priority:** **[LOW]**

### [LOW] `tests/_manual_format_check.py` in tests dir
- **What:** Non-pytest helper (leading underscore prefix avoids auto-collection). If intent is to exclude from test runs, good — but it's still in the tests dir.
- **Priority:** **[LOW]**

---

## 5. Observability & Monitoring

### [HIGH] Phase 4 trace_id → Apify propagation has 6 deferred items
- **What:** `docs/superpowers/followups/2026-04-22-observability-trace-id-apify-followups.md` lists 6 open items. Paraphrased:
  - **P4.1** (§50-55): step/api_call inside actors — today actors emit only lifecycle (`cron_started`/`cron_finished`). Operator cannot see login, grid nav, supabase upload in `/tail`.
  - **P4.2** (§57-61): `/tail --trace=<id>` cross-workflow filter. Today `/tail <workflow>` returns only that workflow's rows; correlated failures require manual SQL.
  - **P4.3** (§63-68): instrument legacy actors `platts-news-only` and `platts-scrap-price` if reactivated.
  - **P4.4** (§70-74): shared EventBus npm package (see §2 Duplicated EventBus).
  - **P4.5** (§76-82): JS EventBus env-var fallback for `TRACE_ID` / `PARENT_RUN_ID` (Python has it; JS doesn't).
- **Files:** `/Users/bigode/Dev/agentics_workflows/docs/superpowers/followups/2026-04-22-observability-trace-id-apify-followups.md`
- **Known limitations (§84-124):** L1 manual actor deploy, L2 orphan Apify Console runs create fresh traces, L3 Supabase slow-insert could block actor shutdown, L4 PostgREST errors now logged via `console.warn` (fixed), L5 non-Error throws handled (fixed), L6 pre-run `Actor.fail/exit` orphans (fixed for current scripts, pattern required for any new guard).
- **Effort:** small to medium each.
- **Priority:** **[HIGH]** collective, **[MEDIUM]** each

### [MEDIUM] No alerting dashboard for `cron_events` / `event_log`
- **What:** Events land in Supabase `event_log` table (`supabase/migrations/20260418_event_log.sql`). `/tail <workflow>` queries it on demand. No standing alert (Grafana, Supabase dashboard, or Telegram bot auto-digest) that notifies on `cron_crashed` events without operator explicitly running `/tail`. Streak alerts exist (`state_store._send_streak_alert` at 3x failures) but fire only after 3 consecutive failures.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/core/state_store.py:290-317`, `/Users/bigode/Dev/agentics_workflows/supabase/migrations/20260418_event_log.sql`
- **Effort:** medium — add a cron or Supabase Edge Function that polls `event_log` every 5 min and alerts on `level='error'` in the last 5 min.
- **Priority:** **[MEDIUM]**

### [MEDIUM] No histogram of cron duration
- **What:** `duration_ms` is captured in `state_store.record_success`/`record_failure` payloads but is write-only — no read/aggregate exposed. Prometheus has no `cron_duration_ms{workflow}` histogram.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/core/state_store.py:80-116`
- **Effort:** medium.
- **Priority:** **[MEDIUM]**

### [LOW] Sentry opt-in not enforced
- **What:** `.env.example:19` has `SENTRY_DSN=` (empty = disabled, explicitly by comment). Production deploys must remember to set it.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/core/sentry_init.py`
- **Priority:** **[LOW]**

---

## 6. Performance

### [MEDIUM] `redis_queries.list_staging` uses SCAN + N GETs (N=50 default, 200 in bulk select-all)
- **What:** `webhook/redis_queries.py:48+` does `scan_iter(match="platts:staging:*", count=200)` then `client.get(key)` per key. For the bulk select-all flow, this is 200 round-trips to Redis.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/redis_queries.py:50-70`
- **Why it matters:** Each GET is ~1 ms local, ~10–50 ms from Railway to managed Redis. 200 items = up to 10s for a single `/queue` bulk operation.
- **Effort:** small — use `MGET` with the scanned keys, or pipeline the GETs.
- **Priority:** **[MEDIUM]**

### [MEDIUM] `queue_selection.toggle` issues 3–4 Redis commands per click
- **What:** `webhook/queue_selection.py:88-103` — SADD (returns 1 or 0), then a pipeline of either (SREM + 3× EXPIRE) or just (3× EXPIRE). Per checkbox click. For rapid clicking, 3–4 round-trips × multiple clicks.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/queue_selection.py:88-103`
- **Effort:** small — collapse to a single pipeline including SADD.
- **Priority:** **[MEDIUM]** (UX: perceptible lag on each click if Redis is remote)

### [LOW] `list_staging` default `limit=50` not enforced in webhook; bulk select-all uses 200
- **What:** `format_queue_page` calls `list_staging(limit=200)`. If staging ever grows past 200, `on_queue_sel_all` silently selects only the first 200.
- **Files:** `/Users/bigode/Dev/agentics_workflows/webhook/bot/routers/callbacks_queue.py:153`, `/Users/bigode/Dev/agentics_workflows/webhook/redis_queries.py`
- **Effort:** small — document the cap in a user-visible toast or raise an error.
- **Priority:** **[LOW]**

### [LOW] No dashboard bundle analysis
- **What:** `dashboard/next.config.ts` has minimal config; no bundle analyzer. All pages are client components (`"use client"` at the top), which maximizes JS shipped to browser.
- **Files:** `/Users/bigode/Dev/agentics_workflows/dashboard/next.config.ts`, `/Users/bigode/Dev/agentics_workflows/dashboard/app/*/page.tsx`
- **Effort:** medium — migrate list pages to Server Components.
- **Priority:** **[LOW]**

---

## 7. Data Integrity

### [HIGH] Idempotency split-lock rework — correctness audit vs. spec
- **What:** Spec `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md` prescribes Phase 0 (`check_sent_flag`) → Phase 1 (fetch) → Phase 2 (validate) → Phase 3 (acquire `inflight_key`) → Phase 4a-b (process + side-effects) → Phase 4c (`set_sent_flag`) → finally (`release_inflight`). Implementation landed in `execution/scripts/baltic_ingestion.py` (commit `35629d4`) and `execution/scripts/morning_check.py` (commit `43aa332`). New `state_store` helpers landed (`check_sent_flag`, `set_sent_flag`, `release_inflight` — see `execution/core/state_store.py:240-287`). `event_bus` label fallback landed (`"label": label or event` at `event_bus.py:137`). Tests: `tests/test_baltic_ingestion_idempotency.py` and `tests/test_morning_check_idempotency.py` present with 7 + 5 scenarios per the plan.
- **Unaudited carefully:** exact `_SENT_FLAG_TTL_SEC = 48 * 3600` and `_INFLIGHT_LOCK_TTL_SEC = 20 * 60` constants (`baltic_ingestion.py:44-45`) match the spec's 48h / 20min. Whether `release_inflight` actually runs in `finally` (vs. being inside an `except`) requires line-level code read of both scripts. The spec's "ordering of sent-flag before release" (so a crash between them leaves sent=set, inflight=expired naturally) is implied but not asserted in the test scenarios — tests only count call counts, not relative order.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/scripts/baltic_ingestion.py:44-45`, `/Users/bigode/Dev/agentics_workflows/execution/scripts/morning_check.py`, `/Users/bigode/Dev/agentics_workflows/execution/core/state_store.py:240-287`, `/Users/bigode/Dev/agentics_workflows/tests/test_baltic_ingestion_idempotency.py`, `/Users/bigode/Dev/agentics_workflows/tests/test_morning_check_idempotency.py`.
- **Why it matters:** The whole reason for the rework was a production outage on 2026-04-22 (Baltic report not delivered). Regression of this ordering would repeat the outage.
- **Effort:** small — add `mock.call_args_list` order assertions to scenario 7 in baltic test and scenario 5 in morning_check test to pin `set_sent_flag` < `release_inflight`.
- **Priority:** **[HIGH]**

### [HIGH] `state_store` and `daily_report:sent:*` key are the sole source of truth for "delivered today"
- **What:** A Redis flush / eviction would allow a re-send of the same daily report. Contacts table in Supabase is source of truth for recipients, but there is no "delivered to this contact today" table. `delivery_reporter` writes per-contact status to `event_log` but that is not queried on startup to reconstruct state.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/core/state_store.py`, `/Users/bigode/Dev/agentics_workflows/supabase/migrations/20260418_event_log.sql`.
- **Why it matters:** If Redis evicts the 48h sent-flag (which shouldn't happen on managed Redis with enough memory, but could under pressure), the next cron re-broadcasts. This is a known limitation documented in the idempotency spec.
- **Effort:** medium — add a Supabase-backed fallback check (query `event_log` for `cron_finished` within last 24h of same workflow).
- **Priority:** **[HIGH]**

### [MEDIUM] Contacts table has no foreign key to any other table
- **What:** `supabase/migrations/20260422_contacts.sql` creates `contacts(id uuid primary key, ...)` with indices and RLS but no FK. Delivery history lives in `event_log` with no `contact_id` column (detail is in JSON payload). Cannot easily query "last delivery status for phone X".
- **Files:** `/Users/bigode/Dev/agentics_workflows/supabase/migrations/20260422_contacts.sql`
- **Effort:** medium — design a `delivery_log(contact_id, report_type, delivered_at, status)` table.
- **Priority:** **[MEDIUM]**

### [MEDIUM] Phone normalization assumes BR 9-digit convention
- **What:** `execution/integrations/contacts_repo.py:42-75` uses `phonenumbers` library with E.164 validation. Commit `84835b2` (referenced in task prompt) addressed BR 9-digit. The `normalize_phone` rejects non-valid numbers (line 70 `is_valid_number` check). Older contacts in historical data may have been stored with 8-digit local numbers pre-migration — those are now unreachable.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/integrations/contacts_repo.py:42-75`
- **Priority:** **[MEDIUM]**

### [MEDIUM] Contacts dedup enforced only on `phone_uazapi`, not on `name`
- **What:** `supabase/migrations/20260422_contacts.sql:16` has `create unique index contacts_phone_uazapi_uidx on contacts (phone_uazapi)`. No uniqueness on `name`. Two contacts with same name but different phones are allowed (intentional — person may have work+personal). No soft-dedup warning in `/add` flow.
- **Files:** `/Users/bigode/Dev/agentics_workflows/supabase/migrations/20260422_contacts.sql:16`
- **Priority:** **[MEDIUM]**

### [LOW] No automated backup or point-in-time recovery config
- **What:** No scripts, GH Action, or Supabase config for regular dumps. Supabase managed plan has automatic backups, but the retention window is plan-dependent and not pinned in repo.
- **Files:** none.
- **Effort:** medium — document Supabase plan's backup retention; consider weekly `pg_dump` to S3/GCS for longer retention.
- **Priority:** **[LOW]**

### [LOW] `data/` directory tracked with stale JSON
- **What:** `data/news_drafts.json` + `data/msg_draft_*.txt` on disk. `.gitignore:17` says `data/`. `git ls-files data/` — empty. Local-only artifacts.
- **Priority:** **[LOW]**

---

## 8. In-Flight / Undone Work

### [HIGH] `/queue` bulk-actions: 7 commits landed, plan steps fully executed
- **What:** Plan `docs/superpowers/plans/2026-04-22-bot-queue-bulk-actions-plan.md` has 7 tasks, all visible in git log: `e9901c3` (queue_selection module), `3c3332c` (atomic toggle), `183e476` (bulk_archive/discard), `5c2329b` (plan doc), `27c268f` (spec doc), `d873fa9` (CallbackData classes), `c67b85c` (select-mode render), `f940166` (singular/plural polish), `f2897d9` (mode toggle handler), `4a46b53` (catch TelegramBadRequest), `fa4b042` (item toggle/select-all/clear), `7e8f29f` (bulk prompt/confirm/cancel), `7d5f337` (error recovery), `9ca394c` (final review polish), `38f59f3` (preserve page). Feature appears shipped to main.
- **Residual concerns:** (a) Per-user authorization — see §1 [HIGH]. (b) Test file `tests/test_queue_selection.py` covers the state module but callback handler tests assumed in plan (`tests/test_callbacks_queue.py`) were not visible in `git log` — need verification.
- **Effort:** small — verify `tests/test_callbacks_queue.py` exists and covers the 7 handlers.
- **Priority:** **[HIGH]** (not because it's broken, but because the per-user auth gap is a design debt)

### [HIGH] Uncommitted changes to all 6 codebase map docs
- **What:** `git status` shows `.planning/codebase/{ARCHITECTURE,CONCERNS,INTEGRATIONS,STACK,STRUCTURE,TESTING}.md` all modified but not committed.
- **Files:** `/Users/bigode/Dev/agentics_workflows/.planning/codebase/*.md`
- **Effort:** trivial — commit these after the refresh pass.
- **Priority:** **[HIGH]** (workflow hygiene — they're the output of this very mapping effort)

### [MEDIUM] Stale local branches
- **What:** Branches `feature/phase1-aiogram3-migration`, `feature/phase2-professional-ux`, `phase1-safety-net`, `phase2-router-split`, `phase3-observability` exist locally. Phase migrations presumably merged into main. Worktrees `phase1-aiogram` and `phase2-ux` exist under `.worktrees/`.
- **Files:** local git refs, `/Users/bigode/Dev/agentics_workflows/.worktrees/`
- **Effort:** small — `git branch -d` and `git worktree remove`.
- **Priority:** **[MEDIUM]**

### [MEDIUM] Remote branches `chore/cleanup-sheets-client` and `feat/contacts-supabase`
- **What:** `git branch -a` shows `remotes/origin/chore/cleanup-sheets-client` and `remotes/origin/feat/contacts-supabase`. Contacts Supabase migration landed on main (`cdf354d chore(contacts): retire Google Sheets artifacts after Supabase migration (#2)`) — these remotes may be stale.
- **Effort:** small — `git push origin --delete` if truly merged.
- **Priority:** **[MEDIUM]**

### [LOW] Phase-4 followups explicitly open
- **What:** Six phase-4 deferred items (see §5). None blocking.
- **Priority:** **[LOW]** per item

### [LOW] Supabase migration pending placeholder
- **What:** `execution/supabase/migrations/20260416042250_remote_orphan_placeholder.sql` — presumably a placeholder from Supabase CLI remote-orphan scenario. Not reviewed for content.
- **Files:** `/Users/bigode/Dev/agentics_workflows/execution/supabase/migrations/20260416042250_remote_orphan_placeholder.sql`
- **Priority:** **[LOW]**

---

## Priority Summary

**CRITICAL (1):**
1. Hardcoded `IRONMARKET_API_KEY` in `baltic_ingestion.py:41` — rotate + purge history now.

**HIGH (11):**
- Service-role key in Next.js routes (design fragility)
- Telegram webhook missing `secret_token`
- `REDIS_URL` — 5 duplicated lazy clients, `.env.example` omission
- Per-user authorization absent in queue bulk ops (design debt)
- Two `requirements.txt` must be manually synced
- Hardcoded IronMarket URL + dry-run HTTP leak
- Asymmetric Redis error handling across 4 modules
- Minimal Redis observability
- In-flight lock TTL race with slow broadcasts
- Missing dead-letter / per-contact dedup in broadcast
- No root README, pip broken on this machine
- Phase 4 trace_id/Apify followups (collective)
- Idempotency split-lock order assertion gap
- `state_store` sole source of truth for "delivered today"
- `/queue` bulk-actions: per-user auth + test coverage gap
- Uncommitted codebase map docs

**MEDIUM (≈15):** rate limiting, metrics auth, `/health` token prefix leak, orphan `rationale_dispatcher`, dead `debug_apify.py`, duplicated JS EventBus, 4 Redis client factories, queue-selection TTL vs. restart, cron_crashed decorator ordering, uazapi 429 handling, Apify manual deploys, no event_log alerting, no duration histogram, `list_staging` N+1, queue-selection 3–4 commands per click, contacts table FKs, BR 9-digit phone assumption, stale local/remote branches, dashboard tests missing.

**LOW (≈10):** SQL injection surface nil, XSS surface nil, gitignore patterns un-anchored, `.env` layout, in-source TODOs, `.state/` dir, Sentry opt-in, dashboard bundle analysis, worktrees present, `tests/_manual_format_check.py`.

---

*Concerns audit: 2026-04-22. Next refresh recommended after the [CRITICAL] IronMarket key rotation lands and after per-user queue-selection authorization is designed.*
