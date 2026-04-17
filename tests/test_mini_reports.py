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


_SENTINEL = object()


def _mock_supabase(reports=_SENTINEL, storage_url="https://signed.url/report.pdf"):
    mock_sb = MagicMock()
    mock_query = MagicMock()
    mock_result = MagicMock()
    mock_result.data = FAKE_REPORTS if reports is _SENTINEL else reports
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
    single_report = {"storage_path": "2026/04/report.pdf"}
    with _patch_auth(), _mock_supabase(reports=single_report):
        response = await download_report(request)
    data = json.loads(response.body)
    assert "download_url" in data
    assert data["download_url"] == "https://signed.url/report.pdf"


@pytest.mark.asyncio
async def test_download_report_not_found():
    from routes.mini_api import download_report
    request = FakeRequest(match_info={"report_id": "nonexistent"})
    with _patch_auth(), _mock_supabase(reports=None):
        response = await download_report(request)
    assert response.status == 404
