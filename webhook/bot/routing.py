"""Workflow → delivery destination routing.

Client-facing workflows are broadcast as a single post to the private
Telegram channel; internal/operational workflows keep the current
DM-to-subscribers path. CLIENT_DELIVERY_CHANNEL flips client content
back to the legacy uazapi path for rollback.
"""

from __future__ import annotations

import os

CLIENT_WORKFLOWS = frozenset({"daily_report", "market_news", "platts_reports"})

DEST_CLIENT_CHANNEL = "client_channel"
DEST_INTERNAL = "internal"


def resolve_destination(workflow_type: str | None) -> str:
    """Return DEST_CLIENT_CHANNEL for client workflows, DEST_INTERNAL otherwise."""
    if workflow_type in CLIENT_WORKFLOWS:
        return DEST_CLIENT_CHANNEL
    return DEST_INTERNAL


def client_delivery_mode() -> str:
    """'telegram' (default) or 'uazapi' (legacy rollback).

    Read at call time (not import) so tests and redeploys pick up env changes.
    """
    mode = os.getenv("CLIENT_DELIVERY_CHANNEL", "telegram").strip().lower()
    return "uazapi" if mode == "uazapi" else "telegram"
