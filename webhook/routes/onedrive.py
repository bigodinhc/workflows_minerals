"""aiohttp route for Microsoft Graph change notifications on the OneDrive folder.

Mounted at POST /onedrive/notify.

Handles two request types:
  1. Graph validation handshake — `?validationToken=<token>`. Must echo the token
     plaintext within 10 seconds, else Graph refuses to create the subscription.
  2. Actual change notifications — JSON body with `value[]`. We validate the
     shared `clientState`, return 202 Accepted immediately, and spawn the
     detection pipeline asynchronously.
"""
from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from onedrive_pipeline import process_notification, validate_notification


logger = logging.getLogger(__name__)


async def onedrive_notify(request: web.Request) -> web.Response:
    validation_token = request.query.get("validationToken")
    if validation_token:
        return web.Response(text=validation_token, content_type="text/plain")

    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400, text="bad json")

    if not payload:
        return web.Response(status=400, text="empty payload")

    expected_state = os.environ.get("GRAPH_WEBHOOK_CLIENT_STATE", "")
    if not validate_notification(payload, expected_state):
        logger.warning("onedrive webhook rejected: bad clientState")
        return web.Response(status=401, text="unauthorized")

    # Spawn the pipeline without blocking the HTTP response.
    asyncio.create_task(_safe_process(payload))
    return web.Response(status=202, text="accepted")


async def _safe_process(payload: dict) -> None:
    try:
        await process_notification(payload)
    except Exception:
        logger.exception("process_notification crashed")


def setup_routes(app: web.Application) -> None:
    app.router.add_post("/onedrive/notify", onedrive_notify)
