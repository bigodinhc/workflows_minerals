"""
Telegram Bot API wrapper helpers.
All low-level calls to the Telegram HTTP API live here.
Other modules import from this file — it has no internal dependencies.
"""

import os
import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


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


def send_telegram_message(chat_id, text, reply_markup=None):
    """Send a message via Telegram."""
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    return telegram_api("sendMessage", data)


def edit_message(chat_id, message_id, text, reply_markup=None):
    """Edit an existing message."""
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        data["reply_markup"] = reply_markup
    return telegram_api("editMessageText", data)


def finalize_card(chat_id, callback_query, status_text):
    """Final feedback for curation buttons: edit the original card; on failure send a new plain-text message.

    Removes the inline keyboard so the user can't double-click, and guarantees
    a visual confirmation even if the Markdown edit fails (old message, parse
    errors, etc.).
    """
    message_id = callback_query.get("message", {}).get("message_id")
    if not message_id:
        logger.warning("finalize_card: missing message_id in callback_query")
        send_telegram_message(chat_id, status_text)
        return

    edit_result = edit_message(chat_id, message_id, status_text, reply_markup=None)
    if edit_result.get("ok"):
        return

    # Edit failed (markdown parse error, msg too old, etc.) — fallback to a new plain message
    logger.warning(
        f"finalize_card: edit_message failed for msg_id={message_id}: "
        f"{edit_result.get('description', 'unknown')} — sending fallback"
    )
    # Strip markdown for safety in fallback
    plain = status_text.replace("*", "").replace("`", "").replace("_", "")
    send_telegram_message(chat_id, plain)


def send_approval_message(chat_id, draft_id, preview_text):
    """Send preview with 3 approval buttons."""
    # Truncate preview for Telegram (max ~4096 chars)
    display_text = preview_text[:3500] if len(preview_text) > 3500 else preview_text

    buttons = {
        "inline_keyboard": [
            [
                {"text": "✅ Aprovar e Enviar", "callback_data": f"approve:{draft_id}"},
                {"text": "🧪 Teste", "callback_data": f"test_approve:{draft_id}"}
            ],
            [
                {"text": "✏️ Ajustar", "callback_data": f"adjust:{draft_id}"},
                {"text": "❌ Rejeitar", "callback_data": f"reject:{draft_id}"}
            ]
        ]
    }

    return send_telegram_message(chat_id, f"📋 *PREVIEW*\n\n{display_text}", buttons)
