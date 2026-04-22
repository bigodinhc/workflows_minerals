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

from datetime import datetime, timezone as _tz
from execution.integrations.contacts_repo import Contact as _Contact
_N = datetime.now(_tz.utc)
# Only active contacts are returned by ContactsRepo.list_active (DB filter).
FAKE_ACTIVE_CONTACTS = [
    _Contact(id="a", name="A", phone_raw="1", phone_uazapi="1",
             status="ativo", created_at=_N, updated_at=_N),
    _Contact(id="b", name="B", phone_raw="2", phone_uazapi="2",
             status="ativo", created_at=_N, updated_at=_N),
]

FAKE_STAGING = [{"id": f"item_{i}"} for i in range(12)]


def _mock_github(response_data=None):
    """Mock aiohttp.ClientSession to return GitHub-like response for ALL .get() calls."""
    data = response_data or FAKE_GITHUB_RUNS
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=data)
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
    mock_repo = MagicMock()
    mock_repo.list_active.return_value = FAKE_ACTIVE_CONTACTS
    request = FakeRequest()
    with _patch_auth(), _mock_github():
        with patch("routes.mini_api.redis_queries") as mock_rq:
            mock_rq.list_staging.return_value = FAKE_STAGING
            with patch("routes.mini_api.ContactsRepo", return_value=mock_repo):
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
    mock_repo = MagicMock()
    mock_repo.list_active.return_value = []
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
                with patch("routes.mini_api.ContactsRepo", return_value=mock_repo):
                    response = await get_stats(request)
    data = json.loads(response.body)
    assert data["health_pct"] == 0
    assert data["workflows_ok"] == 0
    assert data["runs_today"] == 0


@pytest.mark.asyncio
async def test_get_stats_all_services_fail():
    from routes.mini_api import get_stats
    mock_repo = MagicMock()
    mock_repo.list_active.side_effect = Exception("Supabase down")
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
                with patch("routes.mini_api.ContactsRepo", return_value=mock_repo):
                    response = await get_stats(request)
    assert response.status == 200
    data = json.loads(response.body)
    assert data["contacts_active"] == 0
    assert data["news_today"] == 0
