"""Unit tests for GraphClient (OAuth + subscriptions + delta + get_item)."""
from __future__ import annotations

import os
import time
import pytest
from unittest.mock import patch, MagicMock

from execution.integrations.graph_client import GraphClient


@pytest.fixture(autouse=True)
def _env():
    os.environ["GRAPH_TENANT_ID"] = "tenant-xyz"
    os.environ["GRAPH_CLIENT_ID"] = "client-abc"
    os.environ["GRAPH_CLIENT_SECRET"] = "secret-123"
    os.environ["GRAPH_DRIVE_ID"] = "drive-test"
    os.environ["GRAPH_FOLDER_PATH"] = "/SIGCM/test"
    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "cstate-xyz"
    os.environ["ONEDRIVE_WEBHOOK_URL"] = "https://example.com/onedrive/notify"
    yield


def _mock_ok(json_body, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_body
    m.text = str(json_body)
    return m


def test_get_access_token_requests_client_credentials_grant():
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok-1", "expires_in": 3600})) as p:
        client = GraphClient()
        token = client.get_access_token()
    assert token == "tok-1"
    url = p.call_args.args[0]
    assert "tenant-xyz" in url
    assert "oauth2/v2.0/token" in url
    body = p.call_args.kwargs["data"]
    assert body["client_id"] == "client-abc"
    assert body["client_secret"] == "secret-123"
    assert body["grant_type"] == "client_credentials"


def test_access_token_is_cached_until_expiry():
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok-1", "expires_in": 3600})) as p:
        client = GraphClient()
        client.get_access_token()
        client.get_access_token()
        client.get_access_token()
    # Only one token request for three get_access_token calls.
    assert p.call_count == 1


def test_access_token_refetches_after_expiry():
    client = GraphClient()
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok-1", "expires_in": 1})) as p:
        client.get_access_token()
    # Simulate clock skipping past expiry.
    client._token_expires_at = time.time() - 1
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok-2", "expires_in": 3600})):
        t = client.get_access_token()
    assert t == "tok-2"


def test_create_subscription_posts_expected_payload():
    with patch("execution.integrations.graph_client.requests.post") as p:
        p.side_effect = [
            _mock_ok({"access_token": "tok", "expires_in": 3600}),   # token
            _mock_ok({"id": "sub-1", "expirationDateTime": "2026-04-25T00:00:00Z"}, status=201),
        ]
        client = GraphClient()
        sub = client.create_subscription(
            resource="/drives/drive-test/root:/SIGCM/test",
            notification_url="https://example.com/onedrive/notify",
            client_state="cstate-xyz",
        )
    assert sub["id"] == "sub-1"
    body = p.call_args_list[-1].kwargs["json"]
    assert body["changeType"] == "updated"
    assert body["notificationUrl"] == "https://example.com/onedrive/notify"
    assert body["clientState"] == "cstate-xyz"
    assert body["resource"] == "/drives/drive-test/root:/SIGCM/test"
    assert "expirationDateTime" in body


def test_list_subscriptions():
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok", "expires_in": 3600})), \
         patch("execution.integrations.graph_client.requests.get",
               return_value=_mock_ok({"value": [{"id": "s1"}, {"id": "s2"}]})):
        client = GraphClient()
        subs = client.list_subscriptions()
    assert [s["id"] for s in subs] == ["s1", "s2"]


def test_renew_subscription_patches_expiration():
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok", "expires_in": 3600})), \
         patch("execution.integrations.graph_client.requests.patch",
               return_value=_mock_ok({"id": "sub-1", "expirationDateTime": "2026-04-30T00:00:00Z"})) as p:
        client = GraphClient()
        client.renew_subscription("sub-1")
    body = p.call_args.kwargs["json"]
    assert "expirationDateTime" in body


def test_get_folder_delta_parses_items_and_delta_link():
    delta_resp = {
        "value": [
            {"id": "item-a", "name": "a.pdf", "file": {"mimeType": "application/pdf"}},
            {"id": "item-b", "name": "subfolder", "folder": {}},
        ],
        "@odata.deltaLink": "https://graph.microsoft.com/.../delta?token=next-abc",
    }
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok", "expires_in": 3600})), \
         patch("execution.integrations.graph_client.requests.get",
               return_value=_mock_ok(delta_resp)):
        client = GraphClient()
        items, next_token = client.get_folder_delta(
            drive_id="drive-test",
            folder_path="/SIGCM/test",
        )
    assert [i["id"] for i in items] == ["item-a", "item-b"]
    assert next_token == "next-abc"


def test_get_item_returns_item_with_download_url():
    item = {
        "id": "item-a",
        "name": "Minerals_Report.pdf",
        "@microsoft.graph.downloadUrl": "https://cdn.example.com/get?sig=xyz",
    }
    with patch("execution.integrations.graph_client.requests.post",
               return_value=_mock_ok({"access_token": "tok", "expires_in": 3600})), \
         patch("execution.integrations.graph_client.requests.get",
               return_value=_mock_ok(item)):
        client = GraphClient()
        result = client.get_item("drive-test", "item-a")
    assert result["@microsoft.graph.downloadUrl"].startswith("https://")
