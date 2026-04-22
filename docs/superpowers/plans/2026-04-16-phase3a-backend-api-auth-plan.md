# Phase 3A: Backend API + Auth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build all `/api/mini/*` backend endpoints with Telegram initData authentication so the Phase 3B/3C frontend has a working API to consume.

**Architecture:** Thin aiohttp route handlers that validate Telegram initData (HMAC-SHA256), then delegate to existing data sources — GitHub Actions API (workflows), Redis (news/curation), Supabase (reports), Google Sheets (contacts). All endpoints share a single auth helper. Mounted in the existing aiohttp app alongside current routes.

**Tech Stack:** aiohttp (HTTP), aiogram.utils.web_app (initData validation), Redis/fakeredis (curation data), Supabase (reports), gspread (contacts), pytest + pytest-asyncio (testing)

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `webhook/routes/mini_auth.py` | `validate_init_data(request)` — extracts `X-Telegram-Init-Data` header, verifies HMAC via aiogram, checks user role |
| Create | `webhook/routes/mini_api.py` | All `/api/mini/*` route handlers (10 endpoints) using `web.RouteTableDef()` |
| Modify | `webhook/bot/main.py:83-93` | Mount `mini_api.routes` in `create_app()` |
| Modify | `requirements.txt` | Add `pytest-asyncio>=0.21` for async test support |
| Create | `tests/test_mini_auth.py` | Auth validation tests (valid/invalid/missing/forbidden) |
| Create | `tests/test_mini_workflows.py` | Workflows endpoint tests |
| Create | `tests/test_mini_news.py` | News endpoint tests |
| Create | `tests/test_mini_reports.py` | Reports endpoint tests |
| Create | `tests/test_mini_contacts.py` | Contacts endpoint tests |
| Create | `tests/test_mini_stats.py` | Stats endpoint tests |

---

### Task 1: initData Auth Helper + Route Wiring

**Files:**
- Create: `webhook/routes/mini_auth.py`
- Create: `webhook/routes/mini_api.py` (skeleton)
- Modify: `webhook/bot/main.py:83-93`
- Modify: `requirements.txt`
- Create: `tests/test_mini_auth.py`

- [ ] **Step 1: Add pytest-asyncio to requirements**

In `requirements.txt`, add after the `fakeredis` line:

```
pytest-asyncio>=0.21,<1.0
```

- [ ] **Step 2: Write the auth validation tests**

Create `tests/test_mini_auth.py`:

```python
"""Tests for webhook/routes/mini_auth.py — Telegram initData validation."""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

TEST_TOKEN = "123456789:AAFakeTokenForTesting_abcdefghijk"


def _make_init_data(
    token: str = TEST_TOKEN,
    user_id: int = 12345,
    first_name: str = "Test",
    extra_params: dict | None = None,
) -> str:
    """Generate a correctly signed Telegram initData string."""
    user = json.dumps({"id": user_id, "first_name": first_name})
    params = {
        "user": user,
        "auth_date": str(int(time.time())),
        **(extra_params or {}),
    }
    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )
    secret_key = hmac.new(
        key=b"WebAppData", msg=token.encode(), digestmod=hashlib.sha256,
    )
    calculated_hash = hmac.new(
        key=secret_key.digest(),
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()
    params["hash"] = calculated_hash
    return urlencode(params)


class FakeRequest:
    """Minimal request-like object for testing."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


@pytest.mark.asyncio
async def test_valid_init_data():
    from routes.mini_auth import validate_init_data

    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="admin"):
            result = await validate_init_data(request)
            assert result.user is not None
            assert result.user.id == 12345


@pytest.mark.asyncio
async def test_missing_header_returns_401():
    from aiohttp.web import HTTPUnauthorized
    from routes.mini_auth import validate_init_data

    request = FakeRequest(headers={})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with pytest.raises(HTTPUnauthorized):
            await validate_init_data(request)


@pytest.mark.asyncio
async def test_invalid_signature_returns_401():
    from aiohttp.web import HTTPUnauthorized
    from routes.mini_auth import validate_init_data

    request = FakeRequest(headers={"X-Telegram-Init-Data": "user=bad&hash=bad&auth_date=0"})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with pytest.raises(HTTPUnauthorized):
            await validate_init_data(request)


@pytest.mark.asyncio
async def test_unknown_user_returns_403():
    from aiohttp.web import HTTPForbidden
    from routes.mini_auth import validate_init_data

    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="unknown"):
            with pytest.raises(HTTPForbidden):
                await validate_init_data(request)


@pytest.mark.asyncio
async def test_pending_user_returns_403():
    from aiohttp.web import HTTPForbidden
    from routes.mini_auth import validate_init_data

    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="pending"):
            with pytest.raises(HTTPForbidden):
                await validate_init_data(request)


@pytest.mark.asyncio
async def test_subscriber_allowed():
    from routes.mini_auth import validate_init_data

    init_data = _make_init_data()
    request = FakeRequest(headers={"X-Telegram-Init-Data": init_data})
    with patch("routes.mini_auth.TELEGRAM_BOT_TOKEN", TEST_TOKEN):
        with patch("routes.mini_auth.get_user_role", return_value="subscriber"):
            result = await validate_init_data(request)
            assert result.user.id == 12345
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_mini_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'routes.mini_auth'`

- [ ] **Step 4: Implement the auth helper**

Create `webhook/routes/mini_auth.py`:

```python
"""Telegram Mini App initData authentication.

Validates the X-Telegram-Init-Data header using HMAC-SHA256 per
https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app

Uses aiogram.utils.web_app which provides check_webapp_signature()
and safe_parse_webapp_init_data().
"""
from __future__ import annotations

import logging

from aiohttp import web
from aiogram.utils.web_app import safe_parse_webapp_init_data, WebAppInitData

from bot.config import TELEGRAM_BOT_TOKEN
from bot.users import get_user_role

logger = logging.getLogger(__name__)


async def validate_init_data(request) -> WebAppInitData:
    """Extract and validate Telegram initData from request header.

    Returns WebAppInitData on success.
    Raises HTTPUnauthorized (missing/invalid) or HTTPForbidden (unauthorized user).
    """
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if not init_data:
        raise web.HTTPUnauthorized(text="Missing initData")

    try:
        data = safe_parse_webapp_init_data(TELEGRAM_BOT_TOKEN, init_data)
    except ValueError:
        raise web.HTTPUnauthorized(text="Invalid initData signature")

    if data.user:
        role = get_user_role(data.user.id)
        if role not in ("admin", "subscriber"):
            raise web.HTTPForbidden(text="Not authorized")

    return data
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_mini_auth.py -v`
Expected: all 6 tests PASS

- [ ] **Step 6: Create mini_api.py skeleton and wire into main.py**

Create `webhook/routes/mini_api.py`:

```python
"""Telegram Mini App API endpoints.

All routes require valid Telegram initData in X-Telegram-Init-Data header.
Prefix: /api/mini/
"""
from __future__ import annotations

import logging

from aiohttp import web

from routes.mini_auth import validate_init_data

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()
```

Modify `webhook/bot/main.py` — add import after line 35:

```python
from routes.mini_api import routes as mini_api_routes
```

And in `create_app()`, add after `app.router.add_routes(preview_routes)` (line 93):

```python
    app.router.add_routes(mini_api_routes)
```

- [ ] **Step 7: Verify the app still starts**

Run: `cd webhook && python -c "from bot.main import create_app; print('OK')"`
Expected: `OK` (no import errors)

- [ ] **Step 8: Commit**

```bash
git add requirements.txt webhook/routes/mini_auth.py webhook/routes/mini_api.py webhook/bot/main.py tests/test_mini_auth.py
git commit -m "feat(mini-api): initData auth helper + route skeleton for Phase 3A"
```

---

### Task 2: Workflows API Endpoints

**Files:**
- Modify: `webhook/routes/mini_api.py`
- Create: `tests/test_mini_workflows.py`

**Context:** These endpoints wrap GitHub Actions API calls. The existing `webhook/workflow_trigger.py` already has `WORKFLOW_CATALOG`, `_gh_headers()`, `trigger_workflow()`, and `check_run_status()` — reuse them. The list endpoint needs its own GitHub fetch because `render_workflow_list()` returns Telegram markup, not JSON data.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mini_workflows.py`:

```python
"""Tests for /api/mini/workflows endpoints."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))


class FakeRequest:
    def __init__(self, headers=None, query=None, match_info=None):
        self.headers = headers or {}
        self.query = query or {}
        self.match_info = match_info or {}

    async def json(self):
        return self._json_body

    def set_json(self, body):
        self._json_body = body
        return self


def _patch_auth():
    """Bypass initData validation in tests."""
    mock_data = MagicMock()
    mock_data.user = MagicMock()
    mock_data.user.id = 12345
    return patch("routes.mini_api.validate_init_data", new_callable=AsyncMock, return_value=mock_data)


FAKE_RUNS = {
    "workflow_runs": [
        {
            "path": ".github/workflows/morning_check.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-04-16T08:30:00Z",
            "updated_at": "2026-04-16T08:31:00Z",
            "run_started_at": "2026-04-16T08:30:05Z",
        },
        {
            "path": ".github/workflows/morning_check.yml",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-04-15T08:30:00Z",
            "updated_at": "2026-04-15T08:32:00Z",
            "run_started_at": "2026-04-15T08:30:10Z",
        },
        {
            "path": ".github/workflows/baltic_ingestion.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-04-16T09:00:00Z",
            "updated_at": "2026-04-16T09:01:00Z",
            "run_started_at": "2026-04-16T09:00:05Z",
        },
    ],
}


def _mock_github_session(response_data, status=200):
    """Create a patched aiohttp.ClientSession that returns response_data."""
    mock_resp = AsyncMock()
    mock_resp.status = status
    mock_resp.json = AsyncMock(return_value=response_data)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return patch("routes.mini_api.aiohttp.ClientSession", return_value=mock_session)


@pytest.mark.asyncio
async def test_get_workflows_returns_catalog():
    from routes.mini_api import get_workflows

    request = FakeRequest()
    with _patch_auth(), _mock_github_session(FAKE_RUNS):
        response = await get_workflows(request)

    data = json.loads(response.body)
    assert "workflows" in data
    assert len(data["workflows"]) == 5
    mc = next(w for w in data["workflows"] if w["id"] == "morning_check.yml")
    assert mc["name"] == "MORNING CHECK"
    assert mc["icon"] == "\U0001f4ca"
    assert mc["last_run"] is not None
    assert mc["last_run"]["conclusion"] == "success"
    assert mc["health_pct"] == 50  # 1 success, 1 failure
    assert len(mc["recent_runs"]) == 2


@pytest.mark.asyncio
async def test_get_workflows_github_error_returns_empty_runs():
    from routes.mini_api import get_workflows

    request = FakeRequest()
    with _patch_auth(), _mock_github_session({}, status=500):
        response = await get_workflows(request)

    data = json.loads(response.body)
    assert len(data["workflows"]) == 5
    mc = next(w for w in data["workflows"] if w["id"] == "morning_check.yml")
    assert mc["last_run"] is None
    assert mc["health_pct"] == 100  # no data = assume healthy


@pytest.mark.asyncio
async def test_get_workflow_runs():
    from routes.mini_api import get_workflow_runs

    fake_runs = {
        "workflow_runs": [
            {
                "id": 999,
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-04-16T08:30:00Z",
                "updated_at": "2026-04-16T08:31:00Z",
                "run_started_at": "2026-04-16T08:30:05Z",
                "html_url": "https://github.com/run/999",
            },
        ],
    }
    request = FakeRequest(
        match_info={"workflow_id": "morning_check.yml"},
        query={"limit": "5"},
    )
    with _patch_auth(), _mock_github_session(fake_runs):
        response = await get_workflow_runs(request)

    data = json.loads(response.body)
    assert len(data["runs"]) == 1
    assert data["runs"][0]["id"] == 999
    assert data["runs"][0]["conclusion"] == "success"
    assert data["runs"][0]["duration_seconds"] == 55


@pytest.mark.asyncio
async def test_trigger_workflow():
    from routes.mini_api import trigger_workflow_endpoint

    request = FakeRequest()
    request._json_body = {"workflow_id": "morning_check.yml"}
    request.json = request.json  # use the method from FakeRequest

    with _patch_auth():
        with patch("routes.mini_api.trigger_workflow", new_callable=AsyncMock, return_value=(True, None)):
            response = await trigger_workflow_endpoint(request)

    data = json.loads(response.body)
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_trigger_workflow_failure():
    from routes.mini_api import trigger_workflow_endpoint

    request = FakeRequest()
    request._json_body = {"workflow_id": "morning_check.yml"}

    with _patch_auth():
        with patch("routes.mini_api.trigger_workflow", new_callable=AsyncMock, return_value=(False, "HTTP 422")):
            response = await trigger_workflow_endpoint(request)

    assert response.status == 502
    data = json.loads(response.body)
    assert data["ok"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mini_workflows.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_workflows' from 'routes.mini_api'`

- [ ] **Step 3: Implement the workflows endpoints**

Add to `webhook/routes/mini_api.py` (append after the existing skeleton):

```python
import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp

from workflow_trigger import (
    WORKFLOW_CATALOG, _gh_headers, trigger_workflow,
    GITHUB_OWNER, GITHUB_REPO,
)

_GH_API = "https://api.github.com"

_WORKFLOW_ICONS = {
    "morning_check.yml": "\U0001f4ca",
    "baltic_ingestion.yml": "\u2693",
    "daily_report.yml": "\U0001f4c8",
    "market_news.yml": "\U0001f4f0",
    "platts_reports.yml": "\U0001f4c4",
}


def _run_duration(run: dict) -> int | None:
    """Calculate run duration in seconds from GitHub run data."""
    if run.get("status") != "completed":
        return None
    started = run.get("run_started_at") or run.get("created_at")
    ended = run.get("updated_at")
    if not started or not ended:
        return None
    s = datetime.fromisoformat(started.replace("Z", "+00:00"))
    e = datetime.fromisoformat(ended.replace("Z", "+00:00"))
    return max(0, int((e - s).total_seconds()))


async def _fetch_github_runs(per_page: int = 100) -> dict:
    """Fetch recent workflow runs from GitHub Actions API."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs",
                headers=_gh_headers(),
                params={"per_page": per_page},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as exc:
        logger.error(f"GitHub API error: {exc}")
    return {"workflow_runs": []}


@routes.get("/api/mini/workflows")
async def get_workflows(request: web.Request) -> web.Response:
    await validate_init_data(request)

    data = await _fetch_github_runs()
    runs = data.get("workflow_runs", [])

    last_runs: dict[str, dict] = {}
    recent_by_wf: dict[str, list] = {}
    for run in runs:
        path = run.get("path", "")
        wf_id = path.split("/")[-1] if "/" in path else path
        if wf_id not in last_runs:
            last_runs[wf_id] = run
        if wf_id not in recent_by_wf:
            recent_by_wf[wf_id] = []
        if len(recent_by_wf[wf_id]) < 5:
            recent_by_wf[wf_id].append(run)

    workflows = []
    for wf in WORKFLOW_CATALOG:
        last = last_runs.get(wf["id"])
        recents = recent_by_wf.get(wf["id"], [])

        completed = [r for r in recents if r.get("status") == "completed"]
        successes = [r for r in completed if r.get("conclusion") == "success"]
        health = round(len(successes) / len(completed) * 100) if completed else 100

        last_run = None
        if last:
            last_run = {
                "status": last.get("status", "unknown"),
                "conclusion": last.get("conclusion"),
                "created_at": last.get("created_at"),
                "duration_seconds": _run_duration(last),
            }

        recent_runs_data = [
            {"conclusion": r.get("conclusion"), "created_at": r.get("created_at")}
            for r in recents
        ]

        workflows.append({
            "id": wf["id"],
            "name": wf["name"],
            "description": wf["description"],
            "icon": _WORKFLOW_ICONS.get(wf["id"], "\u2753"),
            "last_run": last_run,
            "health_pct": health,
            "recent_runs": recent_runs_data,
        })

    return web.json_response({"workflows": workflows})


@routes.get("/api/mini/workflows/{workflow_id}/runs")
async def get_workflow_runs(request: web.Request) -> web.Response:
    await validate_init_data(request)
    workflow_id = request.match_info["workflow_id"]
    limit = int(request.query.get("limit", "5"))

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/workflows/{workflow_id}/runs",
                headers=_gh_headers(),
                params={"per_page": limit},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return web.json_response({"runs": []})
                data = await resp.json()
    except Exception as exc:
        logger.error(f"workflow runs API error: {exc}")
        return web.json_response({"runs": []})

    runs = []
    for run in data.get("workflow_runs", [])[:limit]:
        runs.append({
            "id": run.get("id"),
            "status": run.get("status", "unknown"),
            "conclusion": run.get("conclusion"),
            "created_at": run.get("created_at"),
            "duration_seconds": _run_duration(run),
            "error": None,
            "html_url": run.get("html_url", ""),
        })

    return web.json_response({"runs": runs})


@routes.post("/api/mini/trigger")
async def trigger_workflow_endpoint(request: web.Request) -> web.Response:
    await validate_init_data(request)
    body = await request.json()
    workflow_id = body.get("workflow_id", "")
    if not workflow_id:
        return web.json_response({"ok": False, "error": "Missing workflow_id"}, status=400)

    ok, error = await trigger_workflow(workflow_id)
    if not ok:
        return web.json_response({"ok": False, "error": error}, status=502)
    return web.json_response({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mini_workflows.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add webhook/routes/mini_api.py tests/test_mini_workflows.py
git commit -m "feat(mini-api): workflows endpoints — list, runs, trigger"
```

---

### Task 3: News API Endpoints

**Files:**
- Modify: `webhook/routes/mini_api.py`
- Create: `tests/test_mini_news.py`

**Context:** News items live in three Redis stores: staging (pending), archive (archived), and feedback (rejected). The existing `webhook/redis_queries.py` provides `list_staging()`, `list_archive_recent()`, and `list_feedback()`. The `execution/curation/redis_client.py` provides `get_staging()` and `get_archive()` for single-item lookups.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mini_news.py`:

```python
"""Tests for /api/mini/news endpoints."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))


class FakeRequest:
    def __init__(self, headers=None, query=None, match_info=None):
        self.headers = headers or {}
        self.query = query or {}
        self.match_info = match_info or {}


def _patch_auth():
    mock_data = MagicMock()
    mock_data.user = MagicMock()
    mock_data.user.id = 12345
    return patch("routes.mini_api.validate_init_data", new_callable=AsyncMock, return_value=mock_data)


STAGING_ITEMS = [
    {
        "id": "platts_001",
        "title": "Iron ore surges on China stimulus",
        "source": "Platts",
        "source_feed": "allInsights",
        "publishDate": "2026-04-16T08:45:00Z",
        "fullText": "Full article text here...",
        "tables": [],
        "stagedAt": "2026-04-16T09:00:00Z",
    },
    {
        "id": "platts_002",
        "title": "Steel output rises in March",
        "source": "Platts",
        "source_feed": "allInsights",
        "publishDate": "2026-04-16T07:30:00Z",
        "fullText": "Another article...",
        "tables": [{"header": ["Col1"], "rows": [["val"]]}],
        "stagedAt": "2026-04-16T08:00:00Z",
    },
]

ARCHIVE_ITEMS = [
    {
        "id": "platts_003",
        "title": "BDI drops to 3-month low",
        "source": "Platts",
        "source_feed": "balticExchange",
        "publishDate": "2026-04-15T14:00:00Z",
        "fullText": "Archived article...",
        "tables": [],
        "archivedAt": "2026-04-15T15:00:00Z",
        "archived_date": "2026-04-15",
    },
]

FEEDBACK_ITEMS = [
    {
        "feedback_id": "1713200000.000-platts_004",
        "action": "curate_reject",
        "item_id": "platts_004",
        "title": "Irrelevant article about crypto",
        "timestamp": 1713200000.0,
        "chat_id": 12345,
        "reason": "Off topic",
    },
]


@pytest.mark.asyncio
async def test_get_news_pending():
    from routes.mini_api import get_news

    request = FakeRequest(query={"status": "pending", "page": "1", "limit": "20"})
    with _patch_auth():
        with patch("routes.mini_api.redis_queries") as mock_rq:
            mock_rq.list_staging.return_value = STAGING_ITEMS
            response = await get_news(request)

    data = json.loads(response.body)
    assert len(data["items"]) == 2
    assert data["items"][0]["status"] == "pending"
    assert data["items"][0]["id"] == "platts_001"
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_get_news_archived():
    from routes.mini_api import get_news

    request = FakeRequest(query={"status": "archived", "page": "1", "limit": "20"})
    with _patch_auth():
        with patch("routes.mini_api.redis_queries") as mock_rq:
            mock_rq.list_archive_recent.return_value = ARCHIVE_ITEMS
            response = await get_news(request)

    data = json.loads(response.body)
    assert len(data["items"]) == 1
    assert data["items"][0]["status"] == "archived"


@pytest.mark.asyncio
async def test_get_news_rejected():
    from routes.mini_api import get_news

    request = FakeRequest(query={"status": "rejected", "page": "1", "limit": "20"})
    with _patch_auth():
        with patch("routes.mini_api.redis_queries") as mock_rq:
            mock_rq.list_feedback.return_value = FEEDBACK_ITEMS
            response = await get_news(request)

    data = json.loads(response.body)
    assert len(data["items"]) == 1
    assert data["items"][0]["status"] == "rejected"
    assert data["items"][0]["title"] == "Irrelevant article about crypto"


@pytest.mark.asyncio
async def test_get_news_all():
    from routes.mini_api import get_news

    request = FakeRequest(query={"status": "all", "page": "1", "limit": "20"})
    with _patch_auth():
        with patch("routes.mini_api.redis_queries") as mock_rq:
            mock_rq.list_staging.return_value = STAGING_ITEMS
            mock_rq.list_archive_recent.return_value = ARCHIVE_ITEMS
            mock_rq.list_feedback.return_value = FEEDBACK_ITEMS
            response = await get_news(request)

    data = json.loads(response.body)
    assert data["total"] == 4
    assert len(data["items"]) == 4


@pytest.mark.asyncio
async def test_get_news_pagination():
    from routes.mini_api import get_news

    request = FakeRequest(query={"status": "pending", "page": "2", "limit": "1"})
    with _patch_auth():
        with patch("routes.mini_api.redis_queries") as mock_rq:
            mock_rq.list_staging.return_value = STAGING_ITEMS
            response = await get_news(request)

    data = json.loads(response.body)
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == "platts_002"
    assert data["page"] == 2
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_get_news_detail():
    from routes.mini_api import get_news_detail

    request = FakeRequest(match_info={"item_id": "platts_001"})
    with _patch_auth():
        with patch("routes.mini_api.redis_client") as mock_rc:
            mock_rc.get_staging.return_value = STAGING_ITEMS[0]
            response = await get_news_detail(request)

    data = json.loads(response.body)
    assert data["id"] == "platts_001"
    assert data["fullText"] == "Full article text here..."
    assert data["status"] == "pending"


@pytest.mark.asyncio
async def test_get_news_detail_archived():
    from routes.mini_api import get_news_detail

    request = FakeRequest(match_info={"item_id": "platts_003"})
    with _patch_auth():
        with patch("routes.mini_api.redis_client") as mock_rc:
            mock_rc.get_staging.return_value = None
            mock_rc.get_archive.return_value = ARCHIVE_ITEMS[0]
            response = await get_news_detail(request)

    data = json.loads(response.body)
    assert data["id"] == "platts_003"
    assert data["status"] == "archived"


@pytest.mark.asyncio
async def test_get_news_detail_not_found():
    from routes.mini_api import get_news_detail

    request = FakeRequest(match_info={"item_id": "nonexistent"})
    with _patch_auth():
        with patch("routes.mini_api.redis_client") as mock_rc:
            mock_rc.get_staging.return_value = None
            mock_rc.get_archive.return_value = None
            response = await get_news_detail(request)

    assert response.status == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mini_news.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_news' from 'routes.mini_api'`

- [ ] **Step 3: Implement the news endpoints**

Add these imports to the top of `webhook/routes/mini_api.py`:

```python
import redis_queries
from execution.curation import redis_client
```

Append the news endpoints:

```python
_REJECT_ACTIONS = {"curate_reject", "draft_reject"}


def _staging_to_news_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "source_feed": item.get("source_feed", ""),
        "date": item.get("publishDate", ""),
        "status": "pending",
        "preview_url": f"/preview/{item.get('id', '')}",
    }


def _archive_to_news_item(item: dict) -> dict:
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "source_feed": item.get("source_feed", ""),
        "date": item.get("publishDate", item.get("archivedAt", "")),
        "status": "archived",
        "preview_url": f"/preview/{item.get('id', '')}",
    }


def _feedback_to_news_item(item: dict) -> dict:
    ts = item.get("timestamp", 0)
    date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
    return {
        "id": item.get("item_id", ""),
        "title": item.get("title", ""),
        "source": "",
        "source_feed": "",
        "date": date,
        "status": "rejected",
        "preview_url": None,
    }


@routes.get("/api/mini/news")
async def get_news(request: web.Request) -> web.Response:
    await validate_init_data(request)
    status_filter = request.query.get("status", "all")
    page = int(request.query.get("page", "1"))
    limit = int(request.query.get("limit", "20"))

    items = []
    if status_filter in ("all", "pending"):
        staging = await asyncio.to_thread(redis_queries.list_staging, 500)
        items.extend(_staging_to_news_item(i) for i in staging)

    if status_filter in ("all", "archived"):
        archived = await asyncio.to_thread(redis_queries.list_archive_recent, 500)
        items.extend(_archive_to_news_item(i) for i in archived)

    if status_filter in ("all", "rejected"):
        feedback = await asyncio.to_thread(redis_queries.list_feedback, 500)
        rejected = [f for f in feedback if f.get("action") in _REJECT_ACTIONS]
        items.extend(_feedback_to_news_item(i) for i in rejected)

    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    total = len(items)
    start = (page - 1) * limit
    page_items = items[start : start + limit]

    return web.json_response({
        "items": page_items,
        "total": total,
        "page": page,
    })


@routes.get("/api/mini/news/{item_id}")
async def get_news_detail(request: web.Request) -> web.Response:
    await validate_init_data(request)
    item_id = request.match_info["item_id"]

    item = await asyncio.to_thread(redis_client.get_staging, item_id)
    status = "pending"
    if item is None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        item = await asyncio.to_thread(redis_client.get_archive, today, item_id)
        status = "archived"
    if item is None:
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        item = await asyncio.to_thread(redis_client.get_archive, yesterday, item_id)
        status = "archived"
    if item is None:
        return web.json_response({"error": "Item not found"}, status=404)

    return web.json_response({
        "id": item_id,
        "title": item.get("title", ""),
        "source": item.get("source", ""),
        "source_feed": item.get("source_feed", ""),
        "date": item.get("publishDate", ""),
        "status": status,
        "fullText": item.get("fullText", ""),
        "tables": item.get("tables", []),
        "preview_url": f"/preview/{item_id}",
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mini_news.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add webhook/routes/mini_api.py tests/test_mini_news.py
git commit -m "feat(mini-api): news endpoints — list with status filter + detail"
```

---

### Task 4: Reports API Endpoints

**Files:**
- Modify: `webhook/routes/mini_api.py`
- Create: `tests/test_mini_reports.py`

**Context:** Reports metadata lives in Supabase table `platts_reports` (columns: id, report_name, date_key, frequency, report_type, storage_path). PDFs are in Supabase Storage bucket `platts-reports`. The existing `webhook/reports_nav.py` has a `_get_supabase()` singleton — reuse it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mini_reports.py`:

```python
"""Tests for /api/mini/reports endpoints."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))


class FakeRequest:
    def __init__(self, headers=None, query=None, match_info=None):
        self.headers = headers or {}
        self.query = query or {}
        self.match_info = match_info or {}


def _patch_auth():
    mock_data = MagicMock()
    mock_data.user = MagicMock()
    mock_data.user.id = 12345
    return patch("routes.mini_api.validate_init_data", new_callable=AsyncMock, return_value=mock_data)


FAKE_REPORTS = [
    {"id": "uuid-1", "report_name": "Iron Ore Monthly", "date_key": "2026-04-15", "frequency": "monthly"},
    {"id": "uuid-2", "report_name": "Steel Weekly", "date_key": "2026-04-10", "frequency": "weekly"},
    {"id": "uuid-3", "report_name": "Iron Ore Monthly", "date_key": "2026-03-15", "frequency": "monthly"},
]


def _mock_supabase(reports=None, storage_url="https://signed.url/report.pdf"):
    """Mock the Supabase client."""
    mock_sb = MagicMock()

    # Table query chain
    mock_query = MagicMock()
    mock_result = MagicMock()
    mock_result.data = reports if reports is not None else FAKE_REPORTS
    mock_query.execute.return_value = mock_result
    mock_query.eq.return_value = mock_query
    mock_query.gte.return_value = mock_query
    mock_query.lte.return_value = mock_query
    mock_query.lt.return_value = mock_query
    mock_query.order.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.single.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_sb.table.return_value = mock_query

    # Storage signed URL
    mock_storage_bucket = MagicMock()
    mock_storage_bucket.create_signed_url.return_value = {"signedURL": storage_url}
    mock_sb.storage.from_.return_value = mock_storage_bucket

    return patch("routes.mini_api._get_supabase", return_value=mock_sb)


@pytest.mark.asyncio
async def test_get_reports_by_type():
    from routes.mini_api import get_reports

    request = FakeRequest(query={"type": "Market Reports"})
    with _patch_auth(), _mock_supabase():
        response = await get_reports(request)

    data = json.loads(response.body)
    assert "reports" in data
    assert len(data["reports"]) == 3
    assert data["reports"][0]["download_url"] == "/api/mini/reports/uuid-1/download"


@pytest.mark.asyncio
async def test_get_reports_with_year():
    from routes.mini_api import get_reports

    request = FakeRequest(query={"type": "Market Reports", "year": "2026"})
    with _patch_auth(), _mock_supabase():
        response = await get_reports(request)

    data = json.loads(response.body)
    assert "reports" in data


@pytest.mark.asyncio
async def test_get_reports_with_month():
    from routes.mini_api import get_reports

    request = FakeRequest(query={"type": "Market Reports", "year": "2026", "month": "4"})
    with _patch_auth(), _mock_supabase():
        response = await get_reports(request)

    data = json.loads(response.body)
    assert "reports" in data


@pytest.mark.asyncio
async def test_get_reports_supabase_not_configured():
    from routes.mini_api import get_reports

    request = FakeRequest(query={"type": "Market Reports"})
    with _patch_auth():
        with patch("routes.mini_api._get_supabase", return_value=None):
            response = await get_reports(request)

    assert response.status == 503


@pytest.mark.asyncio
async def test_download_report():
    from routes.mini_api import download_report

    request = FakeRequest(match_info={"report_id": "uuid-1"})
    single_report = [{"storage_path": "2026/04/report.pdf"}]
    with _patch_auth(), _mock_supabase(reports=single_report):
        response = await download_report(request)

    data = json.loads(response.body)
    assert "download_url" in data
    assert data["download_url"] == "https://signed.url/report.pdf"


@pytest.mark.asyncio
async def test_download_report_not_found():
    from routes.mini_api import download_report

    request = FakeRequest(match_info={"report_id": "nonexistent"})
    with _patch_auth(), _mock_supabase(reports=[]):
        response = await download_report(request)

    assert response.status == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mini_reports.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_reports' from 'routes.mini_api'`

- [ ] **Step 3: Implement the reports endpoints**

Add this import to the top of `webhook/routes/mini_api.py`:

```python
from reports_nav import _get_supabase
```

Append the reports endpoints:

```python
@routes.get("/api/mini/reports")
async def get_reports(request: web.Request) -> web.Response:
    await validate_init_data(request)
    report_type = request.query.get("type", "")
    year = request.query.get("year")
    month = request.query.get("month")

    sb = _get_supabase()
    if not sb:
        return web.json_response({"error": "Supabase not configured"}, status=503)

    def _query():
        q = sb.table("platts_reports").select("id, report_name, date_key, frequency")
        if report_type:
            q = q.eq("report_type", report_type)
        if year and month:
            m = int(month)
            y = int(year)
            start = f"{y}-{m:02d}-01"
            end = f"{y + 1}-01-01" if m == 12 else f"{y}-{m + 1:02d}-01"
            q = q.gte("date_key", start).lt("date_key", end)
        elif year:
            q = q.gte("date_key", f"{year}-01-01").lte("date_key", f"{year}-12-31")
        else:
            q = q.limit(10)
        return q.order("date_key", desc=True).execute()

    try:
        result = await asyncio.to_thread(_query)
    except Exception as exc:
        logger.error(f"reports query error: {exc}")
        return web.json_response({"error": "Query failed"}, status=500)

    reports = [
        {
            "id": r["id"],
            "report_name": r["report_name"],
            "date_key": r["date_key"],
            "download_url": f"/api/mini/reports/{r['id']}/download",
        }
        for r in (result.data or [])
    ]
    return web.json_response({"reports": reports})


@routes.get("/api/mini/reports/{report_id}/download")
async def download_report(request: web.Request) -> web.Response:
    await validate_init_data(request)
    report_id = request.match_info["report_id"]

    sb = _get_supabase()
    if not sb:
        return web.json_response({"error": "Supabase not configured"}, status=503)

    def _get_signed_url():
        row = sb.table("platts_reports") \
            .select("storage_path") \
            .eq("id", report_id) \
            .single() \
            .execute()
        if not row.data:
            return None
        storage_path = row.data["storage_path"]
        signed = sb.storage.from_("platts-reports").create_signed_url(storage_path, 3600)
        return signed.get("signedURL") if signed else None

    try:
        url = await asyncio.to_thread(_get_signed_url)
    except Exception as exc:
        logger.error(f"report download error: {exc}")
        return web.json_response({"error": "Download failed"}, status=500)

    if not url:
        return web.json_response({"error": "Report not found"}, status=404)
    return web.json_response({"download_url": url})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mini_reports.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add webhook/routes/mini_api.py tests/test_mini_reports.py
git commit -m "feat(mini-api): reports endpoints — list by type/year/month + signed download URL"
```

---

### Task 5: Contacts API Endpoints

**Files:**
- Modify: `webhook/routes/mini_api.py`
- Create: `tests/test_mini_contacts.py`

**Context:** Contacts live in Google Sheets. The existing `execution/integrations/sheets_client.py` has `SheetsClient` with `list_contacts()` and `toggle_contact()`. The sheet ID comes from `bot.config.SHEET_ID`. The active/inactive check is `ButtonPayload == "Big"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mini_contacts.py`:

```python
"""Tests for /api/mini/contacts endpoints."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))


class FakeRequest:
    def __init__(self, headers=None, query=None, match_info=None):
        self.headers = headers or {}
        self.query = query or {}
        self.match_info = match_info or {}


def _patch_auth():
    mock_data = MagicMock()
    mock_data.user = MagicMock()
    mock_data.user.id = 12345
    return patch("routes.mini_api.validate_init_data", new_callable=AsyncMock, return_value=mock_data)


FAKE_CONTACTS = [
    {"ProfileName": "Joao Silva", "Evolution-api": "5511999001122", "ButtonPayload": "Big"},
    {"ProfileName": "Maria Santos", "Evolution-api": "5511999003344", "ButtonPayload": "Inactive"},
    {"ProfileName": "Pedro Costa", "Evolution-api": "5511999005566", "ButtonPayload": "Big"},
]


@pytest.mark.asyncio
async def test_get_contacts():
    from routes.mini_api import get_contacts

    request = FakeRequest(query={"page": "1"})
    mock_sheets = MagicMock()
    mock_sheets.list_contacts.return_value = (FAKE_CONTACTS, 1)

    with _patch_auth():
        with patch("routes.mini_api.SheetsClient", return_value=mock_sheets):
            response = await get_contacts(request)

    data = json.loads(response.body)
    assert len(data["contacts"]) == 3
    assert data["contacts"][0]["name"] == "Joao Silva"
    assert data["contacts"][0]["active"] is True
    assert data["contacts"][1]["active"] is False
    assert data["total"] == 3
    assert data["page"] == 1


@pytest.mark.asyncio
async def test_get_contacts_with_search():
    from routes.mini_api import get_contacts

    filtered = [FAKE_CONTACTS[0]]
    mock_sheets = MagicMock()
    mock_sheets.list_contacts.return_value = (filtered, 1)

    request = FakeRequest(query={"search": "Joao", "page": "1"})
    with _patch_auth():
        with patch("routes.mini_api.SheetsClient", return_value=mock_sheets):
            response = await get_contacts(request)

    data = json.loads(response.body)
    assert len(data["contacts"]) == 1
    assert data["contacts"][0]["name"] == "Joao Silva"


@pytest.mark.asyncio
async def test_toggle_contact():
    from routes.mini_api import toggle_contact

    mock_sheets = MagicMock()
    mock_sheets.toggle_contact.return_value = ("Joao Silva", "Inactive")

    request = FakeRequest(match_info={"phone": "5511999001122"})
    with _patch_auth():
        with patch("routes.mini_api.SheetsClient", return_value=mock_sheets):
            response = await toggle_contact(request)

    data = json.loads(response.body)
    assert data["name"] == "Joao Silva"
    assert data["active"] is False


@pytest.mark.asyncio
async def test_toggle_contact_not_found():
    from routes.mini_api import toggle_contact

    mock_sheets = MagicMock()
    mock_sheets.toggle_contact.side_effect = ValueError("Not found")

    request = FakeRequest(match_info={"phone": "0000000000"})
    with _patch_auth():
        with patch("routes.mini_api.SheetsClient", return_value=mock_sheets):
            response = await toggle_contact(request)

    assert response.status == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mini_contacts.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_contacts' from 'routes.mini_api'`

- [ ] **Step 3: Implement the contacts endpoints**

Add these imports to the top of `webhook/routes/mini_api.py`:

```python
from bot.config import SHEET_ID
from execution.integrations.sheets_client import SheetsClient
```

Append the contacts endpoints:

```python
def _phone_from_contact(contact: dict) -> str:
    """Extract phone number from contact dict (tries multiple column names)."""
    for col in ("Evolution-api", "n8n-evo", "From"):
        val = contact.get(col, "")
        if val:
            return str(val).strip()
    return ""


@routes.get("/api/mini/contacts")
async def get_contacts(request: web.Request) -> web.Response:
    await validate_init_data(request)
    search = request.query.get("search", "").strip() or None
    page = int(request.query.get("page", "1"))

    try:
        sheets = SheetsClient()
        contacts, total_pages = await asyncio.to_thread(
            sheets.list_contacts, SHEET_ID, search=search, page=page, per_page=20,
        )
    except Exception as exc:
        logger.error(f"contacts query error: {exc}")
        return web.json_response({"error": "Failed to fetch contacts"}, status=500)

    result = [
        {
            "name": c.get("ProfileName", ""),
            "phone": _phone_from_contact(c),
            "active": str(c.get("ButtonPayload", "")).strip() == "Big",
        }
        for c in contacts
    ]
    return web.json_response({
        "contacts": result,
        "total": len(result),
        "page": page,
    })


@routes.post("/api/mini/contacts/{phone}/toggle")
async def toggle_contact(request: web.Request) -> web.Response:
    await validate_init_data(request)
    phone = request.match_info["phone"]

    try:
        sheets = SheetsClient()
        name, new_status = await asyncio.to_thread(
            sheets.toggle_contact, SHEET_ID, phone,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except Exception as exc:
        logger.error(f"toggle contact error: {exc}")
        return web.json_response({"error": "Toggle failed"}, status=500)

    return web.json_response({
        "name": name,
        "phone": phone,
        "active": new_status == "Big",
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mini_contacts.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add webhook/routes/mini_api.py tests/test_mini_contacts.py
git commit -m "feat(mini-api): contacts endpoints — list with search + toggle active"
```

---

### Task 6: Stats API Endpoint

**Files:**
- Modify: `webhook/routes/mini_api.py`
- Create: `tests/test_mini_stats.py`

**Context:** The stats endpoint is a composite: it aggregates data from GitHub (workflow health), Redis (news count), and Google Sheets (active contacts). It uses `asyncio.gather()` to fetch all sources in parallel.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mini_stats.py`:

```python
"""Tests for /api/mini/stats endpoint."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))


class FakeRequest:
    def __init__(self, headers=None, query=None, match_info=None):
        self.headers = headers or {}
        self.query = query or {}
        self.match_info = match_info or {}


def _patch_auth():
    mock_data = MagicMock()
    mock_data.user = MagicMock()
    mock_data.user.id = 12345
    return patch("routes.mini_api.validate_init_data", new_callable=AsyncMock, return_value=mock_data)


FAKE_GITHUB_RUNS = {
    "workflow_runs": [
        {"path": ".github/workflows/morning_check.yml", "status": "completed", "conclusion": "success",
         "created_at": "2026-04-16T08:00:00Z", "updated_at": "2026-04-16T08:01:00Z"},
        {"path": ".github/workflows/baltic_ingestion.yml", "status": "completed", "conclusion": "success",
         "created_at": "2026-04-16T09:00:00Z", "updated_at": "2026-04-16T09:01:00Z"},
        {"path": ".github/workflows/daily_report.yml", "status": "completed", "conclusion": "success",
         "created_at": "2026-04-16T10:00:00Z", "updated_at": "2026-04-16T10:01:00Z"},
        {"path": ".github/workflows/market_news.yml", "status": "completed", "conclusion": "failure",
         "created_at": "2026-04-16T11:00:00Z", "updated_at": "2026-04-16T11:01:00Z"},
        {"path": ".github/workflows/platts_reports.yml", "status": "completed", "conclusion": "success",
         "created_at": "2026-04-16T12:00:00Z", "updated_at": "2026-04-16T12:01:00Z"},
    ],
    "total_count": 47,
}

FAKE_CONTACTS = [
    {"ProfileName": "A", "ButtonPayload": "Big"},
    {"ProfileName": "B", "ButtonPayload": "Big"},
    {"ProfileName": "C", "ButtonPayload": "Inactive"},
]

FAKE_STAGING = [{"id": f"item_{i}"} for i in range(12)]


def _mock_github(response_data=None):
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=response_data or FAKE_GITHUB_RUNS)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return patch("routes.mini_api.aiohttp.ClientSession", return_value=mock_session)


@pytest.mark.asyncio
async def test_get_stats():
    from routes.mini_api import get_stats

    mock_sheets = MagicMock()
    mock_sheets.list_contacts.return_value = (FAKE_CONTACTS, 1)

    request = FakeRequest()
    with _patch_auth(), _mock_github():
        with patch("routes.mini_api.redis_queries") as mock_rq:
            mock_rq.list_staging.return_value = FAKE_STAGING
            with patch("routes.mini_api.SheetsClient", return_value=mock_sheets):
                response = await get_stats(request)

    data = json.loads(response.body)
    assert data["health_pct"] == 80  # 4 of 5 workflows OK
    assert data["workflows_ok"] == 4
    assert data["workflows_total"] == 5
    assert data["runs_today"] == 47
    assert data["contacts_active"] == 2
    assert data["news_today"] == 12


@pytest.mark.asyncio
async def test_get_stats_github_failure():
    from routes.mini_api import get_stats

    mock_sheets = MagicMock()
    mock_sheets.list_contacts.return_value = ([], 1)

    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    request = FakeRequest()
    with _patch_auth():
        with patch("routes.mini_api.aiohttp.ClientSession", return_value=mock_session):
            with patch("routes.mini_api.redis_queries") as mock_rq:
                mock_rq.list_staging.return_value = []
                with patch("routes.mini_api.SheetsClient", return_value=mock_sheets):
                    response = await get_stats(request)

    data = json.loads(response.body)
    assert data["health_pct"] == 0
    assert data["workflows_ok"] == 0
    assert data["runs_today"] == 0


@pytest.mark.asyncio
async def test_get_stats_all_services_fail():
    from routes.mini_api import get_stats

    mock_sheets = MagicMock()
    mock_sheets.list_contacts.side_effect = Exception("Sheets down")

    mock_resp = AsyncMock()
    mock_resp.status = 500
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    request = FakeRequest()
    with _patch_auth():
        with patch("routes.mini_api.aiohttp.ClientSession", return_value=mock_session):
            with patch("routes.mini_api.redis_queries") as mock_rq:
                mock_rq.list_staging.side_effect = Exception("Redis down")
                with patch("routes.mini_api.SheetsClient", return_value=mock_sheets):
                    response = await get_stats(request)

    # Should still return 200 with zeroed stats
    assert response.status == 200
    data = json.loads(response.body)
    assert data["contacts_active"] == 0
    assert data["news_today"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mini_stats.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_stats' from 'routes.mini_api'`

- [ ] **Step 3: Implement the stats endpoint**

Append to `webhook/routes/mini_api.py`:

```python
async def _fetch_workflow_health() -> dict:
    """Compute workflow health from GitHub runs. Returns {health_pct, ok, total}."""
    try:
        data = await _fetch_github_runs()
        runs = data.get("workflow_runs", [])

        by_wf: dict[str, list] = {}
        for run in runs:
            path = run.get("path", "")
            wf_id = path.split("/")[-1] if "/" in path else path
            if wf_id not in by_wf:
                by_wf[wf_id] = []
            if len(by_wf[wf_id]) < 5:
                by_wf[wf_id].append(run)

        ok_count = 0
        for wf in WORKFLOW_CATALOG:
            wf_runs = by_wf.get(wf["id"], [])
            completed = [r for r in wf_runs if r.get("status") == "completed"]
            if completed and all(r.get("conclusion") == "success" for r in completed):
                ok_count += 1

        total = len(WORKFLOW_CATALOG)
        health_pct = round(ok_count / total * 100) if total else 0
        return {"health_pct": health_pct, "ok": ok_count, "total": total}
    except Exception as exc:
        logger.error(f"workflow health error: {exc}")
        return {"health_pct": 0, "ok": 0, "total": len(WORKFLOW_CATALOG)}


async def _fetch_contacts_active() -> int:
    """Count active contacts from Google Sheets."""
    try:
        sheets = SheetsClient()
        contacts, _ = await asyncio.to_thread(
            sheets.list_contacts, SHEET_ID, page=1, per_page=10_000,
        )
        return sum(1 for c in contacts if str(c.get("ButtonPayload", "")).strip() == "Big")
    except Exception as exc:
        logger.error(f"contacts count error: {exc}")
        return 0


async def _fetch_news_count() -> int:
    """Count news items in staging."""
    try:
        items = await asyncio.to_thread(redis_queries.list_staging, 500)
        return len(items)
    except Exception as exc:
        logger.error(f"news count error: {exc}")
        return 0


async def _fetch_runs_today() -> int:
    """Count GitHub Actions runs created today."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_GH_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/actions/runs",
                headers=_gh_headers(),
                params={"per_page": 1, "created": f">={today}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("total_count", 0)
    except Exception as exc:
        logger.error(f"runs today error: {exc}")
    return 0


@routes.get("/api/mini/stats")
async def get_stats(request: web.Request) -> web.Response:
    await validate_init_data(request)

    wf_health, contacts_active, news_count, runs_today = await asyncio.gather(
        _fetch_workflow_health(),
        _fetch_contacts_active(),
        _fetch_news_count(),
        _fetch_runs_today(),
    )

    return web.json_response({
        "health_pct": wf_health["health_pct"],
        "workflows_ok": wf_health["ok"],
        "workflows_total": wf_health["total"],
        "runs_today": runs_today,
        "contacts_active": contacts_active,
        "news_today": news_count,
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mini_stats.py -v`
Expected: all 3 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: all existing tests PASS + all new mini API tests PASS

- [ ] **Step 6: Commit**

```bash
git add webhook/routes/mini_api.py tests/test_mini_stats.py
git commit -m "feat(mini-api): stats endpoint — composite health/contacts/news counts"
```
