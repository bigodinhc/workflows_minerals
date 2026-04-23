"""Unit tests for execution/scripts/onedrive_resubscribe.py."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _env():
    os.environ["GRAPH_DRIVE_ID"] = "drive-test"
    os.environ["GRAPH_FOLDER_PATH"] = "/SIGCM/test"
    os.environ["GRAPH_WEBHOOK_CLIENT_STATE"] = "cstate-xyz"
    os.environ["ONEDRIVE_WEBHOOK_URL"] = "https://example.com/onedrive/notify"
    yield


def test_renews_near_expiring_subscription():
    from execution.scripts import onedrive_resubscribe

    sub_near_expiry = {
        "id": "sub-1",
        "notificationUrl": "https://example.com/onedrive/notify",
        "expirationDateTime": (
            datetime.now(timezone.utc) + timedelta(hours=10)
        ).isoformat(),
    }
    graph = MagicMock()
    graph.list_subscriptions.return_value = [sub_near_expiry]

    with patch("execution.scripts.onedrive_resubscribe.GraphClient",
               return_value=graph):
        onedrive_resubscribe._run()

    graph.renew_subscription.assert_called_once_with("sub-1")
    graph.create_subscription.assert_not_called()


def test_leaves_far_expiring_subscription_alone():
    from execution.scripts import onedrive_resubscribe

    sub_far_expiry = {
        "id": "sub-1",
        "notificationUrl": "https://example.com/onedrive/notify",
        "expirationDateTime": (
            datetime.now(timezone.utc) + timedelta(days=2, hours=12)
        ).isoformat(),
    }
    graph = MagicMock()
    graph.list_subscriptions.return_value = [sub_far_expiry]

    with patch("execution.scripts.onedrive_resubscribe.GraphClient",
               return_value=graph):
        onedrive_resubscribe._run()

    graph.renew_subscription.assert_not_called()
    graph.create_subscription.assert_not_called()


def test_creates_subscription_when_none_match_our_url():
    from execution.scripts import onedrive_resubscribe

    other_sub = {
        "id": "sub-other",
        "notificationUrl": "https://other.example.com/hook",
        "expirationDateTime": (
            datetime.now(timezone.utc) + timedelta(days=2)
        ).isoformat(),
    }
    graph = MagicMock()
    graph.list_subscriptions.return_value = [other_sub]

    with patch("execution.scripts.onedrive_resubscribe.GraphClient",
               return_value=graph):
        onedrive_resubscribe._run()

    graph.create_subscription.assert_called_once()
    kwargs = graph.create_subscription.call_args.kwargs
    assert kwargs["notification_url"] == "https://example.com/onedrive/notify"
    assert kwargs["client_state"] == "cstate-xyz"
    # Graph subscriptions only support drive-root as resource; folder
    # scoping happens via delta query in the pipeline, not the subscription.
    assert kwargs["resource"] == "/drives/drive-test/root"


def test_creates_subscription_when_zero_subs_exist():
    from execution.scripts import onedrive_resubscribe
    graph = MagicMock()
    graph.list_subscriptions.return_value = []
    with patch("execution.scripts.onedrive_resubscribe.GraphClient",
               return_value=graph):
        onedrive_resubscribe._run()
    graph.create_subscription.assert_called_once()
