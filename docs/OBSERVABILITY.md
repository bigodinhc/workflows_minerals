# Observability & Operations Runbook

How to set up, verify, and monitor the observability stack added in **Backend Hardening v1** (Phase 3, 2026-04-18). Four layers:

| Layer | What it shows | Where to look |
|---|---|---|
| **Sentry** | Exceptions + breadcrumbs (last ~100 logs before the error) | sentry.io dashboard → Issues |
| **Prometheus `/metrics`** | Aggregated counters (sends, errors, edits) + latency histograms | `GET /metrics` on the webhook service |
| **Postgres `event_log`** | Timeline of every workflow step (not just errors) | Supabase SQL editor |
| **Telegram live progress cards** | What the bot is doing *right now* during long runs | The configured `TELEGRAM_CHAT_ID` |

---

## Setup (one-time)

### 1. Sentry

1. Create a free Sentry account at https://sentry.io/signup/.
2. Create an organization + a project:
   - Platform: **Python**
   - Framework: **aiohttp** (if offered; otherwise plain Python)
   - Project name: e.g., `antigravity-webhook`
3. Copy the **DSN** (Settings → Projects → your project → Client Keys).
4. Set `SENTRY_DSN` in:
   - `.env` (local dev)
   - Railway → Variables, for each service (webhook + any cron service)
5. Verify with the smoke test below.

If `SENTRY_DSN` is empty or missing, Sentry is a no-op (safe — logs a warning at startup).

### 2. Postgres `event_log` table (Supabase)

Apply these migrations in order, via Supabase SQL editor:

1. `supabase/migrations/20260418_event_log.sql` — table + indexes. **✅ applied 2026-04-19.**
2. `supabase/migrations/20260419_event_log_rls.sql` — enable RLS (deny-all for anon; service-role continues to write). **Pending apply.**

See `supabase/migrations/README.md` for the tracking table.

### 3. Prometheus metrics

No separate setup — the `/metrics` endpoint is exposed by the webhook as soon as it runs. Railway can scrape it if you configure a monitoring integration (optional).

### 4. Telegram live progress cards

Automatic — requires no setup beyond `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` being configured (already the case). Phase 3 instrumented `platts_ingestion`, `platts_reports`, `baltic_ingestion`, and the broadcast flow uses the existing `ProgressReporter`.

---

## Verification checklist — "is everything working?"

Run these five checks, in order. Any failure → the layer isn't wired correctly.

### Check 1 — Sentry captures exceptions

**Local:**
```bash
.venv/bin/python3 <<'EOF'
import os
from pathlib import Path
for line in Path(".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"'))

import sentry_sdk
sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], environment="manual-smoke-test")
sentry_sdk.capture_message("manual smoke test", level="info")
try:
    raise RuntimeError("manual smoke test exception — ignore")
except RuntimeError:
    sentry_sdk.capture_exception()
sentry_sdk.flush(timeout=5)
print("sent")
EOF
```

**Expected:** "sent", no errors. Open https://sentry.io → your project → Issues. In ~30s you should see 2 entries tagged `environment: manual-smoke-test`.

**In production:** trigger any real exception in the webhook (e.g., call a malformed callback). The Sentry dashboard should show it within 1 minute tagged with `environment: prod` or whatever `RAILWAY_ENVIRONMENT` is set to.

### Check 2 — `/metrics` endpoint returns counters

**Local (webhook running):**
```bash
curl -s http://localhost:8080/metrics | grep -E "whatsapp_messages_total|telegram_edit_failures_total|progress_card_edits_total"
```

**Production:**
```bash
curl -s https://<your-railway-url>/metrics | grep whatsapp_messages
```

**Expected:** lines like:
```
# HELP whatsapp_messages_total WhatsApp send outcomes
# TYPE whatsapp_messages_total counter
whatsapp_messages_total{status="success"} 127.0
whatsapp_messages_total{status="duplicate"} 3.0
```

Counters start at 0 and increase as real traffic flows. Right after deploy they'll be zero — that's fine. Send one broadcast and the `status="success"` counter should go up.

### Check 3 — `event_log` has rows after a run

After triggering any workflow (e.g., approve a broadcast), open Supabase SQL editor:

```sql
-- Most recent events, across all workflows:
select workflow, label, detail, level, created_at
from event_log
order by created_at desc
limit 20;

-- Full timeline of a specific broadcast:
select label, detail, level, created_at
from event_log
where draft_id = '<paste a real draft id here>'
order by created_at;

-- Counts per workflow in the last 24h:
select workflow, count(*) as events, max(created_at) as last_seen
from event_log
where created_at > now() - interval '24 hours'
group by workflow
order by last_seen desc;
```

**Expected:** rows show up within seconds of a real run. If the table is empty after a broadcast, one of:
- RLS migration applied but the Supabase client isn't using the service role key → rows are written but invisible to your query (use service-role in SQL editor).
- `ProgressReporter` wasn't given a `supabase_client` → silent no-op in `_persist_event_log` (check logs for `event_log_insert_failed`).

### Check 4 — Live progress card in Telegram

Trigger one of the instrumented crons manually:

```bash
.venv/bin/python -m execution.scripts.platts_ingestion
```

Watch the `TELEGRAM_CHAT_ID` chat. You should see a card like:

```
📡 platts_ingestion
━━━━━━━━━━━━━━━━━━━━━━
✅ Actor started — platts-scrap-full-news triggered
✅ Dataset fetched — 34 articles after flatten
✅ Dedup applied — 22 new, 12 duplicates
⏳ Staged in Redis — 22 items
```

The card edits itself every ~2 seconds (debounced). If no card appears: check bot token is valid, `TELEGRAM_CHAT_ID` is correct, and the script reached `reporter._message_id = initial.message_id`.

### Check 5 — Idempotency blocks duplicates

Approve the same draft twice within 24h. On the second approve, check:

- Telegram bot chat: the progress card should still complete (duplicates count as "success").
- Redis: `redis-cli KEYS 'whatsapp:sent:*'` should show keys; each value is "1" with ~24h TTL.
- `event_log`: SQL `select * from event_log where label ilike '%idempotency%'` should show rows with `whatsapp_idempotency_hit` in the label or context.
- No duplicate messages received on WhatsApp.

---

## Where the logs go

| Stream | Destination | How to read |
|---|---|---|
| Python `logging.*` (stdout) | Railway logs (ephemeral, retained per Railway plan) | Railway dashboard → service → Logs tab |
| Sentry breadcrumbs | Attached to every exception event | sentry.io → Issue details → Breadcrumbs tab |
| `WorkflowLogger` structured | Same stdout + (if wired) Sentry | Same as above |
| `event_log` | Supabase Postgres | SQL editor, queries above |

**For a specific production incident, the fastest path is usually:**

1. Sentry → grab the exception timestamp and breadcrumb trail.
2. `event_log` SQL → filter by `draft_id` or `run_id` from the breadcrumb to see the fuller workflow timeline.
3. Railway logs → search by timestamp if you need the raw stdout (worker thread output, print statements, etc.).

---

## Common questions

**"Why is Sentry showing nothing?"**
- `SENTRY_DSN` not set in that environment. Check `echo $SENTRY_DSN` on Railway shell or look for `SENTRY_DSN not set — Sentry disabled` in startup logs.
- Exception is being caught and swallowed before Sentry sees it. Phase 3 Task 6 eliminated the known `except Exception: pass` sites, but custom handlers elsewhere may still swallow. Grep for `except Exception` in the offending module.

**"`/metrics` returns 404."**
- The webhook service didn't pick up the new route. Redeploy. Verify `routes/api.py` has the `@routes.get("/metrics")` handler.

**"`event_log` is empty."**
- The RLS migration wasn't applied and your SQL editor session isn't service-role (rare). Run the `20260419_event_log_rls.sql` file.
- `ProgressReporter` wasn't given a `supabase_client`. Check the cron script's `_run_with_progress` for `supabase_client=sb`.
- Supabase insert failing silently. Grep logs for `event_log_insert_failed`.

**"Live progress card never appears during a cron run."**
- The script's `_run_with_progress` function isn't being called. Check `main()` for `asyncio.run(_run_with_progress(...))`.
- Initial `bot.send_message(chat_id, "📡 ...")` failed. Check `TELEGRAM_BOT_TOKEN` is valid.
- The chat_id is wrong. Double-check `TELEGRAM_CHAT_ID` env var.

**"Bot ran but Prometheus counter didn't move."**
- Counter increment is inside an error path that didn't fire (normal — counters are 0 until their event happens).
- The code path touching that counter wasn't hit. Check the specific counter's instrumentation site.

---

## Maintenance

**Monthly:**
- Check Sentry quota usage (Settings → Usage). Free tier is 5k events/month; upgrade or tighten `traces_sample_rate` if exceeded.
- Run `select count(*), min(created_at) from event_log;` — if the table is growing fast, consider a retention policy (e.g., `delete from event_log where created_at < now() - interval '90 days'`).

**On new exception types:**
- Sentry groups similar stack traces automatically; triage via the Issues dashboard.
- Critical issues should link to a GitHub issue or fix PR; resolve in Sentry once merged.

**On new instrumented workflow:**
- Follow the `_run_with_progress` pattern from `platts_ingestion.py` (commit `6f474a0`) or `platts_reports.py` (`225fb0a`). Call `reporter.step(label, detail)` at phase boundaries. No schema change needed — event_log accepts any `workflow` string.

---

## Reference

- Spec: `docs/superpowers/specs/2026-04-18-backend-hardening-v1-design.md`
- Phase 3 plan: `docs/superpowers/plans/2026-04-18-phase3-observability.md`
- Follow-ups: `docs/superpowers/followups/2026-04-19-backend-hardening-v1-followups.md`
- Migrations: `supabase/migrations/`
