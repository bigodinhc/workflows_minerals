"""
Telegram Webhook Server for Rationale News Approval
Deploy this to Railway to receive button callbacks from Telegram.
"""

import os
import json
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# Config from environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
UAZAPI_URL = os.getenv("UAZAPI_URL", "https://mineralstrading.uazapi.com")
UAZAPI_TOKEN = os.getenv("UAZAPI_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Google Sheets for contacts
SHEET_ID = "1tU3Izdo21JichTXg15bc1paWUiN8XioJYZUPpbIUgL0"

def get_telegram_api(method, data=None):
    """Call Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    if data:
        return requests.post(url, json=data, timeout=10)
    return requests.get(url, timeout=10)

def answer_callback(callback_id, text):
    """Answer callback query."""
    get_telegram_api("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text
    })

def edit_message(chat_id, message_id, new_text):
    """Edit message to show result."""
    get_telegram_api("editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": new_text,
        "parse_mode": "Markdown"
    })

def get_contacts():
    """Fetch contacts from Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials
    
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
    response = requests.post(
        f"{UAZAPI_URL}/send/text",
        json=payload,
        headers=headers,
        timeout=10
    )
    return response.status_code == 200

# In-memory draft storage (for simplicity - could use Redis in production)
DRAFTS = {}

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})

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
    
    return jsonify({"success": True, "draft_id": draft_id})

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    """Handle Telegram webhook callbacks (button presses)."""
    update = request.json
    
    # Handle callback query (button press)
    callback_query = update.get("callback_query")
    if not callback_query:
        return jsonify({"ok": True})
    
    callback_id = callback_query["id"]
    callback_data = callback_query.get("data", "")
    chat_id = callback_query["message"]["chat"]["id"]
    message_id = callback_query["message"]["message_id"]
    
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
            answer_callback(callback_id, "❌ Draft não encontrado ou expirado")
            edit_message(chat_id, message_id, "❌ *EXPIRADO*\n\nEste draft não está mais disponível.")
            return jsonify({"ok": True})
        
        message = draft["message"]
        
        # Broadcast to WhatsApp
        answer_callback(callback_id, "⏳ Enviando para WhatsApp...")
        
        try:
            contacts = get_contacts()
            success_count = 0
            
            for contact in contacts:
                phone = contact.get("Evolution-api") or contact.get("Telefone")
                if not phone:
                    continue
                phone = str(phone).replace("whatsapp:", "").strip()
                
                if send_whatsapp(phone, message):
                    success_count += 1
            
            # Update Telegram message
            edit_message(
                chat_id,
                message_id,
                f"✅ *APROVADO E ENVIADO*\n\n📤 Enviado para {success_count} contatos.\n\n---\n{message[:500]}..."
            )
            
            # Mark as processed
            draft["status"] = "approved"
            
        except Exception as e:
            edit_message(chat_id, message_id, f"❌ *ERRO*\n\n{str(e)}")
    
    elif action == "reject":
        answer_callback(callback_id, "❌ Rejeitado")
        edit_message(chat_id, message_id, "❌ *REJEITADO*\n\nEste relatório foi descartado.")
        
        if draft_id in DRAFTS:
            DRAFTS[draft_id]["status"] = "rejected"
    
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
