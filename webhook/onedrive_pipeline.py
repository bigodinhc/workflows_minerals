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

import hmac
import json
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

            approval_id = await create_approval_state(
                redis_client, item, drive_id=drive_id, trace_id=bus.trace_id
            )
            bus.emit("approval_created", detail={
                "approval_id": approval_id,
                "filename": item["name"],
            })

            await bot.send_message(
                chat_id=admin_chat_id,
                text=build_approval_text(item),
                reply_markup=build_approval_keyboard(approval_id, contacts_repo),
                parse_mode="Markdown",
            )

        bus.emit("webhook_processed")
    except Exception as exc:
        bus.emit("webhook_crashed", level="error", detail={"error": str(exc)})
        raise
