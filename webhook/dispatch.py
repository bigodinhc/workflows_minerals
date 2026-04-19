"""WhatsApp sending + approval/test async flows.

All functions are async — they use aiohttp.ClientSession for HTTP
and Aiogram bot for Telegram messages.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os

import aiohttp
import requests
from redis import asyncio as redis_async

from bot.config import get_bot, UAZAPI_URL, UAZAPI_TOKEN, GOOGLE_CREDENTIALS_JSON, SHEET_ID
from bot.keyboards import build_approval_keyboard
from execution.core.delivery_reporter import DeliveryReporter, build_contact_from_row
from execution.integrations.sheets_client import SheetsClient

logger = logging.getLogger(__name__)


# ── Redis async client (lazy singleton) ──

_redis_async_client = None


async def _get_redis_async():
    """Lazy async redis client. Uses REDIS_URL env var."""
    global _redis_async_client
    if _redis_async_client is None:
        url = os.getenv("REDIS_URL", "")
        if not url:
            raise RuntimeError("REDIS_URL not configured")
        _redis_async_client = redis_async.from_url(url, decode_responses=True)
    return _redis_async_client


def _idempotency_key(phone: str, draft_id: str, message: str) -> str:
    """sha1(phone|draft_id|message) → 'whatsapp:sent:<digest>'."""
    digest = hashlib.sha1(f"{phone}|{draft_id}|{message}".encode()).hexdigest()
    return f"whatsapp:sent:{digest}"


# ── Google Sheets (contacts) — sync, wrapped in to_thread ──

def _get_contacts_sync():
    """Fetch WhatsApp contacts from Google Sheets (sync)."""
    import gspread
    from google.oauth2.service_account import Credentials
    import time

    creds_json = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_json, scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly",
    ])
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).sheet1

    max_retries = 3
    records = []
    for attempt in range(max_retries):
        try:
            records = sheet.get_all_records()
            break
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"Failed to fetch contacts after {max_retries} attempts: {e}")
                raise
            sleep_time = 2 ** attempt
            logger.warning(f"Google Sheets API error {e}. Retrying in {sleep_time}s...")
            time.sleep(sleep_time)

    contacts = [r for r in records if r.get("ButtonPayload") == "Big"]
    logger.info(f"Found {len(contacts)} contacts with ButtonPayload='Big'")
    return contacts


async def get_contacts():
    """Fetch WhatsApp contacts (async wrapper)."""
    return await asyncio.to_thread(_get_contacts_sync)


# ── WhatsApp sending ──

async def send_whatsapp(phone, message, draft_id: str = "", token=None, url=None):
    """Send WhatsApp message via Uazapi (async).

    Idempotency: if the same (phone, draft_id, message) triple has been sent
    within the last 24 hours, the UAZAPI call is skipped and
    {"status": "duplicate", "skipped": True} is returned.
    """
    # Idempotency: SET NX EX 86400 — atomic check-and-mark, 24h window
    if draft_id:
        try:
            redis_client = await _get_redis_async()
            key = _idempotency_key(phone, draft_id, message)
            marked = await redis_client.set(key, "1", ex=86400, nx=True)
            if marked is None:
                logger.info(
                    "whatsapp_idempotency_hit",
                    extra={"phone_last4": phone[-4:], "draft_id": draft_id},
                )
                return {"status": "duplicate", "skipped": True}
        except Exception as exc:
            # Redis down? Don't block sends — but log loudly.
            logger.warning("whatsapp_idempotency_check_failed", exc_info=exc)

    use_token = token or UAZAPI_TOKEN
    use_url = url or UAZAPI_URL
    headers = {"token": use_token, "Content-Type": "application/json"}
    payload = {"number": str(phone), "text": message}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{use_url}/send/text",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"WhatsApp {phone}: HTTP {resp.status} - {body[:200]}")
                return {"status": "ok", "http_status": resp.status, "ok": resp.status == 200}
    except Exception as e:
        logger.error(f"WhatsApp send error for {phone}: {e}")
        return {"status": "error", "ok": False}


# ── Async processing ──

async def process_approval_async(chat_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Process WhatsApp sending with progress updates via DeliveryReporter."""
    bot = get_bot()
    progress = await bot.send_message(chat_id, "⏳ Iniciando envio para WhatsApp...")
    progress_msg_id = progress.message_id

    try:
        raw_contacts = await get_contacts()
        delivery_contacts = [bc for c in raw_contacts if (bc := build_contact_from_row(c))]

        try:
            await bot.edit_message_text(
                f"⏳ Enviando para {len(delivery_contacts)} contatos...\n0/{len(delivery_contacts)}",
                chat_id=chat_id, message_id=progress_msg_id,
            )
        except Exception:
            pass

        # DeliveryReporter is sync — use sync send_fn + to_thread
        def send_fn(phone, text):
            use_token = uazapi_token or UAZAPI_TOKEN
            use_url_val = uazapi_url or UAZAPI_URL
            headers_req = {"token": use_token, "Content-Type": "application/json"}
            payload_req = {"number": str(phone), "text": text}
            response = requests.post(
                f"{use_url_val}/send/text",
                json=payload_req,
                headers=headers_req,
                timeout=30,
            )
            response.raise_for_status()
            return response.json()

        loop = asyncio.get_event_loop()

        def on_progress_sync(processed, total_, result):
            if processed % 10 == 0:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        bot.edit_message_text(
                            f"⏳ Enviando...\n{processed}/{total_} processados",
                            chat_id=chat_id, message_id=progress_msg_id,
                        ),
                        loop,
                    )
                    future.result(timeout=5)
                except Exception:
                    pass  # ignore "message not modified" — don't crash delivery

        reporter = DeliveryReporter(
            workflow="webhook_approval",
            send_fn=send_fn,
            telegram_chat_id=chat_id,
            gh_run_id=None,
        )

        report = await asyncio.to_thread(
            reporter.dispatch, delivery_contacts, draft_message, on_progress_sync,
        )

        try:
            await bot.edit_message_text(
                "✔️ Envio finalizado — veja resumo detalhado abaixo.",
                chat_id=chat_id, message_id=progress_msg_id,
            )
        except Exception:
            pass

        logger.info(
            f"Approval complete: {report.success_count} sent, {report.failure_count} failed"
        )

    except Exception as e:
        logger.error(f"Approval processing error: {e}")
        error_text = f"❌ ERRO NO ENVIO\n\n{str(e)}"
        try:
            await bot.edit_message_text(error_text, chat_id=chat_id, message_id=progress_msg_id)
        except Exception:
            await bot.send_message(chat_id, error_text)


async def process_test_send_async(chat_id, draft_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Send message only to the first contact for testing."""
    bot = get_bot()
    try:
        contacts = await get_contacts()
        if not contacts:
            await bot.send_message(chat_id, "❌ Nenhum contato encontrado na planilha.")
            return

        first_contact = contacts[0]
        name = first_contact.get("Nome", "Contato 1")
        phone = first_contact.get("Evolution-api") or first_contact.get("Telefone")
        if not phone:
            await bot.send_message(chat_id, "❌ Primeiro contato sem telefone.")
            return

        phone = str(phone).replace("whatsapp:", "").strip()

        result = await send_whatsapp(
            phone, draft_message, draft_id=draft_id, token=uazapi_token, url=uazapi_url
        )
        if result.get("ok") or result.get("status") == "duplicate":
            await bot.send_message(
                chat_id,
                f"🧪 *TESTE OK*\n\n"
                f"✅ Enviado para: {name} ({phone})\n\n"
                f"Se ficou bom, clique em ✅ Aprovar para enviar a todos os {len(contacts)} contatos.",
            )
            display = draft_message[:3500] if len(draft_message) > 3500 else draft_message
            await bot.send_message(
                chat_id,
                f"📋 *PREVIEW*\n\n{display}",
                reply_markup=build_approval_keyboard(draft_id),
            )
        else:
            await bot.send_message(
                chat_id,
                f"❌ *TESTE FALHOU*\n\nFalha ao enviar para: {name} ({phone})\nVerifique o token UAZAPI.",
            )

        logger.info(f"Test send for {draft_id}: {name} ({phone})")
    except Exception as e:
        logger.error(f"Test send error: {e}")
        await bot.send_message(chat_id, f"❌ Erro no teste:\n{str(e)[:500]}")
