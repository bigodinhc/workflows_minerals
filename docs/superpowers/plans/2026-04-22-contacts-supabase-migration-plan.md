# Contacts: Google Sheets → Supabase Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Google Sheet used as the WhatsApp broadcast list with a first-class `contacts` table in Supabase, refactoring all consumers (4 workflow scripts, webhook dispatch, 3 bot routers, dashboard API) to use a clean `ContactsRepo`. Also retire the `Controle` sub-sheet by reusing the existing Redis idempotency primitive (`try_claim_alert_key`).

**Architecture:** New `contacts` table with clean schema (`name`, `phone_raw`, `phone_uazapi`, `status`). New `ContactsRepo` class returning `Contact` dataclass instances. `phonenumbers` library for input validation at `/add`. Test-send via uazapi as existence check during `/add`. Migration from existing sheet via one-off idempotent script. `Controle` sub-sheet logic replaced by `execution.core.state_store.try_claim_alert_key`. Bulk activate/deactivate via Telegram inline keyboard with two-step confirmation.

**Tech Stack:** Python 3.11, Supabase (postgres + pgREST via `supabase-py`), aiogram 3.x, Redis, pytest + pytest-mock + fakeredis, `phonenumbers` (new dep), `@supabase/supabase-js` in the Next.js dashboard (new dep).

**Related spec:** `docs/superpowers/specs/2026-04-22-contacts-supabase-migration-design.md` — read this before starting any task.

---

## File Structure

### Created

| Path | Responsibility |
|---|---|
| `supabase/migrations/20260422_contacts.sql` | Create `contacts` table, indexes, `updated_at` trigger, enable RLS. |
| `execution/integrations/contacts_repo.py` | `Contact` dataclass, `normalize_phone`, typed exceptions, `ContactsRepo` class. |
| `scripts/migrate_contacts_from_sheets.py` | One-off idempotent migration script with `--dry-run`. |
| `tests/test_contacts_repo_normalize.py` | Pure unit tests for `normalize_phone`. |
| `tests/test_contacts_repo.py` | Unit tests for `ContactsRepo` methods with a fake Supabase client. |
| `tests/test_migrate_contacts_from_sheets.py` | Unit tests for the migration script. |
| `tests/test_contacts_bulk_ops.py` | Bot integration tests for bulk activate/deactivate flow. |

### Modified

| Path | Change |
|---|---|
| `requirements.txt` | Add `phonenumbers>=8.13,<9.0`. |
| `execution/core/delivery_reporter.py` | Add `build_delivery_contact(contact: Contact) -> DeliveryContact` adapter; keep legacy `build_contact_from_row` until callers migrate. |
| `execution/scripts/morning_check.py` | Swap `SheetsClient` → `ContactsRepo`; swap `check/mark_daily_status` → `try_claim_alert_key`. |
| `execution/scripts/baltic_ingestion.py` | Same as morning_check. |
| `execution/scripts/send_news.py` | Swap `SheetsClient` → `ContactsRepo`. |
| `execution/scripts/send_daily_report.py` | Swap `SheetsClient` → `ContactsRepo`. |
| `webhook/dispatch.py` | Delete `_get_contacts_sync`; `get_contacts()` uses repo. |
| `webhook/contact_admin.py` | `build_list_keyboard` accepts `Contact` objects; add bulk buttons + confirmation keyboard. |
| `webhook/bot/callback_data.py` | Add `ContactBulk(action)` and `ContactBulkConfirm(action, search)` callback factories. |
| `webhook/bot/routers/commands.py` | `_render_list_view` uses repo. |
| `webhook/bot/routers/messages.py` | `on_add_contact_data` uses `repo.add` with welcome send. |
| `webhook/bot/routers/callbacks_contacts.py` | Toggle uses repo; add bulk handlers with confirmation. |
| `dashboard/app/api/contacts/route.ts` | Read from Supabase via `@supabase/supabase-js`. |
| `dashboard/package.json` | Add `@supabase/supabase-js`. |

### Deleted (Final-Cleanup Task)

| Path | Why |
|---|---|
| `execution/integrations/sheets_client.py` | Superseded by `ContactsRepo`. |
| `tests/test_sheets_contact_ops.py` | Tests gone client. |
| `requirements.txt` entries `gspread`, `google-auth` | No longer used after cleanup (audit required). |
| `SHEET_ID` constants in all 4 workflow scripts and `webhook/bot/config.py` | Unreferenced. |

---

## Environment Prerequisites

Before starting: ensure these env vars are set in local `.env` (or shell):
- `SUPABASE_URL` — already required for existing `SupabaseClient`.
- `SUPABASE_KEY` — service-role key. Already required.
- `REDIS_URL` — already required.
- `UAZAPI_URL`, `UAZAPI_TOKEN` — already required for dispatch.
- `GOOGLE_CREDENTIALS_JSON` — still needed for the one-off migration script and gets removed later.

---

## Task 1: Add `phonenumbers` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add phonenumbers to requirements.txt**

Open `requirements.txt` and add a new line under the existing deps (after `aiohttp>=3.9.0,<4.0`):

```
phonenumbers>=8.13,<9.0
```

- [ ] **Step 2: Install locally**

Run: `pip install -r requirements.txt`
Expected: `Successfully installed phonenumbers-X.Y.Z` (or `Requirement already satisfied`).

- [ ] **Step 3: Smoke-test the library**

Run:
```bash
python -c "import phonenumbers; p = phonenumbers.parse('+5511987654321', None); print(phonenumbers.is_valid_number(p), phonenumbers.format_number(p, phonenumbers.PhoneNumberFormat.E164))"
```
Expected output: `True +5511987654321`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore(deps): add phonenumbers for contact phone validation"
```

---

## Task 2: Create Supabase migration

**Files:**
- Create: `supabase/migrations/20260422_contacts.sql`

- [ ] **Step 1: Write the migration SQL**

Create `supabase/migrations/20260422_contacts.sql`:

```sql
-- Phase: contacts migration from Google Sheets to Supabase
-- Replaces: gspread reads of sheet 1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0 / 'Página1'
-- Consumers: execution/scripts/*, webhook/dispatch.py, webhook/bot/routers/*, dashboard/api/contacts

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

drop trigger if exists contacts_updated_at on contacts;
create trigger contacts_updated_at
  before update on contacts
  for each row execute function contacts_set_updated_at();

alter table contacts enable row level security;
-- No policies. Only service_role bypasses RLS; anon/public get zero access.

comment on table contacts is
  'WhatsApp broadcast list. Source of truth for all agentic workflows. '
  'Replaced Google Sheets (1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0) on 2026-04-22.';
comment on column contacts.phone_raw is
  'What the user typed at /add, before normalization. Audit trail.';
comment on column contacts.phone_uazapi is
  'Digits-only E.164 without +, ready for uazapi number field. e.g. 5511987654321';
comment on column contacts.status is
  'ativo = receives broadcasts. inativo = suppressed but preserved, never deleted.';
```

- [ ] **Step 2: Apply the migration to the dev/staging Supabase**

Run (from repo root):
```bash
supabase db push --dry-run
```
Expected: shows the planned SQL execution, no errors.

Then apply for real:
```bash
supabase db push
```
Expected: "Applied migration 20260422_contacts.sql".

*If the project does not use `supabase db push` locally, apply the SQL manually in the Supabase SQL editor of the dev project.*

- [ ] **Step 3: Verify the schema landed**

Run in Supabase SQL editor (or via psql):
```sql
\d contacts;
select indexname from pg_indexes where tablename = 'contacts';
select tgname from pg_trigger where tgrelid = 'contacts'::regclass;
select relrowsecurity from pg_class where relname = 'contacts';
```
Expected: table with 7 columns, 3 indexes (1 unique + 2 normal), 1 trigger (`contacts_updated_at`), `relrowsecurity=t`.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260422_contacts.sql
git commit -m "feat(db): add contacts table with RLS and updated_at trigger"
```

---

## Task 3: `normalize_phone` module with tests (TDD)

**Files:**
- Create: `tests/test_contacts_repo_normalize.py`
- Create: `execution/integrations/contacts_repo.py` (partial — only `normalize_phone` + exceptions)

- [ ] **Step 1: Write the failing test file**

Create `tests/test_contacts_repo_normalize.py`:

```python
"""Unit tests for normalize_phone (pure, no Supabase)."""
import pytest
from execution.integrations.contacts_repo import normalize_phone, InvalidPhoneError


def test_normalize_e164_brazilian_mobile():
    assert normalize_phone("+55 (11) 98765-4321") == "5511987654321"


def test_normalize_plain_digits_with_ddi():
    assert normalize_phone("5511987654321") == "5511987654321"


def test_normalize_idempotent():
    canonical = normalize_phone("+5511987654321")
    assert normalize_phone(canonical) == canonical


def test_normalize_us_number():
    assert normalize_phone("+1 415-555-2671") == "14155552671"


def test_normalize_rejects_empty():
    with pytest.raises(InvalidPhoneError):
        normalize_phone("")


def test_normalize_rejects_none():
    with pytest.raises(InvalidPhoneError):
        normalize_phone(None)


def test_normalize_rejects_letters_only():
    with pytest.raises(InvalidPhoneError):
        normalize_phone("abc")


def test_normalize_rejects_too_short():
    with pytest.raises(InvalidPhoneError):
        normalize_phone("12345")


def test_normalize_rejects_invalid_number_for_country():
    # country code 55 (BR) with obviously invalid national number
    with pytest.raises(InvalidPhoneError):
        normalize_phone("+5500000000000")


def test_normalize_preserves_plus_stripped_from_output():
    result = normalize_phone("+5511987654321")
    assert "+" not in result
    assert result.isdigit()
```

- [ ] **Step 2: Run tests to verify they fail with ImportError**

Run: `pytest tests/test_contacts_repo_normalize.py -v`
Expected: All tests fail because `execution.integrations.contacts_repo` does not exist yet.

- [ ] **Step 3: Create the module skeleton with `normalize_phone` + exceptions**

Create `execution/integrations/contacts_repo.py`:

```python
"""Repository for the Supabase `contacts` table.

Replaces execution/integrations/sheets_client.py. Consumers:
  - execution/scripts/{morning_check,send_news,send_daily_report,baltic_ingestion}.py
  - webhook/dispatch.py
  - webhook/bot/routers/{commands,messages,callbacks_contacts}.py
  - dashboard/app/api/contacts/route.ts (parallel TS implementation)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Callable, Optional

import phonenumbers


# ── Exceptions ──

class ContactNotFoundError(Exception):
    """No contact matches the given phone/id."""


class ContactAlreadyExistsError(Exception):
    """add() would create a duplicate phone_uazapi."""
    def __init__(self, existing: "Contact"):
        self.existing = existing
        super().__init__(
            f"Contact {existing.name!r} already exists "
            f"({existing.phone_uazapi}, status={existing.status})"
        )


class InvalidPhoneError(ValueError):
    """normalize_phone rejected the input."""


# ── Phone normalization ──

def normalize_phone(raw) -> str:
    """Parse and validate a user-supplied phone string, return the canonical
    uazapi-ready form: digits only, no '+', E.164 internally.

    Uses Google's libphonenumber via the `phonenumbers` library.

    Raises:
      InvalidPhoneError: empty input, unparseable, or not a valid number.
    """
    if raw is None:
        raise InvalidPhoneError("phone is empty")
    s = str(raw).strip()
    if not s:
        raise InvalidPhoneError("phone is empty")
    if not any(c.isdigit() for c in s):
        raise InvalidPhoneError("phone must contain digits")

    # Ensure leading '+' so libphonenumber can detect the country code.
    digits_and_plus = re.sub(r"[^\d+]", "", s)
    if not digits_and_plus.startswith("+"):
        digits_and_plus = "+" + digits_and_plus

    try:
        parsed = phonenumbers.parse(digits_and_plus, None)
    except phonenumbers.NumberParseException as e:
        raise InvalidPhoneError(f"could not parse phone: {e}") from e

    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhoneError(
            "not a valid phone number — include DDI (e.g. 55 Brazil, 1 US)"
        )

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return e164.lstrip("+")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_contacts_repo_normalize.py -v`
Expected: All 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add execution/integrations/contacts_repo.py tests/test_contacts_repo_normalize.py
git commit -m "feat(contacts): add normalize_phone using phonenumbers library"
```

---

## Task 4: `Contact` dataclass + `ContactsRepo` read methods (TDD)

**Files:**
- Create: `tests/test_contacts_repo.py`
- Modify: `execution/integrations/contacts_repo.py`

- [ ] **Step 1: Write failing tests for Contact dataclass and read methods**

Create `tests/test_contacts_repo.py`:

```python
"""Unit tests for ContactsRepo using a fake Supabase client."""
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from execution.integrations.contacts_repo import (
    Contact, ContactsRepo, ContactNotFoundError,
    ContactAlreadyExistsError, InvalidPhoneError,
)


def _row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000001",
        "name": "Alice",
        "phone_raw": "+5511987654321",
        "phone_uazapi": "5511987654321",
        "status": "ativo",
        "created_at": "2026-04-22T10:00:00+00:00",
        "updated_at": "2026-04-22T10:00:00+00:00",
    }
    base.update(overrides)
    return base


class FakeQuery:
    """Minimal chainable builder: mirrors supabase-py's PostgrestBuilder."""
    def __init__(self, data, count=None):
        self._data = data
        self._count = count
        self.calls = []

    def select(self, *a, **kw): self.calls.append(("select", a, kw)); return self
    def eq(self, *a, **kw):     self.calls.append(("eq", a, kw)); return self
    def ilike(self, *a, **kw):  self.calls.append(("ilike", a, kw)); return self
    def neq(self, *a, **kw):    self.calls.append(("neq", a, kw)); return self
    def order(self, *a, **kw):  self.calls.append(("order", a, kw)); return self
    def range(self, *a, **kw):  self.calls.append(("range", a, kw)); return self
    def limit(self, *a, **kw):  self.calls.append(("limit", a, kw)); return self
    def insert(self, *a, **kw): self.calls.append(("insert", a, kw)); return self
    def update(self, *a, **kw): self.calls.append(("update", a, kw)); return self
    def upsert(self, *a, **kw): self.calls.append(("upsert", a, kw)); return self

    def execute(self):
        r = MagicMock()
        r.data = self._data
        r.count = self._count
        return r


@pytest.fixture
def fake_client():
    client = MagicMock()
    client._queries = []
    return client


def _set_next_query(client, query: FakeQuery):
    """Configure client.table(...) to return `query` on the next call."""
    client.table.return_value = query
    client._queries.append(query)


def test_contact_is_active_true_for_ativo():
    c = Contact(
        id="x", name="A", phone_raw="+1", phone_uazapi="1",
        status="ativo", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert c.is_active() is True


def test_contact_is_active_false_for_inativo():
    c = Contact(
        id="x", name="A", phone_raw="+1", phone_uazapi="1",
        status="inativo", created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert c.is_active() is False


def test_list_active_filters_by_status_and_orders(fake_client):
    q = FakeQuery([_row(), _row(name="Bob")])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts = repo.list_active()

    assert len(contacts) == 2
    assert contacts[0].name == "Alice"
    # must have: select, eq(status=ativo), order(created_at desc)
    ops = [c[0] for c in q.calls]
    assert ops == ["select", "eq", "order"]
    assert q.calls[1] == ("eq", ("status", "ativo"), {})
    assert q.calls[2] == ("order", ("created_at",), {"desc": True})


def test_list_all_no_search(fake_client):
    q = FakeQuery([_row() for _ in range(3)], count=3)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts, total_pages = repo.list_all(page=1, per_page=10)

    assert len(contacts) == 3
    assert total_pages == 1
    ops = [c[0] for c in q.calls]
    assert "ilike" not in ops
    assert ("range", (0, 9), {}) in q.calls


def test_list_all_with_search_uses_ilike(fake_client):
    q = FakeQuery([_row(name="Joao")], count=1)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts, _ = repo.list_all(search="joao", page=1, per_page=10)

    assert len(contacts) == 1
    assert ("ilike", ("name", "%joao%"), {}) in q.calls


def test_list_all_computes_total_pages(fake_client):
    q = FakeQuery([_row() for _ in range(10)], count=25)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    _, total_pages = repo.list_all(page=1, per_page=10)

    assert total_pages == 3  # ceil(25/10)


def test_list_all_pagination_page_3(fake_client):
    q = FakeQuery([_row()], count=25)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    repo.list_all(page=3, per_page=10)

    assert ("range", (20, 29), {}) in q.calls


def test_list_all_empty(fake_client):
    q = FakeQuery([], count=0)
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    contacts, total_pages = repo.list_all()

    assert contacts == []
    assert total_pages == 0


def test_get_by_phone_normalizes_input(fake_client):
    q = FakeQuery([_row()])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    c = repo.get_by_phone("+55 (11) 98765-4321")

    assert c.phone_uazapi == "5511987654321"
    # must have eq(phone_uazapi, <canonical>)
    assert ("eq", ("phone_uazapi", "5511987654321"), {}) in q.calls


def test_get_by_phone_raises_when_missing(fake_client):
    q = FakeQuery([])
    _set_next_query(fake_client, q)

    repo = ContactsRepo(client=fake_client)
    with pytest.raises(ContactNotFoundError):
        repo.get_by_phone("+5511000000000")


def test_get_by_phone_invalid_input_raises_invalid_phone(fake_client):
    repo = ContactsRepo(client=fake_client)
    with pytest.raises(InvalidPhoneError):
        repo.get_by_phone("abc")
```

- [ ] **Step 2: Run tests — expect failures**

Run: `pytest tests/test_contacts_repo.py -v`
Expected: All tests fail with `ImportError` (Contact/ContactsRepo not yet defined).

- [ ] **Step 3: Extend `contacts_repo.py` with `Contact` dataclass and read methods**

Append to `execution/integrations/contacts_repo.py` (after existing content):

```python
# ── Contact model ──

@dataclass(frozen=True)
class Contact:
    id: str
    name: str
    phone_raw: str
    phone_uazapi: str
    status: str             # 'ativo' | 'inativo'
    created_at: datetime
    updated_at: datetime

    def is_active(self) -> bool:
        return self.status == "ativo"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d


# ── Repository ──

class ContactsRepo:
    def __init__(self, client=None):
        if client is not None:
            self.client = client
        else:
            from supabase import create_client
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_KEY")
            if not url or not key:
                raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
            self.client = create_client(url, key)

    # ---- Reads ----

    def list_active(self) -> list[Contact]:
        """Contacts receiving broadcasts. Ordered by created_at desc."""
        resp = (
            self.client.table("contacts")
            .select("*")
            .eq("status", "ativo")
            .order("created_at", desc=True)
            .execute()
        )
        return [self._row_to_contact(r) for r in (resp.data or [])]

    def list_all(
        self,
        *,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 10,
    ) -> tuple[list[Contact], int]:
        """Paginated admin list, optional name search (ILIKE).
        Returns (rows_on_page, total_pages)."""
        import math
        q = self.client.table("contacts").select("*", count="exact")
        if search:
            q = q.ilike("name", f"%{search}%")
        start = (page - 1) * per_page
        end = start + per_page - 1
        resp = q.order("created_at", desc=True).range(start, end).execute()
        total = resp.count or 0
        total_pages = math.ceil(total / per_page) if total else 0
        return [self._row_to_contact(r) for r in (resp.data or [])], total_pages

    def get_by_phone(self, phone: str) -> Contact:
        """Lookup by phone (accepts any format; normalizes internally)."""
        canonical = normalize_phone(phone)
        resp = (
            self.client.table("contacts")
            .select("*")
            .eq("phone_uazapi", canonical)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            raise ContactNotFoundError(f"no contact with phone {canonical}")
        return self._row_to_contact(rows[0])

    # ---- Internal ----

    @staticmethod
    def _row_to_contact(r: dict) -> Contact:
        return Contact(
            id=r["id"],
            name=r["name"],
            phone_raw=r["phone_raw"],
            phone_uazapi=r["phone_uazapi"],
            status=r["status"],
            created_at=_parse_ts(r["created_at"]),
            updated_at=_parse_ts(r["updated_at"]),
        )


def _parse_ts(s: str) -> datetime:
    """Parse Supabase ISO timestamp (may end with 'Z' or '+00:00')."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_contacts_repo.py -v`
Expected: All 10 tests pass.

- [ ] **Step 5: Commit**

```bash
git add execution/integrations/contacts_repo.py tests/test_contacts_repo.py
git commit -m "feat(contacts): Contact dataclass and ContactsRepo read methods"
```

---

## Task 5: `ContactsRepo` write methods — add, toggle, bulk_set_status (TDD)

**Files:**
- Modify: `tests/test_contacts_repo.py`
- Modify: `execution/integrations/contacts_repo.py`

- [ ] **Step 1: Append write-method tests to `tests/test_contacts_repo.py`**

Append to `tests/test_contacts_repo.py`:

```python
# ── Write tests ──

class FakeWelcomeRecorder:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    def __call__(self, phone_uazapi: str):
        self.calls.append(phone_uazapi)
        if self.fail:
            raise RuntimeError("uazapi send failed")


def test_add_happy_path_sends_welcome_then_inserts(fake_client):
    # 1st query: dup pre-check (empty), 2nd: insert (returns row)
    dup_q = FakeQuery([])
    insert_q = FakeQuery([_row(name="Carol", phone_uazapi="5511900000001")])

    fake_client.table.side_effect = [dup_q, insert_q]

    welcome = FakeWelcomeRecorder()
    repo = ContactsRepo(client=fake_client)

    contact = repo.add("Carol", "+55 11 90000-0001", send_welcome=welcome)

    assert contact.name == "Carol"
    assert contact.phone_uazapi == "5511900000001"
    assert welcome.calls == ["5511900000001"]

    # Insert query must be an insert with status=ativo
    op, args, _ = insert_q.calls[0]
    assert op == "insert"
    payload = args[0]
    assert payload["name"] == "Carol"
    assert payload["phone_uazapi"] == "5511900000001"
    assert payload["status"] == "ativo"


def test_add_invalid_phone_never_sends_welcome(fake_client):
    welcome = FakeWelcomeRecorder()
    repo = ContactsRepo(client=fake_client)

    with pytest.raises(InvalidPhoneError):
        repo.add("Carol", "abc", send_welcome=welcome)

    assert welcome.calls == []
    fake_client.table.assert_not_called()


def test_add_duplicate_pre_check_raises_and_skips_send(fake_client):
    dup_q = FakeQuery([_row(name="Alice Existing")])
    _set_next_query(fake_client, dup_q)

    welcome = FakeWelcomeRecorder()
    repo = ContactsRepo(client=fake_client)

    with pytest.raises(ContactAlreadyExistsError) as exc_info:
        repo.add("Alice", "+5511987654321", send_welcome=welcome)

    assert exc_info.value.existing.name == "Alice Existing"
    assert welcome.calls == []


def test_add_welcome_failure_rolls_back_insert(fake_client):
    # pre-check: no dup
    dup_q = FakeQuery([])
    _set_next_query(fake_client, dup_q)

    welcome = FakeWelcomeRecorder(fail=True)
    repo = ContactsRepo(client=fake_client)

    with pytest.raises(RuntimeError, match="welcome send failed"):
        repo.add("Carol", "+5511900000002", send_welcome=welcome)

    # Only the pre-check should have been issued — no insert.
    assert len(fake_client._queries) == 1
    assert "insert" not in [c[0] for c in dup_q.calls]


def test_add_send_welcome_called_before_insert(fake_client):
    """Ordering guarantee: welcome send must precede DB insert."""
    call_order = []

    dup_q = FakeQuery([])
    insert_q = FakeQuery([_row()])
    fake_client.table.side_effect = [dup_q, insert_q]

    def welcome(p):
        call_order.append("welcome")

    # Patch insert to record order
    orig_insert = insert_q.insert
    def tracked_insert(*a, **kw):
        call_order.append("insert")
        return orig_insert(*a, **kw)
    insert_q.insert = tracked_insert

    repo = ContactsRepo(client=fake_client)
    repo.add("Alice", "+5511987654321", send_welcome=welcome)

    assert call_order == ["welcome", "insert"]


def test_toggle_flips_ativo_to_inativo(fake_client):
    get_q = FakeQuery([_row(status="ativo")])
    update_q = FakeQuery([_row(status="inativo")])
    fake_client.table.side_effect = [get_q, update_q]

    repo = ContactsRepo(client=fake_client)
    updated = repo.toggle("+5511987654321")

    assert updated.status == "inativo"
    op, args, _ = update_q.calls[0]
    assert op == "update"
    assert args[0] == {"status": "inativo"}


def test_toggle_flips_inativo_to_ativo(fake_client):
    get_q = FakeQuery([_row(status="inativo")])
    update_q = FakeQuery([_row(status="ativo")])
    fake_client.table.side_effect = [get_q, update_q]

    repo = ContactsRepo(client=fake_client)
    updated = repo.toggle("+5511987654321")

    assert updated.status == "ativo"


def test_toggle_raises_on_missing_phone(fake_client):
    get_q = FakeQuery([])
    _set_next_query(fake_client, get_q)

    repo = ContactsRepo(client=fake_client)
    with pytest.raises(ContactNotFoundError):
        repo.toggle("+5511000000000")


def test_bulk_set_status_no_search_affects_all(fake_client):
    update_q = FakeQuery([_row(), _row(), _row()])  # 3 rows updated
    _set_next_query(fake_client, update_q)

    repo = ContactsRepo(client=fake_client)
    count = repo.bulk_set_status("inativo")

    assert count == 3
    ops = [c[0] for c in update_q.calls]
    assert "update" in ops
    assert "ilike" not in ops


def test_bulk_set_status_with_search_filters(fake_client):
    update_q = FakeQuery([_row(name="Joao")])
    _set_next_query(fake_client, update_q)

    repo = ContactsRepo(client=fake_client)
    count = repo.bulk_set_status("ativo", search="joao")

    assert count == 1
    assert ("ilike", ("name", "%joao%"), {}) in update_q.calls


def test_bulk_set_status_rejects_invalid_status(fake_client):
    repo = ContactsRepo(client=fake_client)
    with pytest.raises(ValueError, match="invalid status"):
        repo.bulk_set_status("banido")
```

- [ ] **Step 2: Run tests — expect failures**

Run: `pytest tests/test_contacts_repo.py -v`
Expected: Only the new 11 tests fail (methods not yet implemented).

- [ ] **Step 3: Append write methods to `contacts_repo.py`**

Append to `execution/integrations/contacts_repo.py` (inside the `ContactsRepo` class, after `get_by_phone`):

```python
    # ---- Writes ----

    def add(
        self,
        name: str,
        phone_raw: str,
        *,
        send_welcome: Callable[[str], None],
    ) -> Contact:
        """Add a contact after validating the phone and dispatching a welcome
        message via the injected `send_welcome` callable.

        Flow: normalize → duplicate pre-check → send_welcome → insert.

        Raises:
          ValueError: if `name` is empty.
          InvalidPhoneError: if the phone cannot be normalized.
          ContactAlreadyExistsError: if phone_uazapi already present
            (pre-check or post-insert unique-violation race).
          RuntimeError: if `send_welcome` raises (wraps its exception).
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("name is empty")
        canonical = normalize_phone(phone_raw)

        # Duplicate pre-check — avoid sending welcome to someone on the list.
        try:
            existing = self.get_by_phone(canonical)
            raise ContactAlreadyExistsError(existing)
        except ContactNotFoundError:
            pass

        try:
            send_welcome(canonical)
        except Exception as e:
            raise RuntimeError(f"welcome send failed: {e}") from e

        # Insert. Unique index catches race conditions.
        try:
            resp = (
                self.client.table("contacts")
                .insert({
                    "name": name,
                    "phone_raw": phone_raw,
                    "phone_uazapi": canonical,
                    "status": "ativo",
                })
                .execute()
            )
        except Exception as e:
            if "duplicate key" in str(e).lower():
                existing = self.get_by_phone(canonical)
                raise ContactAlreadyExistsError(existing) from e
            raise
        return self._row_to_contact(resp.data[0])

    def toggle(self, phone: str) -> Contact:
        """Flip status ativo ↔ inativo. Raises ContactNotFoundError."""
        current = self.get_by_phone(phone)
        new_status = "inativo" if current.is_active() else "ativo"
        resp = (
            self.client.table("contacts")
            .update({"status": new_status})
            .eq("id", current.id)
            .execute()
        )
        return self._row_to_contact(resp.data[0])

    def bulk_set_status(
        self,
        status: str,
        *,
        search: Optional[str] = None,
    ) -> int:
        """Set status on all matching contacts. Returns count of rows updated.

        If `search` is None, affects ALL rows.
        If provided, affects only rows where name ILIKE %search%.
        """
        if status not in ("ativo", "inativo"):
            raise ValueError(f"invalid status: {status!r}")
        q = self.client.table("contacts").update({"status": status})
        if search:
            q = q.ilike("name", f"%{search}%")
        else:
            # postgrest update requires a filter; pick a tautology.
            q = q.neq("id", "00000000-0000-0000-0000-000000000000")
        resp = q.execute()
        return len(resp.data or [])
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/test_contacts_repo.py -v`
Expected: All 21 tests pass (10 from Task 4 + 11 new).

- [ ] **Step 5: Commit**

```bash
git add execution/integrations/contacts_repo.py tests/test_contacts_repo.py
git commit -m "feat(contacts): add/toggle/bulk_set_status write methods"
```

---

## Task 6: Migration script `migrate_contacts_from_sheets.py` (TDD)

**Files:**
- Create: `scripts/migrate_contacts_from_sheets.py`
- Create: `tests/test_migrate_contacts_from_sheets.py`

Note: `scripts/` directory may not exist. Create it if needed:

- [ ] **Step 1: Ensure `scripts/` directory exists**

Run: `mkdir -p scripts && touch scripts/__init__.py`

- [ ] **Step 2: Write failing tests**

Create `tests/test_migrate_contacts_from_sheets.py`:

```python
"""Unit tests for the one-off Google Sheets → Supabase migration script."""
from unittest.mock import MagicMock, patch
import pytest

from scripts.migrate_contacts_from_sheets import (
    _pick_phone_raw,
    _normalize_for_migration,
    _row_to_payload,
)
from execution.integrations.contacts_repo import InvalidPhoneError


# ── _pick_phone_raw ──

def test_pick_phone_prefers_evolution_api():
    row = {"Evolution-api": "5511999", "n8n-evo": "x@s.whatsapp.net", "From": "whatsapp:+y"}
    assert _pick_phone_raw(row) == "5511999"


def test_pick_phone_falls_back_to_n8n_evo():
    row = {"Evolution-api": "", "n8n-evo": "5511888@s.whatsapp.net", "From": ""}
    assert _pick_phone_raw(row) == "5511888@s.whatsapp.net"


def test_pick_phone_falls_back_to_from_stripping_prefix():
    row = {"Evolution-api": "", "n8n-evo": "", "From": "whatsapp:+5511777"}
    assert _pick_phone_raw(row) == "+5511777"


def test_pick_phone_returns_none_when_all_empty():
    row = {"Evolution-api": "", "n8n-evo": "", "From": ""}
    assert _pick_phone_raw(row) is None


# ── _normalize_for_migration ──

def test_normalize_migration_preserves_explicit_ddi():
    assert _normalize_for_migration("+5511987654321") == "5511987654321"


def test_normalize_migration_br_fallback_11_digits():
    # BR mobile without DDI — 11 digits
    assert _normalize_for_migration("11987654321") == "5511987654321"


def test_normalize_migration_br_fallback_10_digits():
    # BR landline without DDI — 10 digits. Use a realistic one.
    result = _normalize_for_migration("1133334444")
    assert result.startswith("55")
    assert result.endswith("1133334444")


def test_normalize_migration_strips_whatsapp_prefix():
    assert _normalize_for_migration("whatsapp:+5511987654321") == "5511987654321"


def test_normalize_migration_strips_jid_suffix():
    assert _normalize_for_migration("5511987654321@s.whatsapp.net") == "5511987654321"


def test_normalize_migration_rejects_garbage():
    with pytest.raises(InvalidPhoneError):
        _normalize_for_migration("abc")


# ── _row_to_payload ──

def test_row_to_payload_active():
    row = {
        "ProfileName": "Alice",
        "Evolution-api": "5511987654321",
        "ButtonPayload": "Big",
    }
    payload = _row_to_payload(row)
    assert payload == {
        "name": "Alice",
        "phone_raw": "5511987654321",
        "phone_uazapi": "5511987654321",
        "status": "ativo",
    }


def test_row_to_payload_inactive():
    row = {
        "ProfileName": "Bob",
        "Evolution-api": "5511900000000",
        "ButtonPayload": "Inactive",
    }
    payload = _row_to_payload(row)
    assert payload["status"] == "inativo"


def test_row_to_payload_missing_button_payload_is_inactive():
    row = {"ProfileName": "Bob", "Evolution-api": "5511900000000"}
    payload = _row_to_payload(row)
    assert payload["status"] == "inativo"


def test_row_to_payload_returns_none_when_no_phone():
    row = {"ProfileName": "No Phone", "ButtonPayload": "Big"}
    assert _row_to_payload(row) is None


def test_row_to_payload_returns_none_when_no_name():
    row = {"ProfileName": "", "Evolution-api": "5511987654321"}
    assert _row_to_payload(row) is None


def test_row_to_payload_returns_none_on_invalid_phone():
    row = {"ProfileName": "Bad", "Evolution-api": "abc"}
    assert _row_to_payload(row) is None


# ── End-to-end integration of main() ──

def test_main_dry_run_does_not_upsert(capsys):
    fake_sheets = MagicMock()
    fake_sheets.list_contacts.return_value = (
        [
            {"ProfileName": "Alice", "Evolution-api": "5511987654321", "ButtonPayload": "Big"},
            {"ProfileName": "Bob",   "Evolution-api": "5511900000000", "ButtonPayload": "Inactive"},
        ],
        1,
    )
    fake_repo = MagicMock()
    fake_repo.client.table.return_value.upsert.return_value.execute.return_value.data = [{"id": "x"}]

    with patch("scripts.migrate_contacts_from_sheets.SheetsClient", return_value=fake_sheets), \
         patch("scripts.migrate_contacts_from_sheets.ContactsRepo", return_value=fake_repo):
        from scripts.migrate_contacts_from_sheets import main
        exit_code = main(["--dry-run"])

    assert exit_code == 0
    fake_repo.client.table.return_value.upsert.assert_not_called()
    out = capsys.readouterr().out
    assert "WOULD INSERT" in out


def test_main_real_run_calls_upsert():
    fake_sheets = MagicMock()
    fake_sheets.list_contacts.return_value = (
        [{"ProfileName": "Alice", "Evolution-api": "5511987654321", "ButtonPayload": "Big"}],
        1,
    )
    fake_repo = MagicMock()
    # Upsert returns data on insert, None/empty on conflict-skip
    fake_repo.client.table.return_value.upsert.return_value.execute.return_value.data = [{"id": "x"}]

    with patch("scripts.migrate_contacts_from_sheets.SheetsClient", return_value=fake_sheets), \
         patch("scripts.migrate_contacts_from_sheets.ContactsRepo", return_value=fake_repo):
        from scripts.migrate_contacts_from_sheets import main
        exit_code = main([])

    assert exit_code == 0
    fake_repo.client.table.return_value.upsert.assert_called_once()
    call_kwargs = fake_repo.client.table.return_value.upsert.call_args.kwargs
    assert call_kwargs.get("on_conflict") == "phone_uazapi"
    assert call_kwargs.get("ignore_duplicates") is True
```

- [ ] **Step 3: Run tests to verify failures**

Run: `pytest tests/test_migrate_contacts_from_sheets.py -v`
Expected: All tests fail with `ModuleNotFoundError`.

- [ ] **Step 4: Create the migration script**

Create `scripts/migrate_contacts_from_sheets.py`:

```python
#!/usr/bin/env python3
"""One-off migration: Google Sheets → Supabase `contacts` table.

Safe to re-run. Idempotent via ON CONFLICT (phone_uazapi) DO NOTHING.

Usage:
  python scripts/migrate_contacts_from_sheets.py --dry-run
  python scripts/migrate_contacts_from_sheets.py
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

from execution.integrations.sheets_client import SheetsClient
from execution.integrations.contacts_repo import (
    ContactsRepo, normalize_phone, InvalidPhoneError,
)


SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
SHEET_NAME = "Página1"


def _pick_phone_raw(row: dict) -> Optional[str]:
    """Return the first non-empty phone from columns in priority order.
    Strips 'whatsapp:' prefix only from the From column (the one that has it)."""
    for key in ("Evolution-api", "n8n-evo", "From"):
        v = str(row.get(key, "") or "").strip()
        if not v:
            continue
        if key == "From":
            v = v.replace("whatsapp:", "").strip()
        return v
    return None


def _normalize_for_migration(phone_raw: str) -> str:
    """Migration-only normalizer.

    1. Strip 'whatsapp:' prefix and '@s.whatsapp.net' suffix.
    2. If cleaned digits are 10 or 11 (BR local format without DDI), prepend '55'.
    3. Fall through to phonenumbers-based normalize_phone for final validation.

    This BR fallback lives ONLY in the migration script. The /add flow enforces
    explicit DDI via normalize_phone directly.
    """
    s = str(phone_raw).strip()
    s = s.replace("whatsapp:", "").replace("@s.whatsapp.net", "")
    digits_only = re.sub(r"\D", "", s)
    if len(digits_only) in (10, 11):
        digits_only = "55" + digits_only
    return normalize_phone(digits_only)


def _row_to_payload(row: dict) -> Optional[dict]:
    """Convert a sheet row to a contacts-table insert payload.
    Returns None if the row is unusable (no phone, no name, or invalid phone)."""
    name = str(row.get("ProfileName", "") or "").strip()
    if not name:
        return None
    phone_raw = _pick_phone_raw(row)
    if not phone_raw:
        return None
    try:
        phone_uazapi = _normalize_for_migration(phone_raw)
    except InvalidPhoneError:
        return None
    button_payload = str(row.get("ButtonPayload", "") or "").strip()
    status = "ativo" if button_payload == "Big" else "inativo"
    return {
        "name": name,
        "phone_raw": phone_raw,
        "phone_uazapi": phone_uazapi,
        "status": status,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned inserts without writing.")
    args = parser.parse_args(argv)

    sheets = SheetsClient()
    repo = ContactsRepo()
    rows, _ = sheets.list_contacts(SHEET_ID, sheet_name=SHEET_NAME, per_page=10_000)

    inserted = skipped_invalid = skipped_dup = 0

    for r in rows:
        payload = _row_to_payload(r)
        if payload is None:
            print(f"SKIP (invalid): {r!r}")
            skipped_invalid += 1
            continue

        if args.dry_run:
            print(f"WOULD INSERT: {payload['name']!r} / {payload['phone_uazapi']} / {payload['status']}")
            inserted += 1
            continue

        resp = repo.client.table("contacts").upsert(
            payload,
            on_conflict="phone_uazapi",
            ignore_duplicates=True,
        ).execute()
        if resp.data:
            inserted += 1
            print(f"OK: {payload['name']!r} / {payload['phone_uazapi']} / {payload['status']}")
        else:
            skipped_dup += 1
            print(f"DUP: {payload['name']!r} / {payload['phone_uazapi']}")

    print(
        f"\n{'DRY RUN ' if args.dry_run else ''}RESULT: "
        f"inserted={inserted} skipped_invalid={skipped_invalid} skipped_dup={skipped_dup}"
    )
    return 0 if skipped_invalid == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify all pass**

Run: `pytest tests/test_migrate_contacts_from_sheets.py -v`
Expected: All 14 tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py scripts/migrate_contacts_from_sheets.py tests/test_migrate_contacts_from_sheets.py
git commit -m "feat(contacts): migration script with BR-DDI fallback and --dry-run"
```

---

## Task 7: Run the real migration (operational, no code change)

This task does not change code. It moves data.

- [ ] **Step 1: Dry-run against production sheet**

Run (with production `GOOGLE_CREDENTIALS_JSON` and `SUPABASE_URL`/`SUPABASE_KEY` set):

```bash
python scripts/migrate_contacts_from_sheets.py --dry-run
```

Expected output pattern:
```
WOULD INSERT: 'Alice' / 5511987654321 / ativo
WOULD INSERT: 'Bob' / 5511900000000 / inativo
...
DRY RUN RESULT: inserted=N skipped_invalid=0 skipped_dup=0
```

- [ ] **Step 2: Resolve any `skipped_invalid` rows**

If `skipped_invalid > 0`:
  - Review the `SKIP (invalid):` lines in the output.
  - Fix those rows in the source sheet (add DDI, correct digits).
  - Re-run dry-run until `skipped_invalid=0`.

- [ ] **Step 3: Real run**

Run:
```bash
python scripts/migrate_contacts_from_sheets.py
```

Expected: `RESULT: inserted=N skipped_invalid=0 skipped_dup=0` (first run).

- [ ] **Step 4: Sanity check**

Run in Supabase SQL editor:
```sql
select count(*) from contacts;
select count(*) from contacts where status = 'ativo';
select count(*) from contacts where status = 'inativo';
```

Cross-reference `count(*) where status='ativo'` with the number of `ButtonPayload='Big'` rows in the sheet.

- [ ] **Step 5: Verify idempotency**

Re-run `python scripts/migrate_contacts_from_sheets.py` and confirm output shows `skipped_dup=N, inserted=0` (all rows deduped).

- [ ] **Step 6: No commit — this task is operational.**

---

## Task 8: Adapter in `delivery_reporter.py` for new Contact type

**Files:**
- Modify: `execution/core/delivery_reporter.py`

- [ ] **Step 1: Write the failing test**

Create a new section in `tests/test_delivery_reporter.py` (file may exist; if not create with just this test):

```python
# tests/test_delivery_reporter.py — append (or create)
from execution.integrations.contacts_repo import Contact as RepoContact
from execution.core.delivery_reporter import build_delivery_contact, Contact as DeliveryContact
from datetime import datetime, timezone


def test_build_delivery_contact_from_repo_contact():
    repo_c = RepoContact(
        id="x", name="Alice", phone_raw="+5511987654321",
        phone_uazapi="5511987654321", status="ativo",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    dc = build_delivery_contact(repo_c)
    assert isinstance(dc, DeliveryContact)
    assert dc.name == "Alice"
    assert dc.phone == "5511987654321"
```

- [ ] **Step 2: Run test — expect failure**

Run: `pytest tests/test_delivery_reporter.py::test_build_delivery_contact_from_repo_contact -v`
Expected: `ImportError: cannot import name 'build_delivery_contact'`.

- [ ] **Step 3: Add the adapter**

In `execution/core/delivery_reporter.py`, after the existing `build_contact_from_row` function (around line 319), append:

```python
def build_delivery_contact(contact) -> Contact:
    """Adapter: convert a contacts_repo.Contact into a DeliveryReporter.Contact.
    Accepts any object with `name` and `phone_uazapi` attributes (duck typed)."""
    return Contact(name=contact.name, phone=contact.phone_uazapi)
```

- [ ] **Step 4: Run test — pass**

Run: `pytest tests/test_delivery_reporter.py::test_build_delivery_contact_from_repo_contact -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "feat(delivery): add build_delivery_contact adapter for repo Contact"
```

---

## Task 9: Refactor `morning_check.py` to use `ContactsRepo` + Redis idempotency

**Files:**
- Modify: `execution/scripts/morning_check.py`

Read the current file first: `/Users/bigode/Dev/agentics_workflows/execution/scripts/morning_check.py`

- [ ] **Step 1: Update the imports (top of file, around lines 16-20)**

Replace:
```python
from execution.integrations.sheets_client import SheetsClient
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
```

With:
```python
from execution.integrations.contacts_repo import ContactsRepo
from execution.core.delivery_reporter import DeliveryReporter, build_delivery_contact
from execution.core import state_store
```

- [ ] **Step 2: Remove now-unused `SHEET_ID` and `SHEET_NAME_CONTACTS` constants**

Delete lines 72-73:
```python
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
SHEET_NAME_CONTACTS = "Página1"
```

- [ ] **Step 3: Replace the daily-status check block (around line 210-217)**

Find the block (inside `main()` around line 210):
```python
        # 2. Check Control Sheet
        sheets = SheetsClient()

        if not args.dry_run:
            if sheets.check_daily_status(SHEET_ID, date_str, REPORT_TYPE):
                logger.info("Report already sent today. Exiting.")
                progress.finish_empty("report ja enviado hoje")
                return
```

Replace with:
```python
        # 2. Idempotency claim via Redis (48h TTL — report is daily).
        if not args.dry_run:
            claim_key = f"daily_report:sent:{REPORT_TYPE}:{date_str}"
            if not state_store.try_claim_alert_key(claim_key, ttl_seconds=48 * 3600):
                logger.info("Report already sent today. Exiting.")
                progress.finish_empty("report ja enviado hoje")
                return
```

- [ ] **Step 4: Replace the contacts fetch block (around line 258)**

Find:
```python
        contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME_CONTACTS)
```

Replace with:
```python
        contacts_repo = ContactsRepo()
        contacts = contacts_repo.list_active()
```

- [ ] **Step 5: Replace the `build_contact_from_row` adapter usage (around line 276)**

Find:
```python
        delivery_contacts = [bc for c in contacts if (bc := build_contact_from_row(c))]
```

Replace with:
```python
        delivery_contacts = [build_delivery_contact(c) for c in contacts]
```

- [ ] **Step 6: Remove the `mark_daily_status` call (around line 294-296)**

Find:
```python
        if report.success_count > 0:
            sheets.mark_daily_status(SHEET_ID, date_str, REPORT_TYPE)
            logger.info("Control sheet updated.")
```

Delete the entire block (3 lines including the `if`). The claim in step 3 already acts as both check and mark — no separate mark needed.

- [ ] **Step 7: Run existing tests to check nothing breaks**

Run: `pytest tests/ -k "morning" -v`
Expected: all existing morning-check tests pass or skip cleanly. If a test directly mocks `SheetsClient` and expects old signatures, open the test, update its mocks to use `ContactsRepo` instead, and re-run.

- [ ] **Step 8: Smoke test the script locally (dry-run)**

Run:
```bash
python -m execution.scripts.morning_check --dry-run
```
Expected: runs to completion, logs contact count matching Supabase `ativo` count. No `SheetsClient` errors.

- [ ] **Step 9: Commit**

```bash
git add execution/scripts/morning_check.py tests/
git commit -m "refactor(morning_check): use ContactsRepo and Redis daily-status claim"
```

---

## Task 10: Refactor `baltic_ingestion.py` (same pattern)

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py`

- [ ] **Step 1: Update imports (around line 25)**

Replace:
```python
from execution.integrations.sheets_client import SheetsClient
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
```

With:
```python
from execution.integrations.contacts_repo import ContactsRepo
from execution.core.delivery_reporter import DeliveryReporter, build_delivery_contact
from execution.core import state_store
```

- [ ] **Step 2: Remove `SHEET_ID` and `SHEET_NAME_CONTACTS` constants (around line 37)**

Delete:
```python
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
SHEET_NAME_CONTACTS = "Página1"
```

- [ ] **Step 3: Replace the daily-status check (around line 225-230)**

Find the block with `sheets.check_daily_status`:
```python
            already = await asyncio.to_thread(
                sheets.check_daily_status, SHEET_ID, today_str, REPORT_TYPE
            )
            if already:
                ...
```

Replace with:
```python
            claim_key = f"daily_report:sent:{REPORT_TYPE}:{today_str}"
            claimed = await asyncio.to_thread(
                state_store.try_claim_alert_key, claim_key, 48 * 3600
            )
            if not claimed:
                logger.info("Report already sent today. Exiting.")
                progress.finish_empty("report ja enviado hoje")
                return
```

- [ ] **Step 4: Replace the contacts fetch (around line 330)**

Find:
```python
        contacts = await asyncio.to_thread(sheets.get_contacts, SHEET_ID, SHEET_NAME_CONTACTS)
```

Replace with:
```python
        contacts_repo = ContactsRepo()
        contacts = await asyncio.to_thread(contacts_repo.list_active)
```

- [ ] **Step 5: Replace the delivery-contact build (around line 345)**

Find:
```python
        delivery_contacts = [bc for c in contacts if (bc := build_contact_from_row(c))]
```

Replace with:
```python
        delivery_contacts = [build_delivery_contact(c) for c in contacts]
```

- [ ] **Step 6: Remove the `mark_daily_status` call (around line 360-364)**

Find and delete:
```python
            await asyncio.to_thread(sheets.mark_daily_status, SHEET_ID, today_str, REPORT_TYPE)
```
(and any surrounding `if success_count > 0:` scoping — let the delete be surgical, keep the surrounding error-handling block intact, only remove the mark call and its condition if trivial).

- [ ] **Step 7: Remove the now-unused `sheets = SheetsClient()` instantiation**

Search the file for any remaining `sheets = SheetsClient()` or `sheets.` references and delete them.

- [ ] **Step 8: Run tests and smoke-test**

Run: `pytest tests/ -k "baltic" -v`
Run: `python -m execution.scripts.baltic_ingestion --dry-run` (if the script supports `--dry-run`; otherwise skip).

- [ ] **Step 9: Commit**

```bash
git add execution/scripts/baltic_ingestion.py
git commit -m "refactor(baltic): use ContactsRepo and Redis daily-status claim"
```

---

## Task 11: Refactor `send_news.py`

**Files:**
- Modify: `execution/scripts/send_news.py`

- [ ] **Step 1: Update imports (around line 11-15)**

Replace:
```python
from execution.integrations.sheets_client import SheetsClient
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
```

With:
```python
from execution.integrations.contacts_repo import ContactsRepo
from execution.core.delivery_reporter import DeliveryReporter, build_delivery_contact
```

- [ ] **Step 2: Remove `SHEET_ID` and `SHEET_NAME` constants (around line 19)**

Delete:
```python
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
SHEET_NAME = "Página1"
```

- [ ] **Step 3: Replace contacts fetch (around lines 60-63)**

Find:
```python
        try:
            sheets = SheetsClient()
            contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME)
        except Exception as e:
            logger.critical(f"Failed to fetch contacts: {e}")
```

Replace with:
```python
        try:
            contacts_repo = ContactsRepo()
            contacts = contacts_repo.list_active()
        except Exception as e:
            logger.critical(f"Failed to fetch contacts: {e}")
```

- [ ] **Step 4: Replace the build_contact_from_row call (around line 77)**

Find:
```python
        delivery_contacts = [bc for c in contacts if (bc := build_contact_from_row(c))]
```

Replace with:
```python
        delivery_contacts = [build_delivery_contact(c) for c in contacts]
```

- [ ] **Step 5: Smoke test**

Run: `python -m execution.scripts.send_news --message "test" --dry-run`
Expected: runs cleanly, logs `Would send to N contacts`.

- [ ] **Step 6: Commit**

```bash
git add execution/scripts/send_news.py
git commit -m "refactor(send_news): use ContactsRepo"
```

---

## Task 12: Refactor `send_daily_report.py`

**Files:**
- Modify: `execution/scripts/send_daily_report.py`

- [ ] **Step 1: Update imports**

Open the file and replace:
```python
from execution.integrations.sheets_client import SheetsClient
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
```

With:
```python
from execution.integrations.contacts_repo import ContactsRepo
from execution.core.delivery_reporter import DeliveryReporter, build_delivery_contact
```

- [ ] **Step 2: Remove `SHEET_ID` and `SHEET_NAME` constants**

Delete those lines.

- [ ] **Step 3: Replace contacts fetch (around line 136)**

Find:
```python
        contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME)
```

Replace with:
```python
        contacts_repo = ContactsRepo()
        contacts = contacts_repo.list_active()
```

Remove any preceding `sheets = SheetsClient()` that is now dead.

- [ ] **Step 4: Replace build_contact_from_row (around line 156)**

Find:
```python
        delivery_contacts = [bc for c in contacts if (bc := build_contact_from_row(c))]
```

Replace with:
```python
        delivery_contacts = [build_delivery_contact(c) for c in contacts]
```

- [ ] **Step 5: Smoke test**

Run: `python -m execution.scripts.send_daily_report --dry-run` (or equivalent).
Expected: reads from Supabase.

- [ ] **Step 6: Commit**

```bash
git add execution/scripts/send_daily_report.py
git commit -m "refactor(send_daily_report): use ContactsRepo"
```

---

## Task 13: Refactor `webhook/dispatch.py`

**Files:**
- Modify: `webhook/dispatch.py`

- [ ] **Step 1: Update imports (around lines 21-25)**

Replace:
```python
from bot.config import get_bot, UAZAPI_URL, UAZAPI_TOKEN, GOOGLE_CREDENTIALS_JSON, SHEET_ID
...
from execution.integrations.sheets_client import SheetsClient
```

With:
```python
from bot.config import get_bot, UAZAPI_URL, UAZAPI_TOKEN
...
from execution.integrations.contacts_repo import ContactsRepo
```

Note: `GOOGLE_CREDENTIALS_JSON` and `SHEET_ID` imports go away.

- [ ] **Step 2: Delete the `_get_contacts_sync` function entirely (lines 68-99)**

Remove the whole block:
```python
# ── Google Sheets (contacts) — sync, wrapped in to_thread ──

def _get_contacts_sync():
    ...
```

- [ ] **Step 3: Replace the `get_contacts` async wrapper (around line 102)**

Find:
```python
async def get_contacts():
    """Fetch WhatsApp contacts (async wrapper)."""
    return await asyncio.to_thread(_get_contacts_sync)
```

Replace with:
```python
async def get_contacts():
    """Fetch active WhatsApp contacts from Supabase (async wrapper)."""
    def _read():
        return ContactsRepo().list_active()
    return await asyncio.to_thread(_read)
```

- [ ] **Step 4: Update `process_approval_async` (around line 162-163)**

Find:
```python
        raw_contacts = await get_contacts()
        delivery_contacts = [bc for c in raw_contacts if (bc := build_contact_from_row(c))]
```

Replace with:
```python
        raw_contacts = await get_contacts()
        # Import lives at top; contacts are now Contact objects, not dicts.
        from execution.core.delivery_reporter import build_delivery_contact
        delivery_contacts = [build_delivery_contact(c) for c in raw_contacts]
```

Also remove the `build_contact_from_row` from the top-level import if it's there:
```python
# Before:
from execution.core.delivery_reporter import DeliveryReporter, build_contact_from_row
# After:
from execution.core.delivery_reporter import DeliveryReporter, build_delivery_contact
```

- [ ] **Step 5: Update `process_test_send_async` (around line 290-302)**

Find:
```python
        contacts = await get_contacts()
        if not contacts:
            await bot.send_message(chat_id, "❌ Nenhum contato encontrado na planilha.")
            return

        first_contact = contacts[0]
        name = first_contact.get("Nome", "Contato 1")
        phone = first_contact.get("Evolution-api") or first_contact.get("Telefone")
        if not phone:
            await bot.send_message(chat_id, "❌ Primeiro contato sem telefone.")
            return

        phone = str(phone).replace("whatsapp:", "").strip()
```

Replace with:
```python
        contacts = await get_contacts()
        if not contacts:
            await bot.send_message(chat_id, "❌ Nenhum contato ativo encontrado.")
            return

        first_contact = contacts[0]
        name = first_contact.name
        phone = first_contact.phone_uazapi
```

- [ ] **Step 6: Run existing dispatch tests**

Run: `pytest tests/test_dispatch_idempotency.py -v`
Expected: all tests pass. If a test mocks `_get_contacts_sync` or uses dict-shaped contacts, update to mock `ContactsRepo.list_active` returning `Contact` objects.

- [ ] **Step 7: Commit**

```bash
git add webhook/dispatch.py tests/
git commit -m "refactor(dispatch): use ContactsRepo; remove inline gspread call"
```

---

## Task 14: Update `contact_admin.py` helper — `build_list_keyboard` accepts `Contact`

**Files:**
- Modify: `webhook/contact_admin.py`

- [ ] **Step 1: Update `build_list_keyboard` to use `Contact` attributes**

In `webhook/contact_admin.py`, find `build_list_keyboard` (around line 141-180). Replace the body with:

```python
def build_list_keyboard(contacts: list, page: int, total_pages: int,
                        search: Optional[str]) -> dict:
    """
    Build inline_keyboard dict with one toggle button per contact,
    plus bulk-action and nav rows.

    `contacts` is a list of execution.integrations.contacts_repo.Contact
    instances (dataclass with .name, .phone_uazapi, .status, etc.).
    """
    rows = []

    for c in contacts:
        emoji = "✅" if c.status == "ativo" else "❌"
        label = f"{emoji} {c.name} — {c.phone_uazapi}"
        # Telegram button text limit: 64 chars
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append([{
            "text": label,
            "callback_data": f"tgl:{c.phone_uazapi}",
        }])

    # Pagination row (only when >1 page)
    if total_pages > 1:
        prev_page = max(1, page - 1)
        next_page = min(total_pages, page + 1)
        suffix = f":{search}" if search else ""
        rows.append([
            {"text": "◀", "callback_data": f"pg:{prev_page}{suffix}"},
            {"text": f"{page}/{total_pages}", "callback_data": "nop"},
            {"text": "▶", "callback_data": f"pg:{next_page}{suffix}"},
        ])

    # Bulk-action row (always shown when there's at least one contact).
    if contacts:
        bulk_suffix = f":{search}" if search else ":"
        rows.append([
            {"text": "✅ Ativar todos", "callback_data": f"bulk:ativo{bulk_suffix}"},
            {"text": "❌ Desativar todos", "callback_data": f"bulk:inativo{bulk_suffix}"},
        ])

    return {"inline_keyboard": rows}
```

Note the bulk callback payload convention: `bulk:<status>:<search>` where search is empty when there's no filter.

- [ ] **Step 2: Update `digits_only` callers inside this module**

`digits_only` is no longer needed by `build_list_keyboard` (Contact.phone_uazapi is already digits). Leave the function in place since `parse_add_input` still uses it.

- [ ] **Step 3: Quick smoke test**

Run:
```bash
python -c "
from dataclasses import dataclass
from datetime import datetime, timezone
from webhook.contact_admin import build_list_keyboard
from execution.integrations.contacts_repo import Contact
now = datetime.now(timezone.utc)
c = Contact('x','Alice','+55','5511987654321','ativo', now, now)
kb = build_list_keyboard([c], page=1, total_pages=1, search=None)
print(kb)
"
```
Expected: dict with `inline_keyboard` containing one contact row + one bulk-action row.

- [ ] **Step 4: Commit**

```bash
git add webhook/contact_admin.py
git commit -m "refactor(contact_admin): build_list_keyboard accepts Contact objects + bulk buttons"
```

---

## Task 15: Add bulk callback_data classes

**Files:**
- Modify: `webhook/bot/callback_data.py`

- [ ] **Step 1: Append new callback_data classes**

At the end of `webhook/bot/callback_data.py`, append:

```python
class ContactBulk(CallbackData, prefix="bulk"):
    """First tap on bulk activate/deactivate. Shows confirmation prompt."""
    status: str       # 'ativo' | 'inativo'
    search: str = ""


class ContactBulkConfirm(CallbackData, prefix="bulkok"):
    """Second tap — user confirmed the bulk action."""
    status: str       # 'ativo' | 'inativo'
    search: str = ""


class ContactBulkCancel(CallbackData, prefix="bulkno"):
    """Cancel the pending bulk action."""
    pass
```

- [ ] **Step 2: Commit**

```bash
git add webhook/bot/callback_data.py
git commit -m "feat(bot): add ContactBulk/Confirm/Cancel callback factories"
```

---

## Task 16: Refactor `/list` command in `commands.py`

**Files:**
- Modify: `webhook/bot/routers/commands.py`

- [ ] **Step 1: Update imports (around lines 19-27)**

Replace:
```python
from bot.config import get_bot, ANTHROPIC_API_KEY, SHEET_ID, TELEGRAM_WEBHOOK_URL
...
from execution.integrations.sheets_client import SheetsClient
```

With:
```python
from bot.config import get_bot, ANTHROPIC_API_KEY, TELEGRAM_WEBHOOK_URL
...
from execution.integrations.contacts_repo import ContactsRepo
```

- [ ] **Step 2: Rewrite `_render_list_view` (around lines 271-302)**

Replace the whole function with:

```python
async def _render_list_view(chat_id, page, search, message_id=None):
    """Fetch contacts from Supabase and render the list message with keyboard."""
    bot = get_bot()
    try:
        repo = ContactsRepo()
        per_page = 10
        contacts, total_pages = await asyncio.to_thread(
            repo.list_all, search=search, page=page, per_page=per_page,
        )
        # Total count for header: query page 1 with huge per_page and count pages*per_page.
        # Alternative: list_all returns total_pages; total = len(all_rows) would require
        # a second query. Cheaper: total = total_pages * per_page is wrong for partial last page.
        # Keep exact count: refetch with per_page big.
        all_contacts, _ = await asyncio.to_thread(
            repo.list_all, search=search, page=1, per_page=10_000,
        )
        total = len(all_contacts)

        msg = contact_admin.render_list_message(
            contacts, total=total, page=page, per_page=per_page, search=search,
        )
        kb = contact_admin.build_list_keyboard(
            contacts, page=page, total_pages=total_pages, search=search,
        )

        if message_id is None:
            await bot.send_message(chat_id, msg, reply_markup=kb)
        else:
            await bot.edit_message_text(msg, chat_id=chat_id, message_id=message_id, reply_markup=kb)
    except Exception as e:
        logger.error(f"_render_list_view failed: {e}")
        err_msg = "❌ Erro ao acessar base de contatos. Tente novamente."
        if message_id:
            await bot.edit_message_text(err_msg, chat_id=chat_id, message_id=message_id)
        else:
            await bot.send_message(chat_id, err_msg)
```

- [ ] **Step 3: Verify no other references to `SHEET_ID` or `SheetsClient` remain in the file**

Run: `grep -n "SHEET_ID\|SheetsClient" webhook/bot/routers/commands.py`
Expected: no output.

- [ ] **Step 4: Manual smoke test**

Restart the bot locally (`python webhook/app.py` or equivalent) and send `/list` in Telegram.
Expected: contact list renders, pagination works.

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/commands.py
git commit -m "refactor(bot): /list command uses ContactsRepo"
```

---

## Task 17: Refactor `/add` flow in `messages.py`

**Files:**
- Modify: `webhook/bot/routers/messages.py`

- [ ] **Step 1: Update imports at the top of the file**

Find:
```python
from execution.integrations.sheets_client import SheetsClient
```

Replace with:
```python
from execution.integrations.contacts_repo import (
    ContactsRepo, InvalidPhoneError, ContactAlreadyExistsError,
)
```

Also remove `SHEET_ID` from any top-level imports in this file.

- [ ] **Step 2: Define the welcome message constant**

Near the top of the file (after the imports, before the first handler), add:

```python
WELCOME_MESSAGE = (
    "Você foi adicionado à lista de informações de mercado "
    "da Minerals Trading."
)
```

- [ ] **Step 3: Rewrite `on_add_contact_data` (around lines 173-211)**

Replace the whole function with:

```python
@message_router.message(AddContact.waiting_data, F.text)
async def on_add_contact_data(message: Message, state: FSMContext):
    text = message.text or ""

    # /cancel while in add flow
    if text.strip().startswith("/"):
        await state.clear()
        return

    try:
        name, phone = contact_admin.parse_add_input(text)
    except ValueError as e:
        await message.answer(f"❌ {e}")
        return  # keep state so user can retry

    from webhook.dispatch import send_whatsapp  # local import to avoid cycles

    async def _send_welcome(phone_uazapi: str) -> None:
        """Synchronous wrapper around send_whatsapp that raises on failure.
        Runs inside the to_thread call below, but creates its own event loop
        to await the async send_whatsapp."""
        result = await send_whatsapp(
            phone_uazapi, WELCOME_MESSAGE, draft_id="welcome",
        )
        if not result.get("ok") and result.get("status") != "duplicate":
            raise RuntimeError(
                f"uazapi send not ok (status={result.get('http_status')})"
            )

    def _sync_send_welcome(phone_uazapi: str) -> None:
        asyncio.run(_send_welcome(phone_uazapi))

    try:
        repo = ContactsRepo()
        contact = await asyncio.to_thread(
            repo.add, name, phone, send_welcome=_sync_send_welcome,
        )
    except InvalidPhoneError as e:
        await message.answer(
            f"❌ Telefone inválido: {e}\n"
            f"Inclua o DDI (ex: 55 Brasil, 1 EUA).",
        )
        await state.clear()
        return
    except ContactAlreadyExistsError as e:
        await message.answer(
            f"❌ Já existe: {e.existing.name} ({e.existing.status})",
        )
        await state.clear()
        return
    except RuntimeError as e:
        await message.answer(
            f"❌ Não consegui enviar mensagem de boas-vindas — "
            f"o número pode não ter WhatsApp.\n\nDetalhe: {str(e)[:200]}",
        )
        await state.clear()
        return
    except Exception as e:
        logger.error(f"add_contact failed: {e}")
        await message.answer("❌ Erro ao adicionar contato. Tente novamente.")
        await state.clear()
        return

    # Success — fetch active count for confirmation.
    try:
        active_contacts = await asyncio.to_thread(repo.list_active)
        active = len(active_contacts)
    except Exception:
        active = "?"

    await message.answer(
        f"✅ {contact.name} adicionado ({contact.phone_uazapi})\n"
        f"Total ativos: {active}",
    )
    await state.clear()
```

Note: `asyncio.run(_send_welcome(...))` inside `_sync_send_welcome` is fine because `repo.add` runs inside `asyncio.to_thread` — it's on a worker thread with no event loop, so `asyncio.run` is safe there.

- [ ] **Step 4: Manual smoke test**

Restart the bot. In Telegram:
1. Send `/add`.
2. Send `Teste 5511987654321`.

Expected:
- You receive the welcome message on WhatsApp (at 5511987654321).
- Bot replies `✅ Teste adicionado (5511987654321). Total ativos: N+1`.

Try also:
- Invalid phone `/add` → `Teste abc` → expect "Telefone inválido" reply.
- Duplicate `/add` → same phone twice → expect "Já existe".
- Number without WhatsApp → expect "Não consegui enviar...".

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/messages.py
git commit -m "refactor(bot): /add uses ContactsRepo with uazapi welcome validation"
```

---

## Task 18: Refactor toggle callback + add bulk handlers (TDD)

**Files:**
- Create: `tests/test_contacts_bulk_ops.py`
- Modify: `webhook/bot/routers/callbacks_contacts.py`

- [ ] **Step 1: Write failing tests for bulk flow logic**

Create `tests/test_contacts_bulk_ops.py`:

```python
"""Integration tests for the /list bulk activate/deactivate flow."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from webhook.bot.routers.callbacks_contacts import (
    on_bulk_prompt, on_bulk_confirm, on_bulk_cancel,
)
from webhook.bot.callback_data import (
    ContactBulk, ContactBulkConfirm, ContactBulkCancel,
)


@pytest.fixture
def fake_query():
    q = MagicMock()
    q.answer = AsyncMock()
    q.message = MagicMock()
    q.message.chat.id = 123
    q.message.message_id = 456
    q.message.edit_text = AsyncMock()
    return q


@pytest.mark.asyncio
async def test_bulk_prompt_shows_confirmation(fake_query):
    """First tap on [❌ Desativar todos] must show confirmation keyboard."""
    fake_repo = MagicMock()
    fake_repo.list_all.return_value = ([MagicMock() for _ in range(47)], 5)

    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo):
        await on_bulk_prompt(
            fake_query,
            ContactBulk(status="inativo", search=""),
        )

    # Confirmation prompt must have been edited into the message.
    fake_query.message.edit_text.assert_called_once()
    call = fake_query.message.edit_text.call_args
    text = call.args[0] if call.args else call.kwargs.get("text")
    assert "47" in str(text)  # shows count
    assert "desativar" in str(text).lower()


@pytest.mark.asyncio
async def test_bulk_confirm_calls_bulk_set_status_and_reports(fake_query):
    fake_repo = MagicMock()
    fake_repo.bulk_set_status.return_value = 47

    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo):
        await on_bulk_confirm(
            fake_query,
            ContactBulkConfirm(status="inativo", search=""),
        )

    fake_repo.bulk_set_status.assert_called_once_with("inativo", search=None)
    fake_query.answer.assert_awaited()
    toast = fake_query.answer.await_args.args[0]
    assert "47" in toast


@pytest.mark.asyncio
async def test_bulk_confirm_respects_search(fake_query):
    fake_repo = MagicMock()
    fake_repo.bulk_set_status.return_value = 3

    with patch("webhook.bot.routers.callbacks_contacts.ContactsRepo",
               return_value=fake_repo):
        await on_bulk_confirm(
            fake_query,
            ContactBulkConfirm(status="ativo", search="joao"),
        )

    fake_repo.bulk_set_status.assert_called_once_with("ativo", search="joao")


@pytest.mark.asyncio
async def test_bulk_cancel_answers_and_rerenders_list(fake_query):
    with patch("webhook.bot.routers.callbacks_contacts._render_list_view",
               new=AsyncMock()) as rerender:
        await on_bulk_cancel(fake_query, ContactBulkCancel())

    fake_query.answer.assert_awaited_with("Cancelado")
    rerender.assert_awaited_once()
```

- [ ] **Step 2: Run tests — expect import failures**

Run: `pytest tests/test_contacts_bulk_ops.py -v`
Expected: `ImportError: cannot import name 'on_bulk_prompt'` etc.

- [ ] **Step 3: Rewrite `callbacks_contacts.py`**

Replace the entire content of `webhook/bot/routers/callbacks_contacts.py` with:

```python
"""Callback handlers for contact admin (toggle, bulk activate/deactivate)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from bot.callback_data import (
    ContactToggle, ContactPage,
    ContactBulk, ContactBulkConfirm, ContactBulkCancel,
)
from bot.middlewares.auth import RoleMiddleware
from execution.integrations.contacts_repo import (
    ContactsRepo, ContactNotFoundError,
)

logger = logging.getLogger(__name__)

callbacks_contacts_router = Router(name="callbacks_contacts")
callbacks_contacts_router.callback_query.middleware(
    RoleMiddleware(allowed_roles={"admin"})
)


# ── Toggle ──

@callbacks_contacts_router.callback_query(ContactToggle.filter())
async def on_contact_toggle(query: CallbackQuery, callback_data: ContactToggle):
    # Local import avoids circular dep with commands.py.
    from bot.routers.commands import _render_list_view
    try:
        repo = ContactsRepo()
        contact = await asyncio.to_thread(repo.toggle, callback_data.phone)
    except ContactNotFoundError as e:
        await query.answer(f"❌ {str(e)[:100]}")
        return
    except Exception as e:
        logger.error(f"toggle_contact failed: {e}")
        await query.answer("❌ Erro")
        return

    toast = (
        f"✅ {contact.name} ativado" if contact.is_active()
        else f"❌ {contact.name} desativado"
    )
    await query.answer(toast)
    await _render_list_view(
        query.message.chat.id, page=1, search=None,
        message_id=query.message.message_id,
    )


# ── Pagination ──

@callbacks_contacts_router.callback_query(ContactPage.filter())
async def on_contact_page(query: CallbackQuery, callback_data: ContactPage):
    from bot.routers.commands import _render_list_view
    await query.answer("")
    search = callback_data.search if callback_data.search else None
    await _render_list_view(
        query.message.chat.id, page=callback_data.page,
        search=search, message_id=query.message.message_id,
    )


# ── Bulk: first tap (show confirmation) ──

@callbacks_contacts_router.callback_query(ContactBulk.filter())
async def on_bulk_prompt(query: CallbackQuery, callback_data: ContactBulk):
    """First tap — count how many contacts match and show confirmation."""
    search = callback_data.search if callback_data.search else None
    try:
        repo = ContactsRepo()
        all_rows, _ = await asyncio.to_thread(
            repo.list_all, search=search, page=1, per_page=10_000,
        )
    except Exception as e:
        logger.error(f"bulk count failed: {e}")
        await query.answer("❌ Erro")
        return

    count = len(all_rows)
    verb = "ativar" if callback_data.status == "ativo" else "desativar"
    scope = f' (filtro: "{search}")' if search else ""
    prompt = f"Confirma {verb} {count} contatos{scope}?"

    confirm_kb = {
        "inline_keyboard": [[
            {
                "text": "✅ Sim",
                "callback_data": ContactBulkConfirm(
                    status=callback_data.status,
                    search=callback_data.search,
                ).pack(),
            },
            {
                "text": "❌ Cancelar",
                "callback_data": ContactBulkCancel().pack(),
            },
        ]]
    }

    await query.answer("")
    await query.message.edit_text(prompt, reply_markup=confirm_kb)


# ── Bulk: second tap (execute) ──

@callbacks_contacts_router.callback_query(ContactBulkConfirm.filter())
async def on_bulk_confirm(query: CallbackQuery, callback_data: ContactBulkConfirm):
    from bot.routers.commands import _render_list_view
    search = callback_data.search if callback_data.search else None
    try:
        repo = ContactsRepo()
        count = await asyncio.to_thread(
            repo.bulk_set_status, callback_data.status, search=search,
        )
    except Exception as e:
        logger.error(f"bulk_set_status failed: {e}")
        await query.answer("❌ Erro")
        return

    verb = "ativados" if callback_data.status == "ativo" else "desativados"
    await query.answer(f"✅ {count} contatos {verb}")
    await _render_list_view(
        query.message.chat.id, page=1, search=search,
        message_id=query.message.message_id,
    )


# ── Bulk: cancel ──

@callbacks_contacts_router.callback_query(ContactBulkCancel.filter())
async def on_bulk_cancel(query: CallbackQuery, callback_data: ContactBulkCancel):
    from bot.routers.commands import _render_list_view
    await query.answer("Cancelado")
    await _render_list_view(
        query.message.chat.id, page=1, search=None,
        message_id=query.message.message_id,
    )
```

- [ ] **Step 4: Run tests — pass**

Run: `pytest tests/test_contacts_bulk_ops.py -v`
Expected: All 4 tests pass.

- [ ] **Step 5: Manual smoke test**

Restart bot. Send `/list`, tap `❌ Desativar todos`, confirm. Expect toast "N contatos desativados" and the list re-renders with all contacts now inactive. Repeat with `✅ Ativar todos`.

- [ ] **Step 6: Commit**

```bash
git add webhook/bot/routers/callbacks_contacts.py tests/test_contacts_bulk_ops.py
git commit -m "feat(bot): bulk activate/deactivate with two-step confirmation"
```

---

## Task 19: Refactor dashboard API route

**Files:**
- Modify: `dashboard/package.json`
- Modify: `dashboard/app/api/contacts/route.ts`

- [ ] **Step 1: Add @supabase/supabase-js to dashboard**

From `dashboard/`:

```bash
cd dashboard && npm install @supabase/supabase-js && cd ..
```

Verify: `grep @supabase/supabase-js dashboard/package.json` returns a line.

- [ ] **Step 2: Rewrite the route**

Replace the entire content of `dashboard/app/api/contacts/route.ts` with:

```typescript
import { NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";

export async function GET() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_KEY;

  if (!url || !key) {
    console.error("Missing SUPABASE_URL or SUPABASE_KEY");
    return NextResponse.json(
      { error: "Supabase not configured" },
      { status: 500 },
    );
  }

  try {
    const supabase = createClient(url, key);
    const { data, error } = await supabase
      .from("contacts")
      .select("id, name, phone_raw, phone_uazapi, status, created_at, updated_at")
      .order("created_at", { ascending: false });

    if (error) {
      console.error("Supabase error:", error);
      return NextResponse.json(
        { error: "Failed to fetch contacts" },
        { status: 500 },
      );
    }

    return NextResponse.json(data || []);
  } catch (error) {
    console.error("Dashboard contacts route error:", error);
    return NextResponse.json(
      { error: "Unexpected error" },
      { status: 500 },
    );
  }
}
```

- [ ] **Step 3: Verify dashboard consumers still work**

If the dashboard UI for contacts reads specific fields (likely `name`, `phone_uazapi`, `status`), confirm field names match. Previously rows came from Sheets with raw column names like `ProfileName`, `ButtonPayload`. **Search the dashboard source:**

```bash
grep -rn "ProfileName\|ButtonPayload\|Evolution-api" dashboard --include="*.ts" --include="*.tsx" | grep -v node_modules | grep -v ".next"
```

If any file references these old names, update them to use `name`, `status`, `phone_uazapi` respectively.

- [ ] **Step 4: Build the dashboard**

Run:
```bash
cd dashboard && npm run build && cd ..
```
Expected: build succeeds without type errors.

- [ ] **Step 5: Commit**

```bash
git add dashboard/package.json dashboard/package-lock.json dashboard/app/api/contacts/route.ts dashboard/app/
git commit -m "refactor(dashboard): /api/contacts reads from Supabase"
```

---

## Task 20: Remove `SHEET_ID` from bot config

**Files:**
- Modify: `webhook/bot/config.py`

- [ ] **Step 1: Remove the SHEET_ID constant**

Open `webhook/bot/config.py` and delete the line:
```python
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
```

- [ ] **Step 2: Search for any remaining import**

Run:
```bash
grep -rn "from bot.config import.*SHEET_ID\|SHEET_ID" webhook/ --include="*.py" | grep -v __pycache__
```
Expected: no output. If anything shows up, remove it.

- [ ] **Step 3: Run all tests**

Run: `pytest tests/ -v`
Expected: all pass (or only unrelated failures).

- [ ] **Step 4: Commit**

```bash
git add webhook/bot/config.py
git commit -m "chore(config): remove unused SHEET_ID constant"
```

---

## Task 21: Monitoring gate — wait 24–48h after deploy

**This task is operational, not code.**

- [ ] **Step 1: Deploy the branch**

Merge the PR to main, let Railway / whatever CI/CD deploy picks up.

- [ ] **Step 2: Watch the next scheduled runs**

- [ ] Next `morning_check` cron: confirm it reads contacts from Supabase, sends successfully, and a second manual trigger the same day exits early.
- [ ] Next `baltic_ingestion` cron: same verification.
- [ ] Send a `/add` in the bot with a new test number: welcome received, contact appears in `/list` and dashboard.
- [ ] Toggle a contact from `/list`: reflected in dashboard.
- [ ] Use bulk deactivate with a search scope: count matches, rerender works, reactivate restores.

- [ ] **Step 3: If any of the above fail, open a fix PR before proceeding to Task 22.**

---

## Task 22: Final cleanup — delete sheets_client and deps

**Only run after Task 21 passes for 24–48h.**

**Files:**
- Delete: `execution/integrations/sheets_client.py`
- Delete: `tests/test_sheets_contact_ops.py`
- Modify: `requirements.txt`
- Modify: `scripts/migrate_contacts_from_sheets.py` (it still imports SheetsClient — archive instead of delete)

- [ ] **Step 1: Move the migration script to an archive location**

```bash
mkdir -p scripts/archive
git mv scripts/migrate_contacts_from_sheets.py scripts/archive/migrate_contacts_from_sheets.py
git mv tests/test_migrate_contacts_from_sheets.py tests/archive/test_migrate_contacts_from_sheets.py 2>/dev/null || true
```

Update the archive script's docstring (first few lines) to add:
```
ARCHIVED 2026-04-22: one-off migration already completed. Kept for audit/reproducibility.
Requires GOOGLE_CREDENTIALS_JSON + SheetsClient which has been removed; restore those to re-run.
```

- [ ] **Step 2: Audit other `gspread` users**

Run:
```bash
grep -rn "import gspread\|from gspread" --include="*.py" . | grep -v node_modules | grep -v __pycache__ | grep -v archive
```

Expected: no non-archive hits. If there are any, stop and migrate those first.

- [ ] **Step 3: Delete SheetsClient module and its direct tests**

```bash
git rm execution/integrations/sheets_client.py
git rm tests/test_sheets_contact_ops.py
```

- [ ] **Step 4: Remove deps from requirements.txt**

Open `requirements.txt` and remove:
```
gspread>=5.10.0
google-auth>=2.0.0
```

(Keep `google-auth` if another module uses it — re-check with `grep -rn "from google" --include="*.py" .`.)

- [ ] **Step 5: Remove `GOOGLE_CREDENTIALS_JSON` usage**

Search:
```bash
grep -rn "GOOGLE_CREDENTIALS_JSON" --include="*.py" --include="*.ts" . | grep -v node_modules | grep -v .next | grep -v archive
```

Non-archive hits (notably `webhook/bot/config.py` if it exports it, and `webhook/dispatch.py` — already handled in Task 13) should be removed.

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: all pass.

- [ ] **Step 7: Smoke-test bot + one workflow**

Run `python -m execution.scripts.send_news --message "cleanup sanity" --dry-run`.
Restart bot, send `/list`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "chore(contacts): remove sheets_client and gspread after migration"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| Schema (`contacts` table, indexes, trigger, RLS) | Task 2 |
| `Contact` dataclass + exceptions | Task 4 |
| `normalize_phone` | Task 3 |
| `ContactsRepo` reads (`list_active`, `list_all`, `get_by_phone`) | Task 4 |
| `ContactsRepo` writes (`add`, `toggle`, `bulk_set_status`) | Task 5 |
| Migration script with `--dry-run` and BR-55 fallback | Tasks 6, 7 |
| `/add` flow — welcome send + error paths | Task 17 |
| `/list` flow — repo-backed | Task 16 |
| Toggle callback | Task 18 |
| Bulk activate/deactivate with confirmation | Task 18 (+ Tasks 14, 15) |
| Workflow scripts (`morning_check`, `baltic_ingestion`, `send_news`, `send_daily_report`) | Tasks 9, 10, 11, 12 |
| `webhook/dispatch.py` refactor | Task 13 |
| Dashboard API route | Task 19 |
| `Controle` → Redis `try_claim_alert_key` | Tasks 9, 10 |
| Cutover (migration + monitor + cleanup) | Tasks 7, 21, 22 |
| Testing (unit + bot integration) | Tasks 3–6, 8, 18 (tests live with each module) |

**Not explicitly in spec but intentional additions:**
- Task 20 (remove `SHEET_ID` from `bot/config.py`) is spec-implicit.
- Task 8 (DeliveryReporter adapter) is a small adapter needed because the spec changed the Contact shape; keeps `DeliveryReporter` unchanged.

**Open Items from spec (deferred to operations, not blocking the plan):**
1. `gspread` dep audit — handled in Task 22 Step 2 (grep pass).
2. Dashboard API path choice — resolved as "Supabase directly" in Task 19.
3. Bulk-op dashboard UI — explicitly out of scope (spec-confirmed).

**Verification commands at end:**

```bash
pytest tests/ -v                                # all tests pass
python -m execution.scripts.morning_check --dry-run  # reads from Supabase
# /list in Telegram                             # renders from Supabase
# Supabase SQL: select count(*) from contacts   # matches sheet count post-migration
```
