# Webhook Refactor + Workflow Trigger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split webhook/app.py (1,870 lines) into focused modules, then add workflow triggering via Telegram bot with live status tracking.

**Architecture:** Extract 6 domain modules from app.py keeping it as a thin Flask router. Then add `workflow_trigger.py` as a new module following the same pattern. All modules live in `webhook/` and import each other as needed — `telegram.py` is the leaf dependency.

**Tech Stack:** Python 3.10, Flask, requests (GitHub API), threading (background polling)

---

## File Structure

### Existing files to modify
- `webhook/app.py` — strip down to ~400 lines (Flask routes + config + dispatcher)
- `tests/test_webhook_status.py` — update imports to use new `status_builder` module

### New files to create (refactor)
- `webhook/telegram.py` — Telegram Bot API wrapper functions
- `webhook/pipeline.py` — Claude AI 3-agent chain + async processing
- `webhook/dispatch.py` — WhatsApp sending + approval/test async flows
- `webhook/callback_router.py` — handle_callback dispatcher
- `webhook/reports_nav.py` — /reports Telegram navigation helpers
- `webhook/status_builder.py` — /status workflow health message builder

### New files to create (feature)
- `webhook/workflow_trigger.py` — workflow catalog, GitHub API, trigger + polling
- `tests/test_workflow_trigger.py` — unit tests for workflow trigger module

---

## Task 1: Extract `telegram.py`

Leaf module — no webhook imports. All other modules will depend on this.

**Files:**
- Create: `webhook/telegram.py`
- Modify: `webhook/app.py:247-339` (remove moved functions)

- [ ] **Step 1: Create `webhook/telegram.py`**

```python
"""Telegram Bot API helpers."""
import os
import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def telegram_api(method, data):
    """Call Telegram Bot API and return parsed response."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=data, timeout=15)
        result = resp.json()
        if not result.get("ok"):
            logger.warning(f"Telegram {method} failed: {result.get('description', 'unknown')}")
        return result
    except Exception as e:
        logger.error(f"Telegram API error ({method}): {e}")
        return {"ok": False}


def answer_callback(callback_id, text):
    """Answer callback query (acknowledge button press)."""
    return telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text
    })


def send_telegram_message(chat_id, text, reply_markup=None):
    """Send a message via Telegram."""
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    return telegram_api("sendMessage", data)


def edit_message(chat_id, message_id, text, reply_markup=None):
    """Edit an existing message."""
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    return telegram_api("editMessageText", data)


def finalize_card(chat_id, callback_query, status_text):
    """Final feedback for curation buttons: edit the original card.

    Removes the inline keyboard so the user can't double-click, and guarantees
    a visual confirmation even if the Markdown edit fails.
    """
    message_id = callback_query.get("message", {}).get("message_id")
    if not message_id:
        logger.warning("finalize_card: missing message_id in callback_query")
        send_telegram_message(chat_id, status_text)
        return

    edit_result = edit_message(chat_id, message_id, status_text, reply_markup=None)
    if edit_result.get("ok"):
        return

    logger.warning(
        f"finalize_card: edit_message failed for msg_id={message_id}: "
        f"{edit_result.get('description', 'unknown')} — sending fallback"
    )
    plain = status_text.replace("*", "").replace("`", "").replace("_", "")
    send_telegram_message(chat_id, plain)


def send_approval_message(chat_id, draft_id, preview_text):
    """Send preview with approval/test/adjust/reject buttons."""
    display_text = preview_text[:3500] if len(preview_text) > 3500 else preview_text

    buttons = {
        "inline_keyboard": [
            [
                {"text": "✅ Aprovar e Enviar", "callback_data": f"approve:{draft_id}"},
                {"text": "🧪 Teste", "callback_data": f"test_approve:{draft_id}"}
            ],
            [
                {"text": "✏️ Ajustar", "callback_data": f"adjust:{draft_id}"},
                {"text": "❌ Rejeitar", "callback_data": f"reject:{draft_id}"}
            ]
        ]
    }

    return send_telegram_message(chat_id, f"📋 *PREVIEW*\n\n{display_text}", buttons)
```

- [ ] **Step 2: Update imports in `app.py`**

Replace the Telegram helpers section (lines 247-339) with imports. Remove all 6 function definitions (`telegram_api`, `answer_callback`, `send_telegram_message`, `edit_message`, `finalize_card`, `send_approval_message`). Add at the top of app.py:

```python
from telegram import (
    telegram_api, answer_callback, send_telegram_message,
    edit_message, finalize_card, send_approval_message,
)
```

Also remove the `TELEGRAM_BOT_TOKEN` line (line 54) from app.py since telegram.py owns it now. Keep only a reference for the register_commands route:

```python
from telegram import TELEGRAM_BOT_TOKEN
```

- [ ] **Step 3: Run tests to verify no regressions**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/ -x -q`
Expected: All 288 tests pass.

- [ ] **Step 4: Commit**

```bash
git add webhook/telegram.py webhook/app.py
git commit -m "refactor(webhook): extract telegram.py — Bot API helpers"
```

---

## Task 2: Extract `status_builder.py`

**Files:**
- Create: `webhook/status_builder.py`
- Modify: `webhook/app.py:162-245` (remove moved functions + `ALL_WORKFLOWS`)
- Modify: `tests/test_webhook_status.py` (update imports)

- [ ] **Step 1: Create `webhook/status_builder.py`**

```python
"""Build /status workflow health message for Telegram."""
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

ALL_WORKFLOWS = [
    "morning_check",
    "daily_report",
    "baltic_ingestion",
    "market_news",
    "rationale_news",
]


def _format_status_lines(states: dict, next_runs: dict) -> list:
    # Copy the full function body from app.py lines 171-213 exactly as-is
    ...


def build_status_message() -> str:
    # Copy the full function body from app.py lines 214-244 exactly as-is
    # Rename from _build_status_message to build_status_message (public API)
    ...
```

Copy the complete function bodies of `_format_status_lines` (lines 171-213) and `_build_status_message` (lines 214-244) from app.py into this module. Rename `_build_status_message` to `build_status_message` (no underscore prefix — it's now a public module function).

The function `_build_status_message` uses imports from `execution.core.state_store` and `execution.core.cron_parser` — keep those imports inside the function body as they currently are.

- [ ] **Step 2: Update app.py**

Remove `ALL_WORKFLOWS`, `_format_status_lines`, and `_build_status_message` from app.py. Add import:

```python
from status_builder import build_status_message, ALL_WORKFLOWS, _format_status_lines
```

In the `/status` command handler (around line 924), change `_build_status_message()` to `build_status_message()`.

- [ ] **Step 3: Update `tests/test_webhook_status.py`**

Replace the test's import of `app_module._format_status_lines` with a direct import:

```python
from status_builder import _format_status_lines
```

Update both test functions to call `_format_status_lines(states, next_runs)` directly instead of `app_module._format_status_lines(states, next_runs)`. Remove the `app_module` fixture if no longer needed.

- [ ] **Step 4: Run tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_webhook_status.py tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add webhook/status_builder.py webhook/app.py tests/test_webhook_status.py
git commit -m "refactor(webhook): extract status_builder.py — /status message builder"
```

---

## Task 3: Extract `reports_nav.py`

**Files:**
- Create: `webhook/reports_nav.py`
- Modify: `webhook/app.py:1228-1384` (remove moved functions)

- [ ] **Step 1: Create `webhook/reports_nav.py`**

```python
"""Platts Reports navigation helpers for Telegram bot."""
import os
import logging
import requests

from telegram import send_telegram_message, edit_message

logger = logging.getLogger(__name__)

PT_MONTHS = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

# Lazy Supabase client (same pattern as app.py)
_supabase_client = None


def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        sb_url = os.environ.get("SUPABASE_URL")
        sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not sb_url or not sb_key:
            logger.warning("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
            return None
        from supabase import create_client
        _supabase_client = create_client(sb_url, sb_key)
    return _supabase_client
```

Then copy these functions from app.py into this module (keep bodies identical):
- `_reports_show_types` (lines 1236-1248) — change `get_supabase()` calls to `_get_supabase()`
- `_reports_show_latest` (lines 1251-1286) — same
- `_reports_show_years` (lines 1289-1310) — same
- `_reports_show_months` (lines 1313-1343) — same
- `_reports_show_month_list` (lines 1346-1383) — same

Also move the `report_dl` callback handler logic (lines 1508-1543 from handle_callback) into a function here:

```python
def handle_report_download(chat_id, callback_id, report_id):
    """Download a PDF report from Supabase and send as Telegram document."""
    sb = _get_supabase()
    if not sb:
        # caller handles answer_callback
        return False, "Supabase não configurado"
    try:
        row = sb.table("platts_reports").select("storage_path, report_name").eq("id", report_id).single().execute()
        if not row.data:
            return False, "Relatório não encontrado"
        storage_path = row.data["storage_path"]
        report_name = row.data["report_name"]
        signed = sb.storage.from_("platts-reports").create_signed_url(storage_path, 3600)
        if not signed or not signed.get("signedURL"):
            return False, "Erro ao gerar link"
        pdf_url = signed["signedURL"]
        pdf_resp = requests.get(pdf_url, timeout=30)
        pdf_resp.raise_for_status()
        filename = storage_path.split("/")[-1]
        from telegram import TELEGRAM_BOT_TOKEN
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
            data={"chat_id": chat_id, "caption": f"📄 {report_name}", "parse_mode": "Markdown"},
            files={"document": (filename, pdf_resp.content, "application/pdf")},
            timeout=30,
        )
        if not resp.json().get("ok"):
            logger.warning(f"sendDocument failed: {resp.text[:200]}")
        return True, report_name
    except Exception as exc:
        logger.error(f"report_dl error: {exc}")
        return False, "Erro ao baixar relatório"
```

- [ ] **Step 2: Update app.py**

Remove `PT_MONTHS`, `_reports_show_types`, `_reports_show_latest`, `_reports_show_years`, `_reports_show_months`, `_reports_show_month_list` from app.py. Add import:

```python
from reports_nav import (
    _reports_show_types, _reports_show_latest, _reports_show_years,
    _reports_show_months, _reports_show_month_list, handle_report_download,
)
```

Note: keep `get_supabase()` in app.py for now — it's still used by the preview route. The reports_nav module has its own `_get_supabase()`.

- [ ] **Step 3: Run tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add webhook/reports_nav.py webhook/app.py
git commit -m "refactor(webhook): extract reports_nav.py — /reports navigation"
```

---

## Task 4: Extract `pipeline.py`

**Files:**
- Create: `webhook/pipeline.py`
- Modify: `webhook/app.py:475-547` (remove AI functions)

- [ ] **Step 1: Create `webhook/pipeline.py`**

```python
"""Claude AI 3-agent pipeline (Writer -> Critique -> Curator)."""
import os
import logging
import anthropic

from execution.core.prompts import WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
```

Copy these functions from app.py with bodies exactly as-is:
- `call_claude` (lines 479-498)
- `run_3_agents` (lines 500-536)
- `run_adjuster` (lines 538-546)

- [ ] **Step 2: Update app.py**

Remove `call_claude`, `run_3_agents`, `run_adjuster` from app.py. Remove the `import anthropic` line and the `ANTHROPIC_API_KEY` config line. Add:

```python
from pipeline import call_claude, run_3_agents, run_adjuster, ANTHROPIC_API_KEY
```

The test-ai route and the news processing check both reference `ANTHROPIC_API_KEY`, so keep the import.

- [ ] **Step 3: Run tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add webhook/pipeline.py webhook/app.py
git commit -m "refactor(webhook): extract pipeline.py — Claude 3-agent chain"
```

---

## Task 5: Extract `dispatch.py`

**Files:**
- Create: `webhook/dispatch.py`
- Modify: `webhook/app.py:409-738` (remove contacts + WhatsApp + async approval functions)

- [ ] **Step 1: Create `webhook/dispatch.py`**

```python
"""WhatsApp message dispatch and approval flow."""
import os
import json
import logging
import requests

from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
from execution.integrations.sheets_client import SheetsClient
from telegram import send_telegram_message, edit_message, send_approval_message

logger = logging.getLogger(__name__)

UAZAPI_URL = os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")
UAZAPI_TOKEN = (os.getenv("UAZAPI_TOKEN") or "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"
```

Copy these functions from app.py with bodies exactly as-is:
- `get_contacts` (lines 413-443) — update to use module-level `GOOGLE_CREDENTIALS_JSON` and `SHEET_ID`
- `send_whatsapp` (lines 449-473)
- `_send_whatsapp_raising` (lines 637-650)
- `process_approval_async` (lines 653-703)
- `process_test_send_async` (lines 705-738)

Note: `process_approval_async` and `process_test_send_async` call `get_contacts`, `send_whatsapp`, `_send_whatsapp_raising`, `send_telegram_message`, `edit_message`, `send_approval_message`, and `build_contact_from_row` — all available via imports in this module.

- [ ] **Step 2: Update app.py**

Remove the 6 functions listed above from app.py. Remove the `UAZAPI_URL`, `UAZAPI_TOKEN`, `GOOGLE_CREDENTIALS_JSON`, `SHEET_ID` config lines. Remove the `from execution.core.delivery_reporter import ...` and `from execution.integrations.sheets_client import SheetsClient` imports. Add:

```python
from dispatch import (
    get_contacts, send_whatsapp, process_approval_async,
    process_test_send_async, UAZAPI_URL, UAZAPI_TOKEN, SHEET_ID,
)
```

Keep `SHEET_ID` import because `_render_list_view` and `_handle_add_data` still use it in app.py.

- [ ] **Step 3: Run tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add webhook/dispatch.py webhook/app.py
git commit -m "refactor(webhook): extract dispatch.py — WhatsApp sending + approval flow"
```

---

## Task 6: Extract `callback_router.py`

This is the biggest extraction — the `handle_callback` function (~430 lines).

**Files:**
- Create: `webhook/callback_router.py`
- Modify: `webhook/app.py:1386-1814` (remove handle_callback)

- [ ] **Step 1: Create `webhook/callback_router.py`**

```python
"""Telegram callback query (button press) router."""
import os
import logging
import threading
from datetime import datetime, timezone
from flask import jsonify

import contact_admin
import query_handlers
import redis_queries
from telegram import (
    answer_callback, send_telegram_message, edit_message,
    finalize_card, send_approval_message,
)
from dispatch import (
    process_approval_async, process_test_send_async,
    SHEET_ID,
)
from reports_nav import (
    _reports_show_types, _reports_show_latest, _reports_show_years,
    _reports_show_months, _reports_show_month_list, handle_report_download,
)
from status_builder import build_status_message
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)
```

Copy the complete `handle_callback` function (lines 1386-1814) from app.py. Update internal references:

- `drafts_get`, `drafts_contains`, `drafts_update`, `drafts_set` — import from app: `from app import drafts_get, drafts_contains, drafts_update, drafts_set`
- `ADJUST_STATE` — import from app: `from app import ADJUST_STATE`
- `begin_reject_reason` — import from app: `from app import begin_reject_reason`
- `_build_status_message()` → `build_status_message()`
- `_show_main_menu` — import from app: `from app import _show_main_menu`
- `_render_list_view` — import from app: `from app import _render_list_view`
- `_safe_text`, `_safe_call` — import from app: `from app import _safe_text, _safe_call`
- `_run_pipeline_and_archive` — import from app: `from app import _run_pipeline_and_archive`
- `process_news_async` — import from app: `from app import process_news_async`
- `process_adjustment_async` — import from app: `from app import process_adjustment_async`
- `run_adjuster` — `from pipeline import run_adjuster`

For the `report_dl` callback section, replace the inline logic with:

```python
    if callback_data.startswith("report_dl:"):
        report_id = callback_data.split(":", 1)[1]
        ok, msg = handle_report_download(chat_id, callback_id, report_id)
        answer_callback(callback_id, f"📤 {msg}" if ok else msg)
        return jsonify({"ok": True})
```

- [ ] **Step 2: Update app.py**

Remove `handle_callback` from app.py. Add import:

```python
from callback_router import handle_callback
```

The `/webhook` route at line 892 already calls `handle_callback(callback_query)` — this will now resolve to the imported version.

- [ ] **Step 3: Run tests**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add webhook/callback_router.py webhook/app.py
git commit -m "refactor(webhook): extract callback_router.py — button press handler"
```

---

## Task 7: Verify refactor — run full test suite and check app.py size

- [ ] **Step 1: Run full test suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/ -v`
Expected: All 288 tests pass with no import errors.

- [ ] **Step 2: Verify app.py line count**

Run: `wc -l "/Users/bigode/Dev/Antigravity WF /webhook/app.py"`
Expected: ~500-600 lines (down from 1,870).

- [ ] **Step 3: Verify no function was lost**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && grep -c "^def \|^    def " webhook/app.py webhook/telegram.py webhook/pipeline.py webhook/dispatch.py webhook/callback_router.py webhook/reports_nav.py webhook/status_builder.py`
Expected: Total function count matches or exceeds original 51.

- [ ] **Step 4: Commit (if any fixups were needed)**

```bash
git add -A webhook/ tests/
git commit -m "refactor(webhook): verify and fixup module extraction"
```

---

## Task 8: Create `workflow_trigger.py` — catalog + GitHub API

**Files:**
- Create: `webhook/workflow_trigger.py`
- Create: `tests/test_workflow_trigger.py`

- [ ] **Step 1: Write the test file**

```python
"""Tests for webhook/workflow_trigger.py."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import json

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_fake_token")
    monkeypatch.setenv("GITHUB_OWNER", "bigodinhc")
    monkeypatch.setenv("GITHUB_REPO", "workflows_minerals")


@pytest.fixture
def wf():
    """Fresh import of workflow_trigger module."""
    if "workflow_trigger" in sys.modules:
        del sys.modules["workflow_trigger"]
    import workflow_trigger
    return workflow_trigger


def test_catalog_has_5_workflows(wf):
    assert len(wf.WORKFLOW_CATALOG) == 5
    ids = [w["id"] for w in wf.WORKFLOW_CATALOG]
    assert "morning_check.yml" in ids
    assert "daily_report.yml" in ids
    assert "baltic_ingestion.yml" in ids
    assert "market_news.yml" in ids
    assert "platts_reports.yml" in ids


def test_catalog_entries_have_required_fields(wf):
    for w in wf.WORKFLOW_CATALOG:
        assert "id" in w
        assert "name" in w
        assert "description" in w


@patch("workflow_trigger.requests.get")
def test_render_workflow_list_success(mock_get, wf):
    """render_workflow_list fetches last run per workflow and formats message."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "workflow_runs": [
            {
                "id": 123,
                "name": "morning_check",
                "path": ".github/workflows/morning_check.yml",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-04-16T08:30:00Z",
            }
        ]
    }
    mock_get.return_value = mock_response

    text, markup = wf.render_workflow_list()
    assert "MORNING CHECK" in text or "morning" in text.lower()
    assert markup is not None
    buttons = markup["inline_keyboard"]
    assert len(buttons) >= 5  # one row per workflow
    # Each button has callback_data starting with wf_run:
    assert any("wf_run:" in btn["callback_data"] for row in buttons for btn in row)


@patch("workflow_trigger.requests.get")
def test_render_workflow_list_api_failure(mock_get, wf):
    """When GitHub API fails, list still renders with unknown status."""
    mock_get.side_effect = Exception("Connection timeout")

    text, markup = wf.render_workflow_list()
    assert markup is not None
    buttons = markup["inline_keyboard"]
    assert len(buttons) >= 5


@patch("workflow_trigger.requests.post")
def test_trigger_workflow_success(mock_post, wf):
    mock_response = MagicMock()
    mock_response.status_code = 204
    mock_post.return_value = mock_response

    ok, error = wf.trigger_workflow("morning_check.yml")
    assert ok is True
    assert error is None
    mock_post.assert_called_once()
    call_url = mock_post.call_args[0][0]
    assert "morning_check.yml" in call_url
    assert "dispatches" in call_url


@patch("workflow_trigger.requests.post")
def test_trigger_workflow_failure(mock_post, wf):
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    mock_post.return_value = mock_response

    ok, error = wf.trigger_workflow("nonexistent.yml")
    assert ok is False
    assert error is not None


@patch("workflow_trigger.requests.get")
def test_find_triggered_run(mock_get, wf):
    """find_triggered_run returns the run_id of a recently-started in_progress run."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "workflow_runs": [
            {
                "id": 999,
                "status": "in_progress",
                "conclusion": None,
                "created_at": "2026-04-16T12:00:00Z",
            }
        ]
    }
    mock_get.return_value = mock_response

    run_id = wf.find_triggered_run("morning_check.yml")
    assert run_id == 999


@patch("workflow_trigger.requests.get")
def test_check_run_status_completed(mock_get, wf):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/bigodinhc/workflows_minerals/actions/runs/999",
    }
    mock_get.return_value = mock_response

    status, conclusion, url = wf.check_run_status(999)
    assert status == "completed"
    assert conclusion == "success"
    assert "999" in url
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_workflow_trigger.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'workflow_trigger'`

- [ ] **Step 3: Create `webhook/workflow_trigger.py`**

```python
"""Trigger GitHub Actions workflows from Telegram with live status polling."""
import os
import logging
import threading
import time
import requests

from telegram import send_telegram_message, edit_message, answer_callback

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_OWNER = os.getenv("GITHUB_OWNER", "bigodinhc")
GITHUB_REPO = os.getenv("GITHUB_REPO", "workflows_minerals")

WORKFLOW_CATALOG = [
    {
        "id": "morning_check.yml",
        "name": "MORNING CHECK",
        "description": "Precos Platts (Fines, Lump, Pellet, VIU)",
    },
    {
        "id": "baltic_ingestion.yml",
        "name": "BALTIC EXCHANGE",
        "description": "BDI + Rotas Capesize",
    },
    {
        "id": "daily_report.yml",
        "name": "DAILY SGX REPORT",
        "description": "Futuros SGX 62% Fe",
    },
    {
        "id": "market_news.yml",
        "name": "PLATTS INGESTION",
        "description": "Noticias Platts + curadoria",
    },
    {
        "id": "platts_reports.yml",
        "name": "PLATTS REPORTS",
        "description": "PDF reports scraping",
    },
]

_GH_API = "https://api.github.com"
_POLL_INTERVAL_SECONDS = 15
_POLL_TIMEOUT_SECONDS = 600  # 10 minutes


def _gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _workflow_name_by_id(workflow_id):
    for w in WORKFLOW_CATALOG:
        if w["id"] == workflow_id:
            return w["name"]
    return workflow_id


def render_workflow_list():
    """Fetch last run per workflow, return (text, reply_markup) for Telegram."""
    last_runs = {}
    try:
        resp = requests.get(
            f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs",
            headers=_gh_headers(),
            params={"per_page": 50},
            timeout=15,
        )
        if resp.status_code == 200:
            for run in resp.json().get("workflow_runs", []):
                path = run.get("path", "")
                wf_id = path.split("/")[-1] if "/" in path else path
                if wf_id not in last_runs:
                    last_runs[wf_id] = run
    except Exception as exc:
        logger.error(f"render_workflow_list GitHub API error: {exc}")

    text = "⚡ *Workflows*\n\nEscolha um workflow para disparar:"
    keyboard = []
    for wf in WORKFLOW_CATALOG:
        run = last_runs.get(wf["id"])
        if run:
            conclusion = run.get("conclusion")
            if conclusion == "success":
                icon = "✅"
            elif conclusion == "failure":
                icon = "❌"
            elif run.get("status") == "in_progress":
                icon = "🔄"
            else:
                icon = "⏳"
        else:
            icon = "❓"
        label = f"{icon} {wf['name']}"
        keyboard.append([{"text": label, "callback_data": f"wf_run:{wf['id']}"}])

    keyboard.append([{"text": "⬅ Menu", "callback_data": "wf_back_menu"}])
    markup = {"inline_keyboard": keyboard}
    return text, markup


def trigger_workflow(workflow_id):
    """Dispatch a workflow run. Returns (ok: bool, error: str | None)."""
    try:
        resp = requests.post(
            f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/dispatches",
            headers=_gh_headers(),
            json={"ref": "main", "inputs": {"dry_run": "false"}},
            timeout=15,
        )
        if resp.status_code == 204:
            return True, None
        return False, f"HTTP {resp.status_code}: {resp.text[:100]}"
    except Exception as exc:
        logger.error(f"trigger_workflow error: {exc}")
        return False, str(exc)


def find_triggered_run(workflow_id, max_wait=30):
    """Poll for a newly-created run matching workflow_id. Returns run_id or None."""
    for _ in range(max_wait // 5):
        time.sleep(5)
        try:
            resp = requests.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/runs",
                headers=_gh_headers(),
                params={"per_page": 1, "status": "in_progress"},
                timeout=15,
            )
            if resp.status_code == 200:
                runs = resp.json().get("workflow_runs", [])
                if runs:
                    return runs[0]["id"]
        except Exception as exc:
            logger.warning(f"find_triggered_run poll error: {exc}")
    return None


def check_run_status(run_id):
    """Check a specific run. Returns (status, conclusion, html_url)."""
    try:
        resp = requests.get(
            f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs/{run_id}",
            headers=_gh_headers(),
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data["status"], data.get("conclusion"), data.get("html_url", "")
    except Exception as exc:
        logger.error(f"check_run_status error: {exc}")
    return "unknown", None, ""


def _poll_and_update(chat_id, message_id, workflow_id, run_id):
    """Background thread: poll run status and edit Telegram message on changes."""
    name = _workflow_name_by_id(workflow_id)
    elapsed = 0

    while elapsed < _POLL_TIMEOUT_SECONDS:
        time.sleep(_POLL_INTERVAL_SECONDS)
        elapsed += _POLL_INTERVAL_SECONDS

        status, conclusion, html_url = check_run_status(run_id)

        if status == "completed":
            if conclusion == "success":
                icon = "✅"
                label = "concluido"
            else:
                icon = "❌"
                label = f"falhou ({conclusion})"

            buttons = {"inline_keyboard": [
                [{"text": "🔗 Ver no GitHub", "url": html_url}],
                [{"text": "⬅ Workflows", "callback_data": "wf_list"}],
            ]}
            edit_message(
                chat_id, message_id,
                f"{icon} *{name}* {label}",
                reply_markup=buttons,
            )
            return

    # Timeout
    edit_message(
        chat_id, message_id,
        f"⏰ *{name}* — timeout (10min)\n\nVerifique no GitHub.",
        reply_markup={"inline_keyboard": [
            [{"text": "⬅ Workflows", "callback_data": "wf_list"}],
        ]},
    )


def handle_wf_callback(callback_data, chat_id, message_id, callback_id):
    """Handle all wf_* callbacks. Returns Flask jsonify response."""
    from flask import jsonify

    if callback_data == "wf_list":
        answer_callback(callback_id, "")
        text, markup = render_workflow_list()
        edit_message(chat_id, message_id, text, reply_markup=markup)
        return jsonify({"ok": True})

    if callback_data == "wf_back_menu":
        answer_callback(callback_id, "")
        # Import here to avoid circular — app owns _show_main_menu
        from app import _show_main_menu
        _show_main_menu(chat_id)
        return jsonify({"ok": True})

    if callback_data.startswith("wf_run:"):
        workflow_id = callback_data.split(":", 1)[1]
        name = _workflow_name_by_id(workflow_id)
        answer_callback(callback_id, f"Disparando {name}...")

        edit_message(
            chat_id, message_id,
            f"🚀 *Disparando {name}...*",
            reply_markup={"inline_keyboard": [
                [{"text": "⬅ Cancelar", "callback_data": "wf_list"}],
            ]},
        )

        ok, error = trigger_workflow(workflow_id)
        if not ok:
            edit_message(
                chat_id, message_id,
                f"❌ *{name}* — erro ao disparar\n\n`{error}`",
                reply_markup={"inline_keyboard": [
                    [{"text": "🔄 Tentar novamente", "callback_data": f"wf_run:{workflow_id}"}],
                    [{"text": "⬅ Workflows", "callback_data": "wf_list"}],
                ]},
            )
            return jsonify({"ok": True})

        edit_message(
            chat_id, message_id,
            f"🔄 *{name}* rodando...\n\nAguardando conclusao.",
            reply_markup=None,
        )

        # Find the actual run_id, then poll in background
        def _track():
            run_id = find_triggered_run(workflow_id)
            if run_id is None:
                edit_message(
                    chat_id, message_id,
                    f"⚠️ *{name}* — disparado mas nao encontrei o run\n\nVerifique no GitHub.",
                    reply_markup={"inline_keyboard": [
                        [{"text": "⬅ Workflows", "callback_data": "wf_list"}],
                    ]},
                )
                return
            _poll_and_update(chat_id, message_id, workflow_id, run_id)

        threading.Thread(target=_track, daemon=True).start()
        return jsonify({"ok": True})

    answer_callback(callback_id, "")
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_workflow_trigger.py -v`
Expected: All 8 tests pass.

- [ ] **Step 5: Run full test suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/ -x -q`
Expected: All tests pass (288 + 8 new = 296).

- [ ] **Step 6: Commit**

```bash
git add webhook/workflow_trigger.py tests/test_workflow_trigger.py
git commit -m "feat(webhook): add workflow_trigger.py — GitHub Actions dispatch + status polling"
```

---

## Task 9: Wire workflow trigger into bot commands and menu

**Files:**
- Modify: `webhook/app.py` (add /workflows command + menu button)
- Modify: `webhook/callback_router.py` (route wf_* callbacks)

- [ ] **Step 1: Add `/workflows` command to app.py**

In the `/webhook` text command handler section (after the `/s` block, around line 1035-1039), add:

```python
        if text == "/workflows":
            if not contact_admin.is_authorized(chat_id):
                return jsonify({"ok": True})
            from workflow_trigger import render_workflow_list
            text_msg, markup = render_workflow_list()
            send_telegram_message(chat_id, text_msg, reply_markup=markup)
            return jsonify({"ok": True})
```

- [ ] **Step 2: Add "Workflows" button to `/s` menu**

In `_show_main_menu` function, add a new row to the inline_keyboard:

```python
            [
                {"text": "⚡ Workflows", "callback_data": "wf_list"},
                {"text": "❓ Help", "callback_data": "menu:help"},
            ],
```

Replace the existing last row that has "Help" so it's not duplicated.

- [ ] **Step 3: Route wf_* callbacks in callback_router.py**

At the top of `handle_callback`, after the `nop` check (around line 1395), add:

```python
    # Workflow trigger callbacks
    if callback_data.startswith("wf_") or callback_data == "wf_list":
        from workflow_trigger import handle_wf_callback
        message_id = callback_query["message"]["message_id"]
        return handle_wf_callback(callback_data, chat_id, message_id, callback_id)
```

- [ ] **Step 4: Add `/workflows` to register-commands**

In the `register_commands` route, add to the commands list:

```python
        {"command": "workflows", "description": "Disparar workflows (GitHub Actions)"},
```

- [ ] **Step 5: Run full test suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/ -x -q`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add webhook/app.py webhook/callback_router.py
git commit -m "feat(webhook): wire /workflows command + menu button + callback routing"
```

---

## Task 10: Add GITHUB_TOKEN to Railway env and test end-to-end

- [ ] **Step 1: Verify GITHUB_TOKEN is referenced in .env.example**

Add to `.env.example`:

```
GITHUB_TOKEN=ghp_your_github_personal_access_token
GITHUB_OWNER=bigodinhc
GITHUB_REPO=workflows_minerals
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "chore: add GITHUB_TOKEN to .env.example"
```

- [ ] **Step 3: Manual test — deploy to Railway and verify**

After deploying:
1. Send `/workflows` to the bot — should show 5 workflows with status icons
2. Tap a workflow button — should show "Disparando..." then "rodando..."
3. Wait for completion — message should update to "concluido" or "falhou"
4. Send `/s` — should show "Workflows" button in menu
5. Tap "Workflows" in menu — should navigate to workflow list
6. Call `/admin/register-commands` — verify `/workflows` appears in autocomplete
