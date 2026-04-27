# OneDrive Multi-Approver — Design Spec

**Date:** 2026-04-27
**Status:** Approved — ready for implementation planning
**Owner:** bigodinhc
**Related:**
- `docs/superpowers/specs/2026-04-22-onedrive-pdf-broadcast-design.md` (v1 single-admin flow this spec extends)
- `docs/superpowers/specs/2026-04-22-contacts-supabase-migration-design.md` (contacts table consumed)
- `docs/superpowers/specs/2026-04-21-observability-unified-design.md` (EventBus / trace_id spine)

## Goal

Allow up to 5 trusted Telegram users (initially 2 — admin + 1 colleague) to receive and act on OneDrive PDF approval cards, while keeping all other bot capabilities locked to admin/subscriber roles. The first user to click any action button claims the approval; the other users' cards transition to a read-only state showing who decided what.

## Motivation

Today, OneDrive approval cards land only in the admin's chat (`TELEGRAM_CHAT_ID`). When the admin is unavailable, the daily SharePoint report stalls or has to be sent manually outside the approved flow. The team wants a small, scoped delegation: a second person can approve the SIGCM Minerals/Solid Fuels reports on the admin's behalf, without gaining access to the rest of the bot (workflows, queue, curation, contact admin, dashboard triggers).

The replacement is intentionally minimal — env-var-driven approver list (orthogonal to existing roles), no new commands, no UI for managing approvers.

## Scope

### In scope

1. New env var `ONEDRIVE_APPROVER_IDS` (CSV of Telegram chat IDs).
2. New **capability** check `is_onedrive_approver(chat_id)` (orthogonal to the existing role enum). Returns `True` for admin and for any `chat_id` in the env list. Roles (`admin`/`subscriber`/`pending`/`unknown`) are unchanged — a user can simultaneously be a `subscriber` AND an OneDrive approver, with no conflict.
3. Fan-out of the OneDrive approval card from `onedrive_pipeline.py` to admin + every approver in the env list (deduplicated).
4. Atomic Redis-based claim lock (`approval:{uuid}:claimed_by`) — first click wins.
5. Cascade edits to non-clicker recipients' cards on three events: claim (lock), final result (success/partial/failure), discard.
6. Silent onboarding: when an approver-only user sends `/start`, they get a fixed welcome message and skip the existing `pending` user creation flow.
7. New event types in `event_log`: `approval_fanout`, `approval_fanout_partial`, `approval_claimed`, `approval_clashed`, `cascade_edit_skipped`, `cascade_edit_failed`.

### Out of scope (explicit non-goals)

- Bot or dashboard UI to manage the approver list. Env var only.
- New commands tied to the approver capability. Approvers interact only via inline buttons on cards they receive.
- Re-issuing or recovering approval cards that an approver lost (e.g., conversation deleted). Admin always receives their own copy as a safety net.
- Multi-select dispatch (one PDF to several lists in one approval). Same as v1 — single list per approval.
- Rollback after `[Enviar]`. Once Uazapi receives the request, WhatsApp send is irreversible.
- Retroactive invalidation: if an approver is removed from `ONEDRIVE_APPROVER_IDS` mid-approval, their pending card still works.
- Auto-release of stuck claims (claimer walked away mid-flow). 48h Redis TTL is the only recovery.
- Polling/reminders for approvers who haven't acted.
- Per-approver permission scoping (e.g., approver X can only send to list Y). All approvers can pick any list.

## Architecture

### Component changes

| File | Change | Responsibility |
|---|---|---|
| `.env`, `.env.example`, `webhook/.env` (if present) | + `ONEDRIVE_APPROVER_IDS=` | CSV of additional approver chat IDs (admin always implicit). Default empty. |
| `webhook/bot/users.py` | + `get_onedrive_approver_ids()`, + `is_onedrive_approver(chat_id)`, + `format_user_label(from_user)`. **`get_user_role()` is unchanged** — capability lives outside the role enum. | Source of truth for the approver capability and identity rendering. |
| `webhook/onedrive_pipeline.py` | `process_notification` switches from a single `bot.send_message(admin_chat_id, …)` to `asyncio.gather` fan-out across `[admin] + approvers` (deduplicated). Persists `recipients: [{chat_id, message_id}]` in `approval:{uuid}` after sends complete. | Card distribution + recipient persistence for cascade. |
| `webhook/bot/routers/callbacks_onedrive.py` | Replace `RoleMiddleware({"admin"})` with a per-handler aiogram filter `F.func(lambda q: is_onedrive_approver(q.from_user.id))`. Three handlers gain a `_claim()` helper using `SET NX` on `approval:{uuid}:claimed_by`. New `_edit_others()` helper iterates `recipients` (excluding clicker) and calls `bot.edit_message_text` in parallel, swallowing expected `TelegramBadRequest` variants. | Capability-based gating + race-safe lock + cascade edits. |
| `webhook/bot/routers/onboarding.py` | Early return in `/start` handler when `is_onedrive_approver(chat_id) and not is_admin(chat_id) and get_user(chat_id) is None`: sends a fixed welcome and does **not** create a `pending` user record. (If the approver was already approved as a subscriber separately, normal subscriber flow runs unchanged.) | Keeps approver-only users out of the pending-approval flow without disrupting existing subscriber records. |

### Components NOT touched

- `execution/` (cron scripts, integrations, curation) — zero changes.
- `actors/` (Apify) — zero changes.
- `dashboard/` (Next.js) — zero changes.
- `supabase/` (Postgres schema, migrations) — zero changes.
- `webhook/dispatch_document.py` — zero logic changes; consumed identically by `on_confirm`.
- `webhook/routes/onedrive.py` — zero changes; HTTP entry stays identical.
- `execution/integrations/graph_client.py`, `uazapi_client.py`, `contacts_repo.py` — zero changes.
- Watchdog, EventBus sinks, ProgressReporter — zero changes.

### Estimated diff

~150 lines of new Python + ~120 lines of new tests. No deletions, no refactors of stable code paths.

## Data Model

### New environment variable

```
ONEDRIVE_APPROVER_IDS=456789012,234567890
```

- Comma-separated list of Telegram `chat_id`s. Whitespace tolerated.
- Empty / unset → identical to current single-admin behavior (no warning logged).
- Items that fail `int()` parsing are skipped with a single startup warning logged once via `logging`. Process continues.
- Result is cached at module level (`functools.lru_cache(maxsize=1)`) — re-reads require process restart, which is fine because env changes already require Railway redeploy.

### Approval state JSON (`approval:{uuid}` in Redis)

New field in **bold**, all others identical to v1:

```json
{
  "drive_id": "...",
  "drive_item_id": "...",
  "filename": "Minerals_Report_042726.pdf",
  "size": 482931,
  "downloadUrl": "...",
  "downloadUrl_fetched_at": "2026-04-27T...",
  "status": "pending",
  "created_at": "2026-04-27T...",
  "trace_id": "...",
  "recipients": [
    { "chat_id": 123456789, "message_id": 1001 },
    { "chat_id": 456789012, "message_id": 5005 }
  ]
}
```

`recipients` is populated once during `process_notification` after fan-out completes and is never mutated afterward.

### New Redis key — claim lock

```
Key:   approval:{uuid}:claimed_by
Type:  string (JSON)
TTL:   inherits from approval (≤ 48h remaining)
Value: {
         "chat_id": 123456789,
         "label": "@joao",
         "claimed_at": "2026-04-27T14:32:11Z"
       }
```

Atomic operation: `SET approval:{uuid}:claimed_by <json> NX EX <ttl_seconds>`.

- Returns `OK` → this click won. Handler proceeds with the normal flow (approve → confirm OR discard).
- Returns `nil` → already owned. Handler reads the existing value, calls `query.answer("Já em decisão por @X")`, exits without state change.
- Reentrant case: same chat_id clicks again → SETNX returns nil, but `_claim()` reads back and recognizes the same chat_id, returns `("reentrant", claimer)` instead of `("lost", claimer)`. Handler treats as own claim.

A separate key (rather than a field inside the approval JSON) is used because SETNX on a JSON sub-field requires WATCH/MULTI or a Lua script. A separate key gives single-operation atomicity, expires automatically with the approval, and adds zero complexity.

### Identity rendering — `format_user_label(from_user)`

Single function, used wherever an approver name appears in user-facing text. Cached into `claimed_by` at claim time so cascade edits stay consistent even if username changes mid-flow.

```
1. f"@{from_user.username}" if from_user.username
2. from_user.first_name (no "@") otherwise
3. f"Usuário {chat_id_truncated_to_4_digits}" final fallback
```

`chat_id` is never exposed in user-facing strings beyond the truncated fallback.

## State Machine

### Initial fan-out

```
[PDF detected by webhook]
        │
        ▼
onedrive_pipeline.process_notification:
  1. approval_id = uuid12()
  2. SET approval:{uuid} <state-without-recipients> EX 48h
  3. recipients_to_send = dedupe([admin_chat_id] + ONEDRIVE_APPROVER_IDS)
  4. asyncio.gather([
       bot.send_message(chat_id=cid, text=..., reply_markup=...)
       for cid in recipients_to_send
     ], return_exceptions=True)
  5. recipients = [(cid, msg.message_id) for each successful send]
  6. SET approval:{uuid} <state-with-recipients> KEEPTTL
  7. Emit approval_fanout (or approval_fanout_partial if exceptions)
```

Both admin and each approver now see an identical card with `[Lista A] [Lista B] [Todos] [Descartar]` buttons.

### Click handling — winner determination

Any click (any user, any button) flows through `_claim()`:

```python
result, claimer = await _claim(redis, approval_id, query.from_user)
# result ∈ {"won", "lost", "reentrant"}
```

Implementation:

```python
label = format_user_label(query.from_user)
payload = json.dumps({
    "chat_id": query.from_user.id,
    "label": label,
    "claimed_at": now_iso(),
})
ok = await redis.set(
    f"approval:{approval_id}:claimed_by",
    payload,
    nx=True,
    ex=remaining_approval_ttl(),
)
if ok:
    return ("won", json.loads(payload))
existing = json.loads(await redis.get(f"approval:{approval_id}:claimed_by"))
if existing["chat_id"] == query.from_user.id:
    return ("reentrant", existing)
return ("lost", existing)
```

### Winner branches by button

#### Branch 1 — Winner clicked `[Lista X]`

```
Winner's card  → edit to "⚠️ Confirmar envio?  Lista X (N contatos)"
                       [✅ Enviar] [◀ Voltar]
Cascade        → for each recipient where chat_id != winner:
                  edit to "🔒 Sendo decidido por @{label} às HH:MM"
                  (no buttons)
```

Subsequent winner actions (claim still held):

| Action | Winner card | Other recipients' cards |
|---|---|---|
| `[◀ Voltar]` | back to `[Lista A] [Lista B] [Todos] [Descartar]` | unchanged (still 🔒) |
| `[✅ Enviar]` (success) | `📤 Enviando…` → `✅ Enviado N/M` | edit β → `✏️ Decidido por @X → Lista X · ✅ N/M` |
| `[✅ Enviar]` (failure) | `❌ Falha no envio: <reason>` | edit β → `✏️ Decidido por @X → Lista X · ❌ Falha no envio` |
| 48h timeout | message persists in chat history | 🔒 message persists; clicks return "Aprovação expirada" |

#### Branch 2 — Winner clicked `[❌ Descartar]`

```
Winner's card  → edit to "❌ Descartado às HH:MM\n`{filename}`"  (parse_mode="Markdown" — preserves v1 behavior)
Cascade        → for each recipient where chat_id != winner:
                  edit to "❌ Descartado por @{label} às HH:MM\n{filename}"  (parse_mode=None, no backticks — see Markdown safety note below)
```

Skips the intermediate 🔒 state — discard is terminal in one step.

### Cleanup

| Trigger | Action |
|---|---|
| `on_confirm` returns (success or failure of dispatch) | `DEL approval:{uuid}`, `DEL approval:{uuid}:claimed_by` |
| `on_discard` | same DEL pair |
| 48h TTL with no resolution | Redis expires both keys automatically |
| `on_approve` followed by winner's `[◀ Voltar]` | nothing deleted; claim retained by winner; non-clicker cards stay 🔒 |

### `_edit_others` helper

```python
async def _edit_others(bot, redis, approval_id, new_text, exclude_chat_id, bus):
    state = await _load_state(redis, approval_id)
    recipients = state.get("recipients", [])
    targets = [r for r in recipients if r["chat_id"] != exclude_chat_id]
    if not targets:
        return
    coros = [
        bot.edit_message_text(
            chat_id=r["chat_id"],
            message_id=r["message_id"],
            text=new_text,
            parse_mode=None,
            reply_markup=None,
        )
        for r in targets
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)
    for r, exc in zip(targets, results):
        if isinstance(exc, TelegramBadRequest):
            # message_is_not_modified, message_to_edit_not_found, chat_not_found, bot_blocked
            bus.emit("cascade_edit_skipped", level="info", detail={
                "target_chat_id": r["chat_id"],
                "reason": str(exc)[:120],
            })
        elif isinstance(exc, Exception):
            bus.emit("cascade_edit_failed", level="warn", detail={
                "target_chat_id": r["chat_id"],
                "error": str(exc)[:200],
                "exc_type": type(exc).__name__,
            })
```

Principle: **a single approver with a blocked bot or deleted conversation never blocks the rest of the flow nor the dispatch.**

### Markdown safety in cascade text

All cascade edits use `parse_mode=None`. Reason: `format_user_label` returns `@username` or `first_name`, both of which can legitimately contain underscores (e.g., `@some_user`, `Maria_Clara`). Under Markdown V1, an unescaped underscore opens an italic span, and Telegram returns `entity_parsing_error`, breaking the edit. Sending plain text avoids the entire class of bugs at the cost of losing backtick-formatted filenames in cascade messages — a worthwhile trade for ~5 users where readability of `Minerals_Report.pdf` without code-block formatting is fine.

The winner's own card edits (in `on_approve`/`on_confirm`/`on_discard`) keep their existing `parse_mode="Markdown"` from v1 because the username being rendered there is the bot's own template content (`{filename}`, list label), not user-supplied input.

### New event taxonomy (all carry `trace_id` of the originating PDF)

| Event | Emitted when | Detail |
|---|---|---|
| `approval_fanout` | After `process_notification` populates recipients | `{recipient_count, recipient_chat_ids}` |
| `approval_fanout_partial` | One or more `send_message` failures during fan-out | `{succeeded, failed, errors}` |
| `approval_claimed` | SETNX wins | `{chat_id, label}` |
| `approval_clashed` | SETNX loses | `{loser_chat_id, winner_label}` |
| `cascade_edit_skipped` | Edit silenced (expected error) | `{target_chat_id, reason}` |
| `cascade_edit_failed` | Edit raised unexpected exception | `{target_chat_id, error, exc_type}` |

Existing v1 events (`approval_clicked`, `approval_approved`, `approval_discarded`, `dispatch_*`, `pdf_downloaded`, etc.) remain unchanged.

## Error Handling

| # | Failure | Detection | Treatment | Visible to |
|---|---|---|---|---|
| 1 | `ONEDRIVE_APPROVER_IDS` empty/absent | startup | identical to current single-admin behavior; no warning | nobody |
| 2 | `ONEDRIVE_APPROVER_IDS=abc,123` (malformed item) | `get_onedrive_approver_ids()` | discard `abc`, keep `123`; single startup warning | logs/Sentry |
| 3 | Approver chat_id valid but bot blocked / never started | `send_message` raises `TelegramForbiddenError` | catch specifically; emit `approval_fanout_partial`; recipients list excludes them; remaining sends + admin proceed | event_log; admin not pinged |
| 4 | Approver deleted conversation after card arrived | `edit_message_text` raises `TelegramBadRequest("message to edit not found")` | catch; emit `cascade_edit_skipped` info-level; other cards update normally | event_log |
| 5 | `"message is not modified"` on a redundant cascade edit | same | swallowed silently (existing pattern from `callbacks_queue.py`) | none |
| 6 | Bot crashes mid-fan-out (some sent, some not) | `asyncio.gather` partial | `recipients` saved with successes; PDF webhook is not re-sent by Microsoft Graph; missing approver loses this PDF; `approval_fanout_partial` alerts | admin via main-chat sink |
| 7 | Redis unavailable during initial `SET approval:{uuid}` | `process_notification` | exception bubbles to `_safe_process` (logged + dropped); no card sent; matches current v1 behavior | logs + Sentry |
| 8 | Redis unavailable during SETNX claim | callback handler | caught; `query.answer("⚠️ Sistema indisponível, tente novamente")`; no state change; click can be retried | toast |
| 9 | Two approvers click simultaneously | SETNX atomicity | one wins, other gets toast `"Já em decisão por @X"`; cascade from winner edits loser's card shortly after | toast + cascade |
| 10 | Approver removed from env mid-flow | none — `recipients` is a snapshot | pending approval still works for them; future approvals exclude them; **deliberate non-invalidation** | nobody |
| 11 | Approver added to env while approvals are pending | same | pending approvals do not retroactively reach them; next PDF does | nobody |
| 12 | Bot restarted mid-approval | Redis persistence | `approval:{uuid}`, `:claimed_by`, recipients all survive; clicks resume normally | nobody |
| 13 | Winner clicks `[Lista X]` then disappears | 48h TTL | both keys expire; late clicks hit `_load_state → None → "Aprovação expirada"` toast | toast (only on late click) |
| 14 | Dispatch fails completely (download error, Uazapi 5xx) | `dispatch_document` returns `{sent:0, failed:N}` | clicker card → `❌ Falha no envio: <reason>`; cascade β edits other recipients' cards to `✏️ Decidido por @X → ❌ Falha no envio` (summary only, no stack) | all approvers |

### Idempotency layers (unchanged from v1, plus new lock)

1. `seen:onedrive:{item_id}` (30d) — Graph webhook dedup, prevents reprocessing same PDF.
2. `idempotency:sha1(phone|drive_item_id)` (24h) — per-recipient WhatsApp dedup inside `dispatch_document`.
3. **NEW** `approval:{uuid}:claimed_by` (≤48h) — concurrent-click protection, single claimer per approval.

The three layers are orthogonal and never overlap.

### Critical ordering inside `process_notification`

```
1. approval_id = uuid12()
2. SET approval:{uuid} <state-without-recipients> EX 48h
3. asyncio.gather([send_message(r) for r in [admin] + approvers], return_exceptions=True)
4. recipients = [(chat_id, message_id) for successful sends]
5. SET approval:{uuid} <state-with-recipients> KEEPTTL
6. emit approval_fanout (or approval_fanout_partial)
```

Risk window: clicks between steps 3 and 5 (when cards exist but `recipients` may be empty). Practical probability: under 100ms between message arriving and a human clicking — effectively zero. Degraded behavior if it occurs: clicker's own card edits work (chat_id/message_id come from `query.message`); cascade is a no-op; other recipients' cards stay as the initial state. Acceptable.

### Explicit non-coverage

- Webhook drop during Railway downtime — already accepted v1 risk.
- Approver inactive for days — no polling/reminders. Cards naturally scroll out of feed.
- Multiple PDFs queued — each gets independent `approval_id`; no batching.
- Rollback after `[Enviar]` — Uazapi requests are not reversible.
- Auto-retry on `cascade_edit_failed` — rare enough that log + event suffices.

## Testing Strategy

### Coverage targets

- ≥80% on all new lines (global `testing.md` requirement).
- 100% on the `_claim()` helper paths (won/lost/reentrant) — race correctness is load-bearing.

### New unit test file — `tests/test_users_onedrive_approver.py`

```
test_get_approver_ids_empty_env
test_get_approver_ids_single
test_get_approver_ids_csv
test_get_approver_ids_with_whitespace
test_get_approver_ids_skips_malformed
test_get_approver_ids_caches_parsed_value
test_is_onedrive_approver_chat_in_env
test_is_onedrive_approver_chat_not_in_env
test_is_onedrive_approver_admin_implicit         # admin always passes regardless of env
test_is_onedrive_approver_subscriber_in_env      # subscriber + env → True (capability orthogonal to role)
test_get_user_role_unchanged_admin               # regression — role enum untouched
test_get_user_role_unchanged_subscriber          # regression — env presence does NOT change role
test_get_user_role_unchanged_pending             # regression — env presence does NOT change role
test_format_user_label_with_username
test_format_user_label_no_username
test_format_user_label_no_username_no_name
```

### Expanded — `tests/test_onedrive_pipeline.py`

Existing v1 tests (PDF filter, `seen:` dedup, clientState validation, bootstrap guard) remain. Add:

```
test_fanout_admin_only_when_env_empty
test_fanout_admin_plus_approvers
test_fanout_dedup_admin_in_env_var
test_fanout_partial_failure
test_fanout_persists_recipients_after_send
test_fanout_returns_when_admin_send_fails
```

### Expanded — `tests/test_onedrive_callbacks.py`

```
test_claim_setnx_winner_path
test_claim_setnx_loser_path
test_claim_reentrant
test_cascade_edit_skips_clicker
test_cascade_edit_swallows_message_not_modified
test_cascade_edit_swallows_message_to_edit_not_found
test_cascade_edit_swallows_chat_not_found
test_cascade_edit_unknown_error_emits_event
test_on_approve_cascades_lock_message
test_on_confirm_cascades_final_result
test_on_confirm_cascades_failure
test_on_discard_cascades_directly
test_on_discard_deletes_both_keys
test_voltar_does_not_release_claim
```

Mocking strategy: reuse `fakeredis` and the bot mocking pattern from existing `tests/test_onedrive_callbacks.py`. Bot is `AsyncMock`; `edit_message_text` returns or raises depending on the case.

### E2E manual checklist (pre-deploy)

Pre-conditions:
- `ONEDRIVE_APPROVER_IDS=<colleague_chat_id>` set on Railway
- Both admin and colleague have started a chat with the bot at least once
- Supabase `contact_lists` has a `smoke_test` list with only the operator's WhatsApp number

```
[ ] Scenario 1: admin clicks first
    ▸ Drop test PDF in SharePoint
    ▸ Both receive identical card within ~5s
    ▸ Admin clicks [smoke_test (1)]
    ▸ Colleague's card edits to "🔒 Sendo decidido por @admin às HH:MM"
    ▸ Admin clicks [✅ Enviar]
    ▸ PDF arrives on operator's WhatsApp
    ▸ Colleague's card edits to "✏️ Decidido por @admin → smoke_test · ✅ 1/1"

[ ] Scenario 2: colleague clicks first
    ▸ Drop another PDF
    ▸ Colleague clicks [smoke_test (1)]
    ▸ Admin's card edits to "🔒 Sendo decidido por @colega"
    ▸ Colleague clicks [✅ Enviar]
    ▸ Admin's card edits to "✏️ Decidido por @colega → smoke_test · ✅ 1/1"

[ ] Scenario 3: discard
    ▸ Drop PDF
    ▸ Colleague clicks [❌ Descartar]
    ▸ Colleague's card → "❌ Descartado às HH:MM"
    ▸ Admin's card → "❌ Descartado por @colega às HH:MM" (skipped 🔒)

[ ] Scenario 4: simultaneous race
    ▸ Drop PDF
    ▸ Coordinate via voice: 3, 2, 1, both click different buttons at once
    ▸ One wins, other gets toast "Já em decisão por @X"
    ▸ Both cards converge to correct state

[ ] Scenario 5: regression (env empty)
    ▸ Set ONEDRIVE_APPROVER_IDS= and restart bot
    ▸ Drop PDF
    ▸ Only admin receives card (v1 behavior preserved)
    ▸ Restore env

[ ] Scenario 6: approver with bot blocked
    ▸ Colleague blocks the bot temporarily
    ▸ Drop PDF
    ▸ Admin receives normally
    ▸ event_log contains approval_fanout_partial
    ▸ Admin processes normally; colleague gets nothing
    ▸ Colleague unblocks

[ ] Scenario 7: Voltar does not release
    ▸ Drop PDF
    ▸ Admin clicks [Lista X]
    ▸ Colleague's card = 🔒
    ▸ Admin clicks [◀ Voltar]
    ▸ Colleague's card STILL 🔒
    ▸ Colleague clicks [Lista Y] → toast "Já em decisão por @admin"
    ▸ Admin clicks [Descartar] → both cards → "❌ Descartado por @admin"
```

### Production smoke test (first real run)

1. Coordinate with colleague in advance.
2. Drop a real PDF (or wait for the daily SIGCM PDF).
3. Confirm both receive the card.
4. Approve normally; observe dispatch + cascade.
5. Query `event_log` filtered by `workflow='onedrive_webhook' AND created_at > now() - interval '5 min'`. Expected sequence under a single `trace_id`:
   `webhook_received → delta_query_done → approval_created → approval_fanout → approval_claimed → approval_clicked → approval_approved → cascade_edit_skipped/done → dispatch_started → pdf_downloaded → dispatch_completed`.

### Tests explicitly omitted

- Load tests — production volume is ~1 PDF/day.
- Card UI snapshot tests — too brittle; asserting `edit_message_text` call args is enough.
- Real-Telegram integration in CI — dedicated test bot tokens cost more than they prevent.
- Property-based / fuzz testing — surface area too small.

## Environment Variables

New in both root `.env` (GH Actions, though unused there) and Railway (where the bot runs). Keep the example file in sync per existing two-requirements-files convention:

```
# ONEDRIVE_APPROVER_IDS=
# CSV of additional Telegram chat_ids that receive OneDrive approval cards.
# Admin (TELEGRAM_CHAT_ID) is always implicitly included.
# Empty/unset → admin-only behavior (v1).
# Example: ONEDRIVE_APPROVER_IDS=456789012,234567890
```

No other env-var changes.

## Files Summary

**Altered (4 files):**
- `webhook/bot/users.py` — new helpers + role priority update
- `webhook/onedrive_pipeline.py` — fan-out + recipients persistence
- `webhook/bot/routers/callbacks_onedrive.py` — claim helper + cascade helper + middleware update
- `webhook/bot/routers/onboarding.py` — `/start` early return for approvers

**Altered (config):**
- `.env.example` — document new var

**New (1 test file):**
- `tests/test_users_onedrive_approver.py`

**Expanded (2 test files):**
- `tests/test_onedrive_pipeline.py`
- `tests/test_onedrive_callbacks.py`

**Total estimated diff:** ~150 lines new Python + ~120 lines new tests.

## Phase 2 candidates (not blocking v1)

- Bot command for admin to add/remove approvers without redeploy (`/add_approver`, `/list_approvers`).
- "Liberar trava" button visible to non-clickers after N minutes of inactivity by the claimer.
- Dashboard view of pending approvals + claim state.
- Per-approver scoping (approver X can only send to list Y).
- Approval history / re-issue command for lost cards.
- Multi-select dispatch (one PDF to several lists in one approval).
- Rich identity rendering using stored Supabase `contacts` row when available.

---

*Spec finalized 2026-04-27 after brainstorming session.*
