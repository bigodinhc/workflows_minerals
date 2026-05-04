"""Document (PDF) fan-out dispatcher for the OneDrive approval flow.

Loads the Redis approval state, refreshes the Graph downloadUrl if stale,
fans out to the selected list (or all active contacts for ALL_CODE) with
concurrency=5, and applies per-recipient idempotency keys.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Optional

import random

import requests

from execution.core.event_bus import EventBus
from execution.core.logger import WorkflowLogger
from execution.integrations.contacts_repo import ContactsRepo
from execution.integrations.graph_client import GraphClient
from execution.integrations.uazapi_client import UazapiClient


ALL_CODE = "__all__"
CONCURRENCY = 1
DOWNLOAD_URL_STALE_AFTER_SECONDS = 50 * 60     # 50 min safety margin on Graph's ~1h TTL
IDEMPOTENCY_TTL_SECONDS = 24 * 3600


def _broadcast_delay_range() -> tuple[float, float]:
    """Mirror of execution.core.delivery_reporter._broadcast_delay_range.
    Duplicated here to avoid pulling delivery_reporter into the async PDF path.
    Reads the same env vars."""
    import math
    try:
        lo = float(os.environ.get("BROADCAST_DELAY_MIN", "15.0"))
        hi = float(os.environ.get("BROADCAST_DELAY_MAX", "30.0"))
    except (TypeError, ValueError):
        lo, hi = 15.0, 30.0
    if not math.isfinite(lo) or not math.isfinite(hi):
        lo, hi = 15.0, 30.0
    lo = max(0.0, lo)
    hi = max(lo, min(hi, 300.0))
    return lo, hi


class ApprovalExpiredError(Exception):
    """approval:{uuid} key is missing in Redis (TTL expired or never existed)."""


def _redis():
    """Returns an async Redis client. Factored out for test patchability."""
    from redis.asyncio import Redis
    return Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)


def _idempotency_key(phone: str, drive_item_id: str) -> str:
    raw = f"{phone}|{drive_item_id}".encode()
    return f"idempotency:{hashlib.sha1(raw).hexdigest()}"


async def _claim_idempotency(redis_client, phone: str, drive_item_id: str) -> bool:
    """True if this (phone, drive_item_id) hasn't been sent before (claim succeeds)."""
    key = _idempotency_key(phone, drive_item_id)
    return bool(await redis_client.set(key, "1", nx=True, ex=IDEMPOTENCY_TTL_SECONDS))


def _is_stale(iso_ts: str) -> bool:
    try:
        fetched = datetime.fromisoformat(iso_ts)
        age = datetime.now(timezone.utc) - fetched
        return age.total_seconds() > DOWNLOAD_URL_STALE_AFTER_SECONDS
    except Exception:
        return True


async def _refresh_download_url(redis_client, approval_id: str, state: dict) -> dict:
    """Fetch a fresh downloadUrl via Graph, update Redis state, return new state."""
    graph = GraphClient()
    item = graph.get_item(state["drive_id"], state["drive_item_id"])
    new_state = {
        **state,
        "downloadUrl": item["@microsoft.graph.downloadUrl"],
        "downloadUrl_fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.set(
        f"approval:{approval_id}",
        json.dumps(new_state),
        keepttl=True,
    )
    return new_state


async def dispatch_document(
    approval_id: str,
    list_code: str,
    trace_id: str | None = None,
) -> dict:
    """Fan-out PDF broadcast. Returns counter dict for caller to display."""
    logger = WorkflowLogger("DispatchDocument")
    redis_client = _redis()

    raw = await redis_client.get(f"approval:{approval_id}")
    if not raw:
        raise ApprovalExpiredError(approval_id)
    state = json.loads(raw)

    # Refresh downloadUrl when stale OR missing. Older approvals created
    # before the 'get_item in pipeline' fix may have an empty downloadUrl.
    if not state.get("downloadUrl") or _is_stale(state.get("downloadUrl_fetched_at", "")):
        state = await _refresh_download_url(redis_client, approval_id, state)

    contacts_repo = ContactsRepo()
    if list_code == ALL_CODE:
        recipients = contacts_repo.list_active()
    else:
        recipients = contacts_repo.list_by_list_code(list_code)

    bus_trace_id = trace_id or state.get("trace_id")
    bus = EventBus(workflow="onedrive_webhook", trace_id=bus_trace_id)
    bus.emit("dispatch_started", detail={
        "approval_id": approval_id,
        "list_code": list_code,
        "recipients": len(recipients),
    })

    # Download the PDF once on our side and send it as base64 to Uazapi.
    # Passing the raw Graph downloadUrl to Uazapi triggers 500 errors
    # (Uazapi's server can't reliably fetch SharePoint-signed URLs),
    # so we proxy the bytes through ourselves.
    def _download_pdf() -> bytes:
        r = requests.get(state["downloadUrl"], timeout=60, stream=False)
        r.raise_for_status()
        return r.content

    try:
        pdf_bytes = await asyncio.to_thread(_download_pdf)
        bus.emit("pdf_downloaded", detail={
            "approval_id": approval_id,
            "bytes": len(pdf_bytes),
        })
    except Exception as exc:
        logger.error(f"PDF download failed: {exc}")
        bus.emit("pdf_download_failed", level="error", detail={
            "approval_id": approval_id,
            "error": str(exc)[:300],
        })
        bus.emit("dispatch_completed", detail={
            "approval_id": approval_id,
            "sent": 0,
            "failed": len(recipients),
            "skipped": 0,
        })
        return {
            "sent": 0,
            "failed": len(recipients),
            "skipped": 0,
            "errors": [{"phone": "*", "error": f"download: {str(exc)[:250]}"}],
        }

    uazapi = UazapiClient()
    sem = asyncio.Semaphore(CONCURRENCY)
    results: dict = {"sent": 0, "failed": 0, "skipped": 0, "errors": []}

    async def _send_one(contact, idx, total):
        async with sem:
            claimed = await _claim_idempotency(
                redis_client, contact.phone_uazapi, state["drive_item_id"]
            )
            if not claimed:
                results["skipped"] += 1
                return
            try:
                # attachment mode (current default behavior)
                pdf_b64_payload = base64.b64encode(pdf_bytes).decode("ascii")
                await asyncio.to_thread(
                    uazapi.send_document,
                    number=contact.phone_uazapi,
                    file_url=pdf_b64_payload,
                    doc_name=state["filename"],
                )
                results["sent"] += 1
            except Exception as exc:
                logger.error(
                    f"send_document to {contact.phone_uazapi} failed: {exc}"
                )
                results["failed"] += 1
                err_str = str(exc)[:300]
                results["errors"].append({
                    "phone": contact.phone_uazapi,
                    "error": err_str,
                })
                bus.emit(
                    "send_failed",
                    level="error",
                    detail={
                        "phone": contact.phone_uazapi,
                        "error": err_str,
                        "exc_type": type(exc).__name__,
                    },
                )
            # Throttle: sleep between sends, skip after last contact.
            if idx < total - 1:
                lo, hi = _broadcast_delay_range()
                await asyncio.sleep(random.uniform(lo, hi))

    total = len(recipients)
    await asyncio.gather(*[_send_one(c, i, total) for i, c in enumerate(recipients)])
    bus.emit("dispatch_completed", detail={
        "approval_id": approval_id,
        "sent": results["sent"],
        "failed": results["failed"],
        "skipped": results["skipped"],
    })
    return results
