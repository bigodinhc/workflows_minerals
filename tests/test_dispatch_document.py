"""Unit tests for webhook/dispatch_document.py — fan-out with idempotency."""
from __future__ import annotations

import json
import pytest
import fakeredis.aioredis
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def fresh_approval_state():
    """Return a fresh-URL approval state dict (no async needed)."""
    return {
        "drive_id": "drive-test",
        "drive_item_id": "item-abc",
        "filename": "Minerals_Report.pdf",
        "size": 1024,
        "downloadUrl": "https://cdn.example.com/fresh?sig=x",
        "downloadUrl_fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": "dispatching",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture(autouse=True)
def mock_pdf_download():
    """Stub requests.get so tests never actually hit the network."""
    fake_resp = MagicMock()
    fake_resp.content = b"%PDF-1.4 fake-pdf-bytes"
    fake_resp.raise_for_status = MagicMock()
    with patch("dispatch_document.requests.get", return_value=fake_resp) as p:
        yield p


@pytest.fixture(autouse=True)
def mock_asyncio_sleep():
    """Stub asyncio.sleep so per-iteration jitter doesn't slow tests."""
    with patch("dispatch_document.asyncio.sleep", new=AsyncMock(return_value=None)):
        yield


@pytest.fixture
def mock_uazapi():
    client = MagicMock()
    client.send_document.return_value = {"messageId": "m1"}
    return client


@pytest.fixture
def mock_contacts_repo():
    repo = MagicMock()
    repo.list_by_list_code.return_value = [
        MagicMock(name="Alice", phone_uazapi="5511111111111"),
        MagicMock(name="Bob",   phone_uazapi="5511222222222"),
    ]
    repo.list_active.return_value = [
        MagicMock(name="Alice", phone_uazapi="5511111111111"),
        MagicMock(name="Bob",   phone_uazapi="5511222222222"),
        MagicMock(name="Carol", phone_uazapi="5511333333333"),
    ]
    return repo


@pytest.mark.asyncio
async def test_dispatch_sends_to_list_members(
    redis_client, fresh_approval_state, mock_uazapi, mock_contacts_repo
):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    with patch("dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("dispatch_document._redis", return_value=redis_client):
        result = await dispatch_document("abc12", "minerals_report")
    assert mock_uazapi.send_document.call_count == 2
    assert result["sent"] == 2
    assert result["failed"] == 0


@pytest.mark.asyncio
async def test_dispatch_all_uses_list_active(
    redis_client, fresh_approval_state, mock_uazapi, mock_contacts_repo
):
    from dispatch_document import dispatch_document, ALL_CODE
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    with patch("dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("dispatch_document._redis", return_value=redis_client):
        result = await dispatch_document("abc12", ALL_CODE)
    assert mock_uazapi.send_document.call_count == 3
    assert result["sent"] == 3


@pytest.mark.asyncio
async def test_dispatch_idempotency_blocks_duplicate_sends(
    redis_client, fresh_approval_state, mock_uazapi, mock_contacts_repo
):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    with patch("dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("dispatch_document._redis", return_value=redis_client):
        await dispatch_document("abc12", "minerals_report")
        # run again — all 2 should be blocked by idempotency keys
        mock_uazapi.send_document.reset_mock()
        result = await dispatch_document("abc12", "minerals_report")
    assert mock_uazapi.send_document.call_count == 0
    assert result["sent"] == 0
    assert result["skipped"] == 2


@pytest.mark.asyncio
async def test_dispatch_refetches_stale_download_url(
    redis_client, mock_uazapi, mock_contacts_repo
):
    from dispatch_document import dispatch_document
    # seed an approval with a 60-minute-old downloadUrl
    stale_state = {
        "drive_id": "drive-test",
        "drive_item_id": "item-abc",
        "filename": "x.pdf",
        "size": 100,
        "downloadUrl": "https://cdn.example.com/stale?sig=old",
        "downloadUrl_fetched_at": (
            datetime.now(timezone.utc) - timedelta(minutes=60)
        ).isoformat(),
        "status": "dispatching",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.set("approval:stale", json.dumps(stale_state))

    mock_graph = MagicMock()
    mock_graph.get_item.return_value = {
        "id": "item-abc",
        "name": "x.pdf",
        "@microsoft.graph.downloadUrl": "https://cdn.example.com/FRESH",
    }
    with patch("dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("dispatch_document.GraphClient", return_value=mock_graph), \
         patch("dispatch_document._redis", return_value=redis_client):
        await dispatch_document("stale", "minerals_report")
    mock_graph.get_item.assert_called_once_with("drive-test", "item-abc")
    # After refresh, the PDF is downloaded and sent as base64 — verify
    # the dispatch attempted the send (with b64-encoded fake bytes) rather
    # than passing the raw URL through.
    assert mock_uazapi.send_document.call_count >= 1
    call_kwargs = mock_uazapi.send_document.call_args_list[0].kwargs
    assert call_kwargs["file_url"] != "https://cdn.example.com/FRESH"
    assert call_kwargs["file_url"]  # non-empty base64 string


@pytest.mark.asyncio
async def test_dispatch_missing_approval_raises():
    from dispatch_document import dispatch_document, ApprovalExpiredError
    empty = fakeredis.aioredis.FakeRedis(decode_responses=True)
    with patch("dispatch_document._redis", return_value=empty):
        with pytest.raises(ApprovalExpiredError):
            await dispatch_document("missing-id", "minerals_report")


@pytest.mark.asyncio
async def test_dispatch_emits_started_and_completed_events(
    redis_client, fresh_approval_state, mock_uazapi, mock_contacts_repo
):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    bus_instance = MagicMock()
    bus_class = MagicMock(return_value=bus_instance)
    with patch("dispatch_document.UazapiClient", return_value=mock_uazapi), \
         patch("dispatch_document.ContactsRepo", return_value=mock_contacts_repo), \
         patch("dispatch_document._redis", return_value=redis_client), \
         patch("dispatch_document.EventBus", bus_class):
        await dispatch_document("abc12", "minerals_report")
    emitted_events = [call.args[0] for call in bus_instance.emit.call_args_list]
    assert "dispatch_started" in emitted_events
    assert "dispatch_completed" in emitted_events
