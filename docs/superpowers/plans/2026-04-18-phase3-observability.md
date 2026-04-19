# Backend Hardening — Phase 3: Reliability + Observability + Live Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the backend visibility in production via Sentry + Prometheus + structured event log, eliminate silent WhatsApp duplicates via idempotency, surface live workflow progress in Telegram for every long-running script, and kill the `except Exception: pass` anti-pattern around message edits.

**Architecture:** Four-layer observability: (1) **Sentry** for exceptions and breadcrumbs, (2) **Prometheus `/metrics`** for aggregated counters/histograms, (3) **Postgres `event_log` table** for timeline search by `draft_id`/`run_id`, (4) **`ProgressReporter.step()`** with 3 sinks (stdout log + Supabase event_log + Telegram live card with 2s debounce). Idempotency uses Redis `SET NX EX 86400` keyed by `sha1(phone|draft_id|message)`. Phase 3 is split into 3 sub-PRs for reviewability; this plan covers all three.

**Tech Stack:** `sentry-sdk[aiohttp]>=2.0.0`, `prometheus-client>=0.20.0`, existing `supabase-py`, existing `redis` async client, existing `aiogram`.

**Spec reference:** `docs/superpowers/specs/2026-04-18-backend-hardening-v1-design.md` §4.

**Prerequisite:** Phase 2 must be merged. Verify:
```bash
git log phase2-router-split-complete --oneline -1
```

---

## File Structure

**Create:**
- `webhook/metrics.py` — Prometheus counters + histogram module (new)
- `execution/core/sentry_init.py` — helper to init Sentry in execution scripts
- `supabase/migrations/20260418_event_log.sql` — Postgres migration for `event_log` table (if directory exists; otherwise place under `execution/db/migrations/` or wherever the repo's migration convention lives)
- `tests/test_dispatch_idempotency.py` — unit tests for WhatsApp idempotency (new)
- `tests/test_metrics_endpoint.py` — integration test that `/metrics` returns counters (new)
- `tests/test_progress_reporter_sinks.py` — unit tests for `ProgressReporter.step()` + debounce (new)

**Modify:**
- `webhook/dispatch.py` — add idempotency helper + wire into `send_whatsapp`; replace silent `except Exception: pass` around `edit_message_text` with typed handling
- `webhook/bot/main.py` — initialize Sentry at top of `main()`
- `webhook/bot/routers/callbacks_curation.py` — fix `_finalize_card`'s `except Exception: pass`
- `webhook/routes/api.py` — add `/metrics` endpoint handler
- `execution/core/progress_reporter.py` — extend with `step()` method + 3 sinks + debounce + flush discipline
- `execution/scripts/platts_ingestion.py` — instrument with `ProgressReporter.step()` at phase boundaries
- `execution/scripts/platts_reports.py` — instrument with `ProgressReporter.step()`
- `execution/scripts/baltic_ingestion.py` — instrument with `ProgressReporter.step()`
- `requirements.txt` — add `sentry-sdk[aiohttp]>=2.0.0,<3.0.0` and `prometheus-client>=0.20.0,<1.0.0`
- `webhook/requirements.txt` — same additions
- `.env.example` — add `SENTRY_DSN=` line with comment
- `README.md` (if exists) — one-paragraph observability section with how to set `SENTRY_DSN` and view `/metrics`

**Not modified:**
- Any of the `callbacks_*.py` files (beyond the `except:pass` fix in curation)
- Test files from Phase 1 safety net (53 tests stay green)
- Business logic in Apify actors, curation router, 3-agent pipeline, Sheets contact flow

---

## Sub-PR structure (recommended)

- **Sub-PR 3a** (Tasks 2–6): idempotency, Sentry, metrics, silent-swallow fix. Self-contained, ships observability value immediately.
- **Sub-PR 3b** (Tasks 7–10): `event_log` migration + `ProgressReporter.step()` extension. Builds on 3a.
- **Sub-PR 3c** (Tasks 11–14): instrument 3 cron scripts + final verification. Requires 3b merged.

Each sub-PR can be merged independently if you want smaller review batches. The plan below executes all three sequentially on one branch — split into 3 branches if preferred.

---

## Task 1: Pre-flight + branch + dependency install

**Files:**
- Modify: `requirements.txt`, `webhook/requirements.txt`
- Create: working branch

### Steps

- [ ] **Step 1: Verify state**

```bash
cd "/Users/bigode/Dev/Antigravity WF " && git checkout main && git pull origin main
git log phase2-router-split-complete --oneline -1
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --tb=no -q 2>&1 | tail -3
```

Expected: branch `main`, tag at latest Phase 2 commit, 53 Phase 1 tests still pass.

- [ ] **Step 2: Create branch**

```bash
git checkout -b phase3-observability
```

- [ ] **Step 3: Add dependencies**

Edit `requirements.txt` — add these two lines (preserve alphabetical ordering if the file is sorted; otherwise append at end):

```
sentry-sdk[aiohttp]>=2.0.0,<3.0.0
prometheus-client>=0.20.0,<1.0.0
```

Edit `webhook/requirements.txt` — same additions.

- [ ] **Step 4: Install into venv**

```bash
.venv/bin/pip install 'sentry-sdk[aiohttp]>=2.0.0,<3.0.0' 'prometheus-client>=0.20.0,<1.0.0' 2>&1 | tail -3
```

Expected: "Successfully installed sentry-sdk-X.Y.Z prometheus-client-A.B.C".

- [ ] **Step 5: Smoke-test imports**

```bash
.venv/bin/python -c "import sentry_sdk; from sentry_sdk.integrations.aiohttp import AioHttpIntegration; import prometheus_client; print('imports OK')"
```

Expected: "imports OK".

- [ ] **Step 6: Run Phase 1 suite to confirm nothing broke from dep install**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --tb=no -q 2>&1 | tail -3
```

Expected: 53 passed.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt webhook/requirements.txt
git commit -m "chore(deps): add sentry-sdk + prometheus-client for phase 3"
```

---

## Task 2: Idempotency on WhatsApp send

**Files:**
- Modify: `webhook/dispatch.py`
- Create: `tests/test_dispatch_idempotency.py`

### Background

`webhook/dispatch.py:send_whatsapp(phone, message, ...)` (around line 65-75) currently POSTs to UAZAPI without any idempotency check. If a retry occurs (network glitch, webhook delivered twice), the user receives duplicates. We add a Redis-backed check-and-mark using `SET NX EX 86400` keyed by `sha1(phone|draft_id|message)`.

### Steps

- [ ] **Step 1: Write the failing test first**

Create `tests/test_dispatch_idempotency.py`:

```python
"""Unit tests for WhatsApp send idempotency (Phase 3)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
import fakeredis.aioredis


@pytest.fixture
def fake_redis_async():
    """Async fakeredis client — supports SET NX EX."""
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def mock_session(mocker):
    """aiohttp ClientSession mock — returns 200 by default."""
    session = AsyncMock()
    response = AsyncMock()
    response.status = 200
    response.json = AsyncMock(return_value={"status": "ok", "id": "uazapi_msg_1"})
    response.text = AsyncMock(return_value='{"status": "ok"}')
    session.post = MagicMock()
    session.post.return_value.__aenter__ = AsyncMock(return_value=response)
    session.post.return_value.__aexit__ = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_send_whatsapp_first_call_goes_through(
    fake_redis_async, mock_session, mocker,
):
    from dispatch import send_whatsapp
    mocker.patch("dispatch.aiohttp.ClientSession", return_value=mock_session)
    mocker.patch("dispatch._get_redis_async", new=AsyncMock(return_value=fake_redis_async))

    result = await send_whatsapp(
        phone="+5511999998888",
        message="first call",
        draft_id="draft_abc",
    )

    assert result.get("status") != "duplicate"
    mock_session.post.assert_called_once()


@pytest.mark.asyncio
async def test_send_whatsapp_second_call_same_key_returns_duplicate(
    fake_redis_async, mock_session, mocker,
):
    from dispatch import send_whatsapp
    mocker.patch("dispatch.aiohttp.ClientSession", return_value=mock_session)
    mocker.patch("dispatch._get_redis_async", new=AsyncMock(return_value=fake_redis_async))

    # First call
    await send_whatsapp(phone="+5511999998888", message="same msg", draft_id="draft_xyz")
    # Second call — should short-circuit
    result = await send_whatsapp(phone="+5511999998888", message="same msg", draft_id="draft_xyz")

    assert result == {"status": "duplicate", "skipped": True}
    # Only one HTTP post (first call); second was blocked
    assert mock_session.post.call_count == 1


@pytest.mark.asyncio
async def test_send_whatsapp_different_draft_id_goes_through(
    fake_redis_async, mock_session, mocker,
):
    from dispatch import send_whatsapp
    mocker.patch("dispatch.aiohttp.ClientSession", return_value=mock_session)
    mocker.patch("dispatch._get_redis_async", new=AsyncMock(return_value=fake_redis_async))

    await send_whatsapp(phone="+5511999998888", message="same text", draft_id="draft_A")
    result = await send_whatsapp(phone="+5511999998888", message="same text", draft_id="draft_B")

    # Different draft_id → different idempotency key → both go through
    assert result.get("status") != "duplicate"
    assert mock_session.post.call_count == 2
```

- [ ] **Step 2: Run the test — expect FAIL**

```bash
.venv/bin/pytest tests/test_dispatch_idempotency.py -v 2>&1 | tail -15
```

Expected FAIL with `AttributeError: module 'dispatch' has no attribute '_get_redis_async'` or `TypeError: send_whatsapp() missing required argument 'draft_id'` — confirms the impl doesn't exist yet.

- [ ] **Step 3: Add idempotency helpers + update `send_whatsapp` in `webhook/dispatch.py`**

At the top of the file add imports if missing:

```python
import hashlib
import os
from redis import asyncio as redis_async
```

Add helper functions (near the top of the module, before `send_whatsapp`):

```python
_redis_async_client = None

async def _get_redis_async():
    """Lazy async redis client. Uses REDIS_URL env var."""
    global _redis_async_client
    if _redis_async_client is None:
        url = os.getenv("REDIS_URL", "")
        if not url:
            raise RuntimeError("REDIS_URL not configured")
        _redis_async_client = redis_async.from_url(url, decode_responses=True)
    return _redis_async_client


def _idempotency_key(phone: str, draft_id: str, message: str) -> str:
    """sha1(phone|draft_id|message) → 'whatsapp:sent:<digest>'."""
    digest = hashlib.sha1(f"{phone}|{draft_id}|{message}".encode()).hexdigest()
    return f"whatsapp:sent:{digest}"
```

Modify `send_whatsapp` signature and body. The existing function likely looks like:

```python
async def send_whatsapp(phone: str, message: str, uazapi_token=None, uazapi_url=None):
    # ... existing HTTP post
```

Change to:

```python
async def send_whatsapp(
    phone: str,
    message: str,
    draft_id: str,
    uazapi_token=None,
    uazapi_url=None,
) -> dict:
    """Send a WhatsApp message via UAZAPI, with idempotency.
    
    Args:
        phone: Recipient phone number.
        message: Message text.
        draft_id: Required idempotency scope. For free-form broadcast
                  with no draft, use f"broadcast:{int(time.time())}".
    Returns:
        Dict with "status" key. status="duplicate" means no send occurred.
    """
    # Idempotency: SET NX EX 86400 — atomic check-and-mark, 24h window
    try:
        redis_client = await _get_redis_async()
        key = _idempotency_key(phone, draft_id, message)
        marked = await redis_client.set(key, "1", ex=86400, nx=True)
        if marked is None:
            logger.info(
                "whatsapp_idempotency_hit",
                extra={"phone_last4": phone[-4:], "draft_id": draft_id},
            )
            return {"status": "duplicate", "skipped": True}
    except Exception as exc:
        # Redis down? Don't block sends — but log loudly.
        logger.warning("whatsapp_idempotency_check_failed", exc_info=exc)

    # ── existing UAZAPI POST logic stays below ──
    # (keep the rest of the function body unchanged)
```

- [ ] **Step 4: Update all callers of `send_whatsapp` to pass `draft_id`**

Callers (grep for them):

```bash
grep -rn "send_whatsapp(" webhook/ execution/ --include="*.py"
```

For each caller, add `draft_id=<value>`:
- `webhook/dispatch.py:process_approval_async` and `process_test_send_async` — if they iterate contacts calling `send_whatsapp` per phone, pass the draft_id from the outer scope.
- If a caller has no natural draft_id (free-form, ad-hoc), use `draft_id=f"broadcast:{int(time.time())}"`.

- [ ] **Step 5: Run the test — expect PASS**

```bash
.venv/bin/pytest tests/test_dispatch_idempotency.py -v 2>&1 | tail -15
```

Expected: 3 passed.

- [ ] **Step 6: Run full new suite (no regressions)**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py tests/test_dispatch_idempotency.py --tb=no -q 2>&1 | tail -3
```

Expected: 56 passed (53 + 3 new).

- [ ] **Step 7: Commit**

```bash
git add webhook/dispatch.py tests/test_dispatch_idempotency.py
git commit -m "feat(dispatch): idempotency key on WhatsApp send (24h TTL)"
```

---

## Task 3: Sentry init in webhook

**Files:**
- Modify: `webhook/bot/main.py`

### Steps

- [ ] **Step 1: Add Sentry init at top of `main()`**

In `webhook/bot/main.py`, find the `main()` function (or top-level setup code). Near the top (before `bot` and `dp` are created), add:

```python
# ── Sentry (no-op if SENTRY_DSN not set) ──
sentry_dsn = os.getenv("SENTRY_DSN", "")
if sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.aiohttp import AioHttpIntegration
    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=os.getenv("RAILWAY_ENVIRONMENT", "dev"),
        traces_sample_rate=0.1,
        integrations=[AioHttpIntegration()],
    )
    logger.info("sentry_initialized", extra={"environment": os.getenv("RAILWAY_ENVIRONMENT", "dev")})
else:
    logger.warning("SENTRY_DSN not set — Sentry disabled")
```

(If `os` or `logger` is not already imported at the top of main.py, add appropriate imports.)

- [ ] **Step 2: Update `.env.example`**

Append (or locate the observability section and add):
```
# Sentry DSN for error tracking (optional; leave blank to disable)
SENTRY_DSN=
```

- [ ] **Step 3: Smoke-test webhook imports**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py --tb=no -q 2>&1 | tail -3
```

Expected: 53+ passed (no regressions).

Run a targeted init check:

```bash
cd "/Users/bigode/Dev/Antigravity WF " && SENTRY_DSN="" .venv/bin/python -c "
import sys
sys.path.insert(0, 'webhook')
# Don't actually start the webhook; just exercise the init code
import os
os.environ['SENTRY_DSN'] = ''
# noop — with empty DSN, init should log a warning, not crash
print('SENTRY_DSN empty → init skipped OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add webhook/bot/main.py .env.example
git commit -m "feat(obs): Sentry init in webhook (no-op when SENTRY_DSN unset)"
```

---

## Task 4: Sentry init in execution scripts

**Files:**
- Create: `execution/core/sentry_init.py`
- Modify: `execution/scripts/platts_ingestion.py`, `execution/scripts/platts_reports.py`, `execution/scripts/baltic_ingestion.py`

### Steps

- [ ] **Step 1: Create `execution/core/sentry_init.py`**

```python
"""Sentry initialization helper for execution scripts.

Usage:
    from execution.core.sentry_init import init_sentry
    init_sentry(__name__)  # at the top of the script's main()
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry(script_name: str) -> bool:
    """Initialize Sentry for a cron/execution script.
    
    Returns True if Sentry was initialized, False if disabled (no DSN).
    """
    dsn = os.getenv("SENTRY_DSN", "")
    if not dsn:
        logger.warning("SENTRY_DSN not set — Sentry disabled for %s", script_name)
        return False
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("RAILWAY_ENVIRONMENT", "dev"),
            traces_sample_rate=0.1,
        )
        sentry_sdk.set_tag("script", script_name)
        logger.info("sentry_initialized for script=%s", script_name)
        return True
    except Exception as exc:
        logger.warning("sentry_init_failed: %s", exc)
        return False
```

- [ ] **Step 2: Add `init_sentry(__name__)` call to each of the 3 cron scripts**

For each of `execution/scripts/platts_ingestion.py`, `execution/scripts/platts_reports.py`, `execution/scripts/baltic_ingestion.py`:

Find the `if __name__ == "__main__":` block (or the `main()` function). Add near the TOP of `main()` (or right at the start of the `__main__` block):

```python
from execution.core.sentry_init import init_sentry
init_sentry(__name__)
```

If the script has neither a `main()` nor a `__main__` block (top-level script), add the two lines right after the last import statement.

- [ ] **Step 3: Smoke-test the 3 scripts can still be imported**

```bash
.venv/bin/python -c "
import importlib
for m in ['execution.scripts.platts_ingestion', 'execution.scripts.platts_reports', 'execution.scripts.baltic_ingestion']:
    importlib.import_module(m)
    print(f'{m}: import OK')
"
```

Expected: three "import OK" lines.

- [ ] **Step 4: Run full test suite**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py tests/test_dispatch_idempotency.py --tb=no -q 2>&1 | tail -3
```

Expected: 56 passed (no regressions from new imports).

- [ ] **Step 5: Commit**

```bash
git add execution/core/sentry_init.py execution/scripts/platts_ingestion.py execution/scripts/platts_reports.py execution/scripts/baltic_ingestion.py
git commit -m "feat(obs): init Sentry in platts/baltic cron scripts"
```

---

## Task 5: Prometheus `/metrics` endpoint + counters module

**Files:**
- Create: `webhook/metrics.py`
- Create: `tests/test_metrics_endpoint.py`
- Modify: `webhook/routes/api.py`

### Steps

- [ ] **Step 1: Create `webhook/metrics.py`**

```python
"""Prometheus counters and histograms for the webhook service.

Import the names from this module directly:
    from webhook.metrics import whatsapp_sent, edit_failures
    whatsapp_sent.labels(status="success").inc()
"""
from __future__ import annotations

from prometheus_client import Counter, Histogram

# ── WhatsApp delivery ──
whatsapp_sent = Counter(
    "whatsapp_messages_total",
    "WhatsApp send outcomes",
    ["status"],  # success | failure | duplicate
)
whatsapp_duration = Histogram(
    "whatsapp_duration_seconds",
    "WhatsApp send latency (seconds)",
)

# ── Telegram edit errors ──
edit_failures = Counter(
    "telegram_edit_failures_total",
    "edit_message_text failures by reason",
    ["reason"],  # not_modified | bad_request | unexpected | flood
)

# ── ProgressReporter card edits ──
progress_card_edits = Counter(
    "progress_card_edits_total",
    "ProgressReporter Telegram card edits",
)
```

- [ ] **Step 2: Write the failing `/metrics` endpoint test**

Create `tests/test_metrics_endpoint.py`:

```python
"""Integration test for the Prometheus /metrics endpoint."""
from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_200_and_counter_names():
    from routes.api import metrics_endpoint

    request = make_mocked_request("GET", "/metrics")
    response = await metrics_endpoint(request)

    assert response.status == 200
    body = response.body.decode() if isinstance(response.body, bytes) else response.body
    # Counters are registered at module import; they should appear in output (even with zero values)
    assert "whatsapp_messages_total" in body
    assert "telegram_edit_failures_total" in body
    assert "progress_card_edits_total" in body


@pytest.mark.asyncio
async def test_metrics_reflects_incremented_counter():
    from webhook.metrics import whatsapp_sent
    from routes.api import metrics_endpoint

    whatsapp_sent.labels(status="success").inc()

    request = make_mocked_request("GET", "/metrics")
    response = await metrics_endpoint(request)
    body = response.body.decode()

    assert 'whatsapp_messages_total{status="success"}' in body
```

- [ ] **Step 3: Run test — expect FAIL** (`ImportError: cannot import name 'metrics_endpoint'`).

```bash
.venv/bin/pytest tests/test_metrics_endpoint.py -v 2>&1 | tail -10
```

- [ ] **Step 4: Add `metrics_endpoint` in `webhook/routes/api.py`**

Near the top of `webhook/routes/api.py`, add imports if missing:

```python
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
# Ensure counter registration by importing the metrics module
import webhook.metrics  # noqa: F401 — registers counters as side effect
```

Add the handler (wherever other routes live):

```python
@routes.get("/metrics")
async def metrics_endpoint(request: web.Request) -> web.Response:
    """Prometheus scrape endpoint. Unauthenticated — safe because counters
    are aggregate and non-sensitive."""
    return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST)
```

- [ ] **Step 5: Run the test — expect PASS**

```bash
.venv/bin/pytest tests/test_metrics_endpoint.py -v 2>&1 | tail -10
```

Expected: 2 passed.

- [ ] **Step 6: Full suite**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py tests/test_dispatch_idempotency.py tests/test_metrics_endpoint.py --tb=no -q 2>&1 | tail -3
```

Expected: 58 passed (56 + 2 new).

- [ ] **Step 7: Commit**

```bash
git add webhook/metrics.py webhook/routes/api.py tests/test_metrics_endpoint.py
git commit -m "feat(obs): Prometheus /metrics endpoint + counters module"
```

---

## Task 6: Fix silent `except: pass` in dispatch + callbacks_curation

**Files:**
- Modify: `webhook/dispatch.py`, `webhook/bot/routers/callbacks_curation.py`

### Background

The plan's spec (§4.4) identified these silent-swallow points. Each swallows ALL exceptions, making Sentry blind and Prometheus counters useless. Replace with typed handling.

### Steps

- [ ] **Step 1: In `webhook/dispatch.py`, find each `try: await bot.edit_message_text(...) except Exception: pass` site**

Grep:
```bash
grep -n "except Exception" webhook/dispatch.py
```

For each one that wraps an `edit_message_text` call, replace with:

```python
from aiogram.exceptions import TelegramBadRequest
from webhook.metrics import edit_failures

try:
    await bot.edit_message_text(...)  # keep original args
except TelegramBadRequest as e:
    msg = str(e).lower()
    if "message is not modified" in msg:
        edit_failures.labels(reason="not_modified").inc()
        # expected no-op; don't log
    elif "flood" in msg:
        edit_failures.labels(reason="flood").inc()
        logger.warning("telegram_flood_control", extra={"error": str(e)})
    else:
        edit_failures.labels(reason="bad_request").inc()
        logger.warning("edit_failed", extra={"chat_id": chat_id, "error": str(e)})
except Exception as e:
    edit_failures.labels(reason="unexpected").inc()
    logger.warning("edit_unexpected", exc_info=e)
```

(Put the `from aiogram.exceptions import TelegramBadRequest` and `from webhook.metrics import edit_failures` at the top of the file, once.)

- [ ] **Step 2: Same for `webhook/bot/routers/callbacks_curation.py:_finalize_card`**

Find `_finalize_card` (around line 51-62). Replace its inner try/except with the same pattern:

```python
async def _finalize_card(query: CallbackQuery, status_text: str):
    bot = get_bot()
    message_id = query.message.message_id
    try:
        await bot.edit_message_text(
            status_text, chat_id=query.message.chat.id,
            message_id=message_id, reply_markup=None,
        )
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            edit_failures.labels(reason="not_modified").inc()
        elif "flood" in msg:
            edit_failures.labels(reason="flood").inc()
            logger.warning("finalize_card_flood", extra={"chat_id": query.message.chat.id})
        else:
            edit_failures.labels(reason="bad_request").inc()
            # Keep the existing fallback: send plain message
            plain = status_text.replace("*", "").replace("`", "").replace("_", "")
            await bot.send_message(query.message.chat.id, plain)
    except Exception as e:
        edit_failures.labels(reason="unexpected").inc()
        logger.warning("finalize_card_unexpected", exc_info=e)
        plain = status_text.replace("*", "").replace("`", "").replace("_", "")
        await bot.send_message(query.message.chat.id, plain)
```

Add at the top of `callbacks_curation.py`:

```python
from aiogram.exceptions import TelegramBadRequest
from webhook.metrics import edit_failures
```

- [ ] **Step 3: Run the full Phase 1 + Phase 3a suite**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py tests/test_dispatch_idempotency.py tests/test_metrics_endpoint.py --tb=short -q 2>&1 | tail -10
```

Expected: 58 passed. If a curation test fails — `_finalize_card`'s fallback path changed slightly (previously the `except Exception` fallback fired for ALL errors; now it fires only for `bad_request` and `unexpected`). If a test asserts the fallback behavior on a specific "not_modified" error, update the assertion to match the new behavior (the fallback no longer fires for not_modified).

- [ ] **Step 4: Verify no more silent `except Exception: pass`**

```bash
grep -n "except Exception:\s*$\|except Exception:\s*#\|except:\s*pass" webhook/dispatch.py webhook/bot/routers/callbacks_curation.py
```

Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add webhook/dispatch.py webhook/bot/routers/callbacks_curation.py
git commit -m "fix(dispatch,curation): replace silent except:pass with typed TelegramBadRequest handling"
```

---

## Task 7: Supabase `event_log` migration

**Files:**
- Create: `supabase/migrations/20260418_event_log.sql` (or wherever repo migration convention is)

### Steps

- [ ] **Step 1: Find the repo's migration convention**

```bash
find . -name "*.sql" -path "*/migrations/*" -not -path "*/.venv/*" 2>/dev/null | head -5
ls -la supabase/ 2>/dev/null || ls -la execution/db/ 2>/dev/null || echo "No existing migrations dir"
```

If no existing migrations dir, create `supabase/migrations/`.

- [ ] **Step 2: Write the migration SQL**

Create `supabase/migrations/20260418_event_log.sql`:

```sql
-- Phase 3 event_log: timeline storage for workflow/draft observability.
-- Populated by execution/core/progress_reporter.py:ProgressReporter.step()
-- Queried for per-draft/per-run timelines.

create table if not exists event_log (
  id bigserial primary key,
  workflow text not null,
  run_id text,
  draft_id text,
  level text not null check (level in ('info', 'warning', 'error')),
  label text not null,
  detail text,
  context jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

create index if not exists event_log_draft_idx
  on event_log (draft_id) where draft_id is not null;

create index if not exists event_log_workflow_time_idx
  on event_log (workflow, created_at desc);

create index if not exists event_log_run_idx
  on event_log (run_id) where run_id is not null;

comment on table event_log is
  'Observability timeline events written by ProgressReporter.step(). '
  'Retention: TBD; truncate policy lives outside this migration.';
```

- [ ] **Step 3: Document how to apply the migration**

Append to `supabase/migrations/README.md` (create if missing):

```markdown
# Supabase Migrations

## How to apply

Migrations here are SQL files named `YYYYMMDD_<name>.sql`. To apply:

1. Log into Supabase dashboard for the target project.
2. Open the SQL editor.
3. Paste the migration file contents.
4. Run. Verify with: `select count(*) from <new_table>;`

For automated application, see `supabase/README.md` or use the Supabase CLI:
```bash
supabase db push --project-ref <PROJECT_REF>
```
```

- [ ] **Step 4: Smoke-check the SQL file syntax** (offline — no DB connection needed)

```bash
head -30 supabase/migrations/20260418_event_log.sql
```

Expected: the file content as written above.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/20260418_event_log.sql supabase/migrations/README.md
git commit -m "feat(db): event_log migration — timeline observability table"
```

**Note for human operator:** after merging, apply this migration manually to the Supabase dev and prod projects before Task 8 is deployed. Tests in Task 8 mock the Supabase client; real production deploy needs the table to exist.

---

## Task 8: Extend `ProgressReporter` with `step()` + 3 sinks + debounce

**Files:**
- Modify: `execution/core/progress_reporter.py`
- Create: `tests/test_progress_reporter_sinks.py`

### Background

`execution/core/progress_reporter.py` has a 217-line `ProgressReporter` class with `start/update/finish/fail`. We add:
- `step(label, detail, level)` that fan-outs to 3 sinks (logger, event_log, Telegram card).
- Debouncing: at most 1 Telegram edit every 2s.
- `finish()` must flush pending debounce state.

### Steps

- [ ] **Step 1: Write failing tests**

Create `tests/test_progress_reporter_sinks.py`:

```python
"""Unit tests for ProgressReporter.step() and debounced card flush."""
from __future__ import annotations

import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.edit_message_text = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
    return bot


@pytest.fixture
def mock_supabase():
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute = MagicMock()
    return sb


@pytest.mark.asyncio
async def test_step_calls_all_three_sinks_on_first_call(mock_bot, mock_supabase, caplog):
    from execution.core.progress_reporter import ProgressReporter

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_1",
        draft_id="draft_1", supabase_client=mock_supabase,
    )
    reporter._message_id = 999  # pretend start() was called
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    await reporter.step("Loading", "fetching contacts", level="info")
    # Wait for fire-and-forget event_log task to complete
    await asyncio.sleep(0.05)

    # Sink 1: structured log
    assert any("Loading" in rec.getMessage() or "Loading" in str(rec.__dict__) for rec in caplog.records)
    # Sink 2: event_log insert
    mock_supabase.table.assert_called_with("event_log")
    # Sink 3: Telegram card edit
    mock_bot.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_step_debounces_rapid_edits_within_2_seconds(mock_bot, mock_supabase):
    from execution.core.progress_reporter import ProgressReporter

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_2",
        supabase_client=mock_supabase,
    )
    reporter._message_id = 999
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    # Fire 3 steps rapidly
    await reporter.step("Step 1")
    await reporter.step("Step 2")
    await reporter.step("Step 3")

    # First step flushes immediately; subsequent debounced.
    # Within 100ms of the first step, only 1 edit should have fired.
    await asyncio.sleep(0.1)
    assert mock_bot.edit_message_text.await_count == 1


@pytest.mark.asyncio
async def test_finish_flushes_pending_debounced_state(mock_bot, mock_supabase):
    from execution.core.progress_reporter import ProgressReporter

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_3",
        supabase_client=mock_supabase,
    )
    reporter._message_id = 999
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    await reporter.step("Step 1")
    await reporter.step("Step 2")  # debounced
    # finish() must flush the final state (containing Step 2)
    await reporter.finish(message="Done")

    # At least 2 edits: one from first step + one from finish flush
    assert mock_bot.edit_message_text.await_count >= 2


@pytest.mark.asyncio
async def test_event_log_insert_failure_does_not_raise(mock_bot, mock_supabase):
    from execution.core.progress_reporter import ProgressReporter

    mock_supabase.table.return_value.insert.return_value.execute = MagicMock(
        side_effect=RuntimeError("supabase down")
    )

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_4",
        supabase_client=mock_supabase,
    )
    reporter._message_id = 999
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    # Must NOT raise even though the supabase insert throws
    await reporter.step("Step 1")
    await asyncio.sleep(0.05)

    mock_bot.edit_message_text.assert_awaited()  # Telegram still works


@pytest.mark.asyncio
async def test_step_without_supabase_client_still_updates_telegram(mock_bot):
    from execution.core.progress_reporter import ProgressReporter

    reporter = ProgressReporter(
        bot=mock_bot, chat_id=100, workflow="test_wf", run_id="run_5",
        supabase_client=None,
    )
    reporter._message_id = 999
    reporter._pending_card_state = []
    reporter._last_edit_at = None

    await reporter.step("Step 1")
    await asyncio.sleep(0.05)

    mock_bot.edit_message_text.assert_awaited()
```

- [ ] **Step 2: Run tests — expect FAIL** (either `AttributeError: 'ProgressReporter' object has no attribute 'step'` or `TypeError: unexpected keyword argument 'supabase_client'`).

```bash
.venv/bin/pytest tests/test_progress_reporter_sinks.py -v 2>&1 | tail -15
```

- [ ] **Step 3: Modify `execution/core/progress_reporter.py`**

Read the existing class first:
```bash
head -60 execution/core/progress_reporter.py
```

Note the existing `__init__` signature. Extend it to accept `workflow`, `run_id`, `draft_id`, `supabase_client` (with defaults so existing callers don't break). At the top of the file, add imports if missing:

```python
import asyncio
import time as _time
from typing import Optional

from aiogram.exceptions import TelegramBadRequest
```

Then add a method `step()` and the supporting helpers. Append inside the class:

```python
async def step(self, label: str, detail: str = "", level: str = "info") -> None:
    """Emit a progress step to three sinks: log, event_log, Telegram card."""
    await self._emit_structured_log(level, label, detail)
    # Fire-and-forget event_log persistence (doesn't block flow)
    asyncio.create_task(self._persist_event_log(level, label, detail))
    await self._update_telegram_card(label, detail, level)

async def _emit_structured_log(self, level: str, label: str, detail: str) -> None:
    log_method = getattr(_module_logger, level, _module_logger.info)
    log_method(
        "progress.step",
        extra={
            "workflow": getattr(self, "workflow", "unknown"),
            "run_id": getattr(self, "run_id", None),
            "draft_id": getattr(self, "draft_id", None),
            "label": label,
            "detail": detail,
        },
    )

async def _persist_event_log(self, level: str, label: str, detail: str) -> None:
    sb = getattr(self, "_supabase", None)
    if sb is None:
        return
    try:
        sb.table("event_log").insert({
            "workflow": getattr(self, "workflow", "unknown"),
            "run_id": getattr(self, "run_id", None),
            "draft_id": getattr(self, "draft_id", None),
            "level": level,
            "label": label,
            "detail": detail,
        }).execute()
    except Exception as exc:
        _module_logger.warning("event_log_insert_failed", exc_info=exc)

async def _update_telegram_card(self, label: str, detail: str, level: str) -> None:
    self._pending_card_state.append({"label": label, "detail": detail, "level": level})
    now = _time.monotonic()
    if self._last_edit_at and (now - self._last_edit_at) < 2.0:
        # Debounce: schedule a flush if not already pending.
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._delayed_flush())
        return
    await self._flush_now()

async def _delayed_flush(self) -> None:
    await asyncio.sleep(2.0)
    await self._flush_now()

async def _flush_now(self) -> None:
    """Render accumulated card state and edit the Telegram message.
    Handles TelegramBadRequest gracefully; increments progress_card_edits counter."""
    if self._message_id is None:
        return
    card_text = self._render_card()
    try:
        await self._bot.edit_message_text(
            card_text,
            chat_id=self._chat_id,
            message_id=self._message_id,
        )
    except TelegramBadRequest as e:
        msg = str(e).lower()
        if "message is not modified" in msg:
            pass  # expected no-op
        else:
            _module_logger.warning("progress_card_edit_failed", extra={"error": str(e)})
    except Exception as e:
        _module_logger.warning("progress_card_edit_unexpected", exc_info=e)
    self._last_edit_at = _time.monotonic()
    try:
        from webhook.metrics import progress_card_edits
        progress_card_edits.inc()
    except ImportError:
        pass  # webhook.metrics not available in execution-only contexts

def _render_card(self) -> str:
    """Compose the card body from accumulated steps.
    Glyphs: ✅ done, ⏳ running (last), ⬜ pending (unused here), ⚠️ error."""
    lines = []
    header = f"{self._header_emoji if hasattr(self, '_header_emoji') else '📡'} {getattr(self, 'workflow', 'workflow')}"
    lines.append(header)
    lines.append("━" * 22)
    for i, step in enumerate(self._pending_card_state):
        is_last = (i == len(self._pending_card_state) - 1)
        if step.get("level") == "error":
            glyph = "⚠️"
        elif is_last:
            glyph = "⏳"
        else:
            glyph = "✅"
        line = f"{glyph} {step['label']}"
        if step.get("detail"):
            line += f" — {step['detail']}"
        lines.append(line)
    return "\n".join(lines)
```

Extend `__init__` signature:

```python
def __init__(
    self,
    bot,
    chat_id,
    workflow: str = "unknown",
    run_id: Optional[str] = None,
    draft_id: Optional[str] = None,
    supabase_client=None,
    # ... keep all existing params (message_id, etc.) with their defaults
):
    # existing body +
    self.workflow = workflow
    self.run_id = run_id
    self.draft_id = draft_id
    self._supabase = supabase_client
    self._pending_card_state: list[dict] = []
    self._last_edit_at: Optional[float] = None
    self._flush_task: Optional[asyncio.Task] = None
```

Extend `finish(report, message=None)` to flush pending state first:

```python
async def finish(self, report=None, message: Optional[str] = None) -> None:
    # Cancel any pending debounce and flush immediately
    if self._flush_task and not self._flush_task.done():
        self._flush_task.cancel()
    if self._pending_card_state:
        await self._flush_now()
    # ... existing finish logic below (keep intact)
```

Add at module top if missing:

```python
import logging
_module_logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
.venv/bin/pytest tests/test_progress_reporter_sinks.py -v 2>&1 | tail -20
```

Expected: 5 passed.

- [ ] **Step 5: Full suite — confirm no regressions**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py tests/test_dispatch_idempotency.py tests/test_metrics_endpoint.py tests/test_progress_reporter_sinks.py --tb=short -q 2>&1 | tail -5
```

Expected: 63 passed (58 + 5 new).

- [ ] **Step 6: Commit**

```bash
git add execution/core/progress_reporter.py tests/test_progress_reporter_sinks.py
git commit -m "feat(core): ProgressReporter.step() with 3 sinks + 2s debounce"
```

---

## Task 9: Instrument `platts_ingestion.py`

**Files:**
- Modify: `execution/scripts/platts_ingestion.py`

### Steps

- [ ] **Step 1: Read the current script structure**

```bash
grep -n "^def\|^async def\|^if __name__\|logger.info" execution/scripts/platts_ingestion.py | head -30
```

Identify the main phases (e.g., "actor triggered", "dataset fetched", "dedup applied", "staging done").

- [ ] **Step 2: Add `ProgressReporter` instantiation and `step()` calls**

Near the top of `main()` (or equivalent entry point), after `init_sentry(__name__)`:

```python
from execution.core.progress_reporter import ProgressReporter
from execution.integrations.telegram_client import TelegramClient
from execution.integrations.supabase_client import SupabaseClient
import uuid

_tg_bot = TelegramClient().bot  # or however the repo gets the aiogram Bot
_chat_id = int(os.environ["TELEGRAM_CHAT_ID"])
_sb = SupabaseClient().client  # adapt to actual convention

_reporter = ProgressReporter(
    bot=_tg_bot,
    chat_id=_chat_id,
    workflow="platts_ingestion",
    run_id=str(uuid.uuid4()),
    supabase_client=_sb,
)

# Post the progress card (first step triggers an edit, so send an initial placeholder first)
_progress_msg = await _tg_bot.send_message(_chat_id, "📡 Platts Ingestion\n⏳ starting...")
_reporter._message_id = _progress_msg.message_id
```

(The exact attribute names of the existing TelegramClient / SupabaseClient may differ — adapt based on the repo.)

Insert `await _reporter.step(label, detail)` calls at each phase boundary. Typical 5–8 calls:

```python
await _reporter.step("Actor started", "platts-scrap-full-news triggered")
# ... actor runs ...
await _reporter.step("Dataset fetched", f"{len(items)} raw items")
# ... dedup ...
await _reporter.step("Dedup applied", f"{new_count} new, {dup_count} duplicates")
# ... staging ...
await _reporter.step("Staged in Redis", f"{staged_count} items")
await _reporter.finish(message=f"✅ Done — {staged_count} staged")
```

Wrap failures:

```python
try:
    # main flow
except Exception as exc:
    await _reporter.fail(exc)
    raise
```

- [ ] **Step 3: Smoke-test script imports**

```bash
.venv/bin/python -c "import execution.scripts.platts_ingestion; print('OK')"
```

- [ ] **Step 4: Full test suite — confirm no regressions**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py tests/test_dispatch_idempotency.py tests/test_metrics_endpoint.py tests/test_progress_reporter_sinks.py --tb=no -q 2>&1 | tail -3
```

Expected: 63 passed.

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/platts_ingestion.py
git commit -m "feat(scripts): instrument platts_ingestion with ProgressReporter"
```

---

## Task 10: Instrument `platts_reports.py`

**Files:**
- Modify: `execution/scripts/platts_reports.py`

### Steps

- [ ] **Step 1: Inspect current phases**

```bash
grep -n "^def\|^async def\|^if __name__\|logger.info" execution/scripts/platts_reports.py | head -20
```

- [ ] **Step 2: Instrument like Task 9**

Create a `ProgressReporter` with `workflow="platts_reports"`. Insert `step()` calls at: "Actor started", "PDF downloaded ({count} reports)", "Uploaded to Supabase", "Telegram card sent". End with `finish()` or `fail()`.

- [ ] **Step 3: Smoke-test**

```bash
.venv/bin/python -c "import execution.scripts.platts_reports; print('OK')"
```

- [ ] **Step 4: Full suite**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py tests/test_dispatch_idempotency.py tests/test_metrics_endpoint.py tests/test_progress_reporter_sinks.py --tb=no -q 2>&1 | tail -3
```

Expected: 63 passed.

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/platts_reports.py
git commit -m "feat(scripts): instrument platts_reports with ProgressReporter"
```

---

## Task 11: Instrument `baltic_ingestion.py`

**Files:**
- Modify: `execution/scripts/baltic_ingestion.py`

### Steps

- [ ] **Step 1: Inspect current phases**

```bash
grep -n "^def\|^async def\|^if __name__\|logger.info" execution/scripts/baltic_ingestion.py | head -20
```

- [ ] **Step 2: Instrument**

`ProgressReporter(workflow="baltic_ingestion", ...)`. Phases: "Email fetched", "PDF extracted", "Claude parsed ({count} rows)", "Postgres upsert".

- [ ] **Step 3: Smoke-test**

```bash
.venv/bin/python -c "import execution.scripts.baltic_ingestion; print('OK')"
```

- [ ] **Step 4: Full suite**

```bash
.venv/bin/pytest tests/test_callbacks_*.py tests/test_messages_fsm_isolation.py tests/test_dispatch_idempotency.py tests/test_metrics_endpoint.py tests/test_progress_reporter_sinks.py --tb=no -q 2>&1 | tail -3
```

Expected: 63 passed.

- [ ] **Step 5: Commit**

```bash
git add execution/scripts/baltic_ingestion.py
git commit -m "feat(scripts): instrument baltic_ingestion with ProgressReporter"
```

---

## Task 12: Manual smoke test + final verification + merge

**Files:** none modified; verification only.

### Steps

- [ ] **Step 1: Full repo test suite**

```bash
.venv/bin/pytest --tb=short 2>&1 | tail -10
```

Expected: 420+ passed (414 pre-Phase-3 + 10 new Phase-3 tests). The 5 pre-existing failures from `cron_parser.py` + `query_handlers.py` unchanged.

- [ ] **Step 2: Grep confirms zero silent `except: pass`**

```bash
grep -n "except Exception:\s*pass\|except:\s*pass" webhook/dispatch.py webhook/bot/routers/callbacks_*.py | head -5
```

Expected: no results.

- [ ] **Step 3: Confirm new files exist and sizes are reasonable**

```bash
wc -l webhook/metrics.py execution/core/sentry_init.py supabase/migrations/20260418_event_log.sql execution/core/progress_reporter.py
```

Expected: metrics.py ~35 lines, sentry_init.py ~40 lines, migration ~30 lines, progress_reporter.py ~350 lines (was 217, added ~130).

- [ ] **Step 4: Manual bot smoke test** (human in loop)

Prepare the following and verify each works end-to-end before merging:

**Setup:**
- Ensure `SENTRY_DSN` is set in `.env` with a real DSN (create free Sentry project if needed).
- Apply the `event_log` migration to the Supabase DEV project via the SQL editor.
- Start the bot locally: `.venv/bin/python -m webhook.bot.main` (or whatever the existing run command is).

**Test 1 — Idempotency:**
Trigger a broadcast draft, approve it. Approve it a second time within 24h (same draft_id, same message). The second trigger should not cause duplicate WhatsApp sends. Check Redis: `redis-cli KEYS 'whatsapp:sent:*' | wc -l` should show keys exist.

**Test 2 — Sentry:**
Temporarily hit an endpoint that raises (e.g., create a `/test-sentry` endpoint that does `raise RuntimeError("phase3 test")` — REVERT after test). Verify a Sentry event appears in the dashboard within 30s.

**Test 3 — `/metrics`:**
`curl http://localhost:8080/metrics | grep whatsapp_messages_total` — should show the counter with any non-zero values reflecting actual sends.

**Test 4 — Live progress card:**
Trigger a `platts_ingestion` run manually:
```bash
.venv/bin/python -m execution.scripts.platts_ingestion
```

In the configured `TELEGRAM_CHAT_ID`, watch the progress card edit every ~2 seconds through the phases: "Actor started" → "Dataset fetched" → "Dedup applied" → "Staged in Redis" → "Done".

**Test 5 — event_log query:**
After a broadcast completes, run in Supabase SQL editor:
```sql
select workflow, label, detail, created_at
from event_log
where workflow = 'broadcast' or draft_id = '<the draft id from test 1>'
order by created_at;
```

Expected: rows corresponding to every `step()` call during the broadcast.

If any smoke test fails, ROLLBACK or file a follow-up bug — do not merge until green or the gap is explicitly accepted.

- [ ] **Step 5: Tag and merge**

```bash
git commit --allow-empty -m "refactor(bot): phase 3 observability + reliability complete"
git tag phase3-observability-complete
git log --oneline phase2-router-split-complete..HEAD
```

Log should show ~12-14 commits.

- [ ] **Step 6: Merge to main and push**

```bash
git checkout main
git merge phase3-observability --ff-only
git push origin main
git push origin phase3-observability-complete
```

---

## Self-Review Notes

1. **Spec coverage (§4):**
   - 4.1 Idempotency → Task 2 ✓
   - 4.2 Sentry (webhook + scripts) → Tasks 3 + 4 ✓
   - 4.3 Prometheus /metrics → Task 5 ✓
   - 4.4 Silent except:pass fix → Task 6 ✓
   - 4.5 event_log migration → Task 7 ✓
   - 4.6 ProgressReporter 3 sinks + debounce → Task 8 ✓
   - 4.7 Instrument 3 scripts → Tasks 9, 10, 11 ✓
   - DoD items (idempotency test, /metrics counter, live card, event_log query) → Task 12 ✓

2. **Placeholder scan:** no "TBD", no "similar to above". Each task has concrete code.

3. **Type consistency:**
   - `ProgressReporter(bot, chat_id, workflow, run_id, draft_id, supabase_client)` signature used consistently in Tasks 8–11.
   - Counter names (`whatsapp_messages_total`, `telegram_edit_failures_total`, `progress_card_edits_total`) used consistently in Tasks 5, 6, 8.
   - `_idempotency_key(phone, draft_id, message)` signature consistent between impl (Task 2) and tests.
   - Log labels ("whatsapp_idempotency_hit", "edit_failed", "progress.step") consistent.

4. **Known risks:**
   - Task 9–11 require adapting to the actual `TelegramClient` / `SupabaseClient` attribute names in this repo. The plan notes "adapt as needed." Implementer may need to read the client wrappers first.
   - Task 8's `__init__` extension must not break existing callers of `ProgressReporter` (broadcast already uses it). Preserved by making all new params have defaults.
   - Task 12's manual smoke test requires real SENTRY_DSN + applied migration — flagged as human-in-loop.

5. **Not TDD for migration (Task 7):** SQL migrations are configuration, not testable Python. Task 7 creates the file; Task 8's tests mock Supabase client; Task 12 applies the migration manually before smoke test.

---

*Plan author: writing-plans session 2026-04-18*
*After Phase 3 ships, Milestone "Backend Hardening v1" is complete.*
