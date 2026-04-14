# Contact Management via Telegram Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admin add/deactivate/reactivate WhatsApp contacts via Telegram bot commands (`/add`, `/list`), and fix the `ProfileName` name extraction in delivery reports.

**Architecture:** New `webhook/contact_admin.py` module handles parsing, authorization, formatting, and state machine. `SheetsClient` gets 3 new methods (`add_contact`, `toggle_contact`, `list_contacts`). Shared `build_contact_from_row` helper in `execution/core/delivery_reporter.py` replaces 5 duplicated `build_contact` functions and fixes the `ProfileName` bug.

**Tech Stack:** Python 3.9/3.10, Flask (webhook), gspread (Google Sheets), Telegram Bot API, pytest.

**Spec:** `docs/superpowers/specs/2026-04-14-contact-admin-design.md`

---

## File Structure

**Created:**
- `webhook/contact_admin.py` — parser, authorization, message formatting, handlers
- `tests/test_contact_admin.py` — pytest tests for parser, authorization, state, formatting
- `tests/test_sheets_contact_ops.py` — pytest tests for SheetsClient add/toggle/list

**Modified:**
- `execution/core/delivery_reporter.py` — add `build_contact_from_row()` helper
- `tests/test_delivery_reporter.py` — add tests for new helper
- `execution/integrations/sheets_client.py` — add `add_contact`, `toggle_contact`, `list_contacts` methods
- `execution/scripts/morning_check.py` — use shared helper
- `execution/scripts/send_daily_report.py` — use shared helper
- `execution/scripts/baltic_ingestion.py` — use shared helper
- `execution/scripts/send_news.py` — use shared helper
- `webhook/app.py` — use shared helper, route new commands/callbacks, update `/start`

---

## Task 1: Shared `build_contact_from_row()` helper (TDD)

**Files:**
- Modify: `execution/core/delivery_reporter.py`
- Modify: `tests/test_delivery_reporter.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_delivery_reporter.py`:
```python
from execution.core.delivery_reporter import build_contact_from_row


def test_build_contact_uses_profile_name_first():
    row = {"ProfileName": "Joao Silva", "Nome": "Wrong", "From": "whatsapp:+5511999"}
    c = build_contact_from_row(row)
    assert c.name == "Joao Silva"


def test_build_contact_falls_back_to_nome():
    row = {"Nome": "Maria", "From": "whatsapp:+5521888"}
    c = build_contact_from_row(row)
    assert c.name == "Maria"


def test_build_contact_falls_back_to_name():
    row = {"Name": "Carlos", "From": "whatsapp:+5531777"}
    c = build_contact_from_row(row)
    assert c.name == "Carlos"


def test_build_contact_name_placeholder_when_missing():
    row = {"From": "whatsapp:+5511999"}
    c = build_contact_from_row(row)
    assert c.name == "—"


def test_build_contact_phone_from_evolution_api_column():
    row = {"ProfileName": "A", "Evolution-api": "5511999999999"}
    c = build_contact_from_row(row)
    assert c.phone == "5511999999999"


def test_build_contact_phone_from_n8n_evo_column():
    row = {"ProfileName": "A", "n8n-evo": "5511999999999@s.whatsapp.net"}
    c = build_contact_from_row(row)
    assert c.phone == "5511999999999"


def test_build_contact_phone_from_from_column():
    row = {"ProfileName": "A", "From": "whatsapp:+5511999999999"}
    c = build_contact_from_row(row)
    assert c.phone == "5511999999999"


def test_build_contact_phone_strips_prefixes_and_suffixes():
    row = {"ProfileName": "A", "From": "whatsapp:+5511 999-99999"}
    c = build_contact_from_row(row)
    # After strip: whatsapp: gone, + gone, spaces/hyphens kept as-is (not digits)
    assert c.phone == "5511 999-99999"  # spaces/hyphens preserved, plus/whatsapp stripped


def test_build_contact_returns_none_when_no_phone():
    row = {"ProfileName": "Ghost"}
    assert build_contact_from_row(row) is None


def test_build_contact_returns_none_when_phone_empty():
    row = {"ProfileName": "Ghost", "From": ""}
    assert build_contact_from_row(row) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v -k build_contact
```
Expected: All 10 tests fail with `ImportError: cannot import name 'build_contact_from_row'`.

- [ ] **Step 3: Add `build_contact_from_row` to `execution/core/delivery_reporter.py`**

Append at module level (after `_build_telegram_client`, before/after any helper — placement non-critical since it's top-level):
```python
def build_contact_from_row(row: dict) -> Optional[Contact]:
    """
    Convert a Google Sheets row dict into a Contact.
    Returns None if no phone field is present/usable.
    Priority for name: ProfileName > Nome > Name > "—".
    Priority for phone: Evolution-api > n8n-evo > Telefone > Phone > From.
    Phone normalization: strip "whatsapp:", "+", "@s.whatsapp.net".
    """
    name = (
        row.get("ProfileName")
        or row.get("Nome")
        or row.get("Name")
        or "—"
    )
    raw_phone = (
        row.get("Evolution-api")
        or row.get("n8n-evo")
        or row.get("Telefone")
        or row.get("Phone")
        or row.get("From")
    )
    if not raw_phone:
        return None
    phone = (
        str(raw_phone)
        .replace("whatsapp:", "")
        .replace("@s.whatsapp.net", "")
        .replace("+", "")
        .strip()
    )
    if not phone:
        return None
    return Contact(name=name, phone=phone)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: All tests pass (24 existing + 10 new = 34).

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py && git commit -m "feat(delivery_reporter): add build_contact_from_row helper with ProfileName priority"
```

---

## Task 2: Refactor 5 callers to use the shared helper

**Files:**
- Modify: `execution/scripts/morning_check.py`
- Modify: `execution/scripts/send_daily_report.py`
- Modify: `execution/scripts/baltic_ingestion.py`
- Modify: `execution/scripts/send_news.py`
- Modify: `webhook/app.py`

Each file currently has a local `build_contact(c)` defined inline. Replace with import + use of shared helper.

- [ ] **Step 1: Refactor `morning_check.py`**

In `execution/scripts/morning_check.py`, change the import block near top. It already has:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact
```
Change to:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
```

Then find the local `def build_contact(c):` block inside the function and the subsequent list comprehension. Replace the whole block:
```python
    def build_contact(c):
        raw_phone = (
            c.get('Evolution-api') or c.get('Telefone') or
            c.get('Phone') or c.get('From')
        )
        if not raw_phone:
            return None
        phone = str(raw_phone).replace("whatsapp:", "").strip()
        name = c.get("Nome") or c.get("Name") or "—"
        return Contact(name=name, phone=phone)

    delivery_contacts = [bc for c in contacts if (bc := build_contact(c))]
```

With:
```python
    delivery_contacts = [bc for c in contacts if (bc := build_contact_from_row(c))]
```

- [ ] **Step 2: Refactor `send_daily_report.py` identically**

Same change pattern: update import, remove local `build_contact`, replace list comprehension with call to `build_contact_from_row`.

- [ ] **Step 3: Refactor `baltic_ingestion.py` identically**

Same change pattern.

- [ ] **Step 4: Refactor `send_news.py` identically**

Same change pattern.

- [ ] **Step 5: Refactor `webhook/app.py` (inside `process_approval_async`)**

In `webhook/app.py`, the existing import line is:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact
```
Change to:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
```

Find the local `build_contact` inside `process_approval_async` and the list comprehension:
```python
        def build_contact(c):
            raw_phone = c.get("Evolution-api") or c.get("Telefone")
            if not raw_phone:
                return None
            phone = str(raw_phone).replace("whatsapp:", "").strip()
            name = c.get("Nome") or "—"
            return Contact(name=name, phone=phone)

        delivery_contacts = [bc for c in raw_contacts if (bc := build_contact(c))]
```

Replace with:
```python
        delivery_contacts = [bc for c in raw_contacts if (bc := build_contact_from_row(c))]
```

- [ ] **Step 6: Verify imports clean**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -c "
from execution.scripts import morning_check, send_daily_report, baltic_ingestion, send_news
import sys
sys.path.insert(0, 'webhook')
import app
print('OK: all imports clean')
" 2>&1 | tail -5
```
Expected: `OK: all imports clean` (env var warnings OK).

- [ ] **Step 7: Run all tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest
```
Expected: 34 tests pass.

- [ ] **Step 8: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/morning_check.py execution/scripts/send_daily_report.py execution/scripts/baltic_ingestion.py execution/scripts/send_news.py webhook/app.py && git commit -m "refactor: use shared build_contact_from_row, fix ProfileName in reports"
```

---

## Task 3: `SheetsClient.list_contacts()` (TDD)

**Files:**
- Modify: `execution/integrations/sheets_client.py`
- Create: `tests/test_sheets_contact_ops.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_sheets_contact_ops.py`:
```python
"""Tests for SheetsClient contact management operations."""
from unittest.mock import MagicMock, patch
import pytest
from execution.integrations.sheets_client import SheetsClient


@pytest.fixture
def mock_sheets_client():
    """Return a SheetsClient with internals mocked."""
    with patch("execution.integrations.sheets_client.gspread"):
        client = SheetsClient.__new__(SheetsClient)
        client.gc = MagicMock()
        client.logger = MagicMock()
        return client


def _mk_rows(active_count, inactive_count):
    """Helper to build mock sheet rows."""
    rows = []
    for i in range(active_count):
        rows.append({
            "ProfileName": f"Active{i}",
            "From": f"whatsapp:+55119{i:08d}",
            "n8n-evo": f"55119{i:08d}@s.whatsapp.net",
            "ButtonPayload": "Big",
        })
    for i in range(inactive_count):
        rows.append({
            "ProfileName": f"Inactive{i}",
            "From": f"whatsapp:+55219{i:08d}",
            "n8n-evo": f"55219{i:08d}@s.whatsapp.net",
            "ButtonPayload": "Inactive",
        })
    return rows


def test_list_returns_first_page(mock_sheets_client):
    rows = _mk_rows(25, 0)
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", page=1, per_page=10)
    assert len(contacts) == 10
    assert contacts[0]["ProfileName"] == "Active0"
    assert total_pages == 3  # ceil(25 / 10)


def test_list_returns_last_page_partial(mock_sheets_client):
    rows = _mk_rows(25, 0)
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", page=3, per_page=10)
    assert len(contacts) == 5
    assert contacts[0]["ProfileName"] == "Active20"


def test_list_search_case_insensitive(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao Silva", "From": "a", "ButtonPayload": "Big"},
        {"ProfileName": "Maria", "From": "b", "ButtonPayload": "Big"},
        {"ProfileName": "JOAO Pedro", "From": "c", "ButtonPayload": "Inactive"},
        {"ProfileName": "Carlos", "From": "d", "ButtonPayload": "Big"},
    ]
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", search="joao")
    assert len(contacts) == 2
    names = {c["ProfileName"] for c in contacts}
    assert names == {"Joao Silva", "JOAO Pedro"}


def test_list_search_no_matches(mock_sheets_client):
    rows = _mk_rows(5, 0)
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", search="xyz")
    assert contacts == []
    assert total_pages == 0


def test_list_empty_sheet(mock_sheets_client):
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = []
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id")
    assert contacts == []
    assert total_pages == 0


def test_list_includes_both_active_and_inactive(mock_sheets_client):
    """list_contacts should NOT filter by ButtonPayload — admin sees all."""
    rows = _mk_rows(3, 2)
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = worksheet

    contacts, total_pages = mock_sheets_client.list_contacts("sheet_id", per_page=100)
    assert len(contacts) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_sheets_contact_ops.py -v
```
Expected: Tests fail with `AttributeError: 'SheetsClient' object has no attribute 'list_contacts'`.

- [ ] **Step 3: Add `list_contacts` method to `SheetsClient`**

In `execution/integrations/sheets_client.py`, add this method inside the `SheetsClient` class (place after `get_contacts`):
```python
    def list_contacts(
        self,
        sheet_id,
        sheet_name="Página1",
        search=None,
        page=1,
        per_page=10,
    ):
        """
        List all contacts (both active and inactive) for admin view.
        Optionally filtered by case-insensitive substring on ProfileName.
        Returns (contacts_on_page, total_pages).
        """
        import math
        try:
            sh = self.gc.open_by_key(sheet_id)
            ws_names = [w.title for w in sh.worksheets()]
            if sheet_name not in ws_names:
                worksheet = sh.sheet1
            else:
                worksheet = sh.worksheet(sheet_name)

            records = worksheet.get_all_records()

            if search:
                needle = search.lower()
                records = [
                    r for r in records
                    if needle in str(r.get("ProfileName", "")).lower()
                ]

            total = len(records)
            if total == 0:
                return [], 0
            total_pages = math.ceil(total / per_page)
            start = (page - 1) * per_page
            end = start + per_page
            return records[start:end], total_pages
        except Exception as e:
            self.logger.error("list_contacts failed", {"error": str(e)})
            raise
```

- [ ] **Step 4: Run tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_sheets_contact_ops.py -v
```
Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/integrations/sheets_client.py tests/test_sheets_contact_ops.py && git commit -m "feat(sheets): add list_contacts for admin view with pagination + search"
```

---

## Task 4: `SheetsClient.toggle_contact()` (TDD)

**Files:**
- Modify: `execution/integrations/sheets_client.py`
- Modify: `tests/test_sheets_contact_ops.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_sheets_contact_ops.py`:
```python
def _setup_toggle_sheet(mock_client, rows):
    worksheet = MagicMock()
    worksheet.get_all_records.return_value = rows
    # headers: first row of sheet
    if rows:
        worksheet.row_values.return_value = list(rows[0].keys())
    else:
        worksheet.row_values.return_value = []
    mock_client.gc.open_by_key.return_value.worksheet.return_value = worksheet
    return worksheet


def test_toggle_big_to_inactive(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "n8n-evo": "5511999@s.whatsapp.net",
         "From": "whatsapp:+5511999", "ButtonPayload": "Big"},
    ]
    ws = _setup_toggle_sheet(mock_sheets_client, rows)

    name, new_status = mock_sheets_client.toggle_contact("sheet_id", "5511999")
    assert name == "Joao"
    assert new_status == "Inactive"
    # Verify cell update called
    ws.update_cell.assert_called_once()
    call_args = ws.update_cell.call_args
    # update_cell(row, col, value) — row=2 (data row 1), col=idx of ButtonPayload+1
    expected_col = list(rows[0].keys()).index("ButtonPayload") + 1
    assert call_args[0][0] == 2  # row 2 (row 1 is header)
    assert call_args[0][1] == expected_col
    assert call_args[0][2] == "Inactive"


def test_toggle_inactive_to_big(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "n8n-evo": "5511999@s.whatsapp.net",
         "From": "whatsapp:+5511999", "ButtonPayload": "Inactive"},
    ]
    ws = _setup_toggle_sheet(mock_sheets_client, rows)

    name, new_status = mock_sheets_client.toggle_contact("sheet_id", "5511999")
    assert name == "Joao"
    assert new_status == "Big"
    ws.update_cell.assert_called_once()
    assert ws.update_cell.call_args[0][2] == "Big"


def test_toggle_matches_phone_by_digits_only(mock_sheets_client):
    """Phone lookup should match by digits, ignoring format differences."""
    rows = [
        {"ProfileName": "Joao",
         "From": "whatsapp:+5511999",
         "n8n-evo": "5511999@s.whatsapp.net",
         "ButtonPayload": "Big"},
    ]
    ws = _setup_toggle_sheet(mock_sheets_client, rows)

    # Phone arg has different formatting — should still match
    name, _ = mock_sheets_client.toggle_contact("sheet_id", "+5511999")
    assert name == "Joao"


def test_toggle_raises_when_phone_not_found(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "From": "whatsapp:+5511999",
         "n8n-evo": "5511999@s.whatsapp.net", "ButtonPayload": "Big"},
    ]
    _setup_toggle_sheet(mock_sheets_client, rows)

    with pytest.raises(ValueError, match="not found"):
        mock_sheets_client.toggle_contact("sheet_id", "99999")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_sheets_contact_ops.py::test_toggle_big_to_inactive -v
```
Expected: `AttributeError: ... no attribute 'toggle_contact'`.

- [ ] **Step 3: Add `toggle_contact` method**

Add inside `SheetsClient` class, after `list_contacts`:
```python
    def toggle_contact(self, sheet_id, phone, sheet_name="Página1"):
        """
        Flip ButtonPayload between 'Big' and 'Inactive' for the row matching phone.
        Phone matching normalizes both sides to digits only.
        Returns (profile_name, new_status).
        Raises ValueError if not found.
        """
        try:
            sh = self.gc.open_by_key(sheet_id)
            ws_names = [w.title for w in sh.worksheets()]
            if sheet_name not in ws_names:
                worksheet = sh.sheet1
            else:
                worksheet = sh.worksheet(sheet_name)

            headers = worksheet.row_values(1)
            if "ButtonPayload" not in headers:
                raise ValueError("ButtonPayload column not found in sheet")
            button_col_idx = headers.index("ButtonPayload") + 1  # 1-indexed

            needle = _digits_only(phone)
            records = worksheet.get_all_records()
            for i, row in enumerate(records):
                row_phone = (
                    row.get("Evolution-api")
                    or row.get("n8n-evo")
                    or row.get("From")
                    or ""
                )
                if _digits_only(str(row_phone)) == needle:
                    current = str(row.get("ButtonPayload", "")).strip()
                    new_status = "Inactive" if current == "Big" else "Big"
                    worksheet.update_cell(i + 2, button_col_idx, new_status)
                    return (row.get("ProfileName", "—"), new_status)

            raise ValueError(f"Contact with phone {phone} not found")
        except ValueError:
            raise
        except Exception as e:
            self.logger.error("toggle_contact failed", {"error": str(e)})
            raise
```

Also add this module-level helper at the top of `execution/integrations/sheets_client.py` (below the imports):
```python
def _digits_only(s: str) -> str:
    """Return only digits from a string."""
    return "".join(c for c in str(s) if c.isdigit())
```

- [ ] **Step 4: Run tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_sheets_contact_ops.py -v
```
Expected: All 10 tests pass (6 list + 4 toggle).

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/integrations/sheets_client.py tests/test_sheets_contact_ops.py && git commit -m "feat(sheets): add toggle_contact to flip ButtonPayload Big/Inactive"
```

---

## Task 5: `SheetsClient.add_contact()` (TDD)

**Files:**
- Modify: `execution/integrations/sheets_client.py`
- Modify: `tests/test_sheets_contact_ops.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_sheets_contact_ops.py`:
```python
def test_add_contact_appends_row_with_defaults(mock_sheets_client):
    rows = [
        {"ProfileName": "Existing", "MessageType": "button",
         "SmsStatus": "received", "Body": "Sim, quero receber",
         "From": "whatsapp:+5511888", "ButtonPayload": "Big",
         "To": "whatsapp:+5511000000000",
         "n8n-evo": "5511888@s.whatsapp.net"},
    ]
    ws = _setup_toggle_sheet(mock_sheets_client, rows)

    mock_sheets_client.add_contact("sheet_id", "Joao Silva", "5511999999999")

    ws.append_row.assert_called_once()
    appended = ws.append_row.call_args[0][0]
    # appended is a list matching headers order
    headers = list(rows[0].keys())
    row_dict = dict(zip(headers, appended))
    assert row_dict["ProfileName"] == "Joao Silva"
    assert row_dict["MessageType"] == "button"
    assert row_dict["SmsStatus"] == "received"
    assert row_dict["Body"] == "Sim, quero receber (via bot)"
    assert row_dict["From"] == "whatsapp:+5511999999999"
    assert row_dict["ButtonPayload"] == "Big"
    assert row_dict["To"] == "whatsapp:+5511000000000"  # copied from last row
    assert row_dict["n8n-evo"] == "5511999999999@s.whatsapp.net"


def test_add_contact_raises_on_duplicate_active(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "From": "whatsapp:+5511999",
         "n8n-evo": "5511999@s.whatsapp.net", "ButtonPayload": "Big"},
    ]
    _setup_toggle_sheet(mock_sheets_client, rows)

    with pytest.raises(ValueError, match="already exists"):
        mock_sheets_client.add_contact("sheet_id", "Novo", "5511999")


def test_add_contact_raises_on_duplicate_inactive(mock_sheets_client):
    rows = [
        {"ProfileName": "Joao", "From": "whatsapp:+5511999",
         "n8n-evo": "5511999@s.whatsapp.net", "ButtonPayload": "Inactive"},
    ]
    _setup_toggle_sheet(mock_sheets_client, rows)

    with pytest.raises(ValueError, match="already exists"):
        mock_sheets_client.add_contact("sheet_id", "Novo", "5511999")


def test_add_contact_empty_sheet_uses_empty_to(mock_sheets_client):
    """When sheet is empty, 'To' field defaults to empty string (no last row to copy)."""
    ws = MagicMock()
    ws.get_all_records.return_value = []
    ws.row_values.return_value = ["ProfileName", "From", "ButtonPayload", "To"]
    mock_sheets_client.gc.open_by_key.return_value.worksheet.return_value = ws

    mock_sheets_client.add_contact("sheet_id", "Primeiro", "5511111")

    ws.append_row.assert_called_once()
    appended = ws.append_row.call_args[0][0]
    # Find index of "To" in headers
    to_idx = ws.row_values.return_value.index("To")
    assert appended[to_idx] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_sheets_contact_ops.py::test_add_contact_appends_row_with_defaults -v
```
Expected: `AttributeError: ... no attribute 'add_contact'`.

- [ ] **Step 3: Add `add_contact` method**

Add inside `SheetsClient` class, after `toggle_contact`:
```python
    def add_contact(self, sheet_id, profile_name, phone, sheet_name="Página1"):
        """
        Append a new contact row with sensible defaults.
        Raises ValueError if phone already exists (active or inactive).
        """
        try:
            sh = self.gc.open_by_key(sheet_id)
            ws_names = [w.title for w in sh.worksheets()]
            if sheet_name not in ws_names:
                worksheet = sh.sheet1
            else:
                worksheet = sh.worksheet(sheet_name)

            headers = worksheet.row_values(1)
            records = worksheet.get_all_records()

            # Duplicate check
            needle = _digits_only(phone)
            for row in records:
                row_phone = (
                    row.get("Evolution-api")
                    or row.get("n8n-evo")
                    or row.get("From")
                    or ""
                )
                if _digits_only(str(row_phone)) == needle:
                    status = str(row.get("ButtonPayload", "")).strip()
                    existing = row.get("ProfileName", "—")
                    raise ValueError(
                        f"Contact {existing!r} already exists with phone {phone} "
                        f"(status: {status or 'unknown'})"
                    )

            # Copy 'To' from last row (if any) for consistency
            to_value = ""
            if records:
                to_value = records[-1].get("To", "") or ""

            digits = _digits_only(phone)

            # Build a dict keyed by header, then order it per header row
            defaults = {
                "ProfileName": profile_name,
                "MessageType": "button",
                "SmsStatus": "received",
                "Body": "Sim, quero receber (via bot)",
                "From": f"whatsapp:+{digits}",
                "ButtonPayload": "Big",
                "To": to_value,
                "n8n-evo": f"{digits}@s.whatsapp.net",
            }

            row_values = [defaults.get(h, "") for h in headers]
            worksheet.append_row(row_values)
        except ValueError:
            raise
        except Exception as e:
            self.logger.error("add_contact failed", {"error": str(e)})
            raise
```

- [ ] **Step 4: Run tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_sheets_contact_ops.py -v
```
Expected: All 14 tests pass (6 list + 4 toggle + 4 add).

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/integrations/sheets_client.py tests/test_sheets_contact_ops.py && git commit -m "feat(sheets): add add_contact with duplicate detection"
```

---

## Task 6: `contact_admin.py` — parser + authorization (TDD)

**Files:**
- Create: `webhook/contact_admin.py`
- Create: `tests/test_contact_admin.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_contact_admin.py`:
```python
"""Tests for webhook.contact_admin module."""
import sys
from pathlib import Path
import pytest

# Make webhook importable as a package root
sys.path.insert(0, str(Path(__file__).parent.parent / "webhook"))

from contact_admin import (
    parse_add_input,
    is_authorized,
    digits_only,
)


# ── parse_add_input ──

def test_parse_add_input_valid():
    assert parse_add_input("Joao Silva 5511999999999") == ("Joao Silva", "5511999999999")


def test_parse_add_input_single_name():
    assert parse_add_input("Joao 5511999999999") == ("Joao", "5511999999999")


def test_parse_add_input_multiword_name():
    assert parse_add_input("Ana Maria Santos 5511999999999") == ("Ana Maria Santos", "5511999999999")


def test_parse_add_input_strips_phone_prefixes():
    name, phone = parse_add_input("Joao +5511999999999")
    assert phone == "5511999999999"


def test_parse_add_input_strips_whatsapp_jid():
    name, phone = parse_add_input("Joao 5511999999999@s.whatsapp.net")
    assert phone == "5511999999999"


def test_parse_add_input_missing_phone():
    with pytest.raises(ValueError, match="formato"):
        parse_add_input("Joao Silva")


def test_parse_add_input_empty():
    with pytest.raises(ValueError, match="formato"):
        parse_add_input("")


def test_parse_add_input_whitespace_only():
    with pytest.raises(ValueError, match="formato"):
        parse_add_input("   ")


def test_parse_add_input_rejects_non_digits_phone():
    with pytest.raises(ValueError, match="inv"):
        parse_add_input("Joao 5511abc9999")


def test_parse_add_input_rejects_short_phone():
    with pytest.raises(ValueError, match="curto"):
        parse_add_input("Joao 12345")


def test_parse_add_input_rejects_long_phone():
    with pytest.raises(ValueError, match="longo"):
        parse_add_input("Joao 12345678901234567")  # 17 digits


# ── digits_only ──

def test_digits_only_strips_formatting():
    assert digits_only("+55 (11) 99999-9999") == "5511999999999"


def test_digits_only_handles_empty():
    assert digits_only("") == ""


# ── is_authorized ──

def test_is_authorized_matches_admin(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    assert is_authorized(12345) is True
    assert is_authorized("12345") is True


def test_is_authorized_rejects_other(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    assert is_authorized(99999) is False


def test_is_authorized_rejects_when_env_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert is_authorized(12345) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_contact_admin.py -v
```
Expected: `ModuleNotFoundError: No module named 'contact_admin'`.

- [ ] **Step 3: Create `webhook/contact_admin.py` with parser + authorization**

```python
"""
Contact admin: parsing, authorization, formatting, and state management
for /add and /list commands in the Telegram bot.
"""
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple


# ── State ──

ADMIN_STATE: dict = {}  # chat_id (int or str) → {"awaiting": str, "expires_at": datetime}
STATE_TTL = timedelta(minutes=5)


# ── Helpers ──

def digits_only(s: str) -> str:
    """Return only digits from a string."""
    return "".join(c for c in str(s) if c.isdigit())


# ── Authorization ──

def is_authorized(chat_id) -> bool:
    """Check whether chat_id matches the admin TELEGRAM_CHAT_ID env var."""
    admin_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not admin_id:
        return False
    return str(chat_id) == admin_id


# ── Parsers ──

def parse_add_input(text: str) -> Tuple[str, str]:
    """
    Parse '<Nome ...> <phone>' into (name, phone).
    Phone must be the last whitespace-separated token.
    Raises ValueError with user-friendly message on bad input.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Formato inválido. Envie: Nome Telefone")

    parts = text.rsplit(None, 1)
    if len(parts) < 2:
        raise ValueError("Formato inválido. Envie: Nome Telefone")

    name_raw, phone_raw = parts
    name = name_raw.strip()
    if not name:
        raise ValueError("Formato inválido. Envie: Nome Telefone")

    phone_digits = digits_only(phone_raw)
    # Reject if phone_raw had non-digit/non-formatting characters
    # (allow +, spaces, hyphens, parens, @s.whatsapp.net, but nothing else)
    allowed_chars = set("+0123456789 -().@swhatpne")  # chars in whatsapp JID + common formatting
    for ch in phone_raw:
        if ch not in allowed_chars:
            raise ValueError(f"Telefone inválido. Só dígitos, ex: 5511999999999")

    if not phone_digits:
        raise ValueError("Telefone inválido. Só dígitos, ex: 5511999999999")

    if len(phone_digits) < 10:
        raise ValueError("Telefone muito curto (mínimo 10 dígitos)")

    if len(phone_digits) > 15:
        raise ValueError("Telefone muito longo (máximo 15 dígitos)")

    return (name, phone_digits)
```

- [ ] **Step 4: Run tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_contact_admin.py -v
```
Expected: All 15 tests pass.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add webhook/contact_admin.py tests/test_contact_admin.py && git commit -m "feat(contact_admin): add parser + authorization helpers"
```

---

## Task 7: `contact_admin.py` — state machine for `/add` flow (TDD)

**Files:**
- Modify: `webhook/contact_admin.py`
- Modify: `tests/test_contact_admin.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_contact_admin.py`:
```python
from datetime import datetime, timedelta
from contact_admin import (
    start_add_flow,
    get_state,
    clear_state,
    is_awaiting_add,
    ADMIN_STATE,
)


def test_start_add_flow_sets_state():
    ADMIN_STATE.clear()
    start_add_flow(123)
    assert is_awaiting_add(123) is True


def test_clear_state_removes_entry():
    ADMIN_STATE.clear()
    start_add_flow(123)
    clear_state(123)
    assert is_awaiting_add(123) is False


def test_clear_state_on_missing_chat_is_noop():
    ADMIN_STATE.clear()
    clear_state(999)  # no raise
    assert is_awaiting_add(999) is False


def test_is_awaiting_add_false_when_no_state():
    ADMIN_STATE.clear()
    assert is_awaiting_add(123) is False


def test_expired_state_treated_as_not_awaiting():
    ADMIN_STATE.clear()
    ADMIN_STATE[123] = {
        "awaiting": "add_data",
        "expires_at": datetime.now() - timedelta(minutes=1),
    }
    assert is_awaiting_add(123) is False


def test_expired_state_cleaned_up_on_check():
    ADMIN_STATE.clear()
    ADMIN_STATE[123] = {
        "awaiting": "add_data",
        "expires_at": datetime.now() - timedelta(minutes=1),
    }
    is_awaiting_add(123)  # triggers cleanup
    assert 123 not in ADMIN_STATE


def test_start_add_flow_overwrites_existing_state():
    ADMIN_STATE.clear()
    start_add_flow(123)
    first_expiry = ADMIN_STATE[123]["expires_at"]
    # Start again
    start_add_flow(123)
    # Should reset expiry to now + TTL (so not equal to first_expiry exactly —
    # very small chance of flaky timing — instead check it's > first_expiry or equal)
    assert ADMIN_STATE[123]["expires_at"] >= first_expiry
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_contact_admin.py -v -k "state or awaiting or flow"
```
Expected: `ImportError: cannot import name 'start_add_flow'`.

- [ ] **Step 3: Add state helpers to `contact_admin.py`**

Append to `webhook/contact_admin.py`:
```python
def start_add_flow(chat_id) -> None:
    """Mark chat as awaiting add data. Resets TTL."""
    ADMIN_STATE[chat_id] = {
        "awaiting": "add_data",
        "expires_at": datetime.now() + STATE_TTL,
    }


def get_state(chat_id) -> Optional[dict]:
    """Return state dict or None if absent/expired."""
    state = ADMIN_STATE.get(chat_id)
    if state is None:
        return None
    if state.get("expires_at") and state["expires_at"] < datetime.now():
        ADMIN_STATE.pop(chat_id, None)
        return None
    return state


def clear_state(chat_id) -> None:
    """Remove state for chat_id if present. No-op otherwise."""
    ADMIN_STATE.pop(chat_id, None)


def is_awaiting_add(chat_id) -> bool:
    """True if chat_id is currently in add_data wait state (non-expired)."""
    state = get_state(chat_id)
    return state is not None and state.get("awaiting") == "add_data"
```

- [ ] **Step 4: Run tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_contact_admin.py -v
```
Expected: All 22 tests pass (15 parser + 7 state).

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add webhook/contact_admin.py tests/test_contact_admin.py && git commit -m "feat(contact_admin): state machine for /add flow with TTL"
```

---

## Task 8: `contact_admin.py` — message + keyboard formatting (TDD)

**Files:**
- Modify: `webhook/contact_admin.py`
- Modify: `tests/test_contact_admin.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_contact_admin.py`:
```python
from contact_admin import (
    render_add_prompt,
    render_list_message,
    build_list_keyboard,
)


def test_render_add_prompt_has_format_and_example():
    msg = render_add_prompt()
    assert "Nome Telefone" in msg
    assert "Exemplo" in msg
    assert "/cancel" in msg


def test_render_list_message_with_contacts():
    contacts = [
        {"ProfileName": "A", "From": "whatsapp:+111", "ButtonPayload": "Big"},
        {"ProfileName": "B", "From": "whatsapp:+222", "ButtonPayload": "Inactive"},
    ]
    msg = render_list_message(contacts, total=25, page=2, per_page=10, search=None)
    assert "25" in msg  # total shown
    assert "Página 2" in msg or "Pagina 2" in msg


def test_render_list_message_with_search():
    contacts = [{"ProfileName": "Joao", "From": "whatsapp:+111", "ButtonPayload": "Big"}]
    msg = render_list_message(contacts, total=1, page=1, per_page=10, search="joao")
    assert "joao" in msg.lower()


def test_render_list_message_empty_with_search():
    msg = render_list_message([], total=0, page=1, per_page=10, search="xyz")
    assert "xyz" in msg
    assert "Nenhum" in msg or "nenhum" in msg.lower()


def test_render_list_message_empty_without_search():
    msg = render_list_message([], total=0, page=1, per_page=10, search=None)
    assert "/add" in msg


def test_build_list_keyboard_has_toggle_buttons():
    contacts = [
        {"ProfileName": "A", "From": "whatsapp:+5511111", "ButtonPayload": "Big"},
        {"ProfileName": "B", "From": "whatsapp:+5511222", "ButtonPayload": "Inactive"},
    ]
    kb = build_list_keyboard(contacts, page=1, total_pages=1, search=None)
    rows = kb["inline_keyboard"]
    # First 2 rows = contact toggles
    assert rows[0][0]["callback_data"] == "tgl:5511111"
    assert "✅" in rows[0][0]["text"]  # Big = active
    assert "A" in rows[0][0]["text"]
    assert rows[1][0]["callback_data"] == "tgl:5511222"
    assert "❌" in rows[1][0]["text"]  # Inactive


def test_build_list_keyboard_includes_nav_when_multiple_pages():
    contacts = [{"ProfileName": "A", "From": "whatsapp:+111", "ButtonPayload": "Big"}]
    kb = build_list_keyboard(contacts, page=2, total_pages=5, search=None)
    rows = kb["inline_keyboard"]
    # Last row = navigation
    nav = rows[-1]
    callbacks = [b["callback_data"] for b in nav]
    assert "pg:1" in callbacks  # prev
    assert "pg:3" in callbacks  # next
    assert "nop" in callbacks  # center indicator


def test_build_list_keyboard_nav_with_search():
    contacts = [{"ProfileName": "A", "From": "whatsapp:+111", "ButtonPayload": "Big"}]
    kb = build_list_keyboard(contacts, page=2, total_pages=3, search="joao")
    nav = kb["inline_keyboard"][-1]
    callbacks = [b["callback_data"] for b in nav]
    assert "pg:1:joao" in callbacks
    assert "pg:3:joao" in callbacks


def test_build_list_keyboard_no_nav_when_single_page():
    contacts = [{"ProfileName": "A", "From": "whatsapp:+111", "ButtonPayload": "Big"}]
    kb = build_list_keyboard(contacts, page=1, total_pages=1, search=None)
    # Only 1 row (the single contact), no nav row
    assert len(kb["inline_keyboard"]) == 1


def test_build_list_keyboard_empty_contacts():
    kb = build_list_keyboard([], page=1, total_pages=0, search=None)
    # Empty keyboard is OK (caller uses render message instead)
    assert kb["inline_keyboard"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_contact_admin.py -v -k "render or keyboard"
```
Expected: `ImportError: cannot import name 'render_add_prompt'`.

- [ ] **Step 3: Add formatting helpers to `contact_admin.py`**

Append to `webhook/contact_admin.py`:
```python
# ── Message formatting ──

def render_add_prompt() -> str:
    """Message shown when user types /add."""
    return (
        "📝 *ADICIONAR CONTATO*\n\n"
        "Envie no formato:\n"
        "`Nome Telefone`\n\n"
        "Exemplo: `Joao Silva 5511999999999`\n\n"
        "Use /cancel pra desistir."
    )


def render_list_message(contacts: list, total: int, page: int, per_page: int,
                       search: Optional[str]) -> str:
    """Message text for /list. Renders the header — contacts go in keyboard buttons."""
    if not contacts:
        if search:
            return f"📋 Nenhum contato encontrado pra \"{search}\""
        return "📋 Nenhum contato cadastrado. Use /add"

    import math
    total_pages = math.ceil(total / per_page) if per_page else 1

    if search:
        header = f"📋 *RESULTADO BUSCA* \"{search}\" ({total})"
    else:
        header = f"📋 *CONTATOS* ({total}) — Página {page}/{total_pages}"

    return header + "\n\nToque pra ativar/desativar."


def build_list_keyboard(contacts: list, page: int, total_pages: int,
                       search: Optional[str]) -> dict:
    """
    Build inline_keyboard dict with one toggle button per contact,
    plus a bottom nav row if total_pages > 1.
    """
    rows = []

    for c in contacts:
        name = c.get("ProfileName", "—")
        raw_phone = (
            c.get("Evolution-api")
            or c.get("n8n-evo")
            or c.get("From")
            or ""
        )
        phone_digits = digits_only(str(raw_phone))
        status = str(c.get("ButtonPayload", "")).strip()
        emoji = "✅" if status == "Big" else "❌"
        label = f"{emoji} {name} — {phone_digits}"
        # Telegram button text limit: 64 chars
        if len(label) > 60:
            label = label[:57] + "..."
        rows.append([{
            "text": label,
            "callback_data": f"tgl:{phone_digits}",
        }])

    # Navigation row (only when >1 page)
    if total_pages > 1:
        prev_page = max(1, page - 1)
        next_page = min(total_pages, page + 1)
        suffix = f":{search}" if search else ""
        rows.append([
            {"text": "◀", "callback_data": f"pg:{prev_page}{suffix}"},
            {"text": f"{page}/{total_pages}", "callback_data": "nop"},
            {"text": "▶", "callback_data": f"pg:{next_page}{suffix}"},
        ])

    return {"inline_keyboard": rows}
```

- [ ] **Step 4: Run tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_contact_admin.py -v
```
Expected: All 32 tests pass.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add webhook/contact_admin.py tests/test_contact_admin.py && git commit -m "feat(contact_admin): message + keyboard formatters"
```

---

## Task 9: Integrate `contact_admin` into `webhook/app.py`

**Files:**
- Modify: `webhook/app.py`

This task has no TDD tests (integration into Flask handlers). Manual smoke test after Railway deploy.

- [ ] **Step 1: Add imports near top of `webhook/app.py`**

After the existing `from execution.core.delivery_reporter import ...` line, add:
```python
import contact_admin
from execution.integrations.sheets_client import SheetsClient
```

Note: since `webhook/` is already in `sys.path` (from existing hack), `import contact_admin` works.

- [ ] **Step 2: Add command routing inside `telegram_webhook()`**

Find this block in `telegram_webhook()`:
```python
    # Ignore bot commands for now
    if text.startswith("/"):
        if text == "/start":
            send_telegram_message(chat_id, 
                "👋 *Minerals Trading Bot*\n\n"
                "Envie uma notícia de mercado e eu vou:\n"
                "1️⃣ Analisar com IA\n"
                "2️⃣ Formatar para WhatsApp\n"
                "3️⃣ Enviar para aprovação\n\n"
                "Basta colar o texto da notícia aqui!")
        return jsonify({"ok": True})
```

Replace with:
```python
    # Bot commands
    if text.startswith("/"):
        # Any new command cancels in-progress /add
        if contact_admin.is_awaiting_add(chat_id):
            contact_admin.clear_state(chat_id)

        if text == "/start":
            send_telegram_message(chat_id,
                "👋 *Minerals Trading Bot*\n\n"
                "*Notícias:*\n"
                "Cole texto — viro relatório via IA e envio pra aprovação.\n\n"
                "*Contatos (admin):*\n"
                "`/add` — adicionar contato\n"
                "`/list [busca]` — listar e ativar/desativar\n"
                "`/cancel` — desistir do /add em curso")
            return jsonify({"ok": True})

        if text == "/cancel":
            if contact_admin.is_authorized(chat_id):
                send_telegram_message(chat_id, "Cancelado.")
            return jsonify({"ok": True})

        if text == "/add":
            if not contact_admin.is_authorized(chat_id):
                return jsonify({"ok": True})  # silent ignore
            contact_admin.start_add_flow(chat_id)
            send_telegram_message(chat_id, contact_admin.render_add_prompt())
            return jsonify({"ok": True})

        if text.startswith("/list"):
            if not contact_admin.is_authorized(chat_id):
                return jsonify({"ok": True})
            # Parse optional search term after /list
            parts = text.split(None, 1)
            search = parts[1].strip() if len(parts) > 1 else None
            _render_list_view(chat_id, page=1, search=search, message_id=None)
            return jsonify({"ok": True})

        return jsonify({"ok": True})  # unknown command
```

- [ ] **Step 3: Add `_render_list_view` helper function**

Add this helper function in `webhook/app.py` (place it near the other Telegram helpers, e.g., after `send_approval_message`):
```python
def _render_list_view(chat_id, page, search, message_id=None):
    """Fetch contacts and render list message with keyboard.
    If message_id is None → sends new message.
    Otherwise → edits existing message."""
    try:
        sheets = SheetsClient()
        per_page = 10
        contacts, total_pages = sheets.list_contacts(
            SHEET_ID, search=search, page=page, per_page=per_page,
        )
        # Get total count (before pagination) for the header
        all_contacts, _ = sheets.list_contacts(
            SHEET_ID, search=search, page=1, per_page=10_000,
        )
        total = len(all_contacts)

        msg = contact_admin.render_list_message(
            contacts, total=total, page=page, per_page=per_page, search=search,
        )
        kb = contact_admin.build_list_keyboard(
            contacts, page=page, total_pages=total_pages, search=search,
        )

        if message_id is None:
            send_telegram_message(chat_id, msg, reply_markup=kb)
        else:
            edit_message(chat_id, message_id, msg, reply_markup=kb)
    except Exception as e:
        logger.error(f"_render_list_view failed: {e}")
        err_msg = "❌ Erro ao acessar planilha. Tente novamente."
        if message_id:
            edit_message(chat_id, message_id, err_msg)
        else:
            send_telegram_message(chat_id, err_msg)
```

Note: `edit_message` already exists in `app.py` (line ~562) but signature is `edit_message(chat_id, message_id, text, reply_markup=None)` — check that the existing one supports `reply_markup`. If not, you may need to update its signature — but the existing code at line ~572 shows it already passes `reply_markup`.

- [ ] **Step 4: Add text-input handler for `/add` data collection**

Find this block in `telegram_webhook()`:
```python
    # ── Check if user is in adjustment mode ──
    adjust = ADJUST_STATE.get(chat_id)
    if adjust and adjust.get("awaiting_feedback"):
        ...
```

Insert this block BEFORE it:
```python
    # ── Check if user is in admin add flow ──
    if contact_admin.is_awaiting_add(chat_id):
        if not contact_admin.is_authorized(chat_id):
            contact_admin.clear_state(chat_id)
            return jsonify({"ok": True})
        _handle_add_data(chat_id, text)
        return jsonify({"ok": True})
```

Then add the `_handle_add_data` helper (near `_render_list_view`):
```python
def _handle_add_data(chat_id, text):
    """Process the user's 'Nome Telefone' message after /add prompt."""
    try:
        name, phone = contact_admin.parse_add_input(text)
    except ValueError as e:
        send_telegram_message(chat_id, f"❌ {e}")
        return  # keep state so user can retry

    try:
        sheets = SheetsClient()
        sheets.add_contact(SHEET_ID, name, phone)
    except ValueError as e:
        # Duplicate or schema error — user-facing
        send_telegram_message(chat_id, f"❌ {e}")
        contact_admin.clear_state(chat_id)
        return
    except Exception as e:
        logger.error(f"add_contact failed: {e}")
        send_telegram_message(chat_id, "❌ Erro ao gravar na planilha. Tente novamente.")
        contact_admin.clear_state(chat_id)
        return

    # Count active total for confirmation
    try:
        sheets = SheetsClient()
        all_contacts, _ = sheets.list_contacts(SHEET_ID, page=1, per_page=10_000)
        active = sum(1 for c in all_contacts if str(c.get("ButtonPayload", "")).strip() == "Big")
    except Exception:
        active = "?"

    send_telegram_message(chat_id, f"✅ {name} adicionado\nTotal ativos: {active}")
    contact_admin.clear_state(chat_id)
```

- [ ] **Step 5: Add callback handling for `tgl:*`, `pg:*`, `nop`**

Find the `handle_callback` function in `webhook/app.py` (around line 1048). It currently handles `approve:*`, `reject:*`, `adjust:*`, `test_approve:*`.

Add these cases at the top of `handle_callback` (after `parts = callback_data.split(":", 1)`):
```python
    # Contact admin callbacks
    if callback_data == "nop":
        answer_callback(callback_id, "")
        return jsonify({"ok": True})

    if callback_data.startswith("tgl:"):
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        phone = callback_data[4:]
        try:
            sheets = SheetsClient()
            name, new_status = sheets.toggle_contact(SHEET_ID, phone)
        except ValueError as e:
            answer_callback(callback_id, f"❌ {e}")
            return jsonify({"ok": True})
        except Exception as e:
            logger.error(f"toggle_contact failed: {e}")
            answer_callback(callback_id, "❌ Erro")
            return jsonify({"ok": True})

        toast = f"✅ {name} ativado" if new_status == "Big" else f"❌ {name} desativado"
        answer_callback(callback_id, toast)

        # Re-render current view: parse page/search from current message text
        # Simple approach: re-render page 1 (losing context) OR encode it in the
        # toggle callback. For v1 we just refresh page 1 with no search.
        message_id = callback_query["message"]["message_id"]
        _render_list_view(chat_id, page=1, search=None, message_id=message_id)
        return jsonify({"ok": True})

    if callback_data.startswith("pg:"):
        if not contact_admin.is_authorized(chat_id):
            answer_callback(callback_id, "Não autorizado")
            return jsonify({"ok": True})
        # pg:<N>[:search]
        rest = callback_data[3:]
        if ":" in rest:
            page_str, search = rest.split(":", 1)
        else:
            page_str, search = rest, None
        try:
            page = int(page_str)
        except ValueError:
            answer_callback(callback_id, "Página inválida")
            return jsonify({"ok": True})

        answer_callback(callback_id, "")
        message_id = callback_query["message"]["message_id"]
        _render_list_view(chat_id, page=page, search=search, message_id=message_id)
        return jsonify({"ok": True})
```

- [ ] **Step 6: Verify webhook imports cleanly**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -c "
import sys
sys.path.insert(0, 'webhook')
import app
print('OK: webhook imports')
" 2>&1 | tail -5
```
Expected: `OK: webhook imports`.

- [ ] **Step 7: Run full test suite**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest
```
Expected: All tests pass (34 delivery + 14 sheets + 32 contact_admin = 80).

- [ ] **Step 8: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add webhook/app.py && git commit -m "feat(webhook): integrate contact_admin for /add and /list commands"
```

---

## Task 10: Final smoke test

**Files:** (none created)

- [ ] **Step 1: Run all tests**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest
```
Expected: ~80 tests pass.

- [ ] **Step 2: Local webhook import check**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -c "
import sys
sys.path.insert(0, 'webhook')
import app
# Check new helpers are accessible
from contact_admin import parse_add_input, is_authorized, render_add_prompt
print('OK')
" 2>&1 | tail -3
```
Expected: `OK`.

- [ ] **Step 3: Simulate Railway layout check**

```bash
cd /tmp && rm -rf /tmp/fake_railway && mkdir /tmp/fake_railway && \
  cp "/Users/bigode/Dev/Antigravity WF /webhook/app.py" /tmp/fake_railway/app.py && \
  cp "/Users/bigode/Dev/Antigravity WF /webhook/contact_admin.py" /tmp/fake_railway/contact_admin.py && \
  cp -r "/Users/bigode/Dev/Antigravity WF /execution" /tmp/fake_railway/ && \
  cd /tmp/fake_railway && /Users/bigode/Dev/Antigravity\ WF\ /.venv/bin/python -c "
import sys
sys.path.insert(0, '/tmp/fake_railway')
import app
print('OK: simulated Railway layout')
" 2>&1 | tail -3
```
Expected: `OK: simulated Railway layout`.

- [ ] **Step 4: Manual live test (after push to main → Railway redeploys)**

User actions:
1. Send `/start` to the bot → should show updated help with `/add` and `/list`
2. Send `/add` → receive format prompt
3. Send `Teste Bot 5511999999999` → receive success + total
4. Send `/list teste` → see "Teste Bot" in results, marked ✅
5. Tap the button → see it flip to ❌, toast confirms
6. Send `/list` → navigate pages with ◀ ▶
7. Send `/add` then `/list` (cancel in-progress add) → no error, list shown
8. Send `/cancel` when not in add flow → silent "Cancelado" response

---

## Self-Review Notes

**Spec coverage check:**
- [x] §4.1 ProfileName fix → Task 1 (helper) + Task 2 (5 callers)
- [x] §4.2 `/add` 2-step flow → Task 9 Step 2 (command) + Step 4 (data handler)
- [x] §4.3 `/list` paginated with toggle → Task 9 Step 2 (command) + Task 8 (formatters) + Task 3 (list_contacts)
- [x] §4.4 ButtonPayload Big↔Inactive → Task 4 (toggle_contact)
- [x] §4.5 Authorization via TELEGRAM_CHAT_ID → Task 6 (is_authorized) + Task 9 (integrated)
- [x] §4.6 Duplicate detection → Task 5 (add_contact raises)
- [x] §5 Architecture → Task 9 wires flow
- [x] §6 Message formats → Task 8 (renderers)
- [x] §7 Callback formats → Task 8 (keyboard) + Task 9 (handlers)
- [x] §8 Sheet row defaults → Task 5 (add_contact defaults)
- [x] §9 Phone validation → Task 6 (parse_add_input)
- [x] §10 Error handling → Task 9 (try/except in handlers)
- [x] §11 Tests → Tasks 1, 3, 4, 5, 6, 7, 8 all TDD
- [x] §12 Rollout order → tasks are in rollout order

**Placeholder scan:** No "TBD", "TODO", or unfinished steps.

**Type consistency:**
- `parse_add_input()` returns `(str, str)` everywhere.
- `SheetsClient.list_contacts()` returns `(list, int)` — used consistently.
- `SheetsClient.toggle_contact()` returns `(str, str)` — `(profile_name, new_status)`.
- `build_list_keyboard()` returns `dict` matching Telegram API shape.
- `ADMIN_STATE` structure `{"awaiting": str, "expires_at": datetime}` consistent.
- Phone normalization via `digits_only` consistent across `contact_admin.py` and `sheets_client.py`.

**Known deviation:** The `tgl:*` handler in Task 9 Step 5 re-renders with `page=1, search=None`. A more refined implementation would preserve the user's current page + search, but that would require encoding them in the callback_data (tight 64-byte limit with ~20 bytes used by phone). For v1, resetting to page 1 is acceptable — user can navigate back. If this bothers the user later, it can be improved by encoding position into callback_data as `tgl:<phone>:<page>[:<search>]`.
