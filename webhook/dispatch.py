"""
WhatsApp sending + approval/test async flows.
Extracted from app.py to keep concerns separated.
"""

import os
import json
import logging
import time
import requests

from execution.core.delivery_reporter import DeliveryReporter, Contact, build_contact_from_row
from execution.integrations.sheets_client import SheetsClient
from telegram import send_telegram_message, edit_message, send_approval_message

logger = logging.getLogger(__name__)

UAZAPI_URL = os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")
UAZAPI_TOKEN = (os.getenv("UAZAPI_TOKEN") or "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"


# ============================================================
# GOOGLE SHEETS (contacts)
# ============================================================

def get_contacts():
    """Fetch WhatsApp contacts from Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds_json = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_json, scopes=[
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ])
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).sheet1

    # Retry logic to handle intermittent Google API 500 errors
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


# ============================================================
# WHATSAPP SENDING
# ============================================================

def send_whatsapp(phone, message, token=None, url=None):
    """Send WhatsApp message via Uazapi."""
    use_token = token or UAZAPI_TOKEN
    use_url = url or UAZAPI_URL
    headers = {
        "token": use_token,
        "Content-Type": "application/json"
    }
    payload = {
        "number": str(phone),
        "text": message
    }
    try:
        response = requests.post(
            f"{use_url}/send/text",
            json=payload,
            headers=headers,
            timeout=30
        )
        if response.status_code != 200:
            logger.error(f"WhatsApp {phone}: HTTP {response.status_code} - {response.text[:200]}")
        return response.status_code == 200
    except Exception as e:
        logger.error(f"WhatsApp send error for {phone}: {e}")
        return False


def _send_whatsapp_raising(phone, text, token=None, url=None):
    """Raising wrapper around send_whatsapp for DeliveryReporter contract."""
    use_token = token or UAZAPI_TOKEN
    use_url = url or UAZAPI_URL
    headers = {"token": use_token, "Content-Type": "application/json"}
    payload = {"number": str(phone), "text": text}
    response = requests.post(
        f"{use_url}/send/text",
        json=payload,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


# ============================================================
# ASYNC PROCESSING
# ============================================================

def process_approval_async(chat_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Process WhatsApp sending with progress updates via DeliveryReporter."""
    progress = send_telegram_message(chat_id, "⏳ Iniciando envio para WhatsApp...")
    progress_msg_id = progress.get("result", {}).get("message_id") if progress.get("ok") else None

    try:
        raw_contacts = get_contacts()

        delivery_contacts = [bc for c in raw_contacts if (bc := build_contact_from_row(c))]

        if progress_msg_id:
            edit_message(chat_id, progress_msg_id,
                f"⏳ Enviando para {len(delivery_contacts)} contatos...\n0/{len(delivery_contacts)}")

        def on_progress(processed, total_, result):
            if progress_msg_id and processed % 10 == 0:
                edit_message(
                    chat_id,
                    progress_msg_id,
                    f"⏳ Enviando...\n{processed}/{total_} processados",
                )

        def send_fn(phone, text):
            _send_whatsapp_raising(phone, text, token=uazapi_token, url=uazapi_url)

        reporter = DeliveryReporter(
            workflow="webhook_approval",
            send_fn=send_fn,
            telegram_chat_id=chat_id,
            gh_run_id=None,
        )
        report = reporter.dispatch(delivery_contacts, draft_message, on_progress=on_progress)

        if progress_msg_id:
            edit_message(
                chat_id,
                progress_msg_id,
                "✔️ Envio finalizado — veja resumo detalhado abaixo.",
            )

        logger.info(
            f"Approval complete: {report.success_count} sent, {report.failure_count} failed"
        )

    except Exception as e:
        logger.error(f"Approval processing error: {e}")
        error_text = f"❌ ERRO NO ENVIO\n\n{str(e)}"
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, error_text)
        else:
            send_telegram_message(chat_id, error_text)


def process_test_send_async(chat_id, draft_id, draft_message, uazapi_token=None, uazapi_url=None):
    """Send message only to the first contact for testing."""
    try:
        contacts = get_contacts()
        if not contacts:
            send_telegram_message(chat_id, "❌ Nenhum contato encontrado na planilha.")
            return

        first_contact = contacts[0]
        name = first_contact.get("Nome", "Contato 1")
        phone = first_contact.get("Evolution-api") or first_contact.get("Telefone")
        if not phone:
            send_telegram_message(chat_id, "❌ Primeiro contato sem telefone.")
            return

        phone = str(phone).replace("whatsapp:", "").strip()

        if send_whatsapp(phone, draft_message, token=uazapi_token, url=uazapi_url):
            send_telegram_message(chat_id,
                f"🧪 *TESTE OK*\n\n"
                f"✅ Enviado para: {name} ({phone})\n\n"
                f"Se ficou bom, clique em ✅ Aprovar para enviar a todos os {len(contacts)} contatos.")
            # Re-send approval buttons
            send_approval_message(chat_id, draft_id, draft_message)
        else:
            send_telegram_message(chat_id,
                f"❌ *TESTE FALHOU*\n\n"
                f"Falha ao enviar para: {name} ({phone})\n"
                f"Verifique o token UAZAPI.")

        logger.info(f"Test send for {draft_id}: {name} ({phone})")
    except Exception as e:
        logger.error(f"Test send error: {e}")
        send_telegram_message(chat_id, f"❌ Erro no teste:\n{str(e)[:500]}")
