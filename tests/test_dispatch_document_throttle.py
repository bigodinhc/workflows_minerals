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
    """All sends happen sequentially, never in parallel.

    Uses an in_flight counter inside the mock send_document to detect
    any concurrent execution. Catches semaphore regressions and
    accidental gather-without-semaphore refactors.
    """
    monkeypatch.setenv("PDF_DELIVERY_MODE", "attachment")
    monkeypatch.setattr(
        "webhook.dispatch_document.asyncio.sleep", AsyncMock(return_value=None)
    )

    in_flight = {"max": 0, "now": 0}

    def _tracked_send(**kwargs):
        import time
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        time.sleep(0.01)  # let the threadpool potentially run others concurrently
        in_flight["now"] -= 1
        return {"messageId": "m"}

    fake_uazapi = MagicMock()
    fake_uazapi.send_document = MagicMock(side_effect=_tracked_send)

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
        # Constant check is informational; the in_flight assertion below
        # is the actual safety net.
        assert CONCURRENCY == 1
        result = await dispatch_document("abc", "__all__")

    assert result["sent"] == 4
    # Real verification: no two sends ever in flight at the same time.
    assert in_flight["max"] == 1, (
        f"Detected concurrent sends (max in-flight = {in_flight['max']}); "
        f"semaphore is not enforcing serial dispatch"
    )


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
