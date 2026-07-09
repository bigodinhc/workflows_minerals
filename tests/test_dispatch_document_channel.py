"""dispatch_document in telegram mode posts the PDF once to the channel."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
import fakeredis.aioredis


@pytest.fixture(autouse=True)
def _telegram_mode(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "telegram")


@pytest.fixture
def redis_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def fresh_approval_state():
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
    fake_resp = MagicMock()
    fake_resp.content = b"%PDF-1.4 fake-pdf-bytes"
    fake_resp.raise_for_status = MagicMock()
    with patch("dispatch_document.requests.get", return_value=fake_resp) as p:
        yield p


@pytest.mark.asyncio
async def test_telegram_mode_posts_pdf_to_channel(redis_client, fresh_approval_state):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 9, "error": None})
    uazapi_cls = MagicMock()
    with patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch_document.UazapiClient", uazapi_cls), \
         patch("dispatch_document._redis", return_value=redis_client):
        result = await dispatch_document("abc12", "minerals_report")
    channel_mock.assert_awaited_once()
    _, kwargs = channel_mock.await_args
    assert kwargs["pdf"] == b"%PDF-1.4 fake-pdf-bytes"
    assert kwargs["pdf_filename"] == "Minerals_Report.pdf"
    uazapi_cls.assert_not_called()
    assert result == {"sent": 1, "failed": 0, "skipped": 0, "errors": []}


@pytest.mark.asyncio
async def test_telegram_mode_idempotent_second_run_skips(redis_client, fresh_approval_state):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 9, "error": None})
    with patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch_document._redis", return_value=redis_client):
        await dispatch_document("abc12", "minerals_report")
        result = await dispatch_document("abc12", "minerals_report")
    assert channel_mock.await_count == 1
    assert result["skipped"] == 1
    assert result["sent"] == 0


@pytest.mark.asyncio
async def test_telegram_mode_channel_failure_counts_failed(redis_client, fresh_approval_state):
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    channel_mock = AsyncMock(return_value={"ok": False, "message_id": None, "error": "not admin"})
    with patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch_document._redis", return_value=redis_client):
        result = await dispatch_document("abc12", "minerals_report")
    assert result["sent"] == 0
    assert result["failed"] == 1
    assert result["errors"][0]["error"] == "not admin"


@pytest.mark.asyncio
async def test_telegram_mode_download_failure_does_not_burn_idempotency(
    redis_client, fresh_approval_state
):
    """A download error must not claim the idempotency key: retry should
    still be able to post once the download succeeds."""
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 9, "error": None})

    with patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch_document._redis", return_value=redis_client), \
         patch("dispatch_document.requests.get", side_effect=RuntimeError("boom")):
        first = await dispatch_document("abc12", "minerals_report")

    assert first == {
        "sent": 0, "failed": 1, "skipped": 0,
        "errors": [{"phone": "telegram_channel", "error": "download: boom"}],
    }
    channel_mock.assert_not_awaited()

    fake_resp = MagicMock()
    fake_resp.content = b"%PDF-1.4 fake-pdf-bytes"
    fake_resp.raise_for_status = MagicMock()
    with patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("dispatch_document._redis", return_value=redis_client), \
         patch("dispatch_document.requests.get", return_value=fake_resp):
        second = await dispatch_document("abc12", "minerals_report")

    assert second == {"sent": 1, "failed": 0, "skipped": 0, "errors": []}
    channel_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_telegram_mode_channel_post_failure_releases_claim(
    redis_client, fresh_approval_state
):
    """A failed channel post (ok=False) must release the idempotency claim
    so a retry can actually re-attempt the post instead of skipping."""
    from dispatch_document import dispatch_document
    await redis_client.set("approval:abc12", json.dumps(fresh_approval_state))
    failing_mock = AsyncMock(return_value={"ok": False, "message_id": None, "error": "not admin"})
    with patch("bot.channel_delivery.post_report_to_channel", failing_mock), \
         patch("dispatch_document._redis", return_value=redis_client):
        first = await dispatch_document("abc12", "minerals_report")
    assert first["failed"] == 1
    assert first["sent"] == 0

    ok_mock = AsyncMock(return_value={"ok": True, "message_id": 9, "error": None})
    with patch("bot.channel_delivery.post_report_to_channel", ok_mock), \
         patch("dispatch_document._redis", return_value=redis_client):
        second = await dispatch_document("abc12", "minerals_report")

    assert second == {"sent": 1, "failed": 0, "skipped": 0, "errors": []}
    ok_mock.assert_awaited_once()
