# OneDrive PDF → WhatsApp Broadcast — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the n8n "RELATORIO DIARIO" workflow with a Microsoft Graph change-notification webhook that detects new PDFs in the SIGCM SharePoint folder and, after admin approval via Telegram, broadcasts each PDF to a chosen WhatsApp contact list (or to every active contact).

**Architecture:** HTTP webhook in the existing Railway `webhook/` app → delta-query-based detection → Redis-backed approval state (48h TTL) → Telegram inline keyboard with one button per `contact_lists` row + `Todos` + `Descartar` → single-select + confirmation flow → fan-out via new `UazapiClient.send_document()` that forwards the Graph `@microsoft.graph.downloadUrl` directly (no local PDF download). Parallel GH Actions cron renews Graph subscriptions every 12h. All events share a `trace_id` via the existing `EventBus`.

**Tech Stack:** Python 3.11, aiohttp (inside existing `webhook/bot/main.py`), aiogram 3.x, `supabase-py`, `redis` + `fakeredis` for tests, `requests` for Graph client, pytest + pytest-asyncio, GitHub Actions.

**Related spec:** `docs/superpowers/specs/2026-04-22-onedrive-pdf-broadcast-design.md` — read this before starting any task.

---

## File Structure

### Created

| Path | Responsibility |
|---|---|
| `supabase/migrations/20260422_contact_lists.sql` | Creates `contact_lists` + `contact_list_members` tables, enables RLS, seeds three initial lists. |
| `execution/integrations/graph_client.py` | Microsoft Graph API client: OAuth2 client-credentials token cache, subscription CRUD (`create/list/renew/delete`), folder delta query, `get_item`. |
| `execution/scripts/onedrive_resubscribe.py` | Cron entrypoint wrapped in `@with_event_bus("onedrive_resubscribe")` that renews near-expiring subscriptions (creates one if none exist). |
| `.github/workflows/onedrive_resubscribe.yml` | GH Actions schedule for the resubscribe cron (12h cadence) + `workflow_dispatch` for manual runs. |
| `webhook/routes/onedrive.py` | aiohttp route `POST /onedrive/notify` — handles Graph validation handshake, validates `clientState`, spawns async processing. |
| `webhook/onedrive_pipeline.py` | `process_notification(payload)` — delta query, dedup, create Redis approval state, send Telegram approval card. |
| `webhook/dispatch_document.py` | `dispatch_document(approval_id, list_code)` — fan-out with concurrency 5, per-recipient idempotency, `DeliveryReporter` + `ProgressReporter` integration. |
| `webhook/bot/routers/callbacks_onedrive.py` | Callback handlers: `on_approve`, `on_confirm`, `on_discard` — in-place card edits + state transitions. |
| `tests/test_graph_client.py` | Unit tests for Graph client (token cache, retries, payload shapes). |
| `tests/test_uazapi_send_document.py` | Unit tests for new `UazapiClient.send_document`. |
| `tests/test_contacts_repo_lists.py` | Unit tests for `list_lists`, `list_by_list_code`, and the `ContactList` dataclass. |
| `tests/test_onedrive_pipeline.py` | Unit tests for the detection pipeline (dedup, PDF filter, approval state creation). |
| `tests/test_onedrive_callbacks.py` | Unit tests for the approval callbacks router. |
| `tests/test_dispatch_document.py` | Unit tests for the document dispatch (stale URL re-fetch, idempotency, fan-out). |
| `tests/test_onedrive_resubscribe.py` | Unit tests for the resubscribe cron. |
| `tests/test_onedrive_route.py` | Unit tests for the aiohttp webhook handler (validation token, clientState check). |

### Modified

| Path | Change |
|---|---|
| `.env.example` | Add 6 new Graph + OneDrive env vars. |
| `execution/integrations/uazapi_client.py` | Add `send_document(number, file_url, doc_name, caption="")`. |
| `execution/integrations/contacts_repo.py` | Add `ContactList` dataclass + `list_lists()` + `list_by_list_code(code)`. |
| `webhook/bot/callback_data.py` | Add `OneDriveApprove`, `OneDriveConfirm`, `OneDriveDiscard`. |
| `webhook/bot/main.py` | Mount `routes/onedrive.py` route + `callbacks_onedrive_router`. |
| `webhook/status_builder.py` | Add `onedrive_resubscribe` to `ALL_WORKFLOWS` (watchdog coverage). |
| `webhook/bot/routers/commands.py` | Add `onedrive_resubscribe` to `_TAIL_KNOWN_WORKFLOWS` for `/tail` autocomplete. |

---

## Environment Prerequisites

Before running any task that touches Graph APIs or dispatch, ensure these env vars are set in local `.env` and in the Railway dashboard:

- `GRAPH_TENANT_ID` — Azure Entra ID tenant ID
- `GRAPH_CLIENT_ID` — App registration client ID
- `GRAPH_CLIENT_SECRET` — App registration client secret (with `Files.Read.All` application permission + admin consent)
- `GRAPH_DRIVE_ID` = `b!OpzpfwNGVEuhVt-oJYZoukWVYCYFUfdDmAJi023i_CwVR7rrWffbSI9pE6zV1uYd` (from existing n8n workflow — SIGCM drive)
- `GRAPH_FOLDER_PATH` = `/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals`
- `GRAPH_WEBHOOK_CLIENT_STATE` — 32-char random secret (generate: `python -c "import secrets; print(secrets.token_urlsafe(24))"`)
- `ONEDRIVE_WEBHOOK_URL` — full Railway URL to `/onedrive/notify` (e.g. `https://mineralsbot-production.up.railway.app/onedrive/notify`)

Already required and used:
- `SUPABASE_URL`, `SUPABASE_KEY` (service role)
- `REDIS_URL`
- `UAZAPI_URL`, `UAZAPI_TOKEN`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

---

## Task 1: Add Supabase migration for contact lists

**Files:**
- Create: `supabase/migrations/20260422_contact_lists.sql`

- [ ] **Step 1: Write the migration SQL**

Create `supabase/migrations/20260422_contact_lists.sql`:

```sql
-- Phase: OneDrive PDF → WhatsApp broadcast workflow
-- Adds contact_lists + contact_list_members for admin-selectable broadcast targets.
-- Related spec: docs/superpowers/specs/2026-04-22-onedrive-pdf-broadcast-design.md

CREATE TABLE IF NOT EXISTS contact_lists (
  code        TEXT PRIMARY KEY,
  label       TEXT NOT NULL,
  description TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contact_list_members (
  list_code     TEXT NOT NULL REFERENCES contact_lists(code) ON DELETE CASCADE,
  contact_phone TEXT NOT NULL REFERENCES contacts(phone_uazapi) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (list_code, contact_phone)
);

CREATE INDEX IF NOT EXISTS idx_clm_list_code ON contact_list_members(list_code);

ALTER TABLE contact_lists ENABLE ROW LEVEL SECURITY;
ALTER TABLE contact_list_members ENABLE ROW LEVEL SECURITY;
-- No policies: service_role bypasses RLS; all access is service-role.

INSERT INTO contact_lists (code, label) VALUES
  ('minerals_report', 'Minerals Report'),
  ('solid_fuels',     'Solid Fuels'),
  ('time_interno',    'Time Interno')
ON CONFLICT (code) DO NOTHING;
```

- [ ] **Step 2: Apply migration via Supabase MCP**

Run via Supabase MCP (if available) or Supabase dashboard SQL editor:

```sql
-- Paste the full content of the migration file above
```

Expected: two tables created, three rows inserted into `contact_lists`.

- [ ] **Step 3: Verify migration applied**

Run (Supabase SQL editor or `psql`):

```sql
SELECT code, label FROM contact_lists ORDER BY code;
SELECT count(*) FROM contact_list_members;
```

Expected output:
```
code            | label
----------------+----------------
minerals_report | Minerals Report
solid_fuels     | Solid Fuels
time_interno    | Time Interno

count: 0
```

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20260422_contact_lists.sql
git commit -m "feat(db): add contact_lists + contact_list_members for broadcast routing"
```

---

## Task 2: Extend ContactsRepo with list support

**Files:**
- Modify: `execution/integrations/contacts_repo.py`
- Create: `tests/test_contacts_repo_lists.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_contacts_repo_lists.py`:

```python
"""Unit tests for ContactsRepo list support (list_lists, list_by_list_code)."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from execution.integrations.contacts_repo import (
    ContactsRepo, ContactList, Contact,
)


class _FakeSupabaseTable:
    """Minimal chainable fake for supabase-py table builder."""
    def __init__(self, data):
        self._data = data
    def select(self, *args, **kwargs): return self
    def eq(self, col, val):
        self._data = [r for r in self._data if r.get(col) == val]
        return self
    def in_(self, col, vals):
        self._data = [r for r in self._data if r.get(col) in vals]
        return self
    def order(self, *args, **kwargs): return self
    def execute(self):
        resp = MagicMock()
        resp.data = self._data
        return resp


class _FakeSupabase:
    def __init__(self, tables): self._tables = tables
    def table(self, name): return _FakeSupabaseTable(list(self._tables.get(name, [])))


@pytest.fixture
def fake_sb():
    return _FakeSupabase({
        "contact_lists": [
            {"code": "minerals_report", "label": "Minerals Report", "description": None},
            {"code": "solid_fuels",     "label": "Solid Fuels",     "description": None},
            {"code": "time_interno",    "label": "Time Interno",    "description": None},
        ],
        "contact_list_members": [
            {"list_code": "minerals_report", "contact_phone": "5511111111111"},
            {"list_code": "minerals_report", "contact_phone": "5511222222222"},
            {"list_code": "solid_fuels",     "contact_phone": "5511111111111"},
        ],
        "contacts": [
            {"id": "a", "name": "Alice", "phone_raw": "+55 11 11111-1111",
             "phone_uazapi": "5511111111111", "status": "active",
             "created_at": "2026-04-01T00:00:00Z", "updated_at": "2026-04-01T00:00:00Z"},
            {"id": "b", "name": "Bob",   "phone_raw": "+55 11 22222-2222",
             "phone_uazapi": "5511222222222", "status": "active",
             "created_at": "2026-04-01T00:00:00Z", "updated_at": "2026-04-01T00:00:00Z"},
            {"id": "c", "name": "Carol", "phone_raw": "+55 11 33333-3333",
             "phone_uazapi": "5511333333333", "status": "inactive",
             "created_at": "2026-04-01T00:00:00Z", "updated_at": "2026-04-01T00:00:00Z"},
        ],
    })


def test_contact_list_dataclass_is_frozen():
    cl = ContactList(code="x", label="X", member_count=0)
    with pytest.raises((AttributeError, Exception)):
        cl.code = "y"  # type: ignore


def test_list_lists_returns_all_with_member_count(fake_sb):
    repo = ContactsRepo(sb=fake_sb)
    lists = repo.list_lists()
    by_code = {l.code: l for l in lists}
    assert by_code["minerals_report"].member_count == 2
    assert by_code["solid_fuels"].member_count == 1
    assert by_code["time_interno"].member_count == 0


def test_list_by_list_code_returns_active_members_only(fake_sb):
    repo = ContactsRepo(sb=fake_sb)
    members = repo.list_by_list_code("minerals_report")
    phones = {c.phone_uazapi for c in members}
    assert phones == {"5511111111111", "5511222222222"}
    for c in members:
        assert c.status == "active"


def test_list_by_list_code_excludes_inactive(fake_sb):
    # add Carol to minerals_report to verify she's filtered out (inactive)
    fake_sb._tables["contact_list_members"].append(
        {"list_code": "minerals_report", "contact_phone": "5511333333333"}
    )
    repo = ContactsRepo(sb=fake_sb)
    members = repo.list_by_list_code("minerals_report")
    phones = {c.phone_uazapi for c in members}
    assert "5511333333333" not in phones
    assert len(members) == 2


def test_list_by_list_code_unknown_list_returns_empty(fake_sb):
    repo = ContactsRepo(sb=fake_sb)
    assert repo.list_by_list_code("nonexistent") == []
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_contacts_repo_lists.py -v`
Expected: All fail with `ImportError: cannot import name 'ContactList'` (or similar).

- [ ] **Step 3: Add `ContactList` dataclass and methods to ContactsRepo**

Open `execution/integrations/contacts_repo.py`. At the top, next to the existing `Contact` dataclass, add:

```python
@dataclass(frozen=True)
class ContactList:
    """A named broadcast group. Used by onedrive_pipeline approval cards."""
    code: str
    label: str
    member_count: int
    description: Optional[str] = None
```

Inside the `ContactsRepo` class, add these two methods after `list_all` (find it via grep for `def list_all`):

```python
    def list_lists(self) -> list[ContactList]:
        """Return every contact_list with its current active-member count."""
        rows = self._sb.table("contact_lists").select("*").order("code").execute().data or []
        members = self._sb.table("contact_list_members").select(
            "list_code, contact_phone"
        ).execute().data or []

        # Count only active members per list.
        active_phones = {
            c["phone_uazapi"]
            for c in (self._sb.table("contacts").select(
                "phone_uazapi"
            ).eq("status", "active").execute().data or [])
        }
        counts: dict[str, int] = {}
        for m in members:
            if m["contact_phone"] in active_phones:
                counts[m["list_code"]] = counts.get(m["list_code"], 0) + 1

        return [
            ContactList(
                code=r["code"],
                label=r["label"],
                description=r.get("description"),
                member_count=counts.get(r["code"], 0),
            )
            for r in rows
        ]

    def list_by_list_code(self, code: str) -> list[Contact]:
        """Return active `Contact` rows subscribed to the given list code."""
        membership = self._sb.table("contact_list_members").select(
            "contact_phone"
        ).eq("list_code", code).execute().data or []
        phones = [m["contact_phone"] for m in membership]
        if not phones:
            return []
        rows = self._sb.table("contacts").select("*").in_(
            "phone_uazapi", phones
        ).eq("status", "active").execute().data or []
        return [self._row_to_contact(r) for r in rows]
```

If the existing repo does not have `_row_to_contact`, reuse whatever helper converts a DB row dict into a `Contact` dataclass — look for the pattern in `list_all` or `get`.

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_contacts_repo_lists.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full test suite to catch regressions**

Run: `pytest tests/test_contacts_repo.py tests/test_contacts_repo_normalize.py tests/test_contacts_repo_lists.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add execution/integrations/contacts_repo.py tests/test_contacts_repo_lists.py
git commit -m "feat(contacts): add list_lists + list_by_list_code for broadcast routing"
```

---

## Task 3: Add `UazapiClient.send_document`

**Files:**
- Modify: `execution/integrations/uazapi_client.py`
- Create: `tests/test_uazapi_send_document.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_uazapi_send_document.py`:

```python
"""Unit tests for UazapiClient.send_document (POST /send/media)."""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

from execution.integrations.uazapi_client import UazapiClient


@pytest.fixture(autouse=True)
def _env():
    os.environ["UAZAPI_URL"] = "https://test.uazapi.example.com"
    os.environ["UAZAPI_TOKEN"] = "fake-token"
    yield
    os.environ.pop("UAZAPI_URL", None)
    os.environ.pop("UAZAPI_TOKEN", None)


def _mock_post_ok():
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"messageId": "abc", "status": "sent"}
    return m


def test_send_document_posts_expected_payload():
    client = UazapiClient()
    with patch("execution.integrations.uazapi_client.requests.post",
               return_value=_mock_post_ok()) as post:
        client.send_document(
            number="5511987654321",
            file_url="https://graph-cdn.example.com/download?sig=xyz",
            doc_name="Minerals_Report_042226.pdf",
        )
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://test.uazapi.example.com/send/media"
    assert kwargs["headers"] == {"token": "fake-token", "Content-Type": "application/json"}
    body = kwargs["json"]
    assert body["number"] == "5511987654321"
    assert body["type"] == "document"
    assert body["file"] == "https://graph-cdn.example.com/download?sig=xyz"
    assert body["docName"] == "Minerals_Report_042226.pdf"
    assert body.get("text", "") == ""


def test_send_document_includes_caption_when_provided():
    client = UazapiClient()
    with patch("execution.integrations.uazapi_client.requests.post",
               return_value=_mock_post_ok()) as post:
        client.send_document(
            number="5511987654321",
            file_url="https://example.com/x.pdf",
            doc_name="x.pdf",
            caption="Novo relatório diário",
        )
    body = post.call_args.kwargs["json"]
    assert body["text"] == "Novo relatório diário"


def test_send_document_returns_json_response():
    client = UazapiClient()
    with patch("execution.integrations.uazapi_client.requests.post",
               return_value=_mock_post_ok()):
        result = client.send_document(
            number="5511987654321",
            file_url="https://example.com/x.pdf",
            doc_name="x.pdf",
        )
    assert result == {"messageId": "abc", "status": "sent"}


def test_send_document_raises_on_4xx():
    client = UazapiClient()
    bad = MagicMock()
    bad.status_code = 400
    bad.text = '{"error":"invalid number"}'
    bad.raise_for_status.side_effect = Exception("400 Bad Request")
    with patch("execution.integrations.uazapi_client.requests.post", return_value=bad):
        with pytest.raises(Exception):
            client.send_document(
                number="bad", file_url="x", doc_name="x.pdf",
            )
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_uazapi_send_document.py -v`
Expected: 4 failures on `AttributeError: 'UazapiClient' object has no attribute 'send_document'`.

- [ ] **Step 3: Implement `send_document`**

Open `execution/integrations/uazapi_client.py`. After the existing `send_message` method, add:

```python
    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def send_document(
        self,
        number: str,
        file_url: str,
        doc_name: str,
        caption: str = "",
    ) -> dict:
        """Send a document (PDF, etc.) via Uazapi /send/media.

        The `file_url` must be publicly fetchable by the Uazapi server —
        Graph `@microsoft.graph.downloadUrl` works because it's a pre-auth'd URL.
        """
        url = f"{self.base_url}/send/media"
        headers = {"token": self.token, "Content-Type": "application/json"}
        payload = {
            "number": str(number),
            "type": "document",
            "file": str(file_url),
            "docName": str(doc_name),
            "text": str(caption or ""),
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code >= 400:
                self.logger.error(
                    f"send_document failed: {response.status_code} {response.text[:300]}"
                )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"send_document to {number} failed", {"error": str(e)})
            raise
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_uazapi_send_document.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add execution/integrations/uazapi_client.py tests/test_uazapi_send_document.py
git commit -m "feat(uazapi): add send_document for PDF broadcasts via /send/media"
```

---

## Task 4: Implement GraphClient (OAuth + subscriptions + delta + get_item)

**Files:**
- Create: `execution/integrations/graph_client.py`
- Create: `tests/test_graph_client.py`

- [ ] **Step 1: Write failing tests for OAuth token cache**

Create `tests/test_graph_client.py`:

```python
"""Unit tests for GraphClient (OAuth + subscriptions + delta + get_item)."""
from __future__ import annotations

import os
import time
import pytest
from unittest.mock import patch, MagicMock

from execution.integrations.graph_client import GraphClient


@pytest.fixture(autouse=True)
def _env():
    os.environ["GRAPH_TENANT_ID"] = "tenant-xyz"
    os.environ["GRAPH_CLIENT_ID"] = "client-abc"
    os.environ["GRAPH_CLIENT_SECRET"] = "secret-123"
    os.environ["GRAPH_DRIVE_ID"] = "drive-test"
    os.environ["GRAPH_FOLDER_PATH"] = "/SIGCM/test"
    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "cstate-xyz"
    os.environ["ONEDRIVE_WEBHOOK_URL"] = "https://example.com/onedrive/notify"
    yield


def _mock_ok(json_body, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_body
    m.text = str(json_body)
    return m


def test_get_access_token_requests_client_credentials_grant():
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok-1", "expires_in": 3600})) as p:
        client = GraphClient()
        token = client.get_access_token()
    assert token == "tok-1"
    url = p.call_args.args[0]
    assert "tenant-xyz" in url
    assert "oauth2/v2.0/token" in url
    body = p.call_args.kwargs["data"]
    assert body["client_id"] == "client-abc"
    assert body["client_secret"] == "secret-123"
    assert body["grant_type"] == "client_credentials"


def test_access_token_is_cached_until_expiry():
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok-1", "expires_in": 3600})) as p:
        client = GraphClient()
        client.get_access_token()
        client.get_access_token()
        client.get_access_token()
    # Only one token request for three get_access_token calls.
    assert p.call_count == 1


def test_access_token_refetches_after_expiry():
    client = GraphClient()
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok-1", "expires_in": 1})) as p:
        client.get_access_token()
    # Simulate clock skipping past expiry.
    client._token_expires_at = time.time() - 1
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok-2", "expires_in": 3600})):
        t = client.get_access_token()
    assert t == "tok-2"


def test_create_subscription_posts_expected_payload():
    with patch("execution.integrations.graph_client.requests.post") as p:
        p.side_effect = [
            _mock_ok({"access_token": "tok", "expires_in": 3600}),   # token
            _mock_ok({"id": "sub-1", "expirationDateTime": "2026-04-25T00:00:00Z"}, status=201),
        ]
        client = GraphClient()
        sub = client.create_subscription(
            resource="/drives/drive-test/root:/SIGCM/test",
            notification_url="https://example.com/onedrive/notify",
            client_state="cstate-xyz",
        )
    assert sub["id"] == "sub-1"
    body = p.call_args_list[-1].kwargs["json"]
    assert body["changeType"] == "updated"
    assert body["notificationUrl"] == "https://example.com/onedrive/notify"
    assert body["clientState"] == "cstate-xyz"
    assert body["resource"] == "/drives/drive-test/root:/SIGCM/test"
    assert "expirationDateTime" in body


def test_list_subscriptions():
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok", "expires_in": 3600})), \
         patch("execution.integrations.graph_client.requests.get",
               return_value=_mock_ok({"value": [{"id": "s1"}, {"id": "s2"}]})):
        client = GraphClient()
        subs = client.list_subscriptions()
    assert [s["id"] for s in subs] == ["s1", "s2"]


def test_renew_subscription_patches_expiration():
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok", "expires_in": 3600})), \
         patch("execution.integrations.graph_client.requests.patch",
               return_value=_mock_ok({"id": "sub-1", "expirationDateTime": "2026-04-30T00:00:00Z"})) as p:
        client = GraphClient()
        client.renew_subscription("sub-1")
    body = p.call_args.kwargs["json"]
    assert "expirationDateTime" in body


def test_get_folder_delta_parses_items_and_delta_link():
    delta_resp = {
        "value": [
            {"id": "item-a", "name": "a.pdf", "file": {"mimeType": "application/pdf"}},
            {"id": "item-b", "name": "subfolder", "folder": {}},
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/.../delta?token=next-abc",
    }
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok", "expires_in": 3600})), \
         patch("execution.integrations.graph_client.requests.get",
               return_value=_mock_ok(delta_resp)):
        client = GraphClient()
        items, next_token = client.get_folder_delta(
            drive_id="drive-test",
            folder_path="/SIGCM/test",
        )
    assert [i["id"] for i in items] == ["item-a", "item-b"]
    assert next_token == "next-abc"


def test_get_item_returns_item_with_download_url():
    item = {
        "id": "item-a",
        "name": "Minerals_Report.pdf",
        "@microsoft.graph.downloadUrl": "https://cdn.example.com/get?sig=xyz",
    }
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok", "expires_in": 3600})), \
         patch("execution.integrations.graph_client.requests.get",
               return_value=_mock_ok(item)):
        client = GraphClient()
        result = client.get_item("drive-test", "item-a")
    assert result["@microsoft.graph.downloadUrl"].startswith("https://")
```

- [ ] **Step 2: Run tests, confirm they fail with ImportError**

Run: `pytest tests/test_graph_client.py -v`
Expected: All fail with `ImportError: cannot import name 'GraphClient'`.

- [ ] **Step 3: Implement GraphClient**

Create `execution/integrations/graph_client.py`:

```python
"""Microsoft Graph API client.

Used by:
  - webhook/onedrive_pipeline.py (delta query, get_item)
  - webhook/dispatch_document.py (get_item for stale downloadUrl refresh)
  - execution/scripts/onedrive_resubscribe.py (subscription CRUD)

Auth: OAuth2 client-credentials (application permissions).
The Azure app registration must have Files.Read.All application permission
with admin consent.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from ..core.logger import WorkflowLogger
from ..core.retry import retry_with_backoff


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_SUBSCRIPTION_DAYS = 3      # Graph max for drive/item subscriptions


class GraphClient:
    """Thin wrapper around the Microsoft Graph endpoints we care about."""

    def __init__(self):
        self.tenant_id = os.environ.get("GRAPH_TENANT_ID")
        self.client_id = os.environ.get("GRAPH_CLIENT_ID")
        self.client_secret = os.environ.get("GRAPH_CLIENT_SECRET")
        if not all([self.tenant_id, self.client_id, self.client_secret]):
            raise ValueError(
                "GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET must be set"
            )
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self.logger = WorkflowLogger("GraphClient")

    # ── Auth ──

    def get_access_token(self) -> str:
        """Return a cached token, refreshing it up to 60s before expiry."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        }
        resp = requests.post(url, data=data, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_access_token()}"}

    # ── Subscriptions ──

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def create_subscription(
        self,
        resource: str,
        notification_url: str,
        client_state: str,
        expires_in_days: int = DEFAULT_SUBSCRIPTION_DAYS,
        change_type: str = "updated",
    ) -> dict:
        expires = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
        body = {
            "changeType": change_type,
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": expires,
            "clientState": client_state,
        }
        resp = requests.post(
            f"{GRAPH_BASE}/subscriptions",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body,
            timeout=20,
        )
        if resp.status_code >= 400:
            self.logger.error(
                f"create_subscription failed: {resp.status_code} {resp.text[:500]}"
            )
        resp.raise_for_status()
        return resp.json()

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def list_subscriptions(self) -> list[dict]:
        resp = requests.get(
            f"{GRAPH_BASE}/subscriptions",
            headers=self._headers(),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("value", [])

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def renew_subscription(
        self, subscription_id: str, expires_in_days: int = DEFAULT_SUBSCRIPTION_DAYS
    ) -> dict:
        expires = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
        resp = requests.patch(
            f"{GRAPH_BASE}/subscriptions/{subscription_id}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"expirationDateTime": expires},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def delete_subscription(self, subscription_id: str) -> None:
        resp = requests.delete(
            f"{GRAPH_BASE}/subscriptions/{subscription_id}",
            headers=self._headers(),
            timeout=20,
        )
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()

    # ── Drive items ──

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def get_item(self, drive_id: str, item_id: str) -> dict:
        """Return the drive item JSON including a fresh @microsoft.graph.downloadUrl."""
        resp = requests.get(
            f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}",
            headers=self._headers(),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def get_folder_delta(
        self,
        drive_id: str,
        folder_path: str,
        delta_token: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """Return (items_changed_since_last_delta, new_delta_token).

        First call (no delta_token) returns the current state of the folder.
        Subsequent calls return only changed items.
        """
        if delta_token:
            url = delta_token if delta_token.startswith("http") else (
                f"{GRAPH_BASE}/drives/{drive_id}/root:{folder_path}:/delta?token={delta_token}"
            )
        else:
            url = f"{GRAPH_BASE}/drives/{drive_id}/root:{folder_path}:/delta"

        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("value", [])

        next_link = payload.get("@odata.nextLink")
        delta_link = payload.get("@odata.deltaLink")

        while next_link:
            resp = requests.get(next_link, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            page = resp.json()
            items.extend(page.get("value", []))
            next_link = page.get("@odata.nextLink")
            delta_link = page.get("@odata.deltaLink", delta_link)

        # Extract the token from deltaLink for storage.
        next_token: Optional[str] = None
        if delta_link and "token=" in delta_link:
            next_token = delta_link.split("token=", 1)[1].split("&", 1)[0]

        return items, next_token
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_graph_client.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add execution/integrations/graph_client.py tests/test_graph_client.py
git commit -m "feat(graph): add Microsoft Graph API client (OAuth + subscriptions + delta + get_item)"
```

---

## Task 5: Add OneDrive callback data classes

**Files:**
- Modify: `webhook/bot/callback_data.py`

- [ ] **Step 1: Append new callback data classes**

Open `webhook/bot/callback_data.py`. At the end of the file, add:

```python
class OneDriveApprove(CallbackData, prefix="od_ap"):
    """First click from approval card — admin picked a list (or '__all__')."""
    approval_id: str       # UUID of Redis approval:{uuid} key
    list_code: str         # contact_lists.code OR '__all__'


class OneDriveConfirm(CallbackData, prefix="od_cf"):
    """Second click — admin confirmed the envio on the confirmation screen."""
    approval_id: str
    list_code: str


class OneDriveDiscard(CallbackData, prefix="od_dc"):
    """Admin clicked Descartar on the approval card."""
    approval_id: str
```

- [ ] **Step 2: Smoke-test serialization**

Run:
```bash
python -c "
from webhook.bot.callback_data import OneDriveApprove, OneDriveConfirm, OneDriveDiscard
a = OneDriveApprove(approval_id='abc-123', list_code='minerals_report')
s = a.pack()
print('pack:', s)
parsed = OneDriveApprove.unpack(s)
print('unpack:', parsed.approval_id, parsed.list_code)
assert parsed.approval_id == 'abc-123'
assert parsed.list_code == 'minerals_report'
print('OK')
"
```
Expected: `OK` printed, with pack/unpack output visible. If you see "callback data too long" raise `IncludingCallbackData`, use shorter approval_ids — the aiogram limit is 64 bytes.

- [ ] **Step 3: Commit**

```bash
git add webhook/bot/callback_data.py
git commit -m "feat(bot): add OneDrive approval/confirm/discard callback data classes"
```

---

## Task 6: Implement onedrive pipeline (detection + approval card)

**Files:**
- Create: `webhook/onedrive_pipeline.py`
- Create: `tests/test_onedrive_pipeline.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_onedrive_pipeline.py`:

```python
"""Unit tests for webhook/onedrive_pipeline.py — detection & approval card."""
from __future__ import annotations

import json
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, MagicMock, patch

# Imports under test — these must exist after the task is implemented.
# Use sys.path manipulation pattern (conftest already adds webhook/ to sys.path).


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def sample_pdf_item():
    return {
        "id": "item-minerals-042226",
        "name": "Minerals_Report_042226.pdf",
        "size": 1258291,
        "file": {"mimeType": "application/pdf"},
        "@microsoft.graph.downloadUrl": "https://cdn.example.com/x?sig=abc",
    }


@pytest.fixture
def sample_folder_item():
    return {"id": "folder-1", "name": "Subfolder", "folder": {"childCount": 0}}


@pytest.fixture
def fake_contacts_repo():
    repo = MagicMock()
    repo.list_lists.return_value = [
        MagicMock(code="minerals_report", label="Minerals Report", member_count=45),
        MagicMock(code="solid_fuels",     label="Solid Fuels",     member_count=12),
        MagicMock(code="time_interno",    label="Time Interno",    member_count=8),
    ]
    repo.list_active.return_value = [MagicMock() for _ in range(62)]
    return repo


@pytest.mark.asyncio
async def test_filter_drops_folders(sample_folder_item):
    from webhook.onedrive_pipeline import _is_pdf_file
    assert _is_pdf_file(sample_folder_item) is False


@pytest.mark.asyncio
async def test_filter_accepts_pdf_by_mime_type(sample_pdf_item):
    from webhook.onedrive_pipeline import _is_pdf_file
    assert _is_pdf_file(sample_pdf_item) is True


@pytest.mark.asyncio
async def test_filter_accepts_pdf_by_extension():
    from webhook.onedrive_pipeline import _is_pdf_file
    item = {"name": "Relatorio.PDF", "file": {"mimeType": "application/octet-stream"}}
    assert _is_pdf_file(item) is True


@pytest.mark.asyncio
async def test_filter_rejects_non_pdf_files():
    from webhook.onedrive_pipeline import _is_pdf_file
    item = {"name": "image.png", "file": {"mimeType": "image/png"}}
    assert _is_pdf_file(item) is False


@pytest.mark.asyncio
async def test_dedup_skips_already_seen_items(redis_client, sample_pdf_item):
    from webhook.onedrive_pipeline import _is_new_item
    await redis_client.set(f"seen:onedrive:{sample_pdf_item['id']}", "1")
    assert await _is_new_item(redis_client, sample_pdf_item["id"]) is False


@pytest.mark.asyncio
async def test_dedup_accepts_unseen_items(redis_client, sample_pdf_item):
    from webhook.onedrive_pipeline import _is_new_item
    assert await _is_new_item(redis_client, sample_pdf_item["id"]) is True


@pytest.mark.asyncio
async def test_mark_seen_sets_30day_ttl(redis_client, sample_pdf_item):
    from webhook.onedrive_pipeline import _mark_seen
    await _mark_seen(redis_client, sample_pdf_item["id"])
    ttl = await redis_client.ttl(f"seen:onedrive:{sample_pdf_item['id']}")
    assert 29 * 24 * 3600 < ttl <= 30 * 24 * 3600


@pytest.mark.asyncio
async def test_create_approval_stores_state_with_48h_ttl(redis_client, sample_pdf_item):
    from webhook.onedrive_pipeline import create_approval_state
    approval_id = await create_approval_state(
        redis_client, sample_pdf_item, drive_id="drive-test"
    )
    assert approval_id
    stored = await redis_client.get(f"approval:{approval_id}")
    data = json.loads(stored)
    assert data["drive_item_id"] == sample_pdf_item["id"]
    assert data["filename"] == sample_pdf_item["name"]
    assert data["downloadUrl"] == sample_pdf_item["@microsoft.graph.downloadUrl"]
    assert data["status"] == "pending"
    ttl = await redis_client.ttl(f"approval:{approval_id}")
    assert 47 * 3600 < ttl <= 48 * 3600


@pytest.mark.asyncio
async def test_build_approval_keyboard_has_all_lists_plus_todos_plus_discard(
    fake_contacts_repo
):
    from webhook.onedrive_pipeline import build_approval_keyboard
    kb = build_approval_keyboard(
        approval_id="abc-123",
        contacts_repo=fake_contacts_repo,
    )
    # Inline keyboard is a list of rows of buttons.
    flat_labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Minerals Report" in l and "45" in l for l in flat_labels)
    assert any("Solid Fuels" in l and "12" in l for l in flat_labels)
    assert any("Time Interno" in l and "8" in l for l in flat_labels)
    assert any("Todos" in l and "62" in l for l in flat_labels)
    assert any("Descartar" in l for l in flat_labels)


@pytest.mark.asyncio
async def test_process_notification_rejects_wrong_client_state():
    from webhook.onedrive_pipeline import validate_notification
    payload = {"value": [{"clientState": "WRONG"}]}
    assert validate_notification(payload, expected_client_state="GOOD") is False


@pytest.mark.asyncio
async def test_process_notification_accepts_correct_client_state():
    from webhook.onedrive_pipeline import validate_notification
    payload = {"value": [{"clientState": "GOOD"}, {"clientState": "GOOD"}]}
    assert validate_notification(payload, expected_client_state="GOOD") is True
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_onedrive_pipeline.py -v`
Expected: All fail with ImportError.

- [ ] **Step 3: Implement the pipeline**

Create `webhook/onedrive_pipeline.py`:

```python
"""OneDrive PDF detection + approval card dispatch.

Runs inside the Railway aiohttp app (webhook/bot/main.py). Triggered by
webhook/routes/onedrive.py after the HTTP handler responds 202.

Responsibilities:
  1. Validate the Graph change-notification payload (clientState).
  2. Query the Graph delta endpoint for what changed.
  3. Filter for new PDFs (not seen before).
  4. Create a Redis approval state (48h TTL).
  5. Send the Telegram approval card to the admin.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Optional, Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from execution.core.event_bus import EventBus
from execution.integrations.graph_client import GraphClient
from execution.integrations.contacts_repo import ContactsRepo

from bot.callback_data import OneDriveApprove, OneDriveDiscard
from bot.config import get_bot


ALL_CODE = "__all__"                        # Special list_code for Todos
SEEN_TTL_SECONDS = 30 * 24 * 3600           # 30 days
APPROVAL_TTL_SECONDS = 48 * 3600            # 48 hours


# ── Filters ──

def _is_pdf_file(item: dict) -> bool:
    """True iff item is a file (not folder) AND is a PDF."""
    if "folder" in item:
        return False
    if "file" not in item:
        return False
    mime = (item.get("file") or {}).get("mimeType", "")
    name = item.get("name", "")
    return mime == "application/pdf" or name.lower().endswith(".pdf")


# ── Dedup ──

async def _is_new_item(redis_client, item_id: str) -> bool:
    return (await redis_client.get(f"seen:onedrive:{item_id}")) is None


async def _mark_seen(redis_client, item_id: str) -> None:
    await redis_client.set(f"seen:onedrive:{item_id}", "1", ex=SEEN_TTL_SECONDS)


# ── Approval state ──

async def create_approval_state(
    redis_client, item: dict, drive_id: str
) -> str:
    approval_id = uuid.uuid4().hex[:12]     # 12-char keeps CallbackData under 64 bytes
    state = {
        "drive_id": drive_id,
        "drive_item_id": item["id"],
        "filename": item["name"],
        "size": item.get("size", 0),
        "downloadUrl": item.get("@microsoft.graph.downloadUrl", ""),
        "downloadUrl_fetched_at": _now_iso(),
        "status": "pending",
        "created_at": _now_iso(),
    }
    await redis_client.set(
        f"approval:{approval_id}",
        json.dumps(state),
        ex=APPROVAL_TTL_SECONDS,
    )
    return approval_id


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ── Approval card keyboard ──

def build_approval_keyboard(
    approval_id: str,
    contacts_repo: ContactsRepo,
) -> InlineKeyboardMarkup:
    """Build the inline keyboard with one button per contact_list + Todos + Descartar."""
    builder = InlineKeyboardBuilder()
    lists = contacts_repo.list_lists()
    for lst in lists:
        label = f"📊 {lst.label} ({lst.member_count})"
        builder.button(
            text=label,
            callback_data=OneDriveApprove(
                approval_id=approval_id, list_code=lst.code
            ).pack(),
        )
    total_active = len(contacts_repo.list_active())
    builder.button(
        text=f"🌐 Todos ({total_active})",
        callback_data=OneDriveApprove(
            approval_id=approval_id, list_code=ALL_CODE
        ).pack(),
    )
    builder.button(
        text="❌ Descartar",
        callback_data=OneDriveDiscard(approval_id=approval_id).pack(),
    )
    builder.adjust(1)  # one button per row — readable on mobile
    return builder.as_markup()


def build_approval_text(item: dict) -> str:
    size_mb = (item.get("size", 0) or 0) / (1024 * 1024)
    size_str = f"{size_mb:.1f} MB" if size_mb >= 0.1 else f"{item.get('size', 0)} bytes"
    return (
        f"📄 *Novo PDF detectado*\n\n"
        f"Arquivo: `{item['name']}`\n"
        f"Tamanho: {size_str}\n\n"
        f"Escolha a lista de envio:"
    )


# ── Notification validation ──

def validate_notification(payload: dict, expected_client_state: str) -> bool:
    """Every notification in payload['value'] must carry our clientState."""
    values = payload.get("value", [])
    if not values:
        return False
    return all(v.get("clientState") == expected_client_state for v in values)


# ── Main entrypoint (called from routes/onedrive.py) ──

async def process_notification(payload: dict) -> None:
    """Process a Graph change-notification payload.

    Safe to call concurrently; idempotent via Redis dedup.
    """
    expected_state = os.environ["GRAPH_WEBHOOK_CLIENT_STATE"]
    if not validate_notification(payload, expected_state):
        return

    bus = EventBus(workflow="onedrive_webhook")
    bus.emit("webhook_received", detail={"count": len(payload.get("value", []))})

    try:
        from redis.asyncio import Redis
        redis_client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

        graph = GraphClient()
        drive_id = os.environ["GRAPH_DRIVE_ID"]
        folder_path = os.environ["GRAPH_FOLDER_PATH"]

        delta_token = await redis_client.get("onedrive:delta_token:sigcm")
        items, next_token = graph.get_folder_delta(
            drive_id=drive_id,
            folder_path=folder_path,
            delta_token=delta_token,
        )
        if next_token:
            await redis_client.set("onedrive:delta_token:sigcm", next_token)

        bus.emit("delta_query_done", detail={"item_count": len(items)})

        contacts_repo = ContactsRepo()
        bot = get_bot()
        admin_chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

        for item in items:
            if not _is_pdf_file(item):
                continue
            if not await _is_new_item(redis_client, item["id"]):
                bus.emit("duplicate_webhook", detail={"item_id": item["id"]})
                continue
            await _mark_seen(redis_client, item["id"])

            approval_id = await create_approval_state(
                redis_client, item, drive_id=drive_id
            )
            bus.emit("approval_created", detail={
                "approval_id": approval_id,
                "filename": item["name"],
            })

            await bot.send_message(
                chat_id=admin_chat_id,
                text=build_approval_text(item),
                reply_markup=build_approval_keyboard(approval_id, contacts_repo),
                parse_mode="Markdown",
            )

        bus.emit("webhook_processed")
    except Exception as exc:
        bus.emit("webhook_crashed", level="error", detail={"error": str(exc)})
        raise
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_onedrive_pipeline.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add webhook/onedrive_pipeline.py tests/test_onedrive_pipeline.py
git commit -m "feat(onedrive): add detection pipeline + approval card builder"
```

---

## Task 7: Implement document dispatch (fan-out)

**Files:**
- Create: `webhook/dispatch_document.py`
- Create: `tests/test_dispatch_document.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_dispatch_document.py`:

```python
"""Unit tests for webhook/dispatch_document.py — fan-out with idempotency."""
from __future__ import annotations

import json
import pytest
import fakeredis.aioredis
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
async def seeded_approval(redis_client):
    state = {
        "drive_id": "drive-test",
        "drive_item_id": "item-abc",
        "filename": "Minerals_Report.pdf",
        "size": 1024,
        "downloadUrl": "https://cdn.example.com/fresh?sig=x",
        "downloadUrl_fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": "dispatching",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.set("approval:abc12", json.dumps(state))
    return "abc12", state


@pytest.fixture
def mock_uazapi():
    client = MagicMock()
    client.send_document.return_value = {"messageId": "m1"}
    return client


@pytest.fixture
def mock_contacts_repo():
    repo = MagicMock()
    repo.list_by_list_code.return_value = [
        MagicMock(name="Alice", phone_uazapi="5511111111111"),
        MagicMock(name="Bob",   phone_uazapi="5511222222222"),
    ]
    repo.list_active.return_value = [
        MagicMock(name="Alice", phone_uazapi="5511111111111"),
        MagicMock(name="Bob",   phone_uazapi="5511222222222"),
        MagicMock(name="Carol", phone_uazapi="5511333333333"),
    ]
    return repo


@pytest.mark.asyncio
async def test_dispatch_sends_to_list_members(
    redis_client, seeded_approval, mock_uazapi, mock_contacts_repo
):
    from webhook.dispatch_document import dispatch_document
    approval_id, _ = seeded_approval
    with patch("webhook.dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("webhook.dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("webhook.dispatch_document._redis", return_value=redis_client):
        result = await dispatch_document(approval_id, "minerals_report")
    assert mock_uazapi.send_document.call_count == 2
    assert result["sent"] == 2
    assert result["failed"] == 0


@pytest.mark.asyncio
async def test_dispatch_all_uses_list_active(
    redis_client, seeded_approval, mock_uazapi, mock_contacts_repo
):
    from webhook.dispatch_document import dispatch_document, ALL_CODE
    approval_id, _ = seeded_approval
    with patch("webhook.dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("webhook.dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("webhook.dispatch_document._redis", return_value=redis_client):
        result = await dispatch_document(approval_id, ALL_CODE)
    assert mock_uazapi.send_document.call_count == 3
    assert result["sent"] == 3


@pytest.mark.asyncio
async def test_dispatch_idempotency_blocks_duplicate_sends(
    redis_client, seeded_approval, mock_uazapi, mock_contacts_repo
):
    from webhook.dispatch_document import dispatch_document
    approval_id, _ = seeded_approval
    with patch("webhook.dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("webhook.dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("webhook.dispatch_document._redis", return_value=redis_client):
        await dispatch_document(approval_id, "minerals_report")
        # run again — all 2 should be blocked by idempotency keys
        mock_uazapi.send_document.reset_mock()
        result = await dispatch_document(approval_id, "minerals_report")
    assert mock_uazapi.send_document.call_count == 0
    assert result["sent"] == 0
    assert result["skipped"] == 2


@pytest.mark.asyncio
async def test_dispatch_refetches_stale_download_url(
    redis_client, mock_uazapi, mock_contacts_repo
):
    from webhook.dispatch_document import dispatch_document
    # seed an approval with a 60-minute-old downloadUrl
    stale_state = {
        "drive_id": "drive-test",
        "drive_item_id": "item-abc",
        "filename": "x.pdf",
        "size": 100,
        "downloadUrl": "https://cdn.example.com/stale?sig=old",
        "downloadUrl_fetched_at": (
            datetime.now(timezone.utc) - timedelta(minutes=60)
        ).isoformat(),
        "status": "dispatching",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.set("approval:stale", json.dumps(stale_state))

    mock_graph = MagicMock()
    mock_graph.get_item.return_value = {
        "id": "item-abc",
        "name": "x.pdf",
        "@microsoft.graph.downloadUrl": "https://cdn.example.com/FRESH",
    }
    with patch("webhook.dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("webhook.dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("webhook.dispatch_document.GraphClient", return_value=mock_graph), \
         patch("webhook.dispatch_document._redis", return_value=redis_client):
        await dispatch_document("stale", "minerals_report")
    mock_graph.get_item.assert_called_once_with("drive-test", "item-abc")
    # first send should use the fresh URL
    call_kwargs = mock_uazapi.send_document.call_args_list[0].kwargs
    assert call_kwargs["file_url"] == "https://cdn.example.com/FRESH"


@pytest.mark.asyncio
async def test_dispatch_missing_approval_raises():
    from webhook.dispatch_document import dispatch_document, ApprovalExpiredError
    empty = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with patch("webhook.dispatch_document._redis", return_value=empty):
        with pytest.raises(ApprovalExpiredError):
            await dispatch_document("missing-id", "minerals_report")
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_dispatch_document.py -v`
Expected: All fail with ImportError.

- [ ] **Step 3: Implement dispatch_document**

Create `webhook/dispatch_document.py`:

```python
"""Document (PDF) fan-out dispatcher for the OneDrive approval flow.

Loads the Redis approval state, refreshes the Graph downloadUrl if stale,
fans out to the selected list (or all active contacts for ALL_CODE) with
concurrency=5, and applies per-recipient idempotency keys.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from execution.core.logger import WorkflowLogger
from execution.integrations.contacts_repo import ContactsRepo
from execution.integrations.graph_client import GraphClient
from execution.integrations.uazapi_client import UazapiClient


ALL_CODE = "__all__"
CONCURRENCY = 5
DOWNLOAD_URL_STALE_AFTER_SECONDS = 50 * 60     # 50 min safety margin on Graph's ~1h TTL
IDEMPOTENCY_TTL_SECONDS = 24 * 3600


class ApprovalExpiredError(Exception):
    """approval:{uuid} key is missing in Redis (TTL expired or never existed)."""


def _redis():
    """Returns an async Redis client. Factored out for test patchability."""
    from redis.asyncio import Redis
    return Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


def _idempotency_key(phone: str, drive_item_id: str) -> str:
    raw = f"{phone}|{drive_item_id}".encode()
    return f"idempotency:{hashlib.sha1(raw).hexdigest()}"


async def _claim_idempotency(redis_client, phone: str, drive_item_id: str) -> bool:
    """True if this (phone, drive_item_id) hasn't been sent before (claim succeeds)."""
    key = _idempotency_key(phone, drive_item_id)
    return bool(await redis_client.set(key, "1", nx=True, ex=IDEMPOTENCY_TTL_SECONDS))


def _is_stale(iso_ts: str) -> bool:
    try:
        fetched = datetime.fromisoformat(iso_ts)
        age = datetime.now(timezone.utc) - fetched
        return age.total_seconds() > DOWNLOAD_URL_STALE_AFTER_SECONDS
    except Exception:
        return True


async def _refresh_download_url(redis_client, approval_id: str, state: dict) -> dict:
    """Fetch a fresh downloadUrl via Graph, update Redis state, return new state."""
    graph = GraphClient()
    item = graph.get_item(state["drive_id"], state["drive_item_id"])
    state["downloadUrl"] = item["@microsoft.graph.downloadUrl"]
    state["downloadUrl_fetched_at"] = datetime.now(timezone.utc).isoformat()
    await redis_client.set(
        f"approval:{approval_id}",
        json.dumps(state),
        keepttl=True,
    )
    return state


async def dispatch_document(
    approval_id: str,
    list_code: str,
) -> dict:
    """Fan-out PDF broadcast. Returns counter dict for caller to display."""
    logger = WorkflowLogger("DispatchDocument")
    redis_client = _redis()

    raw = await redis_client.get(f"approval:{approval_id}")
    if not raw:
        raise ApprovalExpiredError(approval_id)
    state = json.loads(raw)

    if _is_stale(state.get("downloadUrl_fetched_at", "")):
        state = await _refresh_download_url(redis_client, approval_id, state)

    contacts_repo = ContactsRepo()
    if list_code == ALL_CODE:
        recipients = contacts_repo.list_active()
    else:
        recipients = contacts_repo.list_by_list_code(list_code)

    uazapi = UazapiClient()
    sem = asyncio.Semaphore(CONCURRENCY)
    results = {"sent": 0, "failed": 0, "skipped": 0, "errors": []}

    async def _send_one(contact):
        async with sem:
            claimed = await _claim_idempotency(
                redis_client, contact.phone_uazapi, state["drive_item_id"]
            )
            if not claimed:
                results["skipped"] += 1
                return
            try:
                await asyncio.to_thread(
                    uazapi.send_document,
                    number=contact.phone_uazapi,
                    file_url=state["downloadUrl"],
                    doc_name=state["filename"],
                )
                results["sent"] += 1
            except Exception as exc:
                logger.error(
                    f"send_document to {contact.phone_uazapi} failed: {exc}"
                )
                results["failed"] += 1
                results["errors"].append({
                    "phone": contact.phone_uazapi,
                    "error": str(exc)[:200],
                })

    await asyncio.gather(*[_send_one(c) for c in recipients])
    return results
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_dispatch_document.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add webhook/dispatch_document.py tests/test_dispatch_document.py
git commit -m "feat(onedrive): add document dispatch with idempotency + stale URL refresh"
```

---

## Task 8: Implement approval callbacks router

**Files:**
- Create: `webhook/bot/routers/callbacks_onedrive.py`
- Create: `tests/test_onedrive_callbacks.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_onedrive_callbacks.py`:

```python
"""Unit tests for callbacks_onedrive router handlers."""
from __future__ import annotations

import json
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
async def seeded_pending(redis_client):
    state = {
        "drive_id": "drive-test",
        "drive_item_id": "item-1",
        "filename": "Test.pdf",
        "size": 1024,
        "downloadUrl": "https://x",
        "downloadUrl_fetched_at": "2026-04-22T00:00:00+00:00",
        "status": "pending",
        "created_at": "2026-04-22T00:00:00+00:00",
    }
    await redis_client.set("approval:abc12", json.dumps(state))
    return "abc12"


@pytest.mark.asyncio
async def test_on_approve_shows_confirm_screen(
    mock_bot, mock_callback_query, redis_client, seeded_pending
):
    from bot.routers.callbacks_onedrive import on_approve
    from bot.callback_data import OneDriveApprove

    cb_data = OneDriveApprove(approval_id=seeded_pending, list_code="minerals_report")
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    mock_repo = MagicMock()
    mock_repo.list_by_list_code.return_value = [MagicMock() for _ in range(3)]
    mock_repo.list_lists.return_value = [
        MagicMock(code="minerals_report", label="Minerals Report", member_count=3),
    ]

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo):
        await on_approve(cb, cb_data)

    mock_bot.edit_message_text.assert_called_once()
    edited = mock_bot.edit_message_text.call_args.kwargs["text"]
    assert "Confirmar" in edited
    assert "Minerals Report" in edited

    stored = json.loads(await redis_client.get(f"approval:{seeded_pending}"))
    assert stored["status"] == "awaiting_confirm"


@pytest.mark.asyncio
async def test_on_approve_all_uses_list_active_count(
    mock_bot, mock_callback_query, redis_client, seeded_pending
):
    from bot.routers.callbacks_onedrive import on_approve
    from bot.callback_data import OneDriveApprove

    cb_data = OneDriveApprove(approval_id=seeded_pending, list_code="__all__")
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    mock_repo = MagicMock()
    mock_repo.list_active.return_value = [MagicMock() for _ in range(62)]

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.ContactsRepo", return_value=mock_repo):
        await on_approve(cb, cb_data)

    edited = mock_bot.edit_message_text.call_args.kwargs["text"]
    assert "62" in edited
    assert "Todos" in edited or "todos" in edited.lower()


@pytest.mark.asyncio
async def test_on_discard_edits_card_and_deletes_state(
    mock_bot, mock_callback_query, redis_client, seeded_pending
):
    from bot.routers.callbacks_onedrive import on_discard
    from bot.callback_data import OneDriveDiscard

    cb_data = OneDriveDiscard(approval_id=seeded_pending)
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client):
        await on_discard(cb, cb_data)

    edited = mock_bot.edit_message_text.call_args.kwargs["text"]
    assert "Descartado" in edited or "❌" in edited
    assert (await redis_client.get(f"approval:{seeded_pending}")) is None


@pytest.mark.asyncio
async def test_on_confirm_triggers_dispatch(
    mock_bot, mock_callback_query, redis_client, seeded_pending
):
    from bot.routers.callbacks_onedrive import on_confirm
    from bot.callback_data import OneDriveConfirm

    cb_data = OneDriveConfirm(approval_id=seeded_pending, list_code="minerals_report")
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    mock_dispatch = AsyncMock(return_value={"sent": 3, "failed": 0, "skipped": 0})
    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client), \
         patch("bot.routers.callbacks_onedrive.dispatch_document", mock_dispatch):
        await on_confirm(cb, cb_data)

    mock_dispatch.assert_awaited_once()
    args, kwargs = mock_dispatch.call_args
    assert kwargs.get("approval_id") == seeded_pending or args[0] == seeded_pending


@pytest.mark.asyncio
async def test_expired_approval_shows_warning(
    mock_bot, mock_callback_query, redis_client
):
    from bot.routers.callbacks_onedrive import on_approve
    from bot.callback_data import OneDriveApprove

    cb_data = OneDriveApprove(approval_id="nonexistent", list_code="minerals_report")
    cb = mock_callback_query(data=cb_data.pack())
    cb.bot = mock_bot

    with patch("bot.routers.callbacks_onedrive._redis", return_value=redis_client):
        await on_approve(cb, cb_data)

    cb.answer.assert_called()
    answered_text = cb.answer.call_args.kwargs.get("text", "") or cb.answer.call_args.args[0]
    assert "expirada" in str(answered_text).lower() or "expired" in str(answered_text).lower()
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_onedrive_callbacks.py -v`
Expected: All fail with ImportError.

- [ ] **Step 3: Implement the router**

Create `webhook/bot/routers/callbacks_onedrive.py`:

```python
"""Callback handlers for the OneDrive PDF approval flow."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from aiogram import Router
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.callback_data import OneDriveApprove, OneDriveConfirm, OneDriveDiscard
from bot.middlewares.auth import RoleMiddleware
from execution.integrations.contacts_repo import ContactsRepo

from dispatch_document import dispatch_document, ALL_CODE


logger = logging.getLogger(__name__)

callbacks_onedrive_router = Router(name="callbacks_onedrive")
callbacks_onedrive_router.callback_query.middleware(
    RoleMiddleware(allowed_roles={"admin"})
)


def _redis():
    from redis.asyncio import Redis
    return Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load_state(redis_client, approval_id: str) -> dict | None:
    raw = await redis_client.get(f"approval:{approval_id}")
    return json.loads(raw) if raw else None


async def _save_state(redis_client, approval_id: str, state: dict) -> None:
    await redis_client.set(
        f"approval:{approval_id}",
        json.dumps(state),
        keepttl=True,
    )


def _list_label(list_code: str, contacts_repo: ContactsRepo) -> tuple[str, int]:
    if list_code == ALL_CODE:
        return "Todos", len(contacts_repo.list_active())
    for lst in contacts_repo.list_lists():
        if lst.code == list_code:
            return lst.label, lst.member_count
    return list_code, 0


@callbacks_onedrive_router.callback_query(OneDriveApprove.filter())
async def on_approve(query: CallbackQuery, callback_data: OneDriveApprove):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    if not state:
        await query.answer(text="⚠️ Aprovação expirada", show_alert=True)
        return

    contacts_repo = ContactsRepo()
    label, count = _list_label(callback_data.list_code, contacts_repo)

    state["status"] = "awaiting_confirm"
    await _save_state(redis_client, callback_data.approval_id, state)

    text = (
        f"⚠️ *Confirmar envio?*\n\n"
        f"`{state['filename']}`\n"
        f"→ {label} ({count} contatos)"
    )
    kb = InlineKeyboardBuilder()
    kb.button(
        text="✅ Enviar",
        callback_data=OneDriveConfirm(
            approval_id=callback_data.approval_id,
            list_code=callback_data.list_code,
        ).pack(),
    )
    kb.button(
        text="◀ Voltar",
        callback_data=OneDriveDiscard(approval_id=callback_data.approval_id).pack(),
    )
    kb.adjust(2)

    await query.bot.edit_message_text(
        text=text,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        reply_markup=kb.as_markup(),
        parse_mode="Markdown",
    )
    await query.answer()


@callbacks_onedrive_router.callback_query(OneDriveConfirm.filter())
async def on_confirm(query: CallbackQuery, callback_data: OneDriveConfirm):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    if not state:
        await query.answer(text="⚠️ Aprovação expirada", show_alert=True)
        return
    if state.get("status") == "dispatching":
        await query.answer(text="Já em andamento…", show_alert=True)
        return

    state["status"] = "dispatching"
    await _save_state(redis_client, callback_data.approval_id, state)

    contacts_repo = ContactsRepo()
    label, _ = _list_label(callback_data.list_code, contacts_repo)

    await query.bot.edit_message_text(
        text=f"📤 Enviando *{state['filename']}* → {label}…",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await query.answer()

    try:
        result = await dispatch_document(
            approval_id=callback_data.approval_id,
            list_code=callback_data.list_code,
        )
    except Exception as exc:
        logger.exception("dispatch_document failed")
        await query.bot.edit_message_text(
            text=f"❌ Falha no envio: {type(exc).__name__}: {str(exc)[:200]}",
            chat_id=query.message.chat.id,
            message_id=query.message.message_id,
            parse_mode=None,
        )
        return

    total = result["sent"] + result["failed"] + result["skipped"]
    summary = (
        f"✅ *Enviado* — {state['filename']}\n"
        f"Lista: {label}\n"
        f"{result['sent']}/{total} sucesso"
    )
    if result["failed"]:
        summary += f" · {result['failed']} falhas"
    if result["skipped"]:
        summary += f" · {result['skipped']} já enviados antes"

    await query.bot.edit_message_text(
        text=summary,
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await redis_client.delete(f"approval:{callback_data.approval_id}")


@callbacks_onedrive_router.callback_query(OneDriveDiscard.filter())
async def on_discard(query: CallbackQuery, callback_data: OneDriveDiscard):
    redis_client = _redis()
    state = await _load_state(redis_client, callback_data.approval_id)
    filename = state.get("filename", "(expirado)") if state else "(expirado)"

    await redis_client.delete(f"approval:{callback_data.approval_id}")

    await query.bot.edit_message_text(
        text=f"❌ Descartado às {datetime.now().strftime('%H:%M')}\n`{filename}`",
        chat_id=query.message.chat.id,
        message_id=query.message.message_id,
        parse_mode="Markdown",
    )
    await query.answer()
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_onedrive_callbacks.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add webhook/bot/routers/callbacks_onedrive.py tests/test_onedrive_callbacks.py
git commit -m "feat(bot): add OneDrive approval/confirm/discard callback handlers"
```

---

## Task 9: Implement the HTTP webhook endpoint

**Files:**
- Create: `webhook/routes/onedrive.py`
- Create: `tests/test_onedrive_route.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_onedrive_route.py`:

```python
"""Unit tests for webhook/routes/onedrive.py (aiohttp handler)."""
from __future__ import annotations

import os
import json
import pytest
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.fixture(autouse=True)
def _env():
    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "good-state"
    yield


@pytest.fixture
async def client():
    from webhook.routes.onedrive import setup_routes
    app = web.Application()
    setup_routes(app)
    async with TestClient(TestServer(app)) as c:
        yield c


@pytest.mark.asyncio
async def test_validation_token_echoed_as_plaintext(client):
    """Initial Graph handshake — must echo validationToken in <10s."""
    resp = await client.post(
        "/onedrive/notify",
        params={"validationToken": "abc-123-xyz"},
    )
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/plain")
    body = await resp.text()
    assert body == "abc-123-xyz"


@pytest.mark.asyncio
async def test_notification_with_good_client_state_returns_202(client):
    payload = {"value": [{"clientState": "good-state", "resource": "..."}]}
    with patch(
        "webhook.routes.onedrive.process_notification",
        new=AsyncMock(),
    ) as proc:
        resp = await client.post("/onedrive/notify", json=payload)
    assert resp.status == 202
    # Give the task a moment to run.
    await _wait_for_task()
    proc.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_with_bad_client_state_returns_401(client):
    payload = {"value": [{"clientState": "WRONG", "resource": "..."}]}
    with patch(
        "webhook.routes.onedrive.process_notification",
        new=AsyncMock(),
    ) as proc:
        resp = await client.post("/onedrive/notify", json=payload)
    assert resp.status == 401
    proc.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_payload_returns_400(client):
    resp = await client.post("/onedrive/notify", data=b"")
    assert resp.status == 400


async def _wait_for_task():
    import asyncio
    await asyncio.sleep(0.05)
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_onedrive_route.py -v`
Expected: All fail with ImportError.

- [ ] **Step 3: Implement the route**

Create `webhook/routes/onedrive.py`:

```python
"""aiohttp route for Microsoft Graph change notifications on the OneDrive folder.

Mounted at POST /onedrive/notify.

Handles two request types:
  1. Graph validation handshake — `?validationToken=<token>`. Must echo the token
     plaintext within 10 seconds, else Graph refuses to create the subscription.
  2. Actual change notifications — JSON body with `value[]`. We validate the
     shared `clientState`, return 202 Accepted immediately, and spawn the
     detection pipeline asynchronously.
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from onedrive_pipeline import process_notification, validate_notification


logger = logging.getLogger(__name__)


async def onedrive_notify(request: web.Request) -> web.Response:
    validation_token = request.query.get("validationToken")
    if validation_token:
        return web.Response(text=validation_token, content_type="text/plain")

    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")

    if not payload:
        return web.Response(status=400, text="empty payload")

    expected_state = os.environ.get("GRAPH_WEBHOOK_CLIENT_STATE", "")
    if not validate_notification(payload, expected_state):
        logger.warning("onedrive webhook rejected: bad clientState")
        return web.Response(status=401, text="unauthorized")

    # Spawn the pipeline without blocking the HTTP response.
    asyncio.create_task(_safe_process(payload))
    return web.Response(status=202, text="accepted")


async def _safe_process(payload: dict) -> None:
    try:
        await process_notification(payload)
    except Exception:
        logger.exception("process_notification crashed")


def setup_routes(app: web.Application) -> None:
    app.router.add_post("/onedrive/notify", onedrive_notify)
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_onedrive_route.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add webhook/routes/onedrive.py tests/test_onedrive_route.py
git commit -m "feat(onedrive): add /onedrive/notify webhook endpoint"
```

---

## Task 10: Implement the subscription resubscribe cron script

**Files:**
- Create: `execution/scripts/onedrive_resubscribe.py`
- Create: `tests/test_onedrive_resubscribe.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_onedrive_resubscribe.py`:

```python
"""Unit tests for execution/scripts/onedrive_resubscribe.py."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    os.environ["GRAPH_DRIVE_ID"] = "drive-test"
    os.environ["GRAPH_FOLDER_PATH"] = "/SIGCM/test"
    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "cstate-xyz"
    os.environ["ONEDRIVE_WEBHOOK_URL"] = "https://example.com/onedrive/notify"
    yield


def test_renews_near_expiring_subscription():
    from execution.scripts import onedrive_resubscribe

    sub_near_expiry = {
        "id": "sub-1",
        "notificationUrl": "https://example.com/onedrive/notify",
        "expirationDateTime": (
            datetime.now(timezone.utc) + timedelta(hours=10)
        ).isoformat(),
    }
    graph = MagicMock()
    graph.list_subscriptions.return_value = [sub_near_expiry]

    with patch("execution.scripts.onedrive_resubscribe.GraphClient",
               return_value=graph):
        onedrive_resubscribe._run()

    graph.renew_subscription.assert_called_once_with("sub-1")
    graph.create_subscription.assert_not_called()


def test_leaves_far_expiring_subscription_alone():
    from execution.scripts import onedrive_resubscribe

    sub_far_expiry = {
        "id": "sub-1",
        "notificationUrl": "https://example.com/onedrive/notify",
        "expirationDateTime": (
            datetime.now(timezone.utc) + timedelta(days=2, hours=12)
        ).isoformat(),
    }
    graph = MagicMock()
    graph.list_subscriptions.return_value = [sub_far_expiry]

    with patch("execution.scripts.onedrive_resubscribe.GraphClient",
               return_value=graph):
        onedrive_resubscribe._run()

    graph.renew_subscription.assert_not_called()
    graph.create_subscription.assert_not_called()


def test_creates_subscription_when_none_match_our_url():
    from execution.scripts import onedrive_resubscribe

    other_sub = {
        "id": "sub-other",
        "notificationUrl": "https://other.example.com/hook",
        "expirationDateTime": (
            datetime.now(timezone.utc) + timedelta(days=2)
        ).isoformat(),
    }
    graph = MagicMock()
    graph.list_subscriptions.return_value = [other_sub]

    with patch("execution.scripts.onedrive_resubscribe.GraphClient",
               return_value=graph):
        onedrive_resubscribe._run()

    graph.create_subscription.assert_called_once()
    kwargs = graph.create_subscription.call_args.kwargs
    assert kwargs["notification_url"] == "https://example.com/onedrive/notify"
    assert kwargs["client_state"] == "cstate-xyz"
    assert "drive-test" in kwargs["resource"]
    assert "/SIGCM/test" in kwargs["resource"]


def test_creates_subscription_when_zero_subs_exist():
    from execution.scripts import onedrive_resubscribe
    graph = MagicMock()
    graph.list_subscriptions.return_value = []
    with patch("execution.scripts.onedrive_resubscribe.GraphClient",
               return_value=graph):
        onedrive_resubscribe._run()
    graph.create_subscription.assert_called_once()
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `pytest tests/test_onedrive_resubscribe.py -v`
Expected: All fail.

- [ ] **Step 3: Implement the cron script**

Create `execution/scripts/onedrive_resubscribe.py`:

```python
#!/usr/bin/env python3
"""Renew (or create) the OneDrive change-notification subscription.

Scheduled every 12h via .github/workflows/onedrive_resubscribe.yml.
Graph subscriptions for drive resources have a maximum lifetime of ~3 days,
so we renew any subscription expiring within 24h.

If no subscription currently points at our notificationUrl, we create one.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core.event_bus import with_event_bus, get_current_bus
from execution.core.logger import WorkflowLogger
from execution.integrations.graph_client import GraphClient


WORKFLOW_NAME = "onedrive_resubscribe"
RENEW_WHEN_WITHIN_HOURS = 24


def _run() -> None:
    logger = WorkflowLogger(WORKFLOW_NAME)
    bus = get_current_bus()
    if bus:
        bus.emit("step", label="Listando subscriptions")

    graph = GraphClient()
    our_url = os.environ["ONEDRIVE_WEBHOOK_URL"]
    drive_id = os.environ["GRAPH_DRIVE_ID"]
    folder_path = os.environ["GRAPH_FOLDER_PATH"]
    client_state = os.environ["GRAPH_WEBHOOK_CLIENT_STATE"]

    subs = graph.list_subscriptions()
    our_subs = [s for s in subs if s.get("notificationUrl") == our_url]
    logger.info(f"found {len(our_subs)} subs matching our URL ({len(subs)} total)")

    if not our_subs:
        resource = f"/drives/{drive_id}/root:{folder_path}"
        created = graph.create_subscription(
            resource=resource,
            notification_url=our_url,
            client_state=client_state,
        )
        logger.info(f"created subscription id={created.get('id')}")
        if bus:
            bus.emit("subscription_created", detail={"id": created.get("id")})
        return

    threshold = datetime.now(timezone.utc) + timedelta(hours=RENEW_WHEN_WITHIN_HOURS)
    renewed_any = False
    for sub in our_subs:
        raw_exp = sub.get("expirationDateTime", "")
        try:
            exp = datetime.fromisoformat(raw_exp.replace("Z", "+00:00"))
        except Exception:
            logger.warning(f"unparseable expirationDateTime: {raw_exp!r}")
            continue

        if exp < threshold:
            graph.renew_subscription(sub["id"])
            renewed_any = True
            logger.info(f"renewed subscription {sub['id']}")
            if bus:
                bus.emit("subscription_renewed", detail={"id": sub["id"]})
        else:
            logger.info(f"subscription {sub['id']} expires at {raw_exp} — skipping")

    if not renewed_any and bus:
        bus.emit("no_renewal_needed")


@with_event_bus(WORKFLOW_NAME)
def main():
    _run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests, confirm they pass**

Run: `pytest tests/test_onedrive_resubscribe.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/onedrive_resubscribe.py tests/test_onedrive_resubscribe.py
git commit -m "feat(onedrive): add subscription renewal cron script"
```

---

## Task 11: Add GH Actions workflow for resubscribe

**Files:**
- Create: `.github/workflows/onedrive_resubscribe.yml`

- [ ] **Step 1: Write the workflow file**

Create `.github/workflows/onedrive_resubscribe.yml`:

```yaml
name: onedrive_resubscribe

on:
  schedule:
    - cron: "0 */12 * * *"   # 00:00 and 12:00 UTC → 21:00 and 09:00 BRT
  workflow_dispatch:

concurrency:
  group: onedrive_resubscribe
  cancel-in-progress: false

jobs:
  resubscribe:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Run resubscribe
        env:
          GRAPH_TENANT_ID:             ${{ secrets.GRAPH_TENANT_ID }}
          GRAPH_CLIENT_ID:             ${{ secrets.GRAPH_CLIENT_ID }}
          GRAPH_CLIENT_SECRET:         ${{ secrets.GRAPH_CLIENT_SECRET }}
          GRAPH_DRIVE_ID:              ${{ secrets.GRAPH_DRIVE_ID }}
          GRAPH_FOLDER_PATH:           ${{ secrets.GRAPH_FOLDER_PATH }}
          GRAPH_WEBHOOK_CLIENT_STATE:  ${{ secrets.GRAPH_WEBHOOK_CLIENT_STATE }}
          ONEDRIVE_WEBHOOK_URL:        ${{ secrets.ONEDRIVE_WEBHOOK_URL }}
          SUPABASE_URL:                ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY:                ${{ secrets.SUPABASE_KEY }}
          SENTRY_DSN:                  ${{ secrets.SENTRY_DSN }}
          TELEGRAM_BOT_TOKEN:          ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID:            ${{ secrets.TELEGRAM_CHAT_ID }}
          TELEGRAM_EVENTS_CHANNEL_ID:  ${{ secrets.TELEGRAM_EVENTS_CHANNEL_ID }}
        run: python execution/scripts/onedrive_resubscribe.py
```

- [ ] **Step 2: Lint the YAML locally**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/onedrive_resubscribe.yml'))" && echo "YAML OK"
```
Expected: `YAML OK`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/onedrive_resubscribe.yml
git commit -m "ci(onedrive): add resubscribe cron workflow (every 12h)"
```

---

## Task 12: Wire everything into bot/main.py + register watchdog

**Files:**
- Modify: `webhook/bot/main.py`
- Modify: `webhook/status_builder.py`
- Modify: `webhook/bot/routers/commands.py`
- Modify: `.env.example`

- [ ] **Step 1: Mount the HTTP route and router in bot/main.py**

Open `webhook/bot/main.py`. Find the `create_app` function (grep for `def create_app`). Locate where existing routes are registered — typically after the aiogram dispatcher is set up and existing routers are included.

Add imports near the top with the other bot imports:

```python
from routes.onedrive import setup_routes as setup_onedrive_routes
from bot.routers.callbacks_onedrive import callbacks_onedrive_router
```

Inside `create_app`, where other callback routers are included (search for `include_router`), add:

```python
    dp.include_router(callbacks_onedrive_router)
```

Where other aiohttp routes are registered on `app` (search for `add_post` or `add_routes` on the aiohttp app object), add:

```python
    setup_onedrive_routes(app)
```

- [ ] **Step 2: Register the workflow for watchdog**

Open `webhook/status_builder.py`. Find `ALL_WORKFLOWS` (a list/tuple of workflow names). Add `"onedrive_resubscribe"` to it.

- [ ] **Step 3: Register the workflow for `/tail` autocomplete**

Open `webhook/bot/routers/commands.py`. Find `_TAIL_KNOWN_WORKFLOWS` (grep for it). Add `"onedrive_resubscribe"` and `"onedrive_webhook"` (the latter is what the pipeline's EventBus emits as workflow).

- [ ] **Step 4: Document env vars in .env.example**

Open `.env.example`. Append at the end:

```
# ── OneDrive PDF broadcast (spec: 2026-04-22-onedrive-pdf-broadcast) ──
GRAPH_TENANT_ID=
GRAPH_CLIENT_ID=
GRAPH_CLIENT_SECRET=
GRAPH_DRIVE_ID=b!OpzpfwNGVEuhVt-oJYZoukWVYCYFUfdDmAJi023i_CwVR7rrWffbSI9pE6zV1uYd
GRAPH_FOLDER_PATH=/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals
GRAPH_WEBHOOK_CLIENT_STATE=
ONEDRIVE_WEBHOOK_URL=
```

- [ ] **Step 5: Smoke test — does the bot still start?**

Run:
```bash
python -c "from webhook.bot.main import create_app; import asyncio; app = asyncio.run(create_app()); print('OK, routes:', [r.path for r in app.router.routes() if hasattr(r, 'path')])"
```
Expected: Prints `OK, routes: [...]` with `/onedrive/notify` visible in the output. If it errors with missing env vars, set them to dummy values and retry.

- [ ] **Step 6: Run the full test suite to confirm no regressions**

Run: `pytest tests/ -v --ignore=tests/test_graph_subscription_lifecycle.py -x`
Expected: Everything passes that was passing before (new tests also pass).

- [ ] **Step 7: Commit**

```bash
git add webhook/bot/main.py webhook/status_builder.py webhook/bot/routers/commands.py .env.example
git commit -m "feat(bot): wire onedrive webhook + callbacks + watchdog registration"
```

---

## Task 13: End-to-end smoke test (manual, pre-deploy)

**Files:** None (manual validation)

- [ ] **Step 1: Set up local Graph subscription via ngrok**

In one terminal:
```bash
ngrok http 8080   # or whichever port the Railway bot listens on locally
```
Copy the HTTPS URL.

- [ ] **Step 2: Export env vars and start bot locally**

```bash
export ONEDRIVE_WEBHOOK_URL="https://<ngrok-subdomain>.ngrok-free.app/onedrive/notify"
export GRAPH_WEBHOOK_CLIENT_STATE="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export GRAPH_TENANT_ID="..."
export GRAPH_CLIENT_ID="..."
export GRAPH_CLIENT_SECRET="..."
export GRAPH_DRIVE_ID="b!OpzpfwNGVEuhVt-oJYZoukWVYCYFUfdDmAJi023i_CwVR7rrWffbSI9pE6zV1uYd"
export GRAPH_FOLDER_PATH="/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals"
# plus SUPABASE_URL, SUPABASE_KEY, REDIS_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, UAZAPI_URL, UAZAPI_TOKEN

python -m webhook.bot.main
```
Expected: `aiohttp server running on 0.0.0.0:8080` (or similar).

- [ ] **Step 3: Seed a smoke-test contact list**

In Supabase SQL editor:
```sql
INSERT INTO contact_lists (code, label) VALUES ('smoke_test', 'Smoke Test') ON CONFLICT DO NOTHING;
INSERT INTO contact_list_members (list_code, contact_phone)
VALUES ('smoke_test', '<YOUR_OWN_PHONE_UAZAPI>');
```

- [ ] **Step 4: Register the subscription**

```bash
python execution/scripts/onedrive_resubscribe.py
```
Expected: Logs show "created subscription id=…". Check `event_log` for `subscription_created`.

- [ ] **Step 5: Drop a test PDF into SIGCM**

Drop any PDF into the SharePoint folder `/SIGCM/4. Relatórios Mercado/Relatório Diário Minerals/` (use a non-production filename like `SMOKE_TEST.pdf`).

Expected within 30s:
- [ ] Approval card appears in Telegram with buttons for each list + `Todos` + `Descartar`
- [ ] `event_log` has `webhook_received`, `delta_query_done`, `approval_created` rows with the same `trace_id`

- [ ] **Step 6: Approve the smoke test**

Click `[Smoke Test (1)]` → click `[✅ Enviar]`.

Expected:
- [ ] Card edits to "📤 Enviando…" then "✅ Enviado — 1/1 sucesso"
- [ ] Your own WhatsApp receives the PDF
- [ ] `event_log` has the complete timeline under one `trace_id`

- [ ] **Step 7: Test Discard**

Drop another PDF. Click `[❌ Descartar]`.

Expected:
- [ ] Card edits to "❌ Descartado"
- [ ] Redis key `approval:{uuid}` is gone
- [ ] No WhatsApp sent

- [ ] **Step 8: Test duplicate protection**

Within 30 seconds of dropping the first PDF, drop the exact same PDF again (same content, same filename if possible).

Expected:
- [ ] Only one approval card ever appeared (second was deduped by `seen:onedrive:{item_id}`)
- [ ] `event_log` shows `duplicate_webhook` event for the second trigger

- [ ] **Step 9: Cleanup**

```sql
DELETE FROM contact_list_members WHERE list_code = 'smoke_test';
DELETE FROM contact_lists WHERE code = 'smoke_test';
```

Delete the subscription:
```bash
python -c "from execution.integrations.graph_client import GraphClient; g = GraphClient(); [g.delete_subscription(s['id']) for s in g.list_subscriptions()]"
```

- [ ] **Step 10: Document the outcome**

If anything failed, open an issue or back-link in a commit. If everything passed, proceed to production deploy:
1. Set all 7 env vars on Railway.
2. Add same env vars to GitHub Actions secrets (for the resubscribe cron).
3. Deploy webhook to Railway.
4. Manually trigger `onedrive_resubscribe` workflow once on GH Actions (creates the real subscription).
5. Populate real list memberships in Supabase.
6. Drop a real PDF → validate end-to-end in production.

---

## Self-Review (already completed during plan authoring)

**Spec coverage check:**
- [x] Microsoft Graph change-notification webhook → Tasks 4, 9, 11
- [x] Single folder watched → Tasks 4, 10 (env-driven)
- [x] Telegram approval flow with single-select + confirm → Tasks 6, 8
- [x] `contact_lists` + `contact_list_members` tables → Task 1
- [x] Uazapi `/send/media` integration → Task 3
- [x] Subscription renewal cron → Tasks 10, 11
- [x] EventBus integration across webhook + dispatch + cron → Tasks 6, 7, 10
- [x] Watchdog coverage → Task 12
- [x] Out-of-scope items not implemented (bot UI for lists, multi-select, thumbnails, catchup cron) ✅

**Placeholder scan:** No TBD/TODO/FIXME markers. Every code block is complete. Every file path is exact.

**Type consistency check:**
- `ContactList` dataclass defined in Task 2, used in Tasks 6 + 8.
- `ALL_CODE = "__all__"` defined in both `onedrive_pipeline.py` (Task 6) and `dispatch_document.py` (Task 7) — duplicate constant but clearer than importing across modules.
- `ApprovalExpiredError` defined in Task 7, caught in Task 8 handlers.
- Callback data classes (`OneDriveApprove`, `OneDriveConfirm`, `OneDriveDiscard`) defined in Task 5, imported in Tasks 6 + 8.
- `dispatch_document(approval_id, list_code)` signature consistent across Tasks 7, 8.

---

*Plan finalized 2026-04-22 after brainstorming session.*
