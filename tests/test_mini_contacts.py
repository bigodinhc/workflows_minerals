"""Tests for /api/mini/contacts endpoints."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

from execution.integrations.contacts_repo import Contact, ContactNotFoundError


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


_NOW = datetime.now(timezone.utc)


def _contact(name, phone, status="ativo"):
    return Contact(
        id=f"id-{phone}", name=name, phone_raw=phone, phone_uazapi=phone,
        status=status, created_at=_NOW, updated_at=_NOW,
    )


FAKE_CONTACTS = [
    _contact("Joao Silva", "5511999001122", "ativo"),
    _contact("Maria Santos", "5511999003344", "inativo"),
    _contact("Pedro Costa", "5511999005566", "ativo"),
]


@pytest.mark.asyncio
async def test_get_contacts():
    from routes.mini_api import get_contacts
    request = FakeRequest(query={"page": "1"})
    mock_repo = MagicMock()
    mock_repo.list_all.return_value = (FAKE_CONTACTS, 1)
    with _patch_auth():
        with patch("routes.mini_api.ContactsRepo", return_value=mock_repo):
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
    mock_repo = MagicMock()
    mock_repo.list_all.return_value = (filtered, 1)
    request = FakeRequest(query={"search": "Joao", "page": "1"})
    with _patch_auth():
        with patch("routes.mini_api.ContactsRepo", return_value=mock_repo):
            response = await get_contacts(request)
    data = json.loads(response.body)
    assert len(data["contacts"]) == 1
    assert data["contacts"][0]["name"] == "Joao Silva"


@pytest.mark.asyncio
async def test_toggle_contact():
    from routes.mini_api import toggle_contact
    mock_repo = MagicMock()
    mock_repo.toggle.return_value = _contact("Joao Silva", "5511999001122", "inativo")
    request = FakeRequest(match_info={"phone": "5511999001122"})
    with _patch_auth():
        with patch("routes.mini_api.ContactsRepo", return_value=mock_repo):
            response = await toggle_contact(request)
    data = json.loads(response.body)
    assert data["name"] == "Joao Silva"
    assert data["active"] is False


@pytest.mark.asyncio
async def test_toggle_contact_not_found():
    from routes.mini_api import toggle_contact
    mock_repo = MagicMock()
    mock_repo.toggle.side_effect = ContactNotFoundError("Not found")
    request = FakeRequest(match_info={"phone": "0000000000"})
    with _patch_auth():
        with patch("routes.mini_api.ContactsRepo", return_value=mock_repo):
            response = await toggle_contact(request)
    assert response.status == 404
