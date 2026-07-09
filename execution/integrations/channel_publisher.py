"""Publish client-workflow messages to the Telegram channel via the webhook.

GH Actions scripts can't import webhook/bot/* (aiogram is not in the root
requirements), so they publish through the deployed webhook's /store-draft
endpoint, which owns routing, HTML conversion and flood-wait retry.
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

# 90s: the webhook's flood-wait retry can hold the response past 30s; a
# premature timeout false-fails a post that actually lands (double-post
# risk on rerun).
_TIMEOUT_SECONDS = 90


def delivery_mode() -> str:
    """'telegram' (default) or 'uazapi' (legacy rollback).

    Mirror of webhook/bot/routing.client_delivery_mode — keep in sync.
    Read at call time so GH Actions env changes apply without code changes.
    """
    mode = os.getenv("CLIENT_DELIVERY_CHANNEL", "telegram").strip().lower()
    return "uazapi" if mode == "uazapi" else "telegram"


def publish_to_channel(workflow_type: str, message: str, draft_id: str) -> dict:
    """POST the message to {WEBHOOK_BASE_URL}/store-draft with direct_delivery.

    Returns the webhook's telegram_delivery dict ({"ok", "message_id",
    "error"}), or {"ok": False, ...} on any transport problem. Never raises —
    callers decide whether a failed publish fails the job.
    """
    base_url = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        logger.error("WEBHOOK_BASE_URL not set — channel publish skipped")
        return {"ok": False, "message_id": None, "error": "WEBHOOK_BASE_URL not set"}

    try:
        resp = requests.post(
            f"{base_url}/store-draft",
            json={
                "draft_id": draft_id,
                "message": message,
                "workflow_type": workflow_type,
                "direct_delivery": True,
            },
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.error(f"channel publish request failed: {exc}")
        return {"ok": False, "message_id": None, "error": str(exc)[:300]}

    if resp.status_code != 200:
        logger.error(f"channel publish HTTP {resp.status_code}: {resp.text[:200]}")
        return {"ok": False, "message_id": None, "error": f"HTTP {resp.status_code}"}

    try:
        delivery = resp.json().get("telegram_delivery")
    except Exception as exc:
        return {"ok": False, "message_id": None, "error": f"bad response: {str(exc)[:200]}"}
    if not isinstance(delivery, dict):
        return {"ok": False, "message_id": None, "error": "no telegram_delivery in response"}
    return delivery
