# External Integrations

**Analysis Date:** 2026-04-22

This platform integrates with nine external surfaces: **Supabase** (Postgres + Storage + service role), **Apify** (four scraping actors), **Telegram Bot API** (operator chat + events channel + inline cards), **UazAPI** (WhatsApp broadcast), **Redis/Upstash** (state + curation + bot FSM + queue selection), **Claude/Anthropic** (AI extraction), **Microsoft Graph** (Baltic email), **LSEG Refinitiv** + **S&P Global Commodity Insights** (market data), and **IronMarket** (ingestion webhook). Google Sheets was retired on 2026-04-22 (see commit `cdf354d`, migration `supabase/migrations/20260422_contacts.sql`).

## APIs & External Services

### Supabase (primary data layer)
- **URL / role split** — `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` (webhook/Railway and GH Actions convention) or legacy `SUPABASE_KEY` fallback. `dashboard/app/api/contacts/route.ts:8-9` and `execution/core/event_bus.py:49-50` both accept either name, with `SUPABASE_SERVICE_ROLE_KEY` preferred.
- **Python SDK:** `supabase-py >=2.0.0,<3.0` via `execution/integrations/supabase_client.py:2` and `execution/core/event_bus.py:54`.
- **JS SDK:** `@supabase/supabase-js@^2.104.0` in dashboard + `^2.49.0` in `actors/platts-scrap-reports` + `^2.104.0` in `actors/platts-news-only`.
- **Tables used (Postgres):**
  - `contacts` — WhatsApp broadcast list, replaces Google Sheet `1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0`. Columns per `supabase/migrations/20260422_contacts.sql`: `id (uuid)`, `name`, `phone_raw`, `phone_uazapi (unique)`, `status ∈ {ativo, inativo}`, `created_at`, `updated_at` + trigger. RLS enabled (service-role bypass only).
  - `event_log` — observability timeline (both `supabase/migrations/20260418_event_log.sql` with `draft_id/label/detail jsonb` form AND the parallel idempotent schema `execution/supabase/migrations/20260421_event_log.sql` with `workflow/run_id/trace_id/parent_run_id/level/event/label/detail/pod`). Written by `ProgressReporter.step()` and by both Python `_SupabaseSink` (`execution/core/event_bus.py`) and JS `EventBus` (`actors/*/src/lib/eventBus.js:82`). RLS enabled 2026-04-19; no anon policies.
  - `platts_reports` — metadata for PDFs uploaded by the reports actor (`actors/platts-scrap-reports/src/persist/supabaseUpload.js:14,56,73`).
  - `sgx_prices` — referenced at `execution/integrations/supabase_client.py:24` (TODO comment notes table name is unconfirmed).
- **Storage buckets:**
  - `platts-reports` — PDF storage with signed URLs (1 h expiry) at `webhook/reports_nav.py:232` and `webhook/routes/mini_api.py:381`. Populated by the `platts-scrap-reports` actor.
- **Migrations live in two directories** — `supabase/migrations/` (canonical, applied) and `execution/supabase/migrations/` (idempotent operational deltas). The event_log schema diverges between the two and handlers tolerate both shapes.

### Apify (scraping / browser actors)
- **Token:** `APIFY_API_TOKEN` (GH Actions secret; passed to `execution/integrations/apify_client.py:14`).
- **SDKs:** `apify-client >=1.0.0` (Python, `execution/integrations/apify_client.py:2`) and `apify-client@^2.22.0` (root Node scripts).
- **Actors hosted on Apify under account `bigodeio05`:**
  | Actor ID | Source | Purpose | Called from |
  |---|---|---|---|
  | `bigodeio05/platts-scrap-full-news` | `actors/platts-scrap-full-news/` | Crawls Platts allInsights + ironOreTopic + RMW, extracts articles, emits via `eventBus.js` | `execution/scripts/platts_ingestion.py:29` (env `APIFY_PLATTS_ACTOR_ID`) and `execution/scripts/inspect_platts.py:21` |
  | `bigodeio05/platts-scrap-reports` | `actors/platts-scrap-reports/` | Downloads Platts Market + Research Report PDFs, uploads to Supabase Storage `platts-reports` bucket | `execution/scripts/platts_reports.py:26` (env `APIFY_PLATTS_REPORTS_ACTOR_ID`) |
  | `bigodeio05/platts-scrap-price` | `actors/platts-scrap-price/` | Scrapes Platts price symbols (Crawlee + Playwright) | Not yet wired to a cron; manual dispatch only |
  | `bigodeio05/platts-news-only` | `actors/platts-news-only/` | News-only variant; writes to `event_log` via Supabase | Not yet wired to a cron |
- **Input passed to actors** (see `execution/scripts/platts_ingestion.py:244-264`): `username`, `password`, `sources: ["allInsights", "ironOreTopic", "rmw"]`, `includeFlash`, `includeLatest`, `maxArticles`, `dedupArticles`, and the **Phase 4 observability keys `trace_id` + `parent_run_id`** pulled from `get_current_bus()`.
- **Actor → Supabase write path** — actors receive `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` via Apify Secret env vars and write directly to `event_log` and (reports only) `platts_reports` + `platts-reports` bucket.

### Telegram Bot API
- **Auth:** `TELEGRAM_BOT_TOKEN` (required in both cron scripts and webhook bot).
- **Channels:**
  - `TELEGRAM_CHAT_ID` — operator main chat. Receives errors, streak alerts, curation cards.
  - `TELEGRAM_EVENTS_CHANNEL_ID` — firehose of all `info`-level events (`cron_started`, `cron_finished`, step events). Watchdog workflow is on the denylist (`execution/core/event_bus.py:31`) so its every-5-min runs don't flood the channel.
- **Webhook endpoint:** `TELEGRAM_WEBHOOK_URL + /webhook` set at startup (`webhook/bot/main.py:58-61` via `bot.set_webhook(...)` and torn down at shutdown via `bot.delete_webhook()`).
- **Sending clients:**
  - `execution/integrations/telegram_client.py` — sync `requests`-based client used from cron scripts (default Markdown parse mode).
  - aiogram `Bot` singleton (`webhook/bot/config.py:42-49`, default `ParseMode.MARKDOWN`) — used by the webhook bot for inline keyboards and card rerenders.
- **Bot surface:**
  - Command routers: `webhook/bot/routers/commands.py` (public + admin + shared), onboarding, settings, `/queue`, `/tail`.
  - Callback routers: `callbacks_curation`, `callbacks_reports`, `callbacks_queue` (new), `callbacks_menu`, `callbacks_contacts`, `callbacks_workflows`.
  - FSM storage: `aiogram.fsm.storage.redis.RedisStorage` at `REDIS_URL` (`webhook/bot/config.py:11,38`).

### UazAPI (WhatsApp broadcast)
- **Base URL:** `UAZAPI_URL` (default `https://mineralstrading.uazapi.com`).
- **Auth:** `UAZAPI_TOKEN` (sent as `token` header, not `Authorization`).
- **Client:** `execution/integrations/uazapi_client.py:10` — sync `requests.post` to `{base}/send/text`, retried 3× with 2 s exponential backoff via `@retry_with_backoff` (`execution/core/retry.py`).
- **Consumers:** all broadcast paths — `morning_check.py`, `send_daily_report.py`, `send_news.py`, `baltic_ingestion.py`, plus the bot-side dispatch fanout (`webhook/dispatch.py`).

### Redis (Upstash)
- **Connection:** `REDIS_URL` (single URL; provider is Upstash per `docs/superpowers/specs/2026-04-14-redis-state-and-admin-ux-design.md:22`).
- **Five consumers, one connection string:**
  1. **Workflow state** (`execution/core/state_store.py`) — keyspace `wf:last_run:<workflow>`, `wf:failures:<workflow>` (list, trimmed to 3), `wf:streak:<workflow>` (counter), `wf:crash_dedup:<workflow>` (5 min SET NX TTL, added for idempotency), `wf:watchdog_alerted:*` (alert dedup). Non-raising; silent no-op when Redis down.
  2. **Curation staging/archive** (`execution/curation/redis_client.py`) — `platts:staging:<id>` (48 h TTL), `platts:archive:<date>:<id>` (no TTL), `platts:seen` (ZSET, 30 d rolling dedup), `platts:scraped:<date>` (SET, 30 d TTL), `platts:rationale:processed:<date>` (30 h flag with SET NX). **Raises on failure** — load-bearing.
  3. **Bot query helpers** (`webhook/redis_queries.py`) — read-side list/count/stats + new `webhook:feedback:*` Hash + `webhook:feedback:index` ZSET + `platts:pipeline:processed:<date>` write key.
  4. **Bot queue select mode** (new in `webhook/queue_selection.py`) — `bot:queue_mode:{chat_id}` (string "select"), `bot:queue_selected:{chat_id}` (SET of staging ids), `bot:queue_page:{chat_id}` (int as string). All three share a 10 min TTL refreshed on every mutation. Reuses `execution.curation.redis_client._get_client()` so bulk archive/discard ops share the curation connection.
  5. **Aiogram FSM storage** (`webhook/bot/config.py:11,38`) — aiogram's own keyspace for user conversational state.
- **Actor-side dedup** — `actors/platts-scrap-reports/` also consumes `REDIS_URL` for PDF dedup (see `docs/superpowers/plans/2026-04-15-platts-reports-actor-plan.md:884-885`).
- **Library:** `redis-py >=5.0,<6.0` (sync client, `decode_responses=True`, `socket_connect_timeout=3`, `socket_timeout=3`). Tests use `fakeredis >=2.20,<3.0`.

### Claude / Anthropic
- **Key:** `ANTHROPIC_API_KEY`.
- **Client:** `execution/integrations/claude_client.py:5` (`Anthropic` SDK, `anthropic >=0.40.0`).
- **Use cases:**
  - PDF extraction of Baltic Exchange routes tables (`extract_data_from_pdf`, called from `baltic_ingestion.py`).
  - Rationale / curation LLM agent (`execution/agents/rationale_agent.py`).
- Passed in as an env var to the bot container as well (`webhook/bot/config.py:20`) for bot-side LLM use.

### Microsoft Graph (Baltic)
- **Auth:** OAuth2 client-credentials flow via `msal >=1.31.0`. Env vars: `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_TARGET_MAILBOX`.
- **Scope:** `https://graph.microsoft.com/.default`.
- **Client:** `execution/integrations/baltic_client.py:7-40` — resolves a mailbox, reads emails from the last 24 h filtered by sender `DailyReports@midship.com` + keyword `Exchange`, downloads PDF attachments.

### LSEG Refinitiv Data Library
- **Env:** `LSEG_APP_KEY`, `LSEG_USERNAME`, `LSEG_PASSWORD`.
- **SDK:** `lseg-data >=1.0.0`. Bootstrapped via a temporary `lseg-data.config.json` written by `LSEGClient._create_config_file()` (`execution/integrations/lseg_client.py:10-30`). Falls back to an existing file for local dev.
- **Used by:** `execution/scripts/send_daily_report.py` for SGX iron-ore futures curves.

### S&P Global Commodity Insights (Platts legacy API)
- **Env:** `SPGCI_USERNAME`, `SPGCI_PASSWORD`.
- **SDK:** `spgci >=0.0.70`. Hardcoded symbol map at `execution/integrations/platts_client.py:17-58` (Brazilian Blend Fines, Pilbara, Jimblebar, etc.).
- **Used by:** `execution/scripts/morning_check.py` for price-report data (Platts news path uses the Apify actor instead).

### IronMarket (downstream ingestion)
- **Endpoint:** `https://merry-adaptation-production.up.railway.app/ingest/price` (hardcoded at `execution/scripts/baltic_ingestion.py:40`).
- **Auth:** `X-API-Key` header — value from env `IRONMARKET_API_KEY` with a hardcoded fallback at `execution/scripts/baltic_ingestion.py:41` (existing debt).
- **Used by:** `baltic_ingestion.py` to POST extracted BCI/C5TC/route data.

### GitHub API (from dashboard)
- **SDK:** `octokit@^5.0.5` in the dashboard.
- **Auth:** `GITHUB_TOKEN` server-side env var on the dashboard (not a secret in this repo; set on Vercel).
- **Use cases:**
  - `dashboard/app/api/workflows/route.ts:14-22` — lists `GET /repos/bigodinhc/workflows_minerals/actions/runs` for the executions view.
  - `dashboard/app/api/delivery-report/route.ts:35,38-46` — reads job logs for delivery-report rendering.

## Data Storage

**Databases:**
- **Supabase Postgres** — `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`. Primary tables: `contacts`, `event_log`, `platts_reports`, `sgx_prices` (unconfirmed). Service-role key is the only writer; RLS enabled on `contacts` and `event_log`.

**File Storage:**
- **Supabase Storage bucket `platts-reports`** — PDF reports from the reports actor. Signed 1 h URLs for bot and mini-app delivery (`webhook/reports_nav.py:232`, `webhook/routes/mini_api.py:381`).

**Caching / ephemeral state:**
- **Upstash Redis** — see Redis section above. No separate cache layer; Redis is the KV + cache + FSM backend.

## Authentication & Identity

- **Operator access** — Telegram user IDs matched against a role map (see `webhook/bot/middlewares/` `RoleMiddleware`; queue callbacks require `admin` — `webhook/bot/routers/callbacks_queue.py:32`).
- **API auth** — all internal surfaces authenticate via env-var secrets (bearer or custom header); no JWT/session layer.
- **Supabase RLS** — enabled on `event_log` (2026-04-19) and `contacts` (2026-04-22); service-role bypasses. Anon/public have zero access.

## Monitoring & Observability

**EventBus (cross-language):**
- Python: `execution/core/event_bus.py` — `EventBus` class with four sinks: stdout (JSON), Supabase `_SupabaseSink` (inserts into `event_log`), `_MainChatSink` (Telegram main chat, warn/error/`cron_crashed`/`cron_missed` only), `_EventsChannelSink` (firehose; `watchdog` denylisted).
- JS: `actors/platts-scrap-full-news/src/lib/eventBus.js` and `actors/platts-scrap-reports/src/lib/eventBus.js` — two sinks (stdout + Supabase). Schema mirrored against the Python contract so `trace_id` joins work.
- **Trace propagation (Phase 4):** crons emit `cron_started`, pass `bus.trace_id` + `bus.run_id` as `parent_run_id` into the actor run input (`execution/scripts/platts_ingestion.py:258-260`). Actors initialize their `EventBus` with those values (`actors/platts-scrap-full-news/src/main.js:67` → `new EventBus({ workflow, traceId: input.trace_id, parentRunId: ... })`). A single `trace_id` therefore spans Python cron + Apify actor for end-to-end timeline reconstruction.

**Event types emitted** (from `execution/core/event_bus.py` + script emit sites):
- **Cron lifecycle:** `cron_started`, `cron_finished`, `cron_crashed` (from `@with_event_bus` decorator at `execution/core/event_bus.py:361,366,388`).
- **Operator alerts:** `cron_missed` (emitted by watchdog at `execution/scripts/watchdog_cron.py:73`).
- **Step events:** `step`, `api_call` (ad-hoc from scripts — see `morning_check.py:227,243,250,254,289,315`, `baltic_ingestion.py:200,245,251,352,355,363`, `platts_ingestion.py:75,131`, `platts_reports.py:43,74,77,99`, `send_daily_report.py:92,113,121,133,160`, `rebuild_dedup.py:89,94`).
- Actor-side events use the same schema, emitted via `this._bus.emit(event, { label, detail, level })`.

**Sinks:**
- `_ALERT_EVENTS = frozenset({"cron_crashed", "cron_missed"})` — always reach `_MainChatSink` (Telegram main chat), even at `info` level.
- `_SupabaseSink` — writes all events to `event_log` with `workflow, run_id, trace_id, parent_run_id, level, event, label, detail` (+ auto `ts`).
- `_SentryBreadcrumbSink` — attaches each event as a Sentry breadcrumb, which is then included when `cron_crashed` triggers `sentry_sdk.capture_exception(exc)` at `execution/core/event_bus.py:384`.

**State persistence (Redis-backed):**
- `state_store.record_success / record_failure / record_empty / record_crash` — maintain `wf:last_run:<workflow>` and streak counters, and trigger streak alerts at threshold 3 via Telegram (`_send_streak_alert` at `execution/core/state_store.py:290-317`).
- Idempotency: `wf:crash_dedup:<workflow>` with 5 min TTL prevents double-alerting when both `@with_event_bus` and `progress_reporter.fail()` observe the same exception (see `docs/superpowers/specs/2026-04-22-idempotency-claim-ordering-fix-design.md`).

**Sentry:**
- `sentry-sdk[aiohttp] >=2.0.0,<3.0.0` initialized per-script via `execution/core/sentry_init.py` when `SENTRY_DSN` is set (`traces_sample_rate=0.1`, env = `RAILWAY_ENVIRONMENT or "dev"`, tag `script=<name>`). Auto-init also triggered by `@with_event_bus` decorator at `execution/core/event_bus.py:354`.

**Prometheus:**
- `webhook/metrics.py:9` defines counters/histograms; exposed at the `/metrics` route in `webhook/routes/api.py:18`.

## CI/CD & Deployment

**GitHub Actions workflows** (all in `.github/workflows/`, all run on `ubuntu-latest`, all install `requirements.txt`):

| Workflow file | Purpose | Schedule (UTC) | Script |
|---|---|---|---|
| `baltic_ingestion.yml` | Pull Baltic Exchange email PDFs via MS Graph, extract with Claude, POST to IronMarket, broadcast via WhatsApp | `*/15 12,13,14 * * 1-5` (every 15 min, 09:00–11:45 BRT weekdays) | `execution/scripts/baltic_ingestion.py` |
| `morning_check.yml` | Platts morning report (price data via spgci) | `30,45 11 * * 1-5` + `0,15,30,45 12 * * 1-5` + `0 13 * * 1-5` (08:30–10:00 BRT weekdays) | `execution/scripts/morning_check.py` |
| `market_news.yml` | Platts news curation via Apify actor | `0 12,15,18 * * 1-5` (9/12/15h BRT weekdays) | `execution/scripts/platts_ingestion.py` |
| `daily_report.yml` | SGX iron-ore futures report via LSEG | `0 8,10,15,19 * * 1-5` + `30 12 * * 1-5` + `5 1 * * 2-6` (05/07/09:30/12/16 BRT weekdays + 22:05 previous-day) | `execution/scripts/send_daily_report.py` |
| `platts_reports.yml` | Daily PDF downloader via `platts-scrap-reports` actor + Supabase Storage upload | `0 23 * * *` (20:00 BRT daily incl. weekends) | `execution/scripts/platts_reports.py` |
| `watchdog.yml` | Meta-cron: detects missed cron schedules via `wf:last_run` state + croniter | `*/5 * * * *` (every 5 min) | `execution/scripts/watchdog_cron.py` |

Every workflow passes `REDIS_URL`, Telegram creds, Sentry DSN, and Supabase creds. Platts workflows also pass `APIFY_API_TOKEN` + `PLATTS_USERNAME`/`PASSWORD`. `market_news.yml` uniquely passes `TELEGRAM_WEBHOOK_URL` because the actor flow POSTs preview URLs to the bot.

**Railway (webhook bot):**
- Config: `/railway.json` — `DOCKERFILE` builder, `startCommand: python -m webhook.bot.main`, `restartPolicyType: ON_FAILURE`, `restartPolicyMaxRetries: 10`.
- Build: `/Dockerfile` — multi-stage (Node 20 builds Mini App → Python 3.11 runtime).
- **New Redis requirement** — Railway-hosted bot requires `REDIS_URL` (Upstash) to be set for aiogram FSM + queue selection + curation reads.

**Apify (actors):**
- Four actors under `bigodeio05/platts-*`. Each has its own `Dockerfile`, `package.json`, `README.md`. Actors receive env vars via Apify Secret + the run input payload from the calling cron.

**Vercel (dashboard):**
- Hosts `dashboard/` Next.js app at `https://workflows-minerals.vercel.app` (referenced in `execution/core/state_store.py:309` for streak-alert deep links). `GITHUB_TOKEN` + Supabase creds provisioned as Vercel env vars.

## Webhooks & Callbacks

**Incoming:**
- `POST {TELEGRAM_WEBHOOK_URL}/webhook` — Telegram bot updates (aiogram `SimpleRequestHandler`, registered in `webhook/bot/main.py:58-61`).
- `GET /metrics` — Prometheus scrape (`webhook/routes/api.py:18`).
- `GET/POST /api/*`, `GET /preview/*`, `GET/POST /mini/*` — dashboard/mini-app REST + static endpoints (`webhook/routes/{api,preview,mini_api,mini_static,mini_auth}.py`).

**Outgoing:**
- `POST https://merry-adaptation-production.up.railway.app/ingest/price` — IronMarket ingest (`execution/scripts/baltic_ingestion.py:187`).
- `POST {UAZAPI_URL}/send/text` — WhatsApp broadcast (`execution/integrations/uazapi_client.py:24`).
- Telegram Bot API `sendMessage` / `editMessageText` / `sendPhoto` / `sendDocument` — both clients.
- Apify `actor.call()` (via SDK) — blocking run-and-wait, 600 s default timeout (`execution/integrations/apify_client.py:31-35`).
- Graph API `/users/{mailbox}/messages` — Baltic mailbox reads.

## Retired / Decommissioned

- **Google Sheets** — removed as the contacts source of truth on 2026-04-22 (commit `cdf354d`). Data now lives in Supabase `contacts`. Residual artifacts:
  - `dashboard/package.json:18` still lists `googleapis@^171.2.0` but no dashboard source imports it. Safe to remove.
  - `tests/archive/test_migrate_contacts_from_sheets.py` + `scripts/archive/migrate_contacts_from_sheets.py` — archived one-off migration scripts (ignored by pytest via `--ignore=tests/archive`).
  - `execution/core/delivery_reporter.py:289` has a lingering docstring comment "Convert a Google Sheets row dict into a Contact" — stale but functional (the function still accepts dicts of the same shape).
- **`sheets_client.py`** — deleted; referenced by commit history only.

---

*Integration audit: 2026-04-22*
