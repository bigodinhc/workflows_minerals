# Categorized WhatsApp Dispatch Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform Telegram failure alerts from generic strings like `"HTTP 503: True"` into categorized, actionable summaries (e.g., `"74× WhatsApp desconectado → AÇÃO: Reconecte QR em mineralstrading.uazapi.com"`). Add circuit breaker that aborts broadcasts early when the first N sends all fail with the same fatal category.

**Architecture:** Introduce `SendErrorCategory` enum in `execution/core/delivery_reporter.py` plus a `classify_error(exc)` function that maps exceptions to `(category, reason)` tuples. Extend `DeliveryResult` with a `category` field populated by `DeliveryReporter.dispatch`. Rewrite `_format_telegram_message` to group failures by category with PT-BR action hints. Add a circuit breaker inside `dispatch()` that skips remaining contacts when the first N failures are all in the same "fatal" category (disconnected/auth). Emit Sentry tags per category so dashboards group naturally.

**Tech Stack:** Python 3.10, pytest 8.4, `sentry_sdk` (already initialized by `execution/core/sentry_init.py`), `requests` (for HTTPError typing). No new dependencies.

**Out of scope (separate sessions):** (a) Unifying `execution/integrations/uazapi_client.py` with `webhook/dispatch.py`; (b) Prometheus metrics pushgateway for cron; (c) Grafana Cloud setup; (d) CLAUDE.md audit.

**Files touched:**
- Modify: `execution/core/delivery_reporter.py` (single file, entire change surface)
- Modify: `tests/test_delivery_reporter.py` (extend; update 2 existing tests in Task 4)

---

## Task 1: Fix `_categorize_error` when UazAPI returns boolean `error` field

**Why:** The linha 73 bug — `parsed.get("error") or parsed.get("message")` picks the boolean `True`, stringifies to `"True"`, returns `"HTTP 503: True"`. This is the immediate incident root-cause for today's failure.

**Files:**
- Modify: `execution/core/delivery_reporter.py:58-79`
- Test: `tests/test_delivery_reporter.py` (append)

- [ ] **Step 1.1: Write the failing test**

Append to `tests/test_delivery_reporter.py`:

```python
from unittest.mock import MagicMock
import requests


def _mock_http_error(status: int, body: str) -> requests.HTTPError:
    """Build a requests.HTTPError with a fake response for testing _categorize_error."""
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.text = body
    exc = requests.HTTPError(f"{status} Server Error", response=response)
    return exc


def test_categorize_error_prefers_message_over_boolean_error_field():
    """UazAPI returns {"error": true, "message": "WhatsApp disconnected"}.
    Must use 'message', not stringify the boolean 'error' to 'True'.
    """
    from execution.core.delivery_reporter import _categorize_error

    exc = _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')
    result = _categorize_error(exc)
    assert "WhatsApp disconnected" in result
    assert "True" not in result


def test_categorize_error_uses_error_field_when_it_is_a_string():
    """Some upstreams return {"error": "rate limited"} — string, not bool. Keep using it."""
    from execution.core.delivery_reporter import _categorize_error

    exc = _mock_http_error(429, '{"error":"rate limited"}')
    result = _categorize_error(exc)
    assert "rate limited" in result
```

- [ ] **Step 1.2: Run the tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py::test_categorize_error_prefers_message_over_boolean_error_field tests/test_delivery_reporter.py::test_categorize_error_uses_error_field_when_it_is_a_string -v`

Expected: First test FAILS (result contains `"True"`). Second test PASSES (existing behavior already handles string case).

- [ ] **Step 1.3: Apply the minimal fix**

In `execution/core/delivery_reporter.py`, replace lines 72-75:

```python
            if isinstance(parsed, dict):
                reason = parsed.get("error") or parsed.get("message")
                if reason:
                    return f"HTTP {status}: {str(reason)[:120]}"
```

with:

```python
            if isinstance(parsed, dict):
                # UazAPI returns {"error": true, "message": "..."} — boolean 'error'
                # is not a useful reason. Prefer 'message' when 'error' is a bool.
                raw_error = parsed.get("error")
                if isinstance(raw_error, bool):
                    reason = parsed.get("message")
                else:
                    reason = raw_error or parsed.get("message")
                if reason:
                    return f"HTTP {status}: {str(reason)[:120]}"
```

- [ ] **Step 1.4: Run the tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v`

Expected: All tests PASS (including the two new ones and every pre-existing test in the file).

- [ ] **Step 1.5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "fix(delivery): prefer 'message' over boolean 'error' in UazAPI responses

UazAPI returns {\"error\": true, \"message\": \"WhatsApp disconnected\"}.
Previous categorizer picked the boolean and rendered it as the literal
string \"True\", producing unhelpful alerts like \"HTTP 503: True\"."
```

---

## Task 2: Introduce `SendErrorCategory` enum and `classify_error`

**Why:** String-matching error messages is fragile. A typed category is the contract between sender and alert formatter/Sentry/circuit breaker.

**Files:**
- Modify: `execution/core/delivery_reporter.py` (add enum + function)
- Test: `tests/test_delivery_reporter.py` (append)

- [ ] **Step 2.1: Write the failing tests**

Append to `tests/test_delivery_reporter.py`:

```python
def test_send_error_category_is_enum():
    from execution.core.delivery_reporter import SendErrorCategory
    assert SendErrorCategory.WHATSAPP_DISCONNECTED.value == "whatsapp_disconnected"
    assert SendErrorCategory.RATE_LIMIT.value == "rate_limit"
    assert SendErrorCategory.INVALID_NUMBER.value == "invalid_number"
    assert SendErrorCategory.UPSTREAM_5XX.value == "upstream_5xx"
    assert SendErrorCategory.AUTH.value == "auth"
    assert SendErrorCategory.TIMEOUT.value == "timeout"
    assert SendErrorCategory.NETWORK.value == "network"
    assert SendErrorCategory.UNKNOWN.value == "unknown"


def test_classify_error_whatsapp_disconnected():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')
    category, reason = classify_error(exc)
    assert category == SendErrorCategory.WHATSAPP_DISCONNECTED
    assert "WhatsApp disconnected" in reason


def test_classify_error_rate_limit_429():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(429, '{"error":"rate limited"}')
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.RATE_LIMIT


def test_classify_error_invalid_number_400():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(400, '{"error":"number not registered on whatsapp"}')
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.INVALID_NUMBER


def test_classify_error_auth_401():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(401, '{"error":"invalid token"}')
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.AUTH


def test_classify_error_generic_upstream_500():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = _mock_http_error(500, '{"error":"internal server error"}')
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.UPSTREAM_5XX


def test_classify_error_timeout():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = requests.Timeout("read timed out")
    category, reason = classify_error(exc)
    assert category == SendErrorCategory.TIMEOUT
    assert "timeout" in reason.lower()


def test_classify_error_connection_error_is_network():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    exc = requests.ConnectionError("connection refused")
    category, _ = classify_error(exc)
    assert category == SendErrorCategory.NETWORK


def test_classify_error_unknown_exception():
    from execution.core.delivery_reporter import classify_error, SendErrorCategory

    category, reason = classify_error(ValueError("weird"))
    assert category == SendErrorCategory.UNKNOWN
    assert "weird" in reason
```

- [ ] **Step 2.2: Run the tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -k classify_error -v`

Expected: All 8 new tests FAIL with `ImportError: cannot import name 'SendErrorCategory'` or similar.

- [ ] **Step 2.3: Implement the enum and classifier**

In `execution/core/delivery_reporter.py`, immediately after the existing imports and before `_categorize_error`, add:

```python
from enum import Enum


class SendErrorCategory(Enum):
    """Categories of send failures. Used for alert grouping, action hints,
    circuit breaker decisions, and Sentry tagging."""
    WHATSAPP_DISCONNECTED = "whatsapp_disconnected"
    RATE_LIMIT = "rate_limit"
    INVALID_NUMBER = "invalid_number"
    UPSTREAM_5XX = "upstream_5xx"
    AUTH = "auth"
    TIMEOUT = "timeout"
    NETWORK = "network"
    UNKNOWN = "unknown"


def classify_error(exc: Exception) -> tuple["SendErrorCategory", str]:
    """Classify an exception raised by a WhatsApp send into (category, reason).

    The reason is a short, human-readable string suitable for the Telegram alert.
    The category drives action hints, grouping, and circuit breaker behavior.
    """
    import requests as _rq
    import json as _json

    if isinstance(exc, _rq.Timeout):
        return SendErrorCategory.TIMEOUT, "timeout"

    if isinstance(exc, _rq.ConnectionError):
        return SendErrorCategory.NETWORK, str(exc)[:120]

    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        body = exc.response.text or ""

        # Try to extract a human-readable reason from the JSON body
        reason_str = ""
        try:
            parsed = _json.loads(body)
            if isinstance(parsed, dict):
                raw_error = parsed.get("error")
                # UazAPI returns {"error": true, "message": "..."} — prefer message
                candidate = parsed.get("message") if isinstance(raw_error, bool) else raw_error
                reason_str = str(candidate or parsed.get("message") or "")[:120]
        except (ValueError, TypeError):
            reason_str = body[:100]

        reason_lower = reason_str.lower()

        # Category decision tree
        if status == 401 or status == 403:
            return SendErrorCategory.AUTH, reason_str or f"HTTP {status}"
        if status == 429 or "rate" in reason_lower and "limit" in reason_lower:
            return SendErrorCategory.RATE_LIMIT, reason_str or f"HTTP {status}"
        if "disconnected" in reason_lower or "not connected" in reason_lower:
            return SendErrorCategory.WHATSAPP_DISCONNECTED, reason_str
        if status == 400 and ("not registered" in reason_lower or "invalid number" in reason_lower or "not on whatsapp" in reason_lower):
            return SendErrorCategory.INVALID_NUMBER, reason_str
        if 500 <= status < 600:
            return SendErrorCategory.UPSTREAM_5XX, reason_str or f"HTTP {status}"

        return SendErrorCategory.UNKNOWN, reason_str or f"HTTP {status}"

    return SendErrorCategory.UNKNOWN, str(exc)[:200]
```

- [ ] **Step 2.4: Run the tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -k classify_error -v`

Expected: All 8 new tests PASS.

Also run the full file to be sure nothing broke: `python -m pytest tests/test_delivery_reporter.py -v`

Expected: Every test PASSES.

- [ ] **Step 2.5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "feat(delivery): introduce SendErrorCategory enum and classify_error

Typed category for every send failure. Used next by alert formatter,
circuit breaker, and Sentry tagging. Keeps legacy _categorize_error
untouched so existing callers still work."
```

---

## Task 3: Extend `DeliveryResult` with `category` field and populate in `dispatch`

**Why:** Telegram formatter, circuit breaker, and Sentry all need the category attached to each failure, not re-derived later.

**Files:**
- Modify: `execution/core/delivery_reporter.py` (dataclass + dispatch loop)
- Test: `tests/test_delivery_reporter.py` (append)

- [ ] **Step 3.1: Write the failing test**

Append to `tests/test_delivery_reporter.py`:

```python
def test_delivery_result_has_category_field():
    from execution.core.delivery_reporter import DeliveryResult, SendErrorCategory
    c = Contact(name="X", phone="1")
    r = DeliveryResult(
        contact=c,
        success=False,
        error="HTTP 503: WhatsApp disconnected",
        duration_ms=100,
        category=SendErrorCategory.WHATSAPP_DISCONNECTED,
    )
    assert r.category == SendErrorCategory.WHATSAPP_DISCONNECTED


def test_delivery_result_category_defaults_to_unknown():
    from execution.core.delivery_reporter import DeliveryResult, SendErrorCategory
    c = Contact(name="X", phone="1")
    r = DeliveryResult(contact=c, success=True, error=None, duration_ms=100)
    assert r.category == SendErrorCategory.UNKNOWN


def test_dispatch_populates_category_on_http_failure():
    from execution.core.delivery_reporter import DeliveryReporter, SendErrorCategory

    def send_fn(phone, text):
        raise _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name="A", phone="1")]
    report = reporter.dispatch(contacts, message="hi")
    assert report.failures[0].category == SendErrorCategory.WHATSAPP_DISCONNECTED
```

- [ ] **Step 3.2: Run the tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py::test_delivery_result_has_category_field tests/test_delivery_reporter.py::test_delivery_result_category_defaults_to_unknown tests/test_delivery_reporter.py::test_dispatch_populates_category_on_http_failure -v`

Expected: FAIL — `DeliveryResult.__init__() got an unexpected keyword argument 'category'`.

- [ ] **Step 3.3: Update `DeliveryResult` and `dispatch`**

In `execution/core/delivery_reporter.py`, replace the `DeliveryResult` dataclass (currently lines 20-26):

```python
@dataclass
class DeliveryResult:
    """Result of a single delivery attempt."""
    contact: Contact
    success: bool
    error: Optional[str]
    duration_ms: int
```

with:

```python
@dataclass
class DeliveryResult:
    """Result of a single delivery attempt."""
    contact: Contact
    success: bool
    error: Optional[str]
    duration_ms: int
    category: "SendErrorCategory" = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.category is None:
            self.category = SendErrorCategory.UNKNOWN
```

Then in `DeliveryReporter.dispatch` (currently around lines 218-234), replace the inner try/except + result construction:

```python
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
```

with:

```python
            t0 = time.monotonic()
            success = False
            error: Optional[str] = None
            category: SendErrorCategory = SendErrorCategory.UNKNOWN
            try:
                self.send_fn(contact.phone, message)
                success = True
            except Exception as exc:
                category, reason = classify_error(exc)
                error = _categorize_error(exc)  # keep legacy string for stdout JSON + dashboard compat
                _ = reason  # raw reason is already embedded in `error` via _categorize_error
            duration_ms = int((time.monotonic() - t0) * 1000)

            result = DeliveryResult(
                contact=contact,
                success=success,
                error=error,
                duration_ms=duration_ms,
                category=category,
            )
```

- [ ] **Step 3.4: Run the tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v`

Expected: All tests PASS. Pay attention to existing tests that build `DeliveryResult` — they should still work because `category` has a default.

- [ ] **Step 3.5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "feat(delivery): attach SendErrorCategory to each DeliveryResult

dispatch() now runs classify_error on every exception and stores the
category on the result. Legacy _categorize_error string is kept for
stdout JSON/dashboard compatibility. Circuit breaker and formatter
will use the new category field in follow-up commits."
```

---

## Task 4: Group failures by category in Telegram message with action hints

**Why:** The user asked: "quero ter nos avisos qual tipo de falha foi". Today's incident dumped 74 identical rows. The new format collapses them into `"74× WhatsApp desconectado → AÇÃO: Reconecte QR em mineralstrading.uazapi.com"`.

**Files:**
- Modify: `execution/core/delivery_reporter.py:82-143` (rewrite `_format_telegram_message`, add `_action_hint`)
- Test: `tests/test_delivery_reporter.py` (append new tests; **update** two existing tests — see Step 4.3)

- [ ] **Step 4.1: Write the failing tests**

Append to `tests/test_delivery_reporter.py`:

```python
def test_telegram_message_groups_homogeneous_failures():
    """74 identical WhatsApp-disconnected failures → one grouped line + action hint."""
    from execution.core.delivery_reporter import (
        DeliveryReport, DeliveryResult, SendErrorCategory, _format_telegram_message,
    )
    results = [
        DeliveryResult(
            contact=Contact(name=f"U{i}", phone=str(i)),
            success=False,
            error="HTTP 503: WhatsApp disconnected",
            duration_ms=100,
            category=SendErrorCategory.WHATSAPP_DISCONNECTED,
        )
        for i in range(74)
    ]
    report = _make_report("daily_report", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)

    assert "74× WhatsApp desconectado" in msg
    assert "Reconecte QR" in msg
    # Must NOT list every individual contact
    assert "U0 " not in msg and "U73 " not in msg


def test_telegram_message_groups_heterogeneous_failures():
    """Mix of categories → one line per category, sorted by count descending."""
    from execution.core.delivery_reporter import (
        DeliveryReport, DeliveryResult, SendErrorCategory, _format_telegram_message,
    )
    results = []
    for i in range(40):
        results.append(DeliveryResult(
            contact=Contact(name=f"N{i}", phone=str(i)), success=False,
            error="HTTP 400: number not registered", duration_ms=100,
            category=SendErrorCategory.INVALID_NUMBER,
        ))
    for i in range(20):
        results.append(DeliveryResult(
            contact=Contact(name=f"R{i}", phone=str(100+i)), success=False,
            error="HTTP 429: rate limited", duration_ms=100,
            category=SendErrorCategory.RATE_LIMIT,
        ))
    for i in range(14):
        results.append(DeliveryResult(
            contact=Contact(name=f"T{i}", phone=str(200+i)), success=False,
            error="timeout", duration_ms=100,
            category=SendErrorCategory.TIMEOUT,
        ))

    report = _make_report("daily_report", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)

    assert "40× Número inválido" in msg
    assert "20× Rate limit" in msg
    assert "14× Timeout" in msg
    # 40 must appear before 20 (sorted descending)
    assert msg.index("40×") < msg.index("20×") < msg.index("14×")


def test_telegram_message_partial_failure_still_groups():
    """Even with some successes, failures still grouped by category."""
    from execution.core.delivery_reporter import (
        DeliveryResult, SendErrorCategory, _format_telegram_message,
    )
    results = [
        DeliveryResult(contact=Contact(name="OK", phone="1"), success=True, error=None, duration_ms=100),
        DeliveryResult(contact=Contact(name="F1", phone="2"), success=False, error="timeout",
                       duration_ms=100, category=SendErrorCategory.TIMEOUT),
        DeliveryResult(contact=Contact(name="F2", phone="3"), success=False, error="timeout",
                       duration_ms=100, category=SendErrorCategory.TIMEOUT),
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "2× Timeout" in msg
```

- [ ] **Step 4.2: Run the tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -k "groups_homogeneous or groups_heterogeneous or partial_failure_still_groups" -v`

Expected: All 3 FAIL because current format produces `"• U0 (0) — HTTP 503: WhatsApp disconnected"` per contact, not a grouped line.

- [ ] **Step 4.3: Update two existing tests that will break**

In `tests/test_delivery_reporter.py`, replace `test_telegram_message_with_failures` (around line 228):

```python
def test_telegram_message_with_failures():
    from execution.core.delivery_reporter import SendErrorCategory
    results = [
        DeliveryResult(contact=Contact(name="A", phone="111"), success=True, error=None, duration_ms=100),
        DeliveryResult(contact=Contact(name="Carlos", phone="222"), success=False, error="timeout",
                       duration_ms=30000, category=SendErrorCategory.TIMEOUT),
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "⚠️" in msg
    assert "1× Timeout" in msg  # was: "Carlos" / "222" / "timeout"
```

Replace `test_telegram_message_truncates_long_failure_list` (around line 252) — it's no longer applicable because we group instead of truncate. Replace with:

```python
def test_telegram_message_shows_sample_contacts_per_category_when_few():
    """For categories with ≤3 failures, show the contact names inline."""
    from execution.core.delivery_reporter import DeliveryResult, SendErrorCategory
    results = [
        DeliveryResult(contact=Contact(name="Ana", phone="1"), success=False, error="timeout",
                       duration_ms=100, category=SendErrorCategory.TIMEOUT),
        DeliveryResult(contact=Contact(name="Bruno", phone="2"), success=False, error="timeout",
                       duration_ms=100, category=SendErrorCategory.TIMEOUT),
    ]
    report = _make_report("test", results)
    msg = _format_telegram_message(report, dashboard_base_url="https://dash", gh_run_id=None)
    assert "Ana" in msg
    assert "Bruno" in msg
```

- [ ] **Step 4.4: Rewrite `_format_telegram_message` and add `_action_hint`**

In `execution/core/delivery_reporter.py`, replace lines 82-143 (everything from `_MAX_FAILURES_LISTED = 15` through the end of `_format_telegram_message`) with:

```python
# Human-readable PT labels per category (shown in the grouped summary)
_CATEGORY_LABEL = {
    SendErrorCategory.WHATSAPP_DISCONNECTED: "WhatsApp desconectado",
    SendErrorCategory.RATE_LIMIT: "Rate limit",
    SendErrorCategory.INVALID_NUMBER: "Número inválido",
    SendErrorCategory.UPSTREAM_5XX: "Erro UazAPI (5xx)",
    SendErrorCategory.AUTH: "Falha de autenticação",
    SendErrorCategory.TIMEOUT: "Timeout",
    SendErrorCategory.NETWORK: "Erro de rede",
    SendErrorCategory.UNKNOWN: "Erro não categorizado",
}

# Action hint per category. None means no hint (transient, no operator action).
_CATEGORY_HINT = {
    SendErrorCategory.WHATSAPP_DISCONNECTED: "Reconecte QR em mineralstrading.uazapi.com",
    SendErrorCategory.AUTH: "Verifique UAZAPI_TOKEN no secrets do GitHub",
    SendErrorCategory.INVALID_NUMBER: "Revise a planilha de contatos",
    SendErrorCategory.UPSTREAM_5XX: "Verifique status do UazAPI",
    SendErrorCategory.RATE_LIMIT: None,
    SendErrorCategory.TIMEOUT: None,
    SendErrorCategory.NETWORK: None,
    SendErrorCategory.UNKNOWN: "Veja logs do GitHub Actions",
}

# Per-category how many sample contact names to show inline (0 = none, show count only)
_CATEGORY_SAMPLE_LIMIT = 3


def _group_failures_by_category(failures: list) -> list:
    """Return list of (category, results) tuples sorted by count descending."""
    from collections import defaultdict
    buckets: dict = defaultdict(list)
    for f in failures:
        buckets[f.category].append(f)
    return sorted(buckets.items(), key=lambda kv: -len(kv[1]))


def _format_telegram_message(
    report: "DeliveryReport",
    dashboard_base_url: str,
    gh_run_id: Optional[str],
) -> str:
    """Build Telegram-ready text summary of a DeliveryReport.

    Failures are grouped by SendErrorCategory. Each group shows count,
    PT-BR label, optional action hint, and up to 3 sample contact names.
    """
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
    else:
        lines.append("❌ FALHAS POR TIPO:")
        for category, failures in _group_failures_by_category(report.failures):
            label = _CATEGORY_LABEL.get(category, category.value)
            hint = _CATEGORY_HINT.get(category)
            count = len(failures)
            lines.append(f"• {count}× {label}")
            if count <= _CATEGORY_SAMPLE_LIMIT:
                names = ", ".join(f.contact.name for f in failures)
                lines.append(f"  ({names})")
            if hint:
                lines.append(f"  → AÇÃO: {hint}")

    link = (
        f"{dashboard_base_url}/?run_id={gh_run_id}"
        if gh_run_id
        else f"{dashboard_base_url}/"
    )
    lines.append("")
    lines.append(f"[Ver no dashboard]({link})")

    return "\n".join(lines)
```

Note: the old `_MAX_FAILURES_LISTED = 15` constant is **removed** — grouping supersedes truncation.

- [ ] **Step 4.5: Run the tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v`

Expected: All tests PASS — including the new grouping tests and the updated `test_telegram_message_with_failures` / `test_telegram_message_shows_sample_contacts_per_category_when_few`.

- [ ] **Step 4.6: Manual visual check**

Run this one-liner to eyeball the new format with today's incident data:

```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -c "
from datetime import datetime, timezone
from execution.core.delivery_reporter import (
    Contact, DeliveryResult, DeliveryReport, SendErrorCategory, _format_telegram_message,
)
results = [
    DeliveryResult(
        contact=Contact(name=f'U{i}', phone=f'55{i:011d}'),
        success=False,
        error='HTTP 503: WhatsApp disconnected',
        duration_ms=13000,
        category=SendErrorCategory.WHATSAPP_DISCONNECTED,
    )
    for i in range(74)
]
now = datetime.now(timezone.utc)
report = DeliveryReport(workflow='daily_report', started_at=now, finished_at=now, results=results)
print(_format_telegram_message(report, dashboard_base_url='https://workflows-minerals.vercel.app', gh_run_id='999'))
"
```

Expected output should look roughly like:

```
🚨 DAILY REPORT — FALHA TOTAL
21/04/2026 15:34 (0s)

📊 Total: 74 | OK: 0 | Falha: 74

❌ FALHAS POR TIPO:
• 74× WhatsApp desconectado
  → AÇÃO: Reconecte QR em mineralstrading.uazapi.com

[Ver no dashboard](https://workflows-minerals.vercel.app/?run_id=999)
```

- [ ] **Step 4.7: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "feat(delivery): group Telegram failure alerts by error category

Instead of 74 identical rows of 'HTTP 503: True', failures are now
summarized as '74× WhatsApp desconectado → AÇÃO: Reconecte QR ...'.
Heterogeneous failures show one line per category sorted by count
descending. Small buckets (≤3) include contact names inline."
```

---

## Task 5: Circuit breaker — abort batch on N consecutive fatal failures of the same category

**Why:** Today's run burned 16 minutes retrying 74 contacts when the first HTTP 503 already told the full story. Skip the rest, save CI minutes, single Telegram alert.

**Files:**
- Modify: `execution/core/delivery_reporter.py` (extend `DeliveryReporter.__init__` + `dispatch`)
- Test: `tests/test_delivery_reporter.py` (append)

- [ ] **Step 5.1: Write the failing tests**

Append to `tests/test_delivery_reporter.py`:

```python
def test_circuit_breaker_trips_after_5_disconnected():
    """5 consecutive WhatsApp-disconnected failures → remaining contacts skipped."""
    from execution.core.delivery_reporter import DeliveryReporter, SendErrorCategory

    call_count = {"n": 0}
    def send_fn(phone, text):
        call_count["n"] += 1
        raise _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"U{i}", phone=str(i)) for i in range(20)]
    report = reporter.dispatch(contacts, message="hi")

    # Circuit should trip after 5, remaining 15 are skipped
    assert call_count["n"] == 5
    assert report.failure_count == 20  # all 20 are counted as failures
    skipped = [r for r in report.results if r.category == SendErrorCategory.UNKNOWN and r.error == "skipped_due_to_circuit_break"]
    assert len(skipped) == 15


def test_circuit_breaker_does_not_trip_on_transient_timeout():
    """5 consecutive timeouts → continues (timeout is transient, not fatal)."""
    from execution.core.delivery_reporter import DeliveryReporter

    call_count = {"n": 0}
    def send_fn(phone, text):
        call_count["n"] += 1
        raise requests.Timeout("read timed out")

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"U{i}", phone=str(i)) for i in range(10)]
    report = reporter.dispatch(contacts, message="hi")

    assert call_count["n"] == 10  # every contact attempted


def test_circuit_breaker_resets_on_success():
    """4 disconnected, 1 success, 4 more disconnected → circuit does NOT trip
    because success resets the streak. All 9 attempted."""
    from execution.core.delivery_reporter import DeliveryReporter

    call_count = {"n": 0}
    def send_fn(phone, text):
        call_count["n"] += 1
        if call_count["n"] == 5:
            return  # success on 5th
        raise _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"U{i}", phone=str(i)) for i in range(9)]
    report = reporter.dispatch(contacts, message="hi")

    assert call_count["n"] == 9


def test_circuit_breaker_requires_same_category_streak():
    """4 disconnected + 1 auth + 1 disconnected → different-category break resets streak.
    All 6 attempted."""
    from execution.core.delivery_reporter import DeliveryReporter

    call_count = {"n": 0}
    def send_fn(phone, text):
        call_count["n"] += 1
        if call_count["n"] == 5:
            raise _mock_http_error(401, '{"error":"invalid token"}')
        raise _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"U{i}", phone=str(i)) for i in range(6)]
    report = reporter.dispatch(contacts, message="hi")

    assert call_count["n"] == 6
```

- [ ] **Step 5.2: Run the tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -k circuit_breaker -v`

Expected: All 4 tests FAIL (circuit breaker logic not yet implemented).

- [ ] **Step 5.3: Implement the circuit breaker**

In `execution/core/delivery_reporter.py`, add a module-level constant near the `_CATEGORY_*` dicts:

```python
# Categories considered "fatal" — N consecutive failures in the same one triggers abort.
# Transient categories (timeout, network) do NOT trip the breaker.
_FATAL_CATEGORIES = frozenset({
    SendErrorCategory.WHATSAPP_DISCONNECTED,
    SendErrorCategory.AUTH,
    SendErrorCategory.UPSTREAM_5XX,
})

_CIRCUIT_BREAKER_THRESHOLD = 5
```

Update `DeliveryReporter.__init__` signature to accept overrides (append two kwargs before the closing paren):

```python
    def __init__(
        self,
        workflow: str,
        send_fn: Callable[[str, str], None],
        notify_telegram: bool = True,
        telegram_chat_id: Optional[str] = None,
        dashboard_base_url: str = "https://workflows-minerals.vercel.app",
        gh_run_id: Optional[str] = None,
        circuit_breaker_threshold: int = _CIRCUIT_BREAKER_THRESHOLD,
        fatal_categories: frozenset = _FATAL_CATEGORIES,
    ):
        self.workflow = workflow
        self.send_fn = send_fn
        self.notify_telegram = notify_telegram
        self.telegram_chat_id = telegram_chat_id
        self.dashboard_base_url = dashboard_base_url
        self.gh_run_id = gh_run_id
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.fatal_categories = fatal_categories
```

Replace the entire `dispatch` method's for-loop with the following logic that tracks a consecutive-streak counter and marks skipped contacts:

```python
    def dispatch(
        self,
        contacts: Iterable[Contact],
        message: str,
        on_progress: Optional[Callable[[int, int, DeliveryResult], None]] = None,
    ) -> DeliveryReport:
        """Send `message` to each contact. Never raises on send failure.

        Circuit breaker: when `circuit_breaker_threshold` consecutive failures
        all share the same category AND that category is in `fatal_categories`,
        the remaining contacts are skipped and marked with error
        'skipped_due_to_circuit_break'.
        """
        started_at = datetime.now().astimezone()
        results: list = []
        contacts_list = list(contacts)
        total = len(contacts_list)

        streak_category: Optional[SendErrorCategory] = None
        streak_count = 0
        circuit_tripped = False

        for i, contact in enumerate(contacts_list):
            if circuit_tripped:
                result = DeliveryResult(
                    contact=contact,
                    success=False,
                    error="skipped_due_to_circuit_break",
                    duration_ms=0,
                    category=SendErrorCategory.UNKNOWN,
                )
                results.append(result)
                if on_progress is not None:
                    try:
                        on_progress(i + 1, total, result)
                    except Exception:
                        pass
                continue

            t0 = time.monotonic()
            success = False
            error: Optional[str] = None
            category: SendErrorCategory = SendErrorCategory.UNKNOWN
            try:
                self.send_fn(contact.phone, message)
                success = True
            except Exception as exc:
                category, _reason = classify_error(exc)
                error = _categorize_error(exc)
            duration_ms = int((time.monotonic() - t0) * 1000)

            result = DeliveryResult(
                contact=contact,
                success=success,
                error=error,
                duration_ms=duration_ms,
                category=category,
            )
            results.append(result)

            # Circuit breaker bookkeeping
            if success:
                streak_category = None
                streak_count = 0
            else:
                if category == streak_category:
                    streak_count += 1
                else:
                    streak_category = category
                    streak_count = 1
                if (
                    streak_count >= self.circuit_breaker_threshold
                    and streak_category in self.fatal_categories
                ):
                    circuit_tripped = True

            if on_progress is not None:
                try:
                    on_progress(i + 1, total, result)
                except Exception:
                    pass

        finished_at = datetime.now().astimezone()
        report = DeliveryReport(
            workflow=self.workflow,
            started_at=started_at,
            finished_at=finished_at,
            results=results,
        )
        self._emit_stdout_report(report)
        if self.notify_telegram:
            self._send_telegram_summary(report)
        return report
```

- [ ] **Step 5.4: Run the tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v`

Expected: All tests PASS (4 new circuit-breaker tests + every prior test).

- [ ] **Step 5.5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "feat(delivery): circuit breaker aborts batch on 5 fatal failures

When 5 consecutive failures share the same fatal category (disconnected,
auth, upstream 5xx), remaining contacts are marked 'skipped_due_to_circuit_break'
and send_fn is not invoked for them. Transient categories (timeout,
network, rate_limit) never trip the breaker — the retry layer handles
those. Threshold and fatal set are configurable per DeliveryReporter
instance."
```

---

## Task 6: Sentry tagging per failure category

**Why:** Sentry groups errors by exception type + stack today — so 74 failures become 1 issue or a handful. Adding `send.error_category` tag means dashboards can filter by root cause, and alerts can fire specifically on `whatsapp_disconnected`.

**Files:**
- Modify: `execution/core/delivery_reporter.py` (add Sentry capture inside `dispatch`)
- Test: `tests/test_delivery_reporter.py` (append with monkeypatch)

- [ ] **Step 6.1: Write the failing test**

Append to `tests/test_delivery_reporter.py`:

```python
def test_dispatch_tags_sentry_with_error_category(monkeypatch):
    """Each failure should set Sentry tag 'send.error_category' with category value."""
    from execution.core.delivery_reporter import DeliveryReporter

    captured_tags = []

    class _FakeScope:
        def set_tag(self, key, value):
            captured_tags.append((key, value))

    from contextlib import contextmanager
    @contextmanager
    def _fake_push_scope():
        yield _FakeScope()

    import sys
    fake_sentry = type(sys)("sentry_sdk")
    fake_sentry.push_scope = _fake_push_scope
    fake_sentry.capture_exception = lambda exc: captured_tags.append(("__captured__", str(exc)[:30]))
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    def send_fn(phone, text):
        raise _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name="U", phone="1")]
    reporter.dispatch(contacts, message="hi")

    # Tag must be set AND exception captured
    tag_entries = [t for t in captured_tags if t[0] == "send.error_category"]
    assert ("send.error_category", "whatsapp_disconnected") in tag_entries
    assert any(t[0] == "__captured__" for t in captured_tags)


def test_dispatch_does_not_tag_sentry_on_success(monkeypatch):
    """Successful sends must not push Sentry tags or capture."""
    from execution.core.delivery_reporter import DeliveryReporter

    captured = []
    class _FakeScope:
        def set_tag(self, key, value):
            captured.append((key, value))
    from contextlib import contextmanager
    @contextmanager
    def _fake_push_scope():
        yield _FakeScope()
    import sys
    fake_sentry = type(sys)("sentry_sdk")
    fake_sentry.push_scope = _fake_push_scope
    fake_sentry.capture_exception = lambda exc: captured.append(("__captured__", "x"))
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    send_fn = MagicMock()  # no raise → success
    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    reporter.dispatch([Contact(name="U", phone="1")], message="hi")
    assert captured == []
```

- [ ] **Step 6.2: Run the tests to verify they fail**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -k "tags_sentry or does_not_tag" -v`

Expected: FAIL — no Sentry integration in dispatch yet.

- [ ] **Step 6.3: Add Sentry capture inside the exception handler**

In `execution/core/delivery_reporter.py`, inside `DeliveryReporter.dispatch`, locate the exception block:

```python
            except Exception as exc:
                category, _reason = classify_error(exc)
                error = _categorize_error(exc)
```

Replace with:

```python
            except Exception as exc:
                category, _reason = classify_error(exc)
                error = _categorize_error(exc)
                self._capture_sentry(exc, category)
```

Add this new method to the `DeliveryReporter` class (right after `_send_telegram_summary`, near the end of the file):

```python
    def _capture_sentry(self, exc: Exception, category: "SendErrorCategory") -> None:
        """Capture exception to Sentry with category as a searchable tag.
        Silent no-op if sentry_sdk is not importable or not initialized.
        """
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                scope.set_tag("send.error_category", category.value)
                scope.set_tag("workflow", self.workflow)
                sentry_sdk.capture_exception(exc)
        except Exception:
            pass  # never let telemetry failures break dispatch
```

- [ ] **Step 6.4: Run the tests to verify they pass**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v`

Expected: All tests PASS.

- [ ] **Step 6.5: Commit**

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "feat(delivery): tag Sentry exceptions with error category + workflow

Every capture_exception now carries 'send.error_category' (e.g.
whatsapp_disconnected, rate_limit) and 'workflow' tags. Enables Sentry
issue grouping by root cause and workflow-specific alerts. No-op when
sentry_sdk is not initialized."
```

---

## Task 7: End-to-end smoke check + final regression pass

**Why:** Confirm no test regressed and the real-world message looks right.

**Files:** None (verification only)

- [ ] **Step 7.1: Full test suite**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v --tb=short`

Expected: Every test passes. Note the total count for the commit body of Task 7 (something like "23 passed").

- [ ] **Step 7.2: Broader sanity check — other tests that import delivery_reporter**

Run: `cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_bot_delivery.py tests/test_progress_reporter_sinks.py tests/test_progress_reporter.py -v --tb=short`

Expected: All pass. If any fail because of a `DeliveryResult` constructor mismatch, the `category` default we added in Task 3 should already cover it — but inspect and fix if not.

- [ ] **Step 7.3: Manual visual check of today's incident scenario**

Run the same one-liner from Step 4.6 and confirm the output matches the expected format. This is the final acceptance of "o aviso no Telegram me diz qual tipo de falha foi".

- [ ] **Step 7.4: Manual visual check of heterogeneous mix**

Run:

```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -c "
from datetime import datetime, timezone
from execution.core.delivery_reporter import (
    Contact, DeliveryResult, DeliveryReport, SendErrorCategory, _format_telegram_message,
)
results = (
    [DeliveryResult(contact=Contact(name=f'N{i}', phone=str(i)), success=False,
                    error='HTTP 400: number not registered', duration_ms=100,
                    category=SendErrorCategory.INVALID_NUMBER) for i in range(40)]
    + [DeliveryResult(contact=Contact(name=f'R{i}', phone=str(100+i)), success=False,
                      error='HTTP 429', duration_ms=100,
                      category=SendErrorCategory.RATE_LIMIT) for i in range(20)]
    + [DeliveryResult(contact=Contact(name=f'T{i}', phone=str(200+i)), success=False,
                      error='timeout', duration_ms=100,
                      category=SendErrorCategory.TIMEOUT) for i in range(14)]
)
now = datetime.now(timezone.utc)
report = DeliveryReport(workflow='daily_report', started_at=now, finished_at=now, results=results)
print(_format_telegram_message(report, dashboard_base_url='https://dash', gh_run_id='999'))
"
```

Expected output includes three grouped lines:

```
• 40× Número inválido
  → AÇÃO: Revise a planilha de contatos
• 20× Rate limit
• 14× Timeout
```

- [ ] **Step 7.5: Final commit (if any tweaks were made in 7.2)**

If Steps 7.2–7.4 required no further changes, skip this. Otherwise:

```bash
cd "/Users/bigode/Dev/Antigravity WF "
git add -A
git commit -m "test(delivery): confirm cross-module consumers compatible with category field"
```

---

## Post-merge operational notes

- No env-var changes required — everything reads existing secrets.
- No migration required — `DeliveryResult.category` has a default, so stdout JSON consumers (dashboard) keep working; they can be enhanced in a follow-up to read `category` explicitly.
- Sentry will begin emitting new tags on the next cron run. Filter in Sentry UI: `send.error_category:whatsapp_disconnected` to see today-shaped incidents grouped.
- First cron run after merge is the acceptance test: trigger `send_daily_report.yml` manually from GitHub Actions UI with `dry_run=true` to avoid sending, then `dry_run=false` once happy with the Telegram alert format.
