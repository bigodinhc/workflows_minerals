# Backend Hardening v1 — Follow-ups

**Parent spec:** `docs/superpowers/specs/2026-04-18-backend-hardening-v1-design.md`
**Parent plans:**
- `docs/superpowers/plans/2026-04-18-phase1-safety-net.md`
- `docs/superpowers/plans/2026-04-18-phase2-router-split.md`
- `docs/superpowers/plans/2026-04-18-phase3-observability.md`

**Tags on main:** `phase1-safety-net-complete`, `phase2-router-split-complete`, `phase3-observability-complete`.

This file tracks work that was explicitly deferred out of the Backend Hardening v1 milestone, plus manual operational steps required for the shipped work to deliver full value in production.

---

## Manual operational steps (required for Phase 3 value in prod)

| # | Action | Status |
|---|---|---|
| 1 | Apply `supabase/migrations/20260418_event_log.sql` to Supabase dev + prod | ✅ applied 2026-04-19 |
| 2 | Apply `supabase/migrations/20260419_event_log_rls.sql` (RLS enable follow-up) to Supabase dev + prod | ✅ applied 2026-04-19 |
| 3 | Set `SENTRY_DSN` in Railway environment for webhook service + each cron script service | ✅ done |
| 4 | Set `SENTRY_DSN` in local `.env` for dev validation | ✅ done |
| 5 | Sentry smoke test (send test events, verify dashboard receives them) | ✅ done 2026-04-19 |
| 6 | Manual bot smoke test of 5 critical flows (idempotency, live progress card, /metrics, event_log SQL query, end-to-end broadcast) | pending |

---

## Phase 1 minor follow-ups (from final code review)

The Phase 1 final reviewer flagged three minor gaps that don't block the safety net but should be addressed eventually:

### F1.1 — Reports tests missing `query.answer` assertions

**Files:** `tests/test_callbacks_reports.py`
**Affected tests:** `test_on_report_years`, `test_on_report_year`, `test_on_report_month`, `test_on_report_back_year_target_parses_type_and_year`
**Gap:** Each of these tests asserts the delegated helper was called with the right args, but does NOT assert `query.answer("")` was awaited. If a Phase 2 refactor drops the `query.answer("")` call from any of these handlers, no test catches it. `test_on_report_type` already has the assertion — the others should follow the same pattern.

**Effort:** 4 one-line additions (`query.answer.assert_awaited_with("")`).

### F1.2 — `import asyncio` inside test function body

**Files:** `tests/test_callbacks_workflows.py`
**Location:** `test_workflow_run_no_run_id_shows_warning` (around line 57)
**Gap:** `import asyncio` is inside the function body rather than at module top. Stylistically inconsistent with the rest of the test suite; linters/import scanners will not flag it.

**Effort:** move 1 line to top of file.

### F1.3 — Reply keyboard handlers without coverage

**Files:** `tests/test_messages_fsm_isolation.py`
**Gap:** Phase 1 covered `on_reply_reports` and `on_reply_queue` but skipped `on_reply_workflows`, `on_reply_settings`, `on_reply_writer`, `on_reply_broadcast`, `on_reply_admin`. Five handlers on `reply_kb_router` have no characterization. Phase 2 didn't touch them, so no regression occurred — but coverage is incomplete.

**Effort:** 5 tests in `test_messages_fsm_isolation.py`, ~15 lines each.

---

## CONCERNS items deferred out of Backend Hardening v1

Items from `.planning/codebase/CONCERNS.md` that were explicitly not in scope for this milestone.

### C.1 — 🔴 Hardcoded `IRONMARKET_API_KEY`

**File:** `execution/scripts/baltic_ingestion.py:36`
**Status:** user explicitly deferred during brainstorming.
**Risk:** credential exposure if repo is ever public or archived.
**Suggested remediation when prioritized:** replace with `os.getenv("IRONMARKET_API_KEY")`, add to `.env.example` and Railway. Rotate the exposed key once removed.

### C.2 — 🟠 No CI test gate

**Files:** `.github/workflows/`
**Gap:** GitHub Actions workflows run data ingestion crons but nothing runs `pytest` on push/PR. All tests are manual/local. Phase 1's 53-test safety net only protects the developer running pytest locally.
**Suggested remediation:** add `.github/workflows/tests.yml`:
```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt
      - run: pip install -r webhook/requirements.txt
      - run: pytest --tb=short
```
Budget: ~30 minutes including secret-injection for tests that need env vars.

### C.3 — 🟠 Mini App auth hardening

**Files:** `webhook/routes/mini_auth.py`
**Gaps:** no rate limiting on `/api/mini/*`; no timestamp freshness check on `initData` (replay risk within signature validity window).
**Suggested remediation:** add `aiolimiter` per-IP token bucket; reject initData older than N minutes.
**Effort:** ~2-3 hours with tests.

### C.4 — 🟡 Supabase pagination on `reports_show_month_list`

**File:** `webhook/reports_nav.py:145-160`
**Gap:** fetches all reports for a month in one query. Scales poorly if >10k reports/month.
**Current state:** not a real-world problem yet (typical 30-60 reports/month).
**Effort:** ~1 hour if ever needed.

### C.5 — 🟡 Cache contacts in Redis (avoid blocking Google Sheets)

**Files:** `webhook/dispatch.py:_get_contacts_sync`, `execution/integrations/sheets_client.py`
**Gap:** every broadcast triggers a fresh Sheets API call. Thread pool could fill under load.
**Suggested remediation:** Redis cache with TTL=300s; invalidate on `/add` command.
**Effort:** ~1 hour.

---

## Future milestones (loose candidates)

Candidates for a future hardening or UX milestone, not currently planned:

- **CI gate (C.2)** — highest value of this list; prevents merging untested code.
- **Mini App auth (C.3)** — blocks abuse vectors before Mini App sees more traffic.
- **Contact cache (C.5)** — low-effort latency win on broadcast.
- **`IRONMARKET_API_KEY` rotation (C.1)** — only if repo visibility ever changes.
- **Log aggregator** (Loki / Better Stack) — extends `event_log` Postgres approach when scale warrants it.

---

## Visual dashboards — Grafana Cloud (deferred)

### Status

- `/metrics` endpoint is live at `https://web-production-0d909.up.railway.app/metrics` and exposes Phase 3 counters (`whatsapp_messages_total`, `telegram_edit_failures_total`, `progress_card_edits_total`) + histograms + default Python process metrics.
- No external dashboard is wired yet. Monitoring today is: read `/metrics` with curl or browser; query `event_log` in Supabase for timeline; Sentry for exceptions.
- Grafana Cloud setup was explored during wrap-up on 2026-04-19 and deferred in favor of shipping the core milestone.

### Why it's worth doing later

- Counter values are meaningless without trend lines. A gauge alone doesn't tell you if the WhatsApp failure rate is abnormal — you need "X per minute over the last 24h" compared to baseline.
- `rate(whatsapp_messages_total{status="duplicate"}[1h])` + a simple threshold alert catches retry-storm or idempotency misconfiguration before users notice.
- Ties together metrics + logs + traces visually; reduces MTTR on production incidents.

### Setup options (in order of effort)

**Option A — Grafana Cloud "Hosted scrape job" (preferred if supported in free tier UI):**
1. Create account at https://grafana.com/auth/sign-up/create-user (free tier: 10k series, 14d retention).
2. Stack region: `sa-east-1` (São Paulo) for lower Railway↔Grafana latency.
3. In Grafana Cloud UI: **Connections → Add new connection → Prometheus → Hosted scrape**.
4. Scrape target: `https://web-production-0d909.up.railway.app/metrics`, interval `30s`.
5. Build 4 starter panels:
   - `rate(whatsapp_messages_total{status="success"}[5m])` — success rate
   - `rate(whatsapp_messages_total{status="duplicate"}[1h])` — dup rate (alert if > 0.5/min sustained)
   - `sum by (reason) (rate(telegram_edit_failures_total[5m]))` — edit failures by reason
   - `histogram_quantile(0.95, sum by (le) (rate(whatsapp_duration_seconds_bucket[5m])))` — p95 latency

Effort: ~20 minutes if the hosted-scrape option is present in the free-tier UI.

**Option B — Grafana Alloy as a Railway service:**

If the hosted-scrape option is missing in free tier, run Grafana Alloy (the new unified agent that replaced Grafana Agent) as a small additional Railway service in the same project. Alloy scrapes `/metrics` on an interval and pushes via `prometheus.remote_write` to Grafana Cloud.

Minimal `config.alloy`:
```alloy
prometheus.scrape "webhook" {
  targets = [{
    __address__ = "web-production-0d909.up.railway.app",
    __scheme__  = "https",
  }]
  metrics_path = "/metrics"
  forward_to   = [prometheus.remote_write.grafana_cloud.receiver]
  scrape_interval = "30s"
}

prometheus.remote_write "grafana_cloud" {
  endpoint {
    url = "https://prometheus-prod-XX-prod-sa-east-1.grafana.net/api/prom/push"
    basic_auth {
      username = sys.env("GRAFANA_USER_ID")
      password = sys.env("GRAFANA_API_TOKEN")
    }
  }
}
```

Railway secrets: `GRAFANA_USER_ID`, `GRAFANA_API_TOKEN`. Docker image: `grafana/alloy:latest`.

Effort: ~30-45 minutes including the Railway service setup and Alloy config testing.

**Option C — Railway native observability integration (if available):**

Some Railway plans expose a one-click "export metrics to Grafana Cloud" button in **Settings → Observability**. Worth checking the current Railway UI before committing to Option B — it eliminates the need for a separate agent service.

### Prerequisite before shipping

- `/metrics` today is **unauthenticated**. If Grafana Cloud scrapes it, the URL is only exposed to the Grafana egress IPs, but the endpoint itself remains globally scrape-able. Before making the URL more public, add simple auth (a shared-secret header token or IP allowlist) — see CONCERNS §Security section for scope; low urgency today.

### Decision to defer

- Current observability (Sentry + `/metrics` curl + `event_log` SQL + live Telegram card) covers the operational cases needed today. Grafana would upgrade "I can see it" to "I can trend it and alert on it" — real value once traffic scales beyond what one operator can eyeball in Sentry + Supabase daily.
- Tracked here so we pick it up when either (a) traffic/incidents justify the setup, or (b) we want a visible story for stakeholders.

---

## Log

- 2026-04-19 — File created after Backend Hardening v1 merged to main (`d2e8d3a`). All 3 phases shipped. Sentry smoke test passed. `event_log` migration applied. RLS follow-up migration pending.
- 2026-04-19 — RLS migration applied to dev + prod (`19cb726`). `aiogram` + `aiohttp` added to root `requirements.txt` (`3a0e8c3`) to unblock cron scripts. Cron workflow YAMLs updated to pass `SENTRY_DSN`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (`4354d09`). First real `platts_ingestion` run wrote 4 rows to `event_log` and produced live Telegram card — all 4 observability layers verified working end-to-end in production.
- 2026-04-19 — Added "Visual dashboards — Grafana Cloud" section documenting deferred dashboard setup with three ingress paths (hosted scrape, Alloy agent, Railway native) and decision log.
