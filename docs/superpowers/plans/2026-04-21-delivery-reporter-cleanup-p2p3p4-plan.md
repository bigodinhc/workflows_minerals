# Delivery Reporter Cleanup (P2+P3+P4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close followup priorities P2, P3, P4 from the categorized-alerts plan: eliminate duplicated JSON parsing (drift risk), reorder module layout to remove the "7 tasks bolted together" seam, and rename the Sentry tag namespace before alert rules lock the current names.

**Architecture:** Three sequential refactors on `execution/core/delivery_reporter.py`, each behavior-preserving (except P4 which intentionally renames one Sentry tag key). TDD for the one new helper (`_extract_http_reason`); all other changes verified by the existing 64-test suite continuing to pass. One commit per priority.

**Tech Stack:** Python 3.11+, `pytest` (tests run from repo root as `python -m pytest tests/test_delivery_reporter.py -v`). No new runtime dependencies.

**Repo path note:** The repo root directory name has a trailing space: `/Users/bigode/Dev/Antigravity WF ` (with trailing space). Quote all `cd` arguments.

---

## File Structure

Only two files are touched:

- **Modify:** `execution/core/delivery_reporter.py` — all three priorities land here
- **Modify:** `tests/test_delivery_reporter.py` — add 4 new unit tests for `_extract_http_reason` (P2); rename 3 occurrences of `send.error_category` → `send.category` (P4)

No new files. No deletes.

---

## Pre-flight

- [ ] **Step 0: Verify clean working tree and green test baseline**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && git status --short
```

Expected: no uncommitted changes in `execution/core/delivery_reporter.py` or `tests/test_delivery_reporter.py`. (Unrelated `.next/`, `AGENT.md`, plan docs in `docs/` are fine.)

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v 2>&1 | tail -20
```

Expected: 64 tests pass. Record the exact passing count — used as baseline at end of each task.

---

## Task 1 (P2): Eliminate duplicated JSON parsing via shared `_extract_http_reason` helper

**Files:**
- Modify: `execution/core/delivery_reporter.py` (lines 78-155: `classify_error` and `_categorize_error`; line 395-396 in dispatch)
- Modify: `tests/test_delivery_reporter.py` (add 4 new tests after the `_mock_http_error` helper at line 515)

**Design rationale:** Today `dispatch` calls `classify_error` AND `_categorize_error` on every exception — each parses the JSON body independently. Future tweaks to one's extraction logic that forget the other silently desync the dashboard-JSON `error` field from the Telegram category bucket.

**Fix:** Introduce a single pure helper `_extract_http_reason(exc)` that both callers delegate to. Then thread the extracted reason from `classify_error` into `_categorize_error` so dispatch only parses once per exception.

**Contract for `_extract_http_reason`:**
- Input: `requests.HTTPError` (must have `.response` set)
- Returns: str — human-readable reason from JSON body (UazAPI-style `{"error": bool, "message": str}` OR `{"error": str}`), truncated to 120 chars
- Returns empty string `""` when: body is not JSON, JSON is not a dict, or no usable `message`/`error` field is present
- Callers decide their own fallback behavior when the helper returns empty (keeps classifier decisions and legacy-string fallbacks independent)

---

- [ ] **Step 1.1: Write failing tests for `_extract_http_reason`**

Add this block to `tests/test_delivery_reporter.py` immediately after the `_mock_http_error` helper (currently at line 515, ending line 521). The 4 tests cover: UazAPI bool-error form, string-error form, non-JSON body, and dict without usable field.

```python
def test_extract_http_reason_prefers_message_when_error_is_bool():
    """UazAPI-style {"error": true, "message": "..."} — must return message."""
    from execution.core.delivery_reporter import _extract_http_reason

    exc = _mock_http_error(503, '{"error":true,"message":"WhatsApp disconnected"}')
    assert _extract_http_reason(exc) == "WhatsApp disconnected"


def test_extract_http_reason_uses_error_field_when_string():
    """{"error": "rate limited"} — string error is the reason."""
    from execution.core.delivery_reporter import _extract_http_reason

    exc = _mock_http_error(429, '{"error":"rate limited"}')
    assert _extract_http_reason(exc) == "rate limited"


def test_extract_http_reason_returns_empty_for_non_json_body():
    """Body like 'Internal Server Error' is not JSON — helper returns empty, callers handle fallback."""
    from execution.core.delivery_reporter import _extract_http_reason

    exc = _mock_http_error(500, "Internal Server Error")
    assert _extract_http_reason(exc) == ""


def test_extract_http_reason_returns_empty_when_no_usable_field():
    """JSON dict with only {"error": true} and no 'message' → empty (no usable reason)."""
    from execution.core.delivery_reporter import _extract_http_reason

    exc = _mock_http_error(503, '{"error":true}')
    assert _extract_http_reason(exc) == ""
```

- [ ] **Step 1.2: Run the new tests to verify they fail**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v -k "test_extract_http_reason" 2>&1 | tail -20
```

Expected: 4 tests collected, all 4 FAIL with `ImportError: cannot import name '_extract_http_reason' from 'execution.core.delivery_reporter'`.

- [ ] **Step 1.3: Implement `_extract_http_reason` helper**

Edit `execution/core/delivery_reporter.py`. Insert the new helper **immediately before** `def classify_error` (currently line 78). The helper keeps the inline `import requests`/`import json` pattern (startup-time justification already documented elsewhere in the file).

```python
def _extract_http_reason(exc: Exception) -> str:
    """Extract a human-readable reason from a requests.HTTPError's JSON body.

    Handles UazAPI-style bodies: {"error": bool, "message": str} or {"error": str}.
    Returns the reason truncated to 120 chars, or empty string if the body is
    not JSON, is not a dict, or has no usable message/error field. Callers decide
    their own fallback (category decision tree vs. legacy error string).
    """
    import json as _json
    body = exc.response.text or ""
    try:
        parsed = _json.loads(body)
    except (ValueError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    raw_error = parsed.get("error")
    candidate = parsed.get("message") if isinstance(raw_error, bool) else raw_error
    reason = str(candidate or parsed.get("message") or "")[:120]
    return reason
```

- [ ] **Step 1.4: Run the new tests to verify they pass**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v -k "test_extract_http_reason" 2>&1 | tail -20
```

Expected: 4 passed.

- [ ] **Step 1.5: Refactor `classify_error` to use the helper**

In `execution/core/delivery_reporter.py`, replace the body of the `isinstance(exc, _rq.HTTPError)` branch (currently lines 93-123) with the helper call. The full replacement of `classify_error` is:

```python
def classify_error(exc: Exception) -> tuple["SendErrorCategory", str]:
    """Classify an exception raised by a WhatsApp send into (category, reason).

    The reason is a short, human-readable string suitable for the Telegram alert.
    The category drives action hints, grouping, and circuit breaker behavior.
    """
    import requests as _rq

    if isinstance(exc, _rq.Timeout):
        return SendErrorCategory.TIMEOUT, "timeout"

    if isinstance(exc, _rq.ConnectionError):
        return SendErrorCategory.NETWORK, str(exc)[:120]

    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        reason_str = _extract_http_reason(exc)
        reason_lower = reason_str.lower()

        # Category decision tree
        if status == 401 or status == 403:
            return SendErrorCategory.AUTH, reason_str or f"HTTP {status}"
        if status == 429 or ("rate" in reason_lower and "limit" in reason_lower):
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

Note the `import json as _json` is removed from this function — the JSON parsing now lives only in `_extract_http_reason`.

- [ ] **Step 1.6: Run classify_error tests to verify no regression**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v -k "classify_error" 2>&1 | tail -25
```

Expected: all 9 `classify_error` tests pass.

- [ ] **Step 1.7: Refactor `_categorize_error` to accept optional reason + use helper**

In `execution/core/delivery_reporter.py`, replace the entire `_categorize_error` function (currently lines 128-155) with:

```python
def _categorize_error(exc: Exception, reason: Optional[str] = None) -> str:
    """Convert exception into short error category string (legacy format for
    dashboard JSON compat): "timeout", "HTTP N: <reason>", or str(exc)[:200].

    When `reason` is provided (pre-extracted by classify_error), skips the
    JSON re-parse. When not provided, extracts independently via
    `_extract_http_reason`. Falls back to truncated raw body when no structured
    reason is available, preserving historical dashboard behavior.
    """
    import requests as _rq
    if isinstance(exc, _rq.Timeout):
        return "timeout"
    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if reason is None:
            reason = _extract_http_reason(exc)
        if reason:
            return f"HTTP {status}: {reason}"
        body = exc.response.text or ""
        return f"HTTP {status}: {body[:100]}"
    return str(exc)[:200]
```

Note: the `Optional` type is already imported at line 10. The `import json as _json` previously in this function is gone — not needed anymore since the JSON parsing moved to the helper and the fallback only reads `.text`.

- [ ] **Step 1.8: Run _categorize_error tests to verify no regression**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v -k "categorize_error or dispatch_partial_failure or dispatch_all_failure or http_error" 2>&1 | tail -30
```

Expected: all tests pass (2 direct `_categorize_error` tests + the dispatch tests that assert `"HTTP 503"` / `"Service Unavailable"` prefixes still work).

- [ ] **Step 1.9: Update `dispatch` to thread reason from classify into categorize (single JSON parse path)**

In `execution/core/delivery_reporter.py`, find the exception-handling block inside the `dispatch` loop (currently lines 394-397):

```python
            except Exception as exc:
                category, _reason = classify_error(exc)
                error = _categorize_error(exc)  # keep legacy string for stdout JSON + dashboard compat
                self._capture_sentry(exc, category)
```

Replace with:

```python
            except Exception as exc:
                category, reason = classify_error(exc)
                error = _categorize_error(exc, reason)  # single JSON parse; legacy string for dashboard JSON
                self._capture_sentry(exc, category)
```

Change summary: drop the leading underscore from `_reason` (now used), and pass `reason` into `_categorize_error`.

- [ ] **Step 1.10: Run full test suite to verify all 68 tests pass (64 baseline + 4 new)**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py 2>&1 | tail -5
```

Expected: `68 passed`. If any existing test regressed, STOP and investigate before proceeding — the refactor should be behavior-preserving.

- [ ] **Step 1.11: Commit P2**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "$(cat <<'EOF'
refactor(delivery): extract _extract_http_reason helper to eliminate JSON-parse drift

classify_error and _categorize_error previously parsed the HTTPError JSON body
independently on every exception. A future tweak to one's extraction logic that
forgot the other would silently desync the dashboard JSON error field from the
Telegram category bucket. Unified via a single helper, and dispatch now threads
the already-extracted reason from classify_error into _categorize_error — one
JSON parse per exception instead of two. Behavior preserved for all 64 existing
tests; 4 new unit tests cover the helper's contract.
EOF
)"
```

Expected: commit succeeds, pre-commit hooks pass.

---

## Task 2 (P3): Reorder module layout + remove forward-reference hacks

**Files:**
- Modify: `execution/core/delivery_reporter.py` (full module reorder)

**Design rationale:** The module currently has imports at lines 8-10 AND again at 59-61 — a visible seam from task-by-task construction. The early `DeliveryResult` dataclass is forced to forward-reference `SendErrorCategory` with `"SendErrorCategory" = None  # type: ignore[assignment]` + a `__post_init__` coercion hack, because the enum is defined 34 lines later. Similarly `DeliveryReporter.__init__` uses `"frozenset[SendErrorCategory]"` and `_capture_sentry` uses `"SendErrorCategory"`.

Reordering so enums/constants come before dataclasses eliminates all three forward-references and collapses the two import blocks into one.

**Target module layout** (top to bottom):
1. Module docstring
2. Imports (all at top, alphabetized by stdlib then third-party)
3. `SendErrorCategory` enum
4. Module constants: `_CATEGORY_LABEL`, `_CATEGORY_HINT`, `_FATAL_CATEGORIES`, `_CIRCUIT_BREAKER_THRESHOLD`, `_CATEGORY_SAMPLE_LIMIT`
5. Dataclasses: `Contact`, `DeliveryResult` (now with direct `SendErrorCategory` default, no post_init), `DeliveryReport`
6. Classifiers: `_extract_http_reason`, `classify_error`, `_categorize_error`
7. Helpers: `_group_failures_by_category`, `_format_telegram_message`, `_build_telegram_client`, `build_contact_from_row`
8. Class: `DeliveryReporter`

Deferred imports (`import requests as _rq`, `import json as _json`, `import sentry_sdk`, `from execution.integrations.telegram_client import TelegramClient`, `import time`) stay inside functions — justified by startup time.

Behavior is unchanged — this is a pure rearrangement plus three annotation simplifications.

---

- [ ] **Step 2.1: Rewrite the module with the target layout**

This is a whole-file rewrite. Use the `Write` tool with the content below (after a `Read` of the current state). Preserve every line of logic from the current file; only the ORDER of sections and the three forward-reference annotations change.

Full target content for `execution/core/delivery_reporter.py`:

```python
"""
Delivery reporter: shared module for tracking WhatsApp send results
across GH Actions scripts and webhook flows.

Emits structured JSON to stdout (for dashboard parsing) and sends
Telegram summary notification at end of dispatch.
"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Iterable, Optional


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
    SKIPPED_CIRCUIT_BREAK = "skipped_circuit_break"


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
    SendErrorCategory.SKIPPED_CIRCUIT_BREAK: "Pulados pelo circuit breaker",
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
    SendErrorCategory.SKIPPED_CIRCUIT_BREAK: None,
}

# Categories considered "fatal" — N consecutive failures in the same one triggers abort.
# Transient categories (timeout, network) do NOT trip the breaker.
_FATAL_CATEGORIES = frozenset({
    SendErrorCategory.WHATSAPP_DISCONNECTED,
    SendErrorCategory.AUTH,
    SendErrorCategory.UPSTREAM_5XX,
})

_CIRCUIT_BREAKER_THRESHOLD = 5

# Per-category how many sample contact names to show inline (0 = none, show count only)
_CATEGORY_SAMPLE_LIMIT = 3


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
    category: SendErrorCategory = SendErrorCategory.UNKNOWN


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


def _extract_http_reason(exc: Exception) -> str:
    """Extract a human-readable reason from a requests.HTTPError's JSON body.

    Handles UazAPI-style bodies: {"error": bool, "message": str} or {"error": str}.
    Returns the reason truncated to 120 chars, or empty string if the body is
    not JSON, is not a dict, or has no usable message/error field. Callers decide
    their own fallback (category decision tree vs. legacy error string).
    """
    import json as _json
    body = exc.response.text or ""
    try:
        parsed = _json.loads(body)
    except (ValueError, TypeError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    raw_error = parsed.get("error")
    candidate = parsed.get("message") if isinstance(raw_error, bool) else raw_error
    reason = str(candidate or parsed.get("message") or "")[:120]
    return reason


def classify_error(exc: Exception) -> tuple[SendErrorCategory, str]:
    """Classify an exception raised by a WhatsApp send into (category, reason).

    The reason is a short, human-readable string suitable for the Telegram alert.
    The category drives action hints, grouping, and circuit breaker behavior.
    """
    import requests as _rq

    if isinstance(exc, _rq.Timeout):
        return SendErrorCategory.TIMEOUT, "timeout"

    if isinstance(exc, _rq.ConnectionError):
        return SendErrorCategory.NETWORK, str(exc)[:120]

    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        reason_str = _extract_http_reason(exc)
        reason_lower = reason_str.lower()

        # Category decision tree
        if status == 401 or status == 403:
            return SendErrorCategory.AUTH, reason_str or f"HTTP {status}"
        if status == 429 or ("rate" in reason_lower and "limit" in reason_lower):
            return SendErrorCategory.RATE_LIMIT, reason_str or f"HTTP {status}"
        if "disconnected" in reason_lower or "not connected" in reason_lower:
            return SendErrorCategory.WHATSAPP_DISCONNECTED, reason_str
        if status == 400 and ("not registered" in reason_lower or "invalid number" in reason_lower or "not on whatsapp" in reason_lower):
            return SendErrorCategory.INVALID_NUMBER, reason_str
        if 500 <= status < 600:
            return SendErrorCategory.UPSTREAM_5XX, reason_str or f"HTTP {status}"

        return SendErrorCategory.UNKNOWN, reason_str or f"HTTP {status}"

    return SendErrorCategory.UNKNOWN, str(exc)[:200]


def _categorize_error(exc: Exception, reason: Optional[str] = None) -> str:
    """Convert exception into short error category string (legacy format for
    dashboard JSON compat): "timeout", "HTTP N: <reason>", or str(exc)[:200].

    When `reason` is provided (pre-extracted by classify_error), skips the
    JSON re-parse. When not provided, extracts independently via
    `_extract_http_reason`. Falls back to truncated raw body when no structured
    reason is available, preserving historical dashboard behavior.
    """
    import requests as _rq
    if isinstance(exc, _rq.Timeout):
        return "timeout"
    if isinstance(exc, _rq.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if reason is None:
            reason = _extract_http_reason(exc)
        if reason:
            return f"HTTP {status}: {reason}"
        body = exc.response.text or ""
        return f"HTTP {status}: {body[:100]}"
    return str(exc)[:200]


def _group_failures_by_category(failures: list) -> list:
    """Return list of (category, results) tuples sorted by count descending.

    Skipped-by-circuit-breaker entries are excluded — they render as a
    separate trailing footnote, not as a competing bucket.
    """
    from collections import defaultdict
    buckets: dict = defaultdict(list)
    for f in failures:
        if f.category == SendErrorCategory.SKIPPED_CIRCUIT_BREAK:
            continue
        buckets[f.category].append(f)
    return sorted(buckets.items(), key=lambda kv: -len(kv[1]))


def _format_telegram_message(
    report: DeliveryReport,
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

        # Trailing footnote: circuit breaker skipped contacts, shown after the
        # real failure categories so the actionable cause stays at the top.
        skipped_count = sum(
            1 for f in report.failures
            if f.category == SendErrorCategory.SKIPPED_CIRCUIT_BREAK
        )
        if skipped_count > 0:
            lines.append("")
            lines.append(f"ℹ️ {skipped_count} contatos pulados pelo circuit breaker")

    link = (
        f"{dashboard_base_url}/?run_id={gh_run_id}"
        if gh_run_id
        else f"{dashboard_base_url}/"
    )
    lines.append("")
    lines.append(f"[Ver no dashboard]({link})")

    return "\n".join(lines)


def _build_telegram_client():
    """Factory for TelegramClient. Separate function to allow test monkeypatching."""
    from execution.integrations.telegram_client import TelegramClient
    return TelegramClient()


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
        circuit_breaker_threshold: int = _CIRCUIT_BREAKER_THRESHOLD,
        fatal_categories: frozenset[SendErrorCategory] = _FATAL_CATEGORIES,
    ):
        self.workflow = workflow
        self.send_fn = send_fn
        self.notify_telegram = notify_telegram
        self.telegram_chat_id = telegram_chat_id
        self.dashboard_base_url = dashboard_base_url
        self.gh_run_id = gh_run_id
        self.circuit_breaker_threshold = circuit_breaker_threshold
        self.fatal_categories = fatal_categories

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
        import time
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
                    category=SendErrorCategory.SKIPPED_CIRCUIT_BREAK,
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
                category, reason = classify_error(exc)
                error = _categorize_error(exc, reason)  # single JSON parse; legacy string for dashboard JSON
                self._capture_sentry(exc, category)
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
                    pass  # progress callback failures do not abort dispatch

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

    def _capture_sentry(self, exc: Exception, category: SendErrorCategory) -> None:
        """Capture exception to Sentry with category as a searchable tag.
        Silent no-op if sentry_sdk is not importable or not initialized.
        """
        try:
            import sentry_sdk
            with sentry_sdk.new_scope() as scope:
                scope.set_tag("send.error_category", category.value)
                scope.set_tag("workflow", self.workflow)
                sentry_sdk.capture_exception(exc)
        except Exception:
            pass  # never let telemetry failures break dispatch
```

Key annotation changes vs. Task 1 state:
- `DeliveryResult.category`: `"SendErrorCategory" = None  # type: ignore[assignment]` + `__post_init__` → `SendErrorCategory = SendErrorCategory.UNKNOWN` (direct default, no coercion)
- `DeliveryReporter.__init__` `fatal_categories`: `"frozenset[SendErrorCategory]"` → `frozenset[SendErrorCategory]`
- `_capture_sentry` `category` param: `"SendErrorCategory"` → `SendErrorCategory`
- `classify_error` return annotation: `tuple["SendErrorCategory", str]` → `tuple[SendErrorCategory, str]`
- `import time` moved from top-of-second-block to inside `dispatch` (it's startup-path code used only there — consistent with other deferred imports in the class)

- [ ] **Step 2.2: Run the full test suite to verify the reorder is behavior-preserving**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py 2>&1 | tail -5
```

Expected: `68 passed`. If anything regresses, the reorder introduced a subtle bug — diff against the previous commit and compare section-by-section.

- [ ] **Step 2.3: Spot-check callers haven't broken**

The `DeliveryReporter` is imported in several places. Quickly verify imports still resolve:

```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -c "from execution.core.delivery_reporter import DeliveryReporter, DeliveryResult, Contact, DeliveryReport, SendErrorCategory, classify_error, _categorize_error, _extract_http_reason, build_contact_from_row; print('OK')"
```

Expected: prints `OK` with no import error.

- [ ] **Step 2.4: Commit P3**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py
git commit -m "$(cat <<'EOF'
refactor(delivery): reorder module layout and remove forward-reference hacks

Module had two import blocks (lines 8-10 and 59-61) — a visible seam from
task-by-task construction. Early DeliveryResult dataclass forward-referenced
SendErrorCategory with a type: ignore + __post_init__ coercion because the
enum was defined 34 lines later. Reordered to: imports, enum, constants,
dataclasses, classifiers, helpers, class — which lets DeliveryResult use a
direct SendErrorCategory.UNKNOWN default and drops all three "SendErrorCategory"
string annotations (DeliveryResult.category, DeliveryReporter.__init__
fatal_categories, _capture_sentry category param, classify_error return).
Behavior preserved — all 68 tests still pass.
EOF
)"
```

Expected: commit succeeds.

---

## Task 3 (P4): Rename Sentry tag `send.error_category` → `send.category`

**Files:**
- Modify: `execution/core/delivery_reporter.py` (single line in `_capture_sentry`)
- Modify: `tests/test_delivery_reporter.py` (3 occurrences: docstring, tag filter, assertion)

**Design rationale:** The Sentry `send.*` namespace has one prefixed tag (`send.error_category`) and one unprefixed (`workflow`). Renaming `send.error_category` → `send.category` is shorter and avoids redundancy with the category enum's own name. `workflow` stays unprefixed per followup doc — it may later become a first-class Sentry dimension used across other products.

**Timing:** Must happen before any third-party Sentry alert rule / dashboard starts depending on the current `send.error_category` name.

---

- [ ] **Step 3.1: Rename the tag in source**

Edit `execution/core/delivery_reporter.py`. In `_capture_sentry`, find:

```python
                scope.set_tag("send.error_category", category.value)
```

Replace with:

```python
                scope.set_tag("send.category", category.value)
```

Do NOT touch the next line (`scope.set_tag("workflow", self.workflow)`) — `workflow` stays unprefixed.

- [ ] **Step 3.2: Update the 3 occurrences in the Sentry test**

Edit `tests/test_delivery_reporter.py`. The test `test_dispatch_tags_sentry_with_error_category` at line 740 has three references to the old name:

Change line 741 (docstring):
```python
    """Each failure should set Sentry tag 'send.error_category' with category value."""
```
→
```python
    """Each failure should set Sentry tag 'send.category' with category value."""
```

Change line 769 (filter):
```python
    tag_entries = [t for t in captured_tags if t[0] == "send.error_category"]
```
→
```python
    tag_entries = [t for t in captured_tags if t[0] == "send.category"]
```

Change line 770 (assertion):
```python
    assert ("send.error_category", "whatsapp_disconnected") in tag_entries
```
→
```python
    assert ("send.category", "whatsapp_disconnected") in tag_entries
```

Line numbers may drift after Tasks 1-2; use Grep to find the exact lines:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && grep -n "send.error_category" tests/test_delivery_reporter.py
```

All hits must be replaced. If grep returns zero hits after the edits, all three references are updated.

- [ ] **Step 3.3: Verify no `send.error_category` strings remain in the repo**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && grep -rn "send.error_category" execution/ tests/ 2>&1 | grep -v ".pyc"
```

Expected: no output (all occurrences renamed). If anything matches, replace it too.

- [ ] **Step 3.4: Run the Sentry tagging tests**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py -v -k "sentry" 2>&1 | tail -15
```

Expected: 3 Sentry tests pass (`test_dispatch_tags_sentry_with_error_category`, `test_dispatch_does_not_tag_sentry_on_success`, `test_dispatch_silent_when_sentry_sdk_unavailable`).

- [ ] **Step 3.5: Run the full suite one more time**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && python -m pytest tests/test_delivery_reporter.py 2>&1 | tail -5
```

Expected: `68 passed`.

- [ ] **Step 3.6: Commit P4**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add execution/core/delivery_reporter.py tests/test_delivery_reporter.py
git commit -m "$(cat <<'EOF'
refactor(delivery): rename Sentry tag send.error_category → send.category

Shorter, avoids redundancy with the SendErrorCategory enum's own name. The
workflow tag stays unprefixed since it may later become a first-class Sentry
dimension shared across other products. Rename happens before any alert rules
or dashboards depend on the old name.
EOF
)"
```

Expected: commit succeeds.

---

## Post-flight

- [ ] **Step 4.1: Verify 3 clean commits on top of baseline**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && git log --oneline -5
```

Expected: top 3 commits are the P2, P3, P4 refactors in order (newest first: P4, P3, P2), followed by `b26071c` (the pre-existing baseline at session start).

- [ ] **Step 4.2: Confirm no staged/unstaged changes to delivery_reporter.py or its test file**

Run:
```bash
cd "/Users/bigode/Dev/Antigravity WF " && git status --short | grep -E "delivery_reporter"
```

Expected: no output.

- [ ] **Step 4.3: Update followup doc — mark P2/P3/P4 shipped**

Edit `docs/superpowers/followups/2026-04-20-categorized-alerts-followups.md`. At the end of each of the P2, P3, P4 headers, append the shipped marker. Example for P2 heading (currently line 34):

```markdown
## Priority 2 — Collapse `_categorize_error` + `classify_error` duplicated JSON parsing
```

→

```markdown
## Priority 2 — Collapse `_categorize_error` + `classify_error` duplicated JSON parsing — **✅ SHIPPED 2026-04-21**
```

Apply the same pattern to the P3 and P4 headings. Leave P5 and P6 unchanged.

- [ ] **Step 4.4: Update memory pointer**

Edit `/Users/bigode/.claude/projects/-Users-bigode-Dev-Antigravity-WF-/memory/reference_delivery_reporter_followups.md` to reflect the new status. Update the description line and body to say `P1-P4 shipped 2026-04-21; P5-P6 open in followups doc`.

Also update `/Users/bigode/.claude/projects/-Users-bigode-Dev-Antigravity-WF-/memory/MEMORY.md` — change the pointer line for delivery reporter followups to match the new status.

- [ ] **Step 4.5: Commit followup doc + memory updates**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git add docs/superpowers/followups/2026-04-20-categorized-alerts-followups.md
git commit -m "docs(followups): mark P2/P3/P4 shipped (2026-04-21)"
```

Memory files are outside the repo — no commit needed there.

Expected: commit succeeds.

---

## Self-Review (Plan Author)

**Spec coverage:**
- P2 (collapse duplicated JSON parse, drift risk) → Task 1, Steps 1.1–1.11. New helper with TDD (1.1–1.4), classify_error refactor (1.5–1.6), _categorize_error refactor (1.7–1.8), dispatch threading (1.9–1.10), commit (1.11). ✓
- P3 (reorder module layout, remove forward-ref hacks) → Task 2, Steps 2.1–2.4. Full rewrite with target layout, full-suite verification, import smoke test, commit. ✓
- P4 (Sentry tag rename) → Task 3, Steps 3.1–3.6. Source rename, 3 test occurrences updated, repo-wide grep sanity, commit. ✓

**Placeholder scan:** No "TBD", "implement later", "similar to Task N", "add appropriate error handling". All code blocks contain complete content.

**Type consistency:**
- `SendErrorCategory` is the single enum name used throughout — no rename partway.
- `_extract_http_reason` signature `(exc: Exception) -> str` is identical in the new-helper task (1.3) and the final target module (2.1).
- `_categorize_error(exc, reason=None)` signature is identical in task 1.7 and target layout 2.1.
- `classify_error` return type: `tuple["SendErrorCategory", str]` in task 1.5 → `tuple[SendErrorCategory, str]` in task 2.1 (deliberate P3 change — forward-ref removed after reorder).

**Edge-case note:** Task 1.5's `classify_error` uses the quoted `"SendErrorCategory"` return type because at that commit point the enum is still defined AFTER the function. Task 2.1 drops the quotes because the reorder puts the enum first. This is consistent with the "P2 is behavior-preserving, P3 is the cleanup" split.
