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
