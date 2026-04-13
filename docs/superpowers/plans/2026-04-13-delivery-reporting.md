# Delivery Reporting System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify WhatsApp delivery tracking across all sending flows (4 GH Actions scripts + webhook) with per-contact status, Telegram notifications, and structured dashboard view.

**Architecture:** New shared `DeliveryReporter` module in `execution/core/` that encapsulates the send loop, emits structured JSON to stdout (parsed by dashboard from GH Actions logs), and sends Telegram summary. Each caller provides a `send_fn` callback for actual dispatch.

**Tech Stack:** Python 3.10 (dataclasses, pytest), Flask, Next.js 16 / React 19, UazAPI (WhatsApp), Telegram Bot API, GitHub Actions logs as transient persistence layer.

**Spec:** `docs/superpowers/specs/2026-04-13-delivery-reporting-design.md`

---

## File Structure

**Created:**
- `execution/core/delivery_reporter.py` — Main module with `Contact`, `DeliveryResult`, `DeliveryReport`, `DeliveryReporter`
- `tests/test_delivery_reporter.py` — Pytest unit tests
- `tests/__init__.py` — Enable package imports
- `tests/conftest.py` — Shared pytest fixtures
- `dashboard/app/api/delivery-report/route.ts` — Parse GH logs → JSON report
- `dashboard/components/delivery/DeliveryReportView.tsx` — Structured report UI

**Modified:**
- `requirements.txt` — Add `pytest>=7.0.0`
- `execution/scripts/morning_check.py` — Use DeliveryReporter
- `execution/scripts/send_daily_report.py` — Use DeliveryReporter
- `execution/scripts/baltic_ingestion.py` — Use DeliveryReporter
- `execution/scripts/send_news.py` — Use DeliveryReporter
- `webhook/app.py` — Use DeliveryReporter in `process_approval_async`; update Claude model
- `execution/integrations/claude_client.py` — Update Claude model
- `dashboard/app/page.tsx` — Integrate DeliveryReportView + query-param auto-open

---

## Task 1: Setup pytest infrastructure

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `pytest.ini`

- [ ] **Step 1: Add pytest to requirements**

Modify `requirements.txt` — append at end:
```
pytest>=7.0.0
pytest-mock>=3.10.0
```

- [ ] **Step 2: Install pytest locally**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pip install "pytest>=7.0.0" "pytest-mock>=3.10.0"
```

Expected: Both packages install cleanly.

- [ ] **Step 3: Create `tests/__init__.py`**

Create empty file at `tests/__init__.py` (zero bytes).

- [ ] **Step 4: Create `pytest.ini` at repo root**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

- [ ] **Step 5: Create `tests/conftest.py`**

```python
"""Shared pytest fixtures."""
import sys
from pathlib import Path

# Add repo root to sys.path so tests can import execution.* modules
sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 6: Verify pytest runs (with existing test_format.py ignored)**

The existing `tests/test_format.py` is a script, not a pytest test. Rename it so pytest skips it:

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git mv tests/test_format.py tests/_manual_format_check.py
```

Then run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest
```
Expected: "no tests ran" (zero test files match pattern).

- [ ] **Step 7: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add requirements.txt tests/__init__.py tests/conftest.py pytest.ini tests/_manual_format_check.py && git commit -m "chore: setup pytest infrastructure for unit tests"
```

---

## Task 2: DeliveryReporter — dataclasses (TDD)

**Files:**
- Create: `execution/core/delivery_reporter.py`
- Create: `tests/test_delivery_reporter.py`

- [ ] **Step 1: Write failing test for Contact dataclass**

Create `tests/test_delivery_reporter.py`:
```python
"""Tests for execution.core.delivery_reporter module."""
import pytest
from execution.core.delivery_reporter import Contact, DeliveryResult, DeliveryReport
from datetime import datetime


def test_contact_dataclass():
    c = Contact(name="João Silva", phone="5511999999999")
    assert c.name == "João Silva"
    assert c.phone == "5511999999999"


def test_delivery_result_success():
    c = Contact(name="Ana", phone="5511888888888")
    r = DeliveryResult(contact=c, success=True, error=None, duration_ms=340)
    assert r.success is True
    assert r.error is None


def test_delivery_result_failure():
    c = Contact(name="Ana", phone="5511888888888")
    r = DeliveryResult(contact=c, success=False, error="timeout", duration_ms=30000)
    assert r.success is False
    assert r.error == "timeout"


def test_delivery_report_properties():
    c1 = Contact(name="A", phone="111")
    c2 = Contact(name="B", phone="222")
    c3 = Contact(name="C", phone="333")
    results = [
        DeliveryResult(contact=c1, success=True, error=None, duration_ms=100),
        DeliveryResult(contact=c2, success=False, error="timeout", duration_ms=30000),
        DeliveryResult(contact=c3, success=True, error=None, duration_ms=150),
    ]
    now = datetime.now()
    report = DeliveryReport(
        workflow="test",
        started_at=now,
        finished_at=now,
        results=results,
    )
    assert report.total == 3
    assert report.success_count == 2
    assert report.failure_count == 1
    assert len(report.failures) == 1
    assert report.failures[0].contact.name == "B"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: `ModuleNotFoundError: No module named 'execution.core.delivery_reporter'`

- [ ] **Step 3: Create `execution/core/delivery_reporter.py` with dataclasses**

```python
"""
Delivery reporter: shared module for tracking WhatsApp send results
across GH Actions scripts and webhook flows.

Emits structured JSON to stdout (for dashboard parsing) and sends
Telegram summary notification at end of dispatch.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Contact:
    """A WhatsApp contact (name + phone)."""
    name: str
    phone: str


@dataclass
class DeliveryResult:
    """Result of a single delivery attempt."""
    contact: Contact
    success: bool
    error: Optional[str]
    duration_ms: int


@dataclass
class DeliveryReport:
    """Aggregated report of all deliveries in a dispatch."""
    workflow: str
    started_at: datetime
    finished_at: datetime
    results: list

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def failures(self) -> list:
        return [r for r in self.results if not r.success]
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py && git commit -m "feat(delivery_reporter): add Contact, DeliveryResult, DeliveryReport dataclasses"
```

---

## Task 3: DeliveryReporter — dispatch() loop with error categorization (TDD)

**Files:**
- Modify: `execution/core/delivery_reporter.py`
- Modify: `tests/test_delivery_reporter.py`

- [ ] **Step 1: Write failing tests for dispatch()**

Append to `tests/test_delivery_reporter.py`:
```python
from unittest.mock import MagicMock
from execution.core.delivery_reporter import DeliveryReporter
import requests


def test_dispatch_all_success():
    send_fn = MagicMock()  # does not raise = success
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"User{i}", phone=f"11{i}") for i in range(5)]
    report = reporter.dispatch(contacts, message="hello")
    assert report.total == 5
    assert report.success_count == 5
    assert report.failure_count == 0
    assert send_fn.call_count == 5


def test_dispatch_partial_failure():
    call_count = {"n": 0}
    def send_fn(phone, text):
        call_count["n"] += 1
        if call_count["n"] in (2, 4):
            raise RuntimeError("boom")
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"User{i}", phone=f"11{i}") for i in range(5)]
    report = reporter.dispatch(contacts, message="hello")
    assert report.success_count == 3
    assert report.failure_count == 2
    assert all("boom" in r.error for r in report.failures)


def test_dispatch_all_failure():
    def send_fn(phone, text):
        raise RuntimeError("total failure")
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name="A", phone="111")]
    report = reporter.dispatch(contacts, message="hi")
    assert report.success_count == 0
    assert report.failure_count == 1


def test_error_categorization_timeout():
    def send_fn(phone, text):
        raise requests.Timeout("read timeout")
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.failures[0].error == "timeout"


def test_error_categorization_http_error():
    def send_fn(phone, text):
        resp = requests.Response()
        resp.status_code = 400
        resp._content = b'{"error":"invalid number"}'
        err = requests.HTTPError(response=resp)
        raise err
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.failures[0].error.startswith("HTTP 400")


def test_dispatch_tracks_duration():
    send_fn = MagicMock()
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.results[0].duration_ms >= 0
    assert (report.finished_at - report.started_at).total_seconds() >= 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: `DeliveryReporter is not defined` or similar import error.

- [ ] **Step 3: Add `DeliveryReporter` class to `execution/core/delivery_reporter.py`**

Append to `execution/core/delivery_reporter.py`:
```python
import time
from typing import Callable, Iterable


def _categorize_error(exc: Exception) -> str:
    """Convert exception into short error category string."""
    import requests as _rq
    if isinstance(exc, _rq.Timeout):
        return "timeout"
    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        body = (exc.response.text or "")[:100]
        return f"HTTP {exc.response.status_code}: {body}"
    return str(exc)[:200]


class DeliveryReporter:
    """Shared delivery tracker for WhatsApp workflows."""

    def __init__(
        self,
        workflow: str,
        send_fn: Callable[[str, str], None],
        notify_telegram: bool = True,
        telegram_chat_id: Optional[str] = None,
        dashboard_base_url: str = "https://workflows-minerals.vercel.app",
        gh_run_id: Optional[str] = None,
    ):
        self.workflow = workflow
        self.send_fn = send_fn
        self.notify_telegram = notify_telegram
        self.telegram_chat_id = telegram_chat_id
        self.dashboard_base_url = dashboard_base_url
        self.gh_run_id = gh_run_id

    def dispatch(
        self,
        contacts: Iterable[Contact],
        message: str,
        on_progress: Optional[Callable[[int, int, DeliveryResult], None]] = None,
    ) -> DeliveryReport:
        """Send `message` to each contact. Never raises on send failure."""
        started_at = datetime.now().astimezone()
        results: list = []
        contacts_list = list(contacts)
        total = len(contacts_list)

        for i, contact in enumerate(contacts_list):
            t0 = time.monotonic()
            success = False
            error: Optional[str] = None
            try:
                self.send_fn(contact.phone, message)
                success = True
            except Exception as exc:
                error = _categorize_error(exc)
            duration_ms = int((time.monotonic() - t0) * 1000)

            result = DeliveryResult(
                contact=contact,
                success=success,
                error=error,
                duration_ms=duration_ms,
            )
            results.append(result)

            if on_progress is not None:
                try:
                    on_progress(i + 1, total, result)
                except Exception:
                    pass  # progress callback failures do not abort dispatch

        finished_at = datetime.now().astimezone()
        return DeliveryReport(
            workflow=self.workflow,
            started_at=started_at,
            finished_at=finished_at,
            results=results,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: All 10 tests PASS (4 from Task 2 + 6 new).

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py && git commit -m "feat(delivery_reporter): implement dispatch() with error categorization"
```

---

## Task 4: DeliveryReporter — stdout JSON output with markers (TDD)

**Files:**
- Modify: `execution/core/delivery_reporter.py`
- Modify: `tests/test_delivery_reporter.py`

- [ ] **Step 1: Write failing tests for JSON stdout**

Append to `tests/test_delivery_reporter.py`:
```python
import json
import re


def test_dispatch_emits_json_block_on_stdout(capsys):
    send_fn = MagicMock()
    reporter = DeliveryReporter(workflow="test_wf", send_fn=send_fn, notify_telegram=False)
    reporter.dispatch([Contact(name="Ana", phone="5511999")], message="hi")
    captured = capsys.readouterr().out
    assert "<<<DELIVERY_REPORT_START>>>" in captured
    assert "<<<DELIVERY_REPORT_END>>>" in captured


def test_stdout_json_is_parseable(capsys):
    send_fn = MagicMock()
    reporter = DeliveryReporter(workflow="test_wf", send_fn=send_fn, notify_telegram=False)
    reporter.dispatch([
        Contact(name="Ana", phone="5511999"),
        Contact(name="Bob", phone="5511888"),
    ], message="hi")
    captured = capsys.readouterr().out
    match = re.search(
        r"<<<DELIVERY_REPORT_START>>>\s*(\{.*?\})\s*<<<DELIVERY_REPORT_END>>>",
        captured,
        re.DOTALL,
    )
    assert match, "JSON block not found"
    data = json.loads(match.group(1))
    assert data["workflow"] == "test_wf"
    assert data["summary"]["total"] == 2
    assert data["summary"]["success"] == 2
    assert data["summary"]["failure"] == 0
    assert len(data["results"]) == 2
    assert data["results"][0]["name"] == "Ana"
    assert data["results"][0]["phone"] == "5511999"
    assert data["results"][0]["success"] is True
    assert data["results"][0]["error"] is None
    assert "duration_ms" in data["results"][0]


def test_stdout_json_includes_failures(capsys):
    def send_fn(phone, text):
        if phone == "222":
            raise RuntimeError("fail me")
    reporter = DeliveryReporter(workflow="test", send_fn=send_fn, notify_telegram=False)
    reporter.dispatch([
        Contact(name="OK", phone="111"),
        Contact(name="Bad", phone="222"),
    ], message="hi")
    captured = capsys.readouterr().out
    match = re.search(
        r"<<<DELIVERY_REPORT_START>>>\s*(\{.*?\})\s*<<<DELIVERY_REPORT_END>>>",
        captured,
        re.DOTALL,
    )
    data = json.loads(match.group(1))
    assert data["summary"]["failure"] == 1
    fail = [r for r in data["results"] if not r["success"]][0]
    assert fail["name"] == "Bad"
    assert "fail me" in fail["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py::test_dispatch_emits_json_block_on_stdout -v
```
Expected: `AssertionError: assert '<<<DELIVERY_REPORT_START>>>' in '...'`

- [ ] **Step 3: Add JSON emission to `dispatch()`**

In `execution/core/delivery_reporter.py`, add this helper method to `DeliveryReporter` class (below `dispatch`):

```python
    def _emit_stdout_report(self, report: DeliveryReport) -> None:
        """Print structured JSON report delimited by markers for dashboard parsing."""
        import json as _json
        payload = {
            "workflow": report.workflow,
            "started_at": report.started_at.isoformat(),
            "finished_at": report.finished_at.isoformat(),
            "duration_seconds": int((report.finished_at - report.started_at).total_seconds()),
            "summary": {
                "total": report.total,
                "success": report.success_count,
                "failure": report.failure_count,
            },
            "results": [
                {
                    "name": r.contact.name,
                    "phone": r.contact.phone,
                    "success": r.success,
                    "error": r.error,
                    "duration_ms": r.duration_ms,
                }
                for r in report.results
            ],
        }
        print("<<<DELIVERY_REPORT_START>>>")
        print(_json.dumps(payload, indent=2, ensure_ascii=False))
        print("<<<DELIVERY_REPORT_END>>>")
```

Then modify `dispatch()` to call it before returning. Replace the final `return DeliveryReport(...)` with:
```python
        report = DeliveryReport(
            workflow=self.workflow,
            started_at=started_at,
            finished_at=finished_at,
            results=results,
        )
        self._emit_stdout_report(report)
        return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: All 13 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py && git commit -m "feat(delivery_reporter): emit structured JSON report to stdout"
```

---

## Task 5: DeliveryReporter — Telegram notification format (TDD)

**Files:**
- Modify: `execution/core/delivery_reporter.py`
- Modify: `tests/test_delivery_reporter.py`

- [ ] **Step 1: Write failing tests for message formatting**

Append to `tests/test_delivery_reporter.py`:
```python
from execution.core.delivery_reporter import _format_telegram_message


def _make_report(workflow, results):
    from datetime import datetime
    now = datetime.now().astimezone()
    return DeliveryReport(
        workflow=workflow,
        started_at=now,
        finished_at=now,
        results=results,
    )


def test_telegram_message_all_success():
    c = Contact(name="Ana", phone="111")
    results = [DeliveryResult(contact=c, success=True, error=None, duration_ms=100)]
    report = _make_report("morning_check", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "✅" in msg
    assert "MORNING CHECK" in msg
    assert "Total: 1" in msg
    assert "OK: 1" in msg
    assert "Falha: 0" in msg


def test_telegram_message_with_failures():
    results = [
        DeliveryResult(contact=Contact(name="A", phone="111"), success=True, error=None, duration_ms=100),
        DeliveryResult(contact=Contact(name="Carlos", phone="222"), success=False, error="timeout", duration_ms=30000),
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "⚠️" in msg
    assert "Carlos" in msg
    assert "222" in msg
    assert "timeout" in msg


def test_telegram_message_total_failure():
    results = [
        DeliveryResult(contact=Contact(name=f"U{i}", phone=str(i)), success=False, error="boom", duration_ms=100)
        for i in range(10)
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "🚨" in msg
    assert "FALHA TOTAL" in msg


def test_telegram_message_truncates_long_failure_list():
    results = [
        DeliveryResult(contact=Contact(name=f"U{i}", phone=str(i)), success=False, error="boom", duration_ms=100)
        for i in range(50)
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "...e mais 35" in msg


def test_telegram_message_includes_run_id_link():
    results = [DeliveryResult(contact=Contact(name="A", phone="111"), success=True, error=None, duration_ms=100)]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash.com", gh_run_id="999")
    assert "https://dash.com/?run_id=999" in msg


def test_telegram_message_home_link_when_no_run_id():
    results = [DeliveryResult(contact=Contact(name="A", phone="111"), success=True, error=None, duration_ms=100)]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash.com", gh_run_id=None)
    assert "https://dash.com/" in msg
    assert "?run_id=" not in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: `ImportError: cannot import name '_format_telegram_message'`.

- [ ] **Step 3: Add `_format_telegram_message` function**

In `execution/core/delivery_reporter.py`, add at module level (after `_categorize_error`):

```python
_MAX_FAILURES_LISTED = 15


def _format_telegram_message(
    report: DeliveryReport,
    dashboard_base_url: str,
    gh_run_id: Optional[str],
) -> str:
    """Build Telegram-ready text summary of a DeliveryReport."""
    failure_pct = (report.failure_count / report.total * 100) if report.total else 0

    if report.failure_count == 0:
        emoji = "✅"
        header = f"{emoji} {report.workflow.upper().replace('_', ' ')}"
    elif failure_pct > 50:
        emoji = "🚨"
        header = f"{emoji} {report.workflow.upper().replace('_', ' ')} — FALHA TOTAL"
    else:
        emoji = "⚠️"
        header = f"{emoji} {report.workflow.upper().replace('_', ' ')}"

    duration = report.finished_at - report.started_at
    minutes = int(duration.total_seconds() // 60)
    seconds = int(duration.total_seconds() % 60)
    dur_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    when = report.started_at.strftime("%d/%m/%Y %H:%M")

    lines = [header, f"{when} ({dur_str})", ""]
    lines.append(
        f"📊 Total: {report.total} | OK: {report.success_count} | "
        f"Falha: {report.failure_count}"
    )
    lines.append("")

    if report.failure_count == 0:
        lines.append("Todos os contatos receberam.")
    elif failure_pct > 50 and report.success_count == 0:
        lines.append("Todos os envios falharam. Verifique:")
        lines.append("• Token UAZAPI")
        lines.append("• Status do servico UazAPI")
        lines.append("• Logs do GitHub Actions")
        first_err = report.failures[0].error if report.failures else "unknown"
        lines.append("")
        lines.append(f"Primeira falha: {first_err}")
    else:
        lines.append("❌ FALHAS:")
        listed = report.failures[:_MAX_FAILURES_LISTED]
        for f in listed:
            lines.append(f"• {f.contact.name} ({f.contact.phone}) — {f.error}")
        remaining = len(report.failures) - len(listed)
        if remaining > 0:
            lines.append(f"...e mais {remaining} falhas")

    link = (
        f"{dashboard_base_url}/?run_id={gh_run_id}"
        if gh_run_id
        else f"{dashboard_base_url}/"
    )
    lines.append("")
    lines.append(f"[Ver no dashboard]({link})")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: All 19 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py && git commit -m "feat(delivery_reporter): add Telegram message formatting"
```

---

## Task 6: DeliveryReporter — Telegram send integration (TDD)

**Files:**
- Modify: `execution/core/delivery_reporter.py`
- Modify: `tests/test_delivery_reporter.py`

- [ ] **Step 1: Write failing tests for Telegram sending**

Append to `tests/test_delivery_reporter.py`:
```python
def test_dispatch_sends_telegram_when_enabled(monkeypatch):
    send_calls = []

    class FakeTelegram:
        def __init__(self):
            pass

        def send_message(self, text, chat_id=None, **kwargs):
            send_calls.append({"text": text, "chat_id": chat_id})
            return 1

    monkeypatch.setattr(
        "execution.core.delivery_reporter._build_telegram_client",
        lambda: FakeTelegram(),
    )

    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=True,
        telegram_chat_id="123",
    )
    reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert len(send_calls) == 1
    assert "test".upper() in send_calls[0]["text"].upper()
    assert send_calls[0]["chat_id"] == "123"


def test_dispatch_skips_telegram_when_disabled():
    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=False,
    )
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.total == 1


def test_dispatch_continues_when_telegram_fails(monkeypatch):
    class BrokenTelegram:
        def send_message(self, text, chat_id=None, **kwargs):
            raise RuntimeError("telegram down")

    monkeypatch.setattr(
        "execution.core.delivery_reporter._build_telegram_client",
        lambda: BrokenTelegram(),
    )
    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=True,
    )
    report = reporter.dispatch([Contact(name="A", phone="111")], message="hi")
    assert report.total == 1
    assert report.success_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: Failing tests referencing `_build_telegram_client`.

- [ ] **Step 3: Add Telegram integration**

In `execution/core/delivery_reporter.py`, add at module level (below `_format_telegram_message`):

```python
def _build_telegram_client():
    """Factory for TelegramClient. Separate function to allow test monkeypatching."""
    from execution.integrations.telegram_client import TelegramClient
    return TelegramClient()
```

In the `DeliveryReporter.dispatch()` method, after `self._emit_stdout_report(report)` and before `return report`, add:

```python
        if self.notify_telegram:
            self._send_telegram_summary(report)
```

Then add this method to the class:

```python
    def _send_telegram_summary(self, report: DeliveryReport) -> None:
        """Send final delivery summary to Telegram. Never raises."""
        try:
            text = _format_telegram_message(
                report,
                dashboard_base_url=self.dashboard_base_url,
                gh_run_id=self.gh_run_id,
            )
            client = _build_telegram_client()
            client.send_message(
                text=text,
                chat_id=self.telegram_chat_id,
                parse_mode="Markdown",
            )
        except Exception as exc:
            print(f"[WARN] Failed to send Telegram summary: {exc}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: All 22 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py && git commit -m "feat(delivery_reporter): integrate Telegram notification on dispatch"
```

---

## Task 7: DeliveryReporter — on_progress callback (TDD)

**Files:**
- Modify: `tests/test_delivery_reporter.py`

The callback is already implemented in Task 3. Add tests to lock behavior.

- [ ] **Step 1: Write test for progress callback**

Append to `tests/test_delivery_reporter.py`:
```python
def test_on_progress_called_per_contact():
    events = []

    def on_progress(processed, total, result):
        events.append((processed, total, result.contact.name))

    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=False,
    )
    reporter.dispatch(
        [Contact(name=f"U{i}", phone=str(i)) for i in range(3)],
        message="hi",
        on_progress=on_progress,
    )
    assert len(events) == 3
    assert events[0] == (1, 3, "U0")
    assert events[1] == (2, 3, "U1")
    assert events[2] == (3, 3, "U2")


def test_on_progress_exception_does_not_abort():
    def on_progress(processed, total, result):
        raise RuntimeError("callback broken")

    reporter = DeliveryReporter(
        workflow="test",
        send_fn=MagicMock(),
        notify_telegram=False,
    )
    report = reporter.dispatch(
        [Contact(name="A", phone="111"), Contact(name="B", phone="222")],
        message="hi",
        on_progress=on_progress,
    )
    assert report.total == 2
    assert report.success_count == 2
```

- [ ] **Step 2: Run tests to verify they pass**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest tests/test_delivery_reporter.py -v
```
Expected: All 24 tests PASS (callback was already implemented in Task 3).

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add tests/test_delivery_reporter.py && git commit -m "test(delivery_reporter): add progress callback behavior tests"
```

---

## Task 8: Refactor morning_check.py to use DeliveryReporter

**Files:**
- Modify: `execution/scripts/morning_check.py` (lines 242-285)

- [ ] **Step 1: Read current state of `morning_check.py`**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && sed -n '1,30p' execution/scripts/morning_check.py
```

Note existing imports and `main()` function structure.

- [ ] **Step 2: Add import for DeliveryReporter**

Add after existing imports near top of `execution/scripts/morning_check.py`:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact
```

- [ ] **Step 3: Replace send loop**

Find the block in `main()` that starts at `# 5. Send & Mark` (around line 242) ending with `logger.info(f"Broadcast complete. Sent: {success_count}")`.

Replace with:
```python
    # 5. Send & Mark using shared DeliveryReporter
    contacts = sheets.get_contacts(SHEET_ID, SHEET_NAME_CONTACTS)

    if not contacts:
        logger.warning("No contacts found.")
        return

    uazapi = UazapiClient()

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

    if args.dry_run:
        logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
        return

    reporter = DeliveryReporter(
        workflow="morning_check",
        send_fn=uazapi.send_message,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(delivery_contacts, message)

    logger.info(
        f"Broadcast complete. Sent: {report.success_count}, Failed: {report.failure_count}"
    )

    if report.success_count > 0:
        sheets.mark_daily_status(SHEET_ID, date_str, REPORT_TYPE)
        logger.info("Control sheet updated.")
```

- [ ] **Step 4: Verify script still parses / imports without error**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -c "from execution.scripts import morning_check"
```
Expected: No import errors.

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/morning_check.py && git commit -m "refactor(morning_check): use DeliveryReporter for unified tracking"
```

---

## Task 9: Refactor send_daily_report.py to use DeliveryReporter

**Files:**
- Modify: `execution/scripts/send_daily_report.py` (lines 131-165)

- [ ] **Step 1: Add import**

Add near top of `execution/scripts/send_daily_report.py`:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact
```

- [ ] **Step 2: Replace send loop**

Locate the block starting at `# 4. Send Messages` and ending with the end of the per-contact try/except.

Replace with:
```python
        # 4. Send Messages via DeliveryReporter
        from execution.integrations.uazapi_client import UazapiClient
        uazapi = UazapiClient()

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

        if args.dry_run:
            logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
            return

        reporter = DeliveryReporter(
            workflow="daily_report",
            send_fn=uazapi.send_message,
            gh_run_id=os.getenv("GITHUB_RUN_ID"),
        )
        report = reporter.dispatch(delivery_contacts, message)
        logger.info(
            f"Daily report broadcast complete. Sent: {report.success_count}, "
            f"Failed: {report.failure_count}"
        )
```

- [ ] **Step 3: Verify imports**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -c "from execution.scripts import send_daily_report"
```
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/send_daily_report.py && git commit -m "refactor(daily_report): use DeliveryReporter for unified tracking"
```

---

## Task 10: Refactor baltic_ingestion.py to use DeliveryReporter

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py` (lines 262-283)

- [ ] **Step 1: Add import**

Add near top of `execution/scripts/baltic_ingestion.py`:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact
```

- [ ] **Step 2: Replace send loop**

Locate the block starting at `# 5. Send WhatsApp` (around line 262). The current loop iterates contacts and calls `uazapi.send_message`.

Replace the loop with:
```python
    # 5. Send WhatsApp via DeliveryReporter
    message = format_whatsapp_message(data)

    if args.dry_run:
        print("\n--- WHATSAPP PREVIEW ---\n")
        print(message)
        return

    uazapi = UazapiClient()

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

    reporter = DeliveryReporter(
        workflow="baltic",
        send_fn=uazapi.send_message,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(delivery_contacts, message)
    logger.info(
        f"Baltic broadcast complete. Sent: {report.success_count}, "
        f"Failed: {report.failure_count}"
    )
```

- [ ] **Step 3: Verify imports**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -c "from execution.scripts import baltic_ingestion"
```
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/baltic_ingestion.py && git commit -m "refactor(baltic): use DeliveryReporter for unified tracking"
```

---

## Task 11: Refactor send_news.py to use DeliveryReporter

**Files:**
- Modify: `execution/scripts/send_news.py` (lines 50-78)

- [ ] **Step 1: Add import**

Add near top of `execution/scripts/send_news.py`:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact
```

- [ ] **Step 2: Replace send loop**

Locate the block after `# 2. Send` comment (line 49) which contains the contact iteration.

Replace with:
```python
    # 2. Send via DeliveryReporter
    uazapi = UazapiClient()

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

    if args.dry_run:
        logger.info(f"[DRY RUN] Would send to {len(delivery_contacts)} contacts")
        return

    import os
    reporter = DeliveryReporter(
        workflow="manual_news",
        send_fn=uazapi.send_message,
        gh_run_id=os.getenv("GITHUB_RUN_ID"),
    )
    report = reporter.dispatch(delivery_contacts, msg)
    logger.info(
        f"Manual news broadcast complete. Sent: {report.success_count}, "
        f"Failed: {report.failure_count}"
    )
```

- [ ] **Step 3: Verify imports**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -c "from execution.scripts import send_news"
```
Expected: No errors.

- [ ] **Step 4: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/scripts/send_news.py && git commit -m "refactor(send_news): use DeliveryReporter for unified tracking"
```

---

## Task 12: Refactor webhook process_approval_async to use DeliveryReporter

**Files:**
- Modify: `webhook/app.py` (lines 768-820)

This is the most complex refactor — must preserve the intermediate progress updates in Telegram.

- [ ] **Step 1: Add import at top of `webhook/app.py`**

Below existing imports:
```python
from execution.core.delivery_reporter import DeliveryReporter, Contact
```

- [ ] **Step 2: Create a wrapper for `send_whatsapp` that raises on failure**

The existing `send_whatsapp` returns `bool`. DeliveryReporter expects `send_fn` to raise on failure. Add this helper in `webhook/app.py` just above `process_approval_async` (around line 767):

```python
def _send_whatsapp_raising(phone, text, token=None, url=None):
    """Raising wrapper around send_whatsapp for DeliveryReporter contract."""
    use_token = token or UAZAPI_TOKEN
    use_url = url or UAZAPI_URL
    headers = {"token": use_token, "Content-Type": "application/json"}
    payload = {"number": str(phone), "text": text}
    response = requests.post(
        f"{use_url}/send/text",
        json=payload,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 3: Replace `process_approval_async` body**

Replace the entire function body (after the `def process_approval_async(...)` line) with:

```python
def process_approval_async(chat_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Process WhatsApp sending with progress updates via DeliveryReporter."""
    progress = send_telegram_message(chat_id, "⏳ Iniciando envio para WhatsApp...")
    progress_msg_id = progress.get("result", {}).get("message_id") if progress.get("ok") else None

    try:
        raw_contacts = get_contacts()
        total = len(raw_contacts)

        def build_contact(c):
            raw_phone = c.get("Evolution-api") or c.get("Telefone")
            if not raw_phone:
                return None
            phone = str(raw_phone).replace("whatsapp:", "").strip()
            name = c.get("Nome") or "—"
            return Contact(name=name, phone=phone)

        delivery_contacts = [bc for c in raw_contacts if (bc := build_contact(c))]

        if progress_msg_id:
            edit_message(chat_id, progress_msg_id,
                f"⏳ Enviando para {len(delivery_contacts)} contatos...\n0/{len(delivery_contacts)}")

        def on_progress(processed, total_, result):
            if progress_msg_id and processed % 10 == 0:
                edit_message(
                    chat_id,
                    progress_msg_id,
                    f"⏳ Enviando...\n{processed}/{total_} processados",
                )

        def send_fn(phone, text):
            _send_whatsapp_raising(phone, text, token=uazapi_token, url=uazapi_url)

        reporter = DeliveryReporter(
            workflow="webhook_approval",
            send_fn=send_fn,
            telegram_chat_id=chat_id,   # direct to the user who approved
            gh_run_id=None,              # webhook has no GH run
        )
        report = reporter.dispatch(delivery_contacts, draft_message, on_progress=on_progress)

        # Clean up the progress message (DeliveryReporter already sent final summary)
        if progress_msg_id:
            edit_message(
                chat_id,
                progress_msg_id,
                f"✔️ Envio finalizado — veja resumo detalhado abaixo.",
            )

        logger.info(
            f"Approval complete: {report.success_count} sent, {report.failure_count} failed"
        )

    except Exception as e:
        logger.error(f"Approval processing error: {e}")
        error_text = f"❌ ERRO NO ENVIO\n\n{str(e)}"
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, error_text)
        else:
            send_telegram_message(chat_id, error_text)
```

- [ ] **Step 4: Verify webhook imports cleanly**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -c "import webhook.app"
```
Expected: No import errors (env vars may warn but not crash).

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add webhook/app.py && git commit -m "refactor(webhook): use DeliveryReporter in process_approval_async"
```

---

## Task 13: Dashboard — /api/delivery-report endpoint

**Files:**
- Create: `dashboard/app/api/delivery-report/route.ts`

- [ ] **Step 1: Create the route file**

Create `dashboard/app/api/delivery-report/route.ts`:

```typescript
import { NextResponse } from "next/server";
import { Octokit } from "octokit";

type DeliveryResult = {
  name: string;
  phone: string;
  success: boolean;
  error: string | null;
  duration_ms: number;
};

type DeliveryReportPayload = {
  workflow: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  summary: { total: number; success: number; failure: number };
  results: DeliveryResult[];
};

const MARKER_START = "<<<DELIVERY_REPORT_START>>>";
const MARKER_END = "<<<DELIVERY_REPORT_END>>>";

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const run_id = searchParams.get("run_id");

  const token = process.env.GITHUB_TOKEN;
  const owner = "bigodinhc";
  const repo = "workflows_minerals";

  if (!token) return NextResponse.json({ error: "Missing Token" }, { status: 500 });
  if (!run_id) return NextResponse.json({ error: "Missing run_id" }, { status: 400 });

  const octokit = new Octokit({ auth: token });

  try {
    const { data: jobs } = await octokit.request(
      "GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
      { owner, repo, run_id: Number(run_id) }
    );
    const job = jobs.jobs[0];
    if (!job) {
      return NextResponse.json({ found: false, report: null });
    }
    const response = await octokit.request(
      "GET /repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
      { owner, repo, job_id: job.id, mediaType: { format: "raw" } }
    );
    const logText = String(response.data);

    const startIdx = logText.indexOf(MARKER_START);
    const endIdx = logText.indexOf(MARKER_END);
    if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
      return NextResponse.json({ found: false, report: null });
    }

    const jsonBlock = logText
      .slice(startIdx + MARKER_START.length, endIdx)
      .trim();
    // GH Actions prefixes each line with timestamps. Strip them.
    const cleanedJson = jsonBlock
      .split("\n")
      .map((line) => line.replace(/^\S+\s+/, "")) // drop leading "2026-04-13T14:31:22.1234567Z "
      .join("\n");

    let parsed: DeliveryReportPayload;
    try {
      parsed = JSON.parse(cleanedJson);
    } catch (e) {
      console.error("Failed to parse delivery report JSON:", e);
      return NextResponse.json({ found: false, report: null });
    }

    return NextResponse.json({ found: true, report: parsed });
  } catch (error) {
    console.error("Delivery report fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch report" }, { status: 500 });
  }
}
```

- [ ] **Step 2: Verify Next.js build does not break**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF /dashboard" && npx next build --no-lint 2>&1 | tail -20
```
Expected: Build completes, `/api/delivery-report` appears in the route list.

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add dashboard/app/api/delivery-report/route.ts && git commit -m "feat(dashboard): add /api/delivery-report endpoint"
```

---

## Task 14: Dashboard — DeliveryReportView component

**Files:**
- Create: `dashboard/components/delivery/DeliveryReportView.tsx`

- [ ] **Step 1: Create the component**

Create `dashboard/components/delivery/DeliveryReportView.tsx`:

```typescript
"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, CheckCircle2, XCircle } from "lucide-react";

type DeliveryResult = {
  name: string;
  phone: string;
  success: boolean;
  error: string | null;
  duration_ms: number;
};

type DeliveryReport = {
  workflow: string;
  started_at: string;
  finished_at: string;
  duration_seconds: number;
  summary: { total: number; success: number; failure: number };
  results: DeliveryResult[];
};

export function DeliveryReportView({ report }: { report: DeliveryReport }) {
  const [showSuccesses, setShowSuccesses] = useState(false);
  const failures = report.results.filter((r) => !r.success);
  const successes = report.results.filter((r) => r.success);

  const failPct = report.summary.total
    ? (report.summary.failure / report.summary.total) * 100
    : 0;
  const statusEmoji = report.summary.failure === 0 ? "✅" : failPct > 50 ? "🚨" : "⚠️";
  const statusColor =
    report.summary.failure === 0
      ? "text-[#00FF41]"
      : failPct > 50
      ? "text-[#ff3333]"
      : "text-[#FFD700]";

  return (
    <div className="border border-[#1a1a1a] bg-[#0a0a0a] p-4 mb-4">
      <div className="flex items-center gap-2 mb-3">
        <span className={`text-lg ${statusColor}`}>{statusEmoji}</span>
        <p className="text-[11px] text-[#00FF41] uppercase tracking-[0.2em]">
          / DELIVERY REPORT
        </p>
        <div className="flex-1 h-px bg-[#1a1a1a]" />
        <span className="text-[10px] text-[#555] uppercase">
          {report.workflow.replace(/_/g, " ")}
        </span>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="border border-[#1a1a1a] bg-[#050505] p-3">
          <p className="text-[9px] text-[#555] uppercase">TOTAL</p>
          <p className="text-2xl text-white font-bold">{report.summary.total}</p>
        </div>
        <div className="border border-[#00FF41]/20 bg-[#050505] p-3">
          <p className="text-[9px] text-[#555] uppercase">OK</p>
          <p className="text-2xl text-[#00FF41] font-bold">{report.summary.success}</p>
        </div>
        <div
          className={`border ${
            report.summary.failure > 0 ? "border-[#ff3333]/30" : "border-[#1a1a1a]"
          } bg-[#050505] p-3`}
        >
          <p className="text-[9px] text-[#555] uppercase">FALHA</p>
          <p
            className={`text-2xl font-bold ${
              report.summary.failure > 0 ? "text-[#ff3333]" : "text-[#555]"
            }`}
          >
            {report.summary.failure}
          </p>
        </div>
      </div>

      {/* Failures */}
      {failures.length > 0 && (
        <div className="mb-4">
          <p className="text-[10px] text-[#ff3333] uppercase tracking-wider mb-2">
            / FALHAS ({failures.length})
          </p>
          <div className="border border-[#ff3333]/20 bg-[#050505]">
            {failures.map((r, i) => (
              <div
                key={i}
                className="grid grid-cols-12 gap-2 px-3 py-1.5 text-[11px] border-b border-[#1a1a1a] last:border-0"
              >
                <div className="col-span-4 text-white truncate">{r.name}</div>
                <div className="col-span-4 text-[#555] truncate">{r.phone}</div>
                <div className="col-span-4 text-[#ff3333] truncate">{r.error}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Successes (collapsible) */}
      {successes.length > 0 && (
        <div>
          <button
            onClick={() => setShowSuccesses((v) => !v)}
            className="flex items-center gap-1 text-[10px] text-[#00FF41]/70 uppercase tracking-wider mb-2 hover:text-[#00FF41]"
          >
            {showSuccesses ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
            / SUCESSOS ({successes.length})
          </button>
          {showSuccesses && (
            <div className="border border-[#00FF41]/20 bg-[#050505] max-h-60 overflow-y-auto">
              {successes.map((r, i) => (
                <div
                  key={i}
                  className="grid grid-cols-12 gap-2 px-3 py-1.5 text-[11px] border-b border-[#1a1a1a] last:border-0"
                >
                  <div className="col-span-6 text-white truncate">{r.name}</div>
                  <div className="col-span-4 text-[#555] truncate">{r.phone}</div>
                  <div className="col-span-2 text-[#00FF41] text-right">
                    {r.duration_ms}ms
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify it compiles**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF /dashboard" && npx tsc --noEmit 2>&1 | tail -20
```
Expected: No type errors in the new file.

- [ ] **Step 3: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add dashboard/components/delivery/DeliveryReportView.tsx && git commit -m "feat(dashboard): add DeliveryReportView component"
```

---

## Task 15: Dashboard — integrate DeliveryReportView + query-param auto-open

**Files:**
- Modify: `dashboard/app/page.tsx`

- [ ] **Step 1: Add imports and state**

In `dashboard/app/page.tsx`, update imports at top:
```typescript
import { Play, CheckCircle2, XCircle, Loader2, FileText, AlertTriangle, ChevronDown } from "lucide-react";
import useSWR from "swr";
import { formatDistanceToNow } from "date-fns";
import { ptBR } from "date-fns/locale";
import { useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import { DeliveryReportView } from "@/components/delivery/DeliveryReportView";
```

- [ ] **Step 2: Add state for delivery report**

Inside `Home()` component, after existing state declarations:
```typescript
const [deliveryReport, setDeliveryReport] = useState<any | null>(null);
const [showRawLog, setShowRawLog] = useState(false);
const searchParams = useSearchParams();
```

- [ ] **Step 3: Update `handleViewLogs` to fetch delivery report too**

Replace the `handleViewLogs` function with:
```typescript
const handleViewLogs = async (runId: number) => {
  setSelectedRunId(runId);
  setIsLoadingLogs(true);
  setLogContent(null);
  setDeliveryReport(null);
  setShowRawLog(false);

  try {
    // Fetch delivery report and raw log in parallel
    const [reportRes, logRes] = await Promise.all([
      fetch(`/api/delivery-report?run_id=${runId}`),
      fetch(`/api/logs?run_id=${runId}`),
    ]);
    const reportData = await reportRes.json();
    if (reportData.found && reportData.report) {
      setDeliveryReport(reportData.report);
    }
    const text = await logRes.text();
    setLogContent(text);
  } catch (e) {
    setLogContent("Failed to load logs.");
  } finally {
    setIsLoadingLogs(false);
  }
};
```

- [ ] **Step 4: Add effect to auto-open from query param**

Below the `handleViewLogs` function, add:
```typescript
useEffect(() => {
  const runIdParam = searchParams.get("run_id");
  if (runIdParam) {
    const runId = Number(runIdParam);
    if (!Number.isNaN(runId)) {
      handleViewLogs(runId);
    }
  }
}, [searchParams]);
```

- [ ] **Step 5: Update Sheet body to render delivery report + collapsible raw log**

Replace the existing `<div className="flex-1 overflow-hidden...">` block inside `<Sheet>` with:

```typescript
<div className="flex-1 overflow-hidden border border-[#1a1a1a] bg-[#050505] relative">
  {isLoadingLogs ? (
    <div className="absolute inset-0 flex items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin text-[#00FF41]" />
    </div>
  ) : (
    <ScrollArea className="h-full w-full p-4">
      {deliveryReport && <DeliveryReportView report={deliveryReport} />}

      <button
        onClick={() => setShowRawLog((v) => !v)}
        className="flex items-center gap-1 text-[10px] text-[#00FF41]/70 uppercase tracking-wider mb-2 hover:text-[#00FF41]"
      >
        <ChevronDown
          className={`h-3 w-3 transition-transform ${showRawLog ? "" : "-rotate-90"}`}
        />
        / RAW LOG
      </button>
      {showRawLog && (
        <pre className="whitespace-pre-wrap break-all text-[11px] text-[#00FF41]/80 font-mono">
          {logContent || "No log content."}
        </pre>
      )}
    </ScrollArea>
  )}
</div>
```

- [ ] **Step 6: Verify build**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF /dashboard" && npx tsc --noEmit 2>&1 | tail -20
```
Expected: No type errors.

- [ ] **Step 7: Manual smoke test (optional, local dev)**

Run dev server:
```bash
cd "/Users/bigode/Dev/Antigravity WF /dashboard" && npm run dev
```
Then visit `http://localhost:3000/?run_id=<recent_run_id>` — the modal should auto-open.

- [ ] **Step 8: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add dashboard/app/page.tsx && git commit -m "feat(dashboard): integrate DeliveryReportView + query-param auto-open"
```

---

## Task 16: Update Claude model to sonnet-4-6

**Files:**
- Modify: `execution/integrations/claude_client.py` (lines 52, 94)
- Modify: `webhook/app.py` (line 660)

- [ ] **Step 1: Update `claude_client.py` line 52**

Change:
```python
            model="claude-sonnet-4-20250514",
```
To:
```python
            model="claude-sonnet-4-6",
```

- [ ] **Step 2: Update `claude_client.py` line 94**

Change:
```python
            model="claude-3-haiku-20240307",
```
To:
```python
            model="claude-sonnet-4-6",
```

- [ ] **Step 3: Update `webhook/app.py` line 660**

Change:
```python
            model="claude-sonnet-4-20250514",
```
To:
```python
            model="claude-sonnet-4-6",
```

- [ ] **Step 4: Verify no other stale references**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && grep -rn "claude-3-haiku\|claude-sonnet-4-20250514" execution/ webhook/ --include="*.py"
```
Expected: No output (all references updated).

- [ ] **Step 5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/integrations/claude_client.py webhook/app.py && git commit -m "chore: update Claude model to sonnet-4-6 across all callers"
```

---

## Task 17: Final smoke test

**Files:** (none created)

- [ ] **Step 1: Run full test suite**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && pytest
```
Expected: All 24+ tests PASS.

- [ ] **Step 2: Dry-run morning_check**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && source .venv/bin/activate && python -m execution.scripts.morning_check --dry-run
```
Expected: Prints `[DRY RUN] Would send to N contacts`, does not actually send.

- [ ] **Step 3: Manual live test (optional, user confirms)**

On GitHub, trigger `morning_check.yml` via workflow_dispatch with `dry_run: "false"`.
Expected outcomes:
- Telegram receives a summary message with the right emoji + contact count
- Dashboard `/api/delivery-report?run_id=<id>` returns `found: true`
- Link in Telegram message opens dashboard with auto-scrolled DeliveryReportView

- [ ] **Step 4: No commit (smoke only)**

---

## Self-Review Notes

**Spec coverage check:**
- [x] Spec §4 (Architecture): Tasks 2-7 build the module
- [x] Spec §5 (API): Tasks 2, 3, 6 implement full signature
- [x] Spec §6 (stdout JSON): Task 4
- [x] Spec §7 (Telegram format): Task 5 covers all 3 variants + truncation + link
- [x] Spec §8 (Dashboard): Tasks 13, 14, 15
- [x] Spec §9 (Integration points): Tasks 8-12 cover all 5 callers
- [x] Spec §10 (Tests): Tasks 2-7 all include test-first TDD
- [x] Spec §11 (Claude model): Task 16
- [x] Spec §12 (Rollout): Task order matches

**Type consistency:**
- `Contact` used identically across tasks
- `DeliveryReport.failure_count` property used consistently
- `gh_run_id` passed in all 4 script refactors (tasks 8-11), `None` in webhook (task 12)
- Marker strings identical in Task 4 (emission) and Task 13 (parsing)

**No placeholders:** All steps contain concrete code, file paths, and commands.
