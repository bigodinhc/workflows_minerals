"""OneDrive PDF detection + approval card dispatch.

Runs inside the Railway aiohttp app (webhook/bot/main.py). Triggered by
webhook/routes/onedrive.py after the HTTP handler responds 202.

Responsibilities:
  1. Validate the Graph change-notification payload (clientState).
  2. Query the Graph delta endpoint for what changed.
  3. Filter for new PDFs (not seen before).
  4. Create a Redis approval state (48h TTL).
  5. Send the Telegram approval card to the admin.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from execution.core.event_bus import EventBus
from execution.integrations.graph_client import GraphClient
from execution.integrations.contacts_repo import ContactsRepo

from bot.callback_data import OneDriveApprove, OneDriveDiscard
from bot.config import get_bot
from bot.users import get_onedrive_approver_ids


logger = logging.getLogger(__name__)


ALL_CODE = "__all__"                        # Special list_code for Todos
SEEN_TTL_SECONDS = 30 * 24 * 3600           # 30 days
APPROVAL_TTL_SECONDS = 48 * 3600            # 48 hours


# ── Filters ──

def _is_pdf_file(item: dict) -> bool:
    """True iff item is a file (not folder) AND is a PDF."""
    if "folder" in item:
        return False
    if "file" not in item:
        return False
    mime = (item.get("file") or {}).get("mimeType", "")
    name = item.get("name", "")
    return mime == "application/pdf" or name.lower().endswith(".pdf")


# ── Dedup ──

async def _is_new_item(redis_client, item_id: str) -> bool:
    return (await redis_client.get(f"seen:onedrive:{item_id}")) is None


async def _mark_seen(redis_client, item_id: str) -> None:
    await redis_client.set(f"seen:onedrive:{item_id}", "1", ex=SEEN_TTL_SECONDS)


# ── Approval state ──

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_approval_state(
    redis_client, item: dict, drive_id: str, trace_id: str | None = None
) -> str:
    approval_id = uuid.uuid4().hex[:12]     # 12-char keeps CallbackData under 64 bytes
    state = {
        "drive_id": drive_id,
        "drive_item_id": item["id"],
        "filename": item["name"],
        "size": item.get("size", 0),
        "downloadUrl": item.get("@microsoft.graph.downloadUrl", ""),
        "downloadUrl_fetched_at": _now_iso(),
        "status": "pending",
        "created_at": _now_iso(),
        "trace_id": trace_id,
    }
    await redis_client.set(
        f"approval:{approval_id}",
        json.dumps(state),
        ex=APPROVAL_TTL_SECONDS,
    )
    return approval_id


# ── Approval card ──

def build_approval_keyboard(
    approval_id: str,
    contacts_repo: ContactsRepo,
) -> InlineKeyboardMarkup:
    """Inline keyboard with one button per contact_list + Todos + Descartar."""
    builder = InlineKeyboardBuilder()
    for lst in contacts_repo.list_lists():
        label = f"📊 {lst.label} ({lst.member_count})"
        builder.button(
            text=label,
            callback_data=OneDriveApprove(
                approval_id=approval_id, list_code=lst.code
            ).pack(),
        )
    total_active = len(contacts_repo.list_active())
    builder.button(
        text=f"🌐 Todos ({total_active})",
        callback_data=OneDriveApprove(
            approval_id=approval_id, list_code=ALL_CODE
        ).pack(),
    )
    builder.button(
        text="❌ Descartar",
        callback_data=OneDriveDiscard(approval_id=approval_id).pack(),
    )
    builder.adjust(1)  # one button per row — readable on mobile
    return builder.as_markup()


def build_approval_text(item: dict) -> str:
    size_mb = (item.get("size", 0) or 0) / (1024 * 1024)
    size_str = f"{size_mb:.1f} MB" if size_mb >= 0.1 else f"{item.get('size', 0)} bytes"
    return (
        f"📄 *Novo PDF detectado*\n\n"
        f"Arquivo: `{item['name']}`\n"
        f"Tamanho: {size_str}\n\n"
        f"Escolha a lista de envio:"
    )


# ── Notification validation ──

def validate_notification(payload: dict, expected_client_state: str) -> bool:
    """Every notification in payload['value'] must carry our clientState (constant-time compare)."""
    values = payload.get("value", [])
    if not values:
        return False
    expected = expected_client_state or ""
    return all(
        hmac.compare_digest(v.get("clientState") or "", expected)
        for v in values
    )


async def _send_approval_cards(
    bot,
    admin_chat_id: int,
    text: str,
    keyboard,
) -> dict:
    """Fan-out the approval card to admin + every approver in ONEDRIVE_APPROVER_IDS.

    Returns a dict:
      {
        "recipients": [{"chat_id": int, "message_id": int}, ...],  # successful sends
        "errors": [{"chat_id": int, "error": str}, ...],            # failed sends
        "attempted": int,                                            # deduplicated target count
      }
    Failures are logged but never raise — partial fan-out is acceptable
    (other approvers + admin still receive the card).
    """
    # Dedupe: admin always implicit, may also appear in the env list
    targets: list[int] = [admin_chat_id]
    for cid in get_onedrive_approver_ids():
        if cid not in targets:
            targets.append(cid)

    coros = [
        bot.send_message(
            chat_id=cid,
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        for cid in targets
    ]
    results = await asyncio.gather(*coros, return_exceptions=True)

    recipients: list[dict] = []
    errors: list[dict] = []
    for cid, res in zip(targets, results):
        if isinstance(res, Exception):
            logger.warning(
                "OneDrive approval card send to %s failed: %s", cid, res
            )
            errors.append({"chat_id": cid, "error": str(res)[:200]})
            continue
        # res is the Message object returned by aiogram
        message_id = getattr(res, "message_id", None)
        if message_id is None:
            errors.append({"chat_id": cid, "error": "no message_id"})
            continue
        recipients.append({"chat_id": cid, "message_id": message_id})
    return {
        "recipients": recipients,
        "errors": errors,
        "attempted": len(targets),
    }


async def _persist_recipients(
    redis_client, approval_id: str, recipients: list[dict]
) -> None:
    """Update approval:{uuid} JSON in place with recipients[]. Preserves TTL."""
    raw = await redis_client.get(f"approval:{approval_id}")
    if not raw:
        return
    state = json.loads(raw)
    state = {**state, "recipients": recipients}
    await redis_client.set(
        f"approval:{approval_id}",
        json.dumps(state),
        keepttl=True,
    )


# ── Main entrypoint (called from routes/onedrive.py) ──

async def process_notification(payload: dict) -> None:
    """Process a Graph change-notification payload.

    Safe to call concurrently; idempotent via Redis dedup.
    """
    expected_state = os.environ["GRAPH_WEBHOOK_CLIENT_STATE"]
    if not validate_notification(payload, expected_state):
        return

    bus = EventBus(workflow="onedrive_webhook")
    bus.emit("webhook_received", detail={"count": len(payload.get("value", []))})

    try:
        from redis.asyncio import Redis
        redis_client = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

        graph = GraphClient()
        drive_id = os.environ["GRAPH_DRIVE_ID"]
        folder_path = os.environ["GRAPH_FOLDER_PATH"]

        delta_token = await redis_client.get("onedrive:delta_token:sigcm")
        items, next_token = graph.get_folder_delta(
            drive_id=drive_id,
            folder_path=folder_path,
            delta_token=delta_token,
        )
        if next_token:
            await redis_client.set("onedrive:delta_token:sigcm", next_token)

        bus.emit("delta_query_done", detail={"item_count": len(items)})

        # Bootstrap guard: on the FIRST webhook for this folder, the Graph delta
        # query returns the full folder snapshot (not just changes). Without this
        # check we'd send an approval card for every historical PDF already in the
        # folder. Instead, mark every existing PDF as "seen" silently, persist
        # a bootstrap flag, and notify only on subsequent deltas.
        bootstrap_done = await redis_client.get("onedrive:bootstrap_done")
        if not bootstrap_done:
            pdf_count = 0
            for item in items:
                if _is_pdf_file(item):
                    await _mark_seen(redis_client, item["id"])
                    pdf_count += 1
            await redis_client.set("onedrive:bootstrap_done", "1")
            bus.emit(
                "bootstrap_complete",
                detail={"baseline_pdfs": pdf_count, "total_items": len(items)},
            )
            return

        contacts_repo = ContactsRepo()
        bot = get_bot()
        admin_chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

        for item in items:
            if not _is_pdf_file(item):
                continue
            if not await _is_new_item(redis_client, item["id"]):
                bus.emit("duplicate_webhook", detail={"item_id": item["id"]})
                continue
            await _mark_seen(redis_client, item["id"])

            # Delta responses omit @microsoft.graph.downloadUrl — we must
            # fetch the full driveItem to get a signed download URL.
            full_item = graph.get_item(drive_id, item["id"])

            approval_id = await create_approval_state(
                redis_client, full_item, drive_id=drive_id, trace_id=bus.trace_id
            )
            bus.emit("approval_created", detail={
                "approval_id": approval_id,
                "filename": full_item.get("name", item.get("name", "?")),
            })

            fanout = await _send_approval_cards(
                bot=bot,
                admin_chat_id=admin_chat_id,
                text=build_approval_text(full_item),
                keyboard=build_approval_keyboard(approval_id, contacts_repo),
            )
            recipients = fanout["recipients"]

            await _persist_recipients(redis_client, approval_id, recipients)

            if not recipients:
                bus.emit("approval_fanout_failed", level="error", detail={
                    "approval_id": approval_id,
                    "errors": fanout["errors"],
                })
            elif len(recipients) < fanout["attempted"]:
                # Fewer recipients than expected (admin + approvers, deduplicated)
                bus.emit("approval_fanout_partial", level="warn", detail={
                    "approval_id": approval_id,
                    "succeeded": len(recipients),
                    "failed": fanout["attempted"] - len(recipients),
                    "errors": fanout["errors"],
                })
            else:
                bus.emit("approval_fanout", detail={
                    "approval_id": approval_id,
                    "recipient_count": len(recipients),
                    "recipient_chat_ids": [r["chat_id"] for r in recipients],
                })

        bus.emit("webhook_processed")
    except Exception as exc:
        bus.emit("webhook_crashed", level="error", detail={"error": str(exc)})
        raise
