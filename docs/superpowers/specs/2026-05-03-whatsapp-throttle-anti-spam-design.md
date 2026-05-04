# WhatsApp Throttle + Anti-Spam Hardening — Design

**Date:** 2026-05-03
**Status:** Draft
**Authors:** bigode + Claude

## Context

The WhatsApp Business number used by all broadcasts (uazapi-backed, QR-linked, NOT the official WhatsApp Cloud API) was flagged as spam by WhatsApp **twice in 48 hours**. Account is in heightened-monitoring state — a third flag may escalate to temporary or permanent ban.

### Current pipeline (the cause)

Two send paths, both burst-mode, no throttle anywhere:

1. **Text broadcasts** (`execution/core/delivery_reporter.py:374-441`) — sequential `for contact in contacts` loop calling `self.send_fn(contact.phone, message)`. No `time.sleep` between iterations. Effective throughput: ~1-3 msg/s. Used by `morning_check.py`, `baltic_ingestion.py`, `send_daily_report.py`, `send_news.py`, and `webhook/dispatch.py` (bot-triggered draft broadcasts).
2. **PDF broadcasts** (`webhook/dispatch_document.py:27,147`) — `CONCURRENCY = 5` semaphore over `asyncio.gather`. Up to 5 PDFs in flight simultaneously. Used by OneDrive multi-approver flow when an approver clicks Approve.

Compounding factors:
- Every broadcast sends **byte-identical content** to all 105 recipients on the `minerals_report` list.
- 429 responses are classified by `delivery_reporter.classify_error` but the dispatch loop ignores the category — no backoff.
- `@retry_with_backoff(max_attempts=3, base_delay=2.0)` in `uazapi_client.py:18` may compound the spam signal under transient errors.

### Research findings (summary)

Validated against practitioner sources (Prime Sender 50k+ users, WASenderAPI, Whapi, Evolution API community, WhatsApp Business AWS docs):

- **Burst sending of identical messages × 100+ recipients is the primary spam trigger.** Identical messages flag at ~20-30 sends; varied content allows 500+/day on mature accounts.
- **Documented "low-risk" delay range: 30-60s with random jitter.** 20s = "medium risk"; 5s = "very high risk".
- **QR-linked unofficial APIs (uazapi/Evolution) have lower spam thresholds** than official Cloud API.
- **PDFs are not officially weighted heavier**, but their identity (same binary to all) cannot be naturally varied — making them a stronger "bulk identical" signal than text.
- **Recovery from a flag is operational, not technical** — reduce volume 80-90% for 48-72h, then ramp gradually.

Operator chose **15-30s jitter** (more aggressive than research recommends) to keep broadcasts within current cron windows. Residual risk acknowledged; mitigated by the variation techniques in this spec.

## Goals

- Add deterministic throttle to all WhatsApp send paths so no broadcast bursts faster than ~1 msg per 15-30s.
- Break byte-identity across the 105 recipients of any single broadcast (per-message reference token).
- Serialize PDF broadcasts (concurrency 1) and add an opt-in flag to deliver PDFs as Supabase Storage signed URLs instead of binary attachments.
- Respect 429 / rate-limit responses with a backoff before continuing the broadcast.
- Bump baltic-ingestion in-flight lock TTL so the slower broadcast does not race a re-trigger.

## Non-Goals

- **Per-recipient name personalization.** Operator explicitly declined. The variation here is content-only (random ref token), not contact-data-driven.
- **Header rotation across messages.** Operator explicitly declined. Variation comes solely from the ref token.
- **Operational recovery from the current flag.** Outside code scope. Operator decides the 48-72h pause/ramp manually.
- **Migration to the official WhatsApp Cloud API.** Out of scope for this iteration.
- **Per-contact engagement scoring / list pruning.** Future work if quality rating does not recover.
- **Apply throttle to one-to-one bot interactions** (status replies, FSM prompts, single-recipient sends from `webhook/bot/*`). Scope is broadcasts only.

## Design Overview

```
┌─────────────────────────────────────────────────────────────┐
│ Caller (morning_check / baltic / send_news / dispatch.py)   │
│   reporter.dispatch(contacts, body)                         │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ DeliveryReporter.dispatch  (execution/core/delivery_reporter)│
│  for contact in contacts:                                    │
│    msg = body + "\n\nRef: <6-char token>"                   │
│    try send_fn(contact.phone, msg)                          │
│    on RATE_LIMIT → sleep(BROADCAST_RATE_LIMIT_SLEEP, 60s)   │
│    if not last → sleep(uniform(DELAY_MIN, DELAY_MAX))       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ webhook/dispatch_document.py                                 │
│  CONCURRENCY = 1 (was 5)                                    │
│  if PDF_DELIVERY_MODE == "link":                            │
│    text_msg = "📄 {filename}\n{signed_url}\n\nRef: …"       │
│    uazapi.send_message(contact.phone, text_msg)             │
│  else:                                                       │
│    uazapi.send_document(...)  ← current path, default       │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. `execution/core/delivery_reporter.py` — throttle, variation, 429 backoff

#### 1a. Per-contact reference token (always on)

After every send, append a footer to `message`:

```
\n\nRef: <6-char token>
```

Token generated per contact via `secrets.token_urlsafe(6).upper()[:6]` (~62^6 ≈ 56 billion possibilities — collision irrelevant, the goal is byte-uniqueness across the 105 messages of a single broadcast). Disabling: `BROADCAST_REF_TOKEN_ENABLED=false`.

#### 1b. Throttle between sends

After every send (success OR failure), and before the next iteration, sleep:

```python
if i < total - 1:  # not last contact
    delay = random.uniform(BROADCAST_DELAY_MIN, BROADCAST_DELAY_MAX)
    time.sleep(delay)
```

Defaults: `BROADCAST_DELAY_MIN=15.0`, `BROADCAST_DELAY_MAX=30.0`. Operator can tune via env without redeploy.

The circuit-breaker skip path (early `continue` for skipped contacts) does NOT incur the delay — no actual API call was made.

#### 1c. Rate-limit (429) backoff

When `category == SendErrorCategory.RATE_LIMIT`, before the regular inter-message delay, sleep an additional `BROADCAST_RATE_LIMIT_SLEEP` seconds (default 60.0). This precedes (not replaces) the normal jitter delay.

Rationale: the spam classifier treats "ignored 429 → kept sending" as evidence of bot behavior. Honoring the signal is itself a positive trust signal.

#### 1d. Emit throttle metadata to EventBus

`_emit_delivery_summary_event` already emits `delivery_summary` with success/failure counts. Extend the `detail` dict to include:

```python
detail={
    ...,
    "delay_min": BROADCAST_DELAY_MIN,
    "delay_max": BROADCAST_DELAY_MAX,
    "duration_seconds": int((finished - started).total_seconds()),
}
```

So the events channel card shows wall-clock vs configured throttle, helping the operator tune.

### 2. `webhook/dispatch_document.py` — serialize + link mode

#### 2a. `CONCURRENCY = 1`

Change `CONCURRENCY = 5` (line 27) → `CONCURRENCY = 1`. The `asyncio.Semaphore(1)` keeps the existing async structure but serializes execution. No other changes needed in the `_send_one` flow.

Add per-iteration delay matching the text-broadcast jitter:

```python
async def _send_one(contact, idx, total):
    async with sem:
        # ... existing send ...
        if idx < total - 1:
            await asyncio.sleep(random.uniform(BROADCAST_DELAY_MIN, BROADCAST_DELAY_MAX))
```

#### 2b. PDF delivery mode flag

New env var `PDF_DELIVERY_MODE`:
- `"attachment"` (default): current behavior — `uazapi.send_document` with base64 PDF.
- `"link"`: generate a Supabase Storage signed URL with TTL 7 days, send as a text message:

```
📄 {filename}

{signed_url}

(Link válido por 7 dias)

Ref: <token>
```

Implementation: refactor `_download_pdf` (currently `dispatch_document.py:116-119`) to return raw bytes instead of base64; build base64 lazily only when `PDF_DELIVERY_MODE=attachment`. In `link` mode, upload the raw bytes to Supabase Storage bucket `pdf-broadcasts` with key `{approval_id}/{filename}`, generate signed URL with `expiresIn=604800` (7 days), embed in the text message.

The Supabase Storage bucket `pdf-broadcasts` must be created (private, no public read; signed URLs only) as part of implementation. The Apify actor's `platts-reports` bucket (`actors/platts-scrap-reports/src/persist/supabaseUpload.js:5`) confirms `supabase-py`/`@supabase/supabase-js` Storage usage in this project — reuse the same client pattern.

The Storage upload is idempotent per `(approval_id, filename)` — repeat calls overwrite. Cleanup happens via Storage TTL or a future cron (out of scope).

When operator wants to test: set `PDF_DELIVERY_MODE=link` in Railway env, trigger one OneDrive approval pointing at a single test contact (manually adjust the recipients list or test via the `is_onedrive_approver` capability in dev). After verifying delivery, decide whether to keep the flag at `link` for production.

#### 2c. Signed URL token

The signed URL itself contains a per-request token from Supabase, so each recipient's link is naturally byte-unique even for the same underlying PDF. No additional ref token needed in link mode.

### 3. `execution/scripts/baltic_ingestion.py` — extend lock TTL

Bump `_INFLIGHT_LOCK_TTL_SEC = 20 * 60` (line 44) → `60 * 60`. Rationale: with 105 contacts at 15-30s avg = ~39 min broadcast (vs ~17 min today), the existing 20-min TTL leaves only ~3 min of margin and risks expiring mid-broadcast — a re-triggered cron would see no lock and start a parallel broadcast.

`morning_check.py:75` defines the same constant `_INFLIGHT_LOCK_TTL_SEC = 20 * 60` for its own split-lock — bump to `60 * 60` in lockstep.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `BROADCAST_DELAY_MIN` | `15.0` | Min seconds between sends |
| `BROADCAST_DELAY_MAX` | `30.0` | Max seconds between sends (uniform jitter range) |
| `BROADCAST_RATE_LIMIT_SLEEP` | `60.0` | Extra sleep when 429 detected |
| `BROADCAST_REF_TOKEN_ENABLED` | `true` | Append per-message Ref: token |
| `PDF_DELIVERY_MODE` | `attachment` | `attachment` (current) or `link` (Supabase signed URL) |

All five must be added to `.env.example` with explanatory comments.

## Caller updates

No source-code changes required in any caller (`morning_check.py`, `baltic_ingestion.py`, `send_daily_report.py`, `send_news.py`, `webhook/dispatch.py`). Throttle, ref token, and 429 backoff are applied transparently inside `DeliveryReporter.dispatch()` — existing callers benefit automatically once the reporter is updated.

## Operational notes (NOT code)

The spec does not implement recovery from the current flag. The operator should:

1. **Pause manual broadcasts for 48-72h.** Daily crons (`morning_check`, `baltic_ingestion`, `send_daily_report`) can keep running once this spec ships — they will be slower and varied. `send_news` and OneDrive PDF approvals should stay paused.
2. **Check WhatsApp Business → Quality Rating.** Note the current state (Green/Yellow/Red).
3. **After 48-72h with the throttle deployed**, resume `send_news` and PDF flows gradually. Watch for any new flag.
4. If flagged a third time within 7 days of deploying this spec → reduce `BROADCAST_DELAY_MIN/MAX` upward (e.g., 30/60), reduce list to engaged contacts only, and reconsider name personalization.

## Testing

### Unit tests (pytest, fakeredis)

- `tests/test_delivery_reporter_throttle.py`:
  - delay applied between sends (mock `time.sleep`, assert call args in `[MIN, MAX]` range)
  - no delay after last contact
  - no delay for circuit-broken-skipped contacts
  - 429 path triggers extra sleep before regular delay
  - ref token appended when enabled, omitted when `BROADCAST_REF_TOKEN_ENABLED=false`
  - tokens differ across contacts in same dispatch (byte-uniqueness)
- `tests/test_dispatch_document_serialized.py`:
  - `CONCURRENCY=1` enforced (assert no overlap in async timeline)
  - `PDF_DELIVERY_MODE=attachment` calls `send_document`
  - `PDF_DELIVERY_MODE=link` uploads to Storage, generates signed URL, calls `send_message` with URL embedded

### Integration / manual smoke tests

- Run `morning_check.py --dry-run`; inspect stdout DELIVERY_REPORT and verify each contact's outgoing message has a unique `Ref:` token.
- Run `baltic_ingestion.py` end-to-end against a 3-contact test list (operator-curated). Verify wall-clock duration ≈ `2 × ((MIN + MAX) / 2)` = ~45s.
- For PDF link mode: set `PDF_DELIVERY_MODE=link` on Railway, trigger an OneDrive approval directed at one test contact, verify the link works and the PDF downloads.

### Regression coverage to preserve

- `tests/test_delivery_reporter.py` — keep; ensure throttle additions don't break circuit breaker logic.
- `tests/test_baltic_ingestion_idempotency.py` — verify the lock TTL bump doesn't break the split-lock scenarios (the timeout-related ones may need adjustment).
- `tests/test_morning_check_idempotency.py` — same.
- `tests/test_dispatch_idempotency.py` — keep; the per-contact dedup logic is unchanged.

## Risks & residual concerns

| Risk | Mitigation |
|---|---|
| 15-30s jitter is in the "medium risk" zone per research | Per-contact ref token removes byte-identity (defeats hash-classifier). Operator can bump delay to 30-60s via env if quality rating does not recover. |
| Per-contact ref token defeats hash-classifier but a semantic-similarity classifier may still cluster the 105 messages as "same content" | Accepted residual risk. If a third spam flag occurs after deploy, escalate by adopting name personalization or pruning the list to engaged contacts. |
| `time.sleep` blocks the async dispatch in `webhook/dispatch.py` (sync `reporter.dispatch` runs inside `asyncio.to_thread`) | Already runs in a thread (`webhook/dispatch.py:212`). Sleep is fine there. |
| Supabase Storage signed URL generation can fail (network) | If `PDF_DELIVERY_MODE=link` and the upload/sign fails: log error, fall through to `send_document` attachment (best-effort fallback). Avoids losing the broadcast on a Storage hiccup. |
| `BROADCAST_DELAY_MIN/MAX` misconfigured (e.g., MIN > MAX, or negative) | Validate at startup: clamp `MIN = max(0, MIN)`, `MAX = max(MIN, MAX)`. Log warning if clamped. |
| Existing scripts that call `reporter.dispatch` without keyword args | Signature change is purely additive (new optional param). Backward compatible. |
| Lock TTL bump from 20→60min interacts with `try_claim_alert_key` retries | Verify `state_store.try_claim_alert_key` honors the new TTL; it should — TTL is set on claim, not on read. Cover with idempotency test. |
| Per-iteration `time.sleep(15-30s)` × 105 contacts = 26-52 min broadcast wall-clock — must fit cron windows | `morning_check`: 08:30-10:00 (90 min) → fits. `baltic_ingestion`: 09:00-11:45 (165 min) → fits. `daily_report`: single-shot, no window concern. |

## Out of scope (for this spec)

- Per-recipient name personalization (declined by operator).
- Operational recovery from the existing flag (manual decision).
- Replacing the existing uazapi number with a fresh one + warm-up protocol (only if account gets fully banned).
- Reply-rate / engagement tracking (future quality observability).
- Migration to WhatsApp Cloud API (separate project).
- Quality-rating polling automation (future watchdog enhancement).

## Open questions / followups

- Should `dispatch_document.py` link-mode also append a ref token? Spec says no — the signed URL token already provides byte-uniqueness.
- Should we add a Prometheus counter `whatsapp_throttle_sleep_seconds_total` for observability? Defer to a follow-up.

---

*Spec authored 2026-05-03. Ready for implementation planning via `superpowers:writing-plans`.*
