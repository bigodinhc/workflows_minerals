# Contacts: Google Sheets → Supabase Migration

**Date:** 2026-04-22
**Status:** Design approved, ready for planning
**Scope:** Replace the shared Google Sheet (`1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0`) used by every agentic workflow as the WhatsApp broadcast list, with a first-class `contacts` table in Supabase. Also retires the `Controle` sub-sheet by reusing the existing Redis-based idempotency primitive.

## Motivation

Two forces, equal weight:

1. **Stack consolidation.** Supabase is already the source of truth for `event_log` (observability) and other tables. The Google Sheet is the last remaining external data store in the critical path. Retiring it removes a whole authentication surface (`GOOGLE_CREDENTIALS_JSON`), a runtime dependency (`gspread` + `google-auth`), and a class of failure modes specific to that integration.
2. **Performance.** Every workflow run pays gspread/HTTP latency to list contacts. A Supabase read over pgREST is sub-100ms and supports real indexes, constraints, and joins. Future features (per-contact metrics, dedupe, multi-list segmentation) become trivial instead of requiring more sheet gymnastics.

## Non-Goals

- Multi-tenant contact lists (the `ButtonPayload == "Big"` filter hinted at this; not in scope).
- Contact segmentation / tagging beyond `ativo`/`inativo`.
- Migrating other sheets-based features. Only the contact list and its `Controle` sub-sheet.
- UX changes to the bot or dashboard. Operations remain identical; only storage changes.

## High-Level Decisions

| Decision | Choice | Reasoning |
|---|---|---|
| Source of truth after cutover | Supabase `contacts` table | No dual-write, no mirror back to Sheets (mirror would negate performance gain). |
| Schema shape | Clean schema, not sheet-mirrored | Callers refactored to use correct field names (`name`, `phone_uazapi`, `status`). Avoids carrying webhook-legacy column names (`Evolution-api`, `ButtonPayload`) forever. |
| DDI policy | Explicit DDI required at `/add` | Accepts any country code, not just Brazil. Migration script has one-off BR-55 fallback for existing rows. |
| Phone validation | Google `phonenumbers` library | Catches format errors at input time with clear messages, not at send time. |
| Existence validation at `/add` | Test-send a welcome message via uazapi | Confirms the number receives on WhatsApp. Accepts known limitation: uazapi 200 OK does not guarantee delivery (async webhook status is out of scope here). |
| Welcome message text | `"Você foi adicionado à lista de informações de mercado da Minerals Trading."` | |
| Refactor shape | Full refactor — new `ContactsRepo` + `Contact` dataclass, callers updated | Drop-in replacement was considered but rejected; continuing `c.get("Evolution-api")` in 2027 is not acceptable for 6 callers-worth of cost. |
| Existing rows | Migrate all (active + inactive) via one-off script | Preserves history; `ButtonPayload=="Big"` → `ativo`, others → `inativo`. |
| Daily-report idempotency (`Controle` sheet) | **Not** a new Supabase table. Reuse `execution/core/state_store.try_claim_alert_key` (Redis `SET NX EX`) | Same pattern already used by the watchdog for "fire exactly once per window". Atomic check-and-mark, auto-expiring, no new infra. |

## Architecture Changes

### What is removed

- `execution/integrations/sheets_client.py` (entire `SheetsClient` class, including `Controle` helpers).
- Inline `_get_contacts_sync` in `webhook/dispatch.py:70`.
- All `SHEET_ID = "1tU3Izd..."` constants in workflow scripts.
- `gspread` dependency from `requirements.txt` (pending audit for other users).
- The sheet itself stays alive as a read-only historical backup. Nothing in the new code reads it.

### What is added

- `supabase/migrations/20260422_contacts.sql` — creates `contacts` table, indexes, `updated_at` trigger, enables RLS.
- `execution/integrations/contacts_repo.py` — `ContactsRepo` class + `Contact` dataclass + `normalize_phone` helper + typed exceptions (`ContactNotFoundError`, `ContactAlreadyExistsError`, `InvalidPhoneError`).
- `scripts/migrate_contacts_from_sheets.py` — one-off idempotent migration script with `--dry-run`.
- `phonenumbers` added to `requirements.txt`.

### Consumer map

| Consumer | Before | After |
|---|---|---|
| `execution/scripts/morning_check.py` | `sheets.get_contacts(SHEET_ID, ...)` + `check/mark_daily_status` | `contacts_repo.list_active()` + `state_store.try_claim_alert_key` |
| `execution/scripts/send_news.py` | `sheets.get_contacts(...)` | `contacts_repo.list_active()` |
| `execution/scripts/send_daily_report.py` | `sheets.get_contacts(...)` | `contacts_repo.list_active()` |
| `execution/scripts/baltic_ingestion.py` | `get_contacts` + `check/mark_daily_status` | `list_active()` + `try_claim_alert_key` |
| `webhook/dispatch.py` | Inline `_get_contacts_sync()` via gspread | `contacts_repo.list_active()`; function deleted |
| `webhook/bot/routers/messages.py` (`/add`) | `sheets.add_contact(...)` | `contacts_repo.add(name, phone, send_welcome=...)` |
| `webhook/bot/routers/commands.py` (`/contacts`) | `sheets.list_contacts(...)` | `contacts_repo.list_all(search, page, per_page)` + bulk activate/deactivate inline buttons |
| `webhook/bot/routers/callbacks_contacts.py` (toggle) | `sheets.toggle_contact(...)` | `contacts_repo.toggle(phone)` |
| `dashboard/app/api/contacts/route.ts` | Reads sheet via googleapis or via Python API | Reads Supabase `contacts` (direct `@supabase/supabase-js` server-side, or proxies updated Python API) |

## Schema

File: `supabase/migrations/20260422_contacts.sql`

```sql
create table if not exists contacts (
  id           uuid        primary key default gen_random_uuid(),
  name         text        not null,
  phone_raw    text        not null,
  phone_uazapi text        not null,
  status       text        not null default 'ativo'
                 check (status in ('ativo', 'inativo')),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create unique index contacts_phone_uazapi_uidx on contacts (phone_uazapi);
create index        contacts_status_idx        on contacts (status);
create index        contacts_status_active_idx on contacts (created_at desc)
  where status = 'ativo';

create or replace function contacts_set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

create trigger contacts_updated_at
  before update on contacts
  for each row execute function contacts_set_updated_at();

alter table contacts enable row level security;
-- No policies. Only service_role bypasses RLS; anon/public get zero access.
-- All consumers (bot, workflows, dashboard API) use service key server-side.

comment on table contacts is
  'WhatsApp broadcast list. Source of truth for all agentic workflows. '
  'Replaced Google Sheets (1tU3Izd...UgL0) on 2026-04-22.';
comment on column contacts.phone_raw is
  'What the user typed at /add, before normalization. Audit trail.';
comment on column contacts.phone_uazapi is
  'Digits-only E.164 without +, ready for uazapi number field. e.g. 5511987654321';
comment on column contacts.status is
  'ativo = receives broadcasts. inativo = suppressed but preserved, never deleted.';
```

### Schema notes

- `id uuid` (not `bigserial`): client-side generation is useful in the migration script and dashboard. `contacts` is a small table; the append-only-volume argument for bigserial does not apply.
- Two phone columns: `phone_raw` is the audit trail of what the user typed (spaces, parens, `+`). `phone_uazapi` is the canonical digits-only form actually passed to uazapi. `UNIQUE` is on `phone_uazapi`, so `"+55 11 98765-4321"` and `"5511987654321"` collide correctly.
- Two status indexes: general (`contacts_status_idx`) for admin queries, and a partial index (`contacts_status_active_idx`) scoped to `status='ativo'` ordered by `created_at desc` — this matches the hot query pattern of workflows (`list_active()` with chronological order for dashboard display).
- RLS with no policies is the same pattern as `event_log_rls.sql` migration. Service role bypass is the intended access path.
- `updated_at` trigger avoids forgetting to bump manually on update.

## `ContactsRepo` Interface

File: `execution/integrations/contacts_repo.py`

```python
@dataclass(frozen=True)
class Contact:
    id: str
    name: str
    phone_raw: str
    phone_uazapi: str
    status: str             # 'ativo' | 'inativo'
    created_at: datetime
    updated_at: datetime

    def is_active(self) -> bool: ...
    def to_dict(self) -> dict: ...


class ContactNotFoundError(Exception): ...
class ContactAlreadyExistsError(Exception):
    existing: Contact
class InvalidPhoneError(ValueError): ...


def normalize_phone(raw: str) -> str:
    """Using `phonenumbers`: parse, validate, return E.164 without '+'.
    Raises InvalidPhoneError on any parse/validation failure (missing DDI,
    invalid country code, invalid length for country, etc.)."""


class ContactsRepo:
    def __init__(self, client: Optional[supabase.Client] = None): ...

    # Reads
    def list_active(self) -> list[Contact]: ...
    def list_all(self, *, search: Optional[str] = None,
                 page: int = 1, per_page: int = 10
                 ) -> tuple[list[Contact], int]: ...
    def get_by_phone(self, phone: str) -> Contact: ...

    # Writes
    def add(self, name: str, phone_raw: str, *,
            send_welcome: Callable[[str], None]) -> Contact:
        """1. normalize phone (→ InvalidPhoneError)
           2. duplicate pre-check (→ ContactAlreadyExistsError)
           3. send_welcome(phone_uazapi) — injected by caller
           4. insert row with status='ativo'
           If send_welcome raises, no insert happens.
           If insert races against another add(), unique index catches it
           and raises ContactAlreadyExistsError."""

    def toggle(self, phone: str) -> Contact:
        """Flip ativo ↔ inativo. Returns updated Contact.
           Raises ContactNotFoundError."""

    def bulk_set_status(self, status: str, *,
                        search: Optional[str] = None) -> int:
        """Set status on all matching contacts. Returns count updated.
           If search is None, affects all rows. If search is provided,
           affects only rows where name ILIKE %search%."""
```

### Design notes

- **Dataclass, not Pydantic.** Lightweight, immutable (frozen), honors the global immutability rule. Upgrade path to Pydantic exists if serialization/validation grows non-trivial.
- **`normalize_phone` is module-level**, not a method. Testable in isolation, reusable by the migration script without instantiating the repo.
- **Uses `phonenumbers` library** for real validation: parses format, confirms DDI is a valid country code, confirms length matches country rules. Clear error messages at input time ("invalid country code +999", "phone too short for country +55").
- **`send_welcome` injected into `add()`** as a callable. Repo does not import `aiohttp` and does not know about the uazapi token. Caller (bot `/add` handler) wires the send function using the existing `webhook.dispatch.send_whatsapp`. Benefit: repo testable without mocking HTTP, and infrastructure layers stay separated.
- **Order inside `add()`: normalize → duplicate check → send → insert.** If insert fails after send, a phantom welcome was sent (rare and recoverable — user retries `/add`, duplicate check on next attempt is still false because insert failed, welcome sent again; idempotent on the user side). Reverse order (insert before send) would leave a DB row with no welcome sent — worse for audit.
- **Duplicate pre-check before send** avoids spamming welcome messages to numbers already in the list.
- **`bulk_set_status` respects `search`** — operates on the same filter scope the user sees in `/contacts`, not a surprise global action.

## Data Flow: `/add`

```
User types /add in Telegram bot
  ↓
aiogram FSM asks for name, then phone
  ↓
messages.on_add_contact_data(name, phone):
  repo = ContactsRepo()

  async def send_welcome(phone_uazapi: str) -> None:
      ok = await send_whatsapp(phone_uazapi, WELCOME_MSG, draft_id="welcome")
      if not ok:
          raise RuntimeError("uazapi send returned non-OK")

  try:
    contact = repo.add(name, phone, send_welcome=send_welcome)
    → reply "✅ Adicionado: {name} ({phone_uazapi})"
  except InvalidPhoneError as e:
    → reply "❌ Telefone inválido: {e}. Inclua o DDI (ex: 55 Brasil, 1 EUA)."
  except ContactAlreadyExistsError as e:
    → reply "❌ Já existe: {e.existing.name} ({e.existing.status})"
  except RuntimeError as e:       # welcome send failed
    → reply "❌ Não consegui enviar a mensagem de boas-vindas — o número pode não ter WhatsApp."
```

Welcome message text: `"Você foi adicionado à lista de informações de mercado da Minerals Trading."`

## Data Flow: `/contacts` listing + bulk ops

Inline keyboard on the listing:

```
[ List of contacts on this page, each with 🔴/🟢 toggle button ]

[ ← Prev ]  [ page X / Y ]  [ Next → ]

[ ✅ Ativar todos ]  [ ❌ Desativar todos ]
```

Bulk button flow (two-step confirmation):

```
User taps [❌ Desativar todos]
  ↓
bot replies: "Confirma desativar 47 contatos? [✅ Sim] [❌ Cancelar]"
  ↓
User taps [✅ Sim]
  ↓
count = repo.bulk_set_status("inativo", search=current_search)
  ↓
bot replies: "✅ {count} contatos desativados."
```

- Scope is the **current filter** (`search` from the `/contacts` command). If user had no search, affects all contacts. If user searched `/contacts joão`, affects only the matching rows.
- Confirmation is mandatory — a two-tap flow prevents accidental mass-suppression.
- Deactivation never deletes. Rows persist with `status='inativo'` and can be reactivated with the same bulk op or row-by-row toggle.

## Data Flow: Workflow scripts & dispatch

Pattern for all four workflow scripts (`morning_check`, `send_news`, `send_daily_report`, `baltic_ingestion`):

```python
from execution.integrations.contacts_repo import ContactsRepo
from execution.core.state_store import try_claim_alert_key  # for daily-status

REPORT_TYPE = "MORNING_REPORT"     # or BALTIC_REPORT, etc.

def main():
    # Idempotency: replaces sheets.check_daily_status + mark_daily_status.
    claim_key = f"daily_report:sent:{REPORT_TYPE}:{date_str}"
    if not try_claim_alert_key(claim_key, ttl_seconds=48 * 3600):
        logger.info("Report already sent today. Exiting.")
        return

    contacts = ContactsRepo().list_active()
    for c in contacts:
        send_whatsapp(c.phone_uazapi, message, draft_id=draft_id)
```

For `webhook/dispatch.py`, the inline `_get_contacts_sync` is deleted. Callers of `get_contacts()` (lines 162, 290) become:

```python
contacts = await asyncio.to_thread(lambda: ContactsRepo().list_active())
```

## Migration Script

File: `scripts/migrate_contacts_from_sheets.py`

Responsibilities:
1. Read all rows from the sheet (including inactive).
2. For each row, derive `name` from `ProfileName`, pick `phone_raw` from the first non-empty of `Evolution-api`, `n8n-evo`, or `From` (stripping `whatsapp:` prefix where present).
3. Normalize phone with **migration-only BR-55 fallback**: if the cleaned number has 10 or 11 digits (BR local format without DDI), prepend `"55"`. This fallback lives **only in the migration script**, not in `normalize_phone`. The `/add` flow enforces explicit DDI going forward.
4. Map `ButtonPayload == "Big"` → `status='ativo'`; everything else → `status='inativo'`.
5. Upsert into `contacts` with `on_conflict='phone_uazapi', ignore_duplicates=True` — safe to re-run.
6. `--dry-run` mode prints what would happen without writing.
7. Skip invalid rows with a visible log line. Exit code 1 if any skips occurred, 0 if clean.

## Cutover Plan

| # | Action | Reversible |
|---|---|---|
| 1 | Apply migration `20260422_contacts.sql` via `supabase db push` | Drop table |
| 2 | `python scripts/migrate_contacts_from_sheets.py --dry-run` — review output | — |
| 3 | Fix any invalid rows in the sheet; re-dry-run until `skipped_invalid=0` | — |
| 4 | `python scripts/migrate_contacts_from_sheets.py` — real migration | `truncate contacts` |
| 5 | Sanity: `select count(*) from contacts` matches sheet row count | — |
| 6 | Deploy code changes (`ContactsRepo`, refactored callers, daily-status swap to Redis) | Revert PR |
| 7 | Monitor 24–48h: dashboard, bot `/add` and `/contacts`, next real runs of `morning_check` and `baltic_ingestion` | — |
| 8 | Separate cleanup PR: delete `sheets_client.py`, migration script, remove `gspread` dep (audit other users first) | Revert |

### Rollback at step 6

Revert the code PR. Callers go back to `SheetsClient`, which still reads from the sheet (data untouched). Empty-ish `contacts` table sits unused. Rollback cost: ~5 minutes.

### Post-migration state of the sheet

Kept as read-only historical backup. Nothing writes to it, nothing reads from it. If auditing is needed later, it remains.

## Testing

### Unit tests (no network)

- **`tests/test_contacts_repo_normalize.py`** — `normalize_phone` in isolation via `phonenumbers`:
  - `"+55 (11) 98765-4321"` → `"5511987654321"`
  - Valid number idempotent on re-normalize
  - Number missing DDI → `InvalidPhoneError`
  - Invalid country code → `InvalidPhoneError`
  - Length invalid for country → `InvalidPhoneError`
  - Empty / None / non-digit input → `InvalidPhoneError`

- **`tests/test_contacts_repo.py`** — `ContactsRepo` with fake Supabase client:
  - `list_active()` filters by `status='ativo'` and orders by `created_at desc`.
  - `list_all(search, page, per_page)` builds correct query, returns `(items, total_pages)`.
  - `add()` happy path: normalize → dup check → send_welcome called with canonical phone → insert → `Contact` returned.
  - `add()` with `send_welcome` raising: nothing inserted, error propagates as `RuntimeError`.
  - `add()` with duplicate `phone_uazapi`: raises `ContactAlreadyExistsError` (both pre-check path and post-insert race path via fake APIError).
  - `add()` with invalid phone: `send_welcome` NEVER called.
  - `toggle()` flips status; missing phone raises `ContactNotFoundError`.
  - `bulk_set_status("inativo")` without search: all rows updated. With `search="joão"`: only matching rows. Returns count.
  - Call-order assertion in `add()`: `send_welcome` invoked before `insert`.

- **`tests/test_migrate_contacts_from_sheets.py`** — migration script:
  - Row with explicit DDI preserved.
  - Row with 10 digits (BR landline) → `"55"` prefixed.
  - Row with 11 digits (BR mobile) → `"55"` prefixed.
  - Row with `From = "whatsapp:+5511..."` → prefix stripped, `"+"` removed.
  - Row with `ButtonPayload != "Big"` → `status='inativo'`.
  - Invalid rows skipped, counters increment, visible log.
  - `--dry-run` makes zero `upsert` calls.

### Integration tests (gated on `SUPABASE_TEST_URL`)

- **`tests/test_contacts_repo_integration.py`**:
  - Migration applies cleanly to empty schema.
  - Unique index blocks duplicate `phone_uazapi` insert.
  - `updated_at` trigger bumps on UPDATE.
  - RLS: anon key sees zero rows; service key sees all.
  - `CHECK (status in ('ativo','inativo'))` rejects invalid values.

### Bot + workflow regression tests

- **`tests/test_add_contact_flow.py`** (updates existing `test_sheets_contact_ops.py`):
  - `/add` with valid name + DDI → `repo.add` called, success reply.
  - `/add` missing DDI → invalid-phone reply with DDI hint.
  - `/add` duplicate → duplicate reply with existing contact info.
  - `/add` with welcome send failing → welcome-failed reply.
  - `/contacts` with search → `list_all(search=...)` called, formatted output.
  - Toggle callback → `toggle(phone)` called.
  - Bulk activate / deactivate with confirmation flow: first tap shows confirmation, second tap executes and reports count.
  - Bulk cancel → no state change.

- **`tests/test_workflow_idempotency.py`** (extends existing dispatch tests):
  - `morning_check.main()` with claim returning False → exits early, `list_active` never called.
  - `morning_check.main()` with claim ok + 3 active contacts → `send_whatsapp` called 3 times with `c.phone_uazapi`.
  - Second run same day → claim False, no resend.

### Smoke test checklist (post-deploy, in the PR description)

- [ ] `/add` with a new real number → welcome received + dashboard shows contact.
- [ ] `/contacts` → lists migrated contacts; pagination works.
- [ ] Toggle one test contact → status flips; reflected in dashboard.
- [ ] Bulk deactivate (on a search scope to limit blast radius) + reactivate → counts match.
- [ ] Manual `morning_check --dry-run` → logs contact count matching sheet.
- [ ] Next scheduled cron of `morning_check` → executes, Redis claim set, second trigger same day exits cleanly.

### Coverage target

80%+ on new modules (`contacts_repo.py`, `migrate_contacts_from_sheets.py`), per the project's global testing rules.

## Open Items for the Implementation Plan

These are explicit handoffs to the writing-plans phase — answers are not part of this design:

1. **`gspread` dep audit.** Before removing from `requirements.txt`, confirm nothing else in the repo imports it. If other code depends on it, remove only after those are migrated or excluded from this scope.
2. **Dashboard API path.** Decide whether `dashboard/app/api/contacts/route.ts` calls Supabase directly via `@supabase/supabase-js` server-side, or continues to proxy through the Python webhook API (which now reads Supabase). Either is fine; implementation plan picks one.
## Out of scope (explicit)

- Multi-list / tagging / segmentation.
- Opt-out / unsubscribe flows driven by recipients.
- Contact import from CSV.
- Historical metrics per contact (who received what when) — `event_log` already captures sends at the workflow level.
- Bulk activate/deactivate UI in the web dashboard. Bulk ops are Telegram-bot only in this design; if the dashboard needs the same buttons, it's a follow-up phase.

## References

- Existing Supabase patterns: `supabase/migrations/20260418_event_log.sql`, `20260419_event_log_rls.sql`.
- Existing Redis idempotency primitive: `execution/core/state_store.py:185` (`try_claim_alert_key`).
- Replaced code: `execution/integrations/sheets_client.py`, `webhook/dispatch.py:70-104`.
- Current sheet columns and semantics: documented in `docs/superpowers/specs/2026-04-14-contact-admin-design.md`.
