"""Microsoft Graph API client.

Used by:
  - webhook/onedrive_pipeline.py (delta query, get_item)
  - webhook/dispatch_document.py (get_item for stale downloadUrl refresh)
  - execution/scripts/onedrive_resubscribe.py (subscription CRUD)

Auth: OAuth2 client-credentials (application permissions).
The Azure app registration must have Files.Read.All application permission
with admin consent.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from ..core.logger import WorkflowLogger
from ..core.retry import retry_with_backoff


GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_SUBSCRIPTION_DAYS = 3      # Graph max for drive/item subscriptions


class GraphClient:
    """Thin wrapper around the Microsoft Graph endpoints we care about."""

    def __init__(self):
        self.tenant_id = os.environ.get("GRAPH_TENANT_ID")
        self.client_id = os.environ.get("GRAPH_CLIENT_ID")
        self.client_secret = os.environ.get("GRAPH_CLIENT_SECRET")
        if not all([self.tenant_id, self.client_id, self.client_secret]):
            raise ValueError(
                "GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET must be set"
            )
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self.logger = WorkflowLogger("GraphClient")

    # ── Auth ──

    def get_access_token(self) -> str:
        """Return a cached token, refreshing it up to 60s before expiry."""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        }
        resp = requests.post(url, data=data, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.get_access_token()}"}

    # ── Subscriptions ──

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def create_subscription(
        self,
        resource: str,
        notification_url: str,
        client_state: str,
        expires_in_days: int = DEFAULT_SUBSCRIPTION_DAYS,
        change_type: str = "updated",
    ) -> dict:
        expires = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
        body = {
            "changeType": change_type,
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": expires,
            "clientState": client_state,
        }
        resp = requests.post(
            f"{GRAPH_BASE}/subscriptions",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=body,
            timeout=20,
        )
        if resp.status_code >= 400:
            self.logger.error(
                f"create_subscription failed: {resp.status_code} {resp.text[:500]}"
            )
        resp.raise_for_status()
        return resp.json()

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def list_subscriptions(self) -> list[dict]:
        resp = requests.get(
            f"{GRAPH_BASE}/subscriptions",
            headers=self._headers(),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json().get("value", [])

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def renew_subscription(
        self, subscription_id: str, expires_in_days: int = DEFAULT_SUBSCRIPTION_DAYS
    ) -> dict:
        expires = (datetime.now(timezone.utc) + timedelta(days=expires_in_days)).isoformat()
        resp = requests.patch(
            f"{GRAPH_BASE}/subscriptions/{subscription_id}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={"expirationDateTime": expires},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def delete_subscription(self, subscription_id: str) -> None:
        resp = requests.delete(
            f"{GRAPH_BASE}/subscriptions/{subscription_id}",
            headers=self._headers(),
            timeout=20,
        )
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()

    # ── Drive items ──

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def get_item(self, drive_id: str, item_id: str) -> dict:
        """Return the drive item JSON including a fresh @microsoft.graph.downloadUrl."""
        resp = requests.get(
            f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}",
            headers=self._headers(),
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    @retry_with_backoff(max_attempts=3, base_delay=2.0)
    def get_folder_delta(
        self,
        drive_id: str,
        folder_path: str,
        delta_token: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """Return (items_changed_since_last_delta, new_delta_token).

        First call (no delta_token) returns the current state of the folder.
        Subsequent calls return only changed items.
        """
        if delta_token:
            url = delta_token if delta_token.startswith("http") else (
                f"{GRAPH_BASE}/drives/{drive_id}/root:{folder_path}:/delta?token={delta_token}"
            )
        else:
            url = f"{GRAPH_BASE}/drives/{drive_id}/root:{folder_path}:/delta"

        resp = requests.get(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        items = payload.get("value", [])

        next_link = payload.get("@odata.nextLink")
        delta_link = payload.get("@odata.deltaLink")

        while next_link:
            resp = requests.get(next_link, headers=self._headers(), timeout=30)
            resp.raise_for_status()
            page = resp.json()
            items.extend(page.get("value", []))
            next_link = page.get("@odata.nextLink")
            delta_link = page.get("@odata.deltaLink", delta_link)

        # Extract the token from deltaLink for storage.
        next_token: Optional[str] = None
        if delta_link and "token=" in delta_link:
            next_token = delta_link.split("token=", 1)[1].split("&", 1)[0]

        return items, next_token
