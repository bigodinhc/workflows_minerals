"""
Telegram Webhook Server for Rationale News Approval
Deploy this to Railway to receive button callbacks from Telegram.
"""

import os
import json
import logging
import threading
import requests
from flask import Flask, request, jsonify

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Config from environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
UAZAPI_URL = os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")
UAZAPI_TOKEN = (os.getenv("UAZAPI_TOKEN") or "").strip()
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Google Sheets for contacts
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"

# Log config at startup (partial token for security)
logger.info(f"UAZAPI_URL: {UAZAPI_URL}")
logger.info(f"UAZAPI_TOKEN: {'SET (' + UAZAPI_TOKEN[:8] + '...)' if UAZAPI_TOKEN else 'NOT SET'}")
logger.info(f"TELEGRAM_BOT_TOKEN: {'SET' if TELEGRAM_BOT_TOKEN else 'NOT SET'}")

def telegram_api(method, data):
    """Call Telegram Bot API and return parsed response."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    try:
        resp = requests.post(url, json=data, timeout=15)
        result = resp.json()
        if not result.get("ok"):
            logger.warning(f"Telegram {method} failed: {result.get('description', 'unknown')}")
        return result
    except Exception as e:
        logger.error(f"Telegram API error ({method}): {e}")
        return {"ok": False}

def answer_callback(callback_id, text):
    """Answer callback query (acknowledge button press)."""
    return telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text
    })

def send_telegram_message(chat_id, text):
    """Send a new Telegram message."""
    return telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": text
    })

def edit_message(chat_id, message_id, new_text):
    """Edit existing Telegram message."""
    return telegram_api("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": new_text
    })

def get_contacts():
    """Fetch contacts from Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials
    
    logger.info("Fetching contacts from Google Sheets...")
    creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ])
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID).worksheet("Página1")
    records = sheet.get_all_records()
    
    # Filter for "Big" contacts
    contacts = [r for r in records if r.get("ButtonPayload") == "Big"]
    logger.info(f"Found {len(contacts)} contacts with ButtonPayload='Big'")
    return contacts

def send_whatsapp(phone, message):
    """Send WhatsApp message via Uazapi."""
    headers = {
        "token": UAZAPI_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "number": str(phone),
        "text": message
    }
    try:
        response = requests.post(
            f"{UAZAPI_URL}/send/text",
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

def process_approval_async(chat_id, draft_message):
    """Process WhatsApp sending in background thread with Telegram progress updates."""
    # Send a NEW message for progress (since original may have expired for editing)
    progress = send_telegram_message(chat_id, "⏳ Iniciando envio para WhatsApp...")
    progress_msg_id = progress.get("result", {}).get("message_id") if progress.get("ok") else None
    
    try:
        contacts = get_contacts()
        total = len(contacts)
        success_count = 0
        fail_count = 0
        
        # Update progress
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, 
                f"⏳ Enviando para {total} contatos...\n0/{total} processados")
        
        for i, contact in enumerate(contacts):
            phone = contact.get("Evolution-api") or contact.get("Telefone")
            if not phone:
                continue
            phone = str(phone).replace("whatsapp:", "").strip()
            
            if send_whatsapp(phone, draft_message):
                success_count += 1
            else:
                fail_count += 1
            
            # Update progress every 10 contacts
            processed = success_count + fail_count
            if progress_msg_id and processed % 10 == 0:
                edit_message(chat_id, progress_msg_id,
                    f"⏳ Enviando...\n{processed}/{total} processados\n✅ {success_count} OK | ❌ {fail_count} falhas")
        
        # Final result
        result_text = f"📊 ENVIO CONCLUÍDO\n\n"
        result_text += f"✅ Enviados: {success_count}\n"
        result_text += f"❌ Falhas: {fail_count}\n"
        result_text += f"📋 Total: {total}\n"
        
        if fail_count == total:
            result_text += "\n⚠️ TODOS falharam! Verifique o token UAZAPI."
        
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, result_text)
        else:
            send_telegram_message(chat_id, result_text)
            
        logger.info(f"Approval complete: {success_count} sent, {fail_count} failed")
        
    except Exception as e:
        logger.error(f"Approval processing error: {e}")
        error_text = f"❌ ERRO NO ENVIO\n\n{str(e)}"
        if progress_msg_id:
            edit_message(chat_id, progress_msg_id, error_text)
        else:
            send_telegram_message(chat_id, error_text)

# In-memory draft storage
DRAFTS = {}

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok", 
        "drafts_count": len(DRAFTS),
        "uazapi_url": UAZAPI_URL,
        "uazapi_token_set": bool(UAZAPI_TOKEN)
    })

@app.route("/store-draft", methods=["POST"])
def store_draft():
    """Store a draft for later approval. Called by GitHub Actions."""
    data = request.json
    draft_id = data.get("draft_id")
    message = data.get("message")
    
    if not draft_id or not message:
        return jsonify({"error": "Missing draft_id or message"}), 400
    
    DRAFTS[draft_id] = {
        "message": message,
        "status": "pending"
    }
    
    logger.info(f"Draft stored: {draft_id} ({len(message)} chars)")
    return jsonify({"success": True, "draft_id": draft_id})

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Handle Telegram webhook callbacks (button presses)."""
    update = request.json
    logger.info(f"Webhook received update_id: {update.get('update_id')}")
    
    # Handle callback query (button press)
    callback_query = update.get("callback_query")
    if not callback_query:
        return jsonify({"ok": True})
    
    callback_id = callback_query["id"]
    callback_data = callback_query.get("data", "")
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    
    logger.info(f"Callback: {callback_data} from chat {chat_id}")
    
    # Parse callback: "approve:draft_id" or "reject:draft_id"
    parts = callback_data.split(":")
    if len(parts) != 2:
        answer_callback(callback_id, "Erro: dados inválidos")
        return jsonify({"ok": True})
    
    action, draft_id = parts
    
    if action == "approve":
        # Get draft message
        draft = DRAFTS.get(draft_id)
        if not draft:
            logger.warning(f"Draft not found: {draft_id}. Available: {list(DRAFTS.keys())}")
            answer_callback(callback_id, "❌ Draft não encontrado")
            send_telegram_message(chat_id, "❌ DRAFT EXPIRADO\n\nEste draft não está mais disponível. Rode o workflow novamente.")
            return jsonify({"ok": True})
        
        if draft["status"] != "pending":
            answer_callback(callback_id, "⚠️ Já processado")
            return jsonify({"ok": True})
        
        draft["status"] = "approved"
        
        # Answer callback immediately
        answer_callback(callback_id, "✅ Aprovado! Enviando...")
        
        # Process WhatsApp in background thread
        thread = threading.Thread(
            target=process_approval_async,
            args=(chat_id, draft["message"])
        )
        thread.daemon = True
        thread.start()
        
        # Return immediately
        return jsonify({"ok": True})
    
    elif action == "reject":
        answer_callback(callback_id, "❌ Rejeitado")
        send_telegram_message(chat_id, "❌ REJEITADO\n\nEste relatório foi descartado.")
        
        if draft_id in DRAFTS:
            DRAFTS[draft_id]["status"] = "rejected"
    
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
