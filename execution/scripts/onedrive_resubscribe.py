#!/usr/bin/env python3
"""Renew (or create) the OneDrive change-notification subscription.

Scheduled every 12h via .github/workflows/onedrive_resubscribe.yml.
Graph subscriptions for drive resources have a maximum lifetime of ~3 days,
so we renew any subscription expiring within 24h.

If no subscription currently points at our notificationUrl, we create one.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from execution.core.event_bus import with_event_bus, get_current_bus
from execution.core.logger import WorkflowLogger
from execution.integrations.graph_client import GraphClient


WORKFLOW_NAME = "onedrive_resubscribe"
RENEW_WHEN_WITHIN_HOURS = 24


def _run() -> None:
    logger = WorkflowLogger(WORKFLOW_NAME)
    bus = get_current_bus()
    if bus:
        bus.emit("step", label="Listando subscriptions")

    graph = GraphClient()
    our_url = os.environ["ONEDRIVE_WEBHOOK_URL"]
    drive_id = os.environ["GRAPH_DRIVE_ID"]
    folder_path = os.environ["GRAPH_FOLDER_PATH"]
    client_state = os.environ["GRAPH_WEBHOOK_CLIENT_STATE"]

    subs = graph.list_subscriptions()
    our_subs = [s for s in subs if s.get("notificationUrl") == our_url]
    logger.info(f"found {len(our_subs)} subs matching our URL ({len(subs)} total)")

    if not our_subs:
        # Graph subscriptions only support drive-root as the resource path
        # (folder-scoped subscriptions are not supported). The pipeline
        # filters by folder_path via the delta query, so drive-wide
        # notifications are fine — they just trigger a folder-scoped delta.
        resource = f"/drives/{drive_id}/root"
        created = graph.create_subscription(
            resource=resource,
            notification_url=our_url,
            client_state=client_state,
        )
        logger.info(f"created subscription id={created.get('id')}")
        if bus:
            bus.emit("subscription_created", detail={"id": created.get("id")})
        return

    threshold = datetime.now(timezone.utc) + timedelta(hours=RENEW_WHEN_WITHIN_HOURS)
    renewed_any = False
    for sub in our_subs:
        raw_exp = sub.get("expirationDateTime", "")
        try:
            exp = datetime.fromisoformat(raw_exp.replace("Z", "+00:00"))
        except Exception:
            logger.warning(f"unparseable expirationDateTime: {raw_exp!r}")
            continue

        if exp < threshold:
            graph.renew_subscription(sub["id"])
            renewed_any = True
            logger.info(f"renewed subscription {sub['id']}")
            if bus:
                bus.emit("subscription_renewed", detail={"id": sub["id"]})
        else:
            logger.info(f"subscription {sub['id']} expires at {raw_exp} — skipping")

    if not renewed_any and bus:
        bus.emit("no_renewal_needed")


@with_event_bus(WORKFLOW_NAME)
def main():
    _run()


if __name__ == "__main__":
    main()
