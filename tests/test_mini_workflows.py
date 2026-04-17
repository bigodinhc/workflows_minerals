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
        self._json_body = {}

    async def json(self):
        return self._json_body

    def set_json(self, body):
        self._json_body = body
        return self


def _patch_auth():
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
    assert mc["health_pct"] == 50
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
    assert mc["health_pct"] == 100


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
