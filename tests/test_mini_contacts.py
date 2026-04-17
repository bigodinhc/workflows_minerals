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
