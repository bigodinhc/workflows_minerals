"""Store-draft routes client workflows to the channel, internal to DMs."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest


class _FakeRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


def _payload(workflow_type: str) -> dict:
    return {
        "draft_id": "d1",
        "message": "conteúdo",
        "workflow_type": workflow_type,
        "direct_delivery": True,
    }


@pytest.mark.asyncio
async def test_client_workflow_posts_to_channel():
    from routes.api import store_draft
    channel_mock = AsyncMock(return_value={"ok": True, "message_id": 7, "error": None})
    dm_mock = AsyncMock()
    with patch("routes.api.drafts_set"), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("bot.delivery.deliver_to_subscribers", dm_mock):
        resp = await store_draft(_FakeRequest(_payload("daily_report")))
    body = json.loads(resp.body)
    channel_mock.assert_awaited_once_with("conteúdo")
    dm_mock.assert_not_awaited()
    assert body["telegram_delivery"] == {"ok": True, "message_id": 7, "error": None}


@pytest.mark.asyncio
async def test_internal_workflow_keeps_dm_broadcast():
    from routes.api import store_draft
    channel_mock = AsyncMock()
    dm_mock = AsyncMock(return_value={"sent": 2, "failed": 0, "errors": []})
    with patch("routes.api.drafts_set"), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("bot.delivery.deliver_to_subscribers", dm_mock):
        resp = await store_draft(_FakeRequest(_payload("watchdog")))
    body = json.loads(resp.body)
    dm_mock.assert_awaited_once_with("watchdog", "conteúdo")
    channel_mock.assert_not_awaited()
    assert body["telegram_delivery"]["sent"] == 2


@pytest.mark.asyncio
async def test_no_direct_delivery_skips_both():
    from routes.api import store_draft
    channel_mock = AsyncMock()
    dm_mock = AsyncMock()
    payload = {**_payload("daily_report"), "direct_delivery": False}
    with patch("routes.api.drafts_set"), \
         patch("bot.channel_delivery.post_report_to_channel", channel_mock), \
         patch("bot.delivery.deliver_to_subscribers", dm_mock):
        resp = await store_draft(_FakeRequest(payload))
    body = json.loads(resp.body)
    channel_mock.assert_not_awaited()
    dm_mock.assert_not_awaited()
    assert "telegram_delivery" not in body
