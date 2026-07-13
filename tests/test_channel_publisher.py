"""Tests for the GH Actions → webhook channel publisher."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

import pytest


def test_delivery_mode_defaults_to_telegram(monkeypatch):
    monkeypatch.delenv("CLIENT_DELIVERY_CHANNEL", raising=False)
    from execution.integrations.channel_publisher import delivery_mode
    assert delivery_mode() == "telegram"


def test_delivery_mode_uazapi(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "uazapi")
    from execution.integrations.channel_publisher import delivery_mode
    assert delivery_mode() == "uazapi"


def test_delivery_mode_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("CLIENT_DELIVERY_CHANNEL", "pombo-correio")
    from execution.integrations.channel_publisher import delivery_mode
    assert delivery_mode() == "telegram"


def _ok_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "success": True,
        "draft_id": "d1",
        "telegram_delivery": {"ok": True, "message_id": 9, "error": None},
    }
    return resp


def test_publish_posts_store_draft(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://example.up.railway.app/")
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", "s3gr3d0")
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", return_value=_ok_response()) as post:
        result = cp.publish_to_channel("daily_report", "corpo *msg*", "draft-42")
    assert result == {"ok": True, "message_id": 9, "error": None}
    args, kwargs = post.call_args
    assert args[0] == "https://example.up.railway.app/store-draft"  # trailing / stripped
    assert kwargs["json"] == {
        "draft_id": "draft-42",
        "message": "corpo *msg*",
        "workflow_type": "daily_report",
        "direct_delivery": True,
    }
    assert kwargs["headers"] == {"X-Webhook-Secret": "s3gr3d0"}
    assert kwargs["timeout"] == 90


def test_publish_without_base_url(monkeypatch):
    monkeypatch.delenv("WEBHOOK_BASE_URL", raising=False)
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post") as post:
        result = cp.publish_to_channel("daily_report", "m", "d")
    post.assert_not_called()
    assert result["ok"] is False
    assert "WEBHOOK_BASE_URL" in result["error"]


def test_publish_http_error(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://x.test")
    resp = MagicMock()
    resp.status_code = 502
    resp.text = "bad gateway"
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", return_value=resp):
        result = cp.publish_to_channel("daily_report", "m", "d")
    assert result["ok"] is False
    assert "502" in result["error"]


def test_publish_network_exception(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://x.test")
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", side_effect=OSError("timeout")):
        result = cp.publish_to_channel("daily_report", "m", "d")
    assert result["ok"] is False
    assert "timeout" in result["error"]


def test_publish_response_without_delivery(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://x.test")
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "draft_id": "d"}
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", return_value=resp):
        result = cp.publish_to_channel("daily_report", "m", "d")
    assert result["ok"] is False
    assert "telegram_delivery" in result["error"]


def test_publish_sem_secret_manda_header_vazio(monkeypatch):
    monkeypatch.setenv("WEBHOOK_BASE_URL", "https://example.up.railway.app")
    monkeypatch.delenv("WEBHOOK_SHARED_SECRET", raising=False)
    from execution.integrations import channel_publisher as cp
    with patch.object(cp.requests, "post", return_value=_ok_response()) as post:
        cp.publish_to_channel("daily_report", "corpo", "draft-43")
    assert post.call_args.kwargs["headers"] == {"X-Webhook-Secret": ""}
