"""User store: Redis-backed CRUD for Telegram bot users.

Redis key pattern: user:{chat_id} -> JSON
No TTL — user records are persistent.

Roles: admin (from TELEGRAM_CHAT_ID env), subscriber (approved users)
Status: pending, approved, rejected
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.config import TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

ADMIN_CHAT_ID = int(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID.isdigit() else 0

_USER_KEY_PREFIX = "user:"

DEFAULT_SUBSCRIPTIONS = {
    "morning_check": True,
    "baltic_ingestion": True,
    "daily_report": True,
    "market_news": True,
    "platts_reports": True,
}


def _get_client():
    """Return Redis client (same as curation keyspace)."""
    from execution.curation.redis_client import _get_client as _rc
    return _rc()


def _user_key(chat_id: int) -> str:
    return f"{_USER_KEY_PREFIX}{chat_id}"


def get_user(chat_id: int) -> Optional[dict]:
    """Return user dict or None."""
    try:
        raw = _get_client().get(_user_key(chat_id))
        if raw:
            return json.loads(raw)
    except Exception as exc:
        logger.warning(f"get_user({chat_id}) failed: {exc}")
    return None


def _save_user(user: dict) -> None:
    """Persist user dict to Redis (no TTL)."""
    try:
        _get_client().set(_user_key(user["chat_id"]), json.dumps(user))
    except Exception as exc:
        logger.error(f"_save_user({user.get('chat_id')}) failed: {exc}")


def create_pending_user(chat_id: int, name: str, username: str) -> dict:
    """Create a new user with status=pending and all subscriptions ON."""
    user = {
        "chat_id": chat_id,
        "name": name,
        "username": username or "",
        "role": "subscriber",
        "status": "pending",
        "subscriptions": dict(DEFAULT_SUBSCRIPTIONS),
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "approved_at": None,
    }
    _save_user(user)
    return user


def approve_user(chat_id: int) -> Optional[dict]:
    """Set user status to approved. Returns updated user or None."""
    user = get_user(chat_id)
    if user is None:
        return None
    updated = {
        **user,
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_user(updated)
    return updated


def reject_user(chat_id: int) -> Optional[dict]:
    """Set user status to rejected. Returns updated user or None."""
    user = get_user(chat_id)
    if user is None:
        return None
    updated = {**user, "status": "rejected"}
    _save_user(updated)
    return updated


def toggle_subscription(chat_id: int, workflow: str) -> bool:
    """Toggle a subscription on/off. Returns new value."""
    user = get_user(chat_id)
    if user is None:
        return False
    subs = user.get("subscriptions", {})
    current = subs.get(workflow, True)
    subs[workflow] = not current
    updated = {**user, "subscriptions": subs}
    _save_user(updated)
    return not current


def get_subscribers_for_workflow(workflow: str) -> list:
    """Return all approved users subscribed to a given workflow."""
    client = _get_client()
    users = []
    for key in client.scan_iter(match=f"{_USER_KEY_PREFIX}*", count=200):
        raw = client.get(key)
        if not raw:
            continue
        try:
            user = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if user.get("status") != "approved":
            continue
        if user.get("subscriptions", {}).get(workflow, False):
            users.append(user)
    return users


def list_pending_users() -> list:
    """Return all users with status=pending."""
    client = _get_client()
    pending = []
    for key in client.scan_iter(match=f"{_USER_KEY_PREFIX}*", count=200):
        raw = client.get(key)
        if not raw:
            continue
        try:
            user = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if user.get("status") == "pending":
            pending.append(user)
    return pending


def is_admin(chat_id: int) -> bool:
    """Check if chat_id is the admin."""
    return chat_id == ADMIN_CHAT_ID and ADMIN_CHAT_ID != 0


def get_user_role(chat_id: int) -> str:
    """Return role string: 'admin', 'subscriber', 'pending', or 'unknown'."""
    if is_admin(chat_id):
        return "admin"
    user = get_user(chat_id)
    if user is None:
        return "unknown"
    status = user.get("status", "")
    if status == "approved":
        return "subscriber"
    if status == "pending":
        return "pending"
    return "unknown"
