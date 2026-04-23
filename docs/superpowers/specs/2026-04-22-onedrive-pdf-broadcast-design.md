# OneDrive PDF → WhatsApp Broadcast — Design Spec

**Date:** 2026-04-22
**Status:** Approved — ready for implementation planning
**Owner:** bigodinhc
**Related:**
- `docs/superpowers/specs/2026-04-22-contacts-supabase-migration-design.md` (contacts table this spec extends)
- `docs/superpowers/specs/2026-04-21-observability-unified-design.md` (EventBus / trace_id spine)
- Legacy `[WF] RELATORIO DIARIO.json` (existing n8n implementation being replaced)

## Goal

Replace the existing n8n "RELATORIO DIARIO" workflow with a webhook-driven, human-in-the-loop pipeline that detects new PDFs in a SharePoint folder and, after admin approval via Telegram, broadcasts each PDF to a chosen WhatsApp contact list (or to every active contact).

## Motivation

Today a polling n8n workflow watches `/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals/` and auto-forwards any new PDF to a Google-Sheets-filtered list based on a filename regex (`contains("Solid")` routes one way, everything else another). This has four problems:

1. **Auto-send without approval** — no gate between "PDF arrived" and "45 people get notified on WhatsApp."
2. **Filename-coupled routing** — the routing logic (which list receives which PDF) is encoded as regex in workflow JSON; new PDF types mean editing n8n.
3. **Google Sheets as contacts source** — diverges from the Supabase `contacts` table that now owns the broadcast list (per contacts-supabase-migration spec).
4. **No observability spine** — n8n logs don't reach `event_log`, so crashes don't trigger the shared Telegram alerts and runs have no `trace_id` for correlation.

The replacement moves source-of-truth to Supabase, inserts a Telegram approval gate, shifts routing decisions to the human ("pick the list at approval time"), and wires everything into the existing `EventBus` so the run is visible in the events channel and queryable via `trace_id` in `event_log`.

## Scope

**In scope (v1):**
1. **Microsoft Graph change-notification webhook** (real-time push, not polling).
2. **One watched folder:** `/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals/` on the SIGCM SharePoint site.
3. **Two initial PDFs supported** (no-op — any PDF is handled identically): `Minerals_Report_data.pdf`, `Solid_Fuels_Overview_data.pdf`.
4. **Telegram approval flow** with inline keyboard: one button per `contact_lists` row + "Todos" (all active contacts) + "Descartar". Single-select, with a confirmation step before dispatch.
5. **Two new Supabase tables:** `contact_lists` (catalog) and `contact_list_members` (junction to `contacts`). SQL-only management — no bot/dashboard UI.
6. **Uazapi `/send/media` integration:** new `UazapiClient.send_document()` method that forwards the Graph `@microsoft.graph.downloadUrl` directly (no local download, no Supabase Storage usage).
7. **Subscription renewal cron** (GH Actions, every 12h) since Graph subscriptions expire every ~3 days.
8. **EventBus integration** for webhook handling, dispatch, and renewal cron — all three share `trace_id` per originating PDF.
9. **Watchdog coverage** for `onedrive_resubscribe` by adding to `ALL_WORKFLOWS`.

**Out of scope (explicit non-goals):**
- Bot UI for list management (add/remove/rename lists, edit membership). Seeded via SQL migration; membership changes are manual SQL/Supabase dashboard operations. Deferred to Phase 2.
- Dashboard UI for lists or pending approvals. Deferred to Phase 2.
- Multi-select (sending one PDF to multiple lists in one approval). User can re-approve the same PDF from history if needed.
- Auto-routing based on filename patterns (replaced by human selection).
- First-page thumbnail preview in the approval card. Deferred to Phase 2 if requested.
- Fallback poll cron for "webhook dropped during Railway downtime." Accepted risk for v1 (Railway rarely goes down, admin will notice if a PDF doesn't arrive on WhatsApp).
- Multi-folder watchers. Single folder only, but `GRAPH_FOLDER_PATH` is env-configurable so moving/adding folders is an env-change + resubscribe, not a code change.
- Delivery receipts (did each contact actually receive/open the PDF?). Uazapi exposes this but surfacing it is Phase 2.
- PDF content inspection or AI-driven caption generation (pattern from `baltic_ingestion.py`). **v1 sends the PDF with no caption (empty `text` field).** Per-list default captions are Phase 2.
- Retry/resend UI for failed individual sends within a dispatch. `DeliveryReporter` logs failures; re-sending to specific contacts is a Phase 2 bot command.

## Architecture

### High-level

```
SharePoint (SIGCM)
      │ PDF added
      ▼
Microsoft Graph (change notification)
      │ POST /onedrive/notify
      ▼
webhook/routes/onedrive.py          ← Railway aiohttp route
      │ clientState check + 202 Accepted
      ▼
webhook/onedrive_pipeline.py        ← detect, dedup, create approval
      │
      ▼
Telegram card (admin DM)            ← ProgressReporter-style inline updates
      │ click lista / Todos / Descartar
      ▼
webhook/dispatch_document.py        ← fan-out via UazapiClient.send_document
      │
      ▼
WhatsApp (via Uazapi /send/media)

parallel track:
GH Actions (cron every 12h)
      │
      ▼
execution/scripts/onedrive_resubscribe.py
      │
      ▼
Microsoft Graph (PATCH /subscriptions/{id})
```

### Component boundaries

| Component | File | Responsibility |
|---|---|---|
| Graph API client | `execution/integrations/graph_client.py` (new) | OAuth2 client-credentials, token cache, subscription CRUD, `get_folder_delta`, `get_item` |
| Webhook endpoint | `webhook/routes/onedrive.py` (new) | HTTP 202 within 10s, validationToken handshake, clientState auth, async spawn |
| Detection pipeline | `webhook/onedrive_pipeline.py` (new) | Delta query, file filter, Redis dedup, approval state creation, Telegram card send |
| Approval callbacks | `webhook/bot/routers/callbacks_onedrive.py` (new) | `on_approve`, `on_confirm`, `on_discard` — in-place card edits + state transitions |
| Callback data classes | `webhook/bot/callback_data.py` (altered) | `OneDriveApprove`, `OneDriveConfirm`, `OneDriveDiscard` |
| Dispatch | `webhook/dispatch_document.py` (new) | Fan-out with concurrency=5, idempotency keys, ProgressReporter live updates |
| Uazapi extension | `execution/integrations/uazapi_client.py` (altered) | `send_document(number, file_url, doc_name, caption)` |
| ContactsRepo extension | `execution/integrations/contacts_repo.py` (altered) | `list_lists()`, `list_by_list_code(code)`, `ContactList` dataclass |
| Subscription renewal | `execution/scripts/onedrive_resubscribe.py` (new) | Cron wrapped in `@with_event_bus`, lists & renews Graph subscriptions |
| GH Actions workflow | `.github/workflows/onedrive_resubscribe.yml` (new) | `cron: 0 */12 * * *` |
| Supabase migration | `supabase/migrations/20260422_contact_lists.sql` (new) | Creates `contact_lists` + `contact_list_members`, RLS deny-all, seeds initial three lists |
| Watchdog registration | `webhook/status_builder.py` (altered) | Add `onedrive_resubscribe` to `ALL_WORKFLOWS` |

## Data Flow (detailed)

### Fase 1 — Initial setup (run once)

1. Operator runs `python execution/scripts/onedrive_resubscribe.py` locally or triggers the GH Actions workflow manually.
2. Script calls `GraphClient.create_subscription` with:
   - `resource`: `/drives/{GRAPH_DRIVE_ID}/root:{GRAPH_FOLDER_PATH}`
   - `notificationUrl`: `{ONEDRIVE_WEBHOOK_URL}`
   - `clientState`: `{GRAPH_WEBHOOK_CLIENT_STATE}` (32-char secret)
   - `expirationDateTime`: `now + 3 days`
   - `changeType`: `updated`
3. Microsoft sends POST `/onedrive/notify?validationToken=<token>`; Railway responds with the token plaintext within 10 s.
4. Subscription ID is cached in Redis at `onedrive:subscription_id` (no TTL).

### Fase 2 — PDF detection and approval

Events with approximate wall-clock timings:

| T (s) | Actor | Action |
|---|---|---|
| 0.0 | User | Drops `Minerals_Report_042226.pdf` into SIGCM folder |
| 2–5 | Microsoft Graph | POST `/onedrive/notify` with `{value: [{resource, clientState, ...}]}` |
| 2.1 | `routes/onedrive.py` | Validates `clientState`, returns 202 Accepted, spawns `asyncio.create_task(process_notification(payload))` |
| 2.2 | `onedrive_pipeline.py` | Creates EventBus (`workflow="onedrive_webhook"`, new `trace_id`), emits `webhook_received` |
| 2.5 | `GraphClient.get_folder_delta` | Delta query (uses cached delta_token at `onedrive:delta_token:sigcm`) |
| 2.8 | Pipeline | Filters: `.file` present, not `.folder`, MIME or extension = PDF, `drive_item_id` not in Redis `seen:onedrive:{item_id}` |
| 3.0 | Pipeline | `SET seen:onedrive:{item_id} 1 EX 2592000` (30 d dedup) |
| 3.1 | Pipeline | `SET approval:{uuid} {json} EX 172800` (48 h approval TTL) |
| 3.2 | `ContactsRepo.list_lists()` | Returns `[ContactList(code, label, member_count), ...]`; also fetches `len(list_active())` for the "Todos" button count |
| 3.5 | Telegram | Bot sends approval card (see Wire Format below) to `TELEGRAM_CHAT_ID` |
| — | Admin | Reads, decides |
| T₁ | Admin | Clicks `[📊 Minerals Report (45)]` |
| T₁+0.1 | `on_approve` handler | Edits same `message_id` to confirmation screen; sets `approval:{uuid}.status = "awaiting_confirm"` |
| T₂ | Admin | Clicks `[✅ Enviar]` |
| T₂+0.1 | `on_confirm` handler | Emits `approval_approved`, marks state `dispatching`, calls `dispatch_document(approval_id, list_code)` |
| T₂+0.2 | `dispatch_document` | Loads approval state; if `downloadUrl` age > 50 min, re-fetches via `GraphClient.get_item(drive_id, item_id)` |
| T₂+0.3 | `ContactsRepo.list_by_list_code("minerals_report")` | Returns 45 active contacts |
| T₂+0.4 | `ProgressReporter` | Edits card → `"📤 Enviando 0/45…"` |
| T₂+0.5…57 | Concurrent loop (5 parallel) | Per contact: SHA1(phone\|drive_item_id) idempotency `SET NX EX 86400` → `UazapiClient.send_document(...)` → `DeliveryReporter.record_success/failure(category)` → ProgressReporter updates `"Enviando N/45"` |
| T₂+57 | Pipeline | Deletes `approval:{uuid}`, emits `cron_finished` with summary counters |
| T₂+57.1 | Telegram | Final card: `"✅ Enviado — 43/45 sucesso, 2 falhas (desconectado) · ⏱ 57s"` |

**Key property:** all EventBus events across phases 2 share the same `trace_id`, so a single `SELECT * FROM event_log WHERE trace_id = '…'` query returns the complete timeline from webhook arrival to final WhatsApp delivery.

### Fase 3 — Discard

Admin clicks `[❌ Descartar]` on the initial card. Handler:
1. Edits card to `"❌ Descartado às HH:MM"` (static, no buttons).
2. Emits `approval_discarded`.
3. Deletes `approval:{uuid}` from Redis.
4. Leaves `seen:onedrive:{item_id}` in place (same PDF won't re-trigger).

### Fase 4 — Subscription renewal

GH Actions cron `onedrive_resubscribe.yml` fires at 00:00 and 12:00 BRT:
1. `GraphClient.list_subscriptions()` returns all subscriptions under the configured app registration.
2. For each subscription with `expirationDateTime < now + 24h`: PATCH `/subscriptions/{id}` with `expirationDateTime = now + 3 days`.
3. If no subscription exists for our `notificationUrl`, create a fresh one.
4. Emit `cron_finished` (via `@with_event_bus`).

If the cron fails for >24 h, the existing watchdog (`watchdog_cron.py`) emits `cron_missed`, which routes to `_MainChatSink` → operator Telegram alert.

## Schema Changes

`supabase/migrations/20260422_contact_lists.sql`:

```sql
CREATE TABLE contact_lists (
  code        TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  description TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE contact_list_members (
  list_code     TEXT NOT NULL REFERENCES contact_lists(code) ON DELETE CASCADE,
  contact_phone TEXT NOT NULL REFERENCES contacts(phone_uazapi) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (list_code, contact_phone)
);

CREATE INDEX idx_clm_list_code ON contact_list_members(list_code);

ALTER TABLE contact_lists ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_list_members ENABLE ROW LEVEL SECURITY;
-- No policies: service_role bypasses RLS; all access is service-role.

INSERT INTO contact_lists (code, label) VALUES
  ('minerals_report', 'Minerals Report'),
  ('solid_fuels',     'Solid Fuels'),
  ('time_interno',    'Time Interno');
```

Note: `contact_list_members` is empty at migration time. Operator populates membership via SQL after the migration ships.

## Wire Formats

### Graph API — create subscription request

```json
POST https://graph.microsoft.com/v1.0/subscriptions
Authorization: Bearer {access_token}
{
  "changeType": "updated",
  "notificationUrl": "https://<railway>/onedrive/notify",
  "resource": "/drives/{GRAPH_DRIVE_ID}/root:/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals",
  "expirationDateTime": "2026-04-25T00:00:00Z",
  "clientState": "<32-char-secret>"
}
```

### Graph API — change notification payload (inbound)

```json
POST /onedrive/notify
{
  "value": [
    {
      "subscriptionId": "...",
      "clientState": "<must match env GRAPH_WEBHOOK_CLIENT_STATE>",
      "resource": "/drives/{drive_id}/root:/SIGCM/.../Minerals_Report_042226.pdf",
      "resourceData": { "id": "<drive_item_id>", ... },
      "changeType": "updated",
      "tenantId": "..."
    }
  ]
}
```

### Uazapi `/send/media` (outbound)

Replicates the payload shape used by the current n8n workflow (verified in `HTTP Request5` node of `[WF] RELATORIO DIARIO.json`):

```json
POST https://mineralstrading.uazapi.com/send/media
Headers: { "token": "{UAZAPI_TOKEN}" }
{
  "number":  "{contact.phone_uazapi}",
  "type":    "document",
  "file":    "{item.@microsoft.graph.downloadUrl}",
  "docName": "{item.name}",
  "text":    "{optional caption}"
}
```

The `file` field accepts a public URL; Uazapi fetches the bytes server-side. Graph `downloadUrl` values are pre-authenticated and valid for ~1 h, after which `dispatch_document` re-fetches via `GraphClient.get_item`.

### Telegram callback_data classes

```python
class OneDriveApprove(CallbackData, prefix="od_ap"):
    approval_id: str   # UUID of the Redis approval:{uuid} key
    list_code: str     # contact_lists.code OR "__all__" for Todos

class OneDriveConfirm(CallbackData, prefix="od_cf"):
    approval_id: str
    list_code: str

class OneDriveDiscard(CallbackData, prefix="od_dc"):
    approval_id: str
```

Aiogram's CallbackData serialization limits each payload to 64 bytes total; UUIDs + short codes fit comfortably.

### Redis keys

| Key | Type | TTL | Purpose |
|---|---|---|---|
| `onedrive:subscription_id` | string | none | Active Graph subscription ID |
| `onedrive:delta_token:sigcm` | string | none | Graph delta query cursor |
| `seen:onedrive:{drive_item_id}` | string `"1"` | 30 days | Webhook dedup (same PDF won't retrigger) |
| `approval:{uuid}` | JSON string | 48 hours | `{drive_item_id, filename, size, downloadUrl, downloadUrl_fetched_at, status, created_at}` where `status ∈ {pending, awaiting_confirm, dispatching, completed}` |
| `idempotency:{sha1(phone\|drive_item_id)}` | string `"1"` | 24 hours | Per-recipient dedup within dispatch |

## Error Handling

| Failure | Detection | Recovery |
|---|---|---|
| Graph webhook duplicate | `seen:onedrive:{item_id}` present | Silent ignore; log `duplicate_webhook` as info-level event |
| `clientState` mismatch | `routes/onedrive.py` validation | HTTP 401; emit `webhook_auth_failed` (warn) |
| Graph 429 (rate limit) | `GraphClient` response code | `@retry_with_backoff` (3 attempts, exponential) |
| Graph 401 (token expired) | Response code | Re-request access_token, retry once; on repeated failure → crash with clear message |
| `downloadUrl` stale | `dispatch_document` proactively re-fetches when `downloadUrl_fetched_at` is older than 50 min (safety margin before the Graph ~1 h expiry) | Re-fetches via `GraphClient.get_item`, retries the send once. If Uazapi still returns a download error, fails the individual send with category `INVALID_MEDIA` and continues |
| Admin no-click for 48h | Redis TTL expires | State vanishes; post-expiry clicks hit a no-op handler that replies "⚠️ Aprovação expirada" |
| Uazapi disconnected (WhatsApp offline) | `DeliveryReporter.record_failure(WHATSAPP_DISCONNECTED)` | Circuit breaker: 3 consecutive failures in this category abort the run and alert "Reconecte QR em mineralstrading.uazapi.com" (existing pattern) |
| Single-recipient failure | Caught per-iteration | Loop continues; categorized in final summary |
| Dispatch crash mid-run | Uncaught exception | `@with_event_bus` emits `cron_crashed`; state remains in Redis; re-dispatch is a Phase 2 bot command |
| Subscription silently expired | Resubscribe cron detects `expirationDateTime < now+24h`; watchdog detects cron miss | Cron renews; watchdog alerts if cron fails repeatedly |
| Webhook dropped during Railway downtime | Microsoft does not retry | **Accepted risk in v1.** Admin notices missing delivery and triggers manual catch-up (not automated) |
| Double-click "Enviar" | Handler checks `approval:{uuid}.status` | If already `dispatching`/`completed`, responds with toast "já em andamento" |
| Contact with invalid phone | Filtered upstream by `ContactsRepo` normalization | Logged count of filtered contacts, dispatch continues |
| PDF rejected by Uazapi (corrupt, unsupported) | Uazapi response error | Categorized `INVALID_MEDIA`, dispatch continues for remaining contacts |

**Idempotency layers:**
1. `seen:onedrive:{item_id}` — prevents reprocessing same PDF from duplicate Graph webhooks.
2. `idempotency:{sha1(phone|drive_item_id)}` — prevents same recipient receiving duplicate send from retries or click storms.

**Circuit-breaker categories (inherited from `DeliveryReporter._FATAL_CATEGORIES`):**
- 3× `WHATSAPP_DISCONNECTED` → abort
- 5× `RATE_LIMITED` → abort
- Any `AUTH_FAILED` → immediate abort

## Testing Strategy

### Unit tests (pytest + mocks)

| File | Coverage |
|---|---|
| `tests/test_graph_client.py` | OAuth token cache, retry on 429/401, delta response parsing, `get_item` with mocked `requests` |
| `tests/test_uazapi_send_document.py` | Payload shape (`number`, `type=document`, `file`, `docName`), retry on 5xx, raise on 4xx |
| `tests/test_contacts_repo_lists.py` | `list_lists()` member_count accuracy, `list_by_list_code()` active-only filter, frozen `ContactList` dataclass |
| `tests/test_onedrive_pipeline.py` | PDF-only filter, `seen:*` dedup path, folder-vs-file branching, `clientState` validation |
| `tests/test_onedrive_callbacks.py` | Each handler with `AsyncMock` bot: verifies correct `edit_message_text` calls + Redis state transitions |
| `tests/test_dispatch_document.py` | Stale `downloadUrl` re-fetch, idempotency key dedup, concurrent fan-out (5 parallel), `DeliveryReporter` integration |
| `tests/test_onedrive_resubscribe.py` | Renewal of near-expiring subs, creation when no sub exists, handling of zero subs edge case |

Redis mocking: reuse `fakeredis` / existing mock in `tests/test_curation_redis_client.py`.

Coverage target: ≥80% on all new modules (global `testing.md` requirement).

### Integration tests

| File | Coverage |
|---|---|
| `tests/test_contact_lists_migration.py` | Applies migration against a Supabase test branch (`mcp__supabase__create_branch`); asserts tables, RLS, FK constraints, and seeded rows |
| `tests/test_graph_subscription_lifecycle.py` | *Optional*, gated by env `RUN_GRAPH_INTEGRATION=1` — creates, renews, and deletes a real subscription against a test tenant |

### E2E manual checklist (pre-deploy)

1. Seed a test list `test_list` with a single contact (operator's own number).
2. Expose local Railway via ngrok or Cloudflare tunnel.
3. Create a test subscription pointing `notificationUrl` at the tunnel.
4. Drop a test PDF into the SIGCM folder and verify:
   - [ ] Approval card appears with 4 list buttons + `Todos` + `Descartar`.
   - [ ] Click on `test_list` shows confirmation screen.
   - [ ] Click `Enviar` triggers dispatch.
   - [ ] Operator's WhatsApp receives the PDF.
   - [ ] Final card shows `"✅ 1/1"`.
   - [ ] `event_log` contains the full timeline under a single `trace_id`.
5. Drop another PDF, click `Descartar`, verify card shows `"❌ Descartado"` and Redis state is cleared.
6. Force `downloadUrl` stale (wait >1 h or mutate cached value), click `Enviar`, verify re-fetch happened.
7. Drop the same PDF twice rapidly; verify only one approval card appears.
8. Run `python execution/scripts/onedrive_resubscribe.py` and verify `cron_finished` appears in the events channel.

### Smoke test (first production run)

1. Create `smoke_test` list with only the operator's number.
2. Drop a real PDF into SIGCM.
3. Validate end-to-end.
4. Delete `smoke_test` list.
5. Proceed to populate real list memberships.

### Tests explicitly omitted

- Load tests (45 contacts at concurrency 5 is trivial for Uazapi).
- Chaos/fault injection (unit mocks already cover the Section: Error Handling matrix).
- Telegram card UI snapshots (too brittle; asserting correct `edit_text` call arguments suffices).

## Environment Variables (new)

Both root `.env` (GH Actions) and `webhook/requirements.txt` consumers (Railway) need these — keep in sync per existing two-requirements-files convention:

```
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
GRAPH_DRIVE_ID=b!OpzpfwNGVEuhVt-oJYZoukWVYCYFUfdDmAJi023i_CwVR7rrWffbSI9pE6zV1uYd
GRAPH_FOLDER_PATH=/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals
GRAPH_WEBHOOK_CLIENT_STATE=<32-char-random-secret>
ONEDRIVE_WEBHOOK_URL=https://<railway-domain>/onedrive/notify
```

Azure Entra ID app registration must grant **application permission** `Files.Read.All` with admin consent (not delegated, so the Graph client uses the client-credentials OAuth2 flow).

## Files Summary

**New (10 files + 1 migration):**
- `execution/integrations/graph_client.py`
- `execution/scripts/onedrive_resubscribe.py`
- `webhook/routes/onedrive.py`
- `webhook/onedrive_pipeline.py`
- `webhook/dispatch_document.py`
- `webhook/bot/routers/callbacks_onedrive.py`
- `.github/workflows/onedrive_resubscribe.yml`
- `supabase/migrations/20260422_contact_lists.sql`
- `tests/test_graph_client.py` (+ six sibling test files per Testing Strategy)

**Altered (5 files):**
- `execution/integrations/uazapi_client.py` — add `send_document`
- `execution/integrations/contacts_repo.py` — add `list_lists`, `list_by_list_code`, `ContactList`
- `webhook/bot/callback_data.py` — add three new CallbackData classes
- `webhook/bot/main.py` (`create_app`) — mount `routes/onedrive.py` and `callbacks_onedrive_router`
- `webhook/status_builder.py` — add `onedrive_resubscribe` to `ALL_WORKFLOWS`

Total: ~900 lines of new code across Python and SQL.

## Phase 2 candidates (not blocking v1)

- Bot UI for managing `contact_lists` and memberships (new router + keyboards).
- Dashboard page for pending approvals and list administration.
- Multi-select approval (send one PDF to several lists).
- First-page thumbnail preview in approval card.
- Fallback poll cron for webhook dropout recovery.
- Per-list default caption/text template, editable at approval time.
- Re-dispatch bot command for individual failed recipients.
- Delivery receipts (did each WhatsApp message actually deliver).
- Multi-folder watcher config (`contact_lists`-style table for `onedrive_watchers`).

---

*Spec finalized 2026-04-22 after brainstorming session.*
