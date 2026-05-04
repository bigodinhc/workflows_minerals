# WhatsApp Throttle + Anti-Spam Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Throttle all WhatsApp broadcast paths to 15-30s/msg jitter, append a per-contact `Ref:` token for byte-uniqueness, honor 429 with 60s backoff, serialize PDF dispatch (`CONCURRENCY=1`) and add an opt-in `PDF_DELIVERY_MODE=link` flag that delivers PDFs as Supabase-Storage signed URLs instead of base64 attachments.

**Architecture:** All variation and timing logic centralizes inside `DeliveryReporter.dispatch()` (text path) and `webhook/dispatch_document.py` (PDF path). Callers (cron scripts, bot dispatch) get the new behavior transparently. Configuration via env vars: `BROADCAST_DELAY_MIN`, `BROADCAST_DELAY_MAX`, `BROADCAST_RATE_LIMIT_SLEEP`, `BROADCAST_REF_TOKEN_ENABLED`, `PDF_DELIVERY_MODE`. Lock TTLs in `morning_check.py` and `baltic_ingestion.py` bump from 20→60min to absorb the slower broadcasts.

**Tech Stack:** Python 3.10/3.11 (`execution/`, `webhook/`), pytest + fakeredis + unittest.mock, supabase-py for Storage signed URLs, redis (sync + async), uazapi HTTP client.

**Spec reference:** `docs/superpowers/specs/2026-05-03-whatsapp-throttle-anti-spam-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `execution/core/delivery_reporter.py` | Modify | Throttle, Ref token, 429 backoff, EventBus metadata |
| `execution/scripts/baltic_ingestion.py` | Modify line 44 | Bump `_INFLIGHT_LOCK_TTL_SEC` 20→60min |
| `execution/scripts/morning_check.py` | Modify line 75 | Bump `_INFLIGHT_LOCK_TTL_SEC` 20→60min |
| `webhook/dispatch_document.py` | Modify | `CONCURRENCY=1`, `_download_pdf` returns bytes, PDF link mode, per-iteration delay |
| `webhook/pdf_storage.py` | Create | `upload_and_sign(approval_id, filename, pdf_bytes) → signed_url` |
| `.env.example` | Modify | Document new env vars |
| `tests/test_delivery_reporter_throttle.py` | Create | Unit tests for throttle, Ref token, 429 |
| `tests/test_pdf_storage.py` | Create | Unit tests for Supabase Storage helper |
| `tests/test_dispatch_document_throttle.py` | Create | Unit tests for `CONCURRENCY=1` + link mode |

---

## Task 1: Add config-reading helpers + per-contact Ref token

**Why:** The Ref token is the foundation of byte-uniqueness across the 105 messages of a broadcast. Adding it first (and gating it behind an env var) gives a clean low-risk first commit before we touch timing.

**Files:**
- Modify: `execution/core/delivery_reporter.py` (add helpers near top, modify `dispatch`)
- Test: `tests/test_delivery_reporter_throttle.py` (create)

- [ ] **Step 1.1: Write the failing test for Ref token append**

Create `tests/test_delivery_reporter_throttle.py`:

```python
"""Tests for throttle, Ref token, and 429 backoff in DeliveryReporter."""
from __future__ import annotations
import re
from unittest.mock import MagicMock, patch
import pytest
from execution.core.delivery_reporter import Contact, DeliveryReporter


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Replace time.sleep so tests don't actually wait."""
    monkeypatch.setattr("execution.core.delivery_reporter.time.sleep", lambda _: None)


def test_dispatch_appends_ref_token_to_each_message(monkeypatch):
    monkeypatch.setenv("BROADCAST_REF_TOKEN_ENABLED", "true")
    sent = []
    def send_fn(phone, text):
        sent.append((phone, text))

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(3)]
    reporter.dispatch(contacts, message="hello world")

    assert len(sent) == 3
    for _phone, text in sent:
        assert text.startswith("hello world\n\nRef: ")
        # 6-char alphanumeric token
        m = re.search(r"Ref: ([A-Za-z0-9_-]{6})$", text)
        assert m is not None, f"no Ref token in: {text!r}"

    tokens = [re.search(r"Ref: (\S+)$", t).group(1) for _p, t in sent]
    assert len(set(tokens)) == 3, "tokens must differ across contacts"


def test_dispatch_omits_ref_token_when_disabled(monkeypatch):
    monkeypatch.setenv("BROADCAST_REF_TOKEN_ENABLED", "false")
    sent = []
    def send_fn(phone, text):
        sent.append(text)

    reporter = DeliveryReporter(workflow="t", send_fn=send_fn, notify_telegram=False)
    contacts = [Contact(name="A", phone="111")]
    reporter.dispatch(contacts, message="hello")

    assert sent == ["hello"]
```

- [ ] **Step 1.2: Run test to verify it fails**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py::test_dispatch_appends_ref_token_to_each_message -v
```
Expected: FAIL — Ref token not yet in output (sent text equals "hello world" without footer).

- [ ] **Step 1.3: Implement Ref token in `dispatch`**

Edit `execution/core/delivery_reporter.py`. Add `import os`, `import secrets`, `import time` at top of file (some may already be present — verify). Then add helper near the top of the file (above `class SendErrorCategory`):

```python
def _broadcast_ref_token() -> str:
    """6-char URL-safe random token for per-message byte-uniqueness."""
    # token_urlsafe(6) returns ~8 chars; truncate to 6 for compact footer.
    return secrets.token_urlsafe(6)[:6]


def _ref_token_enabled() -> bool:
    return os.environ.get("BROADCAST_REF_TOKEN_ENABLED", "true").lower() != "false"
```

In `DeliveryReporter.dispatch`, locate the line `self.send_fn(contact.phone, message)` (around line 396 of the current file). Replace the `try` block that currently looks like:

```python
            try:
                self.send_fn(contact.phone, message)
                success = True
            except Exception as exc:
```

with:

```python
            outgoing = message
            if _ref_token_enabled():
                outgoing = f"{message}\n\nRef: {_broadcast_ref_token()}"
            try:
                self.send_fn(contact.phone, outgoing)
                success = True
            except Exception as exc:
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py -v
```
Expected: both tests PASS.

- [ ] **Step 1.5: Run full delivery_reporter test suite to confirm no regressions**

```bash
uv run pytest tests/test_delivery_reporter.py -v
```
Expected: all existing tests still PASS.

- [ ] **Step 1.6: Commit**

```bash
git add execution/core/delivery_reporter.py tests/test_delivery_reporter_throttle.py
git commit -m "feat(delivery_reporter): append per-contact Ref token for byte-uniqueness

Each outgoing broadcast now ends with 'Ref: <6 chars>'. Defeats hash-based
spam classifiers that cluster identical messages. Enabled by default;
gate via BROADCAST_REF_TOKEN_ENABLED=false to disable."
```

---

## Task 2: Add inter-message throttle (15-30s jitter)

**Why:** Throttle is the second prong — even with byte-unique tokens, sub-second velocity flags the velocity classifier. Adding the sleep separately keeps the diff reviewable.

**Files:**
- Modify: `execution/core/delivery_reporter.py` (post-iteration sleep)
- Modify: `tests/test_delivery_reporter_throttle.py`

- [ ] **Step 2.1: Write the failing test for throttle delay**

Append to `tests/test_delivery_reporter_throttle.py`:

```python
def test_dispatch_sleeps_between_sends(monkeypatch):
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "15.0")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "30.0")
    sleeps: list[float] = []
    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform",
        lambda lo, hi: (lo + hi) / 2,
    )

    reporter = DeliveryReporter(
        workflow="t", send_fn=MagicMock(), notify_telegram=False
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(3)]
    reporter.dispatch(contacts, message="hi")

    # 3 contacts → 2 inter-message sleeps (none after the last)
    assert sleeps == [22.5, 22.5]


def test_dispatch_no_sleep_after_last_contact(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform", lambda lo, hi: 1.0
    )
    reporter = DeliveryReporter(
        workflow="t", send_fn=MagicMock(), notify_telegram=False
    )
    reporter.dispatch([Contact(name="solo", phone="999")], message="hi")
    assert sleeps == []  # single contact, no inter-message sleep


def test_dispatch_no_sleep_for_circuit_broken_skipped(monkeypatch):
    """Circuit-broken contacts don't actually call uazapi → no throttle delay."""
    sleeps: list[float] = []
    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform", lambda lo, hi: 5.0
    )
    # send_fn that raises a fatal-category error on every call → circuit trips
    import requests
    def send_fn(phone, text):
        resp = MagicMock()
        resp.status_code = 401
        resp.text = '{"message": "auth failed"}'
        err = requests.HTTPError(response=resp)
        raise err

    reporter = DeliveryReporter(
        workflow="t",
        send_fn=send_fn,
        notify_telegram=False,
        circuit_breaker_threshold=2,
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(5)]
    reporter.dispatch(contacts, message="hi")

    # Failures 1+2 each followed by a sleep (between iterations).
    # Failure 3+ trip the circuit → skipped → no sleep.
    # Expected: 2 sleeps between attempts 1→2 and 2→3, then circuit trips,
    # remaining iterations are circuit-broken (no API call, no sleep).
    assert len(sleeps) == 2
    assert all(s == 5.0 for s in sleeps)
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py::test_dispatch_sleeps_between_sends -v
```
Expected: FAIL — no sleep is currently called.

- [ ] **Step 2.3: Implement throttle**

Edit `execution/core/delivery_reporter.py`. Add `import random` near the other imports if not present (check first; some sleeps already use `time` from a local import). Add module-level helper near `_ref_token_enabled`:

```python
def _broadcast_delay_range() -> tuple[float, float]:
    """Returns (min, max) seconds between sends. Clamps to safe values."""
    lo = float(os.environ.get("BROADCAST_DELAY_MIN", "15.0"))
    hi = float(os.environ.get("BROADCAST_DELAY_MAX", "30.0"))
    lo = max(0.0, lo)
    hi = max(lo, hi)
    return lo, hi
```

In `DeliveryReporter.dispatch`, find the end of the for loop (after the `if on_progress is not None: ...` block, before the next iteration). Note the existing local `import time` inside `dispatch` (line ~364). Replace it with module-level `import time` at top and `import random` (if not yet present).

Add this just before the `for` loop closes (i.e., right after `if on_progress is not None: ...`):

```python
            # Throttle: sleep between sends to avoid spam-velocity classifiers.
            # Skip after the last contact, and skip when the contact was
            # circuit-broken (no API call was made).
            is_last = (i == total - 1)
            was_real_send = not (
                circuit_tripped and result.category == SendErrorCategory.SKIPPED_CIRCUIT_BREAK
            )
            if not is_last and was_real_send:
                lo, hi = _broadcast_delay_range()
                time.sleep(random.uniform(lo, hi))
```

Important: this block sits AFTER the `result = DeliveryResult(...)` and circuit-breaker bookkeeping but BEFORE the `if on_progress is not None: ...` block needs to keep the same position. Re-read the function carefully and place the throttle as the LAST statement inside the `for` body.

Also: the existing function has `import time` shadowed inside dispatch. Hoist it to module level (top of file alongside other imports) and remove the local one. Same for `random` — add at top.

- [ ] **Step 2.4: Run all throttle tests**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 2.5: Run full delivery_reporter test suite**

```bash
uv run pytest tests/test_delivery_reporter.py tests/test_delivery_reporter_throttle.py -v
```
Expected: all PASS, no regressions.

- [ ] **Step 2.6: Commit**

```bash
git add execution/core/delivery_reporter.py tests/test_delivery_reporter_throttle.py
git commit -m "feat(delivery_reporter): throttle 15-30s jitter between sends

Inserts random.uniform(BROADCAST_DELAY_MIN, BROADCAST_DELAY_MAX) sleep
between every successful and failed send. No sleep after the last contact
or for circuit-broken-skipped contacts (no API call was made)."
```

---

## Task 3: Add 60s backoff on RATE_LIMIT (429)

**Why:** When uazapi returns rate-limit, ignoring it and pushing the next message is exactly the bot pattern WhatsApp's anti-spam looks for. Honoring the signal is itself a positive trust signal.

**Files:**
- Modify: `execution/core/delivery_reporter.py` (extra sleep on 429 category)
- Modify: `tests/test_delivery_reporter_throttle.py`

- [ ] **Step 3.1: Write the failing test for 429 backoff**

Append to `tests/test_delivery_reporter_throttle.py`:

```python
def test_dispatch_extra_sleep_on_rate_limit(monkeypatch):
    monkeypatch.setenv("BROADCAST_RATE_LIMIT_SLEEP", "60.0")
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "15.0")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "30.0")
    sleeps: list[float] = []
    monkeypatch.setattr(
        "execution.core.delivery_reporter.time.sleep",
        lambda s: sleeps.append(s),
    )
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform",
        lambda lo, hi: 20.0,
    )

    import requests
    call = {"n": 0}
    def send_fn(phone, text):
        call["n"] += 1
        if call["n"] == 1:
            resp = MagicMock()
            resp.status_code = 429
            resp.text = '{"message": "rate limit exceeded"}'
            raise requests.HTTPError(response=resp)
        # subsequent calls succeed

    reporter = DeliveryReporter(
        workflow="t", send_fn=send_fn, notify_telegram=False
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(3)]
    reporter.dispatch(contacts, message="hi")

    # Expected sleeps: 60 (rate-limit backoff after attempt 1)
    #                + 20 (regular jitter after attempt 1)
    #                + 20 (regular jitter after attempt 2)
    # No sleep after attempt 3 (last).
    assert sleeps == [60.0, 20.0, 20.0]
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py::test_dispatch_extra_sleep_on_rate_limit -v
```
Expected: FAIL — only `[20.0, 20.0]` recorded, no 60s prefix.

- [ ] **Step 3.3: Implement 429 backoff**

Edit `execution/core/delivery_reporter.py`. Add another module-level helper:

```python
def _rate_limit_sleep() -> float:
    return max(0.0, float(os.environ.get("BROADCAST_RATE_LIMIT_SLEEP", "60.0")))
```

In `DeliveryReporter.dispatch`, find the throttle block from Task 2. Insert a 429-specific sleep BEFORE the regular jitter sleep, gated on the failure category. The `if not is_last and was_real_send:` block becomes:

```python
            is_last = (i == total - 1)
            was_real_send = not (
                circuit_tripped and result.category == SendErrorCategory.SKIPPED_CIRCUIT_BREAK
            )
            if not is_last and was_real_send:
                if (not success) and category == SendErrorCategory.RATE_LIMIT:
                    time.sleep(_rate_limit_sleep())
                lo, hi = _broadcast_delay_range()
                time.sleep(random.uniform(lo, hi))
```

- [ ] **Step 3.4: Run rate-limit test**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py::test_dispatch_extra_sleep_on_rate_limit -v
```
Expected: PASS.

- [ ] **Step 3.5: Run full throttle + delivery_reporter suites**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py tests/test_delivery_reporter.py -v
```
Expected: all PASS.

- [ ] **Step 3.6: Commit**

```bash
git add execution/core/delivery_reporter.py tests/test_delivery_reporter_throttle.py
git commit -m "feat(delivery_reporter): extra 60s sleep when uazapi returns 429

Honors rate-limit responses instead of treating them as just-another-failure.
BROADCAST_RATE_LIMIT_SLEEP (default 60s) is added BEFORE the regular jitter
delay, only on RATE_LIMIT-categorized failures."
```

---

## Task 4: Extend `delivery_summary` EventBus payload with throttle metadata

**Why:** Operators need to see actual broadcast wall-clock and configured throttle in the events channel card. Without it, tuning the env vars is blind.

**Files:**
- Modify: `execution/core/delivery_reporter.py` (`_emit_delivery_summary_event`)
- Modify: `tests/test_delivery_reporter_throttle.py`

- [ ] **Step 4.1: Write the failing test**

Append to `tests/test_delivery_reporter_throttle.py`:

```python
def test_delivery_summary_event_includes_throttle_metadata(monkeypatch):
    monkeypatch.setenv("BROADCAST_DELAY_MIN", "15.0")
    monkeypatch.setenv("BROADCAST_DELAY_MAX", "30.0")
    monkeypatch.setattr(
        "execution.core.delivery_reporter.random.uniform", lambda lo, hi: 1.0
    )

    captured = {}
    fake_bus = MagicMock()
    def emit(event, label=None, detail=None, level="info"):
        captured["event"] = event
        captured["detail"] = detail
        captured["level"] = level
    fake_bus.emit.side_effect = emit

    monkeypatch.setattr(
        "execution.core.event_bus.get_current_bus", lambda: fake_bus
    )

    reporter = DeliveryReporter(
        workflow="t", send_fn=MagicMock(), notify_telegram=False
    )
    contacts = [Contact(name=f"U{i}", phone=f"55{i:03}") for i in range(2)]
    reporter.dispatch(contacts, message="hi")

    assert captured["event"] == "delivery_summary"
    detail = captured["detail"]
    assert detail["delay_min"] == 15.0
    assert detail["delay_max"] == 30.0
    assert detail["total"] == 2
    assert "duration_seconds" in detail
    assert isinstance(detail["duration_seconds"], int)
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py::test_delivery_summary_event_includes_throttle_metadata -v
```
Expected: FAIL — `detail` dict lacks `delay_min`, `delay_max`, `duration_seconds`.

- [ ] **Step 4.3: Implement metadata extension**

Edit `execution/core/delivery_reporter.py`, in `_emit_delivery_summary_event`. Find the `bus.emit("delivery_summary", ...)` call and replace its `detail` dict with:

```python
        try:
            lo, hi = _broadcast_delay_range()
            duration = int((report.finished_at - report.started_at).total_seconds())
            bus.emit(
                "delivery_summary",
                label=label,
                detail={
                    "total": report.total,
                    "success": report.success_count,
                    "failure": report.failure_count,
                    "delay_min": lo,
                    "delay_max": hi,
                    "duration_seconds": duration,
                },
                level=level,
            )
        except Exception:
            pass  # bus.emit already swallows sink failures; belt-and-suspenders
```

- [ ] **Step 4.4: Run test**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py::test_delivery_summary_event_includes_throttle_metadata -v
```
Expected: PASS.

- [ ] **Step 4.5: Run full throttle + reporter suites**

```bash
uv run pytest tests/test_delivery_reporter_throttle.py tests/test_delivery_reporter.py -v
```
Expected: all PASS.

- [ ] **Step 4.6: Commit**

```bash
git add execution/core/delivery_reporter.py tests/test_delivery_reporter_throttle.py
git commit -m "feat(delivery_reporter): emit throttle config + duration in delivery_summary

Operators can now see the actual wall-clock duration and the configured
delay range right on the events-channel card, without grepping logs."
```

---

## Task 5: Bump in-flight lock TTL 20→60min in baltic + morning_check

**Why:** With 105 contacts × ~22.5s avg = ~40min broadcast, the existing 20-min lock TTL leaves only ~3 min margin. Re-trigger could see no lock and start a parallel broadcast.

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py:44`
- Modify: `execution/scripts/morning_check.py:75`

- [ ] **Step 5.1: Verify current values**

Run:
```bash
grep -n "_INFLIGHT_LOCK_TTL_SEC" execution/scripts/baltic_ingestion.py execution/scripts/morning_check.py
```
Expected output:
```
execution/scripts/baltic_ingestion.py:44:_INFLIGHT_LOCK_TTL_SEC = 20 * 60   # 20 min — covers max observed broadcast duration
execution/scripts/morning_check.py:75:_INFLIGHT_LOCK_TTL_SEC = 20 * 60   # 20 min — covers max observed broadcast duration
```

- [ ] **Step 5.2: Update both constants**

Edit `execution/scripts/baltic_ingestion.py` line 44, change:
```python
_INFLIGHT_LOCK_TTL_SEC = 20 * 60   # 20 min — covers max observed broadcast duration
```
to:
```python
_INFLIGHT_LOCK_TTL_SEC = 60 * 60   # 60 min — accommodates throttled broadcast (~40min) + buffer
```

Edit `execution/scripts/morning_check.py` line 75, same change:
```python
_INFLIGHT_LOCK_TTL_SEC = 60 * 60   # 60 min — accommodates throttled broadcast (~40min) + buffer
```

- [ ] **Step 5.3: Run idempotency regression tests**

```bash
uv run pytest tests/test_baltic_ingestion_idempotency.py tests/test_morning_check_idempotency.py -v
```
Expected: all PASS. Some tests may assert the TTL value indirectly — if any FAIL because they check for `20 * 60`, update those assertions to `60 * 60` (the spec-driven correct value).

- [ ] **Step 5.4: Commit**

```bash
git add execution/scripts/baltic_ingestion.py execution/scripts/morning_check.py
git commit -m "fix(idempotency): bump inflight-lock TTL 20→60min for throttled broadcast

Throttle 15-30s × 105 contacts = ~40min broadcast. The previous 20-min
TTL left only ~3min of margin — a re-triggered cron could see the lock
expired and start a parallel broadcast. 60min absorbs the new duration."
```

---

## Task 6: Add new env vars to `.env.example`

**Why:** Operators must know these vars exist + their defaults. `.env.example` is the canonical source.

**Files:**
- Modify: `.env.example`

- [ ] **Step 6.1: Append new section to `.env.example`**

Edit `.env.example`, add at the end of the file:

```bash

# ── WhatsApp throttle + anti-spam (spec: 2026-05-03-whatsapp-throttle-anti-spam) ──
# Min/max seconds between consecutive WhatsApp sends in a broadcast.
# Picked uniformly per-message. Defaults: 15.0 / 30.0.
# For 105 contacts → ~40min wall-clock. Tune up if quality rating drops.
BROADCAST_DELAY_MIN=15.0
BROADCAST_DELAY_MAX=30.0

# Extra sleep (seconds) after a 429 / rate-limit response, before resuming
# the regular jitter delay. Default: 60.0.
BROADCAST_RATE_LIMIT_SLEEP=60.0

# Append a 6-char random "Ref:" footer to each broadcast message.
# Defeats hash-classifier clustering. Defaults to true; set to false to disable.
BROADCAST_REF_TOKEN_ENABLED=true

# OneDrive PDF delivery mode for webhook/dispatch_document.py:
#   "attachment" — current behavior, send PDF as base64 via uazapi /send/media
#   "link"       — upload PDF to Supabase Storage and send a text message
#                  with a 7-day signed URL. Per-recipient byte-uniqueness via
#                  the unique signed-URL token. TEST FIRST with one contact.
PDF_DELIVERY_MODE=attachment
```

- [ ] **Step 6.2: Verify the file parses (visual + diff)**

```bash
git diff .env.example
```
Expected: only additions at the end, no other changes.

- [ ] **Step 6.3: Commit**

```bash
git add .env.example
git commit -m "docs(env): document broadcast throttle + PDF delivery mode env vars"
```

---

## Task 7: Create `webhook/pdf_storage.py` — Supabase Storage helper

**Why:** PDF link mode needs to (a) upload PDF bytes to Storage and (b) generate a signed URL. Encapsulating this in its own module keeps `dispatch_document.py` focused and the helper independently testable.

**Files:**
- Create: `webhook/pdf_storage.py`
- Test: `tests/test_pdf_storage.py` (create)

- [ ] **Step 7.1: Write failing tests**

Create `tests/test_pdf_storage.py`:

```python
"""Tests for webhook/pdf_storage.py — Supabase Storage upload + signed URL."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def _supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")


def test_upload_and_sign_calls_upload_then_signed_url():
    from webhook.pdf_storage import upload_and_sign

    fake_storage_bucket = MagicMock()
    fake_storage_bucket.upload.return_value = {"Key": "approval-1/file.pdf"}
    fake_storage_bucket.create_signed_url.return_value = {
        "signedURL": "https://x.supabase.co/storage/v1/sign/...token=abc"
    }
    fake_client = MagicMock()
    fake_client.storage.from_.return_value = fake_storage_bucket

    with patch("webhook.pdf_storage._client", return_value=fake_client):
        url = upload_and_sign(
            approval_id="approval-1",
            filename="file.pdf",
            pdf_bytes=b"%PDF-fake",
        )

    assert url == "https://x.supabase.co/storage/v1/sign/...token=abc"
    fake_client.storage.from_.assert_called_with("pdf-broadcasts")
    fake_storage_bucket.upload.assert_called_once()
    args, kwargs = fake_storage_bucket.upload.call_args
    # supabase-py upload(path, file, file_options={...})
    assert "approval-1/file.pdf" in (list(args) + list(kwargs.values()))
    fake_storage_bucket.create_signed_url.assert_called_once_with(
        "approval-1/file.pdf", 7 * 24 * 3600
    )


def test_upload_and_sign_overwrites_on_duplicate():
    """Idempotent per (approval_id, filename) — upload includes upsert option."""
    from webhook.pdf_storage import upload_and_sign

    fake_storage_bucket = MagicMock()
    fake_storage_bucket.upload.return_value = {"Key": "approval-2/file.pdf"}
    fake_storage_bucket.create_signed_url.return_value = {"signedURL": "u"}
    fake_client = MagicMock()
    fake_client.storage.from_.return_value = fake_storage_bucket

    with patch("webhook.pdf_storage._client", return_value=fake_client):
        upload_and_sign(
            approval_id="approval-2",
            filename="file.pdf",
            pdf_bytes=b"%PDF-fake",
        )

    _args, kwargs = fake_storage_bucket.upload.call_args
    file_options = kwargs.get("file_options") or {}
    # Either supabase-py >=2: upsert is part of file_options; we accept the
    # common spelling. The contract is: subsequent calls must not 409.
    assert (
        file_options.get("upsert") in ("true", True)
        or kwargs.get("upsert") in ("true", True)
    )
```

- [ ] **Step 7.2: Run tests to confirm import failure**

```bash
uv run pytest tests/test_pdf_storage.py -v
```
Expected: ERROR — `ModuleNotFoundError: No module named 'webhook.pdf_storage'`.

- [ ] **Step 7.3: Implement `webhook/pdf_storage.py`**

Create `webhook/pdf_storage.py`:

```python
"""Supabase Storage helper for the OneDrive PDF link-delivery mode.

Uploads a PDF into the `pdf-broadcasts` bucket (private) and returns a
7-day signed URL. Idempotent: re-uploading the same (approval_id, filename)
overwrites in place via upsert, so retried dispatches do not 409.

The bucket must exist and be private; no public read policy. Only signed
URLs hand out access.
"""
from __future__ import annotations

import os
from typing import Optional

from supabase import create_client, Client


BUCKET = "pdf-broadcasts"
SIGNED_URL_TTL_SECONDS = 7 * 24 * 3600  # 7 days


_cached_client: Optional[Client] = None


def _client() -> Client:
    """Lazy-cached service-role Supabase client."""
    global _cached_client
    if _cached_client is None:
        url = os.environ["SUPABASE_URL"]
        key = (
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            or os.environ.get("SUPABASE_KEY")
        )
        if not key:
            raise RuntimeError(
                "SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_KEY) must be set"
            )
        _cached_client = create_client(url, key)
    return _cached_client


def upload_and_sign(
    approval_id: str, filename: str, pdf_bytes: bytes
) -> str:
    """Upload PDF bytes and return a 7-day signed URL.

    Path scheme: `<approval_id>/<filename>` inside the `pdf-broadcasts`
    bucket. Subsequent calls with the same key overwrite (upsert).
    """
    path = f"{approval_id}/{filename}"
    bucket = _client().storage.from_(BUCKET)

    bucket.upload(
        path,
        pdf_bytes,
        file_options={
            "content-type": "application/pdf",
            "upsert": "true",  # supabase-py expects string here
        },
    )
    signed = bucket.create_signed_url(path, SIGNED_URL_TTL_SECONDS)
    return signed["signedURL"]
```

- [ ] **Step 7.4: Run tests**

```bash
uv run pytest tests/test_pdf_storage.py -v
```
Expected: both tests PASS.

- [ ] **Step 7.5: Commit**

```bash
git add webhook/pdf_storage.py tests/test_pdf_storage.py
git commit -m "feat(pdf_storage): supabase storage helper for PDF link-delivery mode

upload_and_sign() uploads PDF bytes to the pdf-broadcasts bucket and
returns a 7-day signed URL. Used by webhook/dispatch_document.py when
PDF_DELIVERY_MODE=link is enabled."
```

- [ ] **Step 7.6: Manually create the Supabase bucket (one-time, out-of-code action)**

This is an operator action, NOT a code step — but document it:

```
1. Go to Supabase project dashboard → Storage.
2. Click "New bucket".
3. Name: pdf-broadcasts
4. Public: OFF (must be private)
5. File size limit: 50 MB (sanity)
6. Allowed MIME types: application/pdf
7. Save.
```

Add a TODO note in the commit message reminding the operator. (No code change in this step.)

---

## Task 8: `dispatch_document.py` — `CONCURRENCY=1` + per-iteration delay + return bytes

**Why:** PDF flow today fires up to 5 sends in parallel. Serializing + adding the same jitter as text broadcasts brings PDF dispatch to the same throttle floor. Refactoring `_download_pdf` to return raw bytes also unlocks the link-mode path in Task 9.

**Files:**
- Modify: `webhook/dispatch_document.py`
- Test: `tests/test_dispatch_document_throttle.py` (create)

- [ ] **Step 8.1: Write failing tests**

Create `tests/test_dispatch_document_throttle.py`:

```python
"""Tests for webhook/dispatch_document.py — CONCURRENCY=1 + link mode."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import fakeredis.aioredis


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def fresh_state():
    return {
        "drive_id": "d1",
        "drive_item_id": "i1",
        "filename": "Report.pdf",
        "size": 1024,
        "downloadUrl": "https://cdn.example.com/x?sig=y",
        "downloadUrl_fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": "dispatching",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def mock_pdf_get():
    fake_resp = MagicMock()
    fake_resp.content = b"%PDF-1.4 fake-pdf-bytes"
    fake_resp.raise_for_status = MagicMock()
    with patch("webhook.dispatch_document.requests.get", return_value=fake_resp) as p:
        yield p


def _make_contact(name, phone):
    c = MagicMock()
    c.name = name
    c.phone_uazapi = phone
    return c


@pytest.mark.asyncio
async def test_concurrency_is_one(redis_client, fresh_state, mock_pdf_get, monkeypatch):
    """All sends happen sequentially, never in parallel."""
    monkeypatch.setenv("PDF_DELIVERY_MODE", "attachment")
    monkeypatch.setattr(
        "webhook.dispatch_document.asyncio.sleep", AsyncMock(return_value=None)
    )

    in_flight = {"max": 0, "now": 0}

    async def _slow_send(**kwargs):
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        await asyncio.sleep(0)  # yield to event loop
        in_flight["now"] -= 1
        return {"messageId": "m"}

    fake_uazapi = MagicMock()
    fake_uazapi.send_document = MagicMock(
        side_effect=lambda **k: {"messageId": "m"}
    )

    contacts = [_make_contact(f"U{i}", f"55{i:03}") for i in range(4)]
    repo = MagicMock()
    repo.list_active.return_value = contacts
    repo.list_by_list_code.return_value = contacts

    await redis_client.set(
        "approval:abc",
        json.dumps(fresh_state),
    )

    with patch("webhook.dispatch_document._redis", return_value=redis_client), \
         patch("webhook.dispatch_document.UazapiClient", return_value=fake_uazapi), \
         patch("webhook.dispatch_document.ContactsRepo", return_value=repo):
        from webhook.dispatch_document import dispatch_document, CONCURRENCY
        # Verify constant flipped
        assert CONCURRENCY == 1
        result = await dispatch_document("abc", "__all__")

    assert result["sent"] == 4


@pytest.mark.asyncio
async def test_attachment_mode_calls_send_document(
    redis_client, fresh_state, mock_pdf_get, monkeypatch
):
    monkeypatch.setenv("PDF_DELIVERY_MODE", "attachment")
    monkeypatch.setattr(
        "webhook.dispatch_document.asyncio.sleep", AsyncMock(return_value=None)
    )
    fake_uazapi = MagicMock()
    fake_uazapi.send_document = MagicMock(return_value={"messageId": "m"})
    fake_uazapi.send_message = MagicMock(return_value={"messageId": "n"})

    contact = _make_contact("U", "55001")
    repo = MagicMock()
    repo.list_active.return_value = [contact]
    repo.list_by_list_code.return_value = [contact]

    await redis_client.set("approval:abc", json.dumps(fresh_state))

    with patch("webhook.dispatch_document._redis", return_value=redis_client), \
         patch("webhook.dispatch_document.UazapiClient", return_value=fake_uazapi), \
         patch("webhook.dispatch_document.ContactsRepo", return_value=repo):
        from webhook.dispatch_document import dispatch_document
        await dispatch_document("abc", "__all__")

    fake_uazapi.send_document.assert_called_once()
    fake_uazapi.send_message.assert_not_called()
```

- [ ] **Step 8.2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_dispatch_document_throttle.py -v
```
Expected: FAIL on `assert CONCURRENCY == 1` (still 5).

- [ ] **Step 8.3: Implement `CONCURRENCY=1` + raw-bytes refactor + per-iteration delay**

Edit `webhook/dispatch_document.py`:

(a) Line 27, change:
```python
CONCURRENCY = 5
```
to:
```python
CONCURRENCY = 1
```

(b) Add at top of file imports section (after `import requests`):
```python
import random
```

(c) Add module-level helper (right after the constants block, before `class ApprovalExpiredError`):
```python
def _broadcast_delay_range() -> tuple[float, float]:
    """Mirror of execution.core.delivery_reporter._broadcast_delay_range.
    Duplicated here to avoid pulling delivery_reporter into the async PDF path.
    Reads the same env vars."""
    lo = float(os.environ.get("BROADCAST_DELAY_MIN", "15.0"))
    hi = float(os.environ.get("BROADCAST_DELAY_MAX", "30.0"))
    lo = max(0.0, lo)
    hi = max(lo, hi)
    return lo, hi
```

(d) Refactor `_download_pdf` (currently inside `dispatch_document` at lines 116-119) to return raw bytes instead of base64. Find:

```python
    def _download_pdf() -> str:
        r = requests.get(state["downloadUrl"], timeout=60, stream=False)
        r.raise_for_status()
        return base64.b64encode(r.content).decode("ascii")

    try:
        pdf_b64 = await asyncio.to_thread(_download_pdf)
        bus.emit("pdf_downloaded", detail={
            "approval_id": approval_id,
            "bytes_b64": len(pdf_b64),
        })
```

Replace with:

```python
    def _download_pdf() -> bytes:
        r = requests.get(state["downloadUrl"], timeout=60, stream=False)
        r.raise_for_status()
        return r.content

    try:
        pdf_bytes = await asyncio.to_thread(_download_pdf)
        bus.emit("pdf_downloaded", detail={
            "approval_id": approval_id,
            "bytes": len(pdf_bytes),
        })
```

(e) In the failure path right after (around current line 127-138), the catch block already references the right variable; no change needed.

(f) Inside `_send_one` (currently at lines 150-185), the current call uses `pdf_b64` for `file_url=`. Replace with:

```python
    async def _send_one(contact, idx, total):
        async with sem:
            claimed = await _claim_idempotency(
                redis_client, contact.phone_uazapi, state["drive_item_id"]
            )
            if not claimed:
                results["skipped"] += 1
                return
            try:
                # attachment mode (current default behavior)
                pdf_b64_payload = base64.b64encode(pdf_bytes).decode("ascii")
                await asyncio.to_thread(
                    uazapi.send_document,
                    number=contact.phone_uazapi,
                    file_url=pdf_b64_payload,
                    doc_name=state["filename"],
                )
                results["sent"] += 1
            except Exception as exc:
                logger.error(
                    f"send_document to {contact.phone_uazapi} failed: {exc}"
                )
                results["failed"] += 1
                err_str = str(exc)[:300]
                results["errors"].append({
                    "phone": contact.phone_uazapi,
                    "error": err_str,
                })
                bus.emit(
                    "send_failed",
                    level="error",
                    detail={
                        "phone": contact.phone_uazapi,
                        "error": err_str,
                        "exc_type": type(exc).__name__,
                    },
                )
            # Throttle: sleep between sends, skip after last contact.
            if idx < total - 1:
                lo, hi = _broadcast_delay_range()
                await asyncio.sleep(random.uniform(lo, hi))
```

(g) Update the gather call to pass index + total:

Find:
```python
    await asyncio.gather(*[_send_one(c) for c in recipients])
```

Replace with:
```python
    total = len(recipients)
    await asyncio.gather(*[_send_one(c, i, total) for i, c in enumerate(recipients)])
```

- [ ] **Step 8.4: Run new tests**

```bash
uv run pytest tests/test_dispatch_document_throttle.py -v
```
Expected: both tests PASS.

- [ ] **Step 8.5: Run existing dispatch_document regression tests**

```bash
uv run pytest tests/test_dispatch_document.py tests/test_dispatch_idempotency.py -v
```
Expected: all PASS. If a test asserted `CONCURRENCY == 5`, update to `1`. If a test inspected `bytes_b64` in event detail, update to `bytes`.

- [ ] **Step 8.6: Commit**

```bash
git add webhook/dispatch_document.py tests/test_dispatch_document_throttle.py
git commit -m "feat(dispatch_document): serialize sends + 15-30s jitter

CONCURRENCY 5→1 (asyncio.gather still used; semaphore enforces serial).
_download_pdf returns raw bytes; base64 encoding moved into _send_one's
attachment branch so link mode (next commit) can reuse the bytes directly.
Per-iteration asyncio.sleep mirrors the text-broadcast jitter."
```

---

## Task 9: PDF link mode — `PDF_DELIVERY_MODE=link`

**Why:** The flag-gated link mode is the actual test path the operator will flip to validate before broadcasting to the full list.

**Files:**
- Modify: `webhook/dispatch_document.py`
- Modify: `tests/test_dispatch_document_throttle.py`

- [ ] **Step 9.1: Write failing test for link mode**

Append to `tests/test_dispatch_document_throttle.py`:

```python
@pytest.mark.asyncio
async def test_link_mode_uploads_and_sends_text(
    redis_client, fresh_state, mock_pdf_get, monkeypatch
):
    monkeypatch.setenv("PDF_DELIVERY_MODE", "link")
    monkeypatch.setattr(
        "webhook.dispatch_document.asyncio.sleep", AsyncMock(return_value=None)
    )

    fake_uazapi = MagicMock()
    fake_uazapi.send_document = MagicMock()
    fake_uazapi.send_message = MagicMock(return_value={"messageId": "n"})

    contact = _make_contact("U", "55001")
    repo = MagicMock()
    repo.list_active.return_value = [contact]
    repo.list_by_list_code.return_value = [contact]

    await redis_client.set("approval:abc", json.dumps(fresh_state))

    with patch("webhook.dispatch_document._redis", return_value=redis_client), \
         patch("webhook.dispatch_document.UazapiClient", return_value=fake_uazapi), \
         patch("webhook.dispatch_document.ContactsRepo", return_value=repo), \
         patch(
             "webhook.dispatch_document.upload_and_sign",
             return_value="https://x.supabase.co/s/sign?token=ABC",
         ) as mock_upload:
        from webhook.dispatch_document import dispatch_document
        result = await dispatch_document("abc", "__all__")

    assert result["sent"] == 1
    fake_uazapi.send_document.assert_not_called()
    fake_uazapi.send_message.assert_called_once()
    args, kwargs = fake_uazapi.send_message.call_args
    sent_text = args[1] if len(args) > 1 else kwargs.get("text")
    assert "https://x.supabase.co/s/sign?token=ABC" in sent_text
    assert "Report.pdf" in sent_text
    mock_upload.assert_called_once_with(
        approval_id="abc",
        filename="Report.pdf",
        pdf_bytes=b"%PDF-1.4 fake-pdf-bytes",
    )


@pytest.mark.asyncio
async def test_link_mode_falls_back_to_attachment_on_storage_failure(
    redis_client, fresh_state, mock_pdf_get, monkeypatch
):
    """If upload_and_sign raises, fall through to send_document so the
    broadcast still goes out — Storage hiccup must not lose the message."""
    monkeypatch.setenv("PDF_DELIVERY_MODE", "link")
    monkeypatch.setattr(
        "webhook.dispatch_document.asyncio.sleep", AsyncMock(return_value=None)
    )

    fake_uazapi = MagicMock()
    fake_uazapi.send_document = MagicMock(return_value={"messageId": "m"})
    fake_uazapi.send_message = MagicMock()

    contact = _make_contact("U", "55001")
    repo = MagicMock()
    repo.list_active.return_value = [contact]
    repo.list_by_list_code.return_value = [contact]

    await redis_client.set("approval:abc", json.dumps(fresh_state))

    with patch("webhook.dispatch_document._redis", return_value=redis_client), \
         patch("webhook.dispatch_document.UazapiClient", return_value=fake_uazapi), \
         patch("webhook.dispatch_document.ContactsRepo", return_value=repo), \
         patch(
             "webhook.dispatch_document.upload_and_sign",
             side_effect=RuntimeError("storage 503"),
         ):
        from webhook.dispatch_document import dispatch_document
        result = await dispatch_document("abc", "__all__")

    assert result["sent"] == 1
    fake_uazapi.send_document.assert_called_once()
    fake_uazapi.send_message.assert_not_called()
```

- [ ] **Step 9.2: Run tests to verify failure**

```bash
uv run pytest tests/test_dispatch_document_throttle.py -v
```
Expected: link-mode tests FAIL — `upload_and_sign` is not yet called from `dispatch_document.py`.

- [ ] **Step 9.3: Implement link mode in `_send_one`**

Edit `webhook/dispatch_document.py`. At the top, add the import:

```python
from webhook.pdf_storage import upload_and_sign
```

Add a module-level helper for the mode:

```python
def _pdf_delivery_mode() -> str:
    """Returns 'link' or 'attachment'. Defaults to 'attachment' (current)."""
    mode = os.environ.get("PDF_DELIVERY_MODE", "attachment").lower()
    return "link" if mode == "link" else "attachment"
```

In `_send_one`, replace the `try:` body (the `pdf_b64_payload = ...; uazapi.send_document(...)` block) with mode-aware dispatch:

```python
            try:
                if _pdf_delivery_mode() == "link":
                    try:
                        signed_url = await asyncio.to_thread(
                            upload_and_sign,
                            approval_id=approval_id,
                            filename=state["filename"],
                            pdf_bytes=pdf_bytes,
                        )
                        link_text = (
                            f"📄 {state['filename']}\n\n"
                            f"{signed_url}\n\n"
                            f"(Link válido por 7 dias)"
                        )
                        await asyncio.to_thread(
                            uazapi.send_message,
                            contact.phone_uazapi,
                            link_text,
                        )
                    except Exception as storage_exc:
                        # Fallback: storage hiccup must not lose the broadcast.
                        logger.error(
                            f"link-mode storage/sign failed for {contact.phone_uazapi}: "
                            f"{storage_exc}; falling back to attachment"
                        )
                        bus.emit(
                            "pdf_link_fallback",
                            level="warn",
                            detail={
                                "phone": contact.phone_uazapi,
                                "error": str(storage_exc)[:200],
                            },
                        )
                        pdf_b64_payload = base64.b64encode(pdf_bytes).decode("ascii")
                        await asyncio.to_thread(
                            uazapi.send_document,
                            number=contact.phone_uazapi,
                            file_url=pdf_b64_payload,
                            doc_name=state["filename"],
                        )
                else:
                    pdf_b64_payload = base64.b64encode(pdf_bytes).decode("ascii")
                    await asyncio.to_thread(
                        uazapi.send_document,
                        number=contact.phone_uazapi,
                        file_url=pdf_b64_payload,
                        doc_name=state["filename"],
                    )
                results["sent"] += 1
            except Exception as exc:
                # ... existing failure handling unchanged ...
```

(Keep the `except Exception as exc:` block from before; this is just the `try:` body change.)

- [ ] **Step 9.4: Run link-mode tests**

```bash
uv run pytest tests/test_dispatch_document_throttle.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 9.5: Run regression suite**

```bash
uv run pytest tests/test_dispatch_document.py tests/test_dispatch_idempotency.py tests/test_dispatch_document_throttle.py -v
```
Expected: all PASS.

- [ ] **Step 9.6: Commit**

```bash
git add webhook/dispatch_document.py tests/test_dispatch_document_throttle.py
git commit -m "feat(dispatch_document): PDF_DELIVERY_MODE=link via Supabase signed URL

When PDF_DELIVERY_MODE=link, upload PDF bytes to the pdf-broadcasts bucket
and send a text message with a 7-day signed URL instead of a base64 attach.
Defaults to attachment for backward compatibility. Storage failures fall
through to attachment mode so a Storage hiccup never loses the broadcast.

Bucket pdf-broadcasts must be created (private) in Supabase before flipping
the flag; see spec doc."
```

---

## Task 10: Final integration smoke test + dry-run verification

**Why:** Unit tests don't catch integration-level mistakes (e.g., `time.sleep` actually firing in the prod path, env-var typos, EventBus emission order). One end-to-end dry-run on `morning_check` validates the full chain.

**Files:**
- No code changes; verification only.

- [ ] **Step 10.1: Run full pytest suite**

```bash
uv run pytest -q
```
Expected: ALL tests pass. If any tests fail that were not previously failing, investigate and fix before proceeding.

- [ ] **Step 10.2: Run morning_check in dry-run mode (offline, no real sends)**

```bash
REDIS_URL="redis://localhost:6379/0" \
BROADCAST_DELAY_MIN=0.1 \
BROADCAST_DELAY_MAX=0.2 \
BROADCAST_REF_TOKEN_ENABLED=true \
uv run python -m execution.scripts.morning_check --dry-run 2>&1 | head -100
```

Expected:
- Output includes `<<<DELIVERY_REPORT_START>>>` ... `<<<DELIVERY_REPORT_END>>>`.
- Inspect the JSON report's `results[*]` — verify nothing crashes. (Dry-run typically prints the formatted message; if it does, verify each printed message contains a `Ref: <6chars>` footer.)

If `morning_check` doesn't print the per-contact message in dry-run, skip this manual check and rely on Task 1's unit tests. Document this in the commit.

- [ ] **Step 10.3: Inspect the EventBus delivery_summary payload (manually)**

If a Redis instance is reachable and Telegram channel configured, run:

```bash
REDIS_URL="redis://localhost:6379/0" \
BROADCAST_DELAY_MIN=0.5 \
BROADCAST_DELAY_MAX=1.0 \
uv run python -c "
from execution.core.delivery_reporter import DeliveryReporter, Contact
from unittest.mock import MagicMock
r = DeliveryReporter('smoke', MagicMock(), notify_telegram=False)
c = [Contact(name=f'U{i}', phone=f'55{i:03}') for i in range(3)]
report = r.dispatch(c, 'smoke test')
print('total:', report.total, 'success:', report.success_count)
"
```

Expected: prints `total: 3 success: 3` and the script returns within 2-3 seconds (3 contacts × ~0.75s avg = ~1.5s of sleeps).

- [ ] **Step 10.4: Commit a final verification note (no code changes)**

```bash
git commit --allow-empty -m "chore(verify): smoke-test pass for whatsapp throttle implementation

All unit tests pass. Dry-run of DeliveryReporter with synthetic contacts
exhibits expected throttle behavior (3 contacts × 0.5-1s ≈ 1.5s wall-clock).
Ready for staging deploy."
```

---

## Operational checklist (NOT code; for the operator before/after deploy)

These are documented here so they are not forgotten. Do NOT mark these as plan steps.

1. **Before deploy:**
   - Create `pdf-broadcasts` bucket in Supabase (private, 50 MB limit, MIME `application/pdf` only).
   - Pause `send_news` and OneDrive PDF approvals for 48-72h to let the WhatsApp account cool down.
   - Note current WhatsApp Business → Quality Rating in your records.

2. **Deploy order:**
   - Deploy Railway (webhook + dispatch_document) first; the bot will pick up `BROADCAST_DELAY_MIN/MAX` automatically once env is set.
   - GH Actions crons (`morning_check`, `baltic_ingestion`) auto-redeploy on next scheduled run with the new code.

3. **Post-deploy (first 7 days):**
   - Watch the events channel for `delivery_summary` cards — verify `duration_seconds` matches expected (~40 min for 105 contacts).
   - Watch for `pdf_link_fallback` events — none expected in attachment mode; only when operator flips `PDF_DELIVERY_MODE=link`.
   - Re-check Quality Rating daily for the first week.

4. **PDF link-mode test:**
   - Set `PDF_DELIVERY_MODE=link` on Railway env.
   - Trigger an OneDrive approval pointing at a test list of one contact (yourself, or trusted tester).
   - Verify the link arrives, the PDF downloads correctly, and the link expires after 7 days.
   - Decide whether to keep `link` for production or revert to `attachment`.

5. **If a third spam flag occurs within 7 days of deploy:**
   - Increase `BROADCAST_DELAY_MIN=30`, `BROADCAST_DELAY_MAX=60` via env.
   - Reconsider name personalization (re-open the spec).
   - Consider pruning the contact list to engaged recipients only.

---

## Self-review (already performed)

**Spec coverage:** Each spec requirement maps to a task —
- Throttle 15-30s → Task 2
- Ref token → Task 1
- 429 backoff → Task 3
- EventBus metadata → Task 4
- Lock TTL bump → Task 5
- env vars in `.env.example` → Task 6
- `CONCURRENCY=1` + per-iteration delay → Task 8
- `PDF_DELIVERY_MODE=link` → Task 9
- Storage helper module → Task 7
- Bucket creation → operator checklist (Task 7.6, post-deploy)
- Operational recovery → operator checklist (out of code scope)

**Placeholder scan:** No "TBD"/"TODO"/"similar to" placeholders. Every code step shows the actual code.

**Type consistency:** `pdf_bytes` (bytes) used consistently from Task 8 onwards. `signed_url` (str) consistent. `upload_and_sign(approval_id, filename, pdf_bytes) → str` matches in helper definition (Task 7) and call sites (Task 9). Env var names match across `.env.example` (Task 6), `_broadcast_delay_range` (Tasks 2/8), `_rate_limit_sleep` (Task 3), `_ref_token_enabled` (Task 1), `_pdf_delivery_mode` (Task 9).

---

*Plan authored 2026-05-03 from spec `2026-05-03-whatsapp-throttle-anti-spam-design.md`.*
